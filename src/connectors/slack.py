"""SlackConnector — Fetches messages from a Slack channel.

Simulated: returns test data for now, pending Slack API integration.
"""

from __future__ import annotations

import logging
import time
from typing import Any, ClassVar

from connectors.base import ConnectorBase
from connectors.registry import ConnectorRegistry

logger = logging.getLogger(__name__)


class SlackConnector(ConnectorBase):
    """Fetch messages from a Slack channel.

    Config:
        channel (str, required): Channel name or ID.
        limit (int, optional): Max messages to fetch (default 50).
    """

    name: ClassVar[str] = "slack"
    description: ClassVar[str] = "Fetch messages from a Slack channel"
    config_fields: ClassVar[list[dict]] = [
        {"name": "channel", "type": "string", "required": True,
         "description": "Channel name or ID (e.g. '#general')"},
        {"name": "limit", "type": "integer", "required": False,
         "description": "Max messages to fetch (default 50)", "default": 50},
    ]
    auth_required: ClassVar[bool] = True
    rate_limit: ClassVar[str] = "1 request per second (tier 3)"

    def fetch(self) -> list[dict[str, Any]]:
        if not self.validate_config():
            raise ValueError("Missing required config: 'channel'")

        channel = self.config.get("channel", "")
        limit = self.config.get("limit", 50)

        # Simulated: return test messages
        now = time.time()
        results: list[dict[str, Any]] = []
        for i in range(min(limit, 15)):
            results.append({
                "url": f"https://slack.com/archives/{channel}/p{int(now - i * 3600)}",
                "title": f"Message from user_{i}",
                "author": f"user_{i}",
                "text": f"This is simulated message #{i} in {channel}.",
                "ts": str(now - i * 3600),
            })

        return results


# ── Auto-register ─────────────────────────────────────────────────────
ConnectorRegistry.register(SlackConnector)
