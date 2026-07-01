"""ClaimVerifier — full claim verification pipeline: parse, map, grade, and detect hallucinations."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from verifier.grader import CitationGrader
from verifier.detector import HallucinationDetector

logger = logging.getLogger(__name__)

CITATION_RE = re.compile(r"\[(S\d{3,})\]")

PASS_THRESHOLD = float(os.environ.get("VERIFIER_PASS_THRESHOLD", "0.05"))


def _parse_claims(output_text: str) -> list[dict[str, Any]]:
    """Parse output text into individual claims using section/paragraph boundaries.

    Each claim is a dict with ``text`` and ``section`` keys.
    Sections are identified by markdown-style headings (## or ###).
    Paragraphs within each section become individual claims.
    """
    if not output_text or not output_text.strip():
        return []

    claims: list[dict[str, Any]] = []
    current_section = "__root__"

    lines = output_text.split("\n")
    for line in lines:
        stripped = line.strip()
        # Detect markdown section headings
        heading_match = re.match(r"^#{1,4}\s+(.+)$", stripped)
        if heading_match:
            current_section = heading_match.group(1).strip()
            continue
        # Skip blank lines, separators, list markers without content
        if not stripped or stripped.startswith("---") or stripped.startswith("***"):
            continue

        # Consider non-empty paragraphs as claims
        if len(stripped) > 15:
            claims.append({
                "text": stripped,
                "section": current_section,
            })

    return claims


def _map_citations(text: str) -> list[str]:
    """Extract citation IDs from text (e.g. ``[S001]``, ``[S042]``).

    Returns list of citation ID strings (e.g. ``["S001", "S042"]``).
    """
    matches = CITATION_RE.findall(text)
    return matches


class ClaimVerifier:
    """Full claim verification pipeline.

    Parses LLM output into individual claims, maps each claim to its
    citation IDs (from ``[S001]`` style markers), grades each claim
    against cited URLs using LLM-as-Judge, and returns structured results
    with hallucination detection.
    """

    def __init__(
        self,
        llm_endpoint: str = "http://localhost:7999/v1/chat/completions",
        llm_model: str = "gemma-12b",
    ) -> None:
        self.llm_endpoint = llm_endpoint
        self.llm_model = llm_model
        self.grader = CitationGrader(llm_endpoint=llm_endpoint, llm_model=llm_model)
        self.detector = HallucinationDetector()

    def verify_claims(
        self,
        output_text: str,
        citation_map: dict[str, dict[str, str]] | None = None,
        items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Verify all claims in the output text against their cited sources.

        Args:
            output_text: The full LLM-generated output text.
            citation_map: Optional dict mapping citation IDs (e.g. ``S001``)
                to their fetched content ``{url, content, title}``.
                If not provided, ``items`` is used.
            items: Optional list of content source items, each with
                ``citation_id``, ``url``, ``body_extracted``, ``title`` keys.
                Used as fallback when ``citation_map`` is not provided.

        Returns:
            Dict with keys:
            - ``claims``: list of graded claim dicts
            - ``overall_score``: fraction of claims supported
            - ``hallucination_ratio``: fraction of claims unsupported/contradicted
            - ``passed``: bool if hallucination_ratio < 0.05
        """
        # Build citation_map from items if not provided
        cmap: dict[str, dict[str, str]] = {}
        if citation_map:
            cmap = dict(citation_map)
        elif items:
            for item in items:
                cid = str(item.get("citation_id", ""))
                if cid:
                    cmap[cid] = {
                        "url": str(item.get("url", "")),
                        "content": str(item.get("body_extracted", item.get("body_raw", ""))),
                        "title": str(item.get("title", "")),
                    }

        # Parse into claims and map citations
        raw_claims = _parse_claims(output_text)
        graded_claims: list[dict[str, Any]] = []

        for rc in raw_claims:
            citation_ids = _map_citations(rc["text"])
            claim_entry: dict[str, Any] = {
                "text": rc["text"],
                "citation_ids": citation_ids,
                "section": rc["section"],
                "verdict": "unverifiable",
                "confidence": 0.0,
                "evidence": "",
            }

            if not citation_ids:
                # No citations means we can't verify
                claim_entry["verdict"] = "unverifiable"
                claim_entry["confidence"] = 0.0
                claim_entry["evidence"] = "No citation markers found in claim text"
            else:
                # Grade against each citation and take the most common verdict
                verdicts: list[str] = []
                confidences: list[float] = []
                all_evidence: list[str] = []

                for cid in citation_ids:
                    cited = cmap.get(cid, {})
                    cited_url = cited.get("url", "")
                    cited_content = cited.get("content", "")
                    cited_title = cited.get("title")

                    if not cited_url and not cited_content:
                        verdicts.append("unverifiable")
                        confidences.append(0.0)
                        all_evidence.append(f"No source data for citation [{cid}]")
                        continue

                    grade_result = self.grader.grade_claim(
                        claim_text=rc["text"],
                        citation_id=cid,
                        cited_url=cited_url,
                        cited_content=cited_content,
                        cited_title=cited_title,
                    )
                    verdicts.append(grade_result["verdict"])
                    confidences.append(grade_result["confidence"])
                    all_evidence.append(
                        f"[{cid}] {grade_result['reasoning']} | Quote: {grade_result['supporting_quote']}"
                    )

                # Aggregate: pick worst verdict (contradicted > unsupported > unverifiable > supported)
                priority = {"supported": 0, "unverifiable": 1, "unsupported": 2, "contradicted": 3}
                worst_verdict = max(verdicts, key=lambda v: priority.get(v, 0))
                avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

                claim_entry["verdict"] = worst_verdict
                claim_entry["confidence"] = round(avg_confidence, 4)
                claim_entry["evidence"] = "; ".join(all_evidence)

            graded_claims.append(claim_entry)

        # Run hallucination detection
        detection = self.detector.detect(graded_claims)

        total = detection["total_claims"]
        supported = detection["supported_count"]
        overall_score = supported / total if total > 0 else 1.0
        hallucination_ratio = detection["hallucination_ratio"]

        return {
            "claims": graded_claims,
            "overall_score": round(overall_score, 4),
            "hallucination_ratio": round(hallucination_ratio, 4),
            "passed": hallucination_ratio < PASS_THRESHOLD,
            "detection": detection,
        }
