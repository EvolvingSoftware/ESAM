"""Tests for the Entity Extractor components."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure src is on the path
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from entities.dictionary import EntityDictionary
from entities.engine import EntityExtractor
from entities.topic_extractor import TopicExtractor


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def dictionary():
    d = EntityDictionary()
    # Clean any existing test data
    for ent in d.list():
        d.delete(ent["id"])
    return d


@pytest.fixture
def seeded_dict(dictionary):
    dictionary.seed_defaults()
    return dictionary


@pytest.fixture
def extractor():
    return EntityExtractor()


# ── Dictionary Tests ────────────────────────────────────────────────


def test_dictionary_add_get(dictionary):
    """Roundtrip add and get an entity."""
    result = dictionary.add("TestCorp", "company", aliases="TC", category="Test")
    assert result is not None
    assert result["entity"] == "TestCorp"
    assert result["type"] == "company"
    assert result["aliases"] == "TC"
    assert result["category"] == "Test"
    assert result["authority_tier"] == "B"

    fetched = dictionary.get(result["id"])
    assert fetched is not None
    assert fetched["entity"] == "TestCorp"


def test_dictionary_seed(seeded_dict):
    """Seed defaults should create at least 15 entities."""
    entities = seeded_dict.list()
    count = len(entities)
    assert count >= 15, f"Expected >=15 entities, got {count}"

    # Verify some known entities exist
    names = {e["entity"] for e in entities}
    assert "OpenAI" in names
    assert "ChatGPT" in names
    assert "RAG" in names
    assert "Multi-Agent Systems" in names


def test_dictionary_find(seeded_dict):
    """Fuzzy find by name should return matching entities."""
    results = seeded_dict.find("Open")
    assert len(results) >= 1
    assert any(r["entity"] == "OpenAI" for r in results)

    results = seeded_dict.find("Chat")
    assert len(results) >= 1
    assert any(r["entity"] == "ChatGPT" for r in results)

    results = seeded_dict.find("NonexistentXYZ")
    assert len(results) == 0


def test_dictionary_list_filter(seeded_dict):
    """List should support filtering by type and category."""
    companies = seeded_dict.list(entity_type="company")
    assert len(companies) >= 5
    assert all(c["type"] == "company" for c in companies)

    concepts = seeded_dict.list(entity_type="concept")
    assert len(concepts) >= 3
    assert all(c["type"] == "concept" for c in concepts)


def test_dictionary_delete(seeded_dict):
    """Deleting an entity should remove it."""
    entities = seeded_dict.list()
    count_before = len(entities)
    if entities:
        result = seeded_dict.delete(entities[0]["id"])
        assert result is True
        count_after = len(seeded_dict.list())
        assert count_after == count_before - 1


# ── Entity Extraction Tests ──────────────────────────────────────────


def test_extract_company(extractor, seeded_dict):
    """Extract 'OpenAI' from text."""
    text = "OpenAI announced a new model today."
    results = extractor.extract(text)
    names = [r["entity"] for r in results]
    assert "OpenAI" in names
    openai = next(r for r in results if r["entity"] == "OpenAI")
    assert openai["type"] == "company"
    assert openai["confidence"] > 0.5
    assert len(openai["positions"]) >= 1


def test_extract_product(extractor, seeded_dict):
    """Extract 'ChatGPT' from text."""
    text = "I used ChatGPT to write this code."
    results = extractor.extract(text)
    names = [r["entity"] for r in results]
    assert "ChatGPT" in names
    chatgpt = next(r for r in results if r["entity"] == "ChatGPT")
    assert chatgpt["type"] == "product"
    assert chatgpt["confidence"] > 0.5


def test_extract_multiple_entities(extractor, seeded_dict):
    """Extract multiple entities from the same text."""
    text = "OpenAI's ChatGPT and Anthropic's Claude are both great AI products."
    results = extractor.extract(text)
    names = [r["entity"] for r in results]
    assert "OpenAI" in names
    assert "ChatGPT" in names
    assert "Anthropic" in names
    assert "Claude" in names


def test_extract_with_heuristics(extractor):
    """Heuristic extraction should find org-like patterns (Inc., LLC, etc.)."""
    text = "ACME Corp. announced a partnership with Widgets LLC yesterday."
    results = extractor.extract(text)
    names = [r["entity"] for r in results]
    assert any("Corp" in n for n in names), f"Expected Corp in {names}"
    assert any("LLC" in n for n in names), f"Expected LLC in {names}"


def test_batch_extract(extractor, seeded_dict):
    """Batch extraction across multiple items."""
    items = [
        {"id": "item1", "body_extracted": "OpenAI launched GPT-4o."},
        {"id": "item2", "body_extracted": "Anthropic released Claude 3."},
        {"id": "item3", "body_extracted": "Just some random text."},
    ]
    result = extractor.extract_batch(items)
    assert "items" in result
    assert "merged_entities" in result
    assert len(result["items"]) == 3
    assert result["items"][0]["item_id"] == "item1"
    merged_names = [m["entity"] for m in result["merged_entities"]]
    assert "OpenAI" in merged_names
    assert "Anthropic" in merged_names


# ── Keyword Extraction Tests ─────────────────────────────────────────


def test_keywords_extraction(extractor):
    """Extract keywords from text."""
    text = "Machine learning and artificial intelligence are transforming the technology industry. Machine learning enables computers to learn from data. Artificial intelligence powers modern applications."
    keywords = extractor.extract_keywords(text, max_keywords=10)
    assert len(keywords) <= 10
    assert len(keywords) >= 3
    kw_names = [k["keyword"] for k in keywords]
    # "machine" and "learning" should appear
    assert "machine" in kw_names, f"Expected 'machine' in {kw_names}"
    assert "learning" in kw_names, f"Expected 'learning' in {kw_names}"
    # Each keyword should have score and frequency
    for kw in keywords:
        assert "score" in kw
        assert "frequency" in kw
        assert kw["frequency"] >= 1


def test_keywords_empty_text(extractor):
    """Empty text should return empty list."""
    assert extractor.extract_keywords("") == []
    assert extractor.extract_keywords("   ") == []


# ── Scoring Tests ────────────────────────────────────────────────────


def test_score_by_entity(extractor, seeded_dict):
    """Score boost logic."""
    items = [
        {"id": "a", "body_extracted": "OpenAI is leading AI research.", "score": 10.0},
        {"id": "b", "body_extracted": "Some random tech news.", "score": 10.0},
        {"id": "c", "body_extracted": "Microsoft is investing in AI.", "score": 10.0},
    ]
    scored = extractor.score_by_entity(items)
    assert len(scored) == 3
    # Item with OpenAI (tier A) should have score > original (boosted 2x)
    assert scored[0]["score"] > 10.0, f"Score should be boosted: {scored}"
    # Some should have different scores based on entity authority


# ── Topic Extraction Tests ───────────────────────────────────────────


def test_topic_extraction():
    """Topic clustering from text."""
    extractor = TopicExtractor()
    text = """
    Machine learning is transforming the technology industry. Deep learning models
    are becoming more powerful. Artificial intelligence research continues to advance
    rapidly. Natural language processing enables new applications. Computer vision
    systems are improving dramatically. Machine learning techniques evolve quickly.
    """
    topics = extractor.extract(text)
    assert len(topics) >= 1
    for t in topics:
        assert "topic" in t
        assert "score" in t
        assert "keywords" in t
        assert isinstance(t["keywords"], list)
    # Should contain machine-learning related topics
    all_keywords = [kw for t in topics for kw in t["keywords"]]
    assert "machine" in all_keywords or "learning" in all_keywords or "deep" in all_keywords


def test_topic_empty_text():
    """Empty text should return empty list."""
    extractor = TopicExtractor()
    assert extractor.extract("") == []
    assert extractor.extract("   ") == []


def test_topic_extract_from_items():
    """Extract topics from multiple items."""
    extractor = TopicExtractor()
    items = [
        {"id": "a", "body_extracted": "Machine learning is changing the world of technology."},
        {"id": "b", "body_extracted": "Deep learning models require lots of data for training."},
        {"id": "c", "body_extracted": "Artificial intelligence systems are getting smarter every day."},
    ]
    result = extractor.extract_from_items(items)
    assert "topics" in result
    assert "all_keywords" in result
    assert len(result["topics"]) >= 1
    assert len(result["all_keywords"]) >= 3


# ── Step Type Routing Tests ──────────────────────────────────────────


def test_entity_step_type():
    """Verify the step types are recognized by the executor dispatch."""
    from workflow_executor import WorkflowExecutor
    # Just check the class has the methods
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    assert hasattr(executor, "_execute_extract_entities_batch_step")
    assert hasattr(executor, "_execute_extract_keywords_step")
    assert hasattr(executor, "_execute_score_by_entity_step")


# ── Edge Cases ───────────────────────────────────────────────────────


def test_extract_empty_text(extractor):
    """Empty text should return empty list."""
    assert extractor.extract("") == []
    assert extractor.extract(None) == []
    assert extractor.extract("   ") == []


def test_extract_with_urls(extractor):
    """URL extraction heuristic."""
    text = "Check out https://openai.com for more info."
    results = extractor.extract(text)
    names = [r["entity"] for r in results]
    assert "openai" in names or any("openai" in n.lower() for n in names)
