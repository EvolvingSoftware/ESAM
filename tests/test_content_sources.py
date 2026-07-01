"""Tests for P0-3: Content Source Definition Schema + wf_content_sources.

Tests the ContentSourceManager class and the integrated fetch-and-parse
pipeline step types.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure src is on the path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from source_registry import ContentSourceManager

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_db():
    """Ensure we have a clean database for each test.

    Drops the content source tables before each test so tests are
    independent.  The ContentSourceManager re-creates them on init.
    """
    from database import get_connection
    conn = get_connection()
    conn.execute("DROP TABLE IF EXISTS wf_content_source_items")
    conn.execute("DROP TABLE IF EXISTS wf_content_sources")
    conn.commit()
    yield
    conn.execute("DROP TABLE IF EXISTS wf_content_source_items")
    conn.execute("DROP TABLE IF EXISTS wf_content_sources")
    conn.commit()


@pytest.fixture
def csm():
    return ContentSourceManager()


@pytest.fixture
def sample_yaml_def():
    return {
        "name": "Hacker News (Top)",
        "type": "http_api",
        "url": "https://hacker-news.firebaseio.com/v0/topstories.json",
        "response_type": "json_array",
        "item_parser": {
            "extract": "ids",
            "items_url": "https://hacker-news.firebaseio.com/v0/item/{id}.json",
        },
        "fields": {
            "url": "url",
            "title": "title",
            "score": "score",
            "author": "by",
            "created_at": "time",
        },
        "authority_tier": "A",
        "interval_minutes": 30,
        "rate_limit_per_minute": 60,
        "rate_limit_burst": 10,
    }


@pytest.fixture
def reddit_yaml_def():
    return {
        "name": "Reddit Hot Posts",
        "type": "http_api",
        "url_template": "https://www.reddit.com/r/{subreddit}/hot.json",
        "url_params": {"subreddit": ["OpenAI", "ClaudeAI", "Anthropic"]},
        "response_type": "json_path",
        "response_path": "$.data.children[*].data",
        "fields": {
            "url": "url",
            "title": "title",
            "score": "score",
            "author": "author",
            "subreddit": "subreddit",
        },
        "headers": {"User-Agent": "esam-test/1.0"},
        "authority_tier": "B",
        "interval_minutes": 30,
    }


@pytest.fixture
def rss_yaml_def():
    return {
        "name": "Simon Willison's Blog",
        "type": "rss",
        "url": "https://simonwillison.net/atom/everything/",
        "response_type": "rss",
        "fields": {
            "url": "link",
            "title": "title",
            "content": "summary",
            "author": "author",
            "created_at": "published",
        },
        "authority_tier": "A",
        "interval_minutes": 60,
    }


# ── Tests ────────────────────────────────────────────────────────────


class TestContentSourceManager:
    """Tests for ContentSourceManager CRUD operations."""

    def test_create_from_yaml(self, csm, sample_yaml_def):
        """Create a content source from a YAML definition."""
        source = csm.create_from_yaml(sample_yaml_def)

        assert source["name"] == "Hacker News (Top)"
        assert source["type"] == "http_api"
        assert source["authority_tier"] == "A"
        assert source["interval_minutes"] == 30
        assert source["rate_limit_per_minute"] == 60
        assert source["rate_limit_burst"] == 10
        assert source["enabled"] == 1
        assert source.get("id")

        # Check source_config_json was stored correctly
        config = json.loads(source["source_config_json"])
        assert config["url"] == "https://hacker-news.firebaseio.com/v0/topstories.json"
        assert config["response_type"] == "json_array"
        assert config["fields"]["title"] == "title"
        assert "name" not in config  # top-level keys should not be in config

    def test_create_from_yaml_requires_name(self, csm):
        """Creating without a name raises ValueError."""
        with pytest.raises(ValueError, match="name"):
            csm.create_from_yaml({"type": "rss"})

    def test_create_from_yaml_validates_type(self, csm):
        """Creating with invalid type raises ValueError."""
        with pytest.raises(ValueError, match="type"):
            csm.create_from_yaml({"name": "Test", "type": "invalid"})

    def test_create_from_yaml_validates_tier(self, csm):
        """Creating with invalid authority_tier raises ValueError."""
        with pytest.raises(ValueError, match="authority_tier"):
            csm.create_from_yaml({"name": "Test", "type": "rss", "authority_tier": "D"})

    def test_list_sources(self, csm, sample_yaml_def, reddit_yaml_def, rss_yaml_def):
        """List returns created sources."""
        csm.create_from_yaml(sample_yaml_def)
        csm.create_from_yaml(reddit_yaml_def)
        csm.create_from_yaml(rss_yaml_def)

        sources = csm.list()
        assert len(sources) == 3

        names = [s["name"] for s in sources]
        assert "Hacker News (Top)" in names
        assert "Reddit Hot Posts" in names
        assert "Simon Willison's Blog" in names

    def test_get_source(self, csm, sample_yaml_def):
        """Get returns a single source by ID."""
        source = csm.create_from_yaml(sample_yaml_def)
        retrieved = csm.get(source["id"])
        assert retrieved is not None
        assert retrieved["name"] == "Hacker News (Top)"
        assert retrieved["id"] == source["id"]

    def test_get_source_not_found(self, csm):
        """Get returns None for non-existent ID."""
        assert csm.get("non-existent-id") is None

    def test_update_source(self, csm, sample_yaml_def):
        """Update a content source."""
        source = csm.create_from_yaml(sample_yaml_def)

        updated = csm.update(source["id"], {
            "name": "HN Top Stories",
            "interval_minutes": 15,
            "authority_tier": "B",
        })

        assert updated["name"] == "HN Top Stories"
        assert updated["interval_minutes"] == 15
        assert updated["authority_tier"] == "B"
        assert updated["id"] == source["id"]

    def test_delete_source(self, csm, sample_yaml_def):
        """Delete a content source."""
        source = csm.create_from_yaml(sample_yaml_def)
        assert csm.get(source["id"]) is not None

        deleted = csm.delete(source["id"])
        assert deleted is True
        assert csm.get(source["id"]) is None

    def test_delete_source_not_found(self, csm):
        """Delete returns False for non-existent ID."""
        assert csm.delete("non-existent") is False

    def test_create_rss_source(self, csm, rss_yaml_def):
        """Create an RSS-type content source."""
        source = csm.create_from_yaml(rss_yaml_def)
        assert source["type"] == "rss"
        assert source["authority_tier"] == "A"
        assert source["interval_minutes"] == 60

    def test_create_with_url_params(self, csm, reddit_yaml_def):
        """Create a source with url_template and url_params."""
        source = csm.create_from_yaml(reddit_yaml_def)
        config = json.loads(source["source_config_json"])
        assert "url_template" in config
        assert "url_params" in config
        assert config["url_params"]["subreddit"] == ["OpenAI", "ClaudeAI", "Anthropic"]


class TestContentSourceFetchAndParse:
    """Tests for the fetch_and_parse pipeline."""

    def test_build_urls_no_params(self, csm):
        """_build_urls with a simple URL returns a single URL."""
        config = {"url": "https://example.com/feed.xml"}
        source = {"type": "rss"}
        urls = csm._build_urls(config, source)
        assert urls == ["https://example.com/feed.xml"]

    def test_build_urls_with_params(self, csm, reddit_yaml_def):
        """_build_urls with url_params expands to multiple URLs."""
        # Create source first
        source = csm.create_from_yaml(reddit_yaml_def)
        config = json.loads(source["source_config_json"])
        urls = csm._build_urls(config, source)
        assert len(urls) == 3
        assert "OpenAI" in urls[0]
        assert "ClaudeAI" in urls[1]
        assert "Anthropic" in urls[2]

    def test_build_urls_empty_config(self, csm):
        """_build_urls returns empty list for missing URL."""
        assert csm._build_urls({}, {"type": "rss"}) == []

    def test_build_parser_config_rss(self, csm):
        """_build_parser_config for RSS type."""
        config = {"fields": {"url": "link", "title": "title"}}
        pc = csm._build_parser_config(config, "rss")
        assert pc["type"] == "rss"
        assert pc["config"]["field_map"] == {"url": "link", "title": "title"}

    def test_build_parser_config_json_path(self, csm):
        """_build_parser_config for json_path response type."""
        config = {
            "response_type": "json_path",
            "response_path": "$.items[*]",
            "fields": {"url": "url"},
        }
        pc = csm._build_parser_config(config, "http_api")
        assert pc["type"] == "jsonpath"
        assert pc["config"]["path"] == "$.items[*]"

    def test_build_parser_config_json_array(self, csm):
        """_build_parser_config for json_array response type."""
        config = {"response_type": "json_array", "fields": {"url": "url"}}
        pc = csm._build_parser_config(config, "http_api")
        assert pc["type"] == "jsonpath"
        assert pc["config"]["path"] == "$"

    def test_build_parser_config_xpath(self, csm):
        """_build_parser_config for xpath response type."""
        config = {
            "response_type": "xpath",
            "response_path": "//entry",
            "fields": {"url": "id/text()"},
        }
        pc = csm._build_parser_config(config, "http_xml")
        assert pc["type"] == "xpath"
        assert pc["config"]["path"] == "//entry"

    def test_build_parser_config_html_selector(self, csm):
        """_build_parser_config for html_selector response type."""
        config = {
            "response_type": "html_selector",
            "response_path": "article.story",
            "fields": {"url": "a@href"},
        }
        pc = csm._build_parser_config(config, "http_html")
        assert pc["type"] == "html"
        assert pc["config"]["selector"] == "article.story"

    def test_build_parser_config_id_list(self, csm):
        """_build_parser_config for id_list response type."""
        config = {
            "response_type": "id_list",
            "ids": ["1", "2", "3"],
            "items_url": "https://example.com/item/{id}.json",
        }
        pc = csm._build_parser_config(config, "http_api")
        assert pc["type"] == "id_list"
        assert pc["config"]["ids"] == ["1", "2", "3"]

    def test_fetch_and_parse_mock(self, csm, sample_yaml_def):
        """Test fetch_and_parse with mocked fetcher and parser engines."""
        source = csm.create_from_yaml(sample_yaml_def)

        # Create mock FetcherEngine
        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = {
            "status_code": 200,
            "body_text": '{"items": [{"url": "http://example.com/1", "title": "Story 1"}]}',
            "error": None,
        }

        # Create mock ParserEngine
        mock_parser = MagicMock()
        mock_parser.parse.return_value = {
            "items": [
                {
                    "url": "http://example.com/1",
                    "title": "Story 1",
                    "content": "Content 1",
                    "author": "Author 1",
                    "published_date": "2025-01-01",
                }
            ],
            "errors": [],
        }

        items = csm.fetch_and_parse(
            source_id=source["id"],
            fetcher_engine=mock_fetcher,
            parser_engine=mock_parser,
        )

        assert len(items) == 1
        assert items[0]["title"] == "Story 1"
        assert items[0]["url"] == "http://example.com/1"
        assert items[0]["author"] == "Author 1"

        # Verify the source's last_fetched was updated
        updated = csm.get(source["id"])
        assert updated["last_fetched"] is not None

        # Verify item was stored in DB
        stored = csm.get_items(source_id=source["id"])
        assert len(stored) == 1

    def test_fetch_and_parse_disabled_source(self, csm, sample_yaml_def):
        """fetch_and_parse returns empty for a disabled source."""
        source = csm.create_from_yaml(sample_yaml_def)

        # Disable the source
        from database import get_connection
        conn = get_connection()
        conn.execute("UPDATE wf_content_sources SET enabled = 0 WHERE id = ?", (source["id"],))
        conn.commit()

        items = csm.fetch_and_parse(
            source_id=source["id"],
            fetcher_engine=MagicMock(),
            parser_engine=MagicMock(),
        )
        assert items == []

    def test_fetch_and_parse_nonexistent_source(self, csm):
        """fetch_and_parse raises ValueError for non-existent source."""
        with pytest.raises(ValueError, match="not found"):
            csm.fetch_and_parse("nonexistent")


class TestContentSourceBulkImport:
    """Tests for bulk import functionality."""

    def test_bulk_import_creates_new(self, csm, sample_yaml_def, reddit_yaml_def):
        """bulk_import creates new sources."""
        results = csm.bulk_import([sample_yaml_def, reddit_yaml_def])
        assert len(results) == 2
        assert results[0]["action"] == "created"
        assert results[1]["action"] == "created"

        sources = csm.list()
        assert len(sources) == 2

    def test_bulk_import_updates_existing(self, csm, sample_yaml_def):
        """bulk_import updates existing sources by name."""
        csm.create_from_yaml(sample_yaml_def)
        results = csm.bulk_import([
            {"name": "Hacker News (Top)", "interval_minutes": 10}
        ])
        assert results[0]["action"] == "updated"

        source = csm.get(csm.list()[0]["id"])
        assert source["interval_minutes"] == 10

    def test_bulk_import_skips_no_name(self, csm):
        """bulk_import skips entries without a name."""
        results = csm.bulk_import([{"type": "rss"}])
        assert results[0]["action"] == "skipped"


class TestContentSourceYAMLExport:
    """Tests for YAML export functionality."""

    def test_to_yaml_dict(self, csm, sample_yaml_def):
        """to_yaml_dict returns a YAML-friendly dict."""
        source = csm.create_from_yaml(sample_yaml_def)
        yaml_dict = csm.to_yaml_dict(source["id"])

        assert yaml_dict["name"] == "Hacker News (Top)"
        assert yaml_dict["type"] == "http_api"
        assert yaml_dict["authority_tier"] == "A"
        assert yaml_dict["interval_minutes"] == 30
        assert yaml_dict["url"] == sample_yaml_def["url"]
        assert yaml_dict["response_type"] == "json_array"
        assert yaml_dict["rate_limit_per_minute"] == 60

    def test_to_yaml_dict_not_found(self, csm):
        """to_yaml_dict raises for non-existent source."""
        with pytest.raises(ValueError, match="not found"):
            csm.to_yaml_dict("nonexistent")


class TestContentSourceItems:
    """Tests for content source item storage and retrieval."""

    def test_get_items(self, csm, sample_yaml_def):
        """get_items returns stored items."""
        source = csm.create_from_yaml(sample_yaml_def)

        # Manually insert an item
        from database import get_connection
        from source_registry import _new_id, _now
        conn = get_connection()
        conn.execute(
            """INSERT INTO wf_content_source_items
               (id, source_id, fetch_run_id, url, title, author,
                body_raw, published_date, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_new_id(), source["id"], "run-1", "https://example.com/1",
             "Test Item", "Test Author", "Body text", "2025-01-01", _now()),
        )
        conn.commit()

        items = csm.get_items(source_id=source["id"])
        assert len(items) == 1
        assert items[0]["title"] == "Test Item"

    def test_delete_items(self, csm, sample_yaml_def):
        """delete_items removes items for a source."""
        source = csm.create_from_yaml(sample_yaml_def)

        # Insert an item
        from database import get_connection
        from source_registry import _new_id, _now
        conn = get_connection()
        conn.execute(
            """INSERT INTO wf_content_source_items
               (id, source_id, fetch_run_id, url, title, author,
                body_raw, published_date, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_new_id(), source["id"], "run-1", "https://example.com/1",
             "Test Item", "Test Author", "Body text", "2025-01-01", _now()),
        )
        conn.commit()

        assert len(csm.get_items(source_id=source["id"])) == 1
        deleted = csm.delete_items(source_id=source["id"])
        assert deleted == 1
        assert len(csm.get_items(source_id=source["id"])) == 0

    def test_cascade_delete_on_source_delete(self, csm, sample_yaml_def):
        """Deleting a source cascades to delete its items."""
        source = csm.create_from_yaml(sample_yaml_def)

        # Insert an item
        from database import get_connection
        from source_registry import _new_id, _now
        conn = get_connection()
        conn.execute(
            """INSERT INTO wf_content_source_items
               (id, source_id, fetch_run_id, url, title, author,
                body_raw, published_date, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_new_id(), source["id"], "run-1", "https://example.com/1",
             "Test Item", "Test Author", "Body text", "2025-01-01", _now()),
        )
        conn.commit()

        csm.delete(source["id"])
        assert len(csm.get_items(source_id=source["id"])) == 0


