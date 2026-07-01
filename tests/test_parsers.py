"""Tests for the Response Parser Engine components."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# Ensure src is on the path
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from parser.engine import ParserEngine
from parser.rss import RSSParser
from parser.jsonpath import JSONPathParser
from parser.xpath import XPathParser
from parser.html_selector import HTMLParser
from parser.id_list_resolver import IDListResolver


# ── Test Data ────────────────────────────────────────────────────────

RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <link>https://example.com</link>
    <description>Test RSS feed</description>
    <item>
      <title>Item One</title>
      <link>https://example.com/1</link>
      <description>Description of item one</description>
      <author>Author A</author>
      <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
      <category>Tech</category>
      <guid>guid-1</guid>
    </item>
    <item>
      <title>Item Two</title>
      <link>https://example.com/2</link>
      <description>Description of item two</description>
      <author>Author B</author>
      <pubDate>Tue, 02 Jan 2024 00:00:00 GMT</pubDate>
      <category>Science</category>
      <guid>guid-2</guid>
    </item>
  </channel>
</rss>"""

ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Feed</title>
  <link href="https://example.com/atom"/>
  <entry>
    <title>Atom Entry One</title>
    <link href="https://example.com/atom/1" rel="alternate"/>
    <summary>Summary of atom entry one</summary>
    <author><name>Author A</name></author>
    <published>2024-01-01T00:00:00Z</published>
    <category term="Tech"/>
    <id>atom-guid-1</id>
  </entry>
  <entry>
    <title>Atom Entry Two</title>
    <link href="https://example.com/atom/2" rel="alternate"/>
    <summary>Summary of atom entry two</summary>
    <author><name>Author B</name></author>
    <published>2024-01-02T00:00:00Z</published>
    <category term="Science"/>
    <id>atom-guid-2</id>
  </entry>
</feed>"""

NESTED_JSON = {
    "data": {
        "children": [
            {"data": {"title": "Post 1", "url": "https://news.ycombinator.com/item?id=1", "author": "user1"}},
            {"data": {"title": "Post 2", "url": "https://news.ycombinator.com/item?id=2", "author": "user2"}},
            {"data": {"title": "Post 3", "url": "https://news.ycombinator.com/item?id=3", "author": "user3"}},
        ]
    }
}

XML_WITH_ITEMS = """<?xml version="1.0" encoding="UTF-8"?>
<root>
  <item>
    <title>XML Item 1</title>
    <link>https://example.com/xml/1</link>
    <summary>XML summary one</summary>
    <author>XML Author A</author>
    <pubDate>2024-01-01</pubDate>
  </item>
  <item>
    <title>XML Item 2</title>
    <link>https://example.com/xml/2</link>
    <summary>XML summary two</summary>
    <author>XML Author B</author>
    <pubDate>2024-01-02</pubDate>
  </item>
</root>"""

SIMPLE_HTML = """<!DOCTYPE html>
<html>
<head>
  <title>Test Page</title>
  <meta name="author" content="Test Author">
  <meta property="article:published_time" content="2024-01-01T12:00:00Z">
</head>
<body>
  <h1>Hello World</h1>
  <p>This is some content for testing.</p>
  <a href="https://example.com/page1">Link 1</a>
  <a href="https://example.com/page2">Link 2</a>
