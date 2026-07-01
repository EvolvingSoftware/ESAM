"""Batch Extractor — parallel content extraction for multiple items."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from extractor.engine import ContentExtractor

logger = logging.getLogger(__name__)


class BatchExtractor:
    """Extract content from multiple HTML sources in parallel."""

    def __init__(self, extractor: ContentExtractor | None = None) -> None:
        self.extractor = extractor or ContentExtractor()

    def extract_batch(
        self,
        items: list[dict[str, Any]],
        max_workers: int = 5,
    ) -> list[dict[str, Any]]:
        """Extract content from a batch of items in parallel.

        Each item should have ``url`` and either ``body_html`` or
        ``raw_content`` keys.

        Args:
            items: List of dicts, each with at minimum ``url`` and
                ``body_html`` (or ``raw_content``).
            max_workers: Max parallel workers (default 5).

        Returns:
            List of extracted content dicts, each augmented with
            ``_error`` if extraction failed for that item.
        """
        results: list[dict[str, Any]] = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {}
            for item in items:
                url = item.get("url", "")
                html_body = item.get("body_html", item.get("raw_content", ""))
                future = executor.submit(
                    self._extract_single,
                    html_body,
                    url,
                )
                future_map[future] = (url, item)

            for future in as_completed(future_map):
                url, original_item = future_map[future]
                try:
                    result = future.result()
                    result["_item_index"] = items.index(original_item)
                    results.append(result)
                except Exception as exc:
                    logger.error("Batch extract failed for %s: %s", url, exc)
                    results.append({
                        "url": url,
                        "title": "",
                        "content_text": "",
                        "content_html": "",
                        "excerpt": "",
                        "author": "",
                        "published_date": "",
                        "reading_time": 0.0,
                        "word_count": 0,
                        "_item_index": items.index(original_item),
                        "_error": str(exc),
                    })

        # Restore original ordering
        results.sort(key=lambda r: r.get("_item_index", 0))
        for r in results:
            r.pop("_item_index", None)

        return results

    def _extract_single(self, html_body: str, url: str) -> dict[str, Any]:
        return self.extractor.extract(html_body, url)