class TestContentSourceStepType:
    """Tests for executor step type routing with content sources."""

    def test_content_source_step_type_in_valid_set(self):
        """content_source step types are recognized as valid."""
        from workflow_loader import VALID_STEP_TYPES
        assert "content_source" in VALID_STEP_TYPES
        assert "fetch_source" in VALID_STEP_TYPES
        assert "parse_source" in VALID_STEP_TYPES
        assert "fetch_and_parse" in VALID_STEP_TYPES

    def test_validate_workflow_with_content_source_step(self):
        """Valid workflows can include content source step types."""
        from workflow_loader import validate_workflow

        data = {
            "name": "Test Content Acquisition",
            "steps": [
                {"id": "step-fetch", "label": "Fetch HN", "type": "fetch_source"},
                {"id": "step-parse", "label": "Parse HN", "type": "parse_source"},
                {"id": "step-pipe", "label": "Fetch & Parse", "type": "fetch_and_parse"},
            ],
        }
        result = validate_workflow(data)
        assert result["valid"] is True, f"Validation errors: {result['errors']}"

    def test_executor_routes_fetch_source(self):
        """WorkflowExecutor routes fetch_source to correct handler."""
        from workflow_executor import WorkflowExecutor

        executor = WorkflowExecutor()

        # Test that the routing table has entries for fetch_source, parse_source, fetch_and_parse
        step_types_in_executor = {
            "fetch_source": "_execute_fetch_source_step",
            "parse_source": "_execute_parse_source_step",
            "fetch_and_parse": "_execute_fetch_and_parse_step",
        }

        for step_type, method_name in step_types_in_executor.items():
            assert hasattr(executor, method_name), (
                f"WorkflowExecutor missing method {method_name} for step type {step_type}"
            )


