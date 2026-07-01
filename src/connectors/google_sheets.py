"""GoogleSheetsConnector — Fetches rows from a Google Sheet.

Simulated: returns test data for now, pending Google Sheets API integration.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from connectors.base import ConnectorBase
from connectors.registry import ConnectorRegistry

logger = logging.getLogger(__name__)


class GoogleSheetsConnector(ConnectorBase):
    """Fetch rows from a Google Sheet range.

    Config:
        spreadsheet_id (str, required): Google Sheets spreadsheet ID.
        range (str, required): Range in A1 notation (e.g. 'Sheet1!A1:C10').
    """

    name: ClassVar[str] = "google_sheets"
    description: ClassVar[str] = "Fetch rows from a Google Sheet range"
    config_fields: ClassVar[list[dict]] = [
        {"name": "spreadsheet_id", "type": "string", "required": True,
         "description": "Google Sheets spreadsheet ID (from the sheet URL)"},
        {"name": "range", "type": "string", "required": True,
         "description": "Range in A1 notation (e.g. 'Sheet1!A1:C10')"},
    ]
    auth_required: ClassVar[bool] = True
    rate_limit: ClassVar[str] = "100 requests per 100 seconds per project (Sheets API)"

    def fetch(self) -> list[dict[str, Any]]:
        if not self.validate_config():
            raise ValueError("Missing required config: 'spreadsheet_id' and 'range'")

        spreadsheet_id = self.config.get("spreadsheet_id", "")
        sheet_range = self.config.get("range", "")

        # Simulated: return test rows with header-based keys
        headers = ["Name", "Email", "Status", "Score"]
        results: list[dict[str, Any]] = []
        for i in range(8):
            row = {
                headers[0]: f"Person {i + 1}",
                headers[1]: f"person{i + 1}@example.com",
                headers[2]: ["Active", "Inactive", "Pending"][i % 3],
                headers[3]: (i + 1) * 10,
            }
            results.append({
                "url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit#gid=0&range=A{i + 1}:D{i + 1}",
                "row_values": row,
            })

        return results


# ── Auto-register ─────────────────────────────────────────────────────
ConnectorRegistry.register(GoogleSheetsConnector)
