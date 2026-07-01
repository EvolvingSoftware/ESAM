"""ConnectorBase — Abstract base for all data-source connectors."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from fetcher.engine import FetcherEngine

logger = logging.getLogger(__name__)


class ConnectorBase(ABC):
    """Base class for all connectors.

    Subclasses must implement :meth:`fetch` and set :attr:`name`.
    Uses :class:`FetcherEngine` internally for all HTTP calls.
    """

    # Subclasses set these as class-level metadata
    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    config_fields: ClassVar[list[dict]] = []   # schema for config validation
    auth_required: ClassVar[bool] = False
    rate_limit: ClassVar[str] = ""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = config or {}
        self._fetcher = FetcherEngine()

    # ── Required override ─────────────────────────────────────────────

    @abstractmethod
    def fetch(self) -> list[dict[str, Any]]:
        """Fetch data from the source.

        Returns a list of normalized dict records.
        """
        ...

    # ── Config helpers ────────────────────────────────────────────────

    def validate_config(self) -> bool:
        """Validate that all required config fields are present."""
        for field in self.config_fields:
            key = field.get("name", "")
            required = field.get("required", False)
            if required and key not in self.config:
                logger.warning(
                    "Missing required config field '%s' for connector '%s'",
                    key, self.name,
                )
                return False
        return True

    @classmethod
    def get_config_schema(cls) -> dict[str, Any]:
        """Return the config schema for this connector."""
        return {
            "name": cls.name,
            "description": cls.description,
            "config_fields": cls.config_fields,
            "auth_required": cls.auth_required,
            "rate_limit": cls.rate_limit,
        }

    # ── HTTP helpers ──────────────────────────────────────────────────

    def _fetch_json(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        auth_ref: str | None = None,
    ) -> Any:
        """Fetch a URL and parse the response as JSON.

        Args:
            url: The URL to fetch.
            headers: Optional extra HTTP headers.
            auth_ref: Optional credential reference for auth.

        Returns:
            Parsed JSON data (dict or list).

        Raises:
            RuntimeError: If the fetch fails or returns non-JSON.
        """
        result = self._fetcher.fetch(
            url=url,
            headers=headers,
            auth_ref=auth_ref,
        )

        if result.get("error"):
            raise RuntimeError(
                f"Fetch error for {url}: {result['error']}"
            )

        if result["status_code"] not in (200, 301, 302):
            raise RuntimeError(
                f"HTTP {result['status_code']} for {url}: "
                f"{result['body_text'][:500]}"
            )

        try:
            return json.loads(result["body_text"])
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"JSON decode error for {url}: {e}"
            ) from e
