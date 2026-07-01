"""CitationGrader — grades a single claim against cited URL content using LLM-as-Judge."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class CitationGrader:
    """Grades whether a cited URL's content actually supports an LLM-generated claim.

    Uses a structured LLM prompt to ask: "Does this URL content support this claim?"
    Caches results in-memory keyed by ``claim_hash + citation_id``.
    """

    VALID_VERDICTS = {"supported", "contradicted", "unsupported", "unverifiable"}

    def __init__(
        self,
        llm_endpoint: str = "http://localhost:7999/v1/chat/completions",
        llm_model: str = "gemma-12b",
    ) -> None:
        self.llm_endpoint = llm_endpoint
        self.llm_model = llm_model
        self._cache: dict[str, dict[str, Any]] = {}

    def _claim_key(self, claim_text: str, citation_id: str) -> str:
        """Generate a cache key from claim text and citation ID."""
        raw = f"{claim_text}||{citation_id}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def grade_claim(
        self,
        claim_text: str,
        citation_id: str,
        cited_url: str,
        cited_content: str,
        cited_title: str | None = None,
    ) -> dict[str, Any]:
        """Grade a single claim against cited URL content.

        Args:
            claim_text: The claim text extracted from LLM output.
            citation_id: The citation marker (e.g. ``S001``).
            cited_url: The URL of the cited source.
            cited_content: The full text content fetched from the URL.
            cited_title: Optional title of the cited source.

        Returns:
            Dict with keys: ``verdict``, ``confidence``, ``reasoning``, ``supporting_quote``.
        """
        cache_key = self._claim_key(claim_text, citation_id)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        result = self._grade_impl(claim_text, citation_id, cited_url, cited_content, cited_title)
        self._cache[cache_key] = result
        return result

    def _grade_impl(
        self,
        claim_text: str,
        citation_id: str,
        cited_url: str,
        cited_content: str,
        cited_title: str | None,
    ) -> dict[str, Any]:
        """Internal grading implementation with LLM call."""

        # Handle edge cases that shortcut the LLM call
        if not cited_content or len(cited_content.strip()) < 20:
            return {
                "verdict": "unverifiable",
                "confidence": 0.0,
                "reasoning": f"Cited URL {cited_url} returned insufficient content ({len(cited_content or '')} chars) to evaluate.",
                "supporting_quote": "",
            }

        if cited_title and "error" in cited_title.lower():
            return {
                "verdict": "unverifiable",
                "confidence": 0.0,
                "reasoning": f"Cited URL {cited_url} appears inaccessible (title indicates error).",
                "supporting_quote": "",
            }

        prompt = self._build_grade_prompt(claim_text, cited_url, cited_content, cited_title)

        try:
            response = self._call_llm(prompt)
            parsed = self._parse_verdict(response)
            return parsed
        except Exception as exc:
            logger.warning("LLM grading failed for claim against %s: %s", cited_url, exc)
            return {
                "verdict": "unverifiable",
                "confidence": 0.0,
                "reasoning": f"Grading failed due to LLM error: {exc}",
                "supporting_quote": "",
            }

    def _build_grade_prompt(
        self,
        claim_text: str,
        cited_url: str,
        cited_content: str,
        cited_title: str | None,
    ) -> str:
        """Build a structured LLM prompt for claim-content verification."""

        # Truncate content to avoid token limits (roughly 4000 tokens)
        max_content_chars = 12000
        truncated_content = cited_content[:max_content_chars]
        if len(cited_content) > max_content_chars:
            truncated_content += "\n\n[... content truncated]"

        title_line = f"Cited Page Title: {cited_title}\n" if cited_title else ""

        return f"""You are a strict fact-checking judge. Determine whether the cited URL content SUPPORTS, CONTRADICTS, or is UNSUPPORTED by the given claim.

Claim to verify:
```
{claim_text}
```

Cited URL: {cited_url}
{title_line}Cited Content:
```
{truncated_content}
```

Respond with a JSON object in exactly this format (no markdown, no extra text):
{{
    "verdict": "supported" | "contradicted" | "unsupported" | "unverifiable",
    "confidence": 0.0-1.0,
    "reasoning": "Brief explanation of your verdict",
    "supporting_quote": "Exact quote from the cited content that supports or contradicts the claim, or empty string if none"
}}

Definitions:
- **supported**: The cited content directly confirms or supports the claim.
- **contradicted**: The cited content directly contradicts the claim.
- **unsupported**: The cited content does not address or support the claim (irrelevant, off-topic).
- **unverifiable**: Cannot determine due to insufficient content, access issues, or ambiguity.
"""

    def _call_llm(self, prompt: str) -> str:
        """Make an HTTP call to the LLM endpoint."""
        import urllib.request
        import urllib.error

        payload = json.dumps({
            "model": self.llm_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 512,
        }).encode("utf-8")

        req = urllib.request.Request(
            self.llm_endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8")
                data = json.loads(body)
                return data.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as exc:
            logger.warning("LLM endpoint call failed: %s", exc)
            raise

    def _parse_verdict(self, response_text: str) -> dict[str, Any]:
        """Parse structured JSON verdict from LLM response.

        Falls back to ``unverifiable`` if parsing fails.
        """
        # Try to extract JSON from response (handles markdown code fences)
        cleaned = response_text.strip()
        if cleaned.startswith("```"):
            # Remove markdown code fences
            cleaned = cleaned.split("\n", 1)[-1]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:].strip()
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM verdict JSON: %s", response_text[:200])
            return {
                "verdict": "unverifiable",
                "confidence": 0.0,
                "reasoning": "Failed to parse LLM response as structured verdict",
                "supporting_quote": "",
            }

        verdict = parsed.get("verdict", "unverifiable")
        if verdict not in self.VALID_VERDICTS:
            verdict = "unverifiable"

        return {
            "verdict": verdict,
            "confidence": float(parsed.get("confidence", 0.0)),
            "reasoning": str(parsed.get("reasoning", "")),
            "supporting_quote": str(parsed.get("supporting_quote", "")),
        }

    def clear_cache(self) -> None:
        """Clear the in-memory grading cache."""
        self._cache.clear()

    def cache_size(self) -> int:
        """Return the number of cached grading results."""
        return len(self._cache)
