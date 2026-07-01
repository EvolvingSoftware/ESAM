"""ConnectorRegistry — In-memory registry of connector classes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from connectors.base import ConnectorBase

logger = logging.getLogger(__name__)


class ConnectorRegistry:
    """Static, in-memory registry for connector classes.

    Connectors self-register on import via :meth:`register`.
    """

    _registry: dict[str, type[ConnectorBase]] = {}

    @classmethod
    def register(cls, connector_class: type[ConnectorBase]) -> None:
        """Register a connector class by its ``name`` attribute."""
        name = getattr(connector_class, "name", "")
        if not name:
            logger.warning(
                "Connector class %s has no 'name' attribute; skipping registration.",
                connector_class.__name__,
            )
            return
        cls._registry[name] = connector_class
        logger.debug("Registered connector: %s", name)

    @classmethod
    def get(cls, name: str) -> type[ConnectorBase] | None:
        """Get a connector class by name. Returns ``None`` if not found."""
        return cls._registry.get(name)

    @classmethod
    def list(cls) -> list[str]:
        """List all registered connector names."""
        return sorted(cls._registry.keys())

    @classmethod
    def create(cls, name: str, config: dict[str, Any] | None = None) -> ConnectorBase:
        """Create a connector instance by name with the given config.

        Args:
            name: Connector name registered in the registry.
            config: Configuration dict to pass to the connector.

        Returns:
            An instantiated connector.

        Raises:
            KeyError: If no connector is registered with that name.
        """
        connector_class = cls.get(name)
        if connector_class is None:
            raise KeyError(f"Unknown connector: '{name}'. Available: {cls.list()}")
        return connector_class(config or {})


# ── Convenience module-level functions ────────────────────────────────


def get_connector(name: str) -> type[ConnectorBase] | None:
    """Convenience: get a connector class by name."""
    return ConnectorRegistry.get(name)


def list_connectors() -> list[str]:
    """Convenience: list all registered connector names."""
    return ConnectorRegistry.list()
