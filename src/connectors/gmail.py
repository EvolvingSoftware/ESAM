"""GmailConnector — Fetches emails from a Gmail account.

Simulated: returns test data for now, pending Gmail API integration.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from connectors.base import ConnectorBase
from connectors.registry import ConnectorRegistry

logger = logging.getLogger(__name__)


class GmailConnector(ConnectorBase):
    """Fetch emails from a Gmail mailbox.

    Config:
        query (str, optional): Gmail search query (default "in:inbox").
        max_results (int, optional): Max emails to fetch (default 50).
    """

    name: ClassVar[str] = "gmail"
    description: ClassVar[str] = "Fetch emails from a Gmail mailbox"
    config_fields: ClassVar[list[dict]] = [
        {"name": "query", "type": "string", "required": False,
         "description": "Gmail search query (default 'in:inbox')", "default": "in:inbox"},
        {"name": "max_results", "type": "integer", "required": False,
         "description": "Max emails to fetch (default 50)", "default": 50},
    ]
    auth_required: ClassVar[bool] = True
    rate_limit: ClassVar[str] = "250 queries per 100 seconds per user (Gmail API)"

    def fetch(self) -> list[dict[str, Any]]:
        query = self.config.get("query", "in:inbox")
        max_results = self.config.get("max_results", 50)

        # Simulated: return test emails
        results: list[dict[str, Any]] = []
        for i in range(min(max_results, 15)):
            results.append({
                "url": f"https://mail.google.com/mail/u/0/#inbox/{i}",
                "subject": f"Simulated Email #{i}: {query}",
                "from": f"sender{i}@example.com",
                "snippet": f"This is a simulated email snippet #{i} matching query '{query}'.",
                "date": f"2026-06-{25 - (i % 20):02d}T{9 + i % 12:02d}:00:00Z",
            })

        return results


# ── Auto-register ─────────────────────────────────────────────────────
ConnectorRegistry.register(GmailConnector)
