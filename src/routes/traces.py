#!/usr/bin/env python3
"""Trace routes — /api/workflow/traces/* routes."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, HTTPException

from tracing import TraceStore

logger = logging.getLogger(__name__)


def register(app: FastAPI):
    """Register all /api/workflow/traces/* routes."""
    _trace_store = TraceStore()

    @app.get("/api/workflow/traces/{run_id}")
    def get_run_traces(run_id: str):
        """Get trace tree for a run."""
        return _trace_store.get_run_trace_tree(run_id)

    @app.get("/api/workflow/traces/{run_id}/spans")
    def get_run_trace_spans(run_id: str):
        """Get flat list of trace spans for a run."""
        return {"spans": _trace_store.get_run_traces(run_id)}

    @app.get("/api/workflow/traces/tree/{trace_id}")
    def get_trace_tree(trace_id: str):
        """Get nested trace tree by trace_id."""
        return _trace_store.get_trace_tree(trace_id)
