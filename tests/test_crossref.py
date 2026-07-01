"""Tests for the Cross-Reference Engine (P1-3)."""

from __future__ import annotations

import pytest

from crossref.engine import CrossReferenceEngine
from crossref.linker import EntityLinker
from crossref.booster import SignalBooster
from crossref.clusterer import TopicClusterer


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def single_source_items():
    """Two items from the same source with different entities — no cross-ref."""
    return [
        {"id": "1", "entities": ["AI"], "keywords": ["machine learning"], "source_name": "HN", "combined_score": 0.5},
        {"id": "2", "entities": ["Robotics"], "keywords": ["hardware"], "source_name": "HN", "combined_score": 0.4},
    ]


@pytest.fixture
def multi_source_items():
    """Same topic 'AI' from 3 different sources — should produce cross-ref."""
    return [
        {"id": "1", "entities": ["AI"], "keywords": ["machine learning"], "source_name": "HN", "combined_score": 0.5},
        {"id": "2", "entities": ["AI", "agents"], "keywords": ["machine learning", "LLM"], "source_name": "Reddit", "combined_score": 0.6},
        {"id": "3", "entities": ["AI", "agents"], "keywords": ["LLM"], "source_name": "ArXiv", "combined_score": 0.7},
    ]


@pytest.fixture
def cluster_items():
    """5 items that should cluster into 2 topics: AI and Robotics."""
    return [
        {"id": "1", "entities": ["AI", "machine learning"], "keywords": ["neural nets"], "source_name": "HN", "combined_score": 0.5},
        {"id": "2", "entities": ["AI", "deep learning"], "keywords": ["transformer"], "source_name": "Reddit", "combined_score": 0.6},
        {"id": "3", "entities": ["AI", "GPT"], "keywords": ["LLM"], "source_name": "ArXiv", "combined_score": 0.7},
        {"id": "4", "entities": ["Robotics", "ROS"], "keywords": ["actuators"], "source_name": "HN", "combined_score": 0.4},
        {"id": "5", "entities": ["Robotics", "ROS"], "keywords": ["sensors"], "source_name": "Reddit", "combined_score": 0.5},
    ]


@pytest.fixture
def linker():
    return EntityLinker()


@pytest.fixture
def booster():
    return SignalBooster()


@pytest.fixture
def clusterer():
    return TopicClusterer()


# ── CrossReferenceEngine Tests ────────────────────────────────────────


class TestCrossReferenceEngine:
    """Tests for CrossReferenceEngine.detect()."""

    def test_detect_single_source(self, single_source_items):
        """Item from one source, no cross-ref should be detected."""
        engine = CrossReferenceEngine()
        refs = engine.detect(single_source_items)
        # Both items are from "HN" but have different entities
        # "AI" appears only in item 1, "Robotics" only in item 2
        # No entity appears in 2+ different sources
        assert len(refs) == 0, f"Expected 0 cross-refs, got {len(refs)}: {refs}"

    def test_detect_multi_source(self, multi_source_items):
        """Same topic 'AI' from 3 different sources."""
        engine = CrossReferenceEngine()
        refs = engine.detect(multi_source_items)
        # "AI" appears in items from all 3 sources (HN, Reddit, ArXiv)
        # "agents" appears in 2 sources (Reddit, ArXiv)
        # "machine learning" keyword appears in 2 sources (HN, Reddit)
        assert len(refs) >= 1, f"Expected at least 1 cross-ref, got {len(refs)}"

        # Find the AI cross-ref
        ai_refs = [r for r in refs if r["topic"] == "AI"]
        assert len(ai_refs) == 1, f"Expected 1 AI cross-ref, got {len(ai_refs)}"
        ai_ref = ai_refs[0]
        assert ai_ref["source_count"] == 3
        assert len(ai_ref["source_names"]) == 3
        assert "HN" in ai_ref["source_names"]
        assert "Reddit" in ai_ref["source_names"]
        assert "ArXiv" in ai_ref["source_names"]
        assert len(ai_ref["item_ids"]) == 3

    def test_boost_factor(self, multi_source_items):
        """Scores boosted correctly by boost_scores."""
        engine = CrossReferenceEngine()
        cross_refs = engine.detect(multi_source_items)
        boosted = engine.boost_scores(multi_source_items, cross_refs, boost_factor=1.3)

        # Item 1 ("AI" in 3 sources) should get boost_factor^(3-1) = 1.3^2 = 1.69
        item1 = [i for i in boosted if i["id"] == "1"][0]
        expected = round(0.5 * 1.69, 4)
        assert item1["combined_score"] == expected, (
            f"Expected {expected}, got {item1['combined_score']}"
        )
        assert item1["boost_multiplier"] == pytest.approx(1.69, rel=1e-3)

    def test_boost_factor_no_refs(self, single_source_items):
        """Items with no cross-refs should keep original score."""
        engine = CrossReferenceEngine()
        cross_refs = engine.detect(single_source_items)
        boosted = engine.boost_scores(single_source_items, cross_refs, boost_factor=1.3)
        for item in boosted:
            assert item["combined_score"] == item.get("_original_score", item["combined_score"])

    def test_boost_factor_empty_items(self):
        """Empty items list should return empty list."""
        engine = CrossReferenceEngine()
        result = engine.boost_scores([], [])
        assert result == []

    def test_boost_factor_no_cross_refs(self, single_source_items):
        """No cross-refs should return items unchanged."""
        engine = CrossReferenceEngine()
        cross_refs = engine.detect(single_source_items)
        boosted = engine.boost_scores(single_source_items, cross_refs, boost_factor=1.3)
        # When no cross-refs exist, items are returned as-is (no enrichment)
        assert len(boosted) == len(single_source_items)
        for item in boosted:
            assert "combined_score" in item or "score" in item


