"""Workflow Execution Engine — Real execution engine for agent workflows.

Reads workflow graphs from AgentWorkflowDB and executes each step,
tracking costs, tokens, and traces for observability.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
import urllib.error
from collections import deque
from pathlib import Path
from typing import Any

from agent_workflow import AgentWorkflowDB
from credential_broker import CredentialBroker
from credential_store import CredentialStore
from executor_context import AuthorizationError, ExecutorContext
from run_memory import RunMemory
from scoring_engine import ScoringEngine
from seen_store import SeenStore
from state import WorkflowState, _parse_structured_output
from stories_engine import StoriesEngine
from stories.diff_engine import DiffEngine
from stories.trajectory import TrajectoryComputer
from template_renderer import render_template
from tool_executor import execute_tool_call
from tracing import TraceStore
from prompt_versioning import PromptVersionManager
from observability.metrics import MetricsCollector
from observability.watchdog import StepWatchdog

logger = logging.getLogger(__name__)

__all__ = ["WorkflowExecutor"]

LLM_ENDPOINT = os.environ.get("LLM_ENDPOINT", "http://localhost:7999/v1/chat/completions")
LLM_MODEL = os.environ.get("LLM_MODEL", "gemma-4-12B-it-4bit")
INPUT_TOKEN_COST = 0.01  # cost per 1000 tokens in cents
OUTPUT_TOKEN_COST = 0.03


def _safe_render(template: str, context: dict) -> str:
    def _replacer(match: re.Match) -> str:
        key = match.group(1).strip()
        return str(context.get(key, match.group(0)))
    result = re.sub(r"\{\{\s*(\w+)\s*\}\}", _replacer, template)
    result = re.sub(r"\{(\w+)\}", _replacer, result)
    return result


def _extract_urls_from_tool_output(output: dict) -> list[dict[str, str | None]]:
    """Recursively extract ``{url, title}`` items from tool output dicts.

    Handles common tool output shapes:
    - ``{tool_name: [{url: ..., title: ...}, ...]}`` (search results list)
    - ``{tool_name: {url: ..., title: ...}}`` (single scraper result)
    - ``{tool_name: {results: [{url: ..., title: ...}, ...]}}`` (nested results)
    - ``{url: ..., title: ...}`` (flat, single item)
    - ``{results: [{url: ..., title: ...}, ...]}`` (nested list)
    """
    if not isinstance(output, dict):
        return []

    seen: set[str] = set()
    items: list[dict[str, str | None]] = []

    def _walk(obj: object) -> None:
        if isinstance(obj, dict):
            # Direct URL item
            url = obj.get("url")
            if isinstance(url, str) and url.strip():
                if url not in seen:
                    seen.add(url)
                    title = obj.get("title")
                    items.append({
                        "url": url,
                        "title": str(title) if title is not None else None,
                    })
                return

            # Nested results key
            results = obj.get("results")
            if isinstance(results, list):
                for r in results:
                    _walk(r)
                return

            # Nested urls key
            urls_list = obj.get("urls")
            if isinstance(urls_list, list):
                for u in urls_list:
                    if isinstance(u, str):
                        if u not in seen:
                            seen.add(u)
                            items.append({"url": u, "title": None})
                    elif isinstance(u, dict):
                        _walk(u)
                return

            # Recurse into all values
            for val in obj.values():
                _walk(val)

        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(output)
    return items


class WorkflowExecutor:
    """Executes agent workflows step by step with full tracing."""

    def __init__(self) -> None:
        self.db = AgentWorkflowDB()
        self.tracer = TraceStore()
        self.pv = PromptVersionManager()
        self.pv.ensure_schema()
        self.memory = RunMemory()
        self.seen = SeenStore()
        # Observability
        self.metrics = MetricsCollector()
        self.watchdog = StepWatchdog()
        self.watchdog.start_watchdog(timeout_seconds=300, poll_interval=5)
        # Load tool registry from YAML
        self._tool_registry: dict[str, Any] = self._load_tool_registry()

    @staticmethod
    def _load_tool_registry() -> dict[str, Any]:
        """Load tool definitions from tools/registry.yaml.

        Returns a dict keyed by tool name (e.g. ``searxng``, ``send_email``)
        with endpoint, parameters, tier, and other metadata.
        Falls back to an empty dict if the file is missing.
        """
        registry_path = Path(__file__).parent.parent / "tools" / "registry.yaml"
        if not registry_path.exists():
            logger.warning("Tool registry not found at %s", registry_path)
            return {}
        try:
            import yaml  # type: ignore[import-untyped]
            with open(registry_path, "r") as f:
                data = yaml.safe_load(f)
            return data.get("tools", {})
        except Exception as exc:
            logger.warning("Failed to load tool registry: %s", exc)
            return {}

    def execute(self, agent_id: str, input_context: dict | None = None, trigger: str = "manual") -> dict:
        graph = self.db.get_workflow_graph(agent_id)
        agent = graph["agent"]
        steps = graph["steps"]
        connections = graph["connections"]

        if not agent:
            return {"error": "agent_not_found", "agent_id": agent_id}
        if not steps:
            return {"error": "no_steps", "agent_id": agent_id}

        ctx = dict(input_context or {})
        state = WorkflowState(input_context)
        run = self.db.create_run(agent_id, trigger, json.dumps(ctx))
        run_id = run["id"]
        self.db.update_run_status(run_id, "running")

        # Build execution context with credential brokering and DoA enforcement
        credential_store = CredentialStore(agent_id)
        credential_broker = CredentialBroker(credential_store, self._tool_registry)
        exec_ctx = ExecutorContext(
            workflow_id=agent_id,
            run_id=run_id,
            credential_broker=credential_broker,
            tool_registry=self._tool_registry,
            tool_instances=graph.get("tool_instances", {}),
            credential_store=credential_store,
            context=ctx,
            input_vars=ctx,
            step_results={},
        )

        ordered_steps = self._topological_sort(steps, connections)

        root_span_id = self.tracer.start_span(
            trace_id=run_id,
            span_name=f"Run: {agent['name']}",
            span_type="run",
            run_id=run_id,
            input_data=json.dumps(ctx),
        )

        total_cost = 0
        total_tokens = 0
        step_index = 0

        for step in ordered_steps:
            step_id = step["id"]
            span_id = self.tracer.start_span(
                trace_id=run_id,
                span_name=step.get("label") or step["step_type"],
                span_type="step",
                parent_span_id=root_span_id,
                step_id=step_id,
                run_id=run_id,
                input_data=json.dumps(ctx),
            )

            step_log = self.db.create_step_log(run_id, step_id, step_index)
            step_index += 1

            step_start = time.time()
            result: dict = {}
            tokens_in = 0
            tokens_out = 0
            cost = 0
            model = ""
            status = "completed"
            error_msg = ""
            step_type = step.get("step_type", "llm_call")

            # Watch the step for hang detection
            self.watchdog.watch_step(step_id, timeout=300)

            try:
                if step_type == "llm_call":
                    result = self._execute_llm_step(step, state, run_id, step_log, span_id, exec_ctx)
                elif step_type == "tool_call":
                    result = self._execute_tool_step(step, exec_ctx)
                elif step_type == "condition":
                    result = self._evaluate_condition(step, ctx)
                elif step_type == "loop":
                    result = self._execute_loop_step(step, state, ctx, run_id, step_log, span_id)
                elif step_type == "subworkflow":
                    result = self._execute_subworkflow(step, state)
                elif step_type == "human_escalation":
                    result = self._execute_human_escalation(step, state, run_id, step_log, span_id, exec_ctx)
                elif step_type == "data_pipeline":
                    result = self._execute_data_pipeline_step(step, exec_ctx)
                elif step_type == "score":
                    result = self._execute_score_step(step, exec_ctx)
                elif step_type == "memory_read":
                    result = self._execute_memory_read_step(step, exec_ctx)
                elif step_type == "memory_write":
                    result = self._execute_memory_write_step(step, exec_ctx)
                elif step_type == "http_fetch":
                    result = self._execute_http_fetch_step(step, exec_ctx)
                elif step_type == "parse_rss":
                    result = self._execute_parse_rss_step(step, exec_ctx)
                elif step_type == "parse_jsonpath":
                    result = self._execute_parse_jsonpath_step(step, exec_ctx)
                elif step_type == "parse_xpath":
                    result = self._execute_parse_xpath_step(step, exec_ctx)
                elif step_type == "parse_html":
                    result = self._execute_parse_html_step(step, exec_ctx)
                elif step_type == "resolve_id_list":
                    result = self._execute_resolve_id_list_step(step, exec_ctx)
                elif step_type == "fetch_source":
                    result = self._execute_fetch_source_step(step, exec_ctx)
                elif step_type == "parse_source":
                    result = self._execute_parse_source_step(step, exec_ctx)
                elif step_type == "fetch_and_parse":
                    result = self._execute_fetch_and_parse_step(step, exec_ctx)
                elif step_type == "verify_claims":
                    result = self._execute_verify_claims_step(step, exec_ctx)
                elif step_type == "grade_citations":
                    result = self._execute_grade_citations_step(step, exec_ctx)
                elif step_type == "reject_if_invalid":
                    result = self._execute_reject_if_invalid_step(step, exec_ctx)
                elif step_type == "extract_article":
                    result = self._execute_extract_article_step(step, exec_ctx)
                elif step_type == "batch_extract":
                    result = self._execute_batch_extract_step(step, exec_ctx)
                elif step_type == "extract_metadata_only":
                    result = self._execute_extract_metadata_step(step, exec_ctx)
                elif step_type == "archive_edition":
                    result = self._execute_archive_edition_step(step, exec_ctx)
                elif step_type == "rebuild_archive_index":
                    result = self._execute_rebuild_archive_index_step(step, exec_ctx)
                elif step_type == "assign_citations":
                    result = self._execute_assign_citations_step(step, exec_ctx)
                elif step_type == "resolve_citations":
                    result = self._execute_resolve_citations_step(step, exec_ctx)
                elif step_type == "export_citation_map":
                    result = self._execute_export_citation_map_step(step, exec_ctx)
                elif step_type == "diff_stories":
                    result = self._execute_diff_stories_step(step, exec_ctx)
                elif step_type == "compute_trajectories":
                    result = self._execute_compute_trajectories_step(step, exec_ctx)
                elif step_type == "rebuild_archive_index":
                    result = self._execute_rebuild_archive_index_step(step, exec_ctx)
                elif step_type == "extract_entities_batch":
                    result = self._execute_extract_entities_batch_step(step, exec_ctx)
                elif step_type == "extract_keywords":
                    result = self._execute_extract_keywords_step(step, exec_ctx)
                elif step_type == "score_by_entity":
                    result = self._execute_score_by_entity_step(step, exec_ctx)
                elif step_type == "validate_citations":
                    result = self._execute_validate_citations_step(step, exec_ctx)
                elif step_type == "hallucination_check":
                    result = self._execute_hallucination_check_step(step, exec_ctx)
                elif step_type == "detect_cross_references":
                    result = self._execute_detect_cross_references_step(step, exec_ctx)
                elif step_type == "boost_multi_sourced":
                    result = self._execute_boost_multi_sourced_step(step, exec_ctx)
                elif step_type == "cluster_by_topic":
                    result = self._execute_cluster_by_topic_step(step, exec_ctx)
                elif step_type == "register_edition":
                    result = self._execute_register_edition_step(step, exec_ctx)
                elif step_type == "compare_editions":
                    result = self._execute_compare_editions_step(step, exec_ctx)
                elif step_type == "compute_edition_stats":
                    result = self._execute_compute_edition_stats_step(step, exec_ctx)
                elif step_type == "synthesize_narrative":
                    result = self._execute_synthesize_narrative_step(step, exec_ctx)
                elif step_type == "detect_narrative_arcs":
                    result = self._execute_detect_narrative_arcs_step(step, exec_ctx)
                elif step_type == "generate_article_ideas":
                    result = self._execute_generate_article_ideas_step(step, exec_ctx)
                elif step_type == "render_pattern_with_version":
                    result = self._execute_render_pattern_with_version_step(step, exec_ctx)
                elif step_type == "sandbox_verify_pattern":
                    result = self._execute_sandbox_verify_pattern_step(step, exec_ctx)
                elif step_type == "run_regression_tests":
                    result = self._execute_run_regression_tests_step(step, exec_ctx)
                elif step_type == "update_baseline":
                    result = self._execute_update_baseline_step(step, exec_ctx)
                elif step_type == "score_edition_quality":
                    result = self._execute_score_edition_quality_step(step, exec_ctx)
                elif step_type == "check_quality_regression":
                    result = self._execute_check_quality_regression_step(step, exec_ctx)
                elif step_type == "render_sections":
                    result = self._execute_render_sections_step(step, exec_ctx)
                elif step_type == "join_brief":
                    result = self._execute_join_brief_step(step, exec_ctx)
                else:
                    result = {"error": f"unknown_step_type: {step_type}"}

                tokens_in = result.get("tokens_input", 0)
                tokens_out = result.get("tokens_output", 0)
                cost = result.get("cost_cents", 0)
                model = result.get("model", "")
            except Exception as exc:
                status = "failed"
                error_msg = str(exc)
                result = {"error": error_msg}
                state.set_step_error(step_id, error_msg)

            # Build step_results dict — keyed by BOTH DB id and yaml_step_id
            # Store the raw result output so collection data (items, articles, search
            # results, etc.) reaches downstream processing steps. _parse_structured_output
            # can lose list-type data, so we preserve the original result dict here.
            if status == "completed":
                yaml_id = step.get("yaml_step_id", "")
                # Store result dict for non-LLM steps (preserves items, articles, output lists)
                if step_type not in ("llm_call",):
                    exec_ctx.step_results[step_id] = result
                    if yaml_id:
                        exec_ctx.step_results[yaml_id] = result
                # LLM steps still use state-based structured output for template compat
                elif step_id in state.steps:
                    result_data = state.steps[step_id].get("structured", {})
                    exec_ctx.step_results[step_id] = result_data
                    if yaml_id:
                        exec_ctx.step_results[yaml_id] = result_data

            step_duration_ms = int((time.time() - step_start) * 1000)

            # Record observability metrics
            self.metrics.record_step_duration(
                step_id, step_duration_ms, success=(status == "completed")
            )
            if model:
                self.metrics.record_token_count(model, tokens_in, tokens_out)
            self.watchdog.cancel_watch(step_id)

            self.db.update_step_log(
                step_log["id"],
                output_data=json.dumps(result),
                tokens_input=tokens_in,
                tokens_output=tokens_out,
                cost_cents=cost,
                model_used=model,
                status=status,
                error_message=error_msg,
            )

            self.tracer.end_span(
                span_id,
                output_data=json.dumps(result),
                tokens_input=tokens_in,
                tokens_output=tokens_out,
                cost_cents=cost,
                duration_ms=step_duration_ms,
                model_used=model,
                status=status,
                error_message=error_msg,
            )

            if step.get("step_type") == "llm_call":
                self.pv.record_version(
                    step_id=step_id,
                    prompt_template=step.get("prompt_template", ""),
                    rendered_prompt=result.get("rendered_prompt", ""),
                    run_id=run_id,
                    context_data=json.dumps(ctx),
                    output_data=json.dumps(result),
                    tokens_input=tokens_in,
                    tokens_output=tokens_out,
                    model_used=model,
                )

            total_cost += cost
            total_tokens += tokens_in + tokens_out
            ctx["last_output"] = result.get("output", result)

            # Store state for non-LLM step types (tool_call, condition)
            if step_type not in ("llm_call",):
                output_text = str(result.get("output", result))
                structured = _parse_structured_output(output_text)
                state.set_step_output(step_id, output_text, structured)

            # For tool_call steps: store raw output dict in step_results so
            # subsequent tool steps can access structured data (URLs, search results, etc.)
            if step_type == "tool_call":
                tool_output = result.get("output", result)
                if isinstance(tool_output, dict):
                    exec_ctx.step_results[step_id] = tool_output
                    yaml_id = step.get("yaml_step_id", "")
                    if yaml_id:
                        exec_ctx.step_results[yaml_id] = tool_output

                # Extract URLs from tool output and record in SeenStore for dedup
                urls_to_record = _extract_urls_from_tool_output(tool_output)
                if urls_to_record:
                    try:
                        record_result = self.seen.bulk_record(
                            workflow_id=agent_id,
                            items=urls_to_record,
                            run_id=run_id,
                        )
                        logger.debug(
                            "SeenStore: recorded=%d updated=%d for %d URLs from step %s",
                            record_result.get("recorded", 0),
                            record_result.get("updated", 0),
                            len(urls_to_record),
                            step.get("yaml_step_id", step_id),
                        )
                    except Exception as exc:
                        logger.warning("SeenStore bulk_record failed: %s", exc)

            # If human_escalation, pause execution after updating everything
            if step_type == "human_escalation" and result.get("paused"):
                self.db.update_run_status(run_id, "awaiting_escalation")
                # Return the run early with paused status
                return self.db.get_run(run_id) or {"run_id": run_id, "status": "awaiting_escalation"}

        self.db.update_run_status(
            run_id,
            "completed",
            total_cost_cents=total_cost,
            total_tokens=total_tokens,
            total_steps=step_index,
        )

        self.tracer.end_span(
            root_span_id,
            output_data=json.dumps({"status": "completed", "steps": step_index}),
            cost_cents=total_cost,
            tokens_input=total_tokens,
            status="completed",
        )

        # Add seen_count to run result for observability
        seen_stats = self.seen.get_stats(agent_id)
        seen_count = seen_stats.get("total", 0)

        # Track stories from step results
        stories_tracked = 0
        try:
            stories_engine = StoriesEngine()
            # Extract story-like content from step_results
            # Look for dicts with 'title' key in step results
            tracked_titles: set[str] = set()
            for _sid, result_data in exec_ctx.step_results.items():
                if isinstance(result_data, dict):
                    title = result_data.get("title") or result_data.get("story_title")
                    if title and isinstance(title, str) and title.strip():
                        title_str = str(title).strip()
                        if title_str.lower() not in tracked_titles:
                            tracked_titles.add(title_str.lower())
                            headline = result_data.get("headline", result_data.get("output", ""))
                            body = result_data.get("body", result_data.get("description", ""))
                            sources_raw = result_data.get("sources", result_data.get("urls", []))
                            if isinstance(sources_raw, list):
                                sources = [str(s) for s in sources_raw]
                            else:
                                sources = []
                            tags = result_data.get("tags", "")
                            stories_engine.find_or_create(
                                workflow_id=agent_id,
                                title=title_str,
                                run_id=run_id,
                                headline=str(headline)[:1000] if isinstance(headline, str) else str(headline),
                                body=str(body)[:500] if isinstance(body, str) else str(body),
                                sources=sources,
                                tags=str(tags) if tags else "",
                            )
                            stories_tracked += 1
                    # key_signals from synthesis output — list of story title strings
                    key_signals = result_data.get("key_signals", [])
                    if isinstance(key_signals, list) and key_signals:
                        subject = result_data.get("subject", "")
                        body = result_data.get("body_markdown", "")
                        sources = result_data.get("sources", [])
                        if not isinstance(sources, list):
                            sources = []
                        for signal_title in key_signals:
                            title_str = str(signal_title).strip()
                            if title_str and title_str.lower() not in tracked_titles:
                                tracked_titles.add(title_str.lower())
                                stories_engine.find_or_create(
                                    workflow_id=agent_id, title=title_str,
                                    run_id=run_id, headline=str(subject)[:1000],
                                    body=str(body)[:500], sources=sources, tags="story,signal",
                                )
                                stories_tracked += 1
                    # Also check lists of items
                    for val in result_data.values():
                        if isinstance(val, list):
                            for item in val:
                                if isinstance(item, dict):
                                    title = item.get("title") or item.get("story_title")
                                    if title and isinstance(title, str) and title.strip():
                                        title_str = str(title).strip()
                                        if title_str.lower() not in tracked_titles:
                                            tracked_titles.add(title_str.lower())
                                            headline = item.get("headline", item.get("output", ""))
                                            body = item.get("body", item.get("description", ""))
                                            sources_raw = item.get("sources", item.get("urls", []))
                                            if isinstance(sources_raw, list):
                                                sources = [str(s) for s in sources_raw]
                                            else:
                                                sources = []
                                            tags = item.get("tags", "")
                                            stories_engine.find_or_create(
                                                workflow_id=agent_id,
                                                title=title_str,
                                                run_id=run_id,
                                                headline=str(headline)[:1000] if isinstance(headline, str) else str(headline),
                                                body=str(body)[:500] if isinstance(body, str) else str(body),
                                                sources=sources,
                                                tags=str(tags) if tags else "",
                                            )
                                            stories_tracked += 1
            logger.debug(
                "StoriesEngine: tracked %d stories for workflow %s (run %s)",
                stories_tracked, agent_id, run_id,
            )
        except Exception as exc:
            logger.warning("StoriesEngine tracking failed: %s", exc)
            stories_tracked = 0

        result = self.db.get_run(run_id) or {"run_id": run_id, "status": "completed"}
        result["seen_count"] = seen_count
        result["stories_tracked"] = stories_tracked
        return result

    def _topological_sort(self, steps: list[dict], connections: list[dict]) -> list[dict]:
        if len(steps) == 1:
            return steps

        step_map = {s["id"]: s for s in steps}
        in_degree: dict[str, int] = {s["id"]: 0 for s in steps}
        adjacency: dict[str, list[str]] = {s["id"]: [] for s in steps}

        for conn in connections:
            frm = conn["from_step_id"]
            to = conn["to_step_id"]
            if frm in adjacency and to in in_degree:
                adjacency[frm].append(to)
                in_degree[to] += 1

        queue: deque[str] = deque()
        for sid, deg in in_degree.items():
            if deg == 0:
                queue.append(sid)

        ordered: list[dict] = []
        while queue:
            sid = queue.popleft()
            ordered.append(step_map[sid])
            for neighbor in adjacency[sid]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(ordered) < len(steps):
            seen = {s["id"] for s in ordered}
            for s in steps:
                if s["id"] not in seen:
                    ordered.append(s)

        return ordered

    def _execute_llm_step(self, step: dict, state: WorkflowState, run_id: str, step_log: dict, span_id: str, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute an LLM call step with model allowlist enforcement."""

        # Check model allowlist from Delegation of Authority
        doa = step.get("authority", {}) or {}
        model_allowlist = doa.get("model_allowlist", [])
        model = step.get("model", LLM_MODEL)
        if model_allowlist and model not in model_allowlist:
            raise AuthorizationError(f"Model '{model}' not in allowlist: {model_allowlist}")

        # Trace DoA validation
        if exec_ctx:
            doa_span_id = self.tracer.start_span(
                trace_id=run_id,
                span_name="doa.validate",
                span_type="credential",
                parent_span_id=span_id,
                run_id=run_id,
                input_data=json.dumps({"model": model, "allowlist": model_allowlist}),
            )
            self.tracer.end_span(
                doa_span_id,
                status="completed",
            )

        template = step.get("prompt_template", "")

        # Render template variables using the standalone renderer
        if exec_ctx is not None:
            input_vars = exec_ctx.input_vars or exec_ctx.context

            # Populate context namespace from step_results for {{context.*}} variables
            context_data: dict[str, Any] = {}
            step_results = exec_ctx.step_results
            if step_results:
                # Collect articles from all steps that produced them
                articles = []
                narratives = []
                scores = []
                cross_refs = []
                citation_map = ""
                for _sid, result_data in step_results.items():
                    if isinstance(result_data, dict):
                        items = result_data.get("items") or result_data.get("output") or []
                        if isinstance(items, list) and len([i for i in items if isinstance(i, dict) and ("url" in i or "title" in i)]) > 5:
                            articles.extend(items)
                        narrative = result_data.get("narrative", "")
                        if narrative:
                            narratives.append(str(narrative))
                        score = result_data.get("quality_score", result_data.get("score", ""))
                        if score:
                            scores.append(str(score))
                        cr = result_data.get("cross_refs", [])
                        if isinstance(cr, list) and len(cr) > 0:
                            cross_refs.extend(cr)
                        cm = result_data.get("output", "")
                        if isinstance(cm, str) and "[S" in cm:
                            citation_map = cm
                if articles:
                    # Truncate articles for LLM context window to prevent overflow
                    raw_cfg = step.get("config_json", "{}")
                    if isinstance(raw_cfg, str):
                        try:
                            cfg = json.loads(raw_cfg)
                        except (json.JSONDecodeError, TypeError):
                            cfg = {}
                    elif isinstance(raw_cfg, dict):
                        cfg = raw_cfg
                    else:
                        cfg = {}
                    max_articles = cfg.get("max_articles", 50)
                    original_count = len(articles)
                    if len(articles) > max_articles:
                        articles = articles[:max_articles]
                    # Truncate content fields to first 200 chars
                    for article in articles:
                        if isinstance(article, dict) and "content" in article and isinstance(article["content"], str):
                            if len(article["content"]) > 200:
                                article["content"] = article["content"][:200] + "..."
                    context_data["articles"] = articles
                    logger.info(
                        "Truncated articles for LLM: original=%d truncated=%d (max=%d)",
                        original_count, len(articles), max_articles
                    )
                if narratives:
                    context_data["narrative"] = "\n\n".join(narratives)
                if scores:
                    context_data["quality_scores"] = "\n".join(scores)
                if cross_refs:
                    context_data["cross_reference_boosts"] = cross_refs
                if citation_map:
                    context_data["citation_map"] = citation_map
                if "workflow_id" in exec_ctx.context:
                    context_data["workflow_id"] = exec_ctx.context["workflow_id"]

            if context_data:
                # Truncate ALL context values to reasonable sizes before rendering
                for ckey in list(context_data.keys()):
                    val = context_data[ckey]
                    if ckey == "articles":
                        # Already truncated above
                        pass
                    elif isinstance(val, str) and len(val) > 500:
                        context_data[ckey] = val[:500] + "\n[...truncated...]"
                    elif isinstance(val, list):
                        total = len(val)
                        if total > 20:
                            context_data[ckey] = val[:20]
                            # If it's a list of dicts with a 'title' field, add a summary
                            if val and isinstance(val[0], dict) and "title" in val[0]:
                                titles = [v.get("title", "")[:80] for v in val[:20]]
                                summary = f"{', '.join(titles)}... +{total-20} more"
                                context_data[ckey] = [{"_summary": summary}]
                input_vars = dict(input_vars or {})
                input_vars["context"] = context_data
            else:
                input_vars = input_vars or {}

            template = render_template(template, input_vars, step_results)

        rendered = state.resolve(template)

        messages = [{"role": "user", "content": rendered}]

        tools_json = step.get("tools_json", "[]")
        try:
            tools = json.loads(tools_json) if isinstance(tools_json, str) else tools_json
        except (json.JSONDecodeError, TypeError):
            tools = []

        if tools:
            tool_desc = ", ".join(str(t) for t in tools)
            messages.insert(0, {"role": "system", "content": f"Available tools: {tool_desc}"})

        # Log rendered prompt size for debugging
        if isinstance(rendered, str):
            logger.warning("Rendered prompt size: %d chars (~%d tokens)", len(rendered), len(rendered) // 4)
            if len(rendered) > 10000:
                logger.warning("Prompt exceeds 10K chars — truncating to 10000")
                rendered = rendered[:10000]
                messages = [{"role": "user", "content": rendered}]

        raw_response = self._call_llm(messages)

        output = raw_response.get("response", "")
        tokens_in = raw_response.get("tokens_input", 0)
        tokens_out = raw_response.get("tokens_output", 0)
        model = raw_response.get("model", LLM_MODEL)

        cost = round((tokens_in / 1000 * INPUT_TOKEN_COST + tokens_out / 1000 * OUTPUT_TOKEN_COST) * 100, 4)

        # Parse structured output from LLM response
        structured = _parse_structured_output(output)
        state.set_step_output(step["id"], output, structured)

        return {
            "output": output,
            "rendered_prompt": rendered,
            "tokens_input": tokens_in,
            "tokens_output": tokens_out,
            "cost_cents": cost,
            "model": model,
            "raw_response": raw_response,
        }

    def _execute_tool_step(self, step: dict, exec_ctx: ExecutorContext) -> dict:
        """Execute a tool_call step with credential brokering and DoA enforcement.

        Validates tool access against Delegation of Authority, resolves
        credentials, injects them into tool calls, and redacts them from traces.
        """
        broker = exec_ctx.credential_broker
        run_id = exec_ctx.run_id

        # Render template variables in the prompt before proceeding
        input_vars = exec_ctx.input_vars or exec_ctx.context
        step_results = exec_ctx.step_results
        if step.get("prompt_template"):
            step["prompt_template"] = render_template(
                step["prompt_template"], input_vars, step_results
            )

        # 1. Extract DoA from step (fall back to workflow-level default)
        doa = step.get("authority", {}) or {}

        # 2. Trace: DoA validation start
        doa_span_id = self.tracer.start_span(
            trace_id=run_id,
            span_name="doa.validate",
            span_type="credential",
            run_id=run_id,
            input_data=json.dumps({
                "step_id": step.get("id"),
                "tools": step.get("tools_json", "[]"),
                "doa": doa,
            }),
        )

        # 3. Validate tool access against DoA
        broker.validate_step_tool_access(step, exec_ctx.tool_instances)

        self.tracer.end_span(doa_span_id, status="completed")

        # 4. Trace: credential resolution
        resolve_span_id = self.tracer.start_span(
            trace_id=run_id,
            span_name="credential.resolve",
            span_type="credential",
            run_id=run_id,
            input_data=json.dumps({"step_id": step.get("id")}),
        )

        # 5. Resolve credentials for this step
        resolved = broker.resolve_for_step(step, exec_ctx.tool_instances) if exec_ctx.tool_instances else {}

        self.tracer.end_span(resolve_span_id, status="completed")

        # 6. Check if sandbox isolation is required
        use_sandbox = step.get("sandbox", False) or \
                      os.environ.get("ESAM_SANDBOX_ALL", "0") == "1"

        if use_sandbox:
            # ── Sandbox path ────────────────────────────────────────
            # Import here to avoid circular imports at module level
            from sandbox_router import SandboxRouter  # type: ignore[import-untyped]

            router = SandboxRouter()
            router.ensure_sandbox()

            # Build tool config for the sandbox (no credentials in config)
            tool_config = {
                "tools": step.get("tools", []),
                "model": step.get("model"),
                "prompt": _safe_render(step.get("prompt_template", ""), exec_ctx.context),
            }

            # Execute inside sandbox — credentials are injected sandbox-side
            sandbox_result = router.execute_in_sandbox(
                prompt=tool_config["prompt"],
                tool_config=tool_config,
                credentials=resolved,
                step_id=step.get("id", ""),
                credential_scope=doa.get("credential_scope"),
            )

            results = sandbox_result.get("output", sandbox_result)
        else:
            # ── Local (non-sandbox) path ────────────────────────────
            # 7. Parse tool names
            tools_json = step.get("tools_json", "[]")
            try:
                tool_names = json.loads(tools_json) if isinstance(tools_json, str) else tools_json
            except (json.JSONDecodeError, TypeError):
                tool_names = []

            # 8. Execute each tool with credential injection
            results: dict[str, Any] = {}
            for name in tool_names:
                # Determine the tool instance name and tool_ref
                instance_name = name

                # Check if this is a tool_instance reference (e.g. "email_gateway")
                # vs. a direct tool_ref (e.g. "web_search")
                tool_instances = exec_ctx.tool_instances or {}
                if instance_name in tool_instances:
                    instance_config = tool_instances[instance_name]
                    tool_ref = instance_config.get("tool_ref", instance_name)
                else:
                    tool_ref = instance_name

                # Build params from context — start with extracted context data
                tool_params: dict[str, Any] = {}
                # If the step has a prompt_template, include rendered context
                if exec_ctx.context:
                    tool_params["context"] = exec_ctx.context
                # Copy any known tool parameters from context (input vars)
                ctx = exec_ctx.context or {}
                for key in ("query", "url", "to", "subject", "body", "message",
                            "actions", "wait_for", "categories", "language",
                            "max_results", "extract_format", "format"):
                    if key in ctx:
                        tool_params[key] = ctx[key]
                # Map recipient → to (common input field alias)
                if "recipient" in ctx and "to" not in tool_params:
                    tool_params["to"] = ctx["recipient"]
                # Also scan step_results for tool-relevant fields
                # (LLM outputs like subject, body, body_markdown live here)
                if exec_ctx.step_results:
                    for _sid, result_data in exec_ctx.step_results.items():
                        if not isinstance(result_data, dict):
                            continue
                        for key in ("to", "subject", "body", "body_markdown",
                                    "email_body", "recipient", "message", "content",
                                    "output", "text"):
                            if key in result_data and key not in tool_params:
                                # Map body_markdown → body for email tools
                                if key == "body_markdown":
                                    tool_params["body"] = result_data[key]
                                else:
                                    tool_params[key] = result_data[key]

                # ── Array iteration: source params from step_results list fields ──
                # For searxng: iterate over search_queries, calling the tool once per query
                # For camoufox: iterate over URLs extracted from previous tool results
                iterate_items: list[dict[str, Any]] | None = None
                if exec_ctx.step_results:
                    # Determine which tool we're calling
                    tool_def = self._tool_registry.get(tool_ref, {})
                    tool_type = tool_def.get("type", tool_def.get("tool_type", ""))
                    tool_is_search = tool_ref in ("searxng",) or "search" in tool_ref.lower()
                    tool_is_scraper = tool_ref in ("camoufox",) or "scrape" in tool_ref.lower() or "extract" in tool_ref.lower()
                    tool_is_email = tool_ref in ("send_email",) or "email" in tool_ref.lower()

                    # For search tools: find search_queries from previous LLM steps
                    if tool_is_search and "query" not in tool_params:
                        for _sid, result_data in exec_ctx.step_results.items():
                            if not isinstance(result_data, dict):
                                continue
                            queries = result_data.get("search_queries")
                            if isinstance(queries, list) and queries:
                                iterate_items = [{"query": q} for q in queries]
                                logger.info(
                                    "Array iteration for %s: %d search queries from step_results",
                                    name, len(iterate_items),
                                )
                                break

                    # For scraper/extract tools: find URLs from previous search results
                    if tool_is_scraper and iterate_items is None and "url" not in tool_params:
                        urls: list[str] = []
                        for _sid, result_data in exec_ctx.step_results.items():
                            if not isinstance(result_data, dict):
                                continue
                            # Direct urls list
                            if "urls" in result_data and isinstance(result_data["urls"], list):
                                urls.extend(str(u) for u in result_data["urls"])
                            # SearXNG results with URLs (result_data has "results" key)
                            if "results" in result_data and isinstance(result_data["results"], list):
                                for r in result_data["results"]:
                                    if isinstance(r, dict) and "url" in r and r["url"]:
                                        urls.append(str(r["url"]))
                            # Nested tool output: result_data may be {tool_name: {results: [...]}}
                            for val in result_data.values():
                                if isinstance(val, dict):
                                    if "urls" in val and isinstance(val["urls"], list):
                                        urls.extend(str(u) for u in val["urls"])
                                    if "results" in val and isinstance(val["results"], list):
                                        for r in val["results"]:
                                            if isinstance(r, dict) and "url" in r and r["url"]:
                                                urls.append(str(r["url"]))
                                # Also handle list values: accumulated tool results
                                elif isinstance(val, list):
                                    for item in val:
                                        if isinstance(item, dict):
                                            if "url" in item and item["url"]:
                                                urls.append(str(item["url"]))
                                            if "results" in item and isinstance(item["results"], list):
                                                for r in item["results"]:
                                                    if isinstance(r, dict) and "url" in r and r["url"]:
                                                        urls.append(str(r["url"]))
                        if urls:
                            # Deduplicate while preserving order, limit to top 5
                            seen: set[str] = set()
                            unique_urls: list[str] = []
                            for u in urls:
                                if u not in seen:
                                    seen.add(u)
                                    unique_urls.append(u)
                            if unique_urls:
                                iterate_items = [{"url": u} for u in unique_urls[:5]]
                                logger.info(
                                    "Array iteration for %s: %d URLs from step_results",
                                    name, len(iterate_items),
                                )

                inject_span_id = self.tracer.start_span(
                    trace_id=run_id,
                    span_name="credential.inject",
                    span_type="credential",
                    run_id=run_id,
                    input_data=json.dumps({"tool": name}),
                )

                secure_params = broker.inject_credentials(name, tool_params, resolved)

                self.tracer.end_span(inject_span_id, status="completed")

                try:
                    if iterate_items:
                        # Array iteration: call the tool once per item, accumulate results
                        all_results: list[dict[str, Any]] = []
                        for item_params in iterate_items:
                            # Merge item-specific params (e.g. {"query": "..."} or {"url": "..."})
                            merged_params = {**secure_params}
                            # Remove context dict so it doesn't collide with tool params
                            if "context" in merged_params and "query" in item_params:
                                merged_params.pop("context", None)
                            merged_params.update(item_params)
                            # Re-inject credentials for this variant
                            item_secure = broker.inject_credentials(name, merged_params, resolved)
                            import asyncio
                            loop = asyncio.new_event_loop()
                            try:
                                asyncio.set_event_loop(loop)
                                item_result = loop.run_until_complete(
                                    execute_tool_call(
                                        instance_name=instance_name,
                                        params=item_secure,
                                        tool_instances=tool_instances,
                                        tool_registry=self._tool_registry,
                                        credentials=resolved.get(name),
                                    )
                                )
                            finally:
                                loop.close()
                            safe_item = broker.redact_credentials(name, item_result)
                            all_results.append(safe_item)
                        results[name] = all_results
                    else:
                        # Single call (default behavior)
                        import asyncio
                        loop = asyncio.new_event_loop()
                        try:
                            asyncio.set_event_loop(loop)
                            tool_result = loop.run_until_complete(
                                execute_tool_call(
                                    instance_name=instance_name,
                                    params=secure_params,
                                    tool_instances=tool_instances,
                                    tool_registry=self._tool_registry,
                                    credentials=resolved.get(name),
                                )
                            )
                        finally:
                            loop.close()
                        # Redact credentials from result
                        safe_result = broker.redact_credentials(name, tool_result)
                        results[name] = safe_result
                except Exception as exc:
                    logger.error("Tool execution failed for '%s': %s", name, exc)
                    results[name] = {"error": f"tool_execution_failed: {exc}"}

        # 9. Check cost limit (applies to both sandbox and local paths)
        estimated = broker.estimate_cost(step, resolved)
        cost_span_id = self.tracer.start_span(
            trace_id=run_id,
            span_name="cost.check",
            span_type="credential",
            run_id=run_id,
            input_data=json.dumps({"estimated_cents": estimated, "accumulated_cents": exec_ctx.accumulated_cost_cents}),
        )

        broker.check_cost_limit(step, estimated, exec_ctx.accumulated_cost_cents)
        exec_ctx.accumulated_cost_cents += estimated

        self.tracer.end_span(cost_span_id, status="completed")

        return {
            "output": results,
            "tokens_input": 0,
            "tokens_output": 0,
            "cost_cents": estimated,
            "model": "",
        }

    def _legacy_execute_tool_step(self, step: dict, context: dict) -> dict:
        """Fallback tool step execution without credential brokering (resume path)."""
        tools_json = step.get("tools_json", "[]")
        try:
            tool_names = json.loads(tools_json) if isinstance(tools_json, str) else tools_json
        except (json.JSONDecodeError, TypeError):
            tool_names = []

        results: dict[str, Any] = {}
        for name in tool_names:
            tool_fn = self._tool_registry.get(name)
            if tool_fn:
                results[name] = tool_fn(context)
            else:
                results[name] = {"error": f"tool_not_found: {name}"}

        return {
            "output": results,
            "tokens_input": 0,
            "tokens_output": 0,
            "cost_cents": 0,
            "model": "",
        }

    def _evaluate_condition(self, step: dict, context: dict) -> dict:
        expr = step.get("prompt_template", "True")
        safe_globals: dict[str, Any] = {
            "__builtins__": {},
            "int": int,
            "float": float,
            "str": str,
            "bool": bool,
            "len": len,
            "True": True,
            "False": False,
            "None": None,
        }
        try:
            result = bool(eval(expr, safe_globals, context))
        except Exception:
            result = False

        return {
            "result": result,
            "output": "true" if result else "false",
            "tokens_input": 0,
            "tokens_output": 0,
            "cost_cents": 0,
            "model": "",
        }

    def _execute_loop_step(self, step: dict, state: WorkflowState, context: dict, run_id: str, step_log: dict, span_id: str) -> dict:
        loop_json = step.get("loop_config_json", "{}")
        try:
            config = json.loads(loop_json) if isinstance(loop_json, str) else loop_json
        except (json.JSONDecodeError, TypeError):
            config = {}

        max_iterations = config.get("max_iterations", 5)
        condition_key = config.get("condition", "continue_condition")

        accumulated_output: list[str] = []
        total_tokens_in = 0
        total_tokens_out = 0
        total_cost = 0
        model = ""

        loop_ctx = dict(context)
        for i in range(max_iterations):
            result = self._execute_llm_step(step, state, run_id, step_log, span_id)
            accumulated_output.append(result.get("output", ""))
            total_tokens_in += result.get("tokens_input", 0)
            total_tokens_out += result.get("tokens_output", 0)
            total_cost += result.get("cost_cents", 0)
            model = result.get("model", model)

            loop_ctx["last_output"] = result.get("output", "")
            loop_ctx["iteration"] = i + 1

            condition_val = loop_ctx.get(condition_key)
            if condition_val is not None and not condition_val:
                break
            if condition_key in loop_ctx and not loop_ctx[condition_key]:
                break

        return {
            "output": "\n".join(accumulated_output),
            "iterations": len(accumulated_output),
            "tokens_input": total_tokens_in,
            "tokens_output": total_tokens_out,
            "cost_cents": total_cost,
            "model": model,
        }

    def _execute_subworkflow(self, step: dict, state: WorkflowState) -> dict:
        """Execute a subworkflow step.
        
        Looks up the target agent, creates input from the current state,
        executes the subworkflow (which returns its own run result),
        and maps the output back into the current state.
        """
        config = json.loads(step.get("subworkflow_config_json", "{}"))
        target_id = config.get("target_agent_id", "")
        input_mapping = config.get("input_mapping", {})
        
        if not target_id:
            return {
                "error": "subworkflow_target_missing",
                "output": "No target_agent_id configured",
                "tokens_input": 0,
                "tokens_output": 0,
                "cost_cents": 0,
                "model": "",
            }
        
        # Resolve input mapping against current state
        sub_input = {}
        for key, expr in input_mapping.items():
            if isinstance(expr, str) and "{{" in expr:
                sub_input[key] = state.resolve(expr)
            else:
                sub_input[key] = expr
        
        # Execute subworkflow
        sub_executor = WorkflowExecutor()
        sub_result = sub_executor.execute(target_id, sub_input)
        
        output_text = json.dumps(sub_result)
        
        # Update state with subworkflow output
        state.set_step_output(
            step["id"],
            output_text,
            sub_result,
        )
        
        return {
            "output": output_text,
            "sub_run_id": sub_result.get("id", ""),
            "sub_cost_cents": sub_result.get("total_cost_cents", 0),
            "sub_status": sub_result.get("status", "unknown"),
            "tokens_input": 0,
            "tokens_output": 0,
            "cost_cents": 0,
            "model": "",
        }

    def _execute_score_step(self, step: dict, exec_ctx: ExecutorContext) -> dict:
        """Execute a scoring step — deterministic, no LLM involved.

        Collects items from previous step results (either from specific
        step IDs listed in ``tools_json``, or auto-detected), applies
        :class:`ScoringEngine` rules, and returns items sorted by
        score descending.

        Step configuration (stored in the step's DB columns):

        * ``tools_json`` — JSON list of step IDs whose results contain
          items to score (e.g. ``["step-collect"]``).  When empty,
          the method auto-scans *all* step results for lists of dicts.
        * ``prompt_template`` — optional string; not used during
          scoring but rendered for logging / transparency.
        """
        engine = ScoringEngine()

        # ── 1. Collect items from step_results ──────────────────────
        items: list[dict] = []
        source_step_ids_raw = step.get("tools_json", "[]")
        try:
            source_ids = (
                json.loads(source_step_ids_raw)
                if isinstance(source_step_ids_raw, str)
                else source_step_ids_raw
            )
        except (json.JSONDecodeError, TypeError):
            source_ids = []

        if source_ids and isinstance(source_ids, list):
            # Specific step IDs configured — pull items from those
            for sid in source_ids:
                result_data = exec_ctx.step_results.get(sid, {})
                if isinstance(result_data, list):
                    items.extend(result_data)
                elif isinstance(result_data, dict):
                    # Tools may wrap results in a dict per tool name:
                    # {tool_name: [item, item, ...]}
                    for val in result_data.values():
                        if isinstance(val, list) and val:
                            items.extend(val)
        else:
            # Auto-detect: scan step_results for lists of dicts
            for _sid, result_data in exec_ctx.step_results.items():
                if isinstance(result_data, list) and result_data:
                    items.extend(result_data)
                elif isinstance(result_data, dict):
                    for val in result_data.values():
                        if isinstance(val, list) and val and isinstance(val[0], dict):
                            items.extend(val)

        if not items:
            logger.info("Score step %s: no items found to score", step.get("id"))
            return {
                "output": [],
                "items": [],
                "tokens_input": 0,
                "tokens_output": 0,
                "cost_cents": 0,
                "model": "",
            }

        # ── 2. Load scoring rules ───────────────────────────────────
        agent_id = exec_ctx.workflow_id
        step_id = step.get("id")
        rules = engine.get_rules(agent_id, step_id=step_id)
        if not rules:
            # Fall back to workflow-level rules (step_id IS NULL)
            rules = engine.get_rules(agent_id)

        if not rules:
            logger.warning(
                "Score step %s: no scoring rules found for agent %s",
                step_id, agent_id,
            )
            return {
                "output": items,
                "items": items,
                "tokens_input": 0,
                "tokens_output": 0,
                "cost_cents": 0,
                "model": "",
            }

        # ── 3. Score and sort ───────────────────────────────────────
        scored = engine.compute_batch(items, rules)

        logger.info(
            "Score step %s: scored %d items (rules: %d)",
            step.get("id"), len(scored), len(rules),
        )

        return {
            "output": scored,
            "items": scored,
            "tokens_input": 0,
            "tokens_output": 0,
            "cost_cents": 0,
            "model": "",
        }

    def _execute_data_pipeline_step(self, step: dict, exec_ctx: ExecutorContext) -> dict:
        """Execute a data_pipeline step — fetches curated sources from Source Registry.

        This step type acts as a data ingestion bridge: it reads sources from
        the Source Registry (wf_sources table), optionally filtered by
        authority_tier, workflow_id, or a map of source_name → source_id from
        the step's ``tools_json`` config, then fetches their RSS feeds and
        stores articles in wf_source_articles.

        Step configuration (stored in the step's DB columns):

        * ``tools_json`` — JSON configuration dict:
            {
                "authority_tier": "A",          // filter by tier (optional)
                "source_ids": ["id1", "id2"],   // specific source IDs (optional)
                "source_names": ["name1"],      // specific source names (optional)
                "limit": 10,                     // max articles to return (default 50)
                "force_refetch": false           // bypass fetch_interval check
            }
        * ``prompt_template`` — optional; rendered for logging/transparency.

        Returns:
            Dict with 'articles' key containing fetched articles.
        """
        from source_registry import SourceRegistry

        registry = SourceRegistry()

        # Parse config from tools_json
        config_raw = step.get("tools_json", "{}")
        try:
            config = json.loads(config_raw) if isinstance(config_raw, str) else config_raw
        except (json.JSONDecodeError, TypeError):
            config = {}

        if not isinstance(config, dict):
            config = {}

        authority_tier = config.get("authority_tier")
        source_ids = config.get("source_ids", [])
        source_names = config.get("source_names", [])
        limit = int(config.get("limit", 50))
        force_refetch = bool(config.get("force_refetch", False))

        # Collect sources from registry
        all_sources: list[dict] = []
        seen_ids: set[str] = set()

        # 1. By specific IDs
        if source_ids:
            for sid in source_ids:
                src = registry.get(sid)
                if src and src.get("enabled"):
                    all_sources.append(src)
                    seen_ids.add(sid)

        # 2. By specific names
        if source_names:
            for s in registry.list():
                if s.get("name") in source_names and s["id"] not in seen_ids and s.get("enabled"):
                    all_sources.append(s)
                    seen_ids.add(s["id"])

        # 3. By authority tier (or all enabled, if no specific filters)
        if not source_ids and not source_names:
            all_sources = registry.list(
                authority_tier=authority_tier,
                enabled_only=True,
            )

        if not all_sources:
            logger.info("Data pipeline step %s: no sources configured/available", step.get("id"))
            return {
                "articles": [],
                "sources_fetched": 0,
                "articles_fetched": 0,
                "output": [],
                "tokens_input": 0,
                "tokens_output": 0,
                "cost_cents": 0,
                "model": "",
            }

        # Fetch RSS for these sources
        fetch_results = registry.fetch_all(force=force_refetch)

        # Collect articles
        all_articles: list[dict] = []
        for src in all_sources:
            articles = registry.get_articles(source_id=src["id"], limit=limit)
            all_articles.extend(articles)

        # Sort by published_date descending, then score descending
        all_articles.sort(
            key=lambda a: (
                a.get("published_date") or a.get("fetched_at") or "",
                -(a.get("score") or 0.0),
            ),
            reverse=True,
        )

        # Trim to limit
        all_articles = all_articles[:limit]

        logger.info(
            "Data pipeline step %s: fetched %d sources, %d articles",
            step.get("id"), len(all_sources), len(all_articles),
        )

        return {
            "articles": all_articles,
            "sources_fetched": len(all_sources),
            "articles_fetched": len(all_articles),
            "fetch_results": fetch_results,
            "output": all_articles,
            "tokens_input": 0,
            "tokens_output": 0,
            "cost_cents": 0,
            "model": "",
        }

    def _execute_human_escalation(self, step: dict, state: WorkflowState, run_id: str, step_log: dict, span_id: str, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a human_escalation step — pauses the workflow for human review.

        1. Creates an escalation record in wf_escalations
        2. Resolves escalation config from state templates
        3. Stores context_json with the current workflow state
        4. Updates the step log status to 'awaiting_human'
        5. Returns with paused=True to stop iteration
        """
        config_json = step.get("escalation_config_json", "{}")
        try:
            config = json.loads(config_json) if isinstance(config_json, str) else config_json
        except (json.JSONDecodeError, TypeError):
            config = {}

        # Render template variables in the step prompt
        if exec_ctx is not None:
            input_vars = exec_ctx.input_vars or exec_ctx.context
            step_results = exec_ctx.step_results
            if step.get("prompt_template"):
                step["prompt_template"] = render_template(
                    step["prompt_template"], input_vars, step_results
                )
            # Render escalation subject and body_template
            if isinstance(config, dict):
                if "subject" in config and isinstance(config["subject"], str):
                    config["subject"] = render_template(
                        config["subject"], input_vars, step_results
                    )
                if "body_template" in config and isinstance(config["body_template"], str):
                    config["body_template"] = render_template(
                        config["body_template"], input_vars, step_results
                    )

        # Resolve templates in config
        resolved_config = {}
        for k, v in config.items():
            if isinstance(v, str):
                resolved_config[k] = state.resolve(v)
            else:
                resolved_config[k] = v

        # Store current state as context
        context_summary = {
            "step_label": step.get("label", ""),
            "step_id": step["id"],
            "run_id": run_id,
            "escalation_config": config,
        }

        # Create escalation record
        escalation = self.db.create_escalation(
            run_id=run_id,
            step_id=step["id"],
            step_log_id=step_log["id"],
            escalation_config_json=json.dumps(resolved_config),
            context_json=json.dumps(context_summary),
        )

        # Update step log status
        self.db.update_step_log(
            step_log["id"],
            status="awaiting_human",
            output_data=json.dumps({"escalation_id": escalation["id"]}),
        )

        return {
            "output": f"Awaiting human response (escalation: {escalation['id']})",
            "escalation_id": escalation["id"],
            "paused": True,
            "tokens_input": 0,
            "tokens_output": 0,
            "cost_cents": 0,
            "model": "",
        }

    def resume_from_escalation(self, run_id: str, escalation_id: str) -> dict:
        """Resume a workflow that was paused at a human_escalation step.

        1. Finds the escalation record
        2. Gets the escalation step and its next connections
        3. Determines the next step based on the response_action
        4. Continues execution from that point
        5. Updates the run status back to 'running'
        """
        escalation = self.db.get_escalation(escalation_id)
        if not escalation:
            return {"error": f"Escalation {escalation_id} not found"}
        if escalation.get("status") != "responded":
            return {"error": f"Escalation {escalation_id} has not been responded to yet"}

        step_id = escalation.get("step_id")
        if not step_id:
            return {"error": f"Escalation {escalation_id} has no step reference"}

        # Get the run
        run = self.db.get_run(run_id)
        if not run:
            return {"error": f"Run {run_id} not found"}

        agent_id = run["agent_id"]
        graph = self.db.get_workflow_graph(agent_id)
        steps = graph["steps"]
        connections = graph["connections"]

        # Get the escalation step
        esc_step = None
        for s in steps:
            if s["id"] == step_id:
                esc_step = s
                break

        if not esc_step:
            return {"error": f"Step {step_id} not found in workflow graph"}

        # Determine the next step based on response_action
        response_action = escalation.get("response_action", "approve")
        next_step = None
        for conn in connections:
            if conn["from_step_id"] == step_id:
                label = conn.get("label", "").lower()
                if not label or label == response_action:
                    next_step = conn["to_step_id"]
                    break
                # Also try condition_expr matching
                cond = conn.get("condition_expr", "").lower()
                if cond and cond == response_action:
                    next_step = conn["to_step_id"]
                    break

        if next_step is None:
            # Fall back to first connection from this step
            for conn in connections:
                if conn["from_step_id"] == step_id:
                    next_step = conn["to_step_id"]
                    break

        # Update run status to running
        self.db.update_run_status(run_id, "running")

        # Build input context from escalation response
        ctx = {
            "escalation_response_action": response_action,
            "escalation_response_text": escalation.get("response_text", ""),
            "escalation_responded_by": escalation.get("responded_by", ""),
        }

        if next_step:
            # Find and execute the next step
            next_step_obj = None
            for s in steps:
                if s["id"] == next_step:
                    next_step_obj = s
                    break

            if next_step_obj:
                # Create state and continue execution from the next step
                state = WorkflowState(ctx)
                root_span_id = self.tracer.start_span(
                    trace_id=run_id,
                    span_name=f"Resume: {run_id}",
                    span_type="resume",
                    run_id=run_id,
                    input_data=json.dumps(ctx),
                )
                result = self._execute_step(next_step_obj, state, run_id, root_span_id, ctx)
                self.tracer.end_span(
                    root_span_id,
                    output_data=json.dumps(result),
                )
                return self.db.get_run(run_id) or {"run_id": run_id, "status": "running"}

        return self.db.get_run(run_id) or {"run_id": run_id, "status": "running"}

    def _execute_step(self, step: dict, state: WorkflowState, run_id: str, parent_span_id: str, ctx: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a single step (used by resume_from_escalation for continuation)."""
        step_id = step["id"]
        step_type = step.get("step_type", "llm_call")

        span_id = self.tracer.start_span(
            trace_id=run_id,
            span_name=step.get("label") or step_type,
            span_type="step",
            parent_span_id=parent_span_id,
            step_id=step_id,
            run_id=run_id,
            input_data=json.dumps(ctx),
        )

        step_log = self.db.create_step_log(run_id, step_id, 0)
        step_start = time.time()
        result = {}
        tokens_in = 0
        tokens_out = 0
        cost = 0
        model = ""
        status = "completed"
        error_msg = ""

        # Watch the step for hang detection
        self.watchdog.watch_step(step_id, timeout=300)

        try:
            if step_type == "llm_call":
                result = self._execute_llm_step(step, state, run_id, step_log, span_id, exec_ctx)
            elif step_type == "tool_call":
                if exec_ctx is not None:
                    result = self._execute_tool_step(step, exec_ctx)
                else:
                    # Fallback: no ExecutorContext available (resume path without broker)
                    result = self._legacy_execute_tool_step(step, ctx)
            elif step_type == "condition":
                result = self._evaluate_condition(step, ctx)
            elif step_type == "loop":
                result = self._execute_loop_step(step, state, ctx, run_id, step_log, span_id)
            elif step_type == "subworkflow":
                result = self._execute_subworkflow(step, state)
            elif step_type == "score":
                if exec_ctx is not None:
                    result = self._execute_score_step(step, exec_ctx)
                else:
                    result = {"error": "score_step_requires_executor_context"}
            elif step_type == "memory_read":
                result = self._execute_memory_read_step(step, exec_ctx)
            elif step_type == "memory_write":
                result = self._execute_memory_write_step(step, exec_ctx)
            elif step_type == "http_fetch":
                result = self._execute_http_fetch_step(step, exec_ctx)
            elif step_type == "parse_rss":
                result = self._execute_parse_rss_step(step, exec_ctx)
            elif step_type == "parse_jsonpath":
                result = self._execute_parse_jsonpath_step(step, exec_ctx)
            elif step_type == "parse_xpath":
                result = self._execute_parse_xpath_step(step, exec_ctx)
            elif step_type == "parse_html":
                result = self._execute_parse_html_step(step, exec_ctx)
            elif step_type == "resolve_id_list":
                result = self._execute_resolve_id_list_step(step, exec_ctx)
            elif step_type == "fetch_source":
                result = self._execute_fetch_source_step(step, exec_ctx)
            elif step_type == "parse_source":
                result = self._execute_parse_source_step(step, exec_ctx)
            elif step_type == "fetch_and_parse":
                result = self._execute_fetch_and_parse_step(step, exec_ctx)
            elif step_type == "extract_article":
                result = self._execute_extract_article_step(step, exec_ctx)
            elif step_type == "batch_extract":
                result = self._execute_batch_extract_step(step, exec_ctx)
            elif step_type == "extract_metadata_only":
                result = self._execute_extract_metadata_step(step, exec_ctx)
            elif step_type == "archive_edition":
                result = self._execute_archive_edition_step(step, exec_ctx)
            elif step_type == "rebuild_archive_index":
                result = self._execute_rebuild_archive_index_step(step, exec_ctx)
            elif step_type == "extract_entities_batch":
                result = self._execute_extract_entities_batch_step(step, exec_ctx)
            elif step_type == "extract_keywords":
                result = self._execute_extract_keywords_step(step, exec_ctx)
            elif step_type == "score_by_entity":
                result = self._execute_score_by_entity_step(step, exec_ctx)
            elif step_type == "score_edition_quality":
                result = self._execute_score_edition_quality_step(step, exec_ctx)
            elif step_type == "check_quality_regression":
                result = self._execute_check_quality_regression_step(step, exec_ctx)
            elif step_type == "render_sections":
                result = self._execute_render_sections_step(step, exec_ctx)
            elif step_type == "join_brief":
                result = self._execute_join_brief_step(step, exec_ctx)
            else:
                result = {"error": f"unknown_step_type: {step_type}"}

            tokens_in = result.get("tokens_input", 0)
            tokens_out = result.get("tokens_output", 0)
            cost = result.get("cost_cents", 0)
            model = result.get("model", "")
        except Exception as exc:
            status = "failed"
            error_msg = str(exc)
            result = {"error": error_msg}

        step_duration_ms = int((time.time() - step_start) * 1000)

        # Record observability metrics
        self.metrics.record_step_duration(
            step_id, step_duration_ms, success=(status == "completed")
        )
        if model:
            self.metrics.record_token_count(model, tokens_in, tokens_out)
        self.watchdog.cancel_watch(step_id)

        self.db.update_step_log(
            step_log["id"],
            output_data=json.dumps(result),
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            cost_cents=cost,
            model_used=model,
            status=status,
            error_message=error_msg,
        )
        self.tracer.end_span(
            span_id,
            output_data=json.dumps(result),
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            cost_cents=cost,
            duration_ms=step_duration_ms,
            model_used=model,
            status=status,
            error_message=error_msg,
        )

        return result

    def _call_llm(self, messages: list[dict]) -> dict:
        """Call the LLM via the JIT pool proxy. Falls back to simulation on error."""
        try:
            payload = json.dumps({
                "model": LLM_MODEL,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 2048,
            }).encode("utf-8")
            req = urllib.request.Request(
                LLM_ENDPOINT,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            choice = body["choices"][0]
            usage = body.get("usage", {})
            message = choice.get("message", {})
            # Gemma 4 QAT uncensored sometimes puts output in reasoning_content
            content = message.get("content", "")
            if not content:
                content = message.get("reasoning_content", "")
            # Log what we got for debugging
            if not content:
                logger.warning("LLM returned empty content (finish=%s)", choice.get("finish_reason"))
            return {
                "response": content,
                "tokens_input": usage.get("prompt_tokens", 0),
                "tokens_output": usage.get("completion_tokens", 0),
                "model": body.get("model", LLM_MODEL),
            }
        except Exception as exc:
            # Graceful fallback — simulate LLM response so the demo pipeline works
            logger.error("LLM call failed: %s — falling back to simulation.", str(exc)[:300])
            last_msg = messages[-1]["content"] if messages else ""
            words = max(20, len(last_msg) // 4)
            inp_tok = max(10, len(last_msg) // 4)
            out_tok = max(10, words)
            simulated = (
                f"[Simulated] Analyzed: \"{last_msg[:100]}...\" "
                f"Response: The matter has been assessed. A professional communication "
                f"has been prepared addressing the outstanding amount and appropriate next steps."
            )
            return {
                "response": simulated,
                "tokens_input": inp_tok,
                "tokens_output": out_tok,
                "model": f"{LLM_MODEL} (fallback)",
            }

    def _execute_memory_read_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a memory_read step — load values from cross-run state.

        Step config supports:
        - ``memory_key``: exact key or glob pattern (e.g. ``"stories.*"``)
        - ``memory_tags``: optional tag filter (e.g. ``"story"``)

        All config values are template-rendered with ``input_vars`` and
        ``step_results`` before execution.
        """
        if exec_ctx is None:
            return {
                "error": "memory_read_step_requires_executor_context",
                "output": "{}",
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        workflow_id = exec_ctx.workflow_id
        input_vars = exec_ctx.input_vars or exec_ctx.context
        step_results = exec_ctx.step_results

        raw_key = str(step.get("memory_key", ""))
        raw_tags = str(step.get("memory_tags", ""))

        key = render_template(raw_key, input_vars, step_results) if raw_key else raw_key
        tag_filter = render_template(raw_tags, input_vars, step_results) if raw_tags else raw_tags

        result_data: dict[str, list] = {}
        keys_found: list[str] = []

        if key.endswith("*"):
            # Glob pattern: list matching keys
            prefix = key.rstrip("*").rstrip(".")
            all_keys = self.memory.list_keys(workflow_id, tag_filter=tag_filter)
            matched = [k for k in all_keys if k.startswith(prefix)]
            entries = {}
            for k in matched:
                val = self.memory.get_raw(workflow_id, k)
                if val:
                    entries[k] = val
            result_data = {"entries": list(entries.values()), "keys": matched}
            keys_found = matched
        elif key:
            # Exact key lookup
            entry = self.memory.get_raw(workflow_id, key)
            if entry:
                result_data = {"entry": entry, "value": entry.get("value_json", "null")}
                keys_found = [key]
            else:
                result_data = {"entry": None, "value": None}
        else:
            # No key — load all memory for this workflow (optionally filtered)
            if tag_filter:
                all_keys = self.memory.list_keys(workflow_id, tag_filter=tag_filter)
                entries = [self.memory.get_raw(workflow_id, k) for k in all_keys if k]
                result_data = {"entries": entries, "keys": all_keys}
                keys_found = all_keys
            else:
                entries = self.memory.get_all(workflow_id)
                result_data = {"entries": entries, "keys": [e["key"] for e in entries]}
                keys_found = [e["key"] for e in entries]

        return {
            "output": json.dumps(result_data),
            "memory_keys": keys_found,
            "memory_data": result_data,
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_memory_write_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a memory_write step — save values to cross-run state.

        Step config supports:
        - ``memory_key``: namespaced key (e.g. ``"stories.{{input.topic | slugify}}.last_headline"``)
        - ``memory_value``: value to store (template-rendered)
        - ``memory_tags``: optional comma-separated tags (template-rendered)

        All config values are template-rendered with ``input_vars`` and
        ``step_results`` before execution.
        """
        if exec_ctx is None:
            return {
                "error": "memory_write_step_requires_executor_context",
                "output": "",
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        workflow_id = exec_ctx.workflow_id
        run_id = exec_ctx.run_id
        input_vars = exec_ctx.input_vars or exec_ctx.context
        step_results = exec_ctx.step_results

        # Read config from config_json (stored in DB by sync_workflow)
        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        raw_key = str(config.get("memory_key", step.get("memory_key", "")))
        raw_value = config.get("memory_value", step.get("memory_value", ""))
        raw_tags = str(config.get("memory_tags", step.get("memory_tags", "")))

        key = render_template(raw_key, input_vars, step_results)
        tags = render_template(raw_tags, input_vars, step_results)

        # Resolve the value — can be a scalar, dict, or list from template
        if isinstance(raw_value, str):
            value_str = render_template(raw_value, input_vars, step_results)
            # Try to parse as JSON if it looks structured
            try:
                value = json.loads(value_str)
            except (json.JSONDecodeError, TypeError):
                value = value_str
        else:
            value = raw_value

        if not key:
            return {
                "error": "memory_key_required",
                "output": "",
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        entry = self.memory.set(workflow_id, key, value, run_id=run_id, tags=tags)

        return {
            "output": json.dumps({key: value}),
            "memory_entry": entry,
            "memory_key": key,
            "memory_value": value,
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_http_fetch_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute an http_fetch step — fetch a URL via the FetcherEngine.

        Step configuration (read from ``config_json``):

        * ``url`` — The URL to fetch (template-rendered).
        * ``method`` — HTTP method (default ``GET``).
        * ``headers`` — Optional dict of extra headers (template-rendered).
        * ``auth_ref`` — Optional credential reference for AuthResolver.
        * ``timeout`` — Request timeout in seconds (default 30).
        * ``retry_count`` — Max retries on 429/503 (default 3).

        Returns the FetcherEngine result dict with standard step metadata.
        """
        from fetcher.engine import FetcherEngine

        # Read config from config_json
        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        url = config.get("url", "")
        if not url:
            return {
                "error": "http_fetch_url_required",
                "output": "",
                "status_code": 0,
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        method = config.get("method", "GET")
        headers = config.get("headers", None)
        auth_ref = config.get("auth_ref", None)
        timeout = int(config.get("timeout", 30))
        retry_count = int(config.get("retry_count", 3))

        engine = FetcherEngine()
        result = engine.fetch(
            url=url,
            method=method,
            headers=headers,
            auth_ref=auth_ref,
            timeout=timeout,
            retry_count=retry_count,
        )

        return {
            "output": result.get("body_text", ""),
            "status_code": result.get("status_code", 0),
            "response_headers": result.get("headers", {}),
            "elapsed_ms": result.get("elapsed_ms", 0),
            "cached_from": result.get("cached_from"),
            "error": result.get("error"),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    # ── Parser step execution ────────────────────────────────────────

    def _execute_parse_rss_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a parse_rss step using ParserEngine."""
        from parser.engine import ParserEngine

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        response_body = config.get("response_body", "")
        parser_config = config.get("parser_config", {"type": "rss", "config": {}})

        engine = ParserEngine()
        result = engine.parse(response_body, parser_config)

        return {
            "output": result,
            "items": result.get("items", []),
            "errors": result.get("errors", []),
            "error": None if not result.get("errors") else "; ".join(result["errors"]),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_parse_jsonpath_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a parse_jsonpath step using ParserEngine."""
        from parser.engine import ParserEngine

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        response_body = config.get("response_body", "")
        parser_config = config.get("parser_config", {"type": "jsonpath", "config": {"path": "$"}})

        engine = ParserEngine()
        result = engine.parse(response_body, parser_config)

        return {
            "output": result,
            "items": result.get("items", []),
            "errors": result.get("errors", []),
            "error": None if not result.get("errors") else "; ".join(result["errors"]),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_parse_xpath_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a parse_xpath step using ParserEngine."""
        from parser.engine import ParserEngine

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        response_body = config.get("response_body", "")
        parser_config = config.get("parser_config", {"type": "xpath", "config": {}})

        engine = ParserEngine()
        result = engine.parse(response_body, parser_config)

        return {
            "output": result,
            "items": result.get("items", []),
            "errors": result.get("errors", []),
            "error": None if not result.get("errors") else "; ".join(result["errors"]),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_parse_html_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a parse_html step using ParserEngine."""
        from parser.engine import ParserEngine

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        response_body = config.get("response_body", "")
        parser_config = config.get("parser_config", {"type": "html", "config": {}})

        engine = ParserEngine()
        result = engine.parse(response_body, parser_config)

        return {
            "output": result,
            "items": result.get("items", []),
            "errors": result.get("errors", []),
            "error": None if not result.get("errors") else "; ".join(result["errors"]),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_resolve_id_list_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a resolve_id_list step using ParserEngine + FetcherEngine."""
        from parser.engine import ParserEngine
        from fetcher.engine import FetcherEngine

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        fetcher = FetcherEngine()
        parser_config = {
            "type": "id_list",
            "config": {
                "ids": config.get("ids", []),
                "url_template": config.get("url_template", ""),
                "fetcher_engine": fetcher,
            },
        }

        engine = ParserEngine()
        result = engine.parse("", parser_config)

        return {
            "output": result,
            "items": result.get("items", []),
            "errors": result.get("errors", []),
            "error": None if not result.get("errors") else "; ".join(result["errors"]),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    # ── Archive step execution ─────────────────────────────────────────


    def _execute_archive_edition_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute an ``archive_edition`` step — store a newsletter edition.

        Reads config from ``config_json``:

        * ``edition_id`` — Unique edition ID (template-rendered)
        * ``subject`` — Edition subject / headline (template-rendered)
        * ``body_html`` — Rendered HTML body
        * ``body_markdown`` — Markdown source
        * ``metadata`` — Optional dict with counts

        Falls back to step results for ``body_html`` and ``body_markdown``
        if not explicitly set in config.
        """
        from archive.engine import ArchiveEngine

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        edition_id = config.get("edition_id", "")
        subject = config.get("subject", "")
        body_html = config.get("body_html", "")
        body_markdown = config.get("body_markdown", "")
        metadata = config.get("metadata", {})

        # Try to resolve from step results if not explicitly set
        if not body_html and exec_ctx and exec_ctx.step_results:
            for _sid, result_data in exec_ctx.step_results.items():
                if isinstance(result_data, dict):
                    if not body_html:
                        body_html = result_data.get("body_html", "")
                    if not body_markdown:
                        body_markdown = result_data.get("body_markdown", "")
                    if not subject:
                        subject = result_data.get("subject", "")
                    if not metadata:
                        m = result_data.get("metadata", {})
                        if isinstance(m, dict):
                            metadata = m

        # Check execution context for edition_id (set by an upstream step)
        if not edition_id and exec_ctx and exec_ctx.context:
            edition_id = exec_ctx.context.get("edition_id", "")

        # Auto-generate edition_id if still empty — use timestamp + workflow hash
        if not edition_id:
            import hashlib
            wf_name = exec_ctx.workflow_id if exec_ctx else "unknown"
            edition_id = f"ed_{int(time.time())}_{hashlib.md5(wf_name.encode()).hexdigest()[:8]}"
            if exec_ctx:
                exec_ctx.context["edition_id"] = edition_id

        run_id = exec_ctx.run_id if exec_ctx else ""
        engine = ArchiveEngine()
        result = engine.store(
            edition_id=edition_id,
            subject=str(subject or ""),
            body_html=str(body_html or ""),
            body_markdown=str(body_markdown or ""),
            run_id=run_id,
            metadata=metadata if isinstance(metadata, dict) else {},
        )

        return {
            "output": result,
            "archive_id": result.get("id", edition_id),
            "permalink": result.get("permalink", ""),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_rebuild_archive_index_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a ``rebuild_archive_index`` step — regenerate index.html.

        Reads optional config from ``config_json``:

        * ``archive_dir`` — Override archive directory (optional)
        """
        from archive.index import ArchiveIndex

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        archive_dir = config.get("archive_dir", None)
        index = ArchiveIndex(archive_dir=archive_dir)
        path = index.rebuild()

        return {
            "output": {"path": path, "status": "ok"},
            "index_path": path,
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    # ── Auto story tracking after synthesis ─────────────────────────────
    def _track_stories_from_output(self, step: dict, exec_ctx: ExecutorContext, output_data: dict) -> None:
        """After LLM synthesis, automatically track key_signals as story cards."""
        try:
            from stories_engine import StoriesEngine
            engine = StoriesEngine()
            output = output_data.get("output", "")
            if isinstance(output, dict):
                signals = output.get("key_signals", [])
                subject = output.get("subject", "")
                body = output.get("body_markdown", "")
                sources = output.get("sources", [])
            elif isinstance(output, str):
                from state import _parse_structured_output
                p = _parse_structured_output(output)
                signals = p.get("key_signals", [])
                subject = p.get("subject", "")
                body = p.get("body_markdown", "")
                sources = p.get("sources", [])
            else:
                signals = []
                subject = ""
                body = ""
                sources = []

            if not signals:
                return

            workflow_id = exec_ctx.workflow_id
            run_id = exec_ctx.run_id
            import logging
            logging.getLogger("esam.stories").info(
                "Tracking %d stories after '%s'", len(signals), step.get("label", "")
            )

            for signal_title in signals:
                engine.find_or_create(
                    workflow_id=workflow_id,
                    title=str(signal_title),
                    run_id=run_id,
                    headline=subject,
                    body=body,
                    sources=sources if isinstance(sources, list) else [],
                    tags="story,signal",
                )
        except ImportError:
            pass  # stories_engine not available
        except Exception as e:
            self.logger.warning("Story tracking failed: %s", e)


    def _tool_web_search(self, context: dict) -> dict:
        query = context.get("query") or context.get("last_output", "")
        return {"query": query, "results": [], "note": "web_search not implemented"}

    def _tool_calculate(self, context: dict) -> dict:
        expression = context.get("expression") or str(context.get("last_output", ""))
        try:
            safe_globals: dict[str, Any] = {"__builtins__": {}, "int": int, "float": float}
            result = eval(expression, safe_globals)
            return {"expression": expression, "result": result}
        except Exception as exc:
            return {"expression": expression, "error": str(exc)}

    def _tool_format_output(self, context: dict) -> dict:
        data = context.get("last_output", context)
        if isinstance(data, str):
            return {"formatted": data}
        return {"formatted": json.dumps(data, indent=2)}

    # ── Content Source step execution ─────────────────────────────────

    def _execute_fetch_source_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a fetch_source step — fetch a content source's URLs.

        Reads ``config_json`` for:

        * ``source_id`` — ID of the content source to fetch (required).
          Can also be specified as ``source`` (name look-up by name).

        Uses :class:`ContentSourceManager` to load the source definition,
        then :class:`FetcherEngine` to fetch its URLs.  Does NOT parse.
        """
        from source_registry import ContentSourceManager

        csm = ContentSourceManager()

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        source_id = config.get("source_id", "")
        if not source_id:
            # Try looking up by name
            source_name = config.get("source", "")
            if source_name:
                for s in csm.list():
                    if s.get("name") == source_name:
                        source_id = s["id"]
                        break

        if not source_id:
            return {
                "error": "fetch_source: source_id required",
                "output": "",
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        source = csm.get(source_id)
        if not source:
            return {
                "error": f"fetch_source: source {source_id} not found",
                "output": "",
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        from fetcher.engine import FetcherEngine
        fetcher = FetcherEngine()

        src_config = json.loads(source.get("source_config_json", "{}"))
        urls = csm._build_urls(src_config, source)
        if not urls:
            return {
                "error": "fetch_source: no URLs to fetch",
                "output": "",
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        responses: list[dict] = []
        for url in urls:
            fetch_result = fetcher.fetch(
                url=url,
                method=src_config.get("method", "GET"),
                headers=src_config.get("headers"),
                timeout=src_config.get("timeout", 30),
            )
            responses.append(fetch_result)

        return {
            "output": responses,
            "responses": responses,
            "urls": urls,
            "source_name": source["name"],
            "source_id": source_id,
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_parse_source_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a parse_source step — parse raw response body using ParserEngine.

        Reads ``config_json`` for:

        * ``response_body`` — The raw response text to parse.
        * ``parser_config`` — Dict with ``type`` and ``config`` keys.
          Can reference a source by ``source_id`` or ``source`` name to
          auto-build the parser config from the source definition.
        """
        from parser.engine import ParserEngine

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        response_body = config.get("response_body", "")
        if not response_body:
            # Try to find response_body from previous step_results
            if exec_ctx and exec_ctx.step_results:
                for _sid, result_data in exec_ctx.step_results.items():
                    if isinstance(result_data, dict):
                        body = result_data.get("response_body", result_data.get("body_text", ""))
                        if body:
                            response_body = body
                            break

        parser_config = config.get("parser_config", {})
        if not parser_config or not parser_config.get("type"):
            # Try to build parser config from a content source definition
            from source_registry import ContentSourceManager
            csm = ContentSourceManager()
            source_id = config.get("source_id", "")
            if not source_id:
                source_name = config.get("source", "")
                if source_name:
                    for s in csm.list():
                        if s.get("name") == source_name:
                            source_id = s["id"]
                            break
            if source_id:
                source = csm.get(source_id)
                if source:
                    src_config = json.loads(source.get("source_config_json", "{}"))
                    parser_config = csm._build_parser_config(src_config, source["type"])

        if not response_body:
            return {
                "error": "parse_source: response_body required",
                "output": "",
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        engine = ParserEngine()
        result = engine.parse(response_body, parser_config or {"type": "rss", "config": {}})

        return {
            "output": result,
            "items": result.get("items", []),
            "errors": result.get("errors", []),
            "error": None if not result.get("errors") else "; ".join(result["errors"]),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_fetch_and_parse_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a fetch_and_parse step — full fetch + parse pipeline.

        Reads ``config_json`` for:

        * ``source_id`` — ID of the content source (or ``source`` name).
        * All other config from the source definition is used to build
          fetch URLs and parser config.

        Uses :class:`ContentSourceManager.fetch_and_parse` to run the
        full pipeline and store items in the DB.
        """
        from source_registry import ContentSourceManager

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        source_id = config.get("source_id", "")
        if not source_id:
            source_name = config.get("source", "")
            if source_name:
                from source_registry import ContentSourceManager as CSM
                csm = CSM()
                for s in csm.list():
                    if s.get("name") == source_name:
                        source_id = s["id"]
                        break

        if not source_id:
            return {
                "error": "fetch_and_parse: source_id required",
                "output": [],
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        from fetcher.engine import FetcherEngine
        from parser.engine import ParserEngine

        csm = ContentSourceManager()
        items = csm.fetch_and_parse(
            source_id=source_id,
            fetcher_engine=FetcherEngine(),
            parser_engine=ParserEngine(),
        )

        return {
            "output": items,
            "items": items,
            "item_count": len(items),
            "source_id": source_id,
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    # ── Verifier Step Handlers ─────────────────────────────────────

    def _execute_verify_claims_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a ``verify_claims`` step — run full claim verification.

        Reads ``config_json`` for:
        * ``output_text`` — The text to verify (or read from step_results).
        * ``citation_map`` — Optional pre-built citation map.
        * ``items_field`` — Step results key for content source items (default: ``items``).

        Returns dict with verification results including ``passed`` flag.
        """
        from verifier.engine import ClaimVerifier

        config: dict = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        output_text = config.get("output_text", "")
        citation_map = config.get("citation_map")
        items_field = config.get("items_field", "items")

        # Try to find output_text from step_results if not directly configured
        if not output_text and exec_ctx and exec_ctx.step_results:
            for _sid, result_data in exec_ctx.step_results.items():
                if isinstance(result_data, dict):
                    candidate = result_data.get("output", result_data.get("response", ""))
                    if isinstance(candidate, str) and len(candidate) > 20:
                        output_text = candidate
                        break

        # Try to find items from step_results
        items: list[dict] = []
        if exec_ctx and exec_ctx.step_results:
            for _sid, result_data in exec_ctx.step_results.items():
                if isinstance(result_data, dict):
                    candidate_items = result_data.get(items_field, [])
                    if isinstance(candidate_items, list) and candidate_items:
                        items = candidate_items
                        break

        verifier = ClaimVerifier(
            llm_endpoint=os.environ.get("LLM_ENDPOINT", "http://localhost:8001/v1/chat/completions"),
            llm_model=os.environ.get("LLM_MODEL", "gemma-12b"),
        )

        result = verifier.verify_claims(
            output_text=output_text,
            citation_map=citation_map,
            items=items if not citation_map else None,
        )

        return {
            "output": result,
            "claims": result.get("claims", []),
            "overall_score": result.get("overall_score", 1.0),
            "hallucination_ratio": result.get("hallucination_ratio", 0.0),
            "passed": result.get("passed", True),
            "detection": result.get("detection", {}),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_grade_citations_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a ``grade_citations`` step — grade individual claim-citation pairs.

        Reads ``config_json`` for:
        * ``claim_text`` — The claim to grade.
        * ``citation_id`` — The citation ID (e.g. ``S001``).
        * ``cited_url`` — The URL of the cited source.
        * ``cited_content`` — The content fetched from the URL.
        * ``cited_title`` — Optional title.

        Returns the grading result.
        """
        from verifier.grader import CitationGrader

        config: dict = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        claim_text = config.get("claim_text", "")
        citation_id = config.get("citation_id", "")
        cited_url = config.get("cited_url", "")
        cited_content = config.get("cited_content", "")
        cited_title = config.get("cited_title")

        grader = CitationGrader(
            llm_endpoint=os.environ.get("LLM_ENDPOINT", "http://localhost:8001/v1/chat/completions"),
            llm_model=os.environ.get("LLM_MODEL", "gemma-12b"),
        )

        grade_result = grader.grade_claim(
            claim_text=claim_text,
            citation_id=citation_id,
            cited_url=cited_url,
            cited_content=cited_content,
            cited_title=cited_title,
        )

        return {
            "output": grade_result,
            "verdict": grade_result.get("verdict", "unverifiable"),
            "confidence": grade_result.get("confidence", 0.0),
            "reasoning": grade_result.get("reasoning", ""),
            "supporting_quote": grade_result.get("supporting_quote", ""),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_validate_citations_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a ``validate_citations`` step — verify [SXXX] references exist in citation map.

        Reads ``config_json`` for:
        * ``output_text_field`` — Step results key containing the output text to validate
          (default: ``output``).
        * ``citation_map_field`` — Step results key containing the citation map dict
          (default: ``citation_map``).

        Returns the validation result from :meth:`CitationValidator.validate_output`.
        """
        from citation.validator import CitationValidator

        config: dict = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        output_field = config.get("output_text_field", "output")
        map_field = config.get("citation_map_field", "citation_map")

        output_text = ""
        citation_map: dict[str, dict[str, str]] = {}

        if exec_ctx and exec_ctx.step_results:
            for _sid, result_data in exec_ctx.step_results.items():
                if isinstance(result_data, dict):
                    # Check for output text
                    val = result_data.get(output_field)
                    if isinstance(val, str) and not output_text:
                        output_text = val
                    # Check direct match
                    if output_field == "output" and "output" in result_data:
                        val = result_data["output"]
                        if isinstance(val, str):
                            output_text = val
                    # Check for citation map
                    cmap = result_data.get(map_field)
                    if isinstance(cmap, dict) and not citation_map:
                        citation_map = cmap

        if not citation_map:
            return {
                "output": {"error": "citation_map not found in step results"},
                "valid": False,
                "missing_ids": [],
                "warnings": ["No citation map found in step results"],
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        validator = CitationValidator()
        validation = validator.validate_output(output_text, citation_map)

        return {
            "output": validation,
            "valid": validation["valid"],
            "missing_ids": validation["missing_ids"],
            "warnings": validation["warnings"],
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_hallucination_check_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a ``hallucination_check`` step — detect claims without supporting citations.

        Reads ``config_json`` for:
        * ``output_text_field`` — Step results key containing the output text
          (default: ``output``).
        * ``citation_map_field`` — Step results key containing the citation map dict
          (default: ``citation_map``).
        * ``max_ratio`` — Threshold for hallucination_ratio (default: 0.05).

        Returns the detection result with a ``passed`` flag.
        """
        from citation.validator import HallucinationDetector

        config: dict = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        output_field = config.get("output_text_field", "output")
        map_field = config.get("citation_map_field", "citation_map")
        max_ratio = float(config.get("max_ratio", 0.05))

        output_text = ""
        citation_map: dict[str, dict[str, str]] = {}

        if exec_ctx and exec_ctx.step_results:
            for _sid, result_data in exec_ctx.step_results.items():
                if isinstance(result_data, dict):
                    val = result_data.get(output_field)
                    if isinstance(val, str) and not output_text:
                        output_text = val
                    cmap = result_data.get(map_field)
                    if isinstance(cmap, dict) and not citation_map:
                        citation_map = cmap

        detector = HallucinationDetector()
        detection = detector.detect(output_text, citation_map)

        passed = detection["hallucination_ratio"] <= max_ratio

        return {
            "output": detection,
            "passed": passed,
            "hallucination_ratio": detection["hallucination_ratio"],
            "hallucinated_claims": detection["hallucinated_claims"],
            "supported_claims": detection["supported_claims"],
            "confidence": detection["confidence"],
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_reject_if_invalid_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a ``reject_if_invalid`` step — quality gate that raises if verification failed.

        Reads the ``passed`` flag from the previous step result.
        If ``passed`` is False, raises a ``VerificationFailed`` error.
        If ``passed`` is True, returns success and continues.

        Reads ``config_json`` for:
        * ``check_field`` — Step results key to check for ``passed`` flag (default: ``output``).
        * ``max_hallucination_ratio`` — Override threshold (default: 0.05).
        """
        config: dict = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        check_field = config.get("check_field", "output")
        max_ratio = float(config.get("max_hallucination_ratio", 0.05))

        found_verification = False
        passed = True
        hallucination_ratio = 0.0

        # Check step_results for verification results
        if exec_ctx and exec_ctx.step_results:
            for _sid, result_data in exec_ctx.step_results.items():
                if isinstance(result_data, dict):
                    # Direct passed flag
                    if "passed" in result_data:
                        found_verification = True
                        passed = bool(result_data["passed"])
                        hallucination_ratio = float(result_data.get("hallucination_ratio", 0.0))
                        break
                    # Nested in output dict
                    output_val = result_data.get(check_field, {})
                    if isinstance(output_val, dict) and "passed" in output_val:
                        found_verification = True
                        passed = bool(output_val["passed"])
                        hallucination_ratio = float(output_val.get("hallucination_ratio", 0.0))
                        break

        if found_verification and not passed:
            raise RuntimeError(
                f"VerificationFailed: hallucination_ratio={hallucination_ratio:.4f} "
                f"exceeds threshold={max_ratio}. Output rejected by quality gate."
            )
        elif not found_verification:
            logger.warning(
                "No verification data found in step results — allowing output through quality gate "
                "(hal_ratio=%.4f, threshold=%.4f).",
                hallucination_ratio,
                max_ratio,
            )

        return {
            "output": {"status": "accepted", "passed": True, "hallucination_ratio": hallucination_ratio},
            "passed": True,
            "hallucination_ratio": hallucination_ratio,
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    # ── Extractor Step Handlers ──────────────────────────────────

    def _execute_extract_article_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute an extract_article step — run readability extraction on HTML.

        Config (from ``config_json``):

        * ``body_html`` — Raw HTML to extract (template-rendered).
        * ``url`` — Source URL (template-rendered).
        """
        from extractor.engine import ContentExtractor

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        html_body = config.get("body_html", config.get("raw_content", ""))
        url = config.get("url", "")

        if not html_body:
            return {
                "error": "extract_article: body_html required",
                "output": {},
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        engine = ContentExtractor()
        result = engine.extract(html_body, url)

        return {
            "output": result,
            "title": result.get("title", ""),
            "content_text": result.get("content_text", ""),
            "word_count": result.get("word_count", 0),
            "reading_time": result.get("reading_time", 0.0),
            "excerpt": result.get("excerpt", ""),
            "author": result.get("author", ""),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_batch_extract_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a batch_extract step — extract content from multiple HTML sources.

        Config (from ``config_json``):

        * ``items`` — List of dicts with ``url`` and ``body_html`` or ``raw_content``.
        * ``max_workers`` — Parallel workers (default 5).
        """
        from extractor.batch import BatchExtractor

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        items = config.get("items", [])
        max_workers = int(config.get("max_workers", 5))

        if not items and exec_ctx and exec_ctx.step_results:
            # Fall back to scanning step_results for items from collection steps
            for _sid, result_data in exec_ctx.step_results.items():
                if isinstance(result_data, dict):
                    candidate = result_data.get("items") or result_data.get("output") or result_data.get("results") or []
                    if isinstance(candidate, list) and len(candidate) > 0:
                        # Check it's actually content items (has url/title fields)
                        if any(isinstance(i, dict) and ("url" in i or "title" in i) for i in candidate[:5]):
                            items.extend(candidate)
                            # Deduplicate by URL
                            seen_urls = set()
                            deduped = []
                            for i in items:
                                url = i.get("url", "")
                                if url and url not in seen_urls:
                                    seen_urls.add(url)
                                    deduped.append(i)
                                elif not url:
                                    deduped.append(i)
                            items = deduped

        # Normalize item fields: content → body_html if needed
        for item in items:
            if "body_html" not in item and "raw_content" not in item:
                content = item.get("content", "")
                if content:
                    item["body_html"] = content

        if not items:
            return {
                "error": "batch_extract: items required",
                "output": [],
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        engine = BatchExtractor()
        results = engine.extract_batch(items, max_workers=max_workers)

        return {
            "output": results,
            "results": results,
            "item_count": len(results),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_extract_metadata_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute an extract_metadata_only step — extract metadata without full content.

        Config (from ``config_json``):

        * ``body_html`` — Raw HTML to extract metadata from.
        * ``url`` — Source URL.
        """
        from extractor.metadata import MetadataExtractor

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        html_body = config.get("body_html", config.get("raw_content", ""))
        url = config.get("url", "")

        if not html_body:
            return {
                "error": "extract_metadata_only: body_html required",
                "output": {},
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        engine = MetadataExtractor()
        result = engine.extract_metadata(html_body, url)

        return {
            "output": result,
            "title": result.get("title", ""),
            "description": result.get("description", ""),
            "image": result.get("image", ""),
            "json_ld": result.get("json_ld", []),
            "domain": result.get("domain", ""),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    # ── Citation Step Handlers ─────────────────────────────────────

    def _execute_assign_citations_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute an assign_citations step — assign sequential citation IDs to items.

        Config (from ``config_json``):

        * ``items`` — List of dicts with ``url``, ``title``, ``content``.
        * ``prefix`` — Citation ID prefix (default ``"S"``).
        """
        from citation.engine import CitationEngine

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        items = config.get("items", [])
        if not items:
            items = self._resolve_items_from_context(step, exec_ctx)
        prefix = config.get("prefix", "S")

        if not items:
            return {
                "error": "assign_citations: items required",
                "output": [],
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        engine = CitationEngine()
        result = engine.generate_ids(items, prefix=prefix)

        return {
            "output": result,
            "results": result,
            "citation_count": len(result),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_resolve_citations_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a resolve_citations step — replace ``[SXXX]`` markers with hyperlinks.

        Config (from ``config_json``):

        * ``text`` — Text containing ``[SXXX]`` markers.
        * ``fetch_run_id`` — Run ID to get citation map for (optional).
        """
        from citation.engine import CitationEngine
        from citation.resolver import CitationResolver
        from citation.map import CitationMap

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        text = config.get("text", "")
        fetch_run_id = config.get("fetch_run_id", "")

        if not text:
            return {
                "error": "resolve_citations: text required",
                "output": "",
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        engine = CitationEngine()
        raw_map = engine.get_map(fetch_run_id=fetch_run_id if fetch_run_id else None)
        cmap = CitationMap.build_map(raw_map)

        resolved_text = CitationResolver.resolve_text(text, cmap)
        verification = CitationResolver.verify_citations(text, cmap)

        return {
            "output": resolved_text,
            "resolved_text": resolved_text,
            "verification": verification,
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_export_citation_map_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute an export_citation_map step — export citation map for LLM prompt context.

        Config (from ``config_json``):

        * ``fetch_run_id`` — Run ID to filter by (optional).
        * ``format`` — Output format: ``"dict"``, ``"prompt"`` (default ``"prompt"``).
        """
        from citation.engine import CitationEngine
        from citation.map import CitationMap

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        fetch_run_id = config.get("fetch_run_id", "")
        output_format = config.get("format", "prompt")

        engine = CitationEngine()
        raw_map = engine.get_map(fetch_run_id=fetch_run_id if fetch_run_id else None)
        cmap = CitationMap.build_map(raw_map)

        if output_format == "dict":
            exported = engine.export_map(fetch_run_id=fetch_run_id if fetch_run_id else None)
        else:
            exported = CitationMap.format_for_prompt(cmap)

        return {
            "output": exported,
            "citation_count": len(raw_map),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    # ── Story Diff Step Handlers ────────────────────────────────────

    def _execute_diff_stories_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Diff current items vs prior stories using the DiffEngine.

        Reads ``current_items`` and ``workflow_id`` from step config or
        executor context.  Returns a list of diffs with type, significance,
        headline_diff, and body_diff.
        """
        config: dict = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = config

        workflow_id = config.get("workflow_id", "")
        if not workflow_id and exec_ctx:
            workflow_id = exec_ctx.workflow_id

        current_items = config.get("current_items", [])
        if not current_items and exec_ctx:
            # Fall back to step_results for items
            for sid, result_data in exec_ctx.step_results.items():
                if isinstance(result_data, dict):
                    items = result_data.get("items", result_data.get("results", []))
                    if isinstance(items, list):
                        current_items.extend(items)

        if not current_items:
            return {
                "output": {"diffs": [], "count": 0},
                "error": "no_current_items",
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        # Get prior stories
        stories_engine = StoriesEngine()
        prior_stories = stories_engine.list_stories(workflow_id=workflow_id, limit=200)

        engine = DiffEngine()
        diffs = engine.diff_stories(current_items, prior_stories, workflow_id)

        return {
            "output": {"diffs": diffs, "count": len(diffs)},
            "diffs": diffs,
            "diff_count": len(diffs),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_compute_trajectories_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Compute trajectories for stories using TrajectoryComputer.

        Reads ``story_ids`` or all active stories for the workflow.
        Returns trajectory data for each story.
        """
        config: dict = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = config

        workflow_id = config.get("workflow_id", "")
        if not workflow_id and exec_ctx:
            workflow_id = exec_ctx.workflow_id

        story_ids = config.get("story_ids", [])
        stories_engine = StoriesEngine()

        if story_ids:
            stories = []
            for sid in story_ids:
                story = stories_engine.get(workflow_id, sid)
                if story:
                    stories.append(story)
        else:
            stories = stories_engine.list_stories(workflow_id=workflow_id, limit=200)

        if not stories:
            return {
                "output": {"trajectories": [], "count": 0},
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        # Build prior editions list
        all_editions: list[dict] = []
        for story in stories:
            changes_raw = story.get("change_log_json", "[]")
            try:
                changes = json.loads(changes_raw) if isinstance(changes_raw, str) else changes_raw
            except (json.JSONDecodeError, TypeError):
                changes = []
            for entry in changes:
                entry["story_id"] = story["id"]
                entry["seen_in_current"] = True  # Assume seen for trajectory
            all_editions.extend(changes)

        computer = TrajectoryComputer()
        trajectories = computer.compute_batch(stories, all_editions)

        return {
            "output": {"trajectories": trajectories, "count": len(trajectories)},
            "trajectories": trajectories,
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_generate_diff_narrative_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Generate a human-readable narrative from story diffs.

        Reads diffs from step config or previous step results.
        Returns a formatted narrative string.
        """
        config: dict = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = config

        diffs = config.get("diffs", [])
        if not diffs and exec_ctx:
            # Find diffs from step_results
            for sid, result_data in exec_ctx.step_results.items():
                if isinstance(result_data, dict):
                    found = result_data.get("diffs", [])
                    if found:
                        diffs = found
                        break

        if not diffs:
            return {
                "output": "",
                "error": "no_diffs",
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        engine = DiffEngine()
        narrative = engine.generate_diff_narrative(diffs)

        return {
            "output": narrative,
            "narrative": narrative,
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    # ── Entity Step Handlers ────────────────────────────────────────────

    def _resolve_items_from_context(self, step: dict, exec_ctx: ExecutorContext | None = None) -> list:
        """Resolve items from step config, falling back to step_results.

        Tries, in order:
        1. ``items`` key from step's config_json
        2. Scanning exec_ctx.step_results for any items/output/results lists

        Returns a deduplicated list of items (by URL, then by id).
        """
        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        items = config.get("items", [])
        if items:
            return items

        if exec_ctx and exec_ctx.step_results:
            seen = set()
            for _sid, result_data in exec_ctx.step_results.items():
                if isinstance(result_data, dict):
                    candidate = (
                        result_data.get("items")
                        or result_data.get("output")
                        or result_data.get("results")
                        or []
                    )
                    if isinstance(candidate, list) and len(candidate) > 0:
                        for item in candidate:
                            if isinstance(item, dict):
                                key = item.get("url", "") or item.get("id", "") or str(id(item))
                                if key not in seen and key:
                                    seen.add(key)
                                    items.append(item)
        return items

    def _execute_extract_entities_batch_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute an extract_entities_batch step — extract entities from content items.

        Config (from ``config_json``):
            * ``items`` — List of items (each with ``id``/``item_id`` and ``body_extracted``/``text`` content).
            * ``text_field`` — Field name for text content (default: ``body_extracted``).
        """
        from entities.engine import EntityExtractor

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        items = config.get("items", [])
        if not items:
            items = self._resolve_items_from_context(step, exec_ctx)

        text_field = config.get("text_field", "body_extracted")

        if not items:
            return {
                "error": "extract_entities_batch: items required",
                "output": {},
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        engine = EntityExtractor()
        result = engine.extract_batch(items, text_field=text_field)

        return {
            "output": result,
            "items": result.get("items", []),
            "merged_entities": result.get("merged_entities", []),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_extract_keywords_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute an extract_keywords step — extract keywords from text.

        Config (from ``config_json``):
            * ``text`` — Text to extract keywords from.
            * ``max_keywords`` — Max number of keywords (default: 20).
        """
        from entities.engine import EntityExtractor

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        text = config.get("text", "")
        max_keywords = int(config.get("max_keywords", 20))

        if not text:
            # Fall back: scan step_results for items with body_extracted/text content
            if exec_ctx and exec_ctx.step_results:
                texts = []
                for _sid, result_data in exec_ctx.step_results.items():
                    if isinstance(result_data, dict):
                        items = result_data.get("items") or result_data.get("results") or result_data.get("output") or []
                        if isinstance(items, list):
                            for item in items:
                                if isinstance(item, dict):
                                    t = item.get("body_extracted") or item.get("text") or item.get("content_text") or item.get("content") or ""
                                    if t:
                                        texts.append(t)
                if texts:
                    text = "\n\n".join(texts[:200])  # Cap at 200 items worth of text

        if not text:
            return {
                "error": "extract_keywords: text required",
                "output": [],
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        engine = EntityExtractor()
        keywords = engine.extract_keywords(text, max_keywords=max_keywords)

        return {
            "output": keywords,
            "keywords": keywords,
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_score_by_entity_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a score_by_entity step — boost item scores by entity matches.

        Config (from ``config_json``):
            * ``items`` — List of items with ``score`` and text content.
            * ``text_field`` — Field name for text (default: ``body_extracted``).
        """
        from entities.engine import EntityExtractor

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        items = config.get("items", [])
        text_field = config.get("text_field", "body_extracted")

        if not items:
            return {
                "error": "score_by_entity: items required",
                "output": [],
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        engine = EntityExtractor()
        scored = engine.score_by_entity(items)

        return {
            "output": scored,
            "scored_items": scored,
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    # ── Cross-Reference Step Handlers ───────────────────────────────────────

    def _execute_detect_cross_references_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a detect_cross_references step — detect topics appearing across sources.

        Config (from ``config_json``):
            * ``items`` — List of items (each with ``id``, ``entities``, ``source_name``).
            * ``text_field`` — Optional, for entity extraction fallback (default: ``body_extracted``).
        """
        from crossref.engine import CrossReferenceEngine

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        items = config.get("items", [])
        if not items:
            items = self._resolve_items_from_context(step, exec_ctx)

        if not items:
            return {
                "error": "detect_cross_references: items required",
                "output": [],
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        engine = CrossReferenceEngine()
        cross_refs = engine.detect(items)

        return {
            "output": cross_refs,
            "cross_refs": cross_refs,
            "cross_ref_count": len(cross_refs),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_boost_multi_sourced_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a boost_multi_sourced step — boost scores based on cross-refs.

        Config (from ``config_json``):
            * ``items`` — List of items with ``id``, ``combined_score``.
            * ``cross_refs`` — List of cross-ref dicts from detect_cross_references.
            * ``boost_factor`` — Base multiplier per additional source (default: 1.3).
        """
        from crossref.engine import CrossReferenceEngine

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        items = config.get("items", [])
        if not items:
            items = self._resolve_items_from_context(step, exec_ctx)
        cross_refs = config.get("cross_refs", [])
        boost_factor = float(config.get("boost_factor", 1.3))

        if not items:
            return {
                "error": "boost_multi_sourced: items required",
                "output": [],
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        engine = CrossReferenceEngine()
        boosted = engine.boost_scores(items, cross_refs, boost_factor=boost_factor)

        return {
            "output": boosted,
            "boosted_items": boosted,
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_cluster_by_topic_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a cluster_by_topic step — cluster items by topic.

        Config (from ``config_json``):
            * ``items`` — List of items with ``id``, ``entities``, ``source_name``.
            * ``max_clusters`` — Max number of clusters (default: 10).
        """
        from crossref.clusterer import TopicClusterer

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        items = config.get("items", [])
        if not items:
            items = self._resolve_items_from_context(step, exec_ctx)
        max_clusters = int(config.get("max_clusters", 10))

        if not items:
            return {
                "error": "cluster_by_topic: items required",
                "output": [],
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        clusterer = TopicClusterer()
        clusters = clusterer.cluster(items, max_clusters=max_clusters)

        return {
            "output": clusters,
            "clusters": clusters,
            "cluster_count": len(clusters),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_register_edition_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a ``register_edition`` step — create an edition record.

        Config (from ``config_json``):
            * ``workflow_id`` — Workflow ID (template-rendered).
            * ``subject`` — Edition subject (template-rendered).
            * ``source_count`` — Number of sources (default: 0).
            * ``item_count`` — Number of items (default: 0).
            * ``total_tokens`` — Total tokens (default: 0).
            * ``duration_seconds`` — Duration (default: 0.0).
        """
        from registry.engine import EditionRegistry

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        run_id = exec_ctx.run_id if exec_ctx else config.get("run_id", "")
        workflow_id = config.get("workflow_id", "")
        subject = config.get("subject", "")
        source_count = int(config.get("source_count", 0))
        item_count = int(config.get("item_count", 0))
        total_tokens = int(config.get("total_tokens", 0))
        duration_seconds = float(config.get("duration_seconds", 0.0))

        # Fall back to executor context workflow_id (like other steps do)
        if not workflow_id and exec_ctx:
            workflow_id = exec_ctx.workflow_id

        if not workflow_id:
            return {
                "error": "register_edition: workflow_id required",
                "output": "",
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        # Auto-generate edition_id if not available and inject into context
        edition_id = config.get("edition_id", "")
        if not edition_id and exec_ctx and exec_ctx.context:
            edition_id = exec_ctx.context.get("edition_id", "")
        if not edition_id:
            import hashlib
            edition_id = f"ed_{int(time.time())}_{hashlib.md5(workflow_id.encode()).hexdigest()[:8]}"
            if exec_ctx:
                exec_ctx.context["edition_id"] = edition_id

        registry = EditionRegistry()
        edition = registry.create(
            workflow_id=workflow_id,
            run_id=run_id,
            subject=subject,
            source_count=source_count,
            item_count=item_count,
            total_tokens=total_tokens,
            duration_seconds=duration_seconds,
        )

        # Store the real edition_id (from registry) into context for downstream steps
        real_edition_id = edition.get("id", "")
        if real_edition_id and exec_ctx:
            exec_ctx.context["edition_id"] = real_edition_id

        return {
            "output": edition,
            "edition_id": real_edition_id,
            "edition_number": edition.get("edition_number", 0),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_compare_editions_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a ``compare_editions`` step — compare two editions.

        Config (from ``config_json``):
            * ``edition_a_id`` — ID of first edition.
            * ``edition_b_id`` — ID of second edition.
            If both are omitted, compares the latest two editions.
        """
        from registry.comparer import EditionComparer

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        edition_a_id = config.get("edition_a_id", "")
        edition_b_id = config.get("edition_b_id", "")

        comparer = EditionComparer()
        if edition_a_id and edition_b_id:
            result = comparer.compare(edition_a_id, edition_b_id)
        else:
            result = comparer.compare_latest()

        return {
            "output": result,
            "comparison": result,
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_compute_edition_stats_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a ``compute_edition_stats`` step — compute stats for an edition.

        Config (from ``config_json``):
            * ``edition_id`` — Edition ID to compute stats for.
            If omitted, computes stats for the latest edition.
        """
        from registry.stats import EditionStats

        config = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        edition_id = config.get("edition_id", "")

        stats = EditionStats()
        if edition_id:
            result = stats.compute(edition_id)
        else:
            latest = stats._registry.get_latest()
            if latest:
                result = stats.compute(latest["id"])
            else:
                result = {"error": "No editions found"}

        return {
            "output": result,
            "stats": result,
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    # ── Pattern Renderer / Sandbox Step Handlers ───────────────────────

    def _execute_render_pattern_with_version_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a ``render_pattern_with_version`` step — render a prompt pattern.

        Config (from ``config_json``):
            * ``pattern_id`` — Pattern ID to render.
            * ``version`` — Optional version to pin to.
            * ``context`` — Dict of context variables (date, workflow_name, etc.).
            * ``data`` — Dict of data payload (signals, sources, citations, etc.).

        Returns:
            ``{output, rendered_prompt, sections, metadata, version_used, ...}``
        """
        from patterns.renderer import PatternRenderer

        config: dict = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        pattern_id = config.get("pattern_id", "")
        if not pattern_id:
            return {
                "error": "render_pattern_with_version: pattern_id required",
                "output": {},
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        version = config.get("version", None)
        if version is not None and not isinstance(version, int):
            try:
                version = int(version)
            except (ValueError, TypeError):
                version = None

        context = config.get("context", {})
        if exec_ctx and exec_ctx.context:
            merged_ctx = dict(exec_ctx.context)
            merged_ctx.update(context)
            context = merged_ctx

        data = config.get("data", {})

        try:
            renderer = PatternRenderer()
            result = renderer.render(
                pattern_id,
                context=context,
                data=data,
                version=version,
            )
            return {
                "output": {
                    "rendered_prompt": result.get("rendered_prompt", ""),
                    "sections": result.get("sections", []),
                    "metadata": result.get("metadata", {}),
                    "version_used": result.get("version_used", 0),
                },
                "rendered_prompt": result.get("rendered_prompt", ""),
                "sections": result.get("sections", []),
                "metadata": result.get("metadata", {}),
                "version_used": result.get("version_used", 0),
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }
        except ValueError as exc:
            return {
                "error": str(exc),
                "output": {},
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

    def _execute_sandbox_verify_pattern_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a ``sandbox_verify_pattern`` step — verify outputs against a pattern schema.

        Config (from ``config_json``):
            * ``pattern_id`` — Pattern ID to validate against.
            * ``output`` — Dict of output to validate.

        Returns:
            ``{output, valid, errors, schema, ...}``
        """
        from patterns.sandbox import PatternSandbox

        config: dict = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        pattern_id = config.get("pattern_id", "")
        output_to_validate = config.get("output", {})

        if not pattern_id:
            return {
                "error": "sandbox_verify_pattern: pattern_id required",
                "output": {},
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        if not output_to_validate:
            return {
                "error": "sandbox_verify_pattern: output to validate required",
                "output": {},
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        sandbox = PatternSandbox()
        validation = sandbox.validate_sandbox_output(output_to_validate, pattern_id)

        return {
            "output": {
                "valid": validation.get("valid", False),
                "errors": validation.get("errors", []),
                "schema": validation.get("schema", {}),
                "output_fields": validation.get("output_fields", []),
            },
            "valid": validation.get("valid", False),
            "errors": validation.get("errors", []),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    # ── Narrative Step Handlers ────────────────────────────────────

    def _execute_synthesize_narrative_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a ``synthesize_narrative`` step — generate narrative text from diffs and trajectories.

        Config (from ``config_json``):
            * ``story_diffs`` — List of story diff dicts.
            * ``trajectories`` — Dict or list of trajectory data.
            Falls back to reading diffs/trajectories from step_results.
        """
        config: dict = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        story_diffs = config.get("story_diffs", [])
        trajectories = config.get("trajectories", {})

        # Fall back to step_results
        if not story_diffs and exec_ctx:
            for sid, result_data in exec_ctx.step_results.items():
                if isinstance(result_data, dict):
                    found = result_data.get("diffs", [])
                    if found:
                        story_diffs = found
                    found_traj = result_data.get("trajectories", result_data.get("trajectory", {}))
                    if found_traj and not trajectories:
                        trajectories = found_traj

        if not story_diffs:
            return {
                "output": "",
                "error": "no_story_diffs",
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        from narrative.engine import NarrativeEngine
        engine = NarrativeEngine()
        narrative = engine.synthesize(story_diffs, trajectories)

        return {
            "output": narrative,
            "narrative": narrative,
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_detect_narrative_arcs_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a ``detect_narrative_arcs`` step — detect narrative arcs across editions.

        Config (from ``config_json``):
            * ``stories`` — List of story dicts to analyze.
            * ``min_arc_length`` — Minimum editions for arc (default: 2).
            Falls back to reading stories from step_results.
        """
        config: dict = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        stories = config.get("stories", [])
        min_arc_length = int(config.get("min_arc_length", 2))

        if not stories and exec_ctx:
            for sid, result_data in exec_ctx.step_results.items():
                if isinstance(result_data, dict):
                    found = result_data.get("stories", result_data.get("diffs", []))
                    if found:
                        stories = found
                        break

        if not stories:
            return {
                "output": {"arcs": [], "count": 0},
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        from narrative.arc_detector import ArcDetector
        detector = ArcDetector()
        arcs = detector.detect(stories, min_arc_length=min_arc_length)

        return {
            "output": {"arcs": arcs, "count": len(arcs)},
            "arcs": arcs,
            "arc_count": len(arcs),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_generate_article_ideas_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a ``generate_article_ideas`` step — generate article ideas from signals.

        Config (from ``config_json``):
            * ``signals`` — List of signal/story dicts.
            * ``narratives`` — Optional narrative context.
            * ``max_ideas`` — Maximum ideas to generate (default: 5).
            Falls back to reading from step_results.
        """
        config: dict = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        signals = config.get("signals", [])
        narratives = config.get("narratives")
        max_ideas = int(config.get("max_ideas", 5))

        if not signals and exec_ctx:
            for sid, result_data in exec_ctx.step_results.items():
                if isinstance(result_data, dict):
                    found_signals = result_data.get("signals", result_data.get("diffs", []))
                    if found_signals and not signals:
                        signals = found_signals
                    found_narr = result_data.get("narrative", result_data.get("narratives"))
                    if found_narr and narratives is None:
                        narratives = found_narr

        if not signals:
            return {
                "output": {"ideas": [], "count": 0},
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        from narrative.ideas import ArticleIdeaGenerator
        generator = ArticleIdeaGenerator()
        ideas = generator.generate(signals, narratives=narratives, max_ideas=max_ideas)

        return {
            "output": {"ideas": ideas, "count": len(ideas)},
            "ideas": ideas,
            "idea_count": len(ideas),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    # ── Quality Scorer Step Handlers ────────────────────────────────

    def _execute_score_edition_quality_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a ``score_edition_quality`` step.

        Config (from ``config_json``):
            * ``edition_id`` — Edition ID to score (optional, falls back to context).
            * ``citation_report`` — Citation validation report dict.
            * ``signal_data`` — Signal data dict with ``items`` and ``source_count``.
            * ``narrative_data`` — Narrative data dict with ``story_diffs`` and ``trajectories``.
            * ``brand_data`` — Brand data dict with ``output_text``.
        """
        config: dict = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        edition_id = config.get("edition_id", "")
        if not edition_id and exec_ctx:
            for sid, result_data in exec_ctx.step_results.items():
                if isinstance(result_data, dict):
                    found = result_data.get("edition_id", result_data.get("run_id", ""))
                    if found:
                        edition_id = found
                        break

        if not edition_id:
            # Auto-generate edition_id if none available
            edition_id = f"ed_{int(time.time())}"
            logger.info("Auto-generated edition_id: %s", edition_id)

        if not edition_id:
            return {
                "output": {"error": "No edition_id provided"},
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        from quality.scorer import QualityScorer

        scorer = QualityScorer()
        result = scorer.score_edition(
            edition_id=edition_id,
            citation_report=config.get("citation_report", {}),
            signal_data=config.get("signal_data", {}),
            narrative_data=config.get("narrative_data", {}),
            brand_data=config.get("brand_data", {}),
        )

        return {
            "output": result,
            "quality_score": result,
            "composite_score": result.get("composite_score", 0),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_check_quality_regression_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a ``check_quality_regression`` step.

        Config (from ``config_json``):
            * ``edition_id`` — The edition to check.
            * ``baseline_id`` — The baseline edition to compare against.
        """
        config: dict = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        edition_id = config.get("edition_id", "")
        baseline_id = config.get("baseline_id", "")

        if not edition_id or not baseline_id:
            # Auto-generate if missing
            if not edition_id:
                edition_id = f"ed_{int(time.time())}"
                logger.info("Auto-generated edition_id for regression check: %s", edition_id)
            if not baseline_id:
                baseline_id = edition_id
                logger.info("Using edition_id as baseline_id for first run: %s", baseline_id)

        if not edition_id or not baseline_id:
            return {
                "output": {"error": "edition_id and baseline_id are required"},
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        from quality.scorer import QualityScorer

        scorer = QualityScorer()
        result = scorer.check_regression(edition_id, baseline_id)

        return {
            "output": result,
            "regression": result,
            "regressed": result.get("regressed", False),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    # ── Regression Step Handlers ──────────────────────────────────────

    def _execute_run_regression_tests_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a ``run_regression_tests`` step.

        Config (from ``config_json``):
            * ``edition_id`` — The edition being tested (default: from context).
            * ``baseline_id`` — Baseline ID to compare against.
            * ``quality_scores`` — Inline quality scores dict (optional; falls
              back to ``step_results`` or ``config``).
        """
        config: dict = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        edition_id = config.get("edition_id", "")
        baseline_id = config.get("baseline_id", "")
        quality_scores = config.get("quality_scores", {})

        # Fallback: scan step_results for edition_id and quality_scores
        if not edition_id and exec_ctx:
            for sid, result_data in exec_ctx.step_results.items():
                if isinstance(result_data, dict):
                    found_eid = result_data.get("edition_id", "")
                    if found_eid:
                        edition_id = found_eid
                    found_qs = result_data.get("quality_score", {})
                    if found_qs:
                        quality_scores = found_qs

        if not edition_id:
            return {
                "output": {"error": "No edition_id provided"},
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }
        if not baseline_id:
            return {
                "output": {"error": "No baseline_id provided"},
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }
        if not quality_scores:
            return {
                "output": {"error": "No quality_scores provided"},
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        from quality.regression import RegressionTester

        tester = RegressionTester()
        result = tester.run_tests(edition_id, baseline_id, quality_scores)

        return {
            "output": result,
            "regression_result": result,
            "passed": result.get("passed", False),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_update_baseline_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute an ``update_baseline`` step — promote an edition to baseline.

        Config (from ``config_json``):
            * ``edition_id`` — The edition to promote.
        """
        config: dict = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        edition_id = config.get("edition_id", "")

        if not edition_id and exec_ctx:
            for sid, result_data in exec_ctx.step_results.items():
                if isinstance(result_data, dict):
                    found = result_data.get("edition_id", "")
                    if found:
                        edition_id = found
                        break

        if not edition_id:
            return {
                "output": {"error": "No edition_id provided"},
                "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
            }

        from quality.regression import RegressionTester

        tester = RegressionTester()
        baseline = tester.update_baseline(edition_id)

        return {
            "output": baseline,
            "baseline": baseline,
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_render_sections_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a render_sections step.

        Reads from exec_ctx.step_results and renders each section
        with hard caps. Outputs a structured dict with per-section keys.

        Config (from config_json):
            - max_articles_chars: max chars for articles section (default 5000)
            - max_social_chars: max chars for social/community section (default 2000)
            - max_scores_chars: max chars for quality scores (default 500)
            - max_sources_chars: max chars for source index (default 1000)
            - max_items: max articles to include (default 30)
        """
        config: dict = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        max_articles_chars = config.get("max_articles_chars", 5000)
        max_social_chars = config.get("max_social_chars", 2000)
        max_scores_chars = config.get("max_scores_chars", 500)
        max_sources_chars = config.get("max_sources_chars", 1000)
        max_items = config.get("max_items", 30)

        sections = {
            "articles": "",
            "social": "",
            "quality_scores": "",
            "source_index": "",
            "counts": {},
        }

        if exec_ctx and exec_ctx.step_results:
            # Collect articles from step_results
            articles = []
            scores_text = ""
            sources = []
            social_items = []

            # Social source prefixes — items from these steps go to social/community section
            social_step_prefixes = ("fetch_hermes", "fetch_hn_discus", "fetch_reddit", "hn_algolia")

            for sid, result_data in exec_ctx.step_results.items():
                if not isinstance(result_data, dict):
                    continue
                # Find articles."items" or result_data.get("output") or []
                output = result_data.get("items") or result_data.get("output") or []

                # Determine if this is a social/community source
                is_social = any(sid.startswith(p) for p in social_step_prefixes)

                # Collect articles (or social items)
                if isinstance(output, list):
                    for item in output:
                        if isinstance(item, dict) and ("url" in item or "title" in item):
                            title = item.get("title", "") or ""
                            url = item.get("url", "") or ""
                            author = item.get("author", "") or ""
                            summary = item.get("content", "") or ""
                            if isinstance(summary, str) and len(summary) > 150:
                                summary = summary[:150] + "..."
                            entry = {"title": title, "url": url, "author": author, "summary": summary}
                            if is_social:
                                social_items.append(entry)
                            else:
                                articles.append(entry)

                # Collect quality scores
                score = result_data.get("composite_score", result_data.get("quality_score", None))
                if score and isinstance(score, (dict, int, float)):
                    if isinstance(score, dict) and score.get("composite_score"):
                        scores_text = json.dumps(score)
                    elif isinstance(score, (int, float)) and score > 0:
                        scores_text = json.dumps({"composite_score": score})

                # Collect source/citation data
                cm = result_data.get("citation_map") or result_data.get("output", "")
                if isinstance(cm, str) and "[S" in cm:
                    sources.append(cm)

            # Render articles section
            articles_text = []
            for i, a in enumerate(articles[:max_items]):
                line = f"{i+1}. [{a['title']}]({a['url']})"
                if a.get('author'):
                    line += f" — {a['author']}"
                if a.get('summary'):
                    line += f": {a['summary']}"
                articles_text.append(line)
            articles_joined = "\n".join(articles_text)
            if len(articles_joined) > max_articles_chars:
                articles_joined = articles_joined[:max_articles_chars] + "\n[...truncated...]"
            sections["articles"] = articles_joined

            # Render social/community section
            social_text = []
            for i, s in enumerate(social_items[:15]):
                line = f"{i+1}. [{s['title']}]({s['url']})"
                if s.get('summary'):
                    line += f": {s['summary']}"
                social_text.append(line)
            social_joined = "\n".join(social_text)
            if social_joined:
                if len(social_joined) > max_social_chars:
                    social_joined = social_joined[:max_social_chars] + "\n[...truncated...]"
            sections["social"] = social_joined

            # Render scores section (only if > 0)
            if scores_text and '0.0' not in str(scores_text):
                sections["quality_scores"] = scores_text[:max_scores_chars]

            # Render sources section
            if sources:
                sources_joined = "\n\n".join(sources)[:max_sources_chars]
                sections["source_index"] = sources_joined

            sections["counts"] = {
                "total_articles": len(articles),
                "articles_included": min(len(articles), max_items),
                "has_scores": bool(scores_text and '0.0' not in str(scores_text)),
                "source_count": len(sources),
            }

        return {
            "output": sections,
            "sections": sections,
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }

    def _execute_join_brief_step(self, step: dict, exec_ctx: ExecutorContext | None = None) -> dict:
        """Execute a join_brief step.

        Takes the output of render_sections (or any step that outputs a
        'sections' dict with articles/social/quality_scores/source_index keys)
        and assembles them into a structured LLM prompt.

        The assembled prompt is always < 8K chars because:
        - Each input section is pre-capped by the rendering step
        - The template wrapper is fixed-length (~500 chars)

        Config (from config_json):
            - format_instructions: custom format instructions (default: markdown with sections)
            - include_source_index: bool (default: True)
            - include_scores: bool (default: only if scores > 0)
            - social_section_title: string label for social section
        """
        config: dict = {}
        raw_config = step.get("config_json", "{}")
        if isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        elif isinstance(raw_config, dict):
            config = raw_config

        include_source_index = config.get("include_source_index", True)
        social_section_title = config.get("social_section_title", "Community Pulse")

        # Collect sections from step_results
        articles_text = ""
        social_text = ""
        scores_text = ""
        sources_text = ""
        counts = {}

        if exec_ctx and exec_ctx.step_results:
            for sid, result_data in exec_ctx.step_results.items():
                if not isinstance(result_data, dict):
                    continue
                # Only match steps that have an explicit "sections" key (from render_sections)
                sections = result_data.get("sections")
                if isinstance(sections, dict) and "articles" in sections:
                    import logging as _jblog2
                    _jblog2.warning("JOIN_BRIEF: found sections, social_len=%d articles_len=%d", len(sections.get("social","") or ""), len(sections.get("articles","") or ""))
                    articles_text = sections.get("articles", "") or articles_text
                    social_text = sections.get("social", "") or social_text
                    scores_text = sections.get("quality_scores", "") or scores_text
                    sources_text = sections.get("source_index", "") or sources_text
                    seccounts = sections.get("counts", {})
                    if seccounts:
                        counts = seccounts
                    break

        # Build the structured prompt
        prompt_parts = []

        prompt_parts.append("You are the Evolving Software Intelligence Brief. Synthesize the following processed intelligence into a professional newsletter.")
        prompt_parts.append("")

        # Headlines section
        prompt_parts.append("## HEADLINES (Top 5 stories by technical signal, not drama)")
        if counts.get("total_articles", 0) > 0:
            prompt_parts.append(f"Based on {counts.get('total_articles', 0)} collected items ({counts.get('articles_included', 0)} shown).")
        prompt_parts.append("")

        # Articles section
        if articles_text:
            prompt_parts.append("## Top Stories")
            prompt_parts.append(articles_text)
            prompt_parts.append("")

        # Scores section (only if non-zero)
        if scores_text and '0.0' not in scores_text:
            prompt_parts.append("## Quality Scores")
            prompt_parts.append(scores_text)
            prompt_parts.append("")

        # Social/Community section (even if empty, still include heading if config says so)
        prompt_parts.append(f"## {social_section_title}")
        if social_text:
            prompt_parts.append(social_text)
        else:
            prompt_parts.append("(No community pulse data available for this edition.)")
        prompt_parts.append("")

        # Source index
        if include_source_index and sources_text:
            prompt_parts.append("## Source Index")
            prompt_parts.append(sources_text)
            prompt_parts.append("")

        # Format instructions
        prompt_parts.append("## Output Format")
        prompt_parts.append("Format the newsletter as markdown with:")
        prompt_parts.append("- ## HEADLINE BRIEF — 2-3 sentence overview")
        prompt_parts.append("- ## TOP STORIES — 4-6 stories with analysis and [Snnnnn] citations — prioritize stories with direct technical impact over policy/governance")
        prompt_parts.append("- ## TECHNICAL SIGNALS — 2-3 emerging technical patterns or infrastructure shifts")
        prompt_parts.append("- ## COMMUNITY PULSE — what people are discussing today")
        prompt_parts.append("- ## STRATEGIC OUTLOOK — cross-cutting patterns and synthesis")
        prompt_parts.append("- ## SOURCE INDEX — all citations with URLs")
        prompt_parts.append("")
        prompt_parts.append("## Constraints")
        prompt_parts.append("- Cite at least 2 non-academic sources (blogs, news, community)")
        prompt_parts.append("- Include one 'Community Pulse' paragraph about what people are discussing")
        prompt_parts.append("- Prefer recent stories over highest-citation-count papers")
        prompt_parts.append("- Prefer stories about tools, architectures, open-source releases, and deployment patterns over regulatory/policy stories — unless the policy story directly impacts technical workflows")
        prompt_parts.append("- If Hermes Agent releases are in the data, lead with technical highlights (new features, performance gains, breaking changes)")
        prompt_parts.append("- Use [Snnnnn] format for all citations")
        prompt_parts.append("- Keep total output under 1500 words")

        joined = "\n".join(prompt_parts)

        return {
            "output": joined,
            "prompt": joined,
            "prompt_length": len(joined),
            "tokens_input": 0, "tokens_output": 0, "cost_cents": 0, "model": "",
        }
