"""Tests for the Citation Map Generator module."""

import json
import os
import sys
import uuid
from pathlib import Path

import pytest

# Add src to path
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from citation.engine import CitationEngine
from citation.resolver import CitationResolver
from citation.map import CitationMap
from database import get_connection


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _ensure_table():
    """Ensure the wf_citation_map table exists for each test."""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS wf_citation_map (
            id TEXT PRIMARY KEY,
            source_id TEXT, item_id TEXT,
            citation_id TEXT NOT NULL UNIQUE,
            url TEXT NOT NULL, title TEXT DEFAULT '',
            content_hash TEXT DEFAULT '', fetch_run_id TEXT,
            created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_wf_citation_map_ids ON wf_citation_map(citation_id);
        CREATE INDEX IF NOT EXISTS idx_wf_citation_map_run ON wf_citation_map(fetch_run_id);
    """)
    conn.commit()


@pytest.fixture(autouse=True)
def _clean_table():
    """Clean wf_citation_map before each test."""
    conn = get_connection()
    conn.execute("DELETE FROM wf_citation_map")
    conn.commit()


@pytest.fixture
def engine():
    return CitationEngine()


@pytest.fixture
def sample_items():
    return [
        {"url": "http://example.com/article1", "title": "Article One",
         "source_id": "src-1", "content": "Content of article one",
         "fetch_run_id": "run-001"},
        {"url": "http://example.com/article2", "title": "Article Two",
         "source_id": "src-1", "content": "Content of article two",
         "fetch_run_id": "run-001"},
        {"url": "http://other.com/article3", "title": "Article Three",
         "source_id": "src-2", "content": "Content of article three",
         "fetch_run_id": "run-001"},
    ]


# ── Tests ────────────────────────────────────────────────────────────


class TestCitationEngine:
    def test_generate_ids(self, engine, sample_items):
        """Assign IDs to 3 items."""
        result = engine.generate_ids(sample_items, prefix="S")
        assert len(result) == 3
        assert result[0]["citation_id"] == "S001"
        assert result[1]["citation_id"] == "S002"
        assert result[2]["citation_id"] == "S003"
        # Check items have citation_id added
        for item in result:
            assert "citation_id" in item
        # Check persisted
        rows = engine.get_map()
        assert len(rows) == 3

    def test_get_map(self, engine, sample_items):
        """Retrieve full map."""
        engine.generate_ids(sample_items, prefix="S")
        cmap = engine.get_map()
        assert len(cmap) == 3
        assert all("citation_id" in r for r in cmap)
        assert all("url" in r for r in cmap)

    def test_resolve_id(self, engine, sample_items):
        """Resolve S001."""
        engine.generate_ids(sample_items, prefix="S")
        entry = engine.resolve("S001")
        assert entry is not None
        assert entry["citation_id"] == "S001"
        assert "http://example.com/article1" in entry["url"]

    def test_resolve_nonexistent(self, engine):
        """Resolve a citation that doesn't exist."""
        entry = engine.resolve("S999")
        assert entry is None

    def test_export_map(self, engine, sample_items):
        """Export as dict {S001: {url, title}}."""
        engine.generate_ids(sample_items, prefix="S")
        exported = engine.export_map("run-001")
        assert len(exported) == 3
        assert "S001" in exported
        assert exported["S001"]["url"] == "http://example.com/article1"
        assert exported["S001"]["title"] == "Article One"

    def test_get_next_number(self, engine, sample_items):
        """Find next available number."""
        assert engine.get_next_number("S") == 1
        engine.generate_ids(sample_items, prefix="S")
        assert engine.get_next_number("S") == 4

    def test_get_map_filtered_by_run(self, engine, sample_items):
        """Get citation map filtered by fetch_run_id."""
        engine.generate_ids(sample_items[:2], prefix="S")
        # Add items with different run_id
        other_items = [
            {"url": "http://other.com/article4", "title": "Article Four",
             "source_id": "src-3", "content": "Other content",
             "fetch_run_id": "run-002"},
        ]
        engine.generate_ids(other_items, prefix="S")
        filtered = engine.get_map(fetch_run_id="run-002")
        assert len(filtered) == 1
        assert filtered[0]["citation_id"] == "S003"


class TestCitationResolver:
    def test_resolve_text(self):
        """Replace [S001] in text with hyperlinked HTML."""
        cmap = {"S001": {"url": "http://example.com/a", "title": "A"}}
        text = "According to [S001], this is important."
        result = CitationResolver.resolve_text(text, cmap)
        assert '<a href="http://example.com/a">[S001]</a>' in result

    def test_resolve_multiple_citations(self):
        """Replace multiple citation markers."""
        cmap = {
            "S001": {"url": "http://example.com/a", "title": "A"},
            "S002": {"url": "http://example.com/b", "title": "B"},
        }
        text = "Studies [S001] and [S002] show..."
        result = CitationResolver.resolve_text(text, cmap)
        assert '<a href="http://example.com/a">[S001]</a>' in result
        assert '<a href="http://example.com/b">[S002]</a>' in result

    def test_resolve_unknown_citation(self):
        """Unknown citation marker is left unchanged."""
        cmap = {"S001": {"url": "http://example.com/a", "title": "A"}}
        text = "[S999] is unknown."
        result = CitationResolver.resolve_text(text, cmap)
        assert "[S999]" in result

    def test_verify_all_exist(self):
        """Verify all citations exist."""
        cmap = {"S001": {"url": "http://a.com", "title": "A"},
                "S002": {"url": "http://b.com", "title": "B"}}
        text = "As seen in [S001] and [S002]."
        result = CitationResolver.verify_citations(text, cmap)
        assert result["valid"] is True
        assert result["missing_ids"] == []
        assert len(result["found_ids"]) == 2

    def test_missing_citation(self):
        """Detect missing reference."""
        cmap = {"S001": {"url": "http://a.com", "title": "A"}}
        text = "See [S001] and [S999]."
        result = CitationResolver.verify_citations(text, cmap)
        assert result["valid"] is False
        assert "S999" in result["missing_ids"]


class TestCitationMap:
    def test_build_map(self):
        """Build {citation_id: {url, title}} from items."""
        items = [
            {"citation_id": "S001", "url": "http://a.com", "title": "A"},
            {"citation_id": "S002", "url": "http://b.com", "title": "B"},
        ]
        cmap = CitationMap.build_map(items)
        assert len(cmap) == 2
        assert cmap["S001"]["url"] == "http://a.com"
        assert cmap["S002"]["title"] == "B"

    def test_merge_maps(self):
        """Merge multiple citation maps."""
        map1 = {"S001": {"url": "http://a.com", "title": "A"}}
        map2 = {"S002": {"url": "http://b.com", "title": "B"}}
        merged = CitationMap.merge_maps(map1, map2)
        assert len(merged) == 2
        assert merged["S001"]["url"] == "http://a.com"
        assert merged["S002"]["url"] == "http://b.com"

    def test_merge_overrides(self):
        """Later maps override earlier ones on key collision."""
        map1 = {"S001": {"url": "http://old.com", "title": "Old"}}
        map2 = {"S001": {"url": "http://new.com", "title": "New"}}
        merged = CitationMap.merge_maps(map1, map2)
        assert merged["S001"]["url"] == "http://new.com"

    def test_format_for_prompt(self):
        """LLM-friendly format: S001: url "Title"."""
        cmap = {
            "S001": {"url": "http://a.com", "title": "Article A"},
            "S002": {"url": "http://b.com", "title": "Article B"},
        }
        formatted = CitationMap.format_for_prompt(cmap)
        assert 'S001: http://a.com "Article A"' in formatted
        assert 'S002: http://b.com "Article B"' in formatted
        # Check sorted order
        lines = formatted.split("\n")
        assert lines[0].startswith("S001")
        assert lines[1].startswith("S002")

    def test_format_for_prompt_no_title(self):
        """Format handles items without title."""
        cmap = {"S001": {"url": "http://a.com", "title": ""}}
        formatted = CitationMap.format_for_prompt(cmap)
        assert "S001: http://a.com" in formatted


class TestCitationStepType:
    """Test executor routing via the citation step types (integration)."""

    def test_citation_step_type_routing(self):
        """Verify citation step types are registered in executor."""
        from workflow_executor import WorkflowExecutor
        executor = WorkflowExecutor()
        # Check that the handler methods exist
        assert hasattr(executor, "_execute_assign_citations_step")
        assert hasattr(executor, "_execute_resolve_citations_step")
        assert hasattr(executor, "_execute_export_citation_map_step")
