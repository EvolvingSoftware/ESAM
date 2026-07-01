"""Edition Registry Engine — records and retrieves published edition metadata."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from database import get_connection

__all__ = ["EditionRegistry"]

WFE_TABLE = "wf_editions"


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: Any) -> dict:
    return dict(row) if row else {}


def _rows_to_dicts(rows: list[Any]) -> list[dict]:
    return [dict(r) for r in rows]


class EditionRegistry:
    """Records and retrieves edition metadata in the wf_editions table.

    Typical usage::

        reg = EditionRegistry()
        edition = reg.create("wf-001", "run-abc", "Daily Signal", 5, 20, 10000, 45.5)
        latest = reg.get_latest()
        by_run = reg.get_by_run("run-abc")
    """

    def __init__(self, db_conn: Any = None) -> None:
        self._conn = db_conn

    def _get_next_number(self) -> int:
        """Compute the next edition_number (previous + 1)."""
        conn = self._conn or get_connection()
        row = conn.execute(
            f"SELECT MAX(edition_number) FROM {WFE_TABLE}"
        ).fetchone()
        return (row[0] or 0) + 1

    def create(
        self,
        workflow_id: str,
        run_id: str,
        subject: str,
        source_count: int = 0,
        item_count: int = 0,
        total_tokens: int = 0,
        duration_seconds: float = 0.0,
    ) -> dict:
        """Create a new edition record with auto-computed edition_number.

        Args:
            workflow_id:      Workflow that produced this edition.
            run_id:           Run that produced this edition.
            subject:          Edition subject / headline.
            source_count:     Number of sources processed.
            item_count:       Number of items processed.
            total_tokens:     Total tokens consumed.
            duration_seconds: Wall-clock duration in seconds.

        Returns:
            The full edition record as a dict.
        """
        edition_id = _new_id()
        edition_number = self._get_next_number()
        now = _now()

        conn = self._conn or get_connection()
        conn.execute(
            f"""INSERT INTO {WFE_TABLE}
                (id, workflow_id, run_id, edition_number, date, subject,
                 signal_ids, citation_ids, narrative_json,
                 quality_score, source_count, item_count,
                 total_tokens, duration_seconds, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                edition_id,
                workflow_id,
                run_id,
                edition_number,
                now[:10],  # date portion of ISO
                subject,
                "[]",       # signal_ids
                "[]",       # citation_ids
                "{}",       # narrative_json
                None,       # quality_score
                source_count,
                item_count,
                total_tokens,
                duration_seconds,
                now,
            ),
        )
        conn.commit()

        return {
            "id": edition_id,
            "workflow_id": workflow_id,
            "run_id": run_id,
            "edition_number": edition_number,
            "date": now[:10],
            "subject": subject,
            "signal_ids": "[]",
            "citation_ids": "[]",
            "narrative_json": "{}",
            "quality_score": None,
            "source_count": source_count,
            "item_count": item_count,
            "total_tokens": total_tokens,
            "duration_seconds": duration_seconds,
            "created_at": now,
        }

    def get(self, edition_id: str) -> dict | None:
        """Get a single edition by its ID. Returns None if not found."""
        conn = self._conn or get_connection()
        row = conn.execute(
            f"SELECT * FROM {WFE_TABLE} WHERE id = ?", (edition_id,)
        ).fetchone()
        result = _row_to_dict(row)
        return result if result else None

    def get_by_number(self, edition_number: int) -> dict | None:
        """Get an edition by its edition_number. Returns None if not found."""
        conn = self._conn or get_connection()
        row = conn.execute(
            f"SELECT * FROM {WFE_TABLE} WHERE edition_number = ?",
            (edition_number,),
        ).fetchone()
        result = _row_to_dict(row)
        return result if result else None

    def list(self, limit: int = 20, offset: int = 0) -> list[dict]:
        """List editions ordered by edition_number descending."""
        conn = self._conn or get_connection()
        rows = conn.execute(
            f"SELECT * FROM {WFE_TABLE} ORDER BY edition_number DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return _rows_to_dicts(rows)

    def get_latest(self) -> dict | None:
        """Get the most recent edition. Returns None if none exist."""
        conn = self._conn or get_connection()
        row = conn.execute(
            f"SELECT * FROM {WFE_TABLE} ORDER BY edition_number DESC LIMIT 1"
        ).fetchone()
        result = _row_to_dict(row)
        return result if result else None

    def get_by_run(self, run_id: str) -> dict | None:
        """Get an edition by its run_id. Returns None if not found."""
        conn = self._conn or get_connection()
        row = conn.execute(
            f"SELECT * FROM {WFE_TABLE} WHERE run_id = ?", (run_id,)
        ).fetchone()
        result = _row_to_dict(row)
        return result if result else None
