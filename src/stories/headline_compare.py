"""Headline comparison using Jaccard similarity on normalized tokens."""

from __future__ import annotations

import re
from typing import Optional

# Common English stopwords for headline normalization
_STOPWORDS: set[str] = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "by", "with", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "can", "could", "shall", "should", "may", "might", "must", "it", "its",
    "they", "them", "their", "we", "us", "our", "you", "your", "he", "she",
    "him", "her", "his", "this", "that", "these", "those", "not", "no",
    "nor", "so", "as", "if", "then", "than", "just", "about", "up", "out",
    "how", "what", "when", "where", "why", "who", "which", "also", "very",
    "too", "yet", "already",
}


class HeadlineComparer:
    """Compare headlines using token overlap (Jaccard similarity).

    Normalizes titles by lowercasing, stripping punctuation, and removing
    common stopwords before computing similarity.  A score > 0.3 generally
    indicates the same story.
    """

    @staticmethod
    def _normalize(text: str) -> set[str]:
        """Tokenize and normalize a headline into a set of significant tokens."""
        text = text.lower()
        # Strip punctuation (keep letters, digits, spaces, hyphens, apostrophes)
        text = re.sub(r"[^a-z0-9\s'\-]", " ", text)
        tokens = text.split()
        return {t for t in tokens if t and t not in _STOPWORDS and len(t) > 1}

    def similarity(self, title_a: str, title_b: str) -> float:
        """Compute Jaccard similarity between two titles.

        Returns a float in ``[0.0, 1.0]``.  A score > 0.3 is considered
        likely the same story.
        """
        if not title_a or not title_b:
            return 0.0

        tokens_a = self._normalize(title_a)
        tokens_b = self._normalize(title_b)

        if not tokens_a or not tokens_b:
            # Fall back to raw char overlap for very short strings
            a_lower = title_a.lower().strip()
            b_lower = title_b.lower().strip()
            if a_lower == b_lower:
                return 1.0
            # Check if one is contained in the other
            if a_lower in b_lower or b_lower in a_lower:
                return 0.6
            return 0.0

        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b

        return len(intersection) / len(union)

    def find_match(
        self, title: str, existing_titles: list[str]
    ) -> tuple[Optional[str], float]:
        """Find the best-matching title from *existing_titles*.

        Returns ``(best_match_title, similarity_score)`` or ``(None, 0.0)``
        if no match exceeds the 0.3 threshold.
        """
        best_title: Optional[str] = None
        best_score = 0.0

        for existing in existing_titles:
            score = self.similarity(title, existing)
            if score > best_score:
                best_score = score
                best_title = existing

        if best_score >= 0.3:
            return best_title, best_score
        return None, 0.0
