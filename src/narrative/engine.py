"""NarrativeEngine — synthesizes human-readable narrative paragraphs from story diffs and trajectories."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class NarrativeEngine:
    """Generates flowing narrative paragraphs from structured story diffs and trajectories.

    Each paragraph covers one story's trajectory and uses templates like
    "X has been [rising|stable|fading] over Y editions..." to produce
    human-readable newsletter narrative text.
    """

    def __init__(self, db_conn: Any = None) -> None:
        self._conn = db_conn

    # ── Template helpers ────────────────────────────────────────────────

    _TRAJECTORY_TEMPLATES: dict[str, str] = {
        "rising": (
            "{title} has been **rising** over {editions} edition(s), "
            "with growing signal strength suggesting increasing relevance. "
            "{detail}"
        ),
        "stable": (
            "{title} remains **stable** over {editions} edition(s), "
            "maintaining consistent signal strength. "
            "{detail}"
        ),
        "fading": (
            "{title} appears to be **fading** over {editions} edition(s), "
            "with declining signal strength indicating waning interest. "
            "{detail}"
        ),
        "new": (
            "{title} is a **new** story detected in this edition, "
            "with emerging signal potential. "
            "{detail}"
        ),
        "resolved": (
            "{title} has been **resolved** after {editions} edition(s) — "
            "it no longer appears in current signals. "
            "{detail}"
        ),
    }

    _DEFAULT_TEMPLATE = (
        "{title} has trajectory {trajectory} over {editions} edition(s). {detail}"
    )

    def _build_detail(self, story: dict, trajectory: str) -> str:
        """Build a detail sentence from the story's diff data."""
        signal = story.get("signal_strength", story.get("significance", ""))
        diff_type = story.get("diff_type", "continued")

        parts: list[str] = []
        if isinstance(signal, (int, float)):
            parts.append(f"Signal strength: {signal:.2f}")
        elif isinstance(signal, str) and signal:
            parts.append(f"Significance: {signal}")

        if diff_type == "updated":
            parts.append("Content has been updated in this edition.")
        elif diff_type == "continued":
            parts.append("Story persists with consistent coverage.")
        elif diff_type == "new":
            parts.append("First detected in this edition.")

        return " ".join(parts) if parts else ""

    def synthesize(
        self,
        story_diffs: list[dict],
        trajectories: dict[str, str] | list[dict],
        prior_narratives: list[str] | None = None,
    ) -> str:
        """Generate flowing narrative paragraphs from story diffs and trajectories.

        Args:
            story_diffs: List of story diff dicts from the Story Diff Engine.
                Each should have ``story_id``, ``title``, ``diff_type``,
                ``signal_strength``/``significance``.
            trajectories: Either a dict mapping ``story_id -> trajectory_label``
                or a list of trajectory dicts (each with ``story_id`` and
                ``trajectory``).
            prior_narratives: Optional list of prior narrative strings for context.

        Returns:
            Markdown narrative text with section headings.
        """
        if not story_diffs:
            return "No story signals detected in this edition."

        # Build trajectory lookup
        traj_map: dict[str, str] = {}
        if isinstance(trajectories, dict):
            traj_map = trajectories
        elif isinstance(trajectories, list):
            for t in trajectories:
                if isinstance(t, dict):
                    sid = t.get("story_id", "")
                    traj = t.get("trajectory", "stable")
                    if sid:
                        traj_map[sid] = traj

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines: list[str] = [
            f"# Narrative Synthesis — {now_str}",
            "",
        ]

        if prior_narratives:
            lines.append("> *Based on prior narrative context*")
            lines.append("")

        # Group stories by trajectory for section-like ordering
        narrative_paragraphs: list[str] = []

        for story in story_diffs:
            story_id = story.get("story_id", "")
            title = story.get("title", "Untitled")
            trajectory = traj_map.get(story_id, story.get("trajectory", "stable"))
            editions = story.get("edition_count", 1)
            detail = self._build_detail(story, trajectory)

            template = self._TRAJECTORY_TEMPLATES.get(
                trajectory, self._DEFAULT_TEMPLATE
            )

            paragraph = template.format(
                title=title,
                trajectory=trajectory,
                editions=editions,
                detail=detail,
            )
            narrative_paragraphs.append(paragraph)

        # Sort: rising/new first, then stable, then fading/resolved
        def _sort_key(p: str) -> int:
            p_lower = p.lower()
            if "rising" in p_lower or "new" in p_lower:
                return 0
            if "stable" in p_lower:
                return 1
            if "fading" in p_lower:
                return 2
            if "resolved" in p_lower:
                return 3
            return 4

        narrative_paragraphs.sort(key=_sort_key)

        # Write grouped paragraphs
        for para in narrative_paragraphs:
            lines.append(para)
            lines.append("")

        lines.append("---")
        lines.append(
            f"_Synthesized {len(story_diffs)} story signal(s). "
            f"Generated at {now_str}._"
        )

        return "\n".join(lines)

    def synthesize_batch(
        self,
        all_diffs: dict[str, list[dict]],
        all_trajectories: dict[str, dict[str, str] | list[dict]],
    ) -> dict:
        """Synthesize narratives for multiple workflows/groups at once.

        Args:
            all_diffs: Dict mapping a group key to a list of story diffs.
            all_trajectories: Dict mapping the same group keys to trajectory
                data (dict or list).

        Returns:
            ::

                {
                    "narratives": [
                        {
                            "story_id": str,
                            "title": str,
                            "trajectory": str,
                            "narrative_text": str,
                        },
                        ...
                    ],
                    "combined_narrative": str,
                }
        """
        narratives: list[dict] = []
        combined_paragraphs: list[str] = []

        for group_key in all_diffs:
            diffs = all_diffs[group_key]
            trajs = all_trajectories.get(group_key, {})

            # Get trajectory per story
            traj_map: dict[str, str] = {}
            if isinstance(trajs, dict):
                traj_map = trajs
            elif isinstance(trajs, list):
                for t in trajs:
                    if isinstance(t, dict) and t.get("story_id"):
                        traj_map[t["story_id"]] = t.get("trajectory", "stable")

            for story in diffs:
                story_id = story.get("story_id", "")
                title = story.get("title", "Untitled")
                trajectory = traj_map.get(story_id, story.get("trajectory", "stable"))
                detail = self._build_detail(story, trajectory)
                template = self._TRAJECTORY_TEMPLATES.get(
                    trajectory, self._DEFAULT_TEMPLATE
                )
                narrative_text = template.format(
                    title=title,
                    trajectory=trajectory,
                    editions=story.get("edition_count", 1),
                    detail=detail,
                )

                narratives.append({
                    "story_id": story_id,
                    "title": title,
                    "trajectory": trajectory,
                    "narrative_text": narrative_text,
                })
                combined_paragraphs.append(narrative_text)

        combined = (
            "# Combined Narrative Synthesis\n\n"
            + "\n".join(combined_paragraphs)
            + "\n\n---\n_Synthesized in batch._"
            if combined_paragraphs
            else "No narratives to synthesize."
        )

        return {
            "narratives": narratives,
            "combined_narrative": combined,
        }
