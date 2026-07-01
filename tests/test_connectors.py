"""Tests for the Connector SDK (P0)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add src to path
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture(autouse=True)
def _import_all_connectors():
    """Import all connectors so they self-register in the registry."""
    # Force import of all known connector modules
    import connectors.registry  # noqa: F811
    import connectors.reddit  # noqa: F401
    import connectors.hackernews  # noqa: F401
    import connectors.slack  # noqa: F401
    import connectors.notion  # noqa: F401
    import connectors.github  # noqa: F401
    import connectors.gmail  # noqa: F401
    import connectors.twitter_x  # noqa: F401
    import connectors.airtable  # noqa: F401
    import connectors.google_sheets  # noqa: F401
    import connectors.jira  # noqa: F401


# ======================================================================
# Test: Base Connector
# ======================================================================


def test_base_connector():
    """ConnectorBase registers and validates."""
    from connectors.base import ConnectorBase
    from connectors.registry import ConnectorRegistry

    # Verify base is abstract
    with pytest.raises(TypeError):
        ConnectorBase()  # type: ignore

    # Verify Reddit is a proper subclass
    Reddit = ConnectorRegistry.get("reddit")
    assert Reddit is not None
    assert issubclass(Reddit, ConnectorBase)
    assert Reddit.name == "reddit"

    # Test validate_config
    connector = Reddit({"subreddit": "python"})
    assert connector.validate_config() is True

    connector_bad = Reddit({})
    assert connector_bad.validate_config() is False


# ======================================================================
# Test: Registry
# ======================================================================


def test_registry_list():
    """ConnectorRegistry lists 10 connectors."""
    from connectors import list_connectors

    names = list_connectors()
    assert len(names) == 10, f"Expected 10 connectors, got {len(names)}: {names}"

    expected = {
        "reddit", "hackernews", "slack", "notion", "github",
        "gmail", "twitter_x", "airtable", "google_sheets", "jira",
    }
    assert set(names) == expected, f"Missing: {expected - set(names)}; Extra: {set(names) - expected}"


# ======================================================================
# Test: Reddit fetch (mocked)
# ======================================================================


def test_reddit_fetch():
    """Mock fetch, verify output structure."""
    from connectors.registry import ConnectorRegistry

    Reddit = ConnectorRegistry.get("reddit")
    assert Reddit is not None

    fake_response = {
        "data": {
            "children": [
                {
                    "data": {
                        "title": "Test Post",
                        "permalink": "/r/test/comments/123/test_post/",
                        "score": 42,
                        "num_comments": 7,
                        "author": "testuser",
                        "subreddit": "test",
                        "created_utc": 1715000000,
                    }
                },
                {
                    "data": {
                        "title": "Second Post",
                        "permalink": "/r/test/comments/456/second_post/",
                        "score": 99,
                        "num_comments": 15,
                        "author": "anotheruser",
                        "subreddit": "test",
                        "created_utc": 1715000100,
                    }
                },
            ]
        }
    }

    with patch.object(Reddit, "_fetch_json", return_value=fake_response):
        connector = Reddit({"subreddit": "test", "limit": 2})
        results = connector.fetch()

    assert len(results) == 2
    assert results[0]["title"] == "Test Post"
    assert results[0]["url"] == "https://www.reddit.com/r/test/comments/123/test_post/"
    assert results[0]["score"] == 42
    assert results[0]["comments"] == 7
    assert results[0]["author"] == "testuser"
    assert results[0]["subreddit"] == "test"
    assert results[0]["created_utc"] == 1715000000

    assert results[1]["title"] == "Second Post"
    assert results[1]["score"] == 99


# ======================================================================
# Test: HackerNews fetch (mocked)
# ======================================================================


def test_hackernews_fetch():
    """Mock fetch, verify ID resolution pattern."""
    from connectors.registry import ConnectorRegistry

    HN = ConnectorRegistry.get("hackernews")
    assert HN is not None

    fake_ids = [1001, 1002, 1003]
    fake_items = {
        1001: {"title": "Story One", "url": "https://example.com/1", "score": 50, "descendants": 10, "by": "alice", "time": 1715000000},
        1002: {"title": "Story Two", "score": 30, "descendants": 5, "by": "bob", "time": 1715000100},
        1003: {"title": "Story Three", "url": "https://example.com/3", "score": 75, "descendants": 20, "by": "charlie", "time": 1715000200},
    }

    def mock_fetch_json(url: str):
        if "topstories" in url:
            return fake_ids
        # item lookup: extract ID from URL
        for sid, item in fake_items.items():
            if str(sid) in url:
                return item
        return {}

    with patch.object(HN, "_fetch_json", side_effect=mock_fetch_json):
        connector = HN({"limit": 3})
        results = connector.fetch()

    assert len(results) == 3
    assert results[0]["title"] == "Story One"
    assert results[0]["url"] == "https://example.com/1"
    assert results[0]["score"] == 50
    assert results[0]["by"] == "alice"

    assert results[1]["title"] == "Story Two"
    assert "news.ycombinator.com" in results[1]["url"]  # no URL -> HN fallback

    assert results[2]["title"] == "Story Three"
    assert results[2]["score"] == 75


# ======================================================================
# Test: All connectors importable
# ======================================================================


def test_all_connectors_importable():
    """All 10 connectors import cleanly."""
    from connectors import list_connectors
    from connectors.registry import ConnectorRegistry

    names = list_connectors()
    for name in names:
        cls = ConnectorRegistry.get(name)
        assert cls is not None, f"Connector '{name}' not found in registry"
        # Instantiate with valid config
        config_data = {}
        for field in cls.config_fields:
            if field.get("required"):
                config_data[field["name"]] = f"test_{field['name']}"
        instance = cls(config_data)
        assert instance.name == name
        assert hasattr(instance, "fetch")
        assert callable(instance.fetch)


# ======================================================================
# Test: Connector manifest valid
# ======================================================================


def test_connector_manifest_valid():
    """Manifest YAML parses."""
    manifest_path = Path(__file__).resolve().parent.parent / "connectors" / "connector-manifest.yaml"
    assert manifest_path.exists(), f"Manifest not found at {manifest_path}"

    import yaml  # type: ignore[import-untyped]
    with open(manifest_path, "r") as f:
        data = yaml.safe_load(f)

    assert data is not None, "Manifest is empty"
    assert "connectors" in data, "Manifest missing 'connectors' key"
    connectors = data["connectors"]
    assert isinstance(connectors, list), "'connectors' must be a list"
    assert len(connectors) == 10, f"Expected 10 connectors in manifest, got {len(connectors)}"

    names = [c["name"] for c in connectors]
    expected = {
        "reddit", "hackernews", "slack", "notion", "github",
        "gmail", "twitter_x", "airtable", "google_sheets", "jira",
    }
    assert set(names) == expected, f"Missing: {expected - set(names)}; Extra: {set(names) - expected}"

    # Verify all connectors have required fields
    for c in connectors:
        assert "name" in c
        assert "description" in c
        assert "config_fields" in c
        assert "auth_required" in c
        assert "rate_limit" in c
