"""Stories Engine — Track entities over time with change detection across runs.

Tracks stories (narrative entities) across workflow runs, detecting
changes between editions and maintaining a signal strength based on
edition count and recency.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from database import get_connection

__all__ = ["StoriesEngine"]

STORIES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS wf_stories (
    id                  TEXT PRIMARY KEY,
    workflow_id         TEXT NOT NULL,
    title               TEXT NOT NULL,
    title_hash          TEXT NOT NULL,
    first_seen_run_id   TEXT NOT NULL,
    last_seen_run_id    TEXT NOT NULL,
    edition_count       INTEGER DEFAULT 1,
    signal_strength     REAL DEFAULT 0.5,
    change_log_json     TEXT DEFAULT '[]',
    last_headline       TEXT,
    last_body_snippet   TEXT,
    sources_json        TEXT DEFAULT '[]',
    tags                TEXT DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE(workflow_id, title_hash)
);

CREATE INDEX IF NOT EXISTS idx_wf_stories_workflow ON wf_stories(workflow_id);
CREATE INDEX IF NOT EXISTS idx_wf_stories_signal ON wf_stories(workflow_id, signal_strength DESC);
CREATE INDEX IF NOT EXISTS idx_wf_stories_title_hash ON wf_stories(workflow_id, title_hash);
"""


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: Any) -> dict:
    return dict(row) if row else {}


def _rows_to_dicts(rows: list) -> list[dict]:
    return [dict(r) for r in rows]


