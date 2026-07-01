"""JiraConnector — Fetches issues from a Jira project.

Simulated: returns test data for now, pending Jira API integration.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from connectors.base import ConnectorBase
from connectors.registry import ConnectorRegistry

logger = logging.getLogger(__name__)


class JiraConnector(ConnectorBase):
    """Fetch issues from a Jira project.

    Config:
        project (str, required): Jira project key.
        max_results (int, optional): Max issues to fetch (default 50).
    """

    name: ClassVar[str] = "jira"
    description: ClassVar[str] = "Fetch issues from a Jira project"
    config_fields: ClassVar[list[dict]] = [
        {"name": "project", "type": "string", "required": True,
         "description": "Jira project key (e.g. 'PROJ')"},
        {"name": "max_results", "type": "integer", "required": False,
         "description": "Max issues to fetch (default 50)", "default": 50},
    ]
    auth_required: ClassVar[bool] = True
    rate_limit: ClassVar[str] = "100 requests per minute (Jira Cloud API)"

    def fetch(self) -> list[dict[str, Any]]:
        if not self.validate_config():
            raise ValueError("Missing required config: 'project'")

        project = self.config.get("project", "")
        max_results = self.config.get("max_results", 50)

        # Simulated: return test issues
        results: list[dict[str, Any]] = []
        for i in range(min(max_results, 20)):
            results.append({
                "url": f"https://{project.lower()}.atlassian.net/browse/{project}-{100 + i}",
                "key": f"{project}-{100 + i}",
                "summary": f"Simulated issue #{i}: Sample Jira issue summary",
                "status": ["To Do", "In Progress", "Done", "In Review"][i % 4],
                "assignee": f"user_{i % 5}",
                "priority": ["High", "Medium", "Low", "Critical"][i % 4],
            })

        return results


# ── Auto-register ─────────────────────────────────────────────────────
ConnectorRegistry.register(JiraConnector)
