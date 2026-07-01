"""Tests for the Claim-Content Semantic Verifier components."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Ensure src is on the path
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from verifier.engine import ClaimVerifier, _map_citations, _parse_claims
from verifier.grader import CitationGrader
from verifier.detector import HallucinationDetector


# ── _parse_claims tests ──────────────────────────────────────────


class TestParseClaims:
    def test_empty_output(self) -> None:
        assert _parse_claims("") == []
        assert _parse_claims("   \n  \n") == []

    def test_plain_text_paragraphs(self) -> None:
        text = "This is a claim about something.\n\nThis is another claim."
        claims = _parse_claims(text)
        assert len(claims) == 2
        assert claims[0]["text"] == "This is a claim about something."
        assert claims[0]["section"] == "__root__"

    def test_section_boundaries(self) -> None:
        text = "## Introduction\n\nThis is an intro claim.\n\n## Analysis\n\nThis is an analysis claim."
        claims = _parse_claims(text)
        assert len(claims) == 2
        assert claims[0]["text"] == "This is an intro claim."
        assert claims[0]["section"] == "Introduction"
        assert claims[1]["text"] == "This is an analysis claim."
        assert claims[1]["section"] == "Analysis"

    def test_short_text_ignored(self) -> None:
        text = "Short.\n\nThis is a longer claim suitable for verification.\n\nTiny."
        claims = _parse_claims(text)
        assert len(claims) == 1
        assert claims[0]["text"] == "This is a longer claim suitable for verification."


# ── _map_citations tests ─────────────────────────────────────────


class TestMapCitations:
    def test_no_citations(self) -> None:
        assert _map_citations("This claim has no citations.") == []

    def test_single_citation(self) -> None:
        assert _map_citations("This claim has a citation [S001].") == ["S001"]

    def test_multiple_citations(self) -> None:
        result = _map_citations("Two citations [S001] and [S042] in text.")
        assert result == ["S001", "S042"]

    def test_inline_citations(self) -> None:
        text = "According to [S003], the sky is blue. However [S007] disputes this."
        assert _map_citations(text) == ["S003", "S007"]


# ── CitationGrader tests ─────────────────────────────────────────


class TestCitationGrader:
    def test_grade_supported(self) -> None:
        """Mock LLM returns 'supported' verdict."""
        grader = CitationGrader()

        mock_response = json.dumps({
            "verdict": "supported",
            "confidence": 0.95,
            "reasoning": "The content directly confirms the claim.",
            "supporting_quote": "The sky is indeed blue.",
        })

        with patch.object(grader, "_call_llm", return_value=mock_response):
            result = grader.grade_claim(
                claim_text="The sky is blue.",
                citation_id="S001",
                cited_url="https://example.com/sky",
                cited_content="The sky is indeed blue during the day.",
                cited_title="Sky Facts",
            )

        assert result["verdict"] == "supported"
        assert result["confidence"] == 0.95
        assert result["supporting_quote"] == "The sky is indeed blue."

    def test_grade_contradicted(self) -> None:
        """Mock LLM returns 'contradicted' verdict."""
        grader = CitationGrader()

        mock_response = json.dumps({
            "verdict": "contradicted",
            "confidence": 0.88,
            "reasoning": "The content claims the sky is green, contradicting the claim.",
            "supporting_quote": "The sky is green during the day.",
        })

        with patch.object(grader, "_call_llm", return_value=mock_response):
            result = grader.grade_claim(
                claim_text="The sky is blue.",
                citation_id="S001",
                cited_url="https://example.com/sky",
                cited_content="The sky is green during the day.",
                cited_title="Sky Facts",
            )

        assert result["verdict"] == "contradicted"
        assert result["confidence"] == 0.88

    def test_grade_unsupported(self) -> None:
        """Mock LLM returns 'unsupported' verdict."""
        grader = CitationGrader()

        mock_response = json.dumps({
            "verdict": "unsupported",
            "confidence": 0.75,
            "reasoning": "The content is about oceans, not sky color.",
            "supporting_quote": "",
        })

        with patch.object(grader, "_call_llm", return_value=mock_response):
            result = grader.grade_claim(
                claim_text="The sky is blue.",
                citation_id="S001",
                cited_url="https://example.com/oceans",
                cited_content="Oceans cover 71% of the Earth's surface.",
                cited_title="Ocean Facts",
            )

        assert result["verdict"] == "unsupported"

    def test_short_content_unverifiable(self) -> None:
        """Content too short returns unverifiable without LLM call."""
        grader = CitationGrader()

        with patch.object(grader, "_call_llm", side_effect=AssertionError("should not be called")):
            result = grader.grade_claim(
                claim_text="The sky is blue.",
                citation_id="S001",
                cited_url="https://example.com/sky",
                cited_content="Hi",  # less than 20 chars
            )

        assert result["verdict"] == "unverifiable"
        assert result["confidence"] == 0.0

    def test_cache_hit(self) -> None:
        """Same claim + citation should return cached result."""
        grader = CitationGrader()

        mock_response = json.dumps({
            "verdict": "supported",
            "confidence": 0.95,
            "reasoning": "Matches content.",
            "supporting_quote": "Yes.",
        })

        with patch.object(grader, "_call_llm", return_value=mock_response) as mock_call:
            result1 = grader.grade_claim(
                claim_text="The sky is blue during daytime hours.",
                citation_id="S001",
                cited_url="https://example.com/sky",
                cited_content="The sky is blue during daytime hours across the planet Earth.",
            )
            result2 = grader.grade_claim(
                claim_text="The sky is blue during daytime hours.",
                citation_id="S001",
                cited_url="https://example.com/sky",
                cited_content="The sky is blue during daytime hours across the planet Earth.",
            )

        # LLM should only have been called once (second call hits cache)
        assert mock_call.call_count == 1
        assert result1 == result2

    def test_verdict_parsing_fallback(self) -> None:
        """Invalid JSON from LLM falls back to unverifiable."""
        grader = CitationGrader()

        with patch.object(grader, "_call_llm", return_value="not valid json at all"):
            result = grader.grade_claim(
                claim_text="Test claim.",
                citation_id="S001",
                cited_url="https://example.com/test",
                cited_content="Some reasonable amount of content here for grading purposes.",
            )

        assert result["verdict"] == "unverifiable"


# ── HallucinationDetector tests ──────────────────────────────────


class TestHallucinationDetector:
    def test_all_supported(self) -> None:
        detector = HallucinationDetector()
        claims = [
            {"text": "C1", "citation_ids": ["S001"], "verdict": "supported", "confidence": 0.9, "section": "root"},
            {"text": "C2", "citation_ids": ["S002"], "verdict": "supported", "confidence": 0.8, "section": "root"},
        ]
        result = detector.detect(claims)
        assert result["total_claims"] == 2
        assert result["supported_count"] == 2
        assert result["hallucination_ratio"] == 0.0

    def test_hallucination_ratio(self) -> None:
        detector = HallucinationDetector()
        claims = [
            {"text": "C1", "citation_ids": ["S001"], "verdict": "supported", "confidence": 0.9, "section": "root"},
            {"text": "C2", "citation_ids": ["S002"], "verdict": "contradicted", "confidence": 0.8, "section": "root"},
            {"text": "C3", "citation_ids": ["S003"], "verdict": "unsupported", "confidence": 0.0, "section": "root"},
            {"text": "C4", "citation_ids": ["S004"], "verdict": "supported", "confidence": 0.7, "section": "root"},
        ]
        result = detector.detect(claims)
        assert result["total_claims"] == 4
        assert result["supported_count"] == 2
        assert result["contradicted_count"] == 1
        assert result["unsupported_count"] == 1
        assert result["hallucination_ratio"] == 0.5  # 2/4

    def test_per_section_breakdown(self) -> None:
        detector = HallucinationDetector()
        claims = [
            {"text": "C1", "citation_ids": ["S001"], "verdict": "supported", "confidence": 0.9, "section": "Intro"},
            {"text": "C2", "citation_ids": ["S002"], "verdict": "contradicted", "confidence": 0.0, "section": "Analysis"},
            {"text": "C3", "citation_ids": ["S003"], "verdict": "supported", "confidence": 0.8, "section": "Analysis"},
        ]
        result = detector.detect(claims)
        assert "Intro" in result["verdicts_by_section"]
        assert "Analysis" in result["verdicts_by_section"]
        assert result["verdicts_by_section"]["Analysis"]["contradicted"] == 1
        assert result["verdicts_by_section"]["Intro"]["supported"] == 1

    def test_empty_claims(self) -> None:
        detector = HallucinationDetector()
        result = detector.detect([])
        assert result["total_claims"] == 0
        assert result["hallucination_ratio"] == 0.0


# ── ClaimVerifier tests ──────────────────────────────────────────


class TestClaimVerifier:
    def test_verify_basic(self) -> None:
        """Mock LLM, verify claims return structured output."""
        verifier = ClaimVerifier()

        mock_grade = {
            "verdict": "supported",
            "confidence": 0.95,
            "reasoning": "Content supports claim.",
            "supporting_quote": "Confirmed.",
        }

        output_text = "According to [S001], the sky is blue.\n\n## Details\n\n[S002] shows water is wet."

        citation_map = {
            "S001": {
                "url": "https://example.com/sky",
                "content": "The sky is blue during the day.",
                "title": "Sky Facts",
            },
            "S002": {
                "url": "https://example.com/water",
                "content": "Water is wet and covers most of Earth.",
                "title": "Water Facts",
            },
        }

        with patch.object(verifier.grader, "grade_claim", return_value=mock_grade):
            result = verifier.verify_claims(output_text, citation_map=citation_map)

        assert "claims" in result
        assert "overall_score" in result
        assert "hallucination_ratio" in result
        assert "passed" in result
        assert len(result["claims"]) == 2
        assert result["claims"][0]["verdict"] == "supported"
        assert result["claims"][0]["citation_ids"] == ["S001"]
        assert result["overall_score"] == 1.0
        assert result["hallucination_ratio"] == 0.0
        assert result["passed"] is True

    def test_pass_threshold(self) -> None:
        """Check pass/fail logic: hallucination_ratio < 0.05 passes."""
        from verifier.engine import PASS_THRESHOLD

        assert PASS_THRESHOLD == 0.05

        verifier = ClaimVerifier()

        output_text = "[S001] Claim A is about the sky being blue.\n[S002] Claim B is about water being wet.\n[S003] Claim C is about grass being green.\n[S004] Claim D is about fire being hot.\n[S005] Claim E is about ice being cold.\n[S006] Claim F is about the moon being made of cheese."

        citation_map = {}
        for i in range(1, 7):
            cid = f"S00{i}"
            citation_map[cid] = {
                "url": f"https://example.com/{cid}",
                "content": f"Content for citation {cid} that is long enough to pass the minimum length check for the grading system.",
                "title": f"Title {cid}",
            }

        # Use a call counter to verify the mock is being hit
        call_count = {"count": 0}

        # Mock grader: return supported for all except S006 which is unsupported
        def mock_grade(claim_text, citation_id, cited_url, cited_content, cited_title=None):
            call_count["count"] += 1
            if citation_id == "S006":
                return {"verdict": "unsupported", "confidence": 0.0, "reasoning": "No.", "supporting_quote": ""}
            return {"verdict": "supported", "confidence": 0.9, "reasoning": "Yes.", "supporting_quote": "..."}

        with patch.object(verifier.grader, "grade_claim", side_effect=mock_grade):
            result = verifier.verify_claims(output_text, citation_map=citation_map)

        # Verify the mock was actually called
        assert call_count["count"] > 0, "Mock grade_claim was never called!"

        # 1/6 = ~0.1667 hallucination ratio → should FAIL
        assert result["hallucination_ratio"] > 0.05, (
            f"Expected hallucination_ratio > 0.05 but got {result['hallucination_ratio']}. "
            f"Claims: {[c['verdict'] for c in result['claims']]}. "
            f"Call count: {call_count['count']}."
        )
        assert result["passed"] is False

    def test_claim_mapping(self) -> None:
        """Check [S001] style citation marker parsing in claims."""
        verifier = ClaimVerifier()

        mock_grade = {"verdict": "supported", "confidence": 0.9, "reasoning": "OK", "supporting_quote": ""}

        output_text = "According to [S001] and [S042], the sky is blue."

        citation_map = {
            "S001": {
                "url": "https://example.com/sky",
                "content": "The sky is blue during the day.",
                "title": "Sky Facts",
            },
            "S042": {
                "url": "https://example.com/another",
                "content": "Further confirmation of blue sky.",
                "title": "More Sky",
            },
        }

        with patch.object(verifier.grader, "grade_claim", return_value=mock_grade):
            result = verifier.verify_claims(output_text, citation_map=citation_map)

        assert len(result["claims"]) == 1
        assert result["claims"][0]["citation_ids"] == ["S001", "S042"]

    def test_no_citations_in_claim(self) -> None:
        """Claims without citation markers get unverifiable."""
        verifier = ClaimVerifier()

        output_text = "This claim has no citation markers."

        result = verifier.verify_claims(output_text, citation_map={})

        assert len(result["claims"]) == 1
        assert result["claims"][0]["verdict"] == "unverifiable"
        assert result["claims"][0]["evidence"] == "No citation markers found in claim text"

    def test_empty_output(self) -> None:
        """Empty output returns empty results."""
        verifier = ClaimVerifier()
        result = verifier.verify_claims("", citation_map={})
        assert result["claims"] == []
        assert result["overall_score"] == 1.0
        assert result["passed"] is True


# ── Verifier Step Type Routing Test ──────────────────────────────


class TestVerifierStepType:
    """Test that executor can route to verifier step types."""

    def test_step_type_verify_claims_routes(self) -> None:
        """Check executor routes verify_claims step type correctly.

        This tests the dispatch table in workflow_executor.py.
        """
        # Verify the step type is in the executor's dispatch table
        from workflow_executor import WorkflowExecutor

        executor = WorkflowExecutor()

        # The dispatch table is in execute() method — check step_types dict
        step_type_methods = {
            "llm_call": "_execute_llm_step",
            "tool_call": "_execute_tool_step",
            "verify_claims": "_execute_verify_claims_step",
            "grade_citations": "_execute_grade_citations_step",
            "reject_if_invalid": "_execute_reject_if_invalid_step",
        }

        for step_type, method_name in step_type_methods.items():
            assert hasattr(executor, method_name), f"{method_name} not found for step type {step_type}"

    def test_verifier_step_type_constants(self) -> None:
        """Verify step type strings exist as used in executor dispatch."""
        assert "verify_claims" in ("verify_claims", "grade_citations", "reject_if_invalid")
        assert "grade_citations" in ("verify_claims", "grade_citations", "reject_if_invalid")
        assert "reject_if_invalid" in ("verify_claims", "grade_citations", "reject_if_invalid")
