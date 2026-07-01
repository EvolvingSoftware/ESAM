#!/usr/bin/env python3
"""Tool registry routes — serving tool definitions from tools/registry.yaml."""

from __future__ import annotations

import os
import logging
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException

logger = logging.getLogger(__name__)

_TOOLS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "tools")
_REGISTRY_PATH = os.path.join(_TOOLS_DIR, "registry.yaml")


def _load_registry() -> dict[str, Any]:
    """Load the tool registry from YAML and return the tools dict."""
    if not os.path.exists(_REGISTRY_PATH):
        logger.warning("Tool registry not found at %s", _REGISTRY_PATH)
        return {}
    with open(_REGISTRY_PATH, "r") as f:
        data = yaml.safe_load(f)
    return (data or {}).get("tools", {})


def register(app: FastAPI) -> None:
    """Register tool registry routes."""

    @app.get("/api/tools")
    def list_tools():
        """Return all registered tools with their metadata."""
        tools = _load_registry()
        return tools

    @app.get("/api/tools/{tool_name}")
    def get_tool(tool_name: str):
        """Return a single tool definition by name."""
        tools = _load_registry()
        tool = tools.get(tool_name)
        if not tool:
            raise HTTPException(404, f"Tool '{tool_name}' not found")
        return tool
