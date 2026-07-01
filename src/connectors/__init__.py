"""Connector SDK — Pluggable data source connectors.

Provides ConnectorBase, ConnectorRegistry, and 10 gold-standard
connectors for common data sources.
"""

from __future__ import annotations

from connectors.base import ConnectorBase
from connectors.registry import ConnectorRegistry, get_connector, list_connectors

# ── Auto-register all built-in connectors ──────────────────────────────
# Each import triggers the module-level registration call.
from connectors import (  # isort: skip # noqa: F401
    reddit as _reddit,
    hackernews as _hn,
    slack as _slack,
    notion as _notion,
    github as _github,
    gmail as _gmail,
    twitter_x as _twx,
    airtable as _airtable,
    google_sheets as _gsheets,
    jira as _jira,
)

__all__ = [
    "ConnectorBase",
    "ConnectorRegistry",
    "get_connector",
    "list_connectors",
]
