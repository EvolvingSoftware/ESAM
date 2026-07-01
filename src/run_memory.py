"""Run Memory — Cross-run persistent state for workflows.

Provides a key-value store (namespaced per workflow) that persists
across runs, enabling workflows to read and write long-lived state.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from database import get_connection

__all__ = ["RunMemory"]


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


MEMORY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS wf_memory (
    id          TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    run_id      TEXT,
    key         TEXT NOT NULL,
    value_json  TEXT NOT NULL,
    tags        TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(workflow_id, key)
);

CREATE INDEX IF NOT EXISTS idx_wf_memory_workflow ON wf_memory(workflow_id);
CREATE INDEX IF NOT EXISTS idx_wf_memory_run ON wf_memory(workflow_id, run_id);
CREATE INDEX IF NOT EXISTS idx_wf_memory_tags ON wf_memory(workflow_id, tags);
"""


class RunMemory:
    """Cross-run persistent memory store for workflows.

    Stores JSON-serializable values keyed by (workflow_id, key) with
    optional run_id attribution and tag-based filtering.
    """

    def __init__(self) -> None:
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        conn = get_connection()
        conn.executescript(MEMORY_SCHEMA_SQL)
        conn.commit()

    # ------------------------------------------------------------------
    # Core CRUD
    # ------------------------------------------------------------------

    def set(
        self,
        workflow_id: str,
        key: str,
        value: Any,
        run_id: Optional[str] = None,
        tags: str = "",
    ) -> dict:
        """Upsert a memory entry by (workflow_id, key).

        Returns the full row dict of the inserted/updated entry.
        """
        conn = get_connection()
        now = _now()
        value_str = json.dumps(value, default=str)
        existing = conn.execute(
            "SELECT id FROM wf_memory WHERE workflow_id = ? AND key = ?",
            (workflow_id, key),
        ).fetchone()

        if existing:
            conn.execute(
                """UPDATE wf_memory
                   SET value_json = ?, tags = ?, run_id = COALESCE(?, run_id), updated_at = ?
                   WHERE workflow_id = ? AND key = ?""",
                (value_str, tags, run_id, now, workflow_id, key),
            )
        else:
            mid = _new_id()
            conn.execute(
                """INSERT INTO wf_memory
                   (id, workflow_id, run_id, key, value_json, tags, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (mid, workflow_id, run_id, key, value_str, tags, now, now),
            )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM wf_memory WHERE workflow_id = ? AND key = ?",
            (workflow_id, key),
        ).fetchone()
        return dict(row)

    def get(self, workflow_id: str, key: str) -> Any:
        """Get the parsed JSON value for a memory key.

        Returns the deserialized Python value, or None if the key
        does not exist.
        """
        row = self.get_raw(workflow_id, key)
        if row is None:
            return None
        try:
            return json.loads(row["value_json"])
        except (json.JSONDecodeError, TypeError):
            return row["value_json"]

    def get_raw(self, workflow_id: str, key: str) -> Optional[dict]:
        """Get the full memory row as a dict, or None if missing."""
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM wf_memory WHERE workflow_id = ? AND key = ?",
            (workflow_id, key),
        ).fetchone()
        return dict(row) if row else None

    def delete(self, workflow_id: str, key: str) -> bool:
        """Delete a single memory key.

        Returns True if a row was deleted, False otherwise.
        """
        conn = get_connection()
        cur = conn.execute(
            "DELETE FROM wf_memory WHERE workflow_id = ? AND key = ?",
            (workflow_id, key),
        )
        conn.commit()
        return cur.rowcount > 0

    def list_keys(self, workflow_id: str, tag_filter: str = "") -> list[str]:
        """List all memory keys for a workflow, optionally filtered by tags.

        Args:
            workflow_id: The workflow to list keys for.
            tag_filter: Comma-separated tag substring filter. Only keys whose
                tags column contains all tag values (as substrings) are returned.

        Returns:
            A list of key strings.
        """
        conn = get_connection()
        if tag_filter:
            tags = [t.strip() for t in tag_filter.split(",") if t.strip()]
            clauses = []
            params: list[Any] = [workflow_id]
            for tag in tags:
                clauses.append("tags LIKE ?")
                params.append(f"%{tag}%")
            where = " AND ".join(clauses)
            rows = conn.execute(
                f"SELECT key FROM wf_memory WHERE workflow_id = ? AND {where} ORDER BY key",
                params,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT key FROM wf_memory WHERE workflow_id = ? ORDER BY key",
                (workflow_id,),
            ).fetchall()
        return [r["key"] for r in rows]

    def get_all(self, workflow_id: str) -> list[dict]:
        """Get all memory entries for a workflow as a list of row dicts."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM wf_memory WHERE workflow_id = ? ORDER BY key",
            (workflow_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Advanced operations
    # ------------------------------------------------------------------

    def increment(
        self,
        workflow_id: str,
        key: str,
        delta: int = 1,
        run_id: Optional[str] = None,
    ) -> int:
        """Atomically increment a numeric memory value.

        If the key does not exist, it is created with value ``delta``.
        Returns the new integer value after increment.
        """
        conn = get_connection()
        now = _now()

        # Ensure row exists if not already present
        existing = conn.execute(
            "SELECT id, value_json FROM wf_memory WHERE workflow_id = ? AND key = ?",
            (workflow_id, key),
        ).fetchone()

        if existing:
            current_val = json.loads(existing["value_json"])
            try:
                new_val = int(current_val) + delta
            except (TypeError, ValueError):
                new_val = delta
            conn.execute(
                "UPDATE wf_memory SET value_json = ?, updated_at = ? WHERE workflow_id = ? AND key = ?",
                (json.dumps(new_val), now, workflow_id, key),
            )
        else:
            new_val = delta
            mid = _new_id()
            conn.execute(
                """INSERT INTO wf_memory
                   (id, workflow_id, run_id, key, value_json, tags, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, '', ?, ?)""",
                (mid, workflow_id, run_id, key, json.dumps(new_val), now, now),
            )
        conn.commit()
        return new_val

    def append(
        self,
        workflow_id: str,
        key: str,
        value: Any,
        run_id: Optional[str] = None,
    ) -> list:
        """Append a value to a JSON array memory entry.

        If the key does not exist, it is initialised as ``[value]``.
        If the existing value is not a list, it is wrapped in one.
        Returns the updated list.
        """
        conn = get_connection()
        now = _now()

        existing = conn.execute(
            "SELECT id, value_json FROM wf_memory WHERE workflow_id = ? AND key = ?",
            (workflow_id, key),
        ).fetchone()

        if existing:
            try:
                arr = json.loads(existing["value_json"])
                if not isinstance(arr, list):
                    arr = [arr]
            except (json.JSONDecodeError, TypeError):
                arr = []
            arr.append(value)
            conn.execute(
                "UPDATE wf_memory SET value_json = ?, updated_at = ? WHERE workflow_id = ? AND key = ?",
                (json.dumps(arr), now, workflow_id, key),
            )
        else:
            arr = [value]
            mid = _new_id()
            conn.execute(
                """INSERT INTO wf_memory
                   (id, workflow_id, run_id, key, value_json, tags, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, '', ?, ?)""",
                (mid, workflow_id, run_id, key, json.dumps(arr), now, now),
            )
        conn.commit()
        return arr

    def get_for_run(self, workflow_id: str, run_id: str) -> dict:
        """Get all memory entries set during a specific run.

        Returns a dict mapping keys to parsed values.
        """
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM wf_memory WHERE workflow_id = ? AND run_id = ? ORDER BY key",
            (workflow_id, run_id),
        ).fetchall()
        result: dict[str, Any] = {}
        for r in rows:
            row = dict(r)
            try:
                result[row["key"]] = json.loads(row["value_json"])
            except (json.JSONDecodeError, TypeError):
                result[row["key"]] = row["value_json"]
        return result

    def migrate_keys(
        self,
        workflow_id: str,
        old_prefix: str,
        new_prefix: str,
    ) -> int:
        """Rename key prefix for a workflow (e.g. when story ID changes).

        Args:
            workflow_id: The workflow whose keys to migrate.
            old_prefix: The current prefix (e.g. ``'stories.old-id.'``).
            new_prefix: The replacement prefix (e.g. ``'stories.new-id.'``).

        Returns:
            The number of keys migrated.
        """
        conn = get_connection()
        now = _now()

        rows = conn.execute(
            "SELECT * FROM wf_memory WHERE workflow_id = ? AND key LIKE ?",
            (workflow_id, f"{old_prefix}%"),
        ).fetchall()

        count = 0
        for r in rows:
            row = dict(r)
            old_key = row["key"]
            new_key = old_key.replace(old_prefix, new_prefix, 1)
            if new_key == old_key:
                continue
            # Delete potential conflicting new key first
            conn.execute(
                "DELETE FROM wf_memory WHERE workflow_id = ? AND key = ?",
                (workflow_id, new_key),
            )
            # Update the existing key
            conn.execute(
                "UPDATE wf_memory SET key = ?, updated_at = ? WHERE id = ?",
                (new_key, now, row["id"]),
            )
            count += 1

        if count:
            conn.commit()
        return count
