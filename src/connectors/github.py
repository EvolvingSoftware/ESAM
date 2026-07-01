"""GitHubConnector — Fetches issues/PRs from a GitHub repository.

Simulated: returns test data for now, pending GitHub API integration.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from connectors.base import ConnectorBase
from connectors.registry import ConnectorRegistry

logger = logging.getLogger(__name__)


class GitHubConnector(ConnectorBase):
    """Fetch issues and pull requests from a GitHub repository.

    Config:
        repo (str, required): Repository in 'owner/name' format.
        per_page (int, optional): Items per page (default 30).
    """

    name: ClassVar[str] = "github"
    description: ClassVar[str] = "Fetch issues and PRs from a GitHub repository"
    config_fields: ClassVar[list[dict]] = [
        {"name": "repo", "type": "string", "required": True,
         "description": "Repository (e.g. 'nousresearch/hermes-agent')"},
        {"name": "per_page", "type": "integer", "required": False,
         "description": "Items per page (default 30)", "default": 30},
    ]
    auth_required: ClassVar[bool] = True
    rate_limit: ClassVar[str] = "60 requests per hour (unauthenticated); 5000/hr (authenticated)"

    def fetch(self) -> list[dict[str, Any]]:
        if not self.validate_config():
            raise ValueError("Missing required config: 'repo'")

        repo = self.config.get("repo", "")
        per_page = self.config.get("per_page", 30)

        # Simulated: return test issues
        results: list[dict[str, Any]] = []
        for i in range(min(per_page, 20)):
            results.append({
                "url": f"https://github.com/{repo}/issues/{i + 1}",
                "title": f"Simulated issue #{i + 1}: Sample issue title",
                "state": "open" if i % 3 != 0 else "closed",
                "user": f"contributor_{i}",
                "created_at": f"2026-06-{25 - (i % 20):02d}T08:00:00Z",
            })

        return results


# ── Auto-register ─────────────────────────────────────────────────────
ConnectorRegistry.register(GitHubConnector)
