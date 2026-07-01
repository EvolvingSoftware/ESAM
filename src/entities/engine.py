"""Entity Extraction Engine — dictionary-based + heuristic entity extraction.

Extracts entities (companies, products, people, concepts, organizations)
from extracted article text using dictionary matching first, then
falling back to regex/heuristics for patterns like capitalized phrases,
email addresses, URL domains, and known suffixes.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from typing import Any, Optional

from database import get_connection

from .dictionary import EntityDictionary

logger = logging.getLogger(__name__)

__all__ = ["EntityExtractor"]

# ── Heuristic Patterns ──────────────────────────────────────────────────

# Email pattern
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# URL domain pattern
URL_DOMAIN_RE = re.compile(r"https?://(?:www\.)?([a-zA-Z0-9-]+\.[a-zA-Z]{2,})(?:/|$)")

# Known legal suffixes (case-insensitive partial match)
KNOWN_SUFFIXES = {"Inc.", "Inc", "LLC", "Corp.", "Corp", "Ltd.", "Ltd", "Co.", "Co", "GmbH", "SA"}

# Industry/acronym suffixes that suggest a company/product/concept
INDUSTRY_SUFFIXES = {"AI", "ML", "API", "SDK", "SaaS", "GPT"}

# Capitalized phrase pattern: 2+ consecutive capitalized words
CAPITALIZED_PHRASE_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,}\b")

# Single capitalized word (potential person)
SINGLE_CAPITAL_RE = re.compile(r"\b[A-Z][a-z]{2,}\b")


class EntityExtractor:
    """Extracts entities from text using dictionary matching and heuristics."""

    def __init__(self, db_conn=None):
        self.db = db_conn or get_connection()
        self.dictionary = EntityDictionary(db_conn=self.db)

    # ── Main Extraction ─────────────────────────────────────────────────

    def extract(self, text: str, source_item_id: str | None = None) -> list[dict]:
        """Extract entities from text.

        Returns::
            [
                {
                    "entity": str,
                    "type": "company"|"product"|"person"|"concept"|"org",
                    "confidence": float,
                    "positions": [{"start": int, "end": int, "fragment": str}]
                },
                ...
            ]
        """
        if not text or not text.strip():
            return []

        seen: dict[str, dict] = {}  # normalized name -> entity dict

        # Step 1: Dictionary matching
        dict_entities = self._match_dictionary(text)
        for ent in dict_entities:
            key = self._normalize(ent["entity"])
            if key not in seen:
                seen[key] = ent
            else:
                # Merge positions
                seen[key]["positions"].extend(ent["positions"])

        # Step 2: Heuristic extraction
        heuristics = self._extract_heuristics(text)
        for ent in heuristics:
            key = self._normalize(ent["entity"])
            if key not in seen or seen[key]["confidence"] < ent["confidence"]:
                seen[key] = ent
            elif key in seen:
                # Also merge positions for heuristic matches
                seen[key]["positions"].extend(ent["positions"])

        # Deduplicate positions within each entity
        result = []
        for ent in seen.values():
            # Deduplicate positions by (start, end)
            unique_positions = []
            seen_positions = set()
            for pos in ent["positions"]:
                pos_key = (pos["start"], pos["end"])
                if pos_key not in seen_positions:
                    seen_positions.add(pos_key)
                    unique_positions.append(pos)
            ent["positions"] = sorted(unique_positions, key=lambda p: p["start"])

            # Recalculate confidence: base from dict match, slight bonus for multiple positions
            if ent["positions"]:
                position_bonus = min(0.3, len(ent["positions"]) * 0.05)
                ent["confidence"] = min(1.0, ent["confidence"] + position_bonus)

            result.append(ent)

        return sorted(result, key=lambda e: (-e["confidence"], e["entity"]))

    def extract_batch(self, items: list[dict], text_field: str = "body_extracted") -> dict:
        """Extract entities from multiple items.

        Returns::
            {
                "items": [{"item_id": str, "entities": [...]}],
                "merged_entities": [{"entity": str, "type": str, "count": int, "items": [str]}]
            }
        """
        item_results = []
        merged: dict[str, dict] = {}

        for item in items:
            item_id = item.get("id", item.get("item_id", ""))
            text = item.get(text_field, item.get("body_extracted", ""))
            if not text:
                item_results.append({"item_id": item_id, "entities": []})
                continue

            entities = self.extract(text, source_item_id=item_id)
            item_results.append({"item_id": item_id, "entities": entities})

            for ent in entities:
                key = self._normalize(ent["entity"])
                if key not in merged:
                    merged[key] = {
                        "entity": ent["entity"],
                        "type": ent["type"],
                        "count": 0,
                        "items": [],
                    }
                merged[key]["count"] += 1
                if item_id not in merged[key]["items"]:
                    merged[key]["items"].append(item_id)

        return {
            "items": item_results,
            "merged_entities": sorted(merged.values(), key=lambda x: -x["count"]),
        }

    # ── Keyword Extraction ──────────────────────────────────────────────

    # Simple stopwords list
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

    def extract_keywords(self, text: str, max_keywords: int = 20) -> list[dict]:
        """Extract keywords from text using simple frequency-based scoring.

        Returns::
            [{"keyword": str, "score": float, "frequency": int}]
        """
        if not text or not text.strip():
            return []

        # Tokenize: split on non-alpha, lowercase, filter stopwords and short tokens
        tokens = re.findall(r"[a-zA-Z][a-zA-Z\-']{1,}", text.lower())
        filtered = [t for t in tokens if t not in self.STOPWORDS and len(t) > 2]

        counter = Counter(filtered)
        total = sum(counter.values())
        max_freq = max(counter.values()) if counter else 1

        keywords = []
        for word, count in counter.most_common(max_keywords * 2):
            freq_ratio = count / max_freq
            # Score: normalize frequency * log bonus for total occurrences
            score = freq_ratio * (1.0 + 0.1 * (count / (total / max(1, len(counter)))))
            score = round(min(1.0, score), 4)
            keywords.append({"keyword": word, "score": score, "frequency": count})

        # Retain top N by score
        keywords.sort(key=lambda x: (-x["score"], -x["frequency"]))
        return keywords[:max_keywords]

    # ── Scoring ─────────────────────────────────────────────────────────

    def score_by_entity(self, items: list[dict], entity_dictionary: list[dict] | None = None) -> list[dict]:
        """Boost item scores by 1.2x if content matches a known entity.

        If the matched entity has authority_tier 'A', the boost is 2.0x.

        Args:
            items: List of items with ``text_field`` content and ``score``.
            entity_dictionary: Optional pre-fetched entity list. If None, loads all.

        Returns:
            Items with updated ``score`` values.
        """
        if entity_dictionary is None:
            entity_dictionary = self.dictionary.list()

        # Build lookup: normalized entity name -> authority_tier
        entity_map: dict[str, str] = {}
        for ent in entity_dictionary:
            key = self._normalize(ent["entity"])
            entity_map[key] = ent.get("authority_tier", "B")
            # Also index aliases
            aliases = ent.get("aliases", "")
            if aliases:
                for alias in aliases.split(","):
                    alias_key = self._normalize(alias.strip())
                    if alias_key:
                        entity_map[alias_key] = ent.get("authority_tier", "B")

        scored = []
        for item in items:
            score = float(item.get("score", 0.0))
            text = item.get("body_extracted", item.get("text", ""))
            if text:
                text_lower = text.lower()
                for entity_name, tier in entity_map.items():
                    if entity_name.lower() in text_lower:
                        if tier == "A":
                            score *= 2.0
                        else:
                            score *= 1.2
                        break  # Apply boost once per item

            scored.append({**item, "score": round(score, 4)})

        return scored

    # ── Internal: Dictionary Matching ───────────────────────────────────

    def _match_dictionary(self, text: str) -> list[dict]:
        """Match known dictionary entities in text."""
        entities = self.dictionary.list()
        results = []
        text_lower = text.lower()

        for ent in entities:
            name = ent["entity"]
            aliases = ent.get("aliases", "")
            all_names = [name]
            if aliases:
                all_names.extend(a.strip() for a in aliases.split(",") if a.strip())

            positions = []
            matched_names = set()
            for candidate in all_names:
                if not candidate.strip():
                    continue
                candidate_lower = candidate.lower()
                if candidate_lower in matched_names:
                    continue
                matched_names.add(candidate_lower)

                # Find all occurrences
                start = 0
                while True:
                    idx = text_lower.find(candidate_lower, start)
                    if idx == -1:
                        break
                    end = idx + len(candidate)
                    fragment = text[idx:end]
                    positions.append({"start": idx, "end": end, "fragment": fragment})
                    start = end + 1

            if positions:
                # Base confidence: more positions = higher confidence
                conf = min(1.0, 0.6 + 0.1 * min(len(positions), 4))
                results.append({
                    "entity": name,
                    "type": ent["type"],
                    "confidence": round(conf, 4),
                    "positions": positions,
                })

        return results

    # ── Internal: Heuristic Extraction ──────────────────────────────────

    def _extract_heuristics(self, text: str) -> list[dict]:
        """Extract entities using heuristic patterns (fallback)."""
        results = []

        # 1. Email patterns -> potential person or org
        for match in EMAIL_RE.finditer(text):
            domain = match.group(0).split("@")[1]
            name_part = match.group(0).split("@")[0].replace(".", " ").replace("_", " ").replace("-", " ").strip()
            results.append({
                "entity": match.group(0),
                "type": "org" if "." in domain and len(domain.split(".")[0]) > 3 else "person",
                "confidence": 0.5,
                "positions": [{"start": match.start(), "end": match.end(), "fragment": match.group(0)}],
            })
            # Also try to extract person name from email prefix
            if name_part and len(name_part) > 3:
                for word in name_part.split():
                    if word[0].isupper():
                        results.append({
                            "entity": word,
                            "type": "person",
                            "confidence": 0.3,
                            "positions": [{"start": match.start(), "end": match.start() + len(name_part), "fragment": name_part}],
                        })

        # 2. URL domains -> potential org
        seen_domains = set()
        for match in URL_DOMAIN_RE.finditer(text):
            domain = match.group(1)
            base = domain.rsplit(".", 1)[0] if domain.count(".") > 1 else domain.split(".")[0]
            if base and base not in seen_domains:
                seen_domains.add(base)
                results.append({
                    "entity": base,
                    "type": "org",
                    "confidence": 0.4,
                    "positions": [{"start": match.start(), "end": match.end(), "fragment": match.group(0)}],
                })

        # 3. Known suffixes -> company/org
        # Look for capitalized phrases right before known suffixes
        suffix_pattern = r"\b([A-Z][a-zA-Z0-9]*(?:\s+[A-Z][a-zA-Z0-9]*)*)\s+(" + \
                         "|".join(re.escape(s) for s in KNOWN_SUFFIXES) + r")\b"
        for match in re.finditer(suffix_pattern, text, re.IGNORECASE):
            name = match.group(1).strip()
            suffix = match.group(2)
            if name and len(name) > 1:
                results.append({
                    "entity": f"{name} {suffix}",
                    "type": "company",
                    "confidence": 0.7,
                    "positions": [{"start": match.start(), "end": match.end(), "fragment": match.group(0)}],
                })

        # 4. Industry/acronym suffixes -> potential company/product/concept
        for suf in INDUSTRY_SUFFIXES:
            pattern = rf"\b([A-Z][a-zA-Z]*)\s+{re.escape(suf)}\b"
            for match in re.finditer(pattern, text):
                name = match.group(1).strip()
                if len(name) > 1:
                    results.append({
                        "entity": f"{name} {suf}",
                        "type": "company" if suf in ("AI", "ML") else "concept",
                        "confidence": 0.5,
                        "positions": [{"start": match.start(), "end": match.end(), "fragment": match.group(0)}],
                    })

        # 5. Capitalized phrases (2+ consecutive capitalized words) -> potential company/org
        for match in CAPITALIZED_PHRASE_RE.finditer(text):
            phrase = match.group(0)
            words = phrase.split()
            # Skip if all words are short (likely not a proper entity)
            if all(len(w) <= 2 for w in words):
                continue
            # Skip if it's just a date-like pattern
            if re.match(r"^[A-Z][a-z]+ \d{1,2},? \d{4}$", phrase):
                continue
            results.append({
                "entity": phrase,
                "type": "org",
                "confidence": 0.35,
                "positions": [{"start": match.start(), "end": match.end(), "fragment": phrase}],
            })

        # 6. Single capitalized words that look like person names (3+ chars, not at sentence start)
        for match in SINGLE_CAPITAL_RE.finditer(text):
            word = match.group(0)
            if len(word) < 3:
                continue
            # Skip if it's the first word in a sentence (likely not a name)
            preceding = text[max(0, match.start() - 2):match.start()].strip()
            if preceding in ("", ".", "!", "?"):
                continue
            # Skip common non-name capitalized words
            if word.lower() in ("the", "this", "that", "these", "those", "what", "which", "when",
                                "where", "there", "here", "they", "them", "their", "were", "have",
                                "has", "had", "been", "being", "some", "such", "each", "both",
                                "most", "other", "into", "over", "under", "about", "then", "than",
                                "also", "very", "just", "because", "after", "before", "while",
                                "during", "through", "until", "against", "within", "without",
                                "along", "among", "upon", "will", "would", "could", "should",
                                "may", "might", "shall", "can", "need", "even", "still", "back",
                                "well", "way", "new", "much", "many", "like", "one", "two"):
                continue
            results.append({
                "entity": word,
                "type": "person",
                "confidence": 0.25,
                "positions": [{"start": match.start(), "end": match.end(), "fragment": word}],
            })

        return results

    # ── Internal: Utilities ─────────────────────────────────────────────

    @staticmethod
    def _normalize(name: str) -> str:
        """Normalize an entity name for deduplication."""
        return name.strip().lower()
