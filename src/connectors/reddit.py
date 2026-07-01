"""RedditConnector — Fetches hot posts from a subreddit."""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from connectors.base import ConnectorBase
from connectors.registry import ConnectorRegistry

logger = logging.getLogger(__name__)


class RedditConnector(ConnectorBase):
    """Fetch hot posts from a Reddit subreddit.

    Config:
        subreddit (str, required): Subreddit name (e.g. "python").
        limit (int, optional): Number of posts to fetch (default 25).
    """

    name: ClassVar[str] = "reddit"
    description: ClassVar[str] = "Fetch hot posts from a Reddit subreddit"
    config_fields: ClassVar[list[dict]] = [
        {"name": "subreddit", "type": "string", "required": True,
         "description": "Subreddit name (e.g. 'python')"},
        {"name": "limit", "type": "integer", "required": False,
         "description": "Number of posts to fetch (default 25)", "default": 25},
    ]
    auth_required: ClassVar[bool] = False
    rate_limit: ClassVar[str] = "60 requests per minute"

    def fetch(self) -> list[dict[str, Any]]:
        subreddit = self.config.get("subreddit", "")
        limit = self.config.get("limit", 25)

        if not subreddit:
            raise ValueError("'subreddit' config is required")

        url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit={limit}"
        headers = {
            "User-Agent": "ESAM-Connector/1.0 (by /u/esam_bot)",
        }

        data = self._fetch_json(url, headers=headers)

        results: list[dict[str, Any]] = []
        children = data.get("data", {}).get("children", [])
        for child in children:
            post = child.get("data", {})
            results.append({
                "url": f"https://www.reddit.com{post.get('permalink', '')}",
                "title": post.get("title", ""),
                "score": post.get("score", 0),
                "comments": post.get("num_comments", 0),
                "author": post.get("author", ""),
                "subreddit": post.get("subreddit", ""),
                "created_utc": post.get("created_utc", 0),
            })

        return results


# ── Auto-register ─────────────────────────────────────────────────────
ConnectorRegistry.register(RedditConnector)
