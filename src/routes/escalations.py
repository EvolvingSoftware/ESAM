#!/usr/bin/env python3
"""Escalation routes — /api/escalations endpoints for human_escalation step handling."""

from __future__ import annotations

import json
import logging

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from agent_workflow import AgentWorkflowDB
from workflow_executor import WorkflowExecutor

logger = logging.getLogger(__name__)


class EscalationResponse(BaseModel):
    action: str  # approve, edit, reject
    text: str = ""
    responded_by: str = ""


_wf_db = AgentWorkflowDB()
_wf_executor = WorkflowExecutor()


def register(app: FastAPI):
    """Register escalation routes."""

    @app.post("/api/escalations/{escalation_id}/respond")
    def respond_to_escalation(escalation_id: str, body: EscalationResponse):
        """Respond to a pending escalation, resuming the workflow."""
        # 1. Find escalation
        escalation = _wf_db.get_escalation(escalation_id)
        if not escalation:
            raise HTTPException(404, f"Escalation {escalation_id} not found")
        if escalation["status"] != "pending":
            raise HTTPException(400, f"Escalation {escalation_id} is not pending (status: {escalation['status']})")

        # 2. Validate action
        valid_actions = {"approve", "edit", "reject"}
        if body.action not in valid_actions:
            raise HTTPException(400, f"Invalid action '{body.action}'. Must be one of: {', '.join(sorted(valid_actions))}")

        # 3. Mark as responded
        updated = _wf_db.respond_to_escalation(
            escalation_id=escalation_id,
            response_action=body.action,
            response_text=body.text,
            responded_by=body.responded_by,
        )
        if not updated:
            raise HTTPException(409, f"Escalation {escalation_id} could not be updated (concurrent modification?)")

        # 4. Call executor.resume_from_escalation()
        run_id = escalation["run_id"]
        result = _wf_executor.resume_from_escalation(run_id, escalation_id)

        # 5. Return updated run status
        return result

    @app.get("/api/escalations")
    def list_pending_escalations(status: str = Query("pending", description="Filter by status")):
        """List escalations with run context."""
        escalations = _wf_db.list_escalations(status=status)
        # Enrich with run context
        enriched = []
        for esc in escalations:
            run = _wf_db.get_run(esc["run_id"])
            esc_dict = dict(esc)
            esc_dict["run"] = run
            enriched.append(esc_dict)
        return {"escalations": enriched}

    @app.get("/api/escalations/{escalation_id}")
    def get_escalation(escalation_id: str):
        """Get full escalation details with context."""
        escalation = _wf_db.get_escalation(escalation_id)
        if not escalation:
            raise HTTPException(404, f"Escalation {escalation_id} not found")
        # Enrich with run info
        run = _wf_db.get_run(escalation["run_id"])
        result = dict(escalation)
        result["run"] = run
        return result