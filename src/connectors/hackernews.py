"""HackerNewsConnector — Fetches top stories from Hacker News."""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from connectors.base import ConnectorBase
from connectors.registry import ConnectorRegistry

logger = logging.getLogger(__name__)

HN_TOP_STORIES = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{id}.json"


class HackerNewsConnector(ConnectorBase):
    """Fetch top stories from Hacker News.

    Implements the ID-list-resolution pattern: fetches a list of story IDs,
    then resolves each ID to its full item data.

    Config:
        limit (int, optional): Number of stories to fetch (default 30).
    """

    name: ClassVar[str] = "hackernews"
    description: ClassVar[str] = "Fetch top stories from Hacker News"
    config_fields: ClassVar[list[dict]] = [
        {"name": "limit", "type": "integer", "required": False,
         "description": "Number of stories to fetch (default 30)", "default": 30},
    ]
    auth_required: ClassVar[bool] = False
    rate_limit: ClassVar[str] = "500 requests per minute (Firebase)"

    def fetch(self) -> list[dict[str, Any]]:
        limit = self.config.get("limit", 30)

        # Step 1: Fetch the list of top story IDs
        story_ids: list[int] = self._fetch_json(HN_TOP_STORIES)
        story_ids = story_ids[:limit]

        # Step 2: Resolve each ID to its full item
        results: list[dict[str, Any]] = []
        for sid in story_ids:
            try:
                item = self._fetch_json(HN_ITEM.format(id=sid))
                if not item or item.get("deleted") or item.get("dead"):
                    continue
                results.append({
                    "url": item.get("url", f"https://news.ycombinator.com/item?id={sid}"),
                    "title": item.get("title", ""),
                    "score": item.get("score", 0),
                    "descendants": item.get("descendants", 0),
                    "by": item.get("by", ""),
                    "time": item.get("time", 0),
                })
            except RuntimeError as e:
                logger.warning("Failed to fetch HN item %s: %s", sid, e)
                continue

        return results


# ── Auto-register ─────────────────────────────────────────────────────
ConnectorRegistry.register(HackerNewsConnector)
