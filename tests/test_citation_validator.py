"""Tests for citation/validator.py — CitationValidator and HallucinationDetector."""

from __future__ import annotations

import pytest
from citation.validator import CitationValidator, HallucinationDetector


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def sample_citation_map() -> dict[str, dict[str, str]]:
    return {
        "S001": {"url": "http://example.com/1", "title": "Article One"},
        "S002": {"url": "http://example.com/2", "title": "Article Two"},
        "S003": {"url": "http://example.com/3", "title": "Article Three"},
    }


# ── CitationValidator Tests ───────────────────────────────────────


class TestCitationValidator:
    def test_validate_all_valid(self, sample_citation_map):
        """All SXXX references exist in the citation map → valid=True."""
        validator = CitationValidator()
        result = validator.validate_output(
            "Claim [S001] here and [S002] also [S003].",
            sample_citation_map,
        )
        assert result["valid"] is True
        assert result["missing_ids"] == []
        assert result["warnings"] == []

    def test_validate_missing_id(self, sample_citation_map):
        """Detect S999 referenced but not in the map."""
        validator = CitationValidator()
        result = validator.validate_output(
            "Claim [S001] here and [S999] missing.",
            sample_citation_map,
        )
        assert result["valid"] is False
        assert "S999" in result["missing_ids"]
        assert "S001" not in result["missing_ids"]

    def test_validate_range_pattern(self, sample_citation_map):
        """[S001-S003] range syntax: both endpoints extracted."""
        validator = CitationValidator()
        result = validator.validate_output(
            "Range [S001-S003] covers two endpoints.",
            sample_citation_map,
        )
        assert result["valid"] is True
        assert result["missing_ids"] == []

    def test_validate_batch(self, sample_citation_map):
        """validate_batch returns a list of results with index."""
        validator = CitationValidator()
        outputs = [
            "Valid [S001].",
            "Missing [S999].",
        ]
        maps = [
            sample_citation_map,
            sample_citation_map,
        ]
        results = validator.validate_batch(outputs, maps)
        assert len(results) == 2
        assert results[0]["valid"] is True
        assert results[0]["index"] == 0
        assert results[1]["valid"] is False
        assert results[1]["index"] == 1
        assert "S999" in results[1]["missing_ids"]

    def test_validate_extra_ids(self, sample_citation_map):
        """Map IDs not referenced in text appear in extra_ids."""
        validator = CitationValidator()
        result = validator.validate_output(
            "Only [S001] is used.",
            sample_citation_map,
        )
        assert result["extra_ids"] == ["S002", "S003"]

    def test_validate_warning_no_citation(self, sample_citation_map):
        """Paragraphs without any [SXXX] are flagged as warnings."""
        validator = CitationValidator()
        result = validator.validate_output(
            "[S001] This is cited.\n\nThis paragraph has no citation.",
            sample_citation_map,
        )
        assert len(result["warnings"]) > 0
        assert "without citation" in result["warnings"][0]


# ── HallucinationDetector Tests ───────────────────────────────────


class TestHallucinationDetector:
    def test_hallucination_detect(self, sample_citation_map):
        """Claims without any [SXXX] reference flagged as hallucinated."""
        detector = HallucinationDetector()
        result = detector.detect(
            "This is a claim with no citation. Another claim [S001] here.",
            sample_citation_map,
        )
        assert len(result["hallucinated_claims"]) == 1
        assert "no citation" in result["hallucinated_claims"][0]

    def test_hallucination_ratio(self, sample_citation_map):
        """hallucination_ratio is fraction of claims without citations."""
        detector = HallucinationDetector()
        result = detector.detect(
            "Unsupported claim. Another unsupported claim. [S001] supported claim.",
            sample_citation_map,
        )
        # 2 of 3 claims are unsupported
        assert result["hallucination_ratio"] == pytest.approx(2.0 / 3.0, rel=0.01)

    def test_reject_threshold(self, sample_citation_map):
        """hallucination_ratio > 0.05 should block workflow (simulated rejection)."""
        detector = HallucinationDetector()
        text = (
            "First unsupported claim with no citation. "
            "Second unsupported claim with no citation. "
            "[S001] Third claim that is properly supported."
        )
        result = detector.detect(text, sample_citation_map)
        assert result["hallucination_ratio"] > 0.05
        # Simulate the reject gate
        should_reject = result["hallucination_ratio"] > 0.05
        assert should_reject is True

    def test_hallucination_detect_all_supported(self, sample_citation_map):
        """All claims properly cited → no hallucinated claims."""
        detector = HallucinationDetector()
        result = detector.detect(
            "[S001] First claim. [S002] Second claim. [S003] Third claim.",
            sample_citation_map,
        )
        assert result["hallucinated_claims"] == []
        assert result["hallucination_ratio"] == 0.0
        assert result["confidence"] == 1.0

    def test_unverifiable_claims(self, sample_citation_map):
        """Claims citing IDs not in map are unverifiable."""
        detector = HallucinationDetector()
        result = detector.detect(
            "[S001] Valid claim. [S999] Unknown source claim.",
            sample_citation_map,
        )
        assert len(result["unverifiable_claims"]) == 1
        assert "S999" in result["unverifiable_claims"][0]


# ── Workflow Executor Routing Tests ───────────────────────────────


def test_step_type_routing():
    """Verify that executor step-type dispatch recognises the new step types.

    This is a structural test: we check that the executor's dispatch chain
    contains branches for the citation validator step types.
    """
    import inspect
    from workflow_executor import WorkflowExecutor

    source = inspect.getsource(WorkflowExecutor.execute)
    assert "validate_citations" in source, (
        "Executor dispatch missing validate_citations branch"
    )
    assert "hallucination_check" in source, (
        "Executor dispatch missing hallucination_check branch"
    )
    assert "reject_if_invalid" in source, (
        "Executor dispatch missing reject_if_invalid branch"
    )