class TestContentSourceFromYAMLFile:
    """Tests for importing content sources from YAML files."""

    def test_import_from_yaml_file(self, csm, sample_yaml_def, reddit_yaml_def):
        """Import sources from a YAML file via bulk_import."""
        # Create a temp YAML file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("""
content_sources:
  - name: "Hacker News (Top)"
    type: http_api
    url: "https://hacker-news.firebaseio.com/v0/topstories.json"
    response_type: json_array
    fields:
      url: url
      title: title
      author: by
    authority_tier: A
    interval_minutes: 30

  - name: "Reddit Hot Posts"
    type: http_api
    url_template: "https://www.reddit.com/r/{subreddit}/hot.json"
    url_params:
      subreddit: ["OpenAI", "ClaudeAI"]
    response_type: json_path
    response_path: "$.data.children[*].data"
    fields:
      url: url
      title: title
      author: author
    authority_tier: B
    interval_minutes: 30
""")
            temp_path = f.name

        try:
            import yaml
            with open(temp_path) as f:
                data = yaml.safe_load(f)
            sources_defs = data.get("content_sources", [])

            results = csm.bulk_import(sources_defs)
            assert len(results) == 2
            assert results[0]["action"] == "created"
            assert results[1]["action"] == "created"

            sources = csm.list()
            assert len(sources) == 2
        finally:
            os.unlink(temp_path)

    def test_sync_sources_from_yaml_with_content_sources(self):
        """sync_sources_from_yaml handles content_sources section."""
        from workflow_loader import sync_sources_from_yaml

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("""
name: Test Workflow
steps:
  - id: step-1
    label: Fetch Sources
    type: fetch_and_parse

content_sources:
  - name: "Test Source"
    type: rss
    url: "https://example.com/feed.xml"
    response_type: rss
    fields:
      url: link
      title: title
    authority_tier: C
    interval_minutes: 1440
""")
            temp_path = f.name

        try:
            results = sync_sources_from_yaml(temp_path)
            # Should have synced the content source
            content_results = [r for r in results if r.get("type") == "content_source"]
            assert len(content_results) >= 1
            assert content_results[0]["action"] in ("created", "updated")
        finally:
            os.unlink(temp_path)
