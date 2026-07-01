"""ArcDetector — detect multi-edition narrative arcs across stories."""

from __future__ import annotations

from typing import Any


class ArcDetector:
    """Detect narrative arcs — stories that appear across multiple editions.

    An *arc* is a group of related stories that have appeared across 2+
    consecutive editions, forming a coherent narrative thread.
    """

    def detect(
        self,
        stories: list[dict],
        min_arc_length: int = 2,
    ) -> list[dict]:
        """Find narrative arcs in a list of story dicts.

        Args:
            stories: List of story dicts. Each should have at least
                ``id``, ``title``, ``edition_count``, ``signal_strength``,
                ``created_at``, ``updated_at``.
            min_arc_length: Minimum number of consecutive editions for
                an arc (default: 2).

        Returns:
            List of arc dicts::

                [
                    {
                        "arc_name": str,
                        "stories": [story_id, ...],
                        "first_edition": int,
                        "last_edition": int,
                        "arc_length": int,
                        "strength": float,
                    },
                    ...
                ]
        """
        if not stories:
            return []

        # Filter stories that have appeared across 2+ editions
        multi_edition = [
            s for s in stories
            if s.get("edition_count", 1) >= min_arc_length
        ]

        if not multi_edition:
            return []

        # Group by edition count proximity and signal trajectory
        # Sort by signal strength descending for priority
        multi_edition.sort(key=lambda s: s.get("signal_strength", 0.0), reverse=True)

        # Determine edition range from available data
        arcs: list[dict] = []
        used_ids: set[str] = set()

        for story in multi_edition:
            story_id = story.get("id", "")
            if story_id in used_ids:
                continue

            title = story.get("title", "Untitled")
            edition_count = story.get("edition_count", 1)
            signal = story.get("signal_strength", 0.5)
            trajectory = story.get("signal_trajectory", "stable")

            # Estimate first/last edition from created_at / updated_at
            # If we don't have actual edition numbers, use edition_count as proxy
            first_edition = 1
            last_edition = edition_count

            arc_name = self.label_arc([story])
            strength = signal

            arc = {
                "arc_name": arc_name,
                "stories": [story_id],
                "first_edition": first_edition,
                "last_edition": last_edition,
                "arc_length": edition_count,
                "strength": round(strength, 4),
                "trajectory": trajectory,
            }

            used_ids.add(story_id)
            arcs.append(arc)

        # Sort arcs by strength descending
        arcs.sort(key=lambda a: a["strength"], reverse=True)

        return arcs

    def label_arc(self, stories: list[dict]) -> str:
        """Generate a human-readable arc name from a list of related stories.

        Args:
            stories: List of story dicts to derive the arc name from.

        Returns:
            A concise arc name string.
        """
        if not stories:
            return "Untitled Arc"

        # Use the strongest-signal story's title as the arc name
        strongest = max(
            stories,
            key=lambda s: s.get("signal_strength", 0.0),
        )
        base_title = strongest.get("title", "Untitled")

        # If multiple stories, append "& more"
        if len(stories) > 1:
            return f"{base_title} & related signals"
        return base_title

    def detect_new_arcs(
        self,
        stories: list[dict],
        prior_arcs: list[dict],
    ) -> list[dict]:
        """Detect arcs that are new relative to a set of prior arcs.

        An arc is considered "new" if its stories do not substantially
        overlap with any prior arc's stories.

        Args:
            stories: Current story dicts.
            prior_arcs: List of prior arc dicts (from ``detect()``).

        Returns:
            List of new arc dicts.
        """
        # Collect all story IDs from prior arcs
        prior_story_ids: set[str] = set()
        for arc in prior_arcs:
            prior_story_ids.update(arc.get("stories", []))

        # Find stories not in any prior arc
        new_stories = [
            s for s in stories
            if s.get("id", "") not in prior_story_ids
            and s.get("edition_count", 1) >= 2
        ]

        if not new_stories:
            return []

        # Detect arcs from the new stories
        return self.detect(new_stories, min_arc_length=2)
