"""Background Job Queue for Async Workflow Execution.

Provides a thread-pool-based job queue that runs workflow executions
and eval runs in the background, so long-running agent workflows
(12-40 minutes) don't block HTTP requests.

Usage:
    from job_queue import get_worker
    worker = get_worker(max_workers=2)
    job = worker.submit("workflow_run", agent_id="...", input_json='{"key": "val"}')
    status = worker.get_job(job["id"])
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from database import get_connection

logger = logging.getLogger(__name__)

# ── Schema ──────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS wf_jobs (
    id              TEXT PRIMARY KEY,
    type            TEXT NOT NULL,           -- 'workflow_run', 'eval_run'
    agent_id        TEXT,
    input_json      TEXT DEFAULT '{}',
    status          TEXT DEFAULT 'queued',   -- queued, running, completed, failed, cancelled
    progress        INTEGER DEFAULT 0,       -- completed steps
    total           INTEGER DEFAULT 0,       -- total steps
    result_json     TEXT DEFAULT '',
    error_msg       TEXT DEFAULT '',
    idempotency_key TEXT DEFAULT '',
    timeout_s       INTEGER DEFAULT 300,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    completed_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_wf_jobs_status ON wf_jobs(status);
"""

# ── Helpers ─────────────────────────────────────────────────────────


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_schema() -> None:
    """Ensure the wf_jobs table exists."""
    conn = get_connection()
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def _row_to_dict(row: Any) -> dict:
    return dict(row) if row else {}


def _rows_to_dicts(rows: list[Any]) -> list[dict]:
    return [dict(r) for r in rows]


# ── BackgroundWorker ────────────────────────────────────────────────


