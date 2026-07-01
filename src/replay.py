"""Execution Replay for ES Agent Management.

Replays a past execution step by step without re-running LLM calls.
Loads step logs, trace spans, and workflow state from the database.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from agent_workflow import AgentWorkflowDB
from tracing import TraceStore


class ReplayEngine:
    """Replay a past execution step by step without re-running LLM calls."""

    def __init__(self) -> None:
        self.db = AgentWorkflowDB()
        self.trace_store = TraceStore()

    def get_replay_data(self, run_id: str) -> dict:
        """Load full replay data for a run.

        Returns structured data including:
        - Run metadata (agent, status, cost, tokens, timing)
        - Full step log with prompt/output for each step
        - Trace tree (nested spans)
        - Workflow state at each step
        """
        run = self.db.get_run(run_id)
        if not run:
            return {"error": f"Run {run_id} not found"}

        # Get agent info
        agent = self.db.get_agent(run["agent_id"])

        # Get step logs (ordered by sequence)
        step_logs = self.db.list_step_logs(run_id)

        # Get workflow state
        state_data = self.db.get_run_state(run_id)

        # Get trace tree
        trace_tree = self.trace_store.get_run_trace_tree(run_id)

        # Get step definitions so we can attach labels and step_types
        steps_def_map: dict[str, dict] = {}
        if agent:
            agent_steps = self.db.list_steps(run["agent_id"])
            steps_def_map = {s["id"]: s for s in agent_steps}

        # Build enriched steps list
        steps: list[dict[str, Any]] = []
        for log in step_logs:
            step_def = steps_def_map.get(log["step_id"], {})

            # output_data is stored as a JSON string — extract readable text
            raw_output = log.get("output_data", "")
            output_text = ""
            if raw_output:
                try:
                    parsed = json.loads(raw_output)
                    if isinstance(parsed, dict):
                        # Try common output keys
                        output_text = (
                            parsed.get("result")
                            or parsed.get("output")
                            or parsed.get("response")
                            or parsed.get("text")
                            or json.dumps(parsed)
                        )
                    elif isinstance(parsed, str):
                        output_text = parsed
                    else:
                        output_text = json.dumps(parsed)
                except (json.JSONDecodeError, TypeError):
                    output_text = str(raw_output)

            # Try to compute duration from timestamps
            duration_ms = 0
            started = log.get("started_at", "")
            completed = log.get("completed_at", "")
            if started and completed:
                try:
                    from datetime import datetime

                    fmt = "%Y-%m-%dT%H:%M:%S.%f"
                    # Handle both with and without fractional seconds / Z suffix
                    s = started.replace("Z", "")
                    e = completed.replace("Z", "")
                    # Strip trailing Z and timezone offsets
                    if "+" in s:
                        s = s.split("+")[0]
                    if "+" in e:
                        e = e.split("+")[0]
                    s_dt = datetime.fromisoformat(s)
                    e_dt = datetime.fromisoformat(e)
                    duration_ms = int((e_dt - s_dt).total_seconds() * 1000)
                except (ValueError, TypeError):
                    duration_ms = 0

            steps.append({
                "step_index": log.get("sequence", 0),
                "step_id": log["step_id"],
                "label": step_def.get("label", ""),
                "step_type": step_def.get("step_type", "llm_call"),
                "prompt_sent": log.get("prompt_sent", ""),
                "output_received": output_text,
                "tokens_input": log.get("tokens_input", 0),
                "tokens_output": log.get("tokens_output", 0),
                "cost_cents": log.get("cost_cents", 0),
                "duration_ms": duration_ms,
                "status": log.get("status", "completed"),
                "state_before": {},
                "state_after": {},
            })

        # Parse run state JSON
        run_state: dict = {}
        try:
            state_raw = state_data.get("state", "{}")
            if isinstance(state_raw, str):
                run_state = json.loads(state_raw) if state_raw.strip() else {}
        except (json.JSONDecodeError, TypeError):
            run_state = {}

        return {
            "run": {
                "id": run["id"],
                "agent_id": run["agent_id"],
                "status": run["status"],
                "total_cost_cents": run.get("total_cost_cents", 0),
                "total_tokens": run.get("total_tokens", 0),
                "total_steps": run.get("total_steps", len(steps)),
                "created_at": run.get("created_at", ""),
                "started_at": run.get("started_at", ""),
                "completed_at": run.get("completed_at", ""),
                "trigger": run.get("trigger", "manual"),
            },
            "agent": {
                "name": agent.get("name", "") if agent else "",
                "description": agent.get("description", "") if agent else "",
            },
            "steps": steps,
            "trace_tree": trace_tree,
            "state": run_state,
        }

    def get_step_at_index(self, run_id: str, step_index: int) -> dict:
        """Get the state of the workflow at a specific step index.

        Returns what the workflow looked like BEFORE this step executed.
        """
        replay = self.get_replay_data(run_id)
        if "error" in replay:
            return replay

        steps = replay.get("steps", [])
        if step_index < 0 or step_index >= len(steps):
            return {
                "error": f"Step index {step_index} out of range "
                         f"(0-{len(steps) - 1})",
            }

        step = steps[step_index]

        return {
            "run": replay["run"],
            "agent": replay["agent"],
            "current_step": step,
            "completed_steps": steps[:step_index],
            "remaining_steps": steps[step_index:],
            "trace_tree": replay["trace_tree"],
            "state": replay["state"],
        }

    def compare_runs(self, run_id_a: str, run_id_b: str) -> dict:
        """Compare two runs of the same agent.

        Highlights: same/different prompts, different outputs, cost diff, token diff.
        """
        data_a = self.get_replay_data(run_id_a)
        data_b = self.get_replay_data(run_id_b)

        if "error" in data_a:
            return {"error": data_a["error"]}
        if "error" in data_b:
            return {"error": data_b["error"]}

        agent_id_a = data_a["run"]["agent_id"]
        agent_id_b = data_b["run"]["agent_id"]

        if agent_id_a != agent_id_b:
            return {
                "error": "Runs are from different agents, cannot compare",
                "agent_a": agent_id_a,
                "agent_b": agent_id_b,
            }

        steps_a = data_a.get("steps", [])
        steps_b = data_b.get("steps", [])

        comparisons: list[dict[str, Any]] = []
        max_steps = max(len(steps_a), len(steps_b))

        for i in range(max_steps):
            sa = steps_a[i] if i < len(steps_a) else None
            sb = steps_b[i] if i < len(steps_b) else None

            prompt_same: bool | None = None
            output_same: bool | None = None
            cost_diff: int | None = None
            token_diff: int | None = None

            if sa and sb:
                prompt_same = sa.get("prompt_sent", "") == sb.get("prompt_sent", "")
                output_same = sa.get("output_received", "") == sb.get("output_received", "")
                cost_diff = (sa.get("cost_cents", 0) or 0) - (sb.get("cost_cents", 0) or 0)
                token_diff = (
                    (sa.get("tokens_input", 0) or 0) + (sa.get("tokens_output", 0) or 0)
                ) - (
                    (sb.get("tokens_input", 0) or 0) + (sb.get("tokens_output", 0) or 0)
                )

            label = ""
            if sa:
                label = sa.get("label", "")
            elif sb:
                label = sb.get("label", "")
            comparisons.append({
                "step_index": i,
                "label": label,
                "run_a": sa,
                "run_b": sb,
                "prompt_same": prompt_same,
                "output_same": output_same,
                "cost_diff_cents": cost_diff,
                "token_diff": token_diff,
            })

        return {
            "run_a": data_a["run"],
            "run_b": data_b["run"],
            "agent": data_a["agent"],
            "same_agent": True,
            "steps_comparison": comparisons,
            "total_cost_diff_cents": (
                (data_a["run"]["total_cost_cents"] or 0)
                - (data_b["run"]["total_cost_cents"] or 0)
            ),
            "total_token_diff": (
                (data_a["run"]["total_tokens"] or 0)
                - (data_b["run"]["total_tokens"] or 0)
            ),
        }
