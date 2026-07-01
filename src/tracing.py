"""Hierarchical tracing for agent workflow system.

Provides span-based tracing with parent-child relationships for tracking
agent execution steps, LLM calls, and tool invocations.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from database import get_connection

__all__ = ["TraceStore"]


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row) if row else {}


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trace_spans (
    id              TEXT PRIMARY KEY,
    trace_id        TEXT NOT NULL,
    span_name       TEXT NOT NULL,
    span_type       TEXT DEFAULT 'step',
    parent_span_id  TEXT,
    step_id         TEXT,
    run_id          TEXT,
    input_data      TEXT DEFAULT '{}',
    output_data     TEXT DEFAULT '{}',
    tokens_input    INTEGER DEFAULT 0,
    tokens_output   INTEGER DEFAULT 0,
    cost_cents      INTEGER DEFAULT 0,
    duration_ms     INTEGER DEFAULT 0,
    model_used      TEXT DEFAULT '',
    status          TEXT DEFAULT 'pending',
    error_message   TEXT DEFAULT '',
    started_at      TEXT NOT NULL,
    ended_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_trace_spans_trace ON trace_spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_trace_spans_run ON trace_spans(run_id);
CREATE INDEX IF NOT EXISTS idx_trace_spans_parent ON trace_spans(parent_span_id);
CREATE INDEX IF NOT EXISTS idx_trace_spans_step ON trace_spans(step_id);
"""


class TraceStore:
    """Hierarchical tracing store backed by SQLite."""

    def __init__(self) -> None:
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        conn = get_connection()
        conn.executescript(SCHEMA_SQL)
        conn.commit()

    def start_span(
        self,
        trace_id: str,
        span_name: str,
        span_type: str = "step",
        parent_span_id: Optional[str] = None,
        step_id: Optional[str] = None,
        run_id: Optional[str] = None,
        input_data: str = "{}",
    ) -> str:
        conn = get_connection()
        span_id = _new_id()
        now = _now()
        conn.execute(
            """INSERT INTO trace_spans
               (id, trace_id, span_name, span_type, parent_span_id,
                step_id, run_id, input_data, started_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'in_progress')""",
            (span_id, trace_id, span_name, span_type, parent_span_id,
             step_id, run_id, input_data, now),
        )
        conn.commit()
        return span_id

    def end_span(
        self,
        span_id: str,
        output_data: str = "{}",
        tokens_input: int = 0,
        tokens_output: int = 0,
        cost_cents: int = 0,
        duration_ms: int = 0,
        model_used: str = "",
        status: str = "completed",
        error_message: str = "",
    ) -> dict:
        conn = get_connection()
        now = _now()
        conn.execute(
            """UPDATE trace_spans SET
               output_data = ?, tokens_input = ?, tokens_output = ?,
               cost_cents = ?, duration_ms = ?, model_used = ?,
               status = ?, error_message = ?, ended_at = ?
               WHERE id = ?""",
            (output_data, tokens_input, tokens_output, cost_cents,
             duration_ms, model_used, status, error_message, now, span_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM trace_spans WHERE id = ?", (span_id,)).fetchone()
        return _row_to_dict(row)

    def get_run_traces(self, run_id: str) -> list[dict]:
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM trace_spans WHERE run_id = ? ORDER BY started_at ASC",
            (run_id,),
        ).fetchall()
        return _rows_to_dicts(rows)

    def get_trace_tree(self, trace_id: str) -> dict:
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM trace_spans WHERE trace_id = ? ORDER BY started_at ASC",
            (trace_id,),
        ).fetchall()
        spans = _rows_to_dicts(rows)

        if not spans:
            return {"trace_id": trace_id, "spans": []}

        by_id = {s["id"]: s for s in spans}
        children: dict[str | None, list[dict]] = {None: []}
        for s in spans:
            parent = s.get("parent_span_id")
            children.setdefault(parent, []).append(s)
            children.setdefault(s["id"], [])

        def build_tree(span: dict) -> dict:
            return {
                **span,
                "children": [build_tree(c) for c in children.get(span["id"], [])],
            }

        roots = children.get(None, [])
        return {
            "trace_id": trace_id,
            "spans": [build_tree(r) for r in roots],
        }

    def get_run_trace_tree(self, run_id: str) -> dict:
        conn = get_connection()
        row = conn.execute(
            "SELECT trace_id FROM trace_spans WHERE run_id = ? LIMIT 1",
            (run_id,),
        ).fetchone()
        if not row:
            return {"run_id": run_id, "trace_id": None, "spans": []}
        trace_id = row["trace_id"]
        tree = self.get_trace_tree(trace_id)
        tree["run_id"] = run_id
        return tree
