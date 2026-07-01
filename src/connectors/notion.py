"""NotionConnector — Fetches pages from a Notion database.

Simulated: returns test data for now, pending Notion API integration.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from connectors.base import ConnectorBase
from connectors.registry import ConnectorRegistry

logger = logging.getLogger(__name__)


class NotionConnector(ConnectorBase):
    """Fetch pages from a Notion database.

    Config:
        database_id (str, required): Notion database ID.
        filter (str, optional): Filter expression (JSON string).
    """

    name: ClassVar[str] = "notion"
    description: ClassVar[str] = "Fetch pages from a Notion database"
    config_fields: ClassVar[list[dict]] = [
        {"name": "database_id", "type": "string", "required": True,
         "description": "Notion database ID (32-char hex)"},
        {"name": "filter", "type": "string", "required": False,
         "description": "Optional filter expression (JSON)"},
    ]
    auth_required: ClassVar[bool] = True
    rate_limit: ClassVar[str] = "3 requests per second (Notion API)"

    def fetch(self) -> list[dict[str, Any]]:
        if not self.validate_config():
            raise ValueError("Missing required config: 'database_id'")

        database_id = self.config.get("database_id", "")

        # Simulated: return test pages
        results: list[dict[str, Any]] = []
        for i in range(5):
            results.append({
                "url": f"https://notion.so/{database_id}?p={i}",
                "title": f"Simulated Notion Page #{i}",
                "content": f"This is the content of simulated notion page #{i} in database {database_id}.",
                "last_edited": f"2026-06-{25 - i:02d}T10:00:00Z",
            })

        return results


# ── Auto-register ─────────────────────────────────────────────────────
ConnectorRegistry.register(NotionConnector)
