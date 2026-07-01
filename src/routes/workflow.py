#!/usr/bin/env python3
"""Workflow routes — /api/workflow/run, /api/workflow/runs, replay, and general workflow routes."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from starlette.responses import JSONResponse

from database import get_connection
from workflow_executor import WorkflowExecutor
from replay import ReplayEngine
from yaml_pipeline import sync_agent_to_yaml
from audit_log import record_event
from job_queue import get_worker
from credential_store import CredentialStore

logger = logging.getLogger(__name__)


def register(app: FastAPI):
    """Register workflow execution, replay, and general workflow routes."""
    _wf_executor = WorkflowExecutor()
    _replay_engine = ReplayEngine()

    # ── Workflow Execution ──

    @app.post("/api/workflow/run/{agent_id}")
    def execute_workflow(agent_id: str, data: dict = {}):
        """Execute a workflow in the background using the job queue.

        Returns immediately with a 202 Accepted and a job_id for tracking.
        The actual workflow execution runs asynchronously.
        """
        input_ctx = data.get("input_context", {})
        idempotency_key = data.get("idempotency_key", "")

        # Validate agent exists
        agent = _wf_executor.db.get_agent(agent_id)
        if not agent:
            raise HTTPException(404, f"Agent {agent_id} not found")

        worker = get_worker(max_workers=2)
        job = worker.submit(
            job_type="workflow_run",
            agent_id=agent_id,
            input_json=json.dumps(input_ctx),
            idempotency_key=idempotency_key,
            timeout_s=data.get("timeout_s", 300),
        )

        return JSONResponse(
            status_code=202,
            content={
                "job_id": job["id"],
                "agent_id": agent_id,
                "status": job["status"],
                "created_at": job["created_at"],
            },
        )

    # ── Workflow Runs ──

    @app.get("/api/workflow/runs/{agent_id}")
    def list_agent_runs(agent_id: str, limit: int = 10):
        """List runs for an agent."""
        return {"runs": _wf_executor.db.list_runs(agent_id, limit)}

    @app.get("/api/workflow/runs/detail/{run_id}")
    def get_run_detail(run_id: str):
        """Get run details with step logs."""
        run = _wf_executor.db.get_run(run_id)
        if not run:
            raise HTTPException(404, "Run not found")
        logs = _wf_executor.db.list_step_logs(run_id)
        return {"run": run, "step_logs": logs}

    # ── Execution Replay (Issue #10) ────────────────────────────────────

    @app.get("/api/workflow/runs/{run_id}/replay")
    def get_replay_data(run_id: str):
        """Get full replay data for a run.

        Returns run metadata, step-by-step prompt/output, trace tree, and state.
        """
        data = _replay_engine.get_replay_data(run_id)
        if "error" in data:
            raise HTTPException(404, data["error"])
        return data

    @app.get("/api/workflow/runs/{run_id}/steps/{step_index}")
    def get_step_state(run_id: str, step_index: int):
        """Get workflow state at a specific step index.

        Returns what the workflow looked like BEFORE this step executed.
        """
        data = _replay_engine.get_step_at_index(run_id, step_index)
        if "error" in data:
            raise HTTPException(404, data["error"])
        return data

    @app.get("/api/workflow/runs/compare/{run_a}/{run_b}")
    def compare_runs(run_a: str, run_b: str):
        """Compare two runs of the same agent.

        Returns step-by-step comparison highlighting prompt/output differences,
        cost diff, and token diff.
        """
        data = _replay_engine.compare_runs(run_a, run_b)
        if "error" in data:
            raise HTTPException(400, data["error"])
        return data

    @app.get("/replay/{run_id}")
    def replay_viewer(run_id: str):
        """Simple HTML page showing replay data."""
        data = _replay_engine.get_replay_data(run_id)
        if "error" in data:
            raise HTTPException(404, data["error"])
        return data

    # ── Engine-level Agent CRUD ──

    @app.get("/api/workflow/agents")
    def list_agents():
        """List all workflow agents."""
        return {"agents": _wf_executor.db.list_agents()}

    @app.post("/api/workflow/agents")
    def create_agent(data: dict):
        """Create a new workflow agent."""
        name = data.get("name", "")
        desc = data.get("description", "")
        if not name:
            raise HTTPException(400, "name is required")
        result = _wf_executor.db.create_agent(name, desc)
        sync_agent_to_yaml(result["id"])
        record_event(
            actor_id="system", actor_type="api",
            action="create", resource_type="agent",
            resource_id=result["id"],
            new_state={"name": name, "description": desc},
            entity_id="",
        )
        return result

    @app.get("/api/workflow/agents/{agent_id}")
    def get_agent(agent_id: str):
        """Get workflow agent details with full graph."""
        agent = _wf_executor.db.get_agent(agent_id)
        if not agent:
            raise HTTPException(404, "Agent not found")
        return _wf_executor.db.get_workflow_graph(agent_id)

    @app.put("/api/workflow/agents/{agent_id}")
    def update_agent(agent_id: str, data: dict):
        """Update agent properties."""
        ok = _wf_executor.db.update_agent(agent_id, **data)
        sync_agent_to_yaml(agent_id)
        record_event(
            actor_id="system", actor_type="api",
            action="update", resource_type="agent",
            resource_id=agent_id,
            new_state=data,
            entity_id="",
        )
        return ok

    @app.delete("/api/workflow/agents/{agent_id}")
    def delete_agent(agent_id: str):
        """Delete a workflow agent."""
        ok = _wf_executor.db.delete_agent(agent_id)
        if not ok:
            raise HTTPException(404, "Agent not found")
        sync_agent_to_yaml(agent_id)
        record_event(
            actor_id="system", actor_type="api",
            action="delete", resource_type="agent",
            resource_id=agent_id,
            entity_id="",
        )
        return {"deleted": True}

    # ── Steps ──

    @app.post("/api/workflow/agents/{agent_id}/steps")
    def create_step(agent_id: str, data: dict):
        """Create a workflow step."""
        result = _wf_executor.db.create_step(
            agent_id=agent_id,
            step_type=data.get("step_type", "llm_call"),
            label=data.get("label", ""),
            prompt_template=data.get("prompt_template", ""),
            tools_json=json.dumps(data.get("tools", [])),
            model_name=data.get("model_name", ""),
            loop_config_json=json.dumps(data.get("loop_config", {})),
            position_x=data.get("position_x", 0),
            position_y=data.get("position_y", 0),
        )
        sync_agent_to_yaml(agent_id)
        return result

    @app.get("/api/workflow/agents/{agent_id}/steps")
    def list_steps(agent_id: str):
        """List all steps for an agent."""
        return {"steps": _wf_executor.db.list_steps(agent_id)}

    @app.put("/api/workflow/steps/{step_id}")
    def update_step(step_id: str, data: dict):
        """Update a step."""
        result = _wf_executor.db.update_step(step_id, **data)
        if result:
            sync_agent_to_yaml(result["agent_id"])
        return result

    @app.delete("/api/workflow/steps/{step_id}")
    def delete_step(step_id: str):
        """Delete a step."""
        step = _wf_executor.db.get_step(step_id)
        agent_id = step["agent_id"] if step else None
        ok = _wf_executor.db.delete_step(step_id)
        if not ok:
            raise HTTPException(404, "Step not found")
        if agent_id:
            sync_agent_to_yaml(agent_id)
        return {"deleted": True}

    # ── Connections ──

    @app.post("/api/workflow/agents/{agent_id}/connections")
    def create_connection(agent_id: str, data: dict):
        """Create a connection between steps."""
        from_step = data.get("from_step_id", "")
        to_step = data.get("to_step_id", "")
        if not from_step or not to_step:
            raise HTTPException(400, "from_step_id and to_step_id are required")
        result = _wf_executor.db.create_connection(
            agent_id=agent_id,
            from_step_id=from_step,
            to_step_id=to_step,
            label=data.get("label", ""),
            condition_expr=data.get("condition_expr", ""),
        )
        sync_agent_to_yaml(agent_id)
        return result

    @app.get("/api/workflow/agents/{agent_id}/connections")
    def list_connections(agent_id: str):
        """List connections for an agent."""
        return {"connections": _wf_executor.db.list_connections(agent_id)}

    @app.delete("/api/workflow/connections/{conn_id}")
    def delete_connection(conn_id: str):
        """Delete a connection."""
        conn = get_connection()
        row = conn.execute("SELECT agent_id FROM wf_step_connections WHERE id = ?", (conn_id,)).fetchone()
        agent_id = row[0] if row else None
        ok = _wf_executor.db.delete_connection(conn_id)
        if not ok:
            raise HTTPException(404, "Connection not found")
        if agent_id:
            sync_agent_to_yaml(agent_id)
        return {"deleted": True}

    # ── Credentials ──

    @app.post("/api/workflow/agents/{agent_id}/credentials")
    def store_credential(agent_id: str, data: dict):
        """Store an encrypted credential for an agent."""
        key = data.get("key", "")
        value = data.get("value", "")
        if not key or not value:
            raise HTTPException(400, "key and value are required")
        store = CredentialStore()
        encrypted = store.encrypt(value)
        result = store.db.create_credential(
            agent_id=agent_id,
            credential_key=key,
            encrypted_value=encrypted,
            scope_step_id=data.get("scope_step_id"),
        )
        sync_agent_to_yaml(agent_id)
        return result

    @app.get("/api/workflow/agents/{agent_id}/credentials")
    def list_credentials(agent_id: str):
        """List credentials (values masked)."""
        return {"credentials": _wf_executor.db.list_credentials(agent_id)}

    @app.delete("/api/workflow/credentials/{cred_id}")
    def delete_credential(cred_id: str):
        """Delete a credential."""
        cred = _wf_executor.db.get_credential(cred_id)
        agent_id = cred["agent_id"] if cred else None
        ok = _wf_executor.db.delete_credential(cred_id)
        if not ok:
            raise HTTPException(404, "Credential not found")
        if agent_id:
            sync_agent_to_yaml(agent_id)
        return {"deleted": True}
