"""Metadata Extractor — extract Open Graph, Twitter Cards, meta tags, and JSON-LD."""

from __future__ import annotations

import json
import logging
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class _MetadataParser(HTMLParser):
    """Internal parser that collects metadata from HTML head elements."""

    def __init__(self) -> None:
        super().__init__()
        self.og: dict[str, str] = {}
        self.twitter: dict[str, str] = {}
        self.meta: dict[str, str] = {}
        self.json_ld: list[dict[str, Any]] = []
        self.title: str = ""
        self._in_title = False
        self._in_json_ld = False
        self._json_ld_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
            return

        if tag == "meta":
            meta_dict = {k: (v or "") for k, v in attrs}
            prop = meta_dict.get("property", meta_dict.get("name", meta_dict.get("http-equiv", ""))).lower()
            content = meta_dict.get("content", "")
            charset = meta_dict.get("charset", "")

            if prop.startswith("og:"):
                key = prop[3:]  # strip "og:" prefix
                self.og[key] = content
            elif prop.startswith("twitter:"):
                key = prop[8:]  # strip "twitter:" prefix
                self.twitter[key] = content
            elif prop in ("description", "keywords", "author", "robots"):
                self.meta[prop] = content
            elif charset:
                self.meta["charset"] = charset

        if tag == "script":
            script_type = ""
            for k, v in attrs:
                if k == "type" and v:
                    script_type = v
            if "application/ld+json" in script_type or "application/json" in script_type:
                self._in_json_ld = True
                self._json_ld_buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag == "script" and self._in_json_ld:
            self._in_json_ld = False
            raw = "".join(self._json_ld_buffer).strip()
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        self.json_ld.extend(parsed)
                    else:
                        self.json_ld.append(parsed)
                except (json.JSONDecodeError, ValueError):
                    pass  # silently skip malformed JSON-LD
            self._json_ld_buffer = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title = self.title + data
        if self._in_json_ld:
            self._json_ld_buffer.append(data)

    def error(self, message: str) -> None:
        logger.warning("Metadata HTML parse error: %s", message)


class MetadataExtractor:
    """Extract rich metadata from HTML: Open Graph, Twitter Cards, meta tags, JSON-LD."""

    def extract_metadata(self, html: str, url: str) -> dict[str, Any]:
        """Extract all discoverable metadata from HTML.

        Args:
            html: Raw HTML string.
            url: Source URL.

        Returns:
            Dict with keys: ``title``, ``description``, ``image``, ``type``,
            ``url``, ``site_name``, ``locale``, ``twitter_card``,
            ``twitter_site``, ``meta_description``, ``meta_keywords``,
            ``meta_author``, ``meta_robots``, ``json_ld``, ``domain``.
        """
        parser = _MetadataParser()
        try:
            parser.feed(html)
        except Exception as exc:
            logger.warning("Metadata parsing failed: %s", exc)

        result: dict[str, Any] = {}

        # Title: prefer OG, then HTML <title>
        result["title"] = parser.og.get("title", parser.title)

        # Description: prefer OG, then meta description
        result["description"] = parser.og.get("description",
                                                parser.meta.get("description", ""))

        # Image: prefer OG, then Twitter image
        result["image"] = parser.og.get("image",
                                          parser.twitter.get("image:src", ""))

        # Type
        result["type"] = parser.og.get("type", "")

        # URL
        result["url"] = parser.og.get("url", url)

        # Site name & locale
        result["site_name"] = parser.og.get("site_name", "")
        result["locale"] = parser.og.get("locale", "")

        # Twitter cards
        result["twitter_card"] = parser.twitter.get("card", "")
        result["twitter_site"] = parser.twitter.get("site", "")

        # Standard meta
        result["meta_description"] = parser.meta.get("description", "")
        result["meta_keywords"] = parser.meta.get("keywords", "")
        result["meta_author"] = parser.meta.get("author", "")
        result["meta_robots"] = parser.meta.get("robots", "")

        # JSON-LD
        result["json_ld"] = parser.json_ld

        # Domain
        parsed_url = urlparse(url)
        result["domain"] = parsed_url.netloc or parsed_url.hostname or "unknown"

        return result
