"""Entity Linker — link same entities across different items and sources."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)


class EntityLinker:
    """Link same entities across different items and build entity graphs."""

    def link(self, items: list[dict]) -> list[dict]:
        """Link same entities across items.

        Returns items enriched with cross-reference metadata: for each item,
        adds ``cross_refs`` list of entities that appear in multiple items.
        """
        if not items:
            return items

        # Build entity -> set of item IDs
        entity_items: dict[str, set[str]] = defaultdict(set)
        for item in items:
            item_id = item.get("id", str(id(item)))
            entities = item.get("entities", [])
            if isinstance(entities, list):
                for ent in entities:
                    if isinstance(ent, str):
                        entity_items[ent].add(item_id)
                    elif isinstance(ent, dict):
                        entity_items[ent.get("entity", str(ent))].add(item_id)

        # Find entities that appear in 2+ items
        cross_entity_items: dict[str, list[str]] = {}
        for entity, ids in entity_items.items():
            if len(ids) >= 2:
                cross_entity_items[entity] = sorted(ids)

        # Enrich each item with cross_refs
        for item in items:
            item_id = item.get("id", str(id(item)))
            cross_refs: list[dict] = []
            for entity, ids in cross_entity_items.items():
                if item_id in ids:
                    cross_refs.append({
                        "entity": entity,
                        "related_item_ids": [i for i in ids if i != item_id],
                        "total_mentions": len(ids),
                    })
            item["cross_refs"] = cross_refs

        return items

    def link_by_name(self, name: str, items: list[dict]) -> list[str]:
        """Find all item IDs mentioning an entity by name.

        Returns sorted list of item IDs.
        """
        matched: list[str] = []
        name_lower = name.strip().lower()
        for item in items:
            item_id = item.get("id", str(id(item)))
            entities = item.get("entities", [])
            if isinstance(entities, list):
                for ent in entities:
                    ent_name = ""
                    if isinstance(ent, str):
                        ent_name = ent
                    elif isinstance(ent, dict):
                        ent_name = ent.get("entity", str(ent))
                    if ent_name.strip().lower() == name_lower:
                        matched.append(item_id)
                        break
        return sorted(matched)

    def build_entity_graph(self, items: list[dict]) -> dict:
        """Build entity graph from items.

        Returns::
            {
                entity_name: {
                    "type": str,
                    "items": [item_ids],
                    "sources": [source_names],
                    "total_mentions": int,
                },
                ...
            }
        """
        graph: dict[str, dict[str, Any]] = {}

        for item in items:
            item_id = item.get("id", str(id(item)))
            source_name = item.get("source_name", item.get("source", ""))
            entities = item.get("entities", [])

            if isinstance(entities, list):
                for ent in entities:
                    ent_name = ""
                    ent_type = "unknown"
                    if isinstance(ent, str):
                        ent_name = ent
                    elif isinstance(ent, dict):
                        ent_name = ent.get("entity", str(ent))
                        ent_type = ent.get("type", "unknown")

                    if not ent_name:
                        continue

                    if ent_name not in graph:
                        graph[ent_name] = {
                            "type": ent_type,
                            "items": [],
                            "sources": [],
                            "total_mentions": 0,
                        }

                    entry = graph[ent_name]
                    if item_id not in entry["items"]:
                        entry["items"].append(item_id)
                    if source_name and source_name not in entry["sources"]:
                        entry["sources"].append(source_name)
                    entry["total_mentions"] += 1

        return graph
