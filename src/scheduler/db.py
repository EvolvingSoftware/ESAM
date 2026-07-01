"""ScheduleDB — SQLite-backed schedule metadata store.

Stores classification and metadata for Hermes cron jobs (Department, Team,
Project, Task Type, Tags). The cron jobs themselves are managed by Hermes;
this layer adds organizational context and filtering.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ScheduleDB:
    """Schedule metadata persistence layer.

    Each record links to a Hermes cron job via cron_job_id and stores
    classification fields: department, team, project, task_type, tags.
    """

    def __init__(self, db_path: str = "~/.hermes/esam/schedule_meta.db"):
        resolved = str(Path(db_path).expanduser().resolve())
        Path(resolved).parent.mkdir(parents=True, exist_ok=True)
        self._db_path = resolved
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def ensure_schema(self) -> None:
        """CREATE TABLE IF NOT EXISTS for schedule metadata."""
        conn = self._connect()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS wf_schedule_meta (
                    id              TEXT PRIMARY KEY,
                    cron_job_id     TEXT NOT NULL UNIQUE,
                    name            TEXT NOT NULL,
                    description     TEXT NOT NULL DEFAULT '',
                    department      TEXT NOT NULL DEFAULT '',
                    team            TEXT NOT NULL DEFAULT '',
                    project         TEXT NOT NULL DEFAULT '',
                    task_type       TEXT NOT NULL DEFAULT '',
                    tags            TEXT NOT NULL DEFAULT '[]',
                    schedule_type   TEXT NOT NULL DEFAULT 'cron',
                    status          TEXT NOT NULL DEFAULT 'unknown',
                    next_run        TEXT DEFAULT '',
                    last_run        TEXT DEFAULT '',
                    last_status     TEXT DEFAULT '',
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wf_schedule_meta_cron_job
                    ON wf_schedule_meta(cron_job_id);
                CREATE INDEX IF NOT EXISTS idx_wf_schedule_meta_department
                    ON wf_schedule_meta(department);
                CREATE INDEX IF NOT EXISTS idx_wf_schedule_meta_team
                    ON wf_schedule_meta(team);
                CREATE INDEX IF NOT EXISTS idx_wf_schedule_meta_project
                    ON wf_schedule_meta(project);
                CREATE INDEX IF NOT EXISTS idx_wf_schedule_meta_task_type
                    ON wf_schedule_meta(task_type);
                CREATE INDEX IF NOT EXISTS idx_wf_schedule_meta_status
                    ON wf_schedule_meta(status);

                CREATE TABLE IF NOT EXISTS wf_schedule_history (
                    id              TEXT PRIMARY KEY,
                    schedule_id     TEXT NOT NULL REFERENCES wf_schedule_meta(id) ON DELETE CASCADE,
                    cron_job_id     TEXT NOT NULL,
                    status          TEXT NOT NULL DEFAULT 'pending',
                    started_at      TEXT DEFAULT '',
                    finished_at     TEXT DEFAULT '',
                    duration_sec    REAL DEFAULT 0.0,
                    output_summary  TEXT DEFAULT '',
                    error_message   TEXT DEFAULT '',
                    created_at      TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wf_schedule_history_schedule
                    ON wf_schedule_history(schedule_id);
                CREATE INDEX IF NOT EXISTS idx_wf_schedule_history_cron
                    ON wf_schedule_history(cron_job_id);
            """)
            conn.commit()
        finally:
            conn.close()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        # Parse tags JSON string -> list
        if isinstance(d.get("tags"), str):
            try:
                d["tags"] = json.loads(d["tags"])
            except (json.JSONDecodeError, TypeError):
                d["tags"] = []
        return d

    def create(
        self,
        cron_job_id: str,
        name: str,
        description: str = "",
        department: str = "",
        team: str = "",
        project: str = "",
        task_type: str = "",
        tags: list[str] | None = None,
        schedule_type: str = "cron",
        status: str = "unknown",
    ) -> dict:
        """Store schedule metadata record."""
        record_id = str(uuid.uuid4())
        now = self._now()
        tags_json = json.dumps(tags or [])
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO wf_schedule_meta
                   (id, cron_job_id, name, description, department, team, project,
                    task_type, tags, schedule_type, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record_id, cron_job_id, name, description,
                    department, team, project, task_type,
                    tags_json, schedule_type, status, now, now,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM wf_schedule_meta WHERE id = ?", (record_id,)
            ).fetchone()
            return self._row_to_dict(row) if row else {}
        finally:
            conn.close()

    def get(self, schedule_id: str) -> dict | None:
        """Retrieve by internal schedule ID."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM wf_schedule_meta WHERE id = ?", (schedule_id,)
            ).fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            conn.close()

    def get_by_cron_job_id(self, cron_job_id: str) -> dict | None:
        """Retrieve by Hermes cron job ID."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM wf_schedule_meta WHERE cron_job_id = ?", (cron_job_id,)
            ).fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            conn.close()

    def update(self, schedule_id: str, **kwargs) -> dict:
        """Update any metadata field(s). Returns full updated record."""
        allowed = {
            "name", "description", "department", "team", "project",
            "task_type", "tags", "schedule_type", "status",
            "next_run", "last_run", "last_status",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            existing = self.get(schedule_id)
            return existing or {}

        now = self._now()
        updates["updated_at"] = now

        # Special handling for tags (list -> JSON string)
        if "tags" in updates and isinstance(updates["tags"], list):
            updates["tags"] = json.dumps(updates["tags"])

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [schedule_id]

        conn = self._connect()
        try:
            conn.execute(
                f"UPDATE wf_schedule_meta SET {set_clause} WHERE id = ?",
                values,
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM wf_schedule_meta WHERE id = ?", (schedule_id,)
            ).fetchone()
            return self._row_to_dict(row) if row else {}
        finally:
            conn.close()

    def delete(self, schedule_id: str) -> bool:
        """Delete schedule metadata. Returns True if a row was removed."""
        conn = self._connect()
        try:
            cur = conn.execute(
                "DELETE FROM wf_schedule_meta WHERE id = ?", (schedule_id,)
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def list(self, filters: dict | None = None) -> list[dict]:
        """List schedules with optional filtering.

        Supported filter keys:
          status, department, team, project, task_type, tags, search, schedule_type
        """
        conditions = []
        params = []

        f = filters or {}

        if f.get("status"):
            conditions.append("status = ?")
            params.append(f["status"])

        if f.get("department"):
            conditions.append("department = ?")
            params.append(f["department"])

        if f.get("team"):
            conditions.append("team = ?")
            params.append(f["team"])

        if f.get("project"):
            conditions.append("project LIKE ?")
            params.append(f"%{f['project']}%")

        if f.get("task_type"):
            conditions.append("task_type = ?")
            params.append(f["task_type"])

        if f.get("schedule_type"):
            conditions.append("schedule_type = ?")
            params.append(f["schedule_type"])

        if f.get("tags"):
            # Tags filter: match any tag in the JSON array
            for tag in f["tags"] if isinstance(f["tags"], list) else [f["tags"]]:
                conditions.append("tags LIKE ?")
                params.append(f"%{tag}%")

        if f.get("search"):
            conditions.append(
                "(name LIKE ? OR description LIKE ? OR cron_job_id LIKE ?)"
            )
            search_term = f"%{f['search']}%"
            params.extend([search_term, search_term, search_term])

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM wf_schedule_meta {where_clause} ORDER BY updated_at DESC",
                params,
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def list_classifications(self) -> dict:
        """Return unique values for each classification dimension.

        Returns:
            {departments: [str], teams: [str], projects: [str], task_types: [str]}
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT DISTINCT department, team, project, task_type FROM wf_schedule_meta"
            ).fetchall()
        finally:
            conn.close()

        departments: set[str] = set()
        teams: set[str] = set()
        projects: set[str] = set()
        task_types: set[str] = set()

        for r in rows:
            if r["department"]:
                departments.add(r["department"])
            if r["team"]:
                teams.add(r["team"])
            if r["project"]:
                projects.add(r["project"])
            if r["task_type"]:
                task_types.add(r["task_type"])

        # Also collect from schedule_meta table directly
        conn = self._connect()
        try:
            dept_rows = conn.execute(
                "SELECT DISTINCT department FROM wf_schedule_meta WHERE department != '' ORDER BY department"
            ).fetchall()
            departments = {r["department"] for r in dept_rows if r["department"]}

            team_rows = conn.execute(
                "SELECT DISTINCT team FROM wf_schedule_meta WHERE team != '' ORDER BY team"
            ).fetchall()
            teams = {r["team"] for r in team_rows if r["team"]}

            project_rows = conn.execute(
                "SELECT DISTINCT project FROM wf_schedule_meta WHERE project != '' ORDER BY project"
            ).fetchall()
            projects = {r["project"] for r in project_rows if r["project"]}

            tt_rows = conn.execute(
                "SELECT DISTINCT task_type FROM wf_schedule_meta WHERE task_type != '' ORDER BY task_type"
            ).fetchall()
            task_types = {r["task_type"] for r in tt_rows if r["task_type"]}
        finally:
            conn.close()

        return {
            "departments": sorted(departments),
            "teams": sorted(teams),
            "projects": sorted(projects),
            "task_types": sorted(task_types),
        }

    # ── History Methods ──────────────────────────────────────────────

    def record_run(
        self,
        schedule_id: str,
        cron_job_id: str,
        status: str = "pending",
        started_at: str = "",
        finished_at: str = "",
        duration_sec: float = 0.0,
        output_summary: str = "",
        error_message: str = "",
    ) -> dict:
        """Record a run in the schedule history."""
        record_id = str(uuid.uuid4())
        now = self._now()
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO wf_schedule_history
                   (id, schedule_id, cron_job_id, status, started_at, finished_at,
                    duration_sec, output_summary, error_message, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record_id, schedule_id, cron_job_id, status,
                    started_at, finished_at, duration_sec,
                    output_summary, error_message, now,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM wf_schedule_history WHERE id = ?", (record_id,)
            ).fetchone()
            return dict(row) if row else {}
        finally:
            conn.close()

    def get_run_history(self, schedule_id: str, limit: int = 20) -> list[dict]:
        """Get recent run history for a schedule."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM wf_schedule_history WHERE schedule_id = ? ORDER BY created_at DESC LIMIT ?",
                (schedule_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Stats ────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Aggregate stats: total, by status, by department."""
        conn = self._connect()
        try:
            total = conn.execute(
                "SELECT COUNT(*) as c FROM wf_schedule_meta"
            ).fetchone()["c"]

            by_status = {}
            for r in conn.execute(
                "SELECT status, COUNT(*) as c FROM wf_schedule_meta GROUP BY status"
            ).fetchall():
                by_status[r["status"]] = r["c"]

            by_department = {}
            for r in conn.execute(
                "SELECT department, COUNT(*) as c FROM wf_schedule_meta WHERE department != '' GROUP BY department"
            ).fetchall():
                by_department[r["department"]] = r["c"]

            return {
                "total": total,
                "by_status": by_status,
                "by_department": by_department,
            }
        finally:
            conn.close()
