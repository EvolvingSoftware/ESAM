"""Claim-Content Semantic Verifier — LLM-as-Judge claim grading against cited sources.

A fact-checking pipeline that verifies LLM-generated claims against their
cited sources using LLM-as-Judge. Platform primitive (quality gate),
independent of any specific workflow.
"""

from __future__ import annotations

from verifier.engine import ClaimVerifier
from verifier.grader import CitationGrader
from verifier.detector import HallucinationDetector

__all__ = [
    "ClaimVerifier",
    "CitationGrader",
    "HallucinationDetector",
]
