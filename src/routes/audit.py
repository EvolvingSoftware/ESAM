#!/usr/bin/env python3
"""Audit routes — /api/audit* routes."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from audit_trail import AuditTrail
from audit_log import ensure_schema, record_event, query_events

logger = logging.getLogger(__name__)


def register(app: FastAPI):
    """Register all /api/audit* routes."""

    @app.get("/api/audit")
    def query_audit(
        agent_id: str | None = None,
        category: str | None = None,
        limit: int = Query(50, le=500),
    ):
        """Query audit trail entries."""
        return AuditTrail().query(agent_id=agent_id, category=category, limit=limit)

    @app.get("/api/audit/verify")
    def verify_audit_chain():
        """Verify audit hash chain integrity."""
        return AuditTrail().verify_chain()

    @app.get("/api/audit/export/{framework}")
    def export_compliance(framework: str = "all"):
        """Export audit data mapped to compliance frameworks."""
        valid = {"all", "eu_ai_act", "nist_ai_rmf", "iso_42001"}
        if framework not in valid:
            raise HTTPException(400, f"Framework must be one of: {', '.join(sorted(valid))}")
        return AuditTrail().export_compliance(framework)

    @app.get("/api/audit/platform")
    def query_platform_audit(
        actor: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        action: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int = Query(50, le=500),
        offset: int = Query(0, ge=0),
    ):
        """Query platform audit log (user/admin actions for compliance)."""
        events = query_events(
            actor_id=actor,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
            offset=offset,
        )
        return {"events": events, "count": len(events), "limit": limit, "offset": offset}

    @app.get("/api/audit")
    def query_audit_events(
        actor: str = "",
        resource_type: str = "",
        resource_id: str = "",
        action: str = "",
        from_date: str = "",
        to_date: str = "",
        limit: int = 50,
        offset: int = 0,
    ):
        """Query audit events with optional filters.

        All filters are optional. Events are returned sorted by created_at DESC.
        """
        events = query_events(
            actor_id=actor or None,
            resource_type=resource_type or None,
            resource_id=resource_id or None,
            action=action or None,
            from_date=from_date or None,
            to_date=to_date or None,
            limit=limit,
            offset=offset,
        )
        return {"events": events, "total": len(events), "limit": limit, "offset": offset}