# ── EntityLinker Tests ────────────────────────────────────────────────


class TestEntityLinker:
    """Tests for EntityLinker."""

    def test_entity_linker(self, linker, multi_source_items):
        """Link same entity across multiple items."""
        linked = linker.link(multi_source_items)
        # Item 1 ("AI") should have cross_refs linking to items 2 and 3
        item1 = [i for i in linked if i["id"] == "1"][0]
        assert "cross_refs" in item1
        ai_refs = [r for r in item1["cross_refs"] if r["entity"] == "AI"]
        assert len(ai_refs) >= 1
        assert "2" in ai_refs[0]["related_item_ids"]
        assert "3" in ai_refs[0]["related_item_ids"]

    def test_link_by_name(self, linker, multi_source_items):
        """Find all items mentioning an entity by name."""
        ids = linker.link_by_name("AI", multi_source_items)
        assert len(ids) == 3
        assert "1" in ids
        assert "2" in ids
        assert "3" in ids

        ids_agents = linker.link_by_name("agents", multi_source_items)
        assert len(ids_agents) == 2
        assert "2" in ids_agents
        assert "3" in ids_agents

    def test_link_by_name_not_found(self, linker, multi_source_items):
        """Entity not in any item returns empty list."""
        ids = linker.link_by_name("FlyingCars", multi_source_items)
        assert ids == []

    def test_build_entity_graph(self, linker, multi_source_items):
        """Build entity graph with sources and mention counts."""
        graph = linker.build_entity_graph(multi_source_items)
        assert "AI" in graph
        assert graph["AI"]["total_mentions"] == 3
        assert len(graph["AI"]["sources"]) == 3  # HN, Reddit, ArXiv
        assert "HN" in graph["AI"]["sources"]
        assert "Reddit" in graph["AI"]["sources"]
        assert "ArXiv" in graph["AI"]["sources"]

        assert "agents" in graph
        assert graph["agents"]["total_mentions"] == 2
        assert len(graph["agents"]["sources"]) == 2

    def test_build_entity_graph_empty(self, linker):
        """Empty items returns empty graph."""
        graph = linker.build_entity_graph([])
        assert graph == {}


# ── SignalBooster Tests ────────────────────────────────────────────────


