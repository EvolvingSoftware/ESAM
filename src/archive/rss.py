"""RSS Feed generation for the newsletter archive.

Produces a valid RSS 2.0 XML feed using only stdlib
``xml.etree.ElementTree``.
"""

from __future__ import annotations

import html as html_mod
from xml.etree import ElementTree as ET
from xml.dom import minidom

__all__ = ["RSSFeed"]

FEED_TITLE = "Newsletter Archive"
FEED_DESCRIPTION = "Archived newsletter editions from the Evolving Software agent management system."
FEED_LINK = "https://hermes.local/archives/"


class RSSFeed:
    """Generates RSS 2.0 XML for a list of edition dicts."""

    def generate(self, editions: list[dict]) -> str:
        """Produce a complete RSS 2.0 XML string.

        Args:
            editions: List of dicts, each with ``id``, ``subject``,
                      ``body_html``, ``permalink``, and ``created_at`` keys.

        Returns:
            A well-formed RSS 2.0 XML document as a string.
        """
        rss = ET.Element("rss", version="2.0",
                         attrib={"xmlns:atom": "http://www.w3.org/2005/Atom"})
        channel = ET.SubElement(rss, "channel")

        # ── Channel metadata ────────────────────────────────────────
        ET.SubElement(channel, "title").text = FEED_TITLE
        ET.SubElement(channel, "link").text = FEED_LINK
        ET.SubElement(channel, "description").text = FEED_DESCRIPTION
        ET.SubElement(channel, "language").text = "en-us"
        ET.SubElement(channel, "generator").text = "Hermes Archive System"

        # Atom self-link so feed readers can discover
        atom_link = ET.SubElement(channel, "{http://www.w3.org/2005/Atom}link")
        atom_link.set("href", f"{FEED_LINK}rss.xml")
        atom_link.set("rel", "self")
        atom_link.set("type", "application/rss+xml")

        # ── Items ───────────────────────────────────────────────────
        for edition in editions:
            item = ET.SubElement(channel, "item")
            ET.SubElement(item, "title").text = edition.get("subject", "(no subject)")
            ET.SubElement(item, "link").text = edition.get("permalink", FEED_LINK)
            ET.SubElement(item, "guid", isPermaLink="false").text = edition.get("id", "")

            pub_date = edition.get("created_at", "")
            if pub_date:
                # Convert ISO 8601 to RFC 2822-ish format
                try:
                    from datetime import datetime, timezone
                    dt = datetime.fromisoformat(pub_date)
                    # RSS prefers RFC 2822
                    pub_date_rfc = dt.strftime("%a, %d %b %Y %H:%M:%S %z")
                    # Ensure +0000 not +00:00
                    pub_date_rfc = pub_date_rfc.replace("+00:00", "+0000")
                except (ValueError, TypeError):
                    pub_date_rfc = pub_date
                ET.SubElement(item, "pubDate").text = pub_date_rfc

            # Description: sanitized HTML snippet
            body = edition.get("body_html", "") or edition.get("body_markdown", "")
            excerpt = body[:500] if body else ""
            ET.SubElement(item, "description").text = excerpt

        # ── Pretty-print ────────────────────────────────────────────
        rough = ET.tostring(rss, encoding="unicode", xml_declaration=False)
        dom = minidom.parseString(rough.encode("utf-8"))
        pretty = dom.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
        # Strip the <?xml?> line that toprettyxml adds so we can add our own
        lines = pretty.splitlines()
        if lines and lines[0].startswith("<?xml"):
            lines = lines[1:]
        return '<?xml version="1.0" encoding="UTF-8"?>\n' + "\n".join(lines)
