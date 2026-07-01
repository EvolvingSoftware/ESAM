"""Topic Extractor — groups related keywords into topics using TF-like scoring.

Uses term frequency + inverse section bonus to score keywords,
then groups related keywords into coherent topics.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = ["TopicExtractor"]

# Common stopwords
STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can", "need",
    "this", "that", "these", "those", "it", "its", "they", "them", "their",
    "we", "us", "our", "you", "your", "he", "she", "him", "her", "his",
    "not", "no", "nor", "so", "if", "then", "than", "too", "very", "just",
    "about", "above", "after", "again", "all", "also", "any", "because",
    "before", "between", "both", "each", "few", "more", "most", "other",
    "some", "such", "only", "own", "same", "into", "over", "under", "up",
    "out", "off", "down", "here", "there", "when", "where", "why", "how",
    "what", "which", "who", "whom", "while", "during", "through", "until",
    "against", "within", "without", "along", "around", "among", "upon",
    "get", "got", "getting", "make", "made", "making", "like", "use",
    "used", "using", "take", "took", "taking", "one", "two", "new", "also",
    "much", "many", "even", "still", "back", "well", "way", "see", "know",
    "say", "said", "think", "going", "come", "came", "look", "want",
})


class TopicExtractor:
    """Extracts topics from text by scoring and grouping keywords."""

    def extract(self, text: str) -> list[dict]:
        """Extract topics from text.

        Returns::
            [{"topic": str, "score": float, "keywords": [str]}]
        """
        if not text or not text.strip():
            return []

        # Tokenize with positions for section bonus
        tokens_with_pos = self._tokenize_with_positions(text)

        if not tokens_with_pos:
            return []

        # Count terms
        term_counter: dict[str, int] = Counter()
        # Track which "sections" each term appears in (early = bonus)
        term_positions: dict[str, list[int]] = defaultdict(list)

        for token, pos in tokens_with_pos:
            term_counter[token] += 1
            term_positions[token].append(pos)

        # Score each term: TF * inverse section bonus
        total_tokens = sum(term_counter.values())
        max_freq = max(term_counter.values()) if term_counter else 1
        text_length = len(text)

        scored_terms: list[dict] = []
        for term, freq in term_counter.most_common(100):
            # Skip single-occurrence terms unless text is very short (< 30 tokens)
            if freq < 2 and len(term_counter) > 15:
                continue

            # Term frequency ratio
            tf_ratio = freq / max_freq

            # Inverse section bonus: terms appearing earlier in the text get a bonus
            min_pos = min(term_positions[term])
            section_factor = 1.0 + max(0, 1.0 - (min_pos / max(1, text_length)))

            # Normalized frequency within total
            freq_norm = freq / max(1, total_tokens)

            score = (tf_ratio * 0.6 + freq_norm * 0.4) * section_factor
            score = round(min(1.0, score), 4)

            scored_terms.append({
                "keyword": term,
                "score": score,
                "frequency": freq,
                "first_position": min_pos,
            })

        # Sort by score descending
        scored_terms.sort(key=lambda x: (-x["score"], -x["frequency"]))

        # Group related keywords into topics
        topics = self._group_topics(scored_terms)

        return topics

    def extract_from_items(self, items: list[dict], text_field: str = "body_extracted") -> dict:
        """Extract topics from multiple content items.

        Returns::
            {
                "topics": [{"topic": str, "score": float, "items": [str]}],
                "all_keywords": [{"keyword": str, "score": float}]
            }
        """
        # Extract keywords from each item
        all_scored: dict[str, dict] = {}
        topic_item_map: dict[str, list[str]] = defaultdict(list)

        for item in items:
            item_id = item.get("id", item.get("item_id", ""))
            text = item.get(text_field, item.get("body_extracted", ""))
            if not text:
                continue

            topics = self.extract(text)
            for t in topics:
                topic_name = t["topic"]
                if topic_name not in topic_item_map:
                    topic_item_map[topic_name] = []
                if item_id and item_id not in topic_item_map[topic_name]:
                    topic_item_map[topic_name].append(item_id)

                # Accumulate keyword scores across items
                for kw in t.get("keywords", []):
                    if kw not in all_scored:
                        all_scored[kw] = {"keyword": kw, "score": 0.0}
                    all_scored[kw]["score"] += t["score"] * 0.5  # Diminishing contribution

        # Build merged topics with cross-item counts
        merged_topics = []
        seen_topic_names = set()
        for t in self.extract(" ".join(item.get(text_field, "") for item in items if text_field in item)):
            name = t["topic"]
            if name in seen_topic_names:
                continue
            seen_topic_names.add(name)
            merged_topics.append({
                "topic": name,
                "score": t["score"],
                "items": sorted(topic_item_map.get(name, [])),
            })

        merged_topics.sort(key=lambda x: -x["score"])

        all_keywords = sorted(all_scored.values(), key=lambda x: -x["score"])[:50]

        return {
            "topics": merged_topics,
            "all_keywords": all_keywords,
        }

    # ── Internal ────────────────────────────────────────────────────────

    def _tokenize_with_positions(self, text: str) -> list[tuple[str, int]]:
        """Tokenize text and return (token, position) pairs."""
        tokens = []
        for match in re.finditer(r"[a-zA-Z][a-zA-Z\-']{2,}", text):
            token = match.group(0).lower()
            if token not in STOPWORDS:
                tokens.append((token, match.start()))
        return tokens

    def _group_topics(self, scored_terms: list[dict]) -> list[dict]:
        """Group related keywords into topics based on shared substrings and proximity."""
        if not scored_terms:
            return []

        # Use the top scored terms to form topics
        top_terms = scored_terms[:30]

        topics: list[dict] = []
        used_keywords: set[str] = set()

        for term in top_terms:
            kw = term["keyword"]
            if kw in used_keywords:
                continue

            # Find related keywords by shared prefix/stemming heuristic
            related = [kw]
            used_keywords.add(kw)

            for other in top_terms:
                other_kw = other["keyword"]
                if other_kw in used_keywords:
                    continue
                # Check if they share a common stem (first 4+ chars) or one contains the other
                if (len(kw) >= 4 and len(other_kw) >= 4 and kw[:4] == other_kw[:4]) \
                   or kw in other_kw or other_kw in kw:
                    related.append(other_kw)
                    used_keywords.add(other_kw)
                    if len(related) >= 5:
                        break

            # Score is the max of the constituent terms (or use average)
            topic_score = max(t["score"] for t in top_terms if t["keyword"] in related)
            topic_name = max(related, key=lambda x: (
                sum(t["frequency"] for t in top_terms if t["keyword"] == x), len(x)
            ))

            topics.append({
                "topic": topic_name,
                "score": topic_score,
                "keywords": related[:5],
            })

        return sorted(topics, key=lambda x: -x["score"])[:10]
