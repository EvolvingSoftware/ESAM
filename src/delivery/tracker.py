"""Delivery Tracker — persists delivery status to the wf_delivery_log table."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from database import get_connection

logger = logging.getLogger(__name__)

__all__ = ["DeliveryTracker"]

DELIVERY_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS wf_delivery_log (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    to_addr TEXT NOT NULL,
    subject TEXT,
    provider TEXT,
    status TEXT,
    message_id TEXT,
    error TEXT,
    sent_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wf_delivery_log_run ON wf_delivery_log(run_id);
CREATE INDEX IF NOT EXISTS idx_wf_delivery_log_message ON wf_delivery_log(message_id);
CREATE INDEX IF NOT EXISTS idx_wf_delivery_log_status ON wf_delivery_log(status);
"""


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeliveryTracker:
    """Track email delivery attempts and results.

    Writes to and reads from the ``wf_delivery_log`` table.  All
    methods are class-level, so no instance is needed::

        DeliveryTracker.record(
            run_id="run-001",
            to="user@example.com",
            subject="Hello",
            provider="smtp",
            status="sent",
            message_id="msg_abc123",
            error="",
        )
    """

    @classmethod
    def ensure_schema(cls) -> None:
        """Ensure the delivery log table exists."""
        conn = get_connection()
        conn.executescript(DELIVERY_LOG_SCHEMA)
        conn.commit()

    @classmethod
    def record(
        cls,
        run_id: str,
        to: str,
        subject: str = "",
        provider: str = "",
        status: str = "",
        message_id: str = "",
        error: str = "",
    ) -> dict[str, Any]:
        """Record a delivery attempt in the log.

        Args:
            run_id: The workflow run ID associated with this delivery.
            to: Recipient email address.
            subject: Email subject line.
            provider: Provider name (``"smtp"``, ``"sendgrid"``, etc.).
            status: Delivery status (``"sent"``, ``"failed"``, etc.).
            message_id: Unique message identifier returned by the engine.
            error: Error message if the delivery failed.

        Returns:
            The newly created log record as a dict.
        """
        cls.ensure_schema()
        conn = get_connection()
        log_id = _new_id()
        now = _now()
        conn.execute(
            """INSERT INTO wf_delivery_log
               (id, run_id, to_addr, subject, provider, status, message_id, error, sent_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (log_id, run_id, to, subject, provider, status, message_id, error, now, now),
        )
        conn.commit()
        return cls._get_by_id(log_id)

    @classmethod
    def get_status(cls, message_id: str) -> str:
        """Get the delivery status for a given message ID.

        Args:
            message_id: The message identifier to look up.

        Returns:
            Status string (``"sent"``, ``"failed"``, etc.) or
            ``"unknown"`` if not found.
        """
        conn = get_connection()
        row = conn.execute(
            "SELECT status FROM wf_delivery_log WHERE message_id = ? ORDER BY created_at DESC LIMIT 1",
            (message_id,),
        ).fetchone()
        return str(row["status"]) if row else "unknown"

    @classmethod
    def get_log(
        cls,
        run_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Retrieve delivery log entries for a run.

        Args:
            run_id: The workflow run ID to fetch logs for.
            limit: Maximum number of entries to return.
            offset: Number of entries to skip (for pagination).

        Returns:
            List of log entry dicts ordered by ``created_at`` descending.
        """
        conn = get_connection()
        rows = conn.execute(
            """SELECT * FROM wf_delivery_log
               WHERE run_id = ?
               ORDER BY created_at DESC
               LIMIT ? OFFSET ?""",
            (run_id, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    @classmethod
    def _get_by_id(cls, log_id: str) -> dict[str, Any]:
        """Fetch a single log record by its primary key."""
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM wf_delivery_log WHERE id = ?", (log_id,)
        ).fetchone()
        return dict(row) if row else {}
