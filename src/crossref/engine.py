"""Cross-Reference Engine — detect topics appearing across multiple sources.

Uses entity overlap and keyword overlap between items to detect topics
that appear in 2+ different sources.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from crossref.linker import EntityLinker
from crossref.booster import SignalBooster
from crossref.clusterer import TopicClusterer

logger = logging.getLogger(__name__)


class CrossReferenceEngine:
    """Detect cross-references across items from different sources.

    Detects topics appearing in 2+ different sources using entity and
    keyword overlap.
    """

    def __init__(self):
        self.linker = EntityLinker()
        self.booster = SignalBooster()
        self.clusterer = TopicClusterer()

    def detect(self, all_items: list[dict]) -> list[dict]:
        """Detect cross-references across all items.

        Args:
            all_items: List of items, each with ``id``, ``entities``
                       (list of entity names), ``keywords`` (optional list
                       of keyword strings), ``source_name`` (string),
                       and optionally ``timestamp``/``date``.

        Returns:
            List of cross-reference dicts::
                {
                    "topic": str,
                    "source_count": int,
                    "item_ids": [str, ...],
                    "source_names": [str, ...],
                    "first_seen": str (ISO timestamp),
                    "last_seen": str (ISO timestamp),
                    "cross_ref_count": int (same as source_count),
                    "sources_represented": [str, ...] (same as source_names),
                    "items": [str, ...] (same as item_ids),
                    "strength": float,
                }
        """
        if not all_items:
            return []

        # Build entity -> item index mapping
        entity_items: dict[str, set[int]] = defaultdict(set)
        keyword_items: dict[str, set[int]] = defaultdict(set)

        for idx, item in enumerate(all_items):
            entities = item.get("entities", [])
            if isinstance(entities, list):
                for ent in entities:
                    ent_name = ""
                    if isinstance(ent, str):
                        ent_name = ent
                    elif isinstance(ent, dict):
                        ent_name = ent.get("entity", str(ent))
                    if ent_name:
                        entity_items[ent_name].add(idx)

            keywords = item.get("keywords", [])
            if isinstance(keywords, list):
                for kw in keywords:
                    kw_name = ""
                    if isinstance(kw, str):
                        kw_name = kw
                    elif isinstance(kw, dict):
                        kw_name = kw.get("keyword", kw.get("text", str(kw)))
                    if kw_name:
                        keyword_items[kw_name.lower()].add(idx)

        # Find items that share entities (high priority) or keywords (lower)
        # Group items by topic (shared entity or keyword)
        topic_groups: dict[str, set[int]] = {}

        # Entities: each entity is a potential topic
        for entity, indices in entity_items.items():
            if len(indices) >= 2:
                topic_groups[entity] = indices

        # Keywords: if not already grouped by entity
        for keyword, indices in keyword_items.items():
            if len(indices) >= 2:
                # Only add if not a duplicate of an existing entity-based group
                already_grouped = False
                for existing_indices in topic_groups.values():
                    if indices == existing_indices:
                        already_grouped = True
                        break
                if not already_grouped:
                    topic_groups[keyword] = indices

        # Build cross-ref results
        cross_refs: list[dict] = []
        for topic, indices in topic_groups.items():
            if len(indices) < 2:
                continue

            item_ids: list[str] = []
            source_names: list[str] = []
            seen_sources: set[str] = set()
            timestamps: list[str] = []

            for idx in indices:
                item = all_items[idx]
                item_ids.append(item.get("id", str(idx)))
                source = item.get("source_name", item.get("source", ""))
                if source and source not in seen_sources:
                    seen_sources.add(source)
                    source_names.append(source)

                ts = item.get("timestamp", item.get("date", ""))
                if ts:
                    timestamps.append(ts)

            source_count = len(seen_sources)

            # Only report topics appearing in 2+ different sources
            if source_count < 2:
                continue

            first_seen = min(timestamps) if timestamps else ""
            last_seen = max(timestamps) if timestamps else ""

            # Strength: source_count / total_items * entity_penetration
            strength = round(
                source_count / max(len(all_items), 1) *
                len(indices) / max(len(all_items), 1),
                3,
            )

            cross_refs.append({
                "topic": topic,
                "source_count": source_count,
                "item_ids": sorted(item_ids),
                "source_names": sorted(source_names),
                "first_seen": first_seen,
                "last_seen": last_seen,
                # Aliases for API consistency
                "cross_ref_count": source_count,
                "sources_represented": sorted(source_names),
                "items": sorted(item_ids),
                "strength": strength,
            })

        # Sort by strength descending
        cross_refs.sort(key=lambda r: r.get("strength", 0), reverse=True)
        return cross_refs

    def boost_scores(
        self,
        items: list[dict],
        cross_refs: list[dict],
        boost_factor: float = 1.3,
    ) -> list[dict]:
        """Boost scores for items that appear in cross-references.

        Each mention from a different source adds the boost_factor as a
        multiplier.

        Args:
            items: List of items with ``id`` and ``combined_score``.
            cross_refs: Output from ``detect()``.
            boost_factor: Base multiplier per additional source (default: 1.3).

        Returns:
            Items with updated ``combined_score``.
        """
        if not items or not cross_refs:
            return items

        # Build item_id -> set of source_names from cross_refs
        item_sources: dict[str, set[str]] = defaultdict(set)
        for ref in cross_refs:
            source_names = ref.get("source_names", [])
            for item_id in ref.get("item_ids", []):
                for sn in source_names:
                    item_sources[item_id].add(sn)

        result = []
        for item in items:
            item_id = item.get("id", str(id(item)))
            sources_for_item = item_sources.get(item_id, set())
            num_sources = len(sources_for_item)

            # Each mention from a different source adds boost_factor multiplier
            if num_sources >= 2:
                multiplier = boost_factor ** (num_sources - 1)
            else:
                multiplier = 1.0

            score = item.get("combined_score", item.get("score", 0.0))
            if not isinstance(score, (int, float)):
                score = 0.0

            item = dict(item)
            item["combined_score"] = round(score * multiplier, 4)
            item["boost_multiplier"] = round(multiplier, 4)
            item["cross_ref_sources"] = num_sources
            result.append(item)

        return result
