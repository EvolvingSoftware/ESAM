"""HallucinationDetector — analyzes claim verdicts to detect hallucinated content."""

from __future__ import annotations

from typing import Any


class HallucinationDetector:
    """Analyzes claim verdicts to detect hallucinated (unsupported) content.

    Identifies 'hallucinated' claims: those where the citation does not
    actually support the claim (verdicts: contradicted, unsupported).
    """

    HALLUCINATED_VERDICTS = {"contradicted", "unsupported"}

    def detect(self, claims: list[dict[str, Any]]) -> dict[str, Any]:
        """Run hallucination detection on a list of graded claims.

        Args:
            claims: List of claim dicts, each containing at least:
                - ``text``
                - ``citation_ids``
                - ``verdict``
                - ``confidence``
                - ``evidence`` (optional)

        Returns:
            Dict with keys:
            - ``total_claims``
            - ``supported_count``
            - ``contradicted_count``
            - ``unsupported_count``
            - ``unverifiable_count``
            - ``hallucination_ratio`` (float 0.0-1.0)
            - ``verdicts_by_section`` (dict of section -> verdict counts)
        """
        total = len(claims)
        supported = sum(1 for c in claims if c.get("verdict") == "supported")
        contradicted = sum(1 for c in claims if c.get("verdict") == "contradicted")
        unsupported = sum(1 for c in claims if c.get("verdict") == "unsupported")
        unverifiable = sum(1 for c in claims if c.get("verdict") == "unverifiable")

        # Hallucinated = contradicted + unsupported
        hallucinated = contradicted + unsupported
        hallucination_ratio = hallucinated / total if total > 0 else 0.0

        # Per-section breakdown
        verdicts_by_section: dict[str, dict[str, int]] = {}
        for c in claims:
            section = c.get("section", "__root__")
            verdict = c.get("verdict", "unverifiable")
            if section not in verdicts_by_section:
                verdicts_by_section[section] = {
                    "supported": 0,
                    "contradicted": 0,
                    "unsupported": 0,
                    "unverifiable": 0,
                    "total": 0,
                }
            verdicts_by_section[section][verdict] = (
                verdicts_by_section[section].get(verdict, 0) + 1
            )
            verdicts_by_section[section]["total"] += 1

        return {
            "total_claims": total,
            "supported_count": supported,
            "contradicted_count": contradicted,
            "unsupported_count": unsupported,
            "unverifiable_count": unverifiable,
            "hallucination_ratio": hallucination_ratio,
            "verdicts_by_section": verdicts_by_section,
        }
