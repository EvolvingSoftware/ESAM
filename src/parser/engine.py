"""Parser Engine — dispatches to sub-parsers based on configuration."""

from __future__ import annotations

import json
import logging
from typing import Any

from parser.rss import RSSParser
from parser.jsonpath import JSONPathParser
from parser.xpath import XPathParser
from parser.html_selector import HTMLParser
from parser.id_list_resolver import IDListResolver

logger = logging.getLogger(__name__)


class ParserEngine:
    """Central parser that dispatches to the correct sub-parser.

    Usage::

        engine = ParserEngine()
        result = engine.parse(response_body, parser_config)
    """

    def __init__(self) -> None:
        self.rss_parser = RSSParser()
        self.jsonpath_parser = JSONPathParser()
        self.xpath_parser = XPathParser()
        self.html_parser = HTMLParser()
        self.id_list_resolver = IDListResolver()

    def parse(
        self,
        response_body: str,
        parser_config: dict[str, Any],
    ) -> dict[str, Any]:
        """Parse a response body according to *parser_config*.

        Args:
            response_body: The raw response body text to parse.
            parser_config: A dict with keys ``type`` (one of ``rss``,
                ``jsonpath``, ``xpath``, ``html``, ``id_list``) and
                ``config`` (sub-parser-specific options).

        Returns:
            A dict ``{items: [...], errors: [...]}``.  Each item has at
            least ``url``, ``title``, ``content``, ``author``,
            ``published_date`` plus any ``source_fields`` dict.
        """
        ptype = parser_config.get("type", "")
        config = parser_config.get("config", {})

        errors: list[str] = []
        items: list[dict[str, Any]] = []

        try:
            if ptype == "rss":
                raw_items = self.rss_parser.parse(response_body)
                items = self._normalize_rss_items(raw_items)
            elif ptype == "jsonpath":
                try:
                    data = json.loads(response_body)
                except (json.JSONDecodeError, TypeError) as exc:
                    errors.append(f"jsonpath: invalid JSON: {exc}")
                    data = {}
                path_expr = config.get("path", "$")
                raw_items = self.jsonpath_parser.parse(data, path_expr)
                items = self._normalize_jsonpath_items(raw_items, config)
            elif ptype == "xpath":
                field_map = config.get("field_map", {})
                xpath_expr = config.get("path", "//item")
                raw_items = self.xpath_parser.parse(
                    response_body, xpath_expr, field_map,
                )
                items = self._normalize_xpath_items(raw_items)
            elif ptype == "html":
                css_selector = config.get("selector", "body")
                field_map = config.get("field_map", {})
                raw_items = self.html_parser.parse(
                    response_body, css_selector, field_map,
                )
                items = self._normalize_html_items(raw_items)
            elif ptype == "id_list":
                # id_list resolution requires a fetcher engine — provided
                # via config or created fresh
                fetcher = config.get("fetcher_engine", None)
                ids = config.get("ids", [])
                url_template = config.get("url_template", "")
                raw_items = self.id_list_resolver.resolve(
                    ids, url_template, fetcher,
                )
                items = self._normalize_id_list_items(raw_items)
            else:
                errors.append(f"Unknown parser type: {ptype}")
        except Exception as exc:
            logger.exception("Parser engine error for type %s", ptype)
            errors.append(f"parser_error: {exc}")

        return {"items": items, "errors": errors}

    # ── Item normalizers ──────────────────────────────────────────────

    @staticmethod
    def _normalize_rss_items(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for item in raw:
            items.append({
                "url": item.get("link") or item.get("url", ""),
                "title": item.get("title", ""),
                "content": (item.get("description")
                            or item.get("summary")
                            or item.get("content", "")),
                "author": item.get("author", ""),
                "published_date": item.get("published_date")
                                  or item.get("pubDate", ""),
                "source_fields": {
                    "category": item.get("category", ""),
                    "guid": item.get("guid", ""),
                },
            })
        return items

    @staticmethod
    def _normalize_jsonpath_items(
        raw: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        field_map = config.get("field_map", {})
        for item in raw:
            # JSONPathParser may return a list-of-lists (e.g. $.hits returns
            # [hits_array] where hits_array itself is the list of dicts).
            # Flatten nested lists to reach the actual item dicts.
            if isinstance(item, list):
                seq = item
            else:
                seq = [item]
            for sub in seq:
                if not isinstance(sub, dict):
                    continue
                items.append({
                    "url": (sub.get(field_map.get("url", "url")) or ""
                            if "url" in field_map
                            else sub.get("url") or ""),
                    "title": (sub.get(field_map.get("title", "title")) or ""
                              if "title" in field_map
                              else sub.get("title") or ""),
                    "content": (sub.get(field_map.get("content", "content")) or ""
                                if "content" in field_map
                                else sub.get("content") or ""),
                    "author": (sub.get(field_map.get("author", "author")) or ""
                               if "author" in field_map
                               else sub.get("author") or ""),
                    "published_date": (sub.get(
                        field_map.get("published_date", "published_date")) or ""
                        if "published_date" in field_map
                        else sub.get("published_date") or ""),
                    "source_fields": {k: v for k, v in sub.items()
                                      if k not in ("url", "title", "content",
                                                   "author", "published_date")},
                })
        return items

    @staticmethod
    def _normalize_xpath_items(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for item in raw:
            items.append({
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "content": item.get("content", ""),
                "author": item.get("author", ""),
                "published_date": item.get("published_date", ""),
                "source_fields": {k: v for k, v in item.items()
                                  if k not in ("url", "title", "content",
                                               "author", "published_date")},
            })
        return items

    @staticmethod
    def _normalize_html_items(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for item in raw:
            items.append({
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "content": item.get("content", ""),
                "author": item.get("author", ""),
                "published_date": item.get("published_date", ""),
                "source_fields": {k: v for k, v in item.items()
                                  if k not in ("url", "title", "content",
                                               "author", "published_date")},
            })
        return items

    @staticmethod
    def _normalize_id_list_items(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for item in raw:
            items.append({
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "content": item.get("content", ""),
                "author": item.get("author", ""),
                "published_date": item.get("published_date", ""),
                "source_fields": {k: v for k, v in item.items()
                                  if k not in ("url", "title", "content",
                                               "author", "published_date")},
            })
        return items
