"""Citation Integrity Validator — Post-synthesis citation verification.

Provides CitationValidator (cross-reference SXXX references against citation
map) and HallucinationDetector (detect claims without supporting citations).
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["CitationValidator", "HallucinationDetector"]


class CitationValidator:
    """Validates that all ``[SXXX]`` references in output text exist in the citation map.

    Finds ``[SXXX]`` and ``[SXXX-SYYY]`` patterns via regex, cross-references
    each found ID against the supplied citation map, and reports missing IDs,
    extra (unused) IDs, and paragraphs that contain no citation reference at all.
    """

    CITATION_PATTERN = re.compile(r"\[(S\d+)(?:-S(\d+))?\]")

    def validate_output(
        self,
        output_text: str,
        citation_map: dict[str, dict[str, str]],
    ) -> dict[str, Any]:
        """Validate all ``[SXXX]`` references in *output_text* exist in *citation_map*.

        Args:
            output_text: The LLM output text to check.
            citation_map: ``{citation_id: {url: str, title: str}}`` dict, typically
                produced by :class:`~citation.map.CitationMap.build_map`.

        Returns:
            A dict with keys:
            - ``valid`` (bool): ``True`` if *all* referenced IDs exist in the map.
            - ``missing_ids`` (list[str]): Referenced IDs not found in the map.
            - ``extra_ids`` (list[str]): Map IDs that were **not** referenced in the text.
            - ``warnings`` (list[str]): Human-readable notes (e.g. paragraphs without any
              citation reference).
        """
        missing_ids: list[str] = []
        extra_ids: list[str] = []
        warnings: list[str] = []

        # 1. Find every citation reference in the output text
        found_ids: set[str] = set()
        for match in self.CITATION_PATTERN.finditer(output_text):
            cid = match.group(1)
            found_ids.add(cid)
            cid2_raw = match.group(2)
            if cid2_raw:
                found_ids.add(f"S{cid2_raw}")

        # 2. Cross-reference against the known citation map
        known_ids = set(citation_map.keys())
        for cid in sorted(found_ids):
            if cid not in known_ids:
                missing_ids.append(cid)

        for cid in sorted(known_ids):
            if cid not in found_ids:
                extra_ids.append(cid)

        # 3. Paragraph-level scan: flag paragraphs without any citation reference
        paragraphs = [p.strip() for p in output_text.split("\n\n") if p.strip()]
        for para in paragraphs:
            if not self.CITATION_PATTERN.search(para):
                # Truncate long paragraphs for readability
                snippet = para[:120] + "..." if len(para) > 120 else para
                warnings.append(f"Paragraph without citation references: {snippet}")

        valid = len(missing_ids) == 0

        return {
            "valid": valid,
            "missing_ids": sorted(missing_ids),
            "extra_ids": sorted(extra_ids),
            "warnings": warnings,
        }

    def validate_batch(
        self,
        outputs: list[str],
        citation_maps: list[dict[str, dict[str, str]]],
    ) -> list[dict[str, Any]]:
        """Validate multiple outputs against corresponding citation maps.

        Args:
            outputs: List of output text strings.
            citation_maps: List of citation map dicts (one per output).

        Returns:
            List of result dicts, each as returned by :meth:`validate_output`
            with an additional ``index`` key.
        """
        results: list[dict[str, Any]] = []
        for i, (output_text, citation_map) in enumerate(zip(outputs, citation_maps)):
            result = self.validate_output(output_text, citation_map)
            result["index"] = i
            results.append(result)
        return results


class HallucinationDetector:
    """Detects potential hallucinations by finding claims without supporting citations.

    Splits output text into sentences, checks each for citation references,
    and categorises claims as *supported*, *hallucinated* (no citations), or
    *unverifiable* (citations point to IDs missing from the map).
    """

    CITATION_PATTERN = re.compile(r"\[(S\d+)(?:-S(\d+))?\]")

    def detect(
        self,
        output_text: str,
        citation_map: dict[str, dict[str, str]],
        item_bodies: list[str] | None = None,
    ) -> dict[str, Any]:
        """Detect potential hallucinations in *output_text*.

        Args:
            output_text: The LLM output to analyse.
            citation_map: ``{citation_id: {url, title}}`` dict.
            item_bodies: Optional list of source item body texts for future
                cross-referencing (reserved for enhancement).

        Returns:
            A dict with keys:
            - ``hallucinated_claims`` (list[str]): Sentences without any ``[SXXX]``.
            - ``hallucination_ratio`` (float): Fraction of claims that are hallucinated
              (0.0 — 1.0).
            - ``supported_claims`` (list[str]): Sentences with valid citation refs.
            - ``unverifiable_claims`` (list[str]): Sentences whose citation IDs are
              missing from the map.
            - ``confidence`` (float): ``1.0 - hallucination_ratio``.
        """
        hallucinated_claims: list[str] = []
        supported_claims: list[str] = []
        unverifiable_claims: list[str] = []

        # Split into sentences on sentence-ending punctuation
        sentences = [
            s.strip()
            for s in re.split(r"(?<=[.!?])\s+", output_text)
            if s.strip()
        ]

        known_ids = set(citation_map.keys())

        for sentence in sentences:
            citations = self.CITATION_PATTERN.findall(sentence)
            # Collect all unique citation IDs from this sentence
            cited_ids: set[str] = set()
            for cid1, cid2 in citations:
                cited_ids.add(cid1)
                if cid2:
                    cited_ids.add(f"S{cid2}")

            if not cited_ids:
                # No citation references at all — potential hallucination
                hallucinated_claims.append(sentence)
            else:
                # Check whether every referenced ID exists in the map
                all_exist = all(cid in known_ids for cid in cited_ids)
                if all_exist:
                    supported_claims.append(sentence)
                else:
                    missing = sorted(cid for cid in cited_ids if cid not in known_ids)
                    unverifiable_claims.append(
                        f"{sentence} (missing citations: {', '.join(missing)})"
                    )

        total_claims = len(sentences)
        hallucination_ratio = (
            len(hallucinated_claims) / total_claims if total_claims > 0 else 0.0
        )
        confidence = 1.0 - hallucination_ratio

        return {
            "hallucinated_claims": hallucinated_claims,
            "hallucination_ratio": round(hallucination_ratio, 4),
            "supported_claims": supported_claims,
            "unverifiable_claims": unverifiable_claims,
            "confidence": round(confidence, 4),
        }