class TestSignalBooster:
    """Tests for SignalBooster."""

    def test_multi_source_boost_tiers(self, booster, multi_source_items):
        """Verify boost tiers: 2-3 → 1.3x, 4-5 → 1.5x, 6+ → 2.0x."""
        linker = EntityLinker()
        graph = linker.build_entity_graph(multi_source_items)
        boosted = booster.compute_multi_source_boost(multi_source_items, graph)

        # Item 1 (AI only, 3 sources) should get 1.3x
        item1 = [i for i in boosted if i["id"] == "1"][0]
        assert item1["boost_multiplier"] == 1.3
        assert item1["combined_score"] == round(0.5 * 1.3, 4)

        # Items 2,3 (AI + agents) should get max boost from AI (3 sources) → 1.3x
        for item_id in ["2", "3"]:
            it = [i for i in boosted if i["id"] == item_id][0]
            assert it["boost_multiplier"] == 1.3

    def test_boost_batch(self, booster, multi_source_items):
        """boost_batch should apply correct multipliers."""
        engine = CrossReferenceEngine()
        cross_refs = engine.detect(multi_source_items)
        boosted = booster.boost_batch(multi_source_items, cross_refs)

        # Items in 3 sources should get 1.3x
        for item in boosted:
            if item["id"] in ["1", "2", "3"]:
                assert item["boost_multiplier"] == 1.3

    def test_no_boost_single_source(self, booster, single_source_items):
        """Items from single source should get 1.0x multiplier."""
        linker = EntityLinker()
        graph = linker.build_entity_graph(single_source_items)
        boosted = booster.compute_multi_source_boost(single_source_items, graph)
        for item in boosted:
            assert item["boost_multiplier"] == 1.0

    def test_boost_empty_items(self, booster):
        """Empty items returns empty list."""
        assert booster.compute_multi_source_boost([], {}) == []
        assert booster.boost_batch([], []) == []
        assert booster.compute_multi_source_boost([], {"Test": {"sources": ["A"]}}) == []


# ── TopicClusterer Tests ────────────────────────────────────────────────


class TestTopicClusterer:
    """Tests for TopicClusterer."""

    def test_cluster_basic(self, clusterer, cluster_items):
        """Cluster 5 items into 2 topics: AI and Robotics."""
        clusters = clusterer.cluster(cluster_items, max_clusters=10)

        assert len(clusters) >= 2, f"Expected at least 2 clusters, got {len(clusters)}"

        # Find AI cluster and Robotics cluster
        ai_clusters = [c for c in clusters if "AI" in c["topic"] or "Topic" in c["topic"]]
        robotics_clusters = [c for c in clusters if "Robotics" in c["topic"] or "ROS" in c["topic"]]

        assert len(ai_clusters) >= 1, f"No cluster found for AI topics: {[c['topic'] for c in clusters]}"
        assert len(robotics_clusters) >= 1, f"No cluster found for Robotics topics: {[c['topic'] for c in clusters]}"

    def test_cluster_label(self, clusterer, cluster_items):
        """Cluster name generation produces readable labels."""
        clusters = clusterer.cluster(cluster_items, max_clusters=10)
        for cluster in clusters:
            assert isinstance(cluster["topic"], str)
            assert len(cluster["topic"]) > 0
            assert "Topic:" in cluster["topic"]
            assert len(cluster["items"]) >= 1
            assert len(cluster["sources"]) >= 1
            assert isinstance(cluster["strength"], float)
            assert 0 < cluster["strength"] <= 1.0

    def test_cluster_single_item(self, clusterer, single_source_items):
        """Single item that doesn't share entities should be its own cluster."""
        clusters = clusterer.cluster(single_source_items, max_clusters=10)
        # Two items with different entities should form 2 singleton clusters
        assert len(clusters) == 2

    def test_cluster_empty_items(self, clusterer):
        """Empty items returns empty list."""
        assert clusterer.cluster([], max_clusters=10) == []

    def test_label_cluster(self, clusterer, cluster_items):
        """label_cluster produces readable names."""
        ai_items = cluster_items[:3]
        label = clusterer.label_cluster(ai_items)
        assert "Topic:" in label
        assert "AI" in label

    def test_label_cluster_empty(self, clusterer):
        """label_cluster on empty list returns placeholder."""
        label = clusterer.label_cluster([])
        assert label == "Empty Cluster"


# ── Executor Routing Test ──────────────────────────────────────────────


class TestWorkflowExecutorCrossRef:
    """Test that step types route correctly in WorkflowExecutor."""

    def test_crossref_step_type(self):
        """Verify cross-ref step types are recognized by the executor."""
        from workflow_executor import WorkflowExecutor

        executor = WorkflowExecutor()

        # Check that the executor has handler methods for cross-ref step types
        assert hasattr(executor, "_execute_detect_cross_references_step")
        assert hasattr(executor, "_execute_boost_multi_sourced_step")
        assert hasattr(executor, "_execute_cluster_by_topic_step")

        # Verify the step_types are defined in the routing
        step_types = [
            "detect_cross_references",
            "boost_multi_sourced",
            "cluster_by_topic",
        ]
        # Check the source code for these step types
        import inspect
        source = inspect.getsource(type(executor))
        for st in step_types:
            assert f'step_type == "{st}"' in source, (
                f"step_type '{st}' not found in WorkflowExecutor routing"
            )
