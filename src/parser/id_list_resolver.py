"""ID List Resolver — resolves a list of IDs via a URL template using FetcherEngine."""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class IDListResolver:
    """Resolve a list of item IDs to full item dicts via a URL template.

    Usage::

        resolver = IDListResolver()
        items = resolver.resolve(
            item_ids=["123", "456"],
            items_url_template="https://hn.algolia.com/api/v1/items/{id}",
            fetcher_engine=fetcher,
        )
    """

    def resolve(
        self,
        item_ids: list[str],
        items_url_template: str,
        fetcher_engine: Any = None,
    ) -> list[dict[str, Any]]:
        """Fetch each ID from the URL template and return resolved items.

        Args:
            item_ids: List of string IDs to resolve.
            items_url_template: URL template with ``{id}`` placeholder.
                Example: ``https://hn.algolia.com/api/v1/items/{id}``
            fetcher_engine: A :class:`~fetcher.engine.FetcherEngine`
                instance.  If ``None``, a fresh one is created.

        Returns:
            A list of resolved item dicts.  Each item includes a
            ``_resolved_id`` field indicating which ID it was resolved
            from.
        """
        if not item_ids:
            return []

        if fetcher_engine is None:
            from fetcher.engine import FetcherEngine
            fetcher_engine = FetcherEngine()

        items: list[dict[str, Any]] = []

        for idx, item_id in enumerate(item_ids):
            url = items_url_template.replace("{id}", item_id)

            try:
                result = fetcher_engine.fetch(url)
            except Exception as exc:
                logger.warning(
                    "IDListResolver: failed to fetch %s: %s", url, exc,
                )
                items.append({
                    "_resolved_id": item_id,
                    "url": url,
                    "error": str(exc),
                })
                continue

            if result.get("error"):
                logger.warning(
                    "IDListResolver: error fetching %s: %s",
                    url, result["error"],
                )
                items.append({
                    "_resolved_id": item_id,
                    "url": url,
                    "error": result["error"],
                })
                continue

            # Parse response body as JSON
            import json
            body = result.get("body_text", "")
            if body:
                try:
                    data = json.loads(body)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "IDListResolver: JSON parse error for %s: %s",
                        url, exc,
                    )
                    items.append({
                        "_resolved_id": item_id,
                        "url": url,
                        "error": f"JSON parse error: {exc}",
                        "body_text": body[:500],
                    })
                    continue
            else:
                data = {}

            if isinstance(data, dict):
                data["_resolved_id"] = item_id
                data["url"] = data.get("url") or data.get("link") or url
                items.append(data)
            elif isinstance(data, list):
                for sub in data:
                    if isinstance(sub, dict):
                        sub["_resolved_id"] = item_id
                        sub.setdefault("url", url)
                    items.append(sub)
            else:
                items.append({
                    "_resolved_id": item_id,
                    "url": url,
                    "data": data,
                })

        return items
