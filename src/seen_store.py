"""Seen Store — URL hash dedup across workflow runs.

Tracks which URLs have been seen by each workflow using SHA256
hashes of normalized URLs and titles, enabling O(1) duplicate
detection and cross-run analytics.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from database import get_connection


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SeenStore:
    """URL hash dedup store for workflow runs.

    All CRUD methods return dicts. Uses raw SQL with sqlite3,
    with INSERT OR UPDATE (idempotent) semantics.
    """

    # ── Hashing Helpers ─────────────────────────────────────────

    @staticmethod
    def hash_url(url: str) -> str:
        """Normalize a URL then return its SHA256 hex digest.

        Normalization: lowercase, strip trailing slash, strip leading ``www.``
        from the host portion.
        """
        normalized = url.strip().lower()
        # Strip trailing slash
        if normalized.endswith("/"):
            normalized = normalized[:-1]
        # Strip www. prefix from host
        if normalized.startswith("http://www."):
            normalized = normalized.replace("http://www.", "http://", 1)
        elif normalized.startswith("https://www."):
            normalized = normalized.replace("https://www.", "https://", 1)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def hash_title(title: str) -> str:
        """Normalize a title then return its SHA256 hex digest.

        Normalization: lowercase, strip leading/trailing whitespace.
        """
        normalized = title.strip().lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    # ── Single-Item Operations ──────────────────────────────────

    def check_url(self, workflow_id: str, url: str) -> dict:
        """Check if a URL has been seen for this workflow.

        Returns ``{seen: bool, hit_count: int, first_seen: str, last_seen: str}``.
        """
        url_hash = self.hash_url(url)
        conn = get_connection()
        row = conn.execute(
            "SELECT hit_count, first_seen_at, last_seen_at FROM wf_seen_store "
            "WHERE workflow_id = ? AND url_hash = ?",
            (workflow_id, url_hash),
        ).fetchone()
        if row:
            return {
                "seen": True,
                "hit_count": row["hit_count"],
                "first_seen": row["first_seen_at"],
                "last_seen": row["last_seen_at"],
            }
        return {
            "seen": False,
            "hit_count": 0,
            "first_seen": "",
            "last_seen": "",
        }

    def record(
        self,
        workflow_id: str,
        url: str,
        title: str | None,
        run_id: str,
    ) -> dict:
        """Record a URL as seen, or update hit count and last_seen if already seen.

        Returns the current row dict from the database.
        """
        url_hash = self.hash_url(url)
        title_hash = self.hash_title(title or "")
        now = _now()
        conn = get_connection()

        # Try INSERT; on conflict (workflow_id, url_hash) update hit_count and timestamps
        conn.execute(
            """INSERT INTO wf_seen_store
               (id, workflow_id, url_hash, title_hash, url, title,
                first_seen_run_id, last_seen_run_id, hit_count,
                first_seen_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
               ON CONFLICT(workflow_id, url_hash) DO UPDATE SET
                hit_count = hit_count + 1,
                last_seen_run_id = excluded.last_seen_run_id,
                last_seen_at = excluded.last_seen_at,
                title = COALESCE(excluded.title, title),
                title_hash = COALESCE(excluded.title_hash, title_hash)
            """,
            (
                _new_id(),
                workflow_id,
                url_hash,
                title_hash,
                url,
                title,
                run_id,
                run_id,
                now,
                now,
            ),
        )
        conn.commit()

        # Fetch and return the current row
        row = conn.execute(
            "SELECT * FROM wf_seen_store WHERE workflow_id = ? AND url_hash = ?",
            (workflow_id, url_hash),
        ).fetchone()
        return dict(row) if row else {
            "error": "failed_to_record",
            "workflow_id": workflow_id,
            "url": url,
        }

    # ── Batch Operations ────────────────────────────────────────

    def check_batch(
        self,
        workflow_id: str,
        urls: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Check a batch of URLs for seen status.

        ``urls`` is a list of ``{url, title}`` dicts. Returns the same
        list with ``{seen: bool}`` added to each item.
        """
        if not urls:
            return urls

        conn = get_connection()
        workflow_id_param: str = workflow_id

        result: list[dict[str, Any]] = []
        for item in urls:
            url = item.get("url", "")
            if not url:
                item["seen"] = False
                result.append(item)
                continue

            url_hash = self.hash_url(url)
            row = conn.execute(
                "SELECT 1 FROM wf_seen_store WHERE workflow_id = ? AND url_hash = ?",
                (workflow_id_param, url_hash),
            ).fetchone()
            item["seen"] = row is not None
            result.append(item)

        return result

    def bulk_record(
        self,
        workflow_id: str,
        items: list[dict[str, Any]],
        run_id: str,
    ) -> dict[str, int]:
        """Record multiple URLs in a single transaction.

        ``items`` is a list of ``{url, title}`` dicts.

        Returns ``{recorded: int, updated: int}``.
        """
        if not items:
            return {"recorded": 0, "updated": 0}

        conn = get_connection()
        now = _now()
        recorded = 0
        updated = 0

        for item in items:
            url = item.get("url", "")
            title = item.get("title")
            if not url:
                continue

            url_hash = self.hash_url(url)
            title_hash = self.hash_title(title or "")

            # Check if exists
            existing = conn.execute(
                "SELECT id, hit_count FROM wf_seen_store "
                "WHERE workflow_id = ? AND url_hash = ?",
                (workflow_id, url_hash),
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE wf_seen_store SET
                       hit_count = hit_count + 1,
                       last_seen_run_id = ?,
                       last_seen_at = ?,
                       title = COALESCE(?, title),
                       title_hash = COALESCE(?, title_hash)
                       WHERE workflow_id = ? AND url_hash = ?
                    """,
                    (run_id, now, title, title_hash, workflow_id, url_hash),
                )
                updated += 1
            else:
                conn.execute(
                    """INSERT INTO wf_seen_store
                       (id, workflow_id, url_hash, title_hash, url, title,
                        first_seen_run_id, last_seen_run_id, hit_count,
                        first_seen_at, last_seen_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        _new_id(),
                        workflow_id,
                        url_hash,
                        title_hash,
                        url,
                        title,
                        run_id,
                        run_id,
                        now,
                        now,
                    ),
                )
                recorded += 1

        conn.commit()
        return {"recorded": recorded, "updated": updated}

    # ── Analytics ───────────────────────────────────────────────

    def get_stats(self, workflow_id: str) -> dict[str, int]:
        """Get seen-store statistics for a workflow.

        Returns ``{total: int, unique: int, seen_multiple: int}``.
        """
        conn = get_connection()
        total = conn.execute(
            "SELECT COALESCE(SUM(hit_count), 0) FROM wf_seen_store WHERE workflow_id = ?",
            (workflow_id,),
        ).fetchone()[0]

        unique = conn.execute(
            "SELECT COUNT(*) FROM wf_seen_store WHERE workflow_id = ?",
            (workflow_id,),
        ).fetchone()[0]

        seen_multiple = conn.execute(
            "SELECT COUNT(*) FROM wf_seen_store WHERE workflow_id = ? AND hit_count > 1",
            (workflow_id,),
        ).fetchone()[0]

        return {
            "total": int(total),
            "unique": int(unique),
            "seen_multiple": int(seen_multiple),
        }