</body>
</html>"""


# ── RSSParser Tests ─────────────────────────────────────────────────


class TestRSSParser:
    def test_rss_parse(self) -> None:
        parser = RSSParser()
        items = parser.parse(RSS_XML)
        assert len(items) == 2

        assert items[0]["title"] == "Item One"
        assert items[0]["url"] == "https://example.com/1"
        assert items[0]["description"] == "Description of item one"
        assert items[0]["author"] == "Author A"
        assert items[0]["pubDate"] == "Mon, 01 Jan 2024 00:00:00 GMT"
        assert items[0]["category"] == "Tech"
        assert items[0]["guid"] == "guid-1"

        assert items[1]["title"] == "Item Two"
        assert items[1]["url"] == "https://example.com/2"
        assert items[1]["category"] == "Science"

    def test_atom_parse(self) -> None:
        parser = RSSParser()
        items = parser.parse(ATOM_XML)
        assert len(items) == 2

        assert items[0]["title"] == "Atom Entry One"
        assert items[0]["url"] == "https://example.com/atom/1"
        assert items[0]["summary"] == "Summary of atom entry one"
        assert items[0]["author"] == "Author A"
        assert items[0]["published_date"] == "2024-01-01T00:00:00Z"
        assert items[0]["category"] == "Tech"
        assert items[0]["guid"] == "atom-guid-1"

        assert items[1]["title"] == "Atom Entry Two"
        assert items[1]["url"] == "https://example.com/atom/2"

    def test_empty_xml(self) -> None:
        parser = RSSParser()
        items = parser.parse("")
        assert items == []

    def test_invalid_xml(self) -> None:
        parser = RSSParser()
        items = parser.parse("<not><valid>xml")
        assert items == []


# ── JSONPathParser Tests ─────────────────────────────────────────────


class TestJSONPathParser:
    def test_jsonpath_basic(self) -> None:
        parser = JSONPathParser()
        items = parser.parse(NESTED_JSON, "$.data.children")
        assert len(items) == 1
        # children is a list wrapped in a single result
        children = items[0]
        assert isinstance(children, list)
        assert len(children) == 3

    def test_jsonpath_wildcard(self) -> None:
        parser = JSONPathParser()
        items = parser.parse(NESTED_JSON, "$.data.children[*].data")
        assert len(items) == 3
        assert items[0]["title"] == "Post 1"
        assert items[1]["title"] == "Post 2"
        assert items[2]["title"] == "Post 3"

    def test_jsonpath_root(self) -> None:
        parser = JSONPathParser()
        items = parser.parse(NESTED_JSON, "$")
        assert len(items) == 1
        assert items[0] == NESTED_JSON

    def test_jsonpath_simple_field(self) -> None:
        parser = JSONPathParser()
        items = parser.parse({"name": "test", "value": 42}, "$.name")
        assert len(items) == 1
        assert items[0] == "test"

    def test_jsonpath_empty(self) -> None:
        parser = JSONPathParser()
        items = parser.parse({}, "$.missing.path")
        assert items == []

    def test_jsonpath_list_input(self) -> None:
        parser = JSONPathParser()
        data = [{"id": 1}, {"id": 2}]
        items = parser.parse(data, "$[*]")
        assert len(items) == 2
        assert items[0]["id"] == 1
        assert items[1]["id"] == 2


# ── XPathParser Tests ────────────────────────────────────────────────


class TestXPathParser:
    def test_xpath_basic(self) -> None:
        parser = XPathParser()
        field_map = {
            "url": "link/text()",
            "title": "title/text()",
            "content": "summary/text()",
            "author": "author/text()",
            "published_date": "pubDate/text()",
        }
        items = parser.parse(XML_WITH_ITEMS, ".//item", field_map)
        assert len(items) == 2
        assert items[0]["title"] == "XML Item 1"
        assert items[0]["url"] == "https://example.com/xml/1"
        assert items[0]["content"] == "XML summary one"
        assert items[0]["author"] == "XML Author A"
        assert items[0]["published_date"] == "2024-01-01"

    def test_xpath_empty(self) -> None:
        parser = XPathParser()
        items = parser.parse("", ".//item", {})
        assert items == []

    def test_xpath_no_match(self) -> None:
        parser = XPathParser()
        items = parser.parse("<root><foo/></root>", ".//item", {"url": "link/text()"})
        assert items == []


# ── HTMLParser Tests ─────────────────────────────────────────────────


class TestHTMLParser:
    def test_html_basic(self) -> None:
        parser = HTMLParser()
        items = parser.parse(SIMPLE_HTML, "body")
        assert len(items) == 1
        item = items[0]
        assert item["title"] == "Test Page"
        assert "content" in item
        assert item["author"] == "Test Author"
        assert item["published_date"] == "2024-01-01T12:00:00Z"
        assert "example.com" in item["url"]

    def test_html_empty(self) -> None:
        parser = HTMLParser()
        items = parser.parse("", "body")
        assert items == []


# ── IDListResolver Tests ─────────────────────────────────────────────


class TestIDListResolver:
    def test_id_list_resolver(self) -> None:
        resolver = IDListResolver()

        # Create a mock fetcher
        mock_fetcher = MagicMock()

        def mock_fetch(url: str, **kwargs: Any) -> dict:
            if "123" in url:
                return {
                    "status_code": 200,
                    "body_text": json.dumps({
                        "title": "Item 123",
                        "url": "https://example.com/123",
                        "author": "Author 123",
                    }),
                    "error": None,
                }
            elif "456" in url:
                return {
                    "status_code": 200,
                    "body_text": json.dumps({
                        "title": "Item 456",
                        "url": "https://example.com/456",
                        "author": "Author 456",
                    }),
                    "error": None,
                }
            return {"status_code": 404, "body_text": "", "error": "not_found"}

        mock_fetcher.fetch = MagicMock(side_effect=mock_fetch)

        items = resolver.resolve(
            item_ids=["123", "456"],
            items_url_template="https://example.com/api/items/{id}",
            fetcher_engine=mock_fetcher,
        )

        assert len(items) == 2
        assert items[0]["title"] == "Item 123"
        assert items[0]["_resolved_id"] == "123"
        assert items[1]["title"] == "Item 456"
        assert items[1]["_resolved_id"] == "456"

    def test_id_list_resolver_empty(self) -> None:
        resolver = IDListResolver()
        items = resolver.resolve([], "https://example.com/{id}", None)
        assert items == []

    def test_id_list_resolver_error(self) -> None:
        resolver = IDListResolver()
        mock_fetcher = MagicMock()
        mock_fetcher.fetch = MagicMock(return_value={
            "status_code": 500,
            "body_text": "",
            "error": "server_error",
        })

        items = resolver.resolve(
            item_ids=["bad"],
            items_url_template="https://example.com/{id}",
            fetcher_engine=mock_fetcher,
        )

        assert len(items) == 1
        assert items[0]["error"] == "server_error"
        assert items[0]["_resolved_id"] == "bad"


# ── ParserEngine Tests ────────────────────────────────────────────────


class TestParserEngine:
    def test_engine_dispatch_rss(self) -> None:
        engine = ParserEngine()
        result = engine.parse(RSS_XML, {"type": "rss", "config": {}})
        assert "items" in result
        assert "errors" in result
        assert len(result["items"]) == 2
        assert result["items"][0]["title"] == "Item One"

    def test_engine_dispatch_jsonpath(self) -> None:
        engine = ParserEngine()
        result = engine.parse(
            json.dumps(NESTED_JSON),
            {"type": "jsonpath", "config": {"path": "$.data.children[*].data"}},
        )
        assert len(result["items"]) == 3
        assert result["items"][0]["title"] == "Post 1"

    def test_engine_dispatch_xpath(self) -> None:
        engine = ParserEngine()
        result = engine.parse(
            XML_WITH_ITEMS,
            {
                "type": "xpath",
                "config": {
                    "path": ".//item",
                    "field_map": {
                        "url": "link/text()",
                        "title": "title/text()",
                        "content": "summary/text()",
                    },
                },
            },
        )
        assert len(result["items"]) == 2
        assert result["items"][0]["title"] == "XML Item 1"

    def test_engine_dispatch_html(self) -> None:
        engine = ParserEngine()
        result = engine.parse(
            SIMPLE_HTML,
            {"type": "html", "config": {"selector": "body"}},
        )
        assert len(result["items"]) >= 1
        assert "Test Page" in str(result["items"])

    def test_engine_dispatch_unknown(self) -> None:
        engine = ParserEngine()
        result = engine.parse("", {"type": "unknown_type", "config": {}})
        assert len(result["errors"]) == 1
        assert "unknown_type" in result["errors"][0]

    def test_engine_dispatch_id_list(self) -> None:
        engine = ParserEngine()
        mock_fetcher = MagicMock()
        mock_fetcher.fetch = MagicMock(return_value={
            "status_code": 200,
            "body_text": json.dumps({"title": "Resolved", "url": "https://example.com/1"}),
            "error": None,
        })

        result = engine.parse(
            "",
            {
                "type": "id_list",
                "config": {
                    "ids": ["1"],
                    "url_template": "https://example.com/{id}",
                    "fetcher_engine": mock_fetcher,
                },
            },
        )
        assert len(result["items"]) == 1
        assert result["items"][0]["title"] == "Resolved"
