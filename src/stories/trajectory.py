"""Trajectory computation — track story trajectory across editions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


class TrajectoryComputer:
    """Compute the trajectory of a story across multiple editions.

    Determines whether a story is rising, stable, fading, resolved, or new
    based on edition count, signal changes, and recency.
    """

    @staticmethod
    def _parse_iso(s: str) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(s)
        except (ValueError, TypeError):
            return None

    def compute(
        self, story: dict, prior_editions: list[dict]
    ) -> dict[str, Any]:
        """Compute trajectory for a single story relative to prior editions.

        Args:
            story: Current story dict (from wf_stories or diff engine).
            prior_editions: List of prior story edition data (change_log entries).

        Returns:
            ::

                {
                    "trajectory": "rising" | "stable" | "fading" | "resolved" | "new",
                    "signal_delta": float,
                    "edition_count": int,
                    "days_active": float,
                    "trajectory_confidence": "high" | "medium" | "low",
                }
        """
        edition_count = story.get("edition_count", 1)
        signal = story.get("signal_strength", 0.5)
        created_at = story.get("created_at", "")

        # Days active
        created_dt = self._parse_iso(created_at)
        if created_dt:
            days_active = (datetime.now(timezone.utc) - created_dt).total_seconds() / 86400.0
        else:
            days_active = 0.0

        # Compute signal delta: compare current signal to average of prior editions
        prior_signals = [
            e.get("signal_strength", 0.5) for e in prior_editions
            if isinstance(e, dict) and "signal_strength" in e
        ]

        if prior_signals:
            avg_prior = sum(prior_signals) / len(prior_signals)
            signal_delta = signal - avg_prior
        else:
            signal_delta = 0.0

        # Determine trajectory
        if edition_count == 1:
            trajectory = "new"
            confidence = "medium"
        elif edition_count >= 3 and signal_delta > 0.05:
            trajectory = "rising"
            confidence = "high" if edition_count >= 5 else "medium"
        elif edition_count >= 3 and abs(signal_delta) <= 0.05:
            trajectory = "stable"
            confidence = "high" if edition_count >= 5 else "medium"
        elif signal_delta < -0.05:
            trajectory = "fading"
            confidence = "low"
        else:
            # Not enough data — default
            trajectory = "stable"
            confidence = "low"

        # Check for resolved: not seen in 3+ consecutive editions
        # (caller passes prior_editions that indicate missing)
        if len(prior_editions) >= 3 and not prior_editions[-1].get("seen_in_current", True):
            # Check last few editions for absence
            absent_count = sum(
                1 for e in prior_editions[-3:] if not e.get("seen_in_current", True)
            )
            if absent_count >= 3:
                trajectory = "resolved"
                confidence = "high"

        return {
            "trajectory": trajectory,
            "signal_delta": round(signal_delta, 4),
            "edition_count": edition_count,
            "days_active": round(days_active, 2),
            "trajectory_confidence": confidence,
        }

    def compute_batch(
        self, stories: list[dict], all_editions: list[dict]
    ) -> list[dict]:
        """Compute trajectory for a batch of stories.

        Args:
            stories: List of current story dicts.
            all_editions: List of all prior edition dicts (with ``story_id`` or
                          matching keys to relate them).

        Returns:
            List of trajectory dicts in the same order as *stories*, with
            ``story_id`` added.
        """
        results: list[dict] = []
        for story in stories:
            # Filter editions for this story
            story_id = story.get("id", "")
            prior = [
                e for e in all_editions
                if e.get("story_id") == story_id or e.get("id") == story_id
            ]
            traj = self.compute(story, prior)
            traj["story_id"] = story_id
            results.append(traj)
        return results
