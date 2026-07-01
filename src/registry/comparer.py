"""Edition Comparer — compares two editions and reports differences."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from database import get_connection

from .engine import EditionRegistry, WFE_TABLE

__all__ = ["EditionComparer"]


class EditionComparer:
    """Compares two editions and produces a structured diff.

    Typical usage::

        comparer = EditionComparer()
        diff = comparer.compare("edition-a-id", "edition-b-id")
        latest_diff = comparer.compare_latest()
    """

    def __init__(self, db_conn: Any = None) -> None:
        self._conn = db_conn
        self._registry = EditionRegistry(db_conn=db_conn)

    def compare(self, edition_a_id: str, edition_b_id: str) -> dict:
        """Compare two editions by their IDs.

        Returns a dict with::

            {
                "edition_a": {...},
                "edition_b": {...},
                "subject_diff": "...",
                "source_count_diff": N,
                "item_count_diff": N,
                "token_diff": N,
                "duration_diff": N.N,
                "signal_changes": [
                    {"story_id": "...", "title": "...", "trajectory_change": "..."}
                ],
                "days_between": N,
            }
        """
        a = self._registry.get(edition_a_id)
        b = self._registry.get(edition_b_id)

        if not a or not b:
            missing = "edition_a" if not a else "edition_b" if not b else ""
            return {"error": f"Edition not found: {missing}"}

        # Basic numeric diffs
        source_count_diff = (b.get("source_count", 0) or 0) - (a.get("source_count", 0) or 0)
        item_count_diff = (b.get("item_count", 0) or 0) - (a.get("item_count", 0) or 0)
        token_diff = (b.get("total_tokens", 0) or 0) - (a.get("total_tokens", 0) or 0)
        duration_diff = round(
            (b.get("duration_seconds", 0.0) or 0.0)
            - (a.get("duration_seconds", 0.0) or 0.0),
            2,
        )

        # Subject diff
        subject_a = a.get("subject", "") or ""
        subject_b = b.get("subject", "") or ""
        if subject_a == subject_b:
            subject_diff = "unchanged"
        else:
            subject_diff = f"'{subject_a}' → '{subject_b}'"

        # Days between
        days_between = 0
        try:
            date_a = datetime.fromisoformat(a.get("date", ""))
            date_b = datetime.fromisoformat(b.get("date", ""))
            days_between = abs((date_b - date_a).days)
        except (ValueError, TypeError):
            pass

        # Signal changes — parse signal_ids from both editions
        signal_changes = self._compute_signal_changes(a, b)

        return {
            "edition_a": a,
            "edition_b": b,
            "subject_diff": subject_diff,
            "source_count_diff": source_count_diff,
            "item_count_diff": item_count_diff,
            "token_diff": token_diff,
            "duration_diff": duration_diff,
            "signal_changes": signal_changes,
            "days_between": days_between,
        }

    def compare_latest(self) -> dict:
        """Compare the last 2 editions. Returns comparison dict or an error."""
        editions = self._registry.list(limit=2, offset=0)
        if len(editions) < 2:
            return {"error": "Need at least 2 editions to compare"}
        return self.compare(editions[1]["id"], editions[0]["id"])

    def _compute_signal_changes(self, a: dict, b: dict) -> list[dict]:
        """Parse signal_ids and look up story trajectory info."""
        changes: list[dict] = []
        try:
            a_signals = set(json.loads(a.get("signal_ids", "[]") or "[]"))
            b_signals = set(json.loads(b.get("signal_ids", "[]") or "[]"))
        except (json.JSONDecodeError, TypeError):
            return changes

        added_ids = b_signals - a_signals
        removed_ids = a_signals - b_signals

        # Look up story info from wf_stories for added signals
        if added_ids:
            conn = self._conn or get_connection()
            placeholders = ",".join("?" for _ in added_ids)
            try:
                rows = conn.execute(
                    f"SELECT id, title, signal_trajectory FROM wf_stories WHERE id IN ({placeholders})",
                    tuple(added_ids),
                ).fetchall()
                for row in rows:
                    changes.append({
                        "story_id": row["id"],
                        "title": row["title"],
                        "trajectory_change": "added",
                    })
            except Exception:
                # wf_stories table may not exist in all contexts
                for sid in added_ids:
                    changes.append({
                        "story_id": sid,
                        "title": "",
                        "trajectory_change": "added",
                    })

        for sid in removed_ids:
            changes.append({
                "story_id": sid,
                "title": "",
                "trajectory_change": "removed",
            })

        return changes
