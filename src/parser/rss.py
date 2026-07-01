"""RSS/Atom feed parser — uses xml.etree.ElementTree (stdlib)."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any

logger = logging.getLogger(__name__)

# Common RSS 2.0 and Atom namespaces
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


class RSSParser:
    """Parse RSS 2.0 and Atom feeds into a list of item dicts.

    Each returned dict contains: ``title``, ``link``/``url``,
    ``description``/``summary``, ``author``, ``pubDate``/``published_date``,
    ``category``, and ``guid``.
    """

    def parse(self, rss_xml: str) -> list[dict[str, Any]]:
        """Parse RSS/Atom XML string into a list of item dicts.

        Args:
            rss_xml: Raw RSS or Atom feed XML.

        Returns:
            A list of dicts, one per item/entry, with keys:
            ``title``, ``link``, ``url``, ``description``, ``summary``,
            ``author``, ``pubDate``, ``published_date``, ``category``,
            ``guid``, ``content``.
        """
        if not rss_xml or not rss_xml.strip():
            return []

        try:
            root = ET.fromstring(rss_xml.strip())
        except ET.ParseError as exc:
            logger.warning("RSS parse error: %s", exc)
            return []

        # Detect feed type by root tag
        tag = root.tag.lower()
        if "rss" in tag:
            return self._parse_rss(root)
        elif "feed" in tag or "{http://www.w3.org/2005/Atom}feed" in tag:
            return self._parse_atom(root)
        else:
            logger.warning("Unknown feed root tag: %s", tag)
            return []

    # ── RSS 2.0 ──────────────────────────────────────────────────────

    def _parse_rss(self, root: ET.Element) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        channel = root.find("channel")
        if channel is None:
            return items

        for item_elem in channel.findall("item"):
            item: dict[str, Any] = {}
            self._extract_text(item_elem, "title", item)
            self._extract_text(item_elem, "link", item)
            self._extract_text(item_elem, "description", item)
            self._extract_text(item_elem, "author", item)
            self._extract_text(item_elem, "pubDate", item)
            self._extract_text(item_elem, "category", item)
            self._extract_text(item_elem, "guid", item)
            # content:encoded (from content: namespace)
            content_el = item_elem.find("content:encoded", NS)
            if content_el is not None and content_el.text:
                item["content"] = content_el.text
            # dc:creator
            dc_creator = item_elem.find("dc:creator", NS)
            if dc_creator is not None and dc_creator.text:
                item["author"] = dc_creator.text
            # Map url from link
            if "link" in item and "url" not in item:
                item["url"] = item["link"]
            items.append(item)

        return items

    # ── Atom ──────────────────────────────────────────────────────────

    def _parse_atom(self, root: ET.Element) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for entry in root.findall("atom:entry", NS):
            item: dict[str, Any] = {}
            # Title
            title_el = entry.find("atom:title", NS)
            if title_el is not None and title_el.text:
                item["title"] = title_el.text
            # Link (href from the 'alternate' link)
            for link_el in entry.findall("atom:link", NS):
                rel = link_el.get("rel", "alternate")
                href = link_el.get("href", "")
                if rel == "alternate" and href:
                    item["link"] = href
                    item["url"] = href
                    break
            if "url" not in item and "link" not in item:
                # Fallback: take the first link
                first_link = entry.find("atom:link", NS)
                if first_link is not None:
                    href = first_link.get("href", "")
                    item["link"] = href
                    item["url"] = href
            # Summary
            summary_el = entry.find("atom:summary", NS)
            if summary_el is not None and summary_el.text:
                item["summary"] = summary_el.text
                item["description"] = summary_el.text
            # Content
            content_el = entry.find("atom:content", NS)
            if content_el is not None:
                txt = content_el.text or ""
                item["content"] = txt
            # Author
            author_el = entry.find("atom:author", NS)
            if author_el is not None:
                name_el = author_el.find("atom:name", NS)
                if name_el is not None and name_el.text:
                    item["author"] = name_el.text
            # Published / Updated
            published_el = entry.find("atom:published", NS)
            if published_el is not None and published_el.text:
                item["published_date"] = published_el.text
                item["pubDate"] = published_el.text
            else:
                updated_el = entry.find("atom:updated", NS)
                if updated_el is not None and updated_el.text:
                    item["published_date"] = updated_el.text
                    item["pubDate"] = updated_el.text
            # Category
            categories = []
            for cat_el in entry.findall("atom:category", NS):
                term = cat_el.get("term", "")
                if term:
                    categories.append(term)
            if categories:
                item["category"] = ", ".join(categories)
            # ID
            id_el = entry.find("atom:id", NS)
            if id_el is not None and id_el.text:
                item["guid"] = id_el.text
            items.append(item)
        return items

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _extract_text(
        parent: ET.Element,
        tag: str,
        target: dict[str, Any],
    ) -> None:
        """Extract text from *tag* under *parent* and add to *target*."""
        el = parent.find(tag)
        if el is not None and el.text:
            target[tag] = el.text.strip()
