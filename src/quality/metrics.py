"""Quality Metrics — Individual metric computations for edition quality scoring."""

from __future__ import annotations

import re
from typing import Any

__all__ = ["QualityMetrics"]


class QualityMetrics:
    """Individual quality metrics for newsletter edition scoring.

    Each method returns a float in [0.0, 1.0] representing the quality
    of a specific dimension.
    """

    # ------------------------------------------------------------------
    # Citation Validity
    # ------------------------------------------------------------------

    def citation_validity(self, citation_report: dict[str, Any]) -> float:
        """Score citation validity based on a citation validation report.

        Parameters
        ----------
        citation_report : dict
            Expected to contain keys like ``valid``, ``missing_ids``,
            ``hallucination_count``, ``total_claims``.

        Returns
        -------
        float
            ``1.0`` = all claims verified, no hallucination.
            ``0.5`` = minor citation issues (some missing, low hallucination).
            ``0.0`` = major hallucination/citation failures.
        """
        if not citation_report:
            return 0.0

        valid = citation_report.get("valid", True)
        missing = citation_report.get("missing_ids", [])
        hallucination_count = citation_report.get("hallucination_count", 0)
        total_claims = citation_report.get("total_claims", 1)

        # Perfect case
        if valid and not missing and hallucination_count == 0:
            return 1.0

        # Major hallucination
        if hallucination_count > 0:
            ratio = max(0.0, 1.0 - (hallucination_count / max(total_claims, 1)))
            if ratio <= 0.3:
                return 0.0
            if ratio <= 0.6:
                return 0.5
            return ratio

        # Missing citations (minor issues)
        if missing:
            valid_pct = max(0.0, 1.0 - len(missing) / max(total_claims, 1))
            if valid_pct >= 0.8:
                return 0.5
            return max(0.2, valid_pct)

        return 0.5

    # ------------------------------------------------------------------
    # Signal Density
    # ------------------------------------------------------------------

    def signal_density(
        self, items: list[dict[str, Any]], edition_source_count: int
    ) -> float:
        """Score signal density — signals per source.

        Higher density = more signals per source, indicating richer
        curation.  Normalises to ``min(1.0, actual_signals / expected_signals)``.

        Parameters
        ----------
        items : list[dict]
            List of signal/items used in the edition.
        edition_source_count : int
            Number of distinct sources the edition draws from.

        Returns
        -------
        float
            Normalised density in ``[0.0, 1.0]``.
        """
        if edition_source_count <= 0:
            return 0.0

        actual_signals = len(items) if items else 0
        # Expect at least 1 signal per source, ideally 2+
        expected_signals = max(1, edition_source_count)

        return min(1.0, actual_signals / expected_signals)

    # ------------------------------------------------------------------
    # Narrative Continuity
    # ------------------------------------------------------------------

    def narrative_continuity(
        self,
        story_diffs: list[dict[str, Any]] | None,
        trajectories: list[dict[str, Any]] | None,
    ) -> float:
        """Score narrative continuity — ratio of continued stories to new.

        Higher values mean more narrative cohesion across editions.

        Parameters
        ----------
        story_diffs : list[dict] or None
            Diff results for stories carried over from previous editions.
        trajectories : list[dict] or None
            Trajectory data for stories (e.g. ``{"trajectory": "continuing"}``).

        Returns
        -------
        float
            ``1.0`` = all stories are continuations.
            ``0.0`` = all stories are brand new (no continuity).
        """
        diffs = story_diffs or []
        trajs = trajectories or []

        if not diffs and not trajs:
            return 0.0

        # Count continued stories from trajectories
        continued = sum(
            1 for t in trajs if t.get("trajectory", "") in ("continuing", "evolving")
        )
        total = len(trajs) if trajs else len(diffs)

        if total == 0:
            return 0.0

        return min(1.0, continued / total)

    # ------------------------------------------------------------------
    # Brand Voice Adherence
    # ------------------------------------------------------------------

    def brand_voice(
        self, output_text: str, brand_patterns: list[str] | None = None
    ) -> float:
        """Score brand voice adherence using a simple heuristic.

        Checks for the presence of brand phrases and tone markers in the
        output text.  Uses default brand patterns if none provided.

        Parameters
        ----------
        output_text : str
            The generated newsletter text.
        brand_patterns : list[str] or None
            List of regex patterns that indicate brand voice.
            Defaults to common professional newsletter patterns.

        Returns
        -------
        float
            ``1.0`` = strong brand voice match.
            ``0.0`` = no brand voice detected.
        """
        if not output_text:
            return 0.0

        if brand_patterns is None:
            brand_patterns = [
                r"\bwe['\u2019]ve\b",
                r"\bhere['\u2019]s\b",
                r"\bkeep reading\b",
                r"\binsight\b",
                r"\bdive into\b",
                r"\bstay tuned\b",
                r"\bcurated\b",
                r"\bexclusive\b",
                r"\bin this edition\b",
                r"\b[a-z][A-Z][a-z]",  # camelCase product names
                r"\b[45]\s*min\b",  # "5 min read"
                r"\btl;dr\b",
                r"\bkey takeaway",
            ]

        text_lower = output_text.lower()
        matches = 0
        for pattern in brand_patterns:
            if re.search(pattern, text_lower):
                matches += 1

        if not brand_patterns:
            return 0.0

        ratio = matches / len(brand_patterns)
        return min(1.0, ratio * 2.0)  # scale up so partial match still scores
