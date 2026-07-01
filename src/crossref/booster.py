"""Signal Booster — boost item scores based on multi-source presence.

Boosts items that appear across multiple sources, with tiers for
increasing source diversity.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class SignalBooster:
    """Boost signal scores for items appearing across multiple sources."""

    def compute_multi_source_boost(self, items: list[dict], entity_graph: dict) -> list[dict]:
        """Boost item scores using entity graph data.

        Boost tiers:
          - 2-3 sources: 1.3x
          - 4-5 sources: 1.5x
          - 6+ sources:  2.0x

        Each item gets the maximum boost from any entity it participates in.

        Args:
            items: List of items, each with ``id``, ``combined_score``, ``entities``.
            entity_graph: Output from ``EntityLinker.build_entity_graph()``.

        Returns:
            Items with updated ``combined_score``.
        """
        if not items or not entity_graph:
            return items

        # Build item_id -> max source_count from entity_graph
        item_source_count: dict[str, int] = {}
        for entity_name, entry in entity_graph.items():
            source_count = len(entry.get("sources", []))
            for item_id in entry.get("items", []):
                current = item_source_count.get(item_id, 0)
                if source_count > current:
                    item_source_count[item_id] = source_count

        # Apply boost
        result = []
        for item in items:
            item_id = item.get("id", str(id(item)))
            source_count = item_source_count.get(item_id, 0)
            score = item.get("combined_score", item.get("score", 0.0))
            if not isinstance(score, (int, float)):
                score = 0.0

            if source_count >= 6:
                multiplier = 2.0
            elif source_count >= 4:
                multiplier = 1.5
            elif source_count >= 2:
                multiplier = 1.3
            else:
                multiplier = 1.0

            item = dict(item)  # shallow copy
            item["combined_score"] = round(score * multiplier, 4)
            item["boost_multiplier"] = multiplier
            item["source_count"] = source_count
            result.append(item)

        return result

    def boost_batch(self, items: list[dict], cross_refs: list[dict]) -> list[dict]:
        """Boost batch of items based on cross-reference data.

        Args:
            items: List of items, each with ``id``, ``combined_score`` (or ``score``).
            cross_refs: List of cross-reference dicts from ``CrossReferenceEngine.detect()``.

        Returns:
            Items with updated ``combined_score``.
        """
        if not items or not cross_refs:
            return items

        # Build item_id -> max source_count from cross_refs
        item_source_count: dict[str, int] = {}
        for ref in cross_refs:
            source_count = ref.get("source_count", len(ref.get("source_names", [])))
            for item_id in ref.get("item_ids", []):
                current = item_source_count.get(item_id, 0)
                if source_count > current:
                    item_source_count[item_id] = source_count

        # Apply boost
        result = []
        for item in items:
            item_id = item.get("id", str(id(item)))
            source_count = item_source_count.get(item_id, 0)
            score = item.get("combined_score", item.get("score", 0.0))
            if not isinstance(score, (int, float)):
                score = 0.0

            if source_count >= 6:
                multiplier = 2.0
            elif source_count >= 4:
                multiplier = 1.5
            elif source_count >= 2:
                multiplier = 1.3
            else:
                multiplier = 1.0

            item = dict(item)
            item["combined_score"] = round(score * multiplier, 4)
            item["boost_multiplier"] = multiplier
            item["source_count"] = source_count
            result.append(item)

        return result
