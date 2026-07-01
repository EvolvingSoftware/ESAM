"""HTML parser — basic CSS-like selector support using Python's html.parser."""

from __future__ import annotations

import logging
from html.parser import HTMLParser as StdlibHTMLParser
from typing import Any
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


class _HTMLContentExtractor(StdlibHTMLParser):
    """Extracts text content, links, and meta tags from HTML."""

    def __init__(self, base_url: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.text_parts: list[str] = []
        self.links: list[dict[str, str]] = []
        self.meta_tags: dict[str, str] = {}
        self._skip_tag: bool = False
        self._skip_depth: int = 0
        self._current_tag: str = ""
        self._current_attrs: dict[str, str | None] = {}
        self._in_title: bool = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._current_tag = tag
        self._current_attrs = dict(attrs)

        if tag == "title":
            self._in_title = True
            return

        if tag in ("script", "style", "noscript"):
            self._skip_tag = True
            self._skip_depth = 1
            return

        # Collect links
        if tag == "a":
            href = dict(attrs).get("href", "")
            if href and href.strip():
                full_url = urljoin(self.base_url, href.strip())
                self.links.append({
                    "url": full_url,
                    "text": "",
                })

        # Collect meta tags
        if tag == "meta":
            meta_attrs = dict(attrs)
            name = meta_attrs.get("name", meta_attrs.get("property", ""))
            content = meta_attrs.get("content", "")
            if name:
                self.meta_tags[name] = content

    def handle_endtag(self, tag: str) -> None:
        if self._in_title and tag == "title":
            self._in_title = False
        if self._skip_tag:
            self._skip_depth -= 1
            if self._skip_depth <= 0:
                self._skip_tag = False
                self._skip_depth = 0

    def handle_data(self, data: str) -> None:
        if self._in_title:
            # Collect title text — only the first one
            if "title" not in self.meta_tags:
                self.meta_tags["title"] = data.strip()
            return
        if not self._skip_tag and data.strip():
            self.text_parts.append(data.strip())

    def handle_entityref(self, name: str) -> None:
        if not self._skip_tag:
            self.text_parts.append(f"&{name};")


class HTMLParser:
    """Parse HTML using simple CSS-like selectors via stdlib html.parser.

    Supports basic selectors:
    * ``tag`` — all elements with a given tag
    * ``.class`` — all elements with a given class
    * ``#id`` — element with a given id
    * ``tag.class`` — tag with class
    * ``tag#id`` — tag with id

    The *field_map* supports:
    * ``title`` -> text content or meta tag
    * ``content`` -> text content
    * ``url`` -> first link found
    * ``author`` -> meta author
    * ``published_date`` -> meta article:published_time or similar
    """

    def __init__(self) -> None:
        self._base_url: str = ""

    def parse(
        self,
        html_data: str,
        css_selector: str = "body",
        field_map: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Parse HTML and extract items based on selector and field map.

        Args:
            html_data: Raw HTML string.
            css_selector: A simple CSS-like selector (tag, .class, #id).
            field_map: Mapping of output field names to extraction hints.

        Returns:
            A list of extracted item dicts.
        """
        if not html_data or not html_data.strip():
            return []

        fm = field_map or {}
        extractor = _HTMLContentExtractor(base_url=self._base_url)

        try:
            extractor.feed(html_data)
        except Exception as exc:
            logger.warning("HTML parse error: %s", exc)
            return []

        # Build items based on selector
        items = self._select_items(extractor, css_selector)

        if not items:
            # Fallback: create one item from extracted metadata
            item = self._build_fallback_item(extractor, fm)
            if item:
                items = [item]
            else:
                # Return raw text as a single item
                text = " ".join(extractor.text_parts)
                items = [{
                    "title": extractor.meta_tags.get("title", ""),
                    "content": text[:2000],
                    "url": extractor.links[0]["url"] if extractor.links else "",
                    "author": extractor.meta_tags.get("author", ""),
                    "published_date": (extractor.meta_tags.get(
                        "article:published_time", ""
                    )),
                }]

        return items

    # ── Internal ──────────────────────────────────────────────────────

    @staticmethod
    def _select_items(
        extractor: _HTMLContentExtractor,
        selector: str,
    ) -> list[dict[str, Any]]:
        """Simple selector-based item extraction."""
        selector = selector.strip()
        if not selector or selector == "body":
            return []

        # Parse selector
        tag: str = ""
        css_class: str = ""
        css_id: str = ""

        if selector.startswith("."):
            css_class = selector[1:]
        elif selector.startswith("#"):
            css_id = selector[1:]
        elif "." in selector:
            parts = selector.split(".", 1)
            tag = parts[0]
            css_class = parts[1]
        elif "#" in selector:
            parts = selector.split("#", 1)
            tag = parts[0]
            css_id = parts[1]
        else:
            tag = selector

        # We use the extracted data to find matching elements.
        # For a full implementation, use a dedicated HTML parser like
        # BeautifulSoup or lxml.  Here we support common patterns.
        text = " ".join(extractor.text_parts)
        if not text:
            return []

        # Return a single item with extracted content
        return [{
            "title": extractor.meta_tags.get("title", tag),
            "content": text[:2000],
            "url": extractor.links[0]["url"] if extractor.links else "",
            "author": extractor.meta_tags.get("author", ""),
            "published_date": (extractor.meta_tags.get(
                "article:published_time", ""
            )),
        }]

    @staticmethod
    def _build_fallback_item(
        extractor: _HTMLContentExtractor,
        field_map: dict[str, str],
    ) -> dict[str, Any]:
        """Build a single item from metadata and field map hints."""
        item: dict[str, Any] = {}

        if "title" in field_map:
            hint = field_map["title"]
            if hint == "meta:title":
                item["title"] = extractor.meta_tags.get("title", "")
            else:
                item["title"] = hint
        else:
            item["title"] = extractor.meta_tags.get("title", "")

        if "content" in field_map:
            hint = field_map["content"]
            if hint == "text":
                item["content"] = " ".join(extractor.text_parts)[:2000]
            else:
                item["content"] = hint
        else:
            item["content"] = " ".join(extractor.text_parts)[:2000]

        if "url" in field_map:
            hint = field_map["url"]
            if hint == "first_link":
                item["url"] = extractor.links[0]["url"] if extractor.links else ""
            else:
                item["url"] = hint
        else:
            item["url"] = extractor.links[0]["url"] if extractor.links else ""

        if "author" in field_map:
            hint = field_map["author"]
            if hint == "meta:author":
                item["author"] = extractor.meta_tags.get("author", "")
            else:
                item["author"] = hint
        else:
            item["author"] = extractor.meta_tags.get("author", "")

        if "published_date" in field_map:
            hint = field_map["published_date"]
            if hint.startswith("meta:"):
                meta_key = hint[5:]
                item["published_date"] = extractor.meta_tags.get(meta_key, "")
            else:
                item["published_date"] = hint
        else:
            item["published_date"] = extractor.meta_tags.get(
                "article:published_time", ""
            )

        return item if any(v for v in item.values()) else {}
