#!/usr/bin/env python3
"""Workflow agents routes — /api/workflow/agents/* (agent CRUD, steps, connections, credentials)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Depends, Request, Body

from database import get_connection
from agent_workflow import AgentWorkflowDB
from credential_store import CredentialStore
from workflow_executor import WorkflowExecutor
from yaml_pipeline import sync_agent_to_yaml
from audit_log import record_event

logger = logging.getLogger(__name__)


def register(app: FastAPI):
    """Register all /api/workflow/agents/* routes."""
    db = AgentWorkflowDB()

    # ── Audit Helper ─────────────────────────────────────────────────────

    def audit_state_change(
        request,
        action: str,
        resource_type: str,
        resource_id: str,
        old_state: dict | None = None,
        new_state: dict | None = None,
    ):
        """Convenience wrapper that extracts actor from request and records event."""
        user = getattr(request.state, "current_user", {}) or {}
        actor_id = user.get("id", "unknown") if isinstance(user, dict) else "unknown"
        ip = request.client.host if request.client else ""
        ua = request.headers.get("User-Agent", "")
        entity_id = getattr(request.state, "current_entity_id", "") or actor_id
        record_event(
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            old_state=old_state,
            new_state=new_state,
            ip_address=ip,
            user_agent=ua,
            entity_id=entity_id,
        )

    # ── Workflow Designer — Agents ──────────────────────────────────────

    @app.get("/api/workflow/agents")
    def wf_list_agents():
        """List all workflow agents."""
        return db.list_agents()

    @app.post("/api/workflow/agents")
    def wf_create_agent(request: Request, data: dict):
        """Create a new workflow agent."""
        result = db.create_agent(
            name=data.get("name", "New Agent"),
            description=data.get("description", ""),
        )
        # Create default start step
        db.create_step(result["id"], step_type="input", label="Start", position_x=100, position_y=300)
        sync_agent_to_yaml(result["id"])
        audit_state_change(request, "create", "agent", result["id"], None, result)
        return result

    @app.get("/api/workflow/agents/{agent_id}")
    def wf_get_agent(agent_id: str):
        """Get a single workflow agent with its full graph."""
        graph = db.get_workflow_graph(agent_id)
        if not graph or not graph.get("agent"):
            raise HTTPException(404, "Agent not found")
        return graph

    @app.put("/api/workflow/agents/{agent_id}")
    def wf_update_agent(request: Request, agent_id: str, data: dict):
        """Update workflow agent fields."""
        result = db.update_agent(agent_id, **data)
        if not result:
            raise HTTPException(404, "Agent not found")
        sync_agent_to_yaml(agent_id)
        audit_state_change(request, "update", "agent", agent_id, None, result)
        return result

    @app.delete("/api/workflow/agents/{agent_id}")
    def wf_delete_agent(request: Request, agent_id: str):
        """Delete a workflow agent and all its data."""
        ok = db.delete_agent(agent_id)
        if not ok:
            raise HTTPException(404, "Agent not found")
        sync_agent_to_yaml(agent_id)
        audit_state_change(request, "delete", "agent", agent_id)
        return {"status": "deleted"}

    @app.post("/api/workflow/agents/{agent_id}/clone")
    def wf_clone_agent(request: Request, agent_id: str, data: dict):
        """Deep clone an agent workflow."""
        result = db.clone_agent(agent_id, data.get("new_name"))
        if not result:
            raise HTTPException(404, "Agent not found")
        audit_state_change(request, "create", "agent", result["id"], None, result)
        return result

    # ── Workflow Designer — Steps ──────────────────────────────────────

    @app.get("/api/workflow/agents/{agent_id}/steps")
    def wf_list_steps(agent_id: str):
        """List all steps for an agent workflow."""
        return db.list_steps(agent_id)

    @app.post("/api/workflow/agents/{agent_id}/steps")
    async def wf_create_step(request: Request, agent_id: str):
        """Create a new workflow step."""
        body = await request.json()
        data = body if body else {}
        result = db.create_step(
            agent_id=agent_id,
            step_type=data.get("step_type", "llm_call"),
            label=data.get("label", "New Step"),
            prompt_template=data.get("prompt_template", ""),
            tools_json=json.dumps(data.get("tools", [])),
            model_name=data.get("model_name", ""),
            loop_config_json=json.dumps(data.get("loop_config", {})),
            authority_json=json.dumps(data.get("authority_config", data.get("authority", {}))),
            position_x=data.get("position_x", 0),
            position_y=data.get("position_y", 0),
        )
        sync_agent_to_yaml(agent_id)
        audit_state_change(request, "create", "step", result["id"], None, result)
        return result

    @app.put("/api/workflow/agents/{agent_id}/steps/{step_id}")
    def wf_update_step(request: Request, agent_id: str, step_id: str, data: dict):
        """Update a workflow step."""
        # Handle JSON fields that come as objects from frontend
        if "tools" in data and isinstance(data["tools"], (list, dict)):
            data["tools_json"] = json.dumps(data["tools"])
            del data["tools"]
        if "loop_config" in data and isinstance(data["loop_config"], dict):
            data["loop_config_json"] = json.dumps(data["loop_config"])
            del data["loop_config"]
        if "authority" in data and isinstance(data["authority"], dict):
            data["authority_json"] = json.dumps(data["authority"])
            del data["authority"]
        if "authority_config" in data and isinstance(data["authority_config"], dict):
            data["authority_json"] = json.dumps(data["authority_config"])
            del data["authority_config"]
        result = db.update_step(step_id, **data)
        if not result:
            raise HTTPException(404, "Step not found")
        sync_agent_to_yaml(agent_id)
        audit_state_change(request, "update", "step", step_id, None, result)
        return result

    @app.delete("/api/workflow/agents/{agent_id}/steps/{step_id}")
    def wf_delete_step(request: Request, agent_id: str, step_id: str):
        """Delete a workflow step and its connections."""
        ok = db.delete_step(step_id)
        if not ok:
            raise HTTPException(404, "Step not found")
        sync_agent_to_yaml(agent_id)
        audit_state_change(request, "delete", "step", step_id)
        return {"status": "deleted"}

    @app.put("/api/workflow/agents/{agent_id}/steps/reorder")
    def wf_reorder_steps(agent_id: str, data: dict):
        """Bulk reorder/reposition steps. Expects {steps: [{id, position_x, position_y}, ...]}"""
        step_ids = [s["id"] for s in data.get("steps", [])]
        if step_ids:
            db.reorder_steps(agent_id, step_ids)
        # Update individual positions
        for s in data.get("steps", []):
            if "position_x" in s or "position_y" in s:
                db.update_step(s["id"], position_x=s.get("position_x", 0), position_y=s.get("position_y", 0))
        return {"status": "ok"}

    # ── Workflow Designer — Connections ─────────────────────────────────

    @app.get("/api/workflow/agents/{agent_id}/connections")
    def wf_list_connections(agent_id: str):
        """List all connections in a workflow."""
        return db.list_connections(agent_id)

    @app.post("/api/workflow/agents/{agent_id}/connections")
    def wf_create_connection(request: Request, agent_id: str, data: dict):
        """Create a connection between two steps."""
        result = db.create_connection(
            agent_id=agent_id,
            from_step_id=data["from_step_id"],
            to_step_id=data["to_step_id"],
            label=data.get("label", ""),
            condition_expr=data.get("condition_expr", ""),
        )
        sync_agent_to_yaml(agent_id)
        audit_state_change(request, "create", "connection", result["id"])
        return result

    @app.delete("/api/workflow/agents/{agent_id}/connections/{conn_id}")
    def wf_delete_connection(request: Request, agent_id: str, conn_id: str):
        """Delete a connection."""
        ok = db.delete_connection(conn_id)
        if not ok:
            raise HTTPException(404, "Connection not found")
        sync_agent_to_yaml(agent_id)
        audit_state_change(request, "delete", "connection", conn_id)
        return {"status": "deleted"}

    # ── Workflow Designer — Credentials ────────────────────────────────

    @app.get("/api/workflow/agents/{agent_id}/credentials")
    def wf_list_credentials(agent_id: str):
        """List credentials for an agent (values masked)."""
        store = CredentialStore(agent_id)
        return store.list()

    @app.post("/api/workflow/agents/{agent_id}/credentials")
    def wf_create_credential(request: Request, agent_id: str, data: dict):
        """Store a credential. Body: {key, value, scope_step_id?}"""
        store = CredentialStore(agent_id)
        result = store.create(
            credential_key=data["key"],
            plaintext_value=data["value"],
            scope_step_id=data.get("scope_step_id"),
        )
        sync_agent_to_yaml(agent_id)
        audit_state_change(request, "create", "credential", result["id"],
                           None, {"key": data["key"], "agent_id": agent_id})
        return result

    @app.delete("/api/workflow/agents/{agent_id}/credentials/{cred_id}")
    def wf_delete_credential(request: Request, agent_id: str, cred_id: str):
        """Delete a credential."""
        store = CredentialStore(agent_id)
        ok = store.delete(cred_id)
        if not ok:
            raise HTTPException(404, "Credential not found")
        sync_agent_to_yaml(agent_id)
        audit_state_change(request, "delete", "credential", cred_id)
        return {"status": "deleted"}

    @app.post("/api/workflow/agents/{agent_id}/credentials/{cred_id}/test")
    def wf_test_credential(agent_id: str, cred_id: str):
        """Test that a credential decrypts successfully."""
        store = CredentialStore(agent_id)
        ok = store.test(cred_id)
        return {"valid": ok}

    # ── Workflow Designer — Runs & Execution ───────────────────────────

    @app.get("/api/workflow/agents/{agent_id}/runs")
    def wf_list_runs(agent_id: str, limit: int = 50):
        """List runs for an agent."""
        return db.list_runs(agent_id, limit=limit)

    @app.get("/api/workflow/agents/{agent_id}/runs/{run_id}")
    def wf_get_run(agent_id: str, run_id: str):
        """Get a run with all its step logs."""
        run = db.get_run(run_id)
        if not run:
            raise HTTPException(404, "Run not found")
        logs = db.list_step_logs(run_id)
        run["step_logs"] = logs
        return run

    @app.post("/api/workflow/agents/{agent_id}/run")
    def wf_execute_agent(request: Request, agent_id: str, data: dict = {}):
        """Execute an agent workflow. Creates a run and reports step execution.

        For the hackathon demo, this simulates execution by recording step logs
        with placeholder timing/cost data. Real execution (calling LLMs) will
        be added in a future phase.
        """
        import time
        graph = db.get_workflow_graph(agent_id)
        if not graph or not graph.get("agent"):
            raise HTTPException(404, "Agent not found")

        steps = graph.get("steps", [])
        connections = graph.get("connections", [])

        # Create the run
        run = db.create_run(
            agent_id=agent_id,
            trigger="manual",
            input_context=json.dumps(data.get("input", {})),
        )
        db.update_run_status(run["id"], "running", started_at=datetime.now(timezone.utc).isoformat())

        total_tokens = 0
        total_cost = 0
        step_count = 0

        # Find start step (step_type = "input" or first step)
        step_map = {s["id"]: s for s in steps}
        conn_map = {}
        for c in connections:
            if c["from_step_id"] not in conn_map:
                conn_map[c["from_step_id"]] = []
            conn_map[c["from_step_id"]].append(c)

        # Execute steps in order following connections
        current_step = None
        # Find the input step or first orphan step
        for s in steps:
            if s["step_type"] == "input":
                current_step = s
                break
        if not current_step and steps:
            current_step = steps[0]

        visited = set()
        while current_step and current_step["id"] not in visited:
            visited.add(current_step["id"])
            step_count += 1

            # Simulate execution
            sim_tokens_in = 150 + hash(current_step["id"]) % 350
            sim_tokens_out = 50 + hash(current_step["id"] + "out") % 200
            sim_cost = (sim_tokens_in + sim_tokens_out) * 5 // 100000  # ~$0.005 per 1K tokens
            total_tokens += sim_tokens_in + sim_tokens_out
            total_cost += sim_cost

            log = db.create_step_log(run["id"], current_step["id"], sequence=step_count - 1)
            db.update_step_log(log["id"],
                status="success",
                input_data=json.dumps(data.get("input", {})),
                prompt_sent=current_step.get("prompt_template", ""),
                output_data=json.dumps({"result": f"Simulated output for step: {current_step.get('label', '')}"}),
                tokens_input=sim_tokens_in,
                tokens_output=sim_tokens_out,
                cost_cents=sim_cost,
                model_used=current_step.get("model_name", "simulated") or "simulated",
                started_at=datetime.now(timezone.utc).isoformat(),
                completed_at=datetime.now(timezone.utc).isoformat(),
                reasoning_trace=json.dumps({"simulated": True, "step_type": current_step.get("step_type", "")}),
            )

            # Follow connection to next step
            next_steps = conn_map.get(current_step["id"], [])
            if next_steps:
                next_id = next_steps[0]["to_step_id"]
                current_step = step_map.get(next_id)
            else:
                current_step = None

        # Update run
        completed = datetime.now(timezone.utc).isoformat()
        db.update_run_status(run["id"], "completed",
            completed_at=completed,
            total_cost_cents=total_cost,
            total_tokens=total_tokens,
            total_steps=step_count,
        )

        # Update agent stats
        db.update_agent(agent_id, total_runs=db.get_agent(agent_id)["total_runs"] + 1)

        result = {
            "run_id": run["id"],
            "status": "completed",
            "steps_executed": step_count,
            "total_tokens": total_tokens,
            "total_cost_cents": total_cost,
        }
        audit_state_change(request, "execute", "workflow_run", run["id"],
                           None, {"agent_id": agent_id, "status": "completed"})
        return result

    @app.post("/api/workflow/agents/{agent_id}/runs/{run_id}/cancel")
    def wf_cancel_run(request: Request, agent_id: str, run_id: str):
        """Cancel a running agent."""
        run = db.get_run(run_id)
        if not run:
            raise HTTPException(404, "Run not found")
        db.update_run_status(run_id, "cancelled", completed_at=datetime.now(timezone.utc).isoformat())
        audit_state_change(request, "stop", "workflow_run", run_id)
        return {"status": "cancelled"}

    # ── Global Dashboard ───────────────────────────────────────────────

    @app.get("/api/workflow/dashboard/summary")
    def wf_dashboard_summary():
        """Global metrics across all workflow agents."""
        agents = db.list_agents()
        total_cost = sum(a.get("total_cost_cents", 0) for a in agents)
        total_runs = sum(a.get("total_runs", 0) for a in agents)
        active = sum(1 for a in agents if a.get("status") == "active")
        return {
            "total_agents": len(agents),
            "active_agents": active,
            "total_runs": total_runs,
            "total_cost_cents": total_cost,
            "draft_agents": sum(1 for a in agents if a.get("status") == "draft"),
        }

    @app.get("/api/workflow/dashboard/recent-runs")
    def wf_recent_runs(limit: int = 20):
        """Latest runs across all agents."""
        all_agents = db.list_agents()
        all_runs = []
        for a in all_agents:
            runs = db.list_runs(a["id"], limit=5)
            for r in runs:
                r["agent_name"] = a.get("name", "Unknown")
                all_runs.append(r)
        all_runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return all_runs[:limit]

    # ── Cost Estimation ─────────────────────────────────────────────────

    MODEL_PRICING = {
        "gpt-4": {"input_per_1k": 0.03, "output_per_1k": 0.06},
        "gpt-4-turbo": {"input_per_1k": 0.01, "output_per_1k": 0.03},
        "gpt-3.5-turbo": {"input_per_1k": 0.0015, "output_per_1k": 0.002},
        "claude-3-opus": {"input_per_1k": 0.015, "output_per_1k": 0.075},
        "claude-3-sonnet": {"input_per_1k": 0.003, "output_per_1k": 0.015},
        "claude-3-haiku": {"input_per_1k": 0.00025, "output_per_1k": 0.00125},
        "gemma-4-12b": {"input_per_1k": 0.0001, "output_per_1k": 0.0001},
        "deepseek-v4-flash": {"input_per_1k": 0.0003, "output_per_1k": 0.0006},
    }
    DEFAULT_PRICING = {"input_per_1k": 0.01, "output_per_1k": 0.03}

    @app.get("/api/workflow/agents/{agent_id}/estimate-cost")
    def estimate_agent_cost(agent_id: str):
        """Estimate cost for all steps in an agent's workflow. No state mutation."""
        from agent_workflow import AgentWorkflowDB
        _local_db = AgentWorkflowDB()
        steps = _local_db.list_steps(agent_id)
        estimates = []
        total_est_cents = 0
        for step in steps:
            st = step.get("step_type", "llm_call")
            if st == "llm_call":
                prompt = step.get("prompt_template", "")
                model = step.get("model_name", "gemma-4-12b")
                pricing = MODEL_PRICING.get(model, DEFAULT_PRICING)
                tokens_in = max(100, len(prompt) // 4)
                tokens_out = 200  # sensible default
                cost_cents = (tokens_in * pricing["input_per_1k"] + tokens_out * pricing["output_per_1k"]) / 10
                estimates.append({
                    "step_id": step["id"],
                    "label": step.get("label", ""),
                    "step_type": st,
                    "model": model,
                    "estimated_tokens_in": tokens_in,
                    "estimated_tokens_out": tokens_out,
                    "estimated_cost_cents": round(cost_cents, 4),
                })
                total_est_cents += cost_cents
            elif st in ("tool_call", "condition", "loop", "subworkflow", "human_escalation"):
                estimates.append({
                    "step_id": step["id"],
                    "label": step.get("label", ""),
                    "step_type": st,
                    "estimated_cost_cents": 0,
                })
        return {
            "steps": estimates,
            "total_estimated_cost_cents": round(total_est_cents, 4),
            "pricing_table": MODEL_PRICING,
            "note": "Non-cached estimate. Actual costs vary ±30% based on output length.",
        }