class StoriesEngine:
    """Track entities (stories) over time across workflow runs.

    Uses fuzzy matching by SHA256 hashed title to detect when an existing
    story gains a new edition.  Each edition comparison logs headline,
    body, and source diffs into a change_log.
    """

    def __init__(self) -> None:
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        conn = get_connection()
        conn.executescript(STORIES_SCHEMA_SQL)
        conn.commit()

    # ------------------------------------------------------------------
    # Hashing
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_title(title: str) -> str:
        """Normalize and SHA256-hash a title for fuzzy matching.

        Normalization: lowercase, strip leading/trailing whitespace.
        """
        normalized = title.strip().lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # Signal computation
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_signal(edition_count: int, days_since_first: float = 0.0) -> float:
        """Compute signal strength using a logistic formula.

        ``1 / (1 + e^(-0.5 * (edition_count - 2)))``

        Clamps the result to ``[0.01, 1.0]`` so even very new stories
        have a minimal non-zero signal.
        """
        raw = 1.0 / (1.0 + math.exp(-0.5 * (edition_count - 2)))
        return max(0.01, min(1.0, raw))

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def find_or_create(
        self,
        workflow_id: str,
        title: str,
        run_id: str,
        headline: str = "",
        body: str = "",
        sources: Optional[list[str]] = None,
        tags: str = "",
    ) -> dict:
        """Find a story by fuzzy title match, or create if not found.

        If the story already exists (same ``workflow_id`` + ``title_hash``):
        - Increments ``edition_count``
        - Detects headline / body / source changes
        - Appends a change_log entry
        - Updates signal_strength
        - Updates ``last_seen_run_id`` and ``last_headline`` / ``last_body_snippet``

        Returns the full row dict of the story after the operation.
        """
        title_hash = self._hash_title(title)
        body_snippet = body[:500] if body else ""
        sources_json = json.dumps(sources or [], default=str)
        now = _now()

        conn = get_connection()

        # Try to find existing story by (workflow_id, title_hash)
        existing = conn.execute(
            "SELECT * FROM wf_stories WHERE workflow_id = ? AND title_hash = ?",
            (workflow_id, title_hash),
        ).fetchone()

        if existing:
            # ── Update existing story (new edition) ────────────────
            existing_row = dict(existing)
            old_edition_count = existing_row["edition_count"]

            # Detect changes
            old_headline = existing_row.get("last_headline") or ""
            old_body = existing_row.get("last_body_snippet") or ""
            old_sources_raw = existing_row.get("sources_json") or "[]"
            try:
                old_sources = json.loads(old_sources_raw) if isinstance(old_sources_raw, str) else old_sources_raw
            except (json.JSONDecodeError, TypeError):
                old_sources = []

            headline_diff = self._diff_text(old_headline, headline)
            body_diff = self._diff_text(old_body, body_snippet)
            sources_diff = self._diff_sources(old_sources, sources or [])

            # Build change log entry
            change_entry = {
                "edition_id": run_id,
                "date": now,
                "headline": headline,
                "headline_diff": headline_diff,
                "body_diff": body_diff,
                "sources_diff": sources_diff,
            }

            # Parse existing change log
            existing_changes_raw = existing_row.get("change_log_json") or "[]"
            try:
                existing_changes = json.loads(existing_changes_raw) if isinstance(existing_changes_raw, str) else existing_changes_raw
            except (json.JSONDecodeError, TypeError):
                existing_changes = []
            if not isinstance(existing_changes, list):
                existing_changes = []

            existing_changes.append(change_entry)

            new_edition_count = existing_row["edition_count"] + 1

            # Compute days since first seen for signal
            first_seen_str = existing_row.get("created_at", now)
            try:
                first_seen_dt = datetime.fromisoformat(first_seen_str)
                days_since_first = (datetime.now(timezone.utc) - first_seen_dt).total_seconds() / 86400.0
            except (ValueError, TypeError):
                days_since_first = 0.0

            new_signal = self._compute_signal(new_edition_count, days_since_first)

            conn.execute(
                """UPDATE wf_stories
                   SET edition_count = ?,
                       signal_strength = ?,
                       last_seen_run_id = ?,
                       last_headline = ?,
                       last_body_snippet = ?,
                       sources_json = ?,
                       tags = ?,
                       change_log_json = ?,
                       updated_at = ?
                   WHERE id = ?""",
                (
                    new_edition_count,
                    new_signal,
                    run_id,
                    headline,
                    body_snippet,
                    sources_json,
                    tags,
                    json.dumps(existing_changes, default=str),
                    now,
                    existing_row["id"],
                ),
            )
            conn.commit()

            # Fetch and return updated row
            row = conn.execute(
                "SELECT * FROM wf_stories WHERE id = ?",
                (existing_row["id"],),
            ).fetchone()
            return dict(row)

        else:
            # ── Create new story ───────────────────────────────────
            story_id = _new_id()
            signal = self._compute_signal(1, 0.0)
            change_log = []
            if headline or body_snippet:
                change_entry = {
                    "edition_id": run_id,
                    "date": now,
                    "headline": headline,
                    "headline_diff": "new",
                    "body_diff": "new" if body_snippet else "",
                    "sources_diff": "new" if sources else "",
                }
                change_log.append(change_entry)

            conn.execute(
                """INSERT INTO wf_stories
                   (id, workflow_id, title, title_hash,
                    first_seen_run_id, last_seen_run_id,
                    edition_count, signal_strength,
                    change_log_json, last_headline, last_body_snippet,
                    sources_json, tags, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    story_id,
                    workflow_id,
                    title,
                    title_hash,
                    run_id,
                    run_id,
                    1,
                    signal,
                    json.dumps(change_log, default=str),
                    headline,
                    body_snippet,
                    sources_json,
                    tags,
                    now,
                    now,
                ),
            )
            conn.commit()

            row = conn.execute(
                "SELECT * FROM wf_stories WHERE id = ?",
                (story_id,),
            ).fetchone()
            return dict(row)

    def get(self, workflow_id: str, story_id: str) -> Optional[dict]:
        """Get a single story by ID within a workflow."""
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM wf_stories WHERE id = ? AND workflow_id = ?",
            (story_id, workflow_id),
        ).fetchone()
        return _row_to_dict(row)

    def list_stories(
        self,
        workflow_id: str,
        tag_filter: str = "",
        limit: int = 50,
        sort_by: str = "signal_strength",
    ) -> list[dict]:
        """List stories for a workflow, optionally filtered by tags.

        Args:
            workflow_id: The workflow to list stories for.
            tag_filter: Optional comma-separated tag substring filter.
            limit: Maximum number of stories to return (default 50).
            sort_by: Sort column (default ``'signal_strength'``).

        Returns:
            A list of story row dicts.
        """
        # Validate sort column to prevent SQL injection
        allowed_sorts = {"signal_strength", "edition_count", "updated_at", "created_at", "title"}
        if sort_by not in allowed_sorts:
            sort_by = "signal_strength"

        conn = get_connection()

        if tag_filter:
            tags = [t.strip() for t in tag_filter.split(",") if t.strip()]
            clauses: list[str] = []
            params: list[Any] = [workflow_id]
            for tag in tags:
                clauses.append("tags LIKE ?")
                params.append(f"%{tag}%")
            where = " AND ".join(clauses)
            rows = conn.execute(
                f"SELECT * FROM wf_stories WHERE workflow_id = ? AND {where} ORDER BY {sort_by} DESC LIMIT ?",
                params + [limit],
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM wf_stories WHERE workflow_id = ? ORDER BY {sort_by} DESC LIMIT ?",
                (workflow_id, limit),
            ).fetchall()

        return _rows_to_dicts(rows)

    def get_changes(self, workflow_id: str, story_id: str) -> list[dict]:
        """Return the change_log of a story parsed as a list of dicts."""
        story = self.get(workflow_id, story_id)
        if not story:
            return []
        raw = story.get("change_log_json") or "[]"
        try:
            return json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            return []

    def compare_editions(
        self,
        workflow_id: str,
        story_id: str,
        edition_a: str,
        edition_b: str,
    ) -> dict:
        """Diff two editions of the same story by edition_id (run_id).

        Args:
            workflow_id: The workflow the story belongs to.
            story_id: The story ID.
            edition_a: The first edition's run_id to compare.
            edition_b: The second edition's run_id to compare.

        Returns:
            A dict with ``{edition_a, edition_b, headline_diff, body_diff,
            sources_diff, changed_fields}``.
        """
        changes = self.get_changes(workflow_id, story_id)
        entry_a: Optional[dict] = None
        entry_b: Optional[dict] = None

        for entry in changes:
            if entry.get("edition_id") == edition_a:
                entry_a = entry
            if entry.get("edition_id") == edition_b:
                entry_b = entry

        if not entry_a or not entry_b:
            return {
                "error": "edition_not_found",
                "edition_a_found": entry_a is not None,
                "edition_b_found": entry_b is not None,
            }

        changed_fields: list[str] = []
        if entry_a.get("headline") != entry_b.get("headline"):
            changed_fields.append("headline")
        if entry_a.get("headline_diff") != entry_b.get("headline_diff"):
            changed_fields.append("headline_diff")
        if entry_a.get("body_diff") != entry_b.get("body_diff"):
            changed_fields.append("body")
        if entry_a.get("sources_diff") != entry_b.get("sources_diff"):
            changed_fields.append("sources")

        return {
            "edition_a": edition_a,
            "edition_b": edition_b,
            "headline_a": entry_a.get("headline", ""),
            "headline_b": entry_b.get("headline", ""),
            "headline_diff": entry_b.get("headline_diff", ""),
            "body_diff": entry_b.get("body_diff", ""),
            "sources_diff": entry_b.get("sources_diff", ""),
            "changed_fields": changed_fields,
            "edition_a_entry": entry_a,
            "edition_b_entry": entry_b,
        }

    def get_active_stories(
        self,
        workflow_id: str,
        min_signal: float = 0.3,
    ) -> list[dict]:
        """Get stories with signal_strength above the threshold."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM wf_stories WHERE workflow_id = ? AND signal_strength >= ? ORDER BY signal_strength DESC",
            (workflow_id, min_signal),
        ).fetchall()
        return _rows_to_dicts(rows)

    def get_stale_stories(
        self,
        workflow_id: str,
        max_editions: int = 3,
    ) -> list[dict]:
        """Get stories that haven't appeared in max_editions+ editions.

        Returns stories whose run_id-based count suggests they've gone
        stale — i.e. stories whose edition_count exceeds the recency
        threshold OR that have low signal despite many editions.

        More practically: returns stories with edition_count <= max_editions
        that haven't been updated recently, or stories where signal has
        decayed below a nominal threshold.
        """
        conn = get_connection()
        # Stories with few editions (≤ max_editions) that haven't been
        # seen recently — treat as stale/emerging
        rows = conn.execute(
            """SELECT * FROM wf_stories
               WHERE workflow_id = ?
                 AND edition_count <= ?
               ORDER BY updated_at ASC, signal_strength ASC
               LIMIT 50""",
            (workflow_id, max_editions),
        ).fetchall()
        return _rows_to_dicts(rows)

    def update_signal(self, workflow_id: str, story_id: str) -> float:
        """Recalculate and update signal_strength for a story.

        Returns the new signal_strength value.
        """
        story = self.get(workflow_id, story_id)
        if not story:
            return 0.0

        edition_count = story["edition_count"]
        first_seen_str = story.get("created_at", "")
        try:
            first_seen_dt = datetime.fromisoformat(first_seen_str)
            days_since_first = (datetime.now(timezone.utc) - first_seen_dt).total_seconds() / 86400.0
        except (ValueError, TypeError):
            days_since_first = 0.0

        new_signal = self._compute_signal(edition_count, days_since_first)
        now = _now()

        conn = get_connection()
        conn.execute(
            "UPDATE wf_stories SET signal_strength = ?, updated_at = ? WHERE id = ? AND workflow_id = ?",
            (new_signal, now, story_id, workflow_id),
        )
        conn.commit()
        return new_signal

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _diff_text(old: str, new: str) -> str:
        """Simple text diff summariser.

        Returns a short string describing what changed:
        - ``"new"`` if old was empty and new is not
        - ``"removed"`` if new is empty and old was not
        - ``"unchanged"`` if they are equal
        - ``"changed"`` otherwise
        """
        if not old and new:
            return "new"
        if old and not new:
            return "removed"
        if old == new:
            return "unchanged"
        return "changed"

    @staticmethod
    def _diff_sources(old: list[str], new: list[str]) -> str:
        """Diff two source URL lists.

        Returns a short description:
        - ``"new"`` if old was empty and new is not
        - ``"removed"`` if new is empty and old was not
        - ``"unchanged"`` if sets are equal
        - ``"added_N_removed_M"`` otherwise
        """
        old_set = set(old or [])
        new_set = set(new or [])
        if not old_set and new_set:
            return "new"
        if old_set and not new_set:
            return "removed"
        if old_set == new_set:
            return "unchanged"
        added = len(new_set - old_set)
        removed = len(old_set - new_set)
        parts = []
        if added:
            parts.append(f"added_{added}")
        if removed:
            parts.append(f"removed_{removed}")
        return "_".join(parts) if parts else "changed"