class BackgroundWorker:
    """Thread-pool-based background job worker.

    Each worker thread polls the database for 'queued' jobs, claims one,
    and processes it. Thread-safe via per-claim database connections.
    """

    def __init__(self, max_workers: int = 2) -> None:
        self.max_workers = max_workers
        self._running = False
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        # Lazy-import heavy modules so job_queue.py stays importable fast
        self._executor: Any = None
        self._evaluator: Any = None

    # ── Lifecycle ──────────────────────────────────────────────────

    def start(self) -> None:
        """Start the worker threads. Safe to call multiple times."""
        with self._lock:
            if self._running:
                logger.debug("Worker already running, skipping start")
                return
            self._running = True
            _ensure_schema()
            for i in range(self.max_workers):
                t = threading.Thread(
                    target=self._worker_loop,
                    name=f"bg-worker-{i}",
                    daemon=True,
                )
                t.start()
                self._threads.append(t)
            logger.info(
                "BackgroundWorker started with %d workers", self.max_workers
            )

    def stop(self) -> None:
        """Signal all worker threads to stop."""
        with self._lock:
            self._running = False
        logger.info("BackgroundWorker stopping")

    @property
    def is_running(self) -> bool:
        return self._running

    # ── Job CRUD ───────────────────────────────────────────────────

    def submit(
        self,
        job_type: str,
        agent_id: str,
        input_json: str = "{}",
        idempotency_key: str = "",
        timeout_s: int = 300,
    ) -> dict:
        """Create a new background job.

        Returns:
            dict with {id, status, created_at}

        If ``idempotency_key`` is provided and a job with that key already
        exists (and is not in a terminal state), the existing job is returned.
        """
        conn = get_connection()

        if idempotency_key:
            existing = conn.execute(
                "SELECT * FROM wf_jobs WHERE idempotency_key = ? AND status NOT IN ('completed', 'failed', 'cancelled')",
                (idempotency_key,),
            ).fetchone()
            if existing:
                logger.debug(
                    "Returning existing job for idempotency_key=%s",
                    idempotency_key,
                )
                return _row_to_dict(existing)

        job_id = _new_id()
        now = _now()
        conn.execute(
            """INSERT INTO wf_jobs
               (id, type, agent_id, input_json, status, idempotency_key,
                timeout_s, created_at)
               VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)""",
            (job_id, job_type, agent_id, input_json, idempotency_key,
             timeout_s, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM wf_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        logger.info(
            "Job submitted: id=%s type=%s agent=%s", job_id, job_type, agent_id
        )
        return _row_to_dict(row)

    def get_job(self, job_id: str) -> Optional[dict]:
        """Get a job by ID, or None if not found."""
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM wf_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a job. Returns True if it was actually updated."""
        conn = get_connection()
        cur = conn.execute(
            "UPDATE wf_jobs SET status = 'cancelled', completed_at = ? WHERE id = ? AND status NOT IN ('completed', 'failed', 'cancelled')",
            (_now(), job_id),
        )
        conn.commit()
        return cur.rowcount > 0

    def list_jobs(self, status: Optional[str] = None) -> list[dict]:
        """List jobs ordered by created_at DESC. Optionally filter by status."""
        conn = get_connection()
        if status:
            rows = conn.execute(
                "SELECT * FROM wf_jobs WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM wf_jobs ORDER BY created_at DESC"
            ).fetchall()
        return _rows_to_dicts(rows)

    # ── Update helpers ─────────────────────────────────────────────

    def _update_job(self, job_id: str, **kwargs: Any) -> None:
        """Update a job's fields in the database."""
        allowed = {
            "status", "progress", "total", "result_json",
            "error_msg", "started_at", "completed_at",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [job_id]
        conn = get_connection()
        conn.execute(f"UPDATE wf_jobs SET {set_clause} WHERE id = ?", vals)
        conn.commit()

    def _advance_progress(self, job_id: str, progress: int, total: int) -> None:
        """Atomically advance progress for a job."""
        conn = get_connection()
        conn.execute(
            "UPDATE wf_jobs SET progress = ?, total = ? WHERE id = ?",
            (progress, total, job_id),
        )
        conn.commit()

    # ── Worker loop ────────────────────────────────────────────────

    def _worker_loop(self) -> None:
        """Background thread: poll for 'queued' jobs, claim and execute one."""
        logger.debug("Worker thread started")
        while self._running:
            try:
                job = self._claim_next_job()
                if job is None:
                    time.sleep(1.0)
                    continue

                job_id = job["id"]
                job_type = job["type"]
                agent_id = job.get("agent_id", "")
                input_json = job.get("input_json", "{}")

                # Mark as running
                self._update_job(
                    job_id, status="running", started_at=_now(),
                )
                logger.info(
                    "Job %s started (type=%s agent=%s)",
                    job_id, job_type, agent_id,
                )

                try:
                    if job_type == "workflow_run":
                        result = self._run_workflow(
                            job_id, agent_id, input_json,
                        )
                    elif job_type == "eval_run":
                        input_data = (
                            json.loads(input_json)
                            if isinstance(input_json, str)
                            else input_json
                        )
                        dataset_id = input_data.get("dataset_id", "")
                        notes = input_data.get("notes", "")
                        result = self._run_eval(
                            job_id, agent_id, dataset_id, notes,
                        )
                    else:
                        raise ValueError(f"Unknown job type: {job_type}")

                    self._update_job(
                        job_id,
                        status="completed",
                        result_json=json.dumps(result),
                        completed_at=_now(),
                    )
                    logger.info("Job %s completed successfully", job_id)

                except Exception as exc:
                    logger.exception("Job %s failed: %s", job_id, exc)
                    self._update_job(
                        job_id,
                        status="failed",
                        error_msg=str(exc),
                        completed_at=_now(),
                    )

            except Exception as loop_exc:
                logger.error("Worker loop error: %s", loop_exc)
                time.sleep(2.0)

    def _claim_next_job(self) -> Optional[dict]:
        """Atomically claim one 'queued' job using a transaction."""
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM wf_jobs WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE wf_jobs SET status = 'claimed' WHERE id = ?",
                    (row["id"],),
                )
            conn.commit()
            return _row_to_dict(row) if row else None
        except Exception:
            conn.rollback()
            return None

    # ── Job type handlers ──────────────────────────────────────────

    def _get_executor(self) -> Any:
        """Lazy-init the WorkflowExecutor."""
        if self._executor is None:
            from workflow_executor import WorkflowExecutor
            self._executor = WorkflowExecutor()
        return self._executor

    def _get_evaluator(self) -> Any:
        """Lazy-init the Evaluator."""
        if self._evaluator is None:
            from evaluator import Evaluator
            self._evaluator = Evaluator()
        return self._evaluator

    def _run_workflow(
        self, job_id: str, agent_id: str, input_json: str,
    ) -> dict:
        """Execute a workflow run with progress tracking."""
        executor = self._get_executor()
        input_ctx = (
            json.loads(input_json)
            if isinstance(input_json, str)
            else {}
        )

        # Count steps for progress tracking
        try:
            graph = executor.db.get_workflow_graph(agent_id)
            steps = graph.get("steps", [])
            total_steps = len(steps)
        except Exception:
            total_steps = 0

        if total_steps > 0:
            self._advance_progress(job_id, 0, total_steps)

        # Dispatch to the executor
        result = executor.execute(agent_id, input_ctx, "manual")

        # Mark progress as complete
        if total_steps > 0:
            self._advance_progress(job_id, total_steps, total_steps)

        if "error" in result:
            raise RuntimeError(result["error"])

        return result

    def _run_eval(
        self,
        job_id: str,
        agent_id: str,
        dataset_id: str,
        notes: str,
    ) -> dict:
        """Execute an evaluation run with progress tracking."""
        evaluator = self._get_evaluator()
        executor = self._get_executor()

        eval_run = evaluator.create_eval_run(
            dataset_id, agent_id, notes=notes,
        )

        # Count items for progress tracking
        conn = get_connection()
        items = conn.execute(
            "SELECT COUNT(*) as cnt FROM eval_items WHERE dataset_id = ?",
            (dataset_id,),
        ).fetchone()
        total_items = items["cnt"] if items else 0

        if total_items > 0:
            self._advance_progress(job_id, 0, total_items)

        def agent_runner(input_text: str) -> str:
            result = executor.execute(agent_id, {"query": input_text})
            logs = executor.db.list_step_logs(result.get("id", ""))
            if logs:
                return logs[-1].get("output_data", "{}")
            return json.dumps(result)

        result = evaluator.run_evaluation(eval_run["id"], agent_runner)
        return result


# ── Singleton ───────────────────────────────────────────────────────

_worker: Optional[BackgroundWorker] = None
_worker_lock = threading.Lock()


def get_worker(max_workers: int = 2) -> BackgroundWorker:
    """Get or create the singleton BackgroundWorker instance.

    The worker is started on first access. Safe to call from multiple threads.
    """
    global _worker
    if _worker is None:
        with _worker_lock:
            if _worker is None:  # Double-check locking
                _worker = BackgroundWorker(max_workers=max_workers)
                _worker.start()
    return _worker
