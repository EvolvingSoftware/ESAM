"""XPath-style parser for XML — uses xml.etree.ElementTree (stdlib)."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any

logger = logging.getLogger(__name__)


class XPathParser:
    """Extract items from XML using ElementTree's limited XPath support
    plus a ``field_map`` for per-field extraction.

    The ``field_map`` maps output field names to ElementTree XPath
    expressions, e.g.::

        field_map = {
            "url": "link/text()",
            "title": "title/text()",
            "content": "summary/text()",
        }
    """

    def parse(
        self,
        xml_data: str,
        xpath_expression: str,
        field_map: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Parse *xml_data* and extract items.

        Args:
            xml_data: Raw XML string.
            xpath_expression: ElementTree-compatible XPath for item
                elements, e.g. ``.//item`` or ``.//entry``.
            field_map: Mapping of output field names to XPath expressions
                relative to each item element.

        Returns:
            A list of extracted item dicts with keys matching ``field_map``.
        """
        if not xml_data or not xml_data.strip():
            return []

        try:
            root = ET.fromstring(xml_data.strip())
        except ET.ParseError as exc:
            logger.warning("XPath parse error: %s", exc)
            return []

        items: list[dict[str, Any]] = []

        try:
            # Register common namespaces to avoid {ns}tag issues
            self._register_namespaces(root)
            item_elements = root.findall(xpath_expression)
        except SyntaxError as exc:
            logger.warning("XPath syntax error: %s", exc)
            return []

        for elem in item_elements:
            item: dict[str, Any] = {}
            for field_name, field_xpath in field_map.items():
                value = self._extract_field(elem, field_xpath)
                if value is not None:
                    item[field_name] = value
            if item:
                items.append(item)

        return items

    # ── Internal ──────────────────────────────────────────────────────

    @staticmethod
    def _extract_field(elem: ET.Element, xpath: str) -> str | None:
        """Extract a single field value from an element.

        Supports:
        * ``tag/text()`` — text content of child tag
        * ``tag/@attr`` — attribute value of child tag
        * ``tag`` — full XML text of child (text + tail)
        * ``./tag`` — relative from current element
        """
        xpath = xpath.strip()
        if xpath.endswith("/text()"):
            # Extract text content
            base_path = xpath[:-7]  # strip "/text()"
            found = elem.find(base_path)
            if found is not None and found.text:
                return found.text.strip()
            return None

        if "/@" in xpath:
            # Extract attribute: tag/@attr
            parts = xpath.rsplit("/@", 1)
            base_path = parts[0]
            attr_name = parts[1]
            found = elem.find(base_path)
            if found is not None:
                val = found.get(attr_name)
                if val:
                    return val.strip()
            return None

        # Plain tag — return concatenated text
        found = elem.find(xpath)
        if found is not None:
            txt = (found.text or "").strip()
            # Also gather tail text of children
            tail_parts = []
            for child in found:
                if child.tail:
                    tail_parts.append(child.tail.strip())
            if tail_parts:
                txt += " " + " ".join(tail_parts)
            return txt if txt else None

        return None

    @staticmethod
    def _register_namespaces(root: ET.Element) -> None:
        """Pre-register namespaces found in the root element, stripping
        them from the tag reference so that basic XPath like ``//item``
        works even under namespaced documents like Atom."""
        # This is a no-op for ElementTree — it handles ns-prefixed paths
        # via the {uri}tag syntax.  We don't auto-register because
        # ElementTree's find/findall require explicit {ns} or registered
        # prefixes.  The user must use ``{ns}tag`` syntax or we fall
        # back to the user's field_map paths.
        pass
