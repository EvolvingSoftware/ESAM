"""DiffEngine — unified story diffing across editions.

Compares current edition items to prior tracked stories using headline
similarity (not hash), detects new/updated/continued/resolved stories,
and generates human-readable narratives.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from database import get_connection
from stories.body_diff import BodyDiffer
from stories.headline_compare import HeadlineComparer
from stories.trajectory import TrajectoryComputer
from stories_engine import StoriesEngine

logger = logging.getLogger(__name__)


class DiffEngine:
    """Unified diff engine for story comparison across editions.

    Uses headline similarity (not exact hash) to match stories, enabling
    fuzzy matching even when headline wording changes between editions.
    """

    def __init__(self, db_conn: Any = None) -> None:
        self._conn = db_conn  # Optional injected connection for testing
        self._headline = HeadlineComparer()
        self._body = BodyDiffer()
        self._trajectory = TrajectoryComputer()
        self._stories = StoriesEngine()

    def _get_conn(self):
        if self._conn:
            return self._conn
        return get_connection()

    def diff_stories(
        self,
        current_items: list[dict],
        prior_stories: list[dict],
        workflow_id: str,
    ) -> list[dict]:
        """Compare current edition items to prior tracked stories.

        Args:
            current_items: List of item dicts from the current edition, each
                with ``title``, ``headline``, ``body``, ``url``, ``tags``.
            prior_stories: List of story dicts from prior runs (from
                ``StoriesEngine.list_stories()``).
            workflow_id: The workflow ID these stories belong to.

        Returns:
            List of diff result dicts, each containing::

                {
                    "story_id": str | None,
                    "title": str,
                    "signal_strength": float,
                    "diff_type": "new" | "updated" | "continued" | "resolved",
                    "headline_diff": {},
                    "body_diff": {},
                    "sources_diff": str,
                    "significance": "high" | "medium" | "low",
                }
        """
        results: list[dict] = []

        # Track which prior stories have been matched
        matched_prior_ids: set[str] = set()

        # Existing titles for headline matching
        existing_titles = [s.get("title", "") for s in prior_stories]

        for item in current_items:
            title = item.get("title", item.get("headline", "")).strip()
            if not title:
                continue

            headline = item.get("headline", title)
            body = item.get("body", item.get("body_extracted", ""))
            sources_raw = item.get("sources", item.get("urls", []))
            if isinstance(sources_raw, str):
                sources = [sources_raw]
            else:
                sources = list(sources_raw or [])

            # Find match by headline similarity
            best_title, sim_score = self._headline.find_match(title, existing_titles)

            if best_title and sim_score >= 0.3:
                # Find the matched prior story
                prior_story = None
                for ps in prior_stories:
                    if ps.get("title") == best_title:
                        prior_story = ps
                        matched_prior_ids.add(ps.get("id", ""))
                        break

                if not prior_story:
                    continue

                # Determine diff type and compute diffs
                prior_headline = prior_story.get("last_headline", "")
                prior_body = prior_story.get("last_body_snippet", "")

                headline_diff = self._headline.similarity(prior_headline, headline)
                body_diff_result = self._body.diff(prior_body, body[:500])

                # Check if changed
                is_updated = (
                    headline_diff < 0.8
                    or body_diff_result.get("diff_type") in ("significant_changes", "new")
                )

                diff_type = "updated" if is_updated else "continued"

                # Compute significance
                sig_strength = prior_story.get("signal_strength", 0.5)
                if sig_strength >= 0.6:
                    significance = "high"
                elif sig_strength >= 0.3:
                    significance = "medium"
                else:
                    significance = "low"

                results.append({
                    "story_id": prior_story.get("id"),
                    "title": best_title,
                    "signal_strength": sig_strength,
                    "diff_type": diff_type,
                    "headline_diff": {"similarity": round(headline_diff, 4)},
                    "body_diff": body_diff_result,
                    "sources_diff": "unchanged",
                    "significance": significance,
                })
            else:
                # New story — not found in prior
                results.append({
                    "story_id": None,
                    "title": title,
                    "signal_strength": 0.1,
                    "diff_type": "new",
                    "headline_diff": {},
                    "body_diff": {"diff_type": "new", "changes": [], "similarity_score": 0.0},
                    "sources_diff": "new",
                    "significance": "low",
                })

        # Detect resolved stories — prior stories not matched in current
        for ps in prior_stories:
            if ps.get("id", "") not in matched_prior_ids:
                results.append({
                    "story_id": ps.get("id"),
                    "title": ps.get("title", ""),
                    "signal_strength": ps.get("signal_strength", 0.5),
                    "diff_type": "resolved",
                    "headline_diff": {},
                    "body_diff": {},
                    "sources_diff": "",
                    "significance": "medium",
                })

        return results

    def get_significant_diffs(
        self, diffs: list[dict], threshold: str = "medium"
    ) -> list[dict]:
        """Filter diffs by significance threshold.

        Args:
            diffs: List of diff result dicts from ``diff_stories()``.
            threshold: One of ``"low"``, ``"medium"``, ``"high"``.
                ``"medium"`` includes ``"medium"`` and ``"high"``.

        Returns:
            Filtered list.
        """
        levels = {"low": 0, "medium": 1, "high": 2}
        min_level = levels.get(threshold, 1)
        return [
            d for d in diffs
            if levels.get(d.get("significance", "low"), 0) >= min_level
        ]

    def generate_diff_narrative(
        self, diffs: list[dict], prior_narratives: Optional[list[str]] = None
    ) -> str:
        """Generate a human-readable summary of what changed in this edition.

        Args:
            diffs: List of diff result dicts from ``diff_stories()``.
            prior_narratives: Optional list of prior narrative strings for context.

        Returns:
            A formatted narrative string.
        """
        if not diffs:
            return "No story changes detected in this edition."

        lines: list[str] = []
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"# Story Diff Narrative — {now_str}")
        lines.append("")

        # Categorize diffs
        new_stories = [d for d in diffs if d.get("diff_type") == "new"]
        updated_stories = [d for d in diffs if d.get("diff_type") == "updated"]
        continued_stories = [d for d in diffs if d.get("diff_type") == "continued"]
        resolved_stories = [d for d in diffs if d.get("diff_type") == "resolved"]

        if new_stories:
            lines.append(f"## New Stories ({len(new_stories)})")
            for s in new_stories[:5]:
                lines.append(f"  • **{s['title']}** — newly detected in this edition")
            if len(new_stories) > 5:
                lines.append(f"  ... and {len(new_stories) - 5} more")
            lines.append("")

        if updated_stories:
            lines.append(f"## Updated Stories ({len(updated_stories)})")
            for s in updated_stories[:5]:
                body_info = ""
                body_diff = s.get("body_diff", {})
                if body_diff.get("diff_type"):
                    body_info = f" ({body_diff['diff_type']})"
                lines.append(f"  • **{s['title']}** — headline/body changed{body_info}")
            if len(updated_stories) > 5:
                lines.append(f"  ... and {len(updated_stories) - 5} more")
            lines.append("")

        if continued_stories:
            lines.append(f"## Continued Stories ({len(continued_stories)})")
            for s in continued_stories[:3]:
                lines.append(f"  • **{s['title']}** — story persists unchanged")
            lines.append("")

        if resolved_stories:
            lines.append(f"## Resolved Stories ({len(resolved_stories)})")
            for s in resolved_stories[:5]:
                lines.append(f"  • **{s['title']}** — no longer appearing")
            if len(resolved_stories) > 5:
                lines.append(f"  ... and {len(resolved_stories) - 5} more")
            lines.append("")

        lines.append(f"---\n_Total: {len(new_stories)} new, {len(updated_stories)} updated, "
                     f"{len(continued_stories)} continued, {len(resolved_stories)} resolved._")

        return "\n".join(lines)
