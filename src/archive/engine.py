"""Archive Engine — stores and retrieves published newsletter editions.

Each edition is saved to disk (HTML, markdown, metadata) and recorded
in the ``wf_archived_editions`` database table.  Permalink URLs are
generated using the Hermes archive domain pattern.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from database import get_connection

__all__ = ["ArchiveEngine"]

ARCHIVE_DOMAIN = "hermes.local"


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: Any) -> dict:
    return dict(row) if row else {}


def _rows_to_dicts(rows: list[Any]) -> list[dict]:
    return [dict(r) for r in rows]


class ArchiveEngine:
    """Persists newsletter editions to disk and tracks them in the database.

    Typical usage::

        engine = ArchiveEngine()
        result = engine.store(
            edition_id="newsletter-2025-03-01",
            subject="Daily Signal: AI Frontiers",
            body_html="<h1>Hello</h1>",
            body_markdown="# Hello",
            run_id="run-abc123",
            metadata={"citation_count": 5, "source_count": 3, "item_count": 12},
        )
        edition = engine.get("newsletter-2025-03-01")
    """

    def __init__(self, archive_dir: str | None = None) -> None:
        self.archive_dir = Path(
            archive_dir or os.path.join(Path.home(), ".hermes", "esam", "archives")
        ).expanduser()
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    # ── Public API ──────────────────────────────────────────────────

    def store(
        self,
        edition_id: str,
        subject: str,
        body_html: str,
        body_markdown: str,
        run_id: str,
        metadata: dict | None = None,
    ) -> dict:
        """Persist a newsletter edition to disk and DB.

        Args:
            edition_id: Unique identifier for this edition (e.g. ``"nl-001"``).
            subject:        Edition subject / headline.
            body_html:      Rendered HTML body.
            body_markdown:  Markdown source.
            run_id:         Workflow run that produced this edition.
            metadata:       Optional dict with ``citation_count``,
                            ``source_count``, ``item_count``, and any
                            extra keys.

        Returns:
            A dict with ``id``, ``path``, ``permalink``, and ``url`` keys.
        """
        meta = dict(metadata or {})

        # ── Save files to disk ──────────────────────────────────────
        edition_dir = self.archive_dir / edition_id
        edition_dir.mkdir(parents=True, exist_ok=True)

        html_path = edition_dir / "index.html"
        md_path = edition_dir / "body.md"
        meta_path = edition_dir / "metadata.json"

        html_path.write_text(body_html, encoding="utf-8")
        md_path.write_text(body_markdown, encoding="utf-8")
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        # ── Compute permalink ───────────────────────────────────────
        archive_path_str = str(edition_dir.resolve())
        permalink = f"https://{ARCHIVE_DOMAIN}/archives/{edition_id}"

        # ── Record in DB ────────────────────────────────────────────
        conn = get_connection()
        now = _now()
        conn.execute(
            """
            INSERT OR REPLACE INTO wf_archived_editions
                (id, run_id, edition_number, date, subject,
                 body_html, body_markdown, archive_path, permalink,
                 citation_count, source_count, item_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edition_id,
                run_id,
                meta.get("edition_number"),
                meta.get("date", now),
                subject,
                body_html,
                body_markdown,
                archive_path_str,
                permalink,
                meta.get("citation_count", 0),
                meta.get("source_count", 0),
                meta.get("item_count", 0),
                now,
            ),
        )
        conn.commit()

        # ── Rebuild index after each store ──────────────────────────
        from .index import ArchiveIndex

        ArchiveIndex(archive_dir=str(self.archive_dir)).rebuild()

        return {
            "id": edition_id,
            "path": archive_path_str,
            "permalink": permalink,
            "url": permalink,
        }

    def get(self, edition_id: str) -> dict:
        """Load a single edition from the DB.

        Returns an empty dict if not found.
        """
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM wf_archived_editions WHERE id = ?", (edition_id,)
        ).fetchone()
        return _row_to_dict(row)

    def list(self, limit: int = 20, offset: int = 0) -> list[dict]:
        """List archived editions, newest first."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM wf_archived_editions ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return _rows_to_dicts(rows)

    def delete(self, edition_id: str) -> bool:
        """Remove an edition from the DB.

        Does NOT delete files from disk (they remain as an archival record).
        Returns True if a row was deleted.
        """
        conn = get_connection()
        cursor = conn.execute(
            "DELETE FROM wf_archived_editions WHERE id = ?", (edition_id,)
        )
        conn.commit()
        return cursor.rowcount > 0

    def get_by_run(self, run_id: str) -> dict | None:
        """Get an edition by run ID.  Returns None if not found."""
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM wf_archived_editions WHERE run_id = ?", (run_id,)
        ).fetchone()
        return _row_to_dict(row) or None

    def get_latest(self) -> dict | None:
        """Get the most recently archived edition.  Returns None if none."""
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM wf_archived_editions ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return _row_to_dict(row) or None
