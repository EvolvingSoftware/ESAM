"""AirtableConnector — Fetches records from an Airtable base/table.

Simulated: returns test data for now, pending Airtable API integration.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from connectors.base import ConnectorBase
from connectors.registry import ConnectorRegistry

logger = logging.getLogger(__name__)


class AirtableConnector(ConnectorBase):
    """Fetch records from an Airtable base and table.

    Config:
        base_id (str, required): Airtable base ID.
        table_name (str, required): Table name.
    """

    name: ClassVar[str] = "airtable"
    description: ClassVar[str] = "Fetch records from an Airtable base/table"
    config_fields: ClassVar[list[dict]] = [
        {"name": "base_id", "type": "string", "required": True,
         "description": "Airtable base ID (e.g. 'appXXXXXXXXXXXXXX')"},
        {"name": "table_name", "type": "string", "required": True,
         "description": "Table name (e.g. 'Projects')"},
    ]
    auth_required: ClassVar[bool] = True
    rate_limit: ClassVar[str] = "5 requests per second per base (Airtable API)"

    def fetch(self) -> list[dict[str, Any]]:
        if not self.validate_config():
            raise ValueError("Missing required config: 'base_id' and 'table_name'")

        base_id = self.config.get("base_id", "")
        table_name = self.config.get("table_name", "")

        # Simulated: return test records
        results: list[dict[str, Any]] = []
        for i in range(10):
            results.append({
                "url": f"https://airtable.com/{base_id}/{table_name}/rec{i:06d}",
                "fields": {
                    "Name": f"Record #{i}",
                    "Status": ["Active", "Pending", "Completed"][i % 3],
                    "Value": i * 100,
                },
                "created_time": f"2026-06-{25 - (i % 20):02d}T{9 + i % 12:02d}:00:00Z",
            })

        return results


# ── Auto-register ─────────────────────────────────────────────────────
ConnectorRegistry.register(AirtableConnector)
