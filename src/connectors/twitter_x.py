"""TwitterXConnector — Fetches tweets/posts from X/Twitter.

Simulated: returns test data for now, pending X API integration.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from connectors.base import ConnectorBase
from connectors.registry import ConnectorRegistry

logger = logging.getLogger(__name__)


class TwitterXConnector(ConnectorBase):
    """Fetch tweets/posts from X (Twitter).

    Config:
        query (str, required): Search query or username.
        count (int, optional): Number of tweets to fetch (default 20).
    """

    name: ClassVar[str] = "twitter_x"
    description: ClassVar[str] = "Fetch tweets/posts from X (Twitter)"
    config_fields: ClassVar[list[dict]] = [
        {"name": "query", "type": "string", "required": True,
         "description": "Search query or @username"},
        {"name": "count", "type": "integer", "required": False,
         "description": "Number of tweets to fetch (default 20)", "default": 20},
    ]
    auth_required: ClassVar[bool] = True
    rate_limit: ClassVar[str] = "100 requests per 15 minutes (X API v2)"

    def fetch(self) -> list[dict[str, Any]]:
        if not self.validate_config():
            raise ValueError("Missing required config: 'query'")

        query = self.config.get("query", "")
        count = self.config.get("count", 20)

        # Simulated: return test tweets
        results: list[dict[str, Any]] = []
        for i in range(min(count, 15)):
            results.append({
                "url": f"https://x.com/i/web/status/{100000 + i}",
                "text": f"Simulated tweet #{i} about '{query}' — this is sample content for testing.",
                "author": f"user_{i}",
                "likes": i * 10 + 5,
                "retweets": i * 2,
                "created_at": f"2026-06-{25 - (i % 20):02d}T{10 + i % 12:02d}:00:00Z",
            })

        return results


# ── Auto-register ─────────────────────────────────────────────────────
ConnectorRegistry.register(TwitterXConnector)
