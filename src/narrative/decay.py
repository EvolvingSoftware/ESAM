"""SignalDecayer — exponential decay for story signal strengths over time."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class SignalDecayer:
    """Apply exponential decay to story signal strengths.

    Uses the standard exponential decay formula::

        decayed = strength * 0.5 ^ (days_since_last_seen / half_life_days)

    This models how story relevance diminishes over time if not reinforced.
    """

    @staticmethod
    def decay(
        signal_strength: float,
        days_since_last_seen: float,
        half_life_days: float = 14,
    ) -> float:
        """Compute exponentially decayed signal strength.

        Args:
            signal_strength: Original signal strength (0.0 to 1.0).
            days_since_last_seen: Number of days since the signal was last seen.
            half_life_days: Half-life in days (default: 14).

        Returns:
            Decayed signal strength.
        """
        if days_since_last_seen <= 0:
            return signal_strength

        factor = 0.5 ** (days_since_last_seen / half_life_days)
        return round(signal_strength * factor, 6)

    def apply_decay_batch(
        self,
        stories: list[dict],
        half_life_days: float = 14,
    ) -> list[dict]:
        """Apply exponential decay to a batch of stories.

        For each story, recalculates signal strength based on days since
        ``updated_at`` or ``last_seen_at``.

        Args:
            stories: List of story dicts. Each should have ``signal_strength``
                and one of ``updated_at``, ``last_seen_at``, or ``created_at``.
            half_life_days: Half-life in days (default: 14).

        Returns:
            Stories with updated ``decayed_signal_strength`` field.
        """
        now = datetime.now(timezone.utc)
        result: list[dict] = []

        for story in stories:
            signal = story.get("signal_strength", 0.5)

            # Determine days since last seen
            last_seen = (
                story.get("updated_at")
                or story.get("last_seen_at")
                or story.get("created_at")
            )
            days = 0.0
            if last_seen:
                try:
                    last_dt = datetime.fromisoformat(
                        last_seen.replace("Z", "+00:00")
                    )
                    days = max(0.0, (now - last_dt).total_seconds() / 86400.0)
                except (ValueError, TypeError):
                    days = 0.0

            decayed = self.decay(signal, days, half_life_days)

            updated = dict(story)
            updated["decayed_signal_strength"] = decayed
            updated["days_since_last_seen"] = round(days, 2)
            result.append(updated)

        return result

    def get_stale_stories(
        self,
        stories: list[dict],
        threshold: float = 0.1,
        min_days: int = 7,
    ) -> list[dict]:
        """Find stories that have decayed below a threshold.

        A story is considered "stale" if:
        - It has been at least ``min_days`` since last seen, AND
        - Its decayed signal strength is below ``threshold``.

        Args:
            stories: List of story dicts (should have been run through
                ``apply_decay_batch()`` or have ``decayed_signal_strength``).
            threshold: Decayed signal strength below which a story is stale
                (default: 0.1).
            min_days: Minimum days since last seen (default: 7).

        Returns:
            List of stories that are stale.
        """
        stale: list[dict] = []

        for story in stories:
            decayed = story.get("decayed_signal_strength")
            if decayed is None:
                # Apply decay on the fly
                signal = story.get("signal_strength", 0.5)
                last_seen = (
                    story.get("updated_at")
                    or story.get("last_seen_at")
                    or story.get("created_at")
                )
                days = 0.0
                if last_seen:
                    try:
                        last_dt = datetime.fromisoformat(
                            last_seen.replace("Z", "+00:00")
                        )
                        days = max(0.0, (datetime.now(timezone.utc) - last_dt).total_seconds() / 86400.0)
                    except (ValueError, TypeError):
                        days = 0.0
                decayed = self.decay(signal, days)

            days = story.get("days_since_last_seen", 0.0)

            if days >= min_days and decayed < threshold:
                stale.append(story)

        return stale
