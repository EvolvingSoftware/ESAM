"""CitationEngine — Sequential citation ID generation and DB persistence.

Assigns stable, sequential citation IDs (S001..SXXX) to content items
and stores them in the wf_citation_map table for cross-session use.
"""

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from database import get_connection

logger = logging.getLogger(__name__)


def _fmt_citation_id(prefix: str, number: int) -> str:
    """Format a citation ID like S001, S042, S123."""
    return f"{prefix}{number:03d}"


class CitationEngine:
    """Manages sequential citation ID generation and persistence."""

    def __init__(self, db_conn=None) -> None:
        self._conn = db_conn
        self._ensure_table()

    def _ensure_table(self) -> None:
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS wf_citation_map (
                id TEXT PRIMARY KEY,
                source_id TEXT, item_id TEXT,
                citation_id TEXT NOT NULL UNIQUE,
                url TEXT NOT NULL, title TEXT DEFAULT '',
                content_hash TEXT DEFAULT '', fetch_run_id TEXT,
                created_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_wf_citation_map_ids ON wf_citation_map(citation_id);
            CREATE INDEX IF NOT EXISTS idx_wf_citation_map_run ON wf_citation_map(fetch_run_id);
        """)
        conn.commit()

    def _get_conn(self):
        if self._conn is not None:
            return self._conn
        return get_connection()

    def get_next_number(self, prefix: str = "S") -> int:
        """Find the next available sequence number for the given prefix.

        Scans existing citation_ids matching ``{prefix}XXX`` and returns
        the smallest unused number starting at 1.
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT citation_id FROM wf_citation_map WHERE citation_id LIKE ?",
            (f"{prefix}%",),
        ).fetchall()
        seen: set[int] = set()
        for row in rows:
            cid: str = row["citation_id"]
            num_str = cid[len(prefix):]
            if num_str.isdigit():
                seen.add(int(num_str))
        n = 1
        while n in seen:
            n += 1
        return n

    def generate_ids(
        self,
        items: list[dict[str, Any]],
        prefix: str = "S",
        start_number: int | None = None,
    ) -> list[dict[str, Any]]:
        """Assign sequential citation IDs to a list of extracted items.

        Args:
            items: List of dicts with at least ``url`` and ``title``.
            prefix: Prefix for citation IDs (default ``"S"``).
            start_number: Start number. If ``None``, auto-detects
                the next available number.

        Returns:
            List of item dicts with ``citation_id`` added.
        """
        if start_number is None:
            start_number = self.get_next_number(prefix)

        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        results: list[dict[str, Any]] = []

        for i, item in enumerate(items):
            number = start_number + i
            citation_id = _fmt_citation_id(prefix, number)
            url = item.get("url", "")
            title = item.get("title", "")
            source_id = item.get("source_id", "")
            content = item.get("content", item.get("body_extracted", ""))
            content_hash = hashlib.sha256(
                (content or "").encode("utf-8")
            ).hexdigest()[:16]
            fetch_run_id = item.get("fetch_run_id", "")
            item_id = item.get("id", item.get("item_id", str(uuid.uuid4())[:12]))

            record = {
                "id": str(uuid.uuid4()),
                "source_id": source_id,
                "item_id": item_id,
                "citation_id": citation_id,
                "url": url,
                "title": title,
                "content_hash": content_hash,
                "fetch_run_id": fetch_run_id,
                "created_at": now,
            }

            try:
                conn.execute(
                    """INSERT INTO wf_citation_map
                       (id, source_id, item_id, citation_id, url, title,
                        content_hash, fetch_run_id, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        record["id"],
                        record["source_id"],
                        record["item_id"],
                        record["citation_id"],
                        record["url"],
                        record["title"],
                        record["content_hash"],
                        record["fetch_run_id"],
                        record["created_at"],
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "Failed to store citation %s for %s: %s",
                    citation_id,
                    url,
                    exc,
                )
                # If UNIQUE constraint fails on citation_id, skip
                # and try the next number
                continue

            item["citation_id"] = citation_id
            results.append(item)

        conn.commit()
        return results

    def get_map(
        self, fetch_run_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Retrieve the citation map, optionally filtered by fetch run.

        Args:
            fetch_run_id: If provided, only return citations for this run.

        Returns:
            List of dicts with all wf_citation_map columns.
        """
        conn = self._get_conn()
        if fetch_run_id:
            rows = conn.execute(
                "SELECT * FROM wf_citation_map WHERE fetch_run_id = ? ORDER BY citation_id",
                (fetch_run_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM wf_citation_map ORDER BY citation_id"
            ).fetchall()
        return [dict(r) for r in rows]

    def resolve(self, citation_id: str) -> dict[str, Any] | None:
        """Resolve a citation ID (e.g. ``S042``) to its full metadata.

        Returns:
            The wf_citation_map row as a dict, or ``None`` if not found.
        """
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM wf_citation_map WHERE citation_id = ?",
            (citation_id,),
        ).fetchone()
        return dict(row) if row else None

    def export_map(
        self, fetch_run_id: str | None = None
    ) -> dict[str, dict[str, str]]:
        """Export the citation map as a simple dict for prompt injection.

        Returns:
            Dict like ``{"S001": {"url": "https://...", "title": "..."}, ...}``
        """
        rows = self.get_map(fetch_run_id)
        result: dict[str, dict[str, str]] = {}
        for r in rows:
            result[r["citation_id"]] = {
                "url": r["url"],
                "title": r["title"],
            }
        return result
