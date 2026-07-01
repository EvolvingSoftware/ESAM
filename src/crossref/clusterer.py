"""Topic Clusterer — group items into topic clusters using entity and keyword overlap."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Any

logger = logging.getLogger(__name__)


class TopicClusterer:
    """Group items into topic clusters based on entity and keyword overlap."""

    def cluster(self, items: list[dict], max_clusters: int = 10) -> list[dict]:
        """Cluster items by topic.

        Uses entity overlap, keyword overlap, and source diversity to
        group related items together.

        Args:
            items: List of items, each with ``id``, ``entities``, ``keywords``,
                   ``source_name``/``source``.
            max_clusters: Maximum number of clusters to return (default: 10).

        Returns:
            List of cluster dicts:
            ``{cluster_id, topic, items: [ids], sources: [names], strength, keywords}``
        """
        if not items:
            return []

        # Build entity -> set of item indices
        entity_items: dict[str, set[int]] = defaultdict(set)
        keyword_items: dict[str, set[int]] = defaultdict(set)

        for idx, item in enumerate(items):
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

        # Build similarity graph: items are connected if they share entities or keywords
        adjacency: list[set[int]] = [set() for _ in items]
        for idx in range(len(items)):
            for jdx in range(idx + 1, len(items)):
                # Check entity overlap
                ent_overlap = False
                for entity, e_ids in entity_items.items():
                    if idx in e_ids and jdx in e_ids:
                        ent_overlap = True
                        break
                if ent_overlap:
                    adjacency[idx].add(jdx)
                    adjacency[jdx].add(idx)
                    continue

                # Check keyword overlap
                for kw, k_ids in keyword_items.items():
                    if idx in k_ids and jdx in k_ids:
                        adjacency[idx].add(jdx)
                        adjacency[jdx].add(idx)
                        break

        # Connected components (BFS) = clusters
        visited = [False] * len(items)
        clusters: list[list[int]] = []
        for idx in range(len(items)):
            if not visited[idx]:
                component: list[int] = []
                stack = [idx]
                while stack:
                    node = stack.pop()
                    if not visited[node]:
                        visited[node] = True
                        component.append(node)
                        for neighbor in adjacency[node]:
                            if not visited[neighbor]:
                                stack.append(neighbor)
                clusters.append(component)

        # Sort clusters by size (largest first)
        clusters.sort(key=len, reverse=True)

        # Build result
        result = []
        for cid, cluster_indices in enumerate(clusters[:max_clusters]):
            cluster_items = [items[i] for i in cluster_indices]

            # Collect item IDs
            item_ids = sorted(
                items[i].get("id", str(i)) for i in cluster_indices
            )

            # Collect sources
            sources: list[str] = []
            seen_sources: set[str] = set()
            for i in cluster_indices:
                src = items[i].get("source_name", items[i].get("source", ""))
                if src and src not in seen_sources:
                    seen_sources.add(src)
                    sources.append(src)

            # Collect keywords
            keyword_counter: Counter[str] = Counter()
            for i in cluster_indices:
                kws = items[i].get("keywords", [])
                if isinstance(kws, list):
                    for kw in kws:
                        kw_name = ""
                        if isinstance(kw, str):
                            kw_name = kw
                        elif isinstance(kw, dict):
                            kw_name = kw.get("keyword", kw.get("text", str(kw)))
                        if kw_name:
                            keyword_counter[kw_name.lower()] += 1
            top_keywords = [kw for kw, _ in keyword_counter.most_common(10)]

            # Strength = cluster size / total items
            strength = round(len(cluster_indices) / max(len(items), 1), 3)

            # Generate topic label
            topic = self.label_cluster(cluster_items, top_keywords)

            result.append({
                "cluster_id": cid,
                "topic": topic,
                "items": item_ids,
                "sources": sources,
                "strength": strength,
                "keywords": top_keywords,
            })

        return result

    def label_cluster(self, items: list[dict], keywords: list[str] | None = None) -> str:
        """Generate a human-readable cluster name from items.

        Uses the most common entities or keywords across items.

        Args:
            items: List of items in the cluster.
            keywords: Optional pre-computed top keywords.

        Returns:
            Human-readable cluster name string.
        """
        if not items:
            return "Empty Cluster"

        # Count entity occurrences
        entity_counter: Counter[str] = Counter()
        for item in items:
            entities = item.get("entities", [])
            if isinstance(entities, list):
                for ent in entities:
                    if isinstance(ent, str):
                        entity_counter[ent] += 1
                    elif isinstance(ent, dict):
                        entity_counter[ent.get("entity", str(ent))] += 1

        # Use most common entities for the label
        top_entities = [e for e, _ in entity_counter.most_common(5)]

        if top_entities:
            if len(top_entities) == 1:
                return f"Topic: {top_entities[0]}"
            return f"Topic: {', '.join(top_entities[:3])}"

        # Fall back to keywords
        if keywords:
            if len(keywords) == 1:
                return f"Topic: {keywords[0]}"
            return f"Topic: {', '.join(keywords[:3])}"

        return f"Cluster ({len(items)} items)"
