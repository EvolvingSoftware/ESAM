"""Content Extractor Engine — readability layer for extracting article text.

Strips HTML boilerplate (scripts, styles, nav, footer, ads, comments)
and extracts full article text, title, excerpt, author, published date,
reading time, word count, language, and Open Graph / meta metadata.
"""

from __future__ import annotations

import logging
import re
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Tags whose content is stripped entirely
STRIP_TAGS = {
    "script", "style", "noscript", "iframe", "embed", "object",
    "nav", "footer", "header", "aside",
    "form", "input", "select", "textarea", "button",
}

# Tags that are block-level (we insert newlines after them in text extraction)
BLOCK_TAGS = {
    "p", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "ol", "ul", "blockquote", "pre", "hr", "table", "tr", "td", "th",
    "section", "article", "main", "figure", "figcaption",
}

# Content-area tags that hint at main article content
CONTENT_TAGS = {"article", "main"}
CONTENT_ROLES = {"main", "article", "content", "post"}

MAX_FALLBACK_CHARS = 10000


def _extract_domain(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc or parsed.hostname or "unknown"


def _compute_reading_time(word_count: int, wpm: int = 200) -> float:
    """Compute reading time in seconds."""
    if word_count <= 0:
        return 0.0
    return round((word_count / wpm) * 60, 1)


class _ReadabilityParser(HTMLParser):
    """Internal HTML parser that strips noise and extracts text/content."""

    def __init__(self) -> None:
        super().__init__()
        self._strip_depth: dict[int, bool] = {}  # depth -> strip flag
        self._in_comment = False
        self._in_content_area = False
        self._content_depth = 0

        # Output buffers
        self.text_parts: list[str] = []
        self.html_parts: list[str] = []
        self.title: str = ""
        self.meta_description: str = ""
        self.meta_keywords: str = ""
        self.meta_author: str = ""
        self.og_image: str = ""
        self.og_description: str = ""
        self.lang: str = ""
        self._in_title = False
        self._in_body = False

    def _is_stripping(self, depth: int) -> bool:
        return any(
            v for d, v in self._strip_depth.items() if d <= depth and v
        )

    def _is_content_element(self, tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        if tag in CONTENT_TAGS:
            return True
        for name, val in attrs:
            if name == "role" and val and val.lower() in CONTENT_ROLES:
                return True
            if name == "id" and val:
                for hint in ("content", "article", "post", "main"):
                    if hint in val.lower():
                        return True
            if name == "class" and val:
                for hint in ("content", "article", "post", "main", "entry"):
                    if hint in val.lower():
                        return True
        return False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        depth = len(self._strip_depth)

        # Track html lang
        if tag == "html":
            for name, val in attrs:
                if name == "lang" and val:
                    self.lang = val

        if tag == "body":
            self._in_body = True

        if tag == "title":
            self._in_title = True

        # Check if we should strip this tag
        strip = tag.lower() in STRIP_TAGS
        self._strip_depth[depth] = strip or (depth > 0 and self._is_stripping(depth - 1))

        # Check if this is a content-area element
        if self._is_content_element(tag, attrs):
            self._in_content_area = True
            self._content_depth = depth

        # Track meta tags
        if tag == "meta":
            meta_attrs = {k: (v or "") for k, v in attrs}
            name = meta_attrs.get("name", meta_attrs.get("property", "")).lower()
            content = meta_attrs.get("content", "")
            if name == "description":
                self.meta_description = content
            elif name == "keywords":
                self.meta_keywords = content
            elif name == "author":
                self.meta_author = content
            elif name == "og:image":
                self.og_image = content
            elif name == "og:description":
                self.og_description = content

        # Emit HTML only if not stripping
        if not strip and not self._is_stripping(depth):
            # Reconstruct opening tag
            attr_str = ""
            for k, v in attrs:
                if v is not None:
                    attr_str += f' {k}="{v}"'
                else:
                    attr_str += f" {k}"
            self.html_parts.append(f"<{tag}{attr_str}>")

    def handle_endtag(self, tag: str) -> None:
        depth = len(self._strip_depth) - 1

        if tag == "title":
            self._in_title = False
        if tag == "body":
            self._in_body = False

        # Clear strip state at this depth
        if depth in self._strip_depth:
            del self._strip_depth[depth]

        # Track content area exit
        if self._in_content_area and tag in CONTENT_TAGS:
            self._in_content_area = False

        # Emit HTML only if we were not stripping (check parent depth)
        parent_depth = depth - 1
        if not self._strip_depth.get(parent_depth, False) and tag not in STRIP_TAGS:
            self.html_parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._in_comment:
            return
        if self._in_title:
            self.title = data.strip()
            return

        # Check if we're inside a strip region
        if self._is_stripping(len(self._strip_depth) - 1):
            return

        text = data.strip()
        if text:
            self.text_parts.append(text)

    def handle_comment(self, data: str) -> None:
        pass  # Strip comments

    def error(self, message: str) -> None:
        logger.warning("HTML parse error: %s", message)

    def get_body_text(self) -> str:
        """Get extracted body text with paragraph spacing."""
        return "\n\n".join(self.text_parts)

    def get_body_html(self) -> str:
        """Get cleaned HTML."""
        return "".join(self.html_parts)


class ContentExtractor:
    """Extract readable article content from raw HTML.

    Strips boilerplate, extracts title, author, dates, metadata,
    and computes reading time / word count.
    """

    def extract(
        self,
        html_body: str,
        url: str,
        source_type: str = "web",
    ) -> dict[str, Any]:
        """Extract structured content from raw HTML.

        Args:
            html_body: Raw HTML string to extract from.
            url: Source URL (used for domain extraction and fallback).
            source_type: Source type hint (unused currently, reserved).

        Returns:
            Dict with keys: ``url``, ``title``, ``content_text``,
            ``content_html``, ``excerpt``, ``author``, ``published_date``,
            ``reading_time``, ``word_count``, ``lang``, ``og_image``,
            ``og_description``, ``meta_keywords``, ``domain``.
        """
        if not html_body or not html_body.strip():
            return self._empty_result(url)

        parser = _ReadabilityParser()
        try:
            parser.feed(html_body)
        except Exception as exc:
            logger.warning("HTML parsing failed, using fallback: %s", exc)
            return self._fallback_extract(html_body, url)

        content_text = parser.get_body_text()
        content_html = parser.get_body_html()

        # Fallback if content_area extraction yielded nothing
        if not content_text.strip():
            return self._fallback_extract(html_body, url)

        word_count = len(content_text.split())
        reading_time = _compute_reading_time(word_count)
        excerpt = content_text[:300] if len(content_text) > 300 else content_text
        if excerpt and len(content_text) > 300:
            # Try to break at a sentence boundary
            last_period = excerpt.rfind(".")
            if last_period > 150:
                excerpt = excerpt[: last_period + 1]

        return {
            "url": url,
            "title": parser.title or "",
            "content_text": content_text,
            "content_html": content_html,
            "excerpt": excerpt,
            "author": parser.meta_author or "",
            "published_date": "",
            "reading_time": reading_time,
            "word_count": word_count,
            "lang": parser.lang or "",
            "og_image": parser.og_image or "",
            "og_description": parser.og_description or "",
            "meta_keywords": parser.meta_keywords or "",
            "domain": _extract_domain(url),
        }

    def _fallback_extract(self, html_body: str, url: str) -> dict[str, Any]:
        """Fallback: strip all tags and return body_text truncated to 10K."""
        text = re.sub(r"<[^>]+>", " ", html_body)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > MAX_FALLBACK_CHARS:
            text = text[:MAX_FALLBACK_CHARS]
        word_count = len(text.split())
        reading_time = _compute_reading_time(word_count)
        excerpt = text[:300] if len(text) > 300 else text
        return {
            "url": url,
            "title": "",
            "content_text": text,
            "content_html": "",
            "excerpt": excerpt,
            "author": "",
            "published_date": "",
            "reading_time": reading_time,
            "word_count": word_count,
            "lang": "",
            "og_image": "",
            "og_description": "",
            "meta_keywords": "",
            "domain": _extract_domain(url),
        }

    def _empty_result(self, url: str) -> dict[str, Any]:
        return {
            "url": url,
            "title": "",
            "content_text": "",
            "content_html": "",
            "excerpt": "",
            "author": "",
            "published_date": "",
            "reading_time": 0.0,
            "word_count": 0,
            "lang": "",
            "og_image": "",
            "og_description": "",
            "meta_keywords": "",
            "domain": _extract_domain(url),
        }
