#!/usr/bin/env python3
"""Workflow Loader — Code-first agent workflow management.

Source of truth is YAML files on disk under workflows/.
The database is a runtime cache/index.
The agent edits YAML files directly.
The UI reads/writes YAML files via the API.
Git provides versioning, diff, PRs, CI/CD.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SRC = Path(__file__).parent
WORKFLOWS_DIR = (SRC.parent / "workflows").resolve()
OUT_OF_THE_BOX_DIR = (SRC.parent / "out-of-the-box").resolve()
DEFAULT_WORKFLOW_DIRS = [OUT_OF_THE_BOX_DIR, WORKFLOWS_DIR]
VALID_STEP_TYPES = {
    # Core step types
    "llm_call", "tool_call", "condition", "loop", "input", "subworkflow",
    "human_escalation", "data_pipeline", "score",
    "memory_read", "memory_write",
    # Content collection
    "http_fetch", "parse_rss", "parse_jsonpath", "parse_xpath", "parse_html",
    "resolve_id_list", "content_source", "fetch_source", "parse_source", "fetch_and_parse",
    # Content extraction
    "extract_article", "batch_extract", "extract_metadata_only",
    # Entity extraction
    "extract_entities_batch", "extract_keywords", "score_by_entity",
    # Citation pipeline
    "assign_citations", "resolve_citations", "export_citation_map",
    "verify_claims", "grade_citations", "validate_citations", "hallucination_check", "reject_if_invalid",
    # Cross-reference
    "detect_cross_references", "boost_multi_sourced", "cluster_by_topic",
    # Story diff
    "diff_stories", "compute_trajectories", "generate_diff_narrative",
    # Narrative synthesis
    "synthesize_narrative", "detect_narrative_arcs", "generate_article_ideas",
    # Quality scoring
    "score_edition_quality", "check_quality_regression",
    # Archive & registry
    "archive_edition", "rebuild_archive_index",
    "register_edition", "compare_editions", "compute_edition_stats",
    # Patterns
    # Rendering
    "render_pattern_with_version", "sandbox_verify_pattern",
    "render_sections", "join_brief",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Database helpers ──────────────────────────────────────────────

def _get_conn():
    """Get the shared ESAM database connection."""
    from database import get_connection
    return get_connection()


def _get_wfdb():
    """Get AgentWorkflowDB instance (uses same DB as everything else)."""
    from agent_workflow import AgentWorkflowDB
    return AgentWorkflowDB()


# ── YAML writer (no PyYAML dependency) ────────────────────────────

def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        if "\n" in value:
            return "|\n" + "\n".join(f"      {line}" for line in value.split("\n"))
        if len(value) > 60 or any(c in value for c in "{}:#'\""):
            return repr(value)
        return value
    if isinstance(value, list):
        if not value:
            return "[]"
        return json.dumps(value)
    if isinstance(value, dict):
        if not value:
            return "{}"
        return json.dumps(value)
    return str(value)


def export_one_yaml(data: dict) -> str:
    """Serialize workflow data dict to YAML string."""
    lines: list[str] = []
    for key in ("name", "description", "version", "created", "updated"):
        if key in data and data[key]:
            lines.append(f"{key}: {_yaml_scalar(data[key])}")

    lines.append("")
    lines.append("steps:")
    for step in data.get("steps", []):
        lines.append(f"  - id: {step.get('id', 'step-unknown')}")
        for key in ("label", "type"):
            if key in step and step[key]:
                lines.append(f"    {key}: {_yaml_scalar(step[key])}")
        prompt = step.get("prompt_template", step.get("prompt", ""))
        if prompt:
            if "\n" in prompt:
                lines.append("    prompt: |")
                for line in prompt.split("\n"):
                    lines.append(f"      {line}")
            else:
                lines.append(f"    prompt: {_yaml_scalar(prompt)}")
        else:
            lines.append("    prompt: ''")
        tools = step.get("tools", [])
        if tools:
            lines.append(f"    tools: {json.dumps(tools)}")
        model = step.get("model_name", step.get("model", ""))
        if model:
            lines.append(f"    model: {model}")
        pos = step.get("position", [0, 0])
        lines.append(f"    position: [{pos[0]}, {pos[1]}]")
        # Export subworkflow config
        if step.get("type") == "subworkflow":
            sw = step.get("subworkflow", {})
            if sw:
                lines.append("    subworkflow:")
                target = sw.get("target", "")
                if target:
                    lines.append(f"      target: {_yaml_scalar(target)}")
                inp = sw.get("input", {})
                if inp:
                    lines.append("      input:")
                    for k, v in inp.items():
                        lines.append(f"        {k}: {_yaml_scalar(v)}")
        # Export escalation config
        if step.get("type") == "human_escalation":
            esc = step.get("escalation", {})
            if esc:
                lines.append("    escalation:")
                for k in ("notify_to", "subject", "instructions", "timeout_minutes"):
                    if k in esc and esc[k]:
                        lines.append(f"      {k}: {_yaml_scalar(esc[k])}")
        # Export authority config (if non-default keys present)
        authority = step.get("authority", {})
        if authority and isinstance(authority, dict):
            is_default = (
                authority.get("level") == "standard"
                and authority.get("cost_limit_cents") == 10
                and authority.get("hard_gate") is True
                and authority.get("model_allowlist") == []
                and authority.get("output_schema") == ""
                and authority.get("escalation_contact") == ""
            )
            if not is_default:
                lines.append("    authority:")
                for k in ("level", "cost_limit_cents", "hard_gate", "model_allowlist", "output_schema", "escalation_contact"):
                    if k in authority:
                        lines.append(f"      {k}: {_yaml_scalar(authority[k])}")

    conns = data.get("connections", [])
    if conns:
        lines.append("")
        lines.append("connections:")
        for c in conns:
            lines.append(f"  - from: {c.get('from', '?')}")
            lines.append(f"    to: {c.get('to', '?')}")
            if c.get("label"):
                lines.append(f"    label: {c['label']}")
            if c.get("condition"):
                lines.append(f"    condition: {c['condition']}")

    sources = data.get("sources", [])
    if sources:
        lines.append("")
        lines.append("sources:")
        for src in sources:
            lines.append(f"  - name: {_yaml_scalar(src.get('name', ''))}")
            if src.get("feed_url"):
                lines.append(f"    feed_url: {_yaml_scalar(src['feed_url'])}")
            if src.get("domain"):
                lines.append(f"    domain: {_yaml_scalar(src['domain'])}")
            if src.get("authority_tier"):
                lines.append(f"    authority_tier: {src['authority_tier']}")
            if src.get("fetch_interval_mins", 1440) != 1440:
                lines.append(f"    fetch_interval_mins: {src['fetch_interval_mins']}")

    content_sources = data.get("content_sources", [])
    if content_sources:
        lines.append("")
        lines.append("content_sources:")
        for cs in content_sources:
            lines.append(f"  - name: {_yaml_scalar(cs.get('name', ''))}")
            lines.append(f"    type: {cs.get('type', 'rss')}")
            if cs.get("url"):
                lines.append(f"    url: {_yaml_scalar(cs['url'])}")
            if cs.get("url_template"):
                lines.append(f"    url_template: {_yaml_scalar(cs['url_template'])}")
            if cs.get("response_type"):
                lines.append(f"    response_type: {cs['response_type']}")
            if cs.get("response_path"):
                lines.append(f"    response_path: {_yaml_scalar(cs['response_path'])}")
            if cs.get("fields"):
                lines.append(f"    fields: {json.dumps(cs['fields'])}")
            if cs.get("method"):
                lines.append(f"    method: {cs['method']}")
            lines.append(f"    authority_tier: {cs.get('authority_tier', 'B')}")
            if cs.get("interval_minutes", 60) != 60:
                lines.append(f"    interval_minutes: {cs['interval_minutes']}")
            if cs.get("rate_limit_per_minute"):
                lines.append(f"    rate_limit_per_minute: {cs['rate_limit_per_minute']}")
            if cs.get("rate_limit_burst"):
                lines.append(f"    rate_limit_burst: {cs['rate_limit_burst']}")

    return "\n".join(lines) + "\n"


# ── YAML reader (simple, no external deps) ───────────────────────

def import_one_yaml(text: str) -> dict:
    """Parse a YAML string into a workflow data dict."""
    result: dict[str, Any] = {}
    steps: list[dict] = []
    conns: list[dict] = []
    sources: list[dict] = []
    current_step: dict | None = None
    current_conn: dict | None = None
    current_source: dict | None = None
    in_section = ""
    in_block = False
    block_key = ""
    block_lines: list[str] = []
    # Nested dict tracking for subworkflow
    nested_stack: list[tuple[str, dict, int]] = []  # (parent_key, dict, indent)

    def flush_block():
        nonlocal in_block, block_key, block_lines
        if in_block and block_key:
            value = "\n".join(block_lines).strip()
            if current_step is not None:
                current_step[block_key] = value
            else:
                result[block_key] = value
        in_block = False
        block_key = ""
        block_lines = []

    def measure_indent(line: str) -> int:
        """Count leading spaces."""
        return len(line) - len(line.lstrip())

    for raw in text.split("\n"):
        stripped = raw.strip()
        indent = measure_indent(raw)

        # Continue block scalar
        if in_block:
            if raw.startswith("      "):
                block_lines.append(raw[6:])
                continue
            else:
                flush_block()

        if not stripped or stripped.startswith("#"):
            continue

        # Section headers
        if stripped == "steps:":
            flush_block()
            in_section = "steps"
            if current_step:
                steps.append(current_step)
                current_step = None
            nested_stack.clear()
            continue
        if stripped == "connections:":
            flush_block()
            in_section = "connections"
            steps.append(current_step) if current_step else None
            current_step = None
            nested_stack.clear()
            continue
        if stripped == "sources:":
            flush_block()
            in_section = "sources"
            steps.append(current_step) if current_step else None
            current_step = None
            nested_stack.clear()
            continue

        # Check if we're continuing a nested dict (subworkflow:)
        if nested_stack:
            top_key, top_dict, top_indent = nested_stack[-1]
            if indent >= top_indent + 2:
                # Still inside nested dict
                if ": " in stripped:
                    k, v = stripped.split(": ", 1)
                    key = k.strip()
                    val = _parse_yaml_val(v)
                    if val == "" or (isinstance(val, str) and val.strip() == ""):
                        # Start a new sub-dict (e.g., "input:")
                        sub = {}
                        top_dict[key] = sub
                        nested_stack.append((key, sub, indent))
                    else:
                        top_dict[key] = val
                elif stripped.endswith(":") and not stripped.startswith("-"):
                    key = stripped[:-1].strip()
                    sub = {}
                    top_dict[key] = sub
                    nested_stack.append((key, sub, indent))
                continue
            else:
                # Exited nested dict — pop all levels that no longer apply
                while nested_stack and indent < nested_stack[-1][2]:
                    parent_key, nested_dict, _ = nested_stack.pop()
                    if not nested_stack:
                        # Flush to current step
                        if current_step is not None:
                            current_step[parent_key] = nested_dict

        # List item
        if stripped.startswith("- "):
            item = stripped[2:]
            flush_block()
            if in_section == "steps":
                if current_step:
                    steps.append(current_step)
                current_step = {}
                if ": " in item:
                    k, v = item.split(": ", 1)
                    current_step[k.strip()] = _parse_yaml_val(v)
            elif in_section == "connections":
                if current_conn:
                    conns.append(current_conn)
                current_conn = {}
                if ": " in item:
                    k, v = item.split(": ", 1)
                    current_conn[k.strip()] = _parse_yaml_val(v)
            elif in_section == "sources":
                if current_source:
                    sources.append(current_source)
                current_source = {}
                if ": " in item:
                    k, v = item.split(": ", 1)
                    current_source[k.strip()] = _parse_yaml_val(v)
            continue

        # Key: value pair
        if ": " in stripped:
            k, v = stripped.split(": ", 1)
            key = k.strip()
            val = _parse_yaml_val(v)

            # Check if this starts a nested dict (subworkflow with deeper indent)
            if current_step is not None and key == "subworkflow" and val == "":
                sub = {}
                nested_stack.append((key, sub, indent))
                continue
            # Check if this starts a nested escalation dict
            if current_step is not None and key == "escalation" and val == "":
                sub = {}
                nested_stack.append((key, sub, indent))
                continue
            # Check if this starts a nested authority dict
            if current_step is not None and key == "authority" and val == "":
                sub = {}
                nested_stack.append((key, sub, indent))
                continue

            if val == "|" or (isinstance(val, str) and val.strip() == "|"):
                in_block = True
                block_key = key
                block_lines = []
                continue

            if current_step is not None:
                current_step[key] = val
            elif current_conn is not None:
                current_conn[key] = val
            elif current_source is not None:
                current_source[key] = val
            else:
                result[key] = val
        elif stripped.endswith(":") and not stripped.startswith("-"):
            # Key with colon but no value (e.g., "subworkflow:")
            key = stripped[:-1].strip()
            if current_step is not None and key == "subworkflow":
                sub = {}
                nested_stack.append((key, sub, indent))
            if current_step is not None and key == "escalation":
                sub = {}
                nested_stack.append((key, sub, indent))
            if current_step is not None and key == "authority":
                sub = {}
                nested_stack.append((key, sub, indent))

    flush_block()
    # Flush any remaining nested dict
    while nested_stack:
        parent_key, nested_dict, _ = nested_stack.pop()
        if not nested_stack and current_step is not None:
            current_step[parent_key] = nested_dict

    if current_step:
        steps.append(current_step)
    if current_conn:
        conns.append(current_conn)
    if current_source:
        sources.append(current_source)
    if steps:
        result["steps"] = steps
    if conns:
        result["connections"] = conns
    if sources:
        result["sources"] = sources
    return result


def _parse_yaml_val(v: str) -> Any:
    v = v.strip()
    if v == "null":
        return None
    if v == "true":
        return True
    if v == "false":
        return False
    if len(v) >= 2 and ((v.startswith("'") and v.endswith("'")) or (v.startswith('"') and v.endswith('"'))):
        return v[1:-1]
    if v.startswith("[") and v.endswith("]"):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v
    if v.startswith("{") and v.endswith("}"):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


# ── Validation ────────────────────────────────────────────────────

def validate_workflow(data: dict) -> dict:
    """Validate a workflow data dict."""
    errors: list[str] = []
    if not data.get("name"):
        errors.append("Missing required field: name")
    steps = data.get("steps", [])
    if not steps:
        errors.append("Workflow must have at least one step")
    else:
        ids: set[str] = set()
        for i, s in enumerate(steps):
            if not s.get("label"):
                errors.append(f"Step {i}: missing label")
            st = s.get("type", "")
            if st not in VALID_STEP_TYPES:
                errors.append(f"Step '{s.get('label', i)}': invalid type '{st}'")
            if st == "subworkflow":
                sw = s.get("subworkflow", {})
                if not sw or not sw.get("target"):
                    errors.append(f"Step '{s.get('label', i)}': subworkflow steps must have a 'subworkflow.target'")
            if st == "human_escalation":
                esc = s.get("escalation", {})
                if not esc or not isinstance(esc, dict):
                    errors.append(f"Step '{s.get('label', i)}': human_escalation steps must have an 'escalation' config block")
            sid = s.get("id", "")
            if sid in ids:
                errors.append(f"Step '{s.get('label', i)}': duplicate id '{sid}'")
            ids.add(sid)

    for i, c in enumerate(data.get("connections", [])):
        known = {s.get("id") for s in steps}
        if c.get("from") not in known:
            errors.append(f"Connection {i}: unknown source '{c.get('from')}'")
        if c.get("to") not in known:
            errors.append(f"Connection {i}: unknown target '{c.get('to')}'")

    return {"valid": len(errors) == 0, "errors": errors}


# ── Import (YAML → DB) ───────────────────────────────────────────

def sync_workflow(filepath: str | Path) -> dict:
    """Sync a single workflow YAML file to the database."""
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    text = path.read_text("utf-8")
    import yaml
    data = yaml.safe_load(text)
    v = validate_workflow(data)
    if not v["valid"]:
        raise ValueError(f"Validation failed for {path.name}: {'; '.join(v['errors'])}")

    db = _get_wfdb()
    conn = _get_conn()

    row = conn.execute(
        "SELECT id FROM wf_agents WHERE name = ?", (data["name"],)
    ).fetchone()

    if row:
        agent_id = row[0]
        # Delete old steps & connections, recreate from YAML
        old_steps = db.list_steps(agent_id)
        for s in old_steps:
            db.delete_step(s["id"])
        db.update_agent(agent_id, description=data.get("description", ""))
    else:
        agent = db.create_agent(data["name"], data.get("description", ""))
        agent_id = agent["id"]

    # Store workflow-level fields: tool_instances, credentials, input_schema, authority
    ti_json = json.dumps(data.get("tool_instances", {}))
    creds_json = json.dumps(data.get("credentials", []))
    is_json = json.dumps(data.get("input_schema", {}))
    auth_json = json.dumps(data.get("authority", {}))
    conn.execute(
        """UPDATE wf_agents SET
               tool_instances_json = ?,
               credentials_json = ?,
               input_schema_json = ?,
               authority_json = ?
           WHERE id = ?""",
        (ti_json, creds_json, is_json, auth_json, agent_id),
    )
    conn.commit()

    id_map: dict[str, str] = {}
    for step in data.get("steps", []):
        pos = step.get("position", [0, 0])
        # Build subworkflow_config_json from YAML subworkflow block
        subworkflow_config = {}
        sw = step.get("subworkflow", {})
        if isinstance(sw, dict) and sw:
            target_name = sw.get("target", "")
            if target_name:
                # Resolve target name to agent ID
                target_row = conn.execute(
                    "SELECT id FROM wf_agents WHERE name = ?", (target_name,)
                ).fetchone()
                if target_row:
                    subworkflow_config["target_agent_id"] = target_row[0]
                else:
                    subworkflow_config["target_agent_id"] = target_name
            subworkflow_config["input_mapping"] = sw.get("input", {})
        subworkflow_config_json = json.dumps(subworkflow_config)

        # Build escalation_config_json from YAML escalation block
        escalation_config = {}
        esc = step.get("escalation", {})
        if isinstance(esc, dict) and esc:
            escalation_config["notify_to"] = esc.get("notify_to", "")
            escalation_config["subject"] = esc.get("subject", "")
            escalation_config["instructions"] = esc.get("instructions", "")
            escalation_config["timeout_minutes"] = esc.get("timeout_minutes", 60)
        escalation_config_json = json.dumps(escalation_config)

        # Build authority_json from YAML authority block
        authority_config = {}
        auth = step.get("authority", {})
        if isinstance(auth, dict) and auth:
            authority_config["level"] = auth.get("level", "standard")
            authority_config["cost_limit_cents"] = auth.get("cost_limit_cents", 10)
            authority_config["hard_gate"] = auth.get("hard_gate", True)
            authority_config["model_allowlist"] = auth.get("model_allowlist", [])
            authority_config["output_schema"] = auth.get("output_schema", "")
            authority_config["escalation_contact"] = auth.get("escalation_contact", "")
        authority_json = json.dumps(authority_config)

        # Parse config_json from YAML if present
        yaml_config = step.get("config_json", "")
        if isinstance(yaml_config, str) and yaml_config.strip():
            try:
                parsed_config = json.loads(yaml_config)
            except (json.JSONDecodeError, TypeError):
                parsed_config = {}
        elif isinstance(yaml_config, dict):
            parsed_config = yaml_config
        else:
            parsed_config = {}

        s = db.create_step(
            agent_id=agent_id,
            label=step.get("label", ""),
            step_type=step.get("type", "llm_call"),
            prompt_template=step.get("prompt", step.get("prompt_template", "")),
            tools_json=json.dumps(step.get("tools", [])),
            model_name=step.get("model", ""),
            yaml_step_id=step.get("id", ""),
            loop_config_json=json.dumps(step.get("loop", {})),
            subworkflow_config_json=subworkflow_config_json,
            escalation_config_json=escalation_config_json,
            authority_json=authority_json,
            config_json=json.dumps({
                "memory_key": step.get("memory_key", ""),
                "memory_value": step.get("memory_value", ""),
                "memory_tags": step.get("memory_tags", ""),
                "pipeline_config": step.get("pipeline_config", {}),
                **parsed_config,
            }),
            position_x=float(pos[0]) if len(pos) > 0 else 0,
            position_y=float(pos[1]) if len(pos) > 1 else 0,
        )
        id_map[step.get("id", s["id"])] = s["id"]

    for c in data.get("connections", []):
        from_id = id_map.get(c.get("from", ""), "")
        to_id = id_map.get(c.get("to", ""), "")
        if from_id and to_id:
            db.create_connection(
                agent_id=agent_id, from_step_id=from_id, to_step_id=to_id,
                label=c.get("label", ""), condition_expr=c.get("condition", ""),
            )

    return db.get_agent(agent_id) or {"id": agent_id, "name": data["name"]}


def sync_all_workflows() -> list[dict]:
    """Scan all workflow directories and sync YAML files to DB."""
    synced: list[dict] = []
    for wf_dir in DEFAULT_WORKFLOW_DIRS:
        if not wf_dir.exists():
            continue
        for path in sorted(wf_dir.glob("*.yaml")):
            if path.name.startswith("_"):
                continue
            try:
                result = sync_workflow(path)
                synced.append(result)
                logger.info("Synced: %s \u2192 %s", path.name, result.get('name', '?'))
            except (ValueError, FileNotFoundError) as e:
                logger.error("Error syncing %s: %s", path.name, e)
    return synced


def sync_sources_from_yaml(filepath: str | Path) -> list[dict]:
    """Sync sources defined in a YAML file's ``sources`` section to the DB.

    Expected YAML structure::

        sources:
          - name: Ars Technica
            feed_url: https://feeds.arstechnica.com/arstechnica/index
            domain: arstechnica.com
            authority_tier: B
            fetch_interval_mins: 1440
          - name: Reuters
            feed_url: https://www.reuters.com/tools/rss
            domain: reuters.com
            authority_tier: A

    If a source with the same name already exists, its feed_url and tier
    are updated.  New sources are created.

    Also syncs ``content_sources`` section::

        content_sources:
          - name: Hacker News (Top)
            type: http_api
            url: https://hacker-news.firebaseio.com/v0/topstories.json
            response_type: json_array
            fields: {url: url, title: title, author: by}
            authority_tier: A
            interval_minutes: 30
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    text = path.read_text("utf-8")
    try:
        import yaml
        data = yaml.safe_load(text)
    except Exception as exc:
        raise ValueError(f"Failed to parse YAML: {exc}") from exc

    results: list[dict] = []

    # Sync legacy sources section
    sources = data.get("sources", [])
    if sources:
        from source_registry import SourceRegistry
        registry = SourceRegistry()

        for src in sources:
            name = src.get("name", "").strip()
            if not name:
                logger.warning("Skip source entry with no name in %s", path.name)
                continue

            feed_url = src.get("feed_url", "")
            domain = src.get("domain", "")
            authority_tier = src.get("authority_tier")
            fetch_interval_mins = src.get("fetch_interval_mins", 1440)

            # Check if source already exists by name
            existing = None
            for s in registry.list():
                if s.get("name") == name:
                    existing = s
                    break

            if existing:
                registry.update(
                    existing["id"],
                    feed_url=feed_url,
                    domain=domain,
                    authority_tier=authority_tier,
                    fetch_interval_mins=fetch_interval_mins,
                )
                results.append({"action": "updated", "name": name, "type": "rss_source"})
                logger.info("Updated RSS source: %s", name)
            else:
                registry.create(
                    name=name,
                    feed_url=feed_url,
                    domain=domain,
                    authority_tier=authority_tier,
                    fetch_interval_mins=fetch_interval_mins,
                )
                results.append({"action": "created", "name": name, "type": "rss_source"})
                logger.info("Created RSS source: %s", name)

    # Sync content_sources section
    content_sources = data.get("content_sources", [])
    if content_sources:
        from source_registry import ContentSourceManager
        csm = ContentSourceManager()
        cs_results = csm.bulk_import(content_sources)
        for r in cs_results:
            r["type"] = "content_source"
        results.extend(cs_results)

    if not sources and not content_sources:
        logger.info("No sources or content_sources section in %s", path.name)

    return results


# ── Export (DB → YAML) ───────────────────────────────────────────

def export_agent_to_yaml(agent_id_or_name: str, output_path: str | None = None) -> str:
    """Export an agent from DB to YAML. Returns YAML string or file path."""
    db = _get_wfdb()
    conn = _get_conn()

    agent = db.get_agent(agent_id_or_name)
    if not agent:
        row = conn.execute(
            "SELECT * FROM wf_agents WHERE name = ?", (agent_id_or_name,)
        ).fetchone()
        if row:
            agent = dict(row)
    if not agent:
        raise ValueError(f"Agent not found: {agent_id_or_name}")

    graph = db.get_workflow_graph(agent["id"])
    data: dict[str, Any] = {
        "name": agent["name"],
        "description": agent.get("description", ""),
        "version": max(1, agent.get("total_runs", 0)),
        "created": agent.get("created_at", _now()),
        "updated": _now(),
        "steps": [],
        "connections": [],
    }

    for step in graph.get("steps", []):
        sid = step["id"][:8]
        # Handle tools_json that may be double-serialized
        raw_tools = step.get("tools_json", "[]") or "[]"
        try:
            tools = json.loads(raw_tools)
            if isinstance(tools, str):
                tools = json.loads(tools)
        except (json.JSONDecodeError, TypeError):
            tools = []
        loop = json.loads(step.get("loop_config_json", "{}") or "{}")
        # Parse subworkflow config
        sw_config = json.loads(step.get("subworkflow_config_json", "{}") or "{}")
        sw_export = {}
        if step.get("step_type") == "subworkflow" and sw_config:
            # Resolve target_agent_id back to name if possible
            target_id = sw_config.get("target_agent_id", "")
            if target_id:
                target_agent = conn.execute(
                    "SELECT name FROM wf_agents WHERE id = ?", (target_id,)
                ).fetchone()
                sw_export["target"] = target_agent["name"] if target_agent else target_id
            sw_export["input"] = sw_config.get("input_mapping", {})
        # Parse escalation config
        esc_config = json.loads(step.get("escalation_config_json", "{}") or "{}")
        esc_export = {}
        if step.get("step_type") == "human_escalation" and esc_config:
            esc_export["notify_to"] = esc_config.get("notify_to", "")
            esc_export["subject"] = esc_config.get("subject", "")
            esc_export["instructions"] = esc_config.get("instructions", "")
            esc_export["timeout_minutes"] = esc_config.get("timeout_minutes", 60)
        # Parse authority config
        auth_config = json.loads(step.get("authority_json", "{}") or "{}")
        auth_export = {}
        if auth_config:
            auth_export["level"] = auth_config.get("level", "standard")
            auth_export["cost_limit_cents"] = auth_config.get("cost_limit_cents", 10)
            auth_export["hard_gate"] = auth_config.get("hard_gate", True)
            auth_export["model_allowlist"] = auth_config.get("model_allowlist", [])
            auth_export["output_schema"] = auth_config.get("output_schema", "")
            auth_export["escalation_contact"] = auth_config.get("escalation_contact", "")

        step_entry = {
            "id": f"step-{sid}",
            "label": step.get("label", ""),
            "type": step.get("step_type", "llm_call"),
            "prompt": step.get("prompt_template", ""),
            "tools": tools,
            "model": step.get("model_name", ""),
            "loop": loop,
            "position": [step.get("position_x", 0), step.get("position_y", 0)],
        }
        if sw_export:
            step_entry["subworkflow"] = sw_export
        if esc_export:
            step_entry["escalation"] = esc_export
        if auth_export and any(
            auth_export[k] != default for k, default in [
                ("level", "standard"), ("cost_limit_cents", 10),
                ("hard_gate", True), ("model_allowlist", []),
                ("output_schema", ""), ("escalation_contact", ""),
            ]
        ):
            step_entry["authority"] = auth_export
        data["steps"].append(step_entry)

    for c in graph.get("connections", []):
        data["connections"].append({
            "from": f"step-{c['from_step_id'][:8]}",
            "to": f"step-{c['to_step_id'][:8]}",
            "label": c.get("label", ""),
            "condition": c.get("condition_expr", ""),
        })

    yaml_str = export_one_yaml(data)
    if output_path:
        op = Path(output_path)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(yaml_str)
        return str(op)
    return yaml_str


def export_all_agents() -> list[str]:
    """Export all agents from DB to YAML files in workflows/ (user workflows dir)."""
    db = _get_wfdb()
    agents = db.list_agents()
    paths: list[str] = []
    WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
    for a in agents:
        safe = a["name"].lower().replace(" ", "-").replace("/", "-")
        out = str(WORKFLOWS_DIR / f"{safe}.yaml")
        export_agent_to_yaml(a["id"], out)
        paths.append(out)
        logger.info("Exported: %s", out)
    return paths


# ── CLI ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="ES Agent Management — Workflow Loader CLI")
    sub = parser.add_subparsers(dest="command")

    p_sync = sub.add_parser("sync", help="Sync all YAML workflow files to DB")
    p_val = sub.add_parser("validate", help="Validate a workflow YAML file")
    p_val.add_argument("path", help="Path to workflow YAML file")
    p_exp = sub.add_parser("export", help="Export agent to YAML")
    p_exp.add_argument("--agent", "-a", required=True, help="Agent ID or name")
    p_exp.add_argument("--output", "-o", help="Output file path")
    sub.add_parser("export-all", help="Export all agents to YAML")
    p_run = sub.add_parser("run", help="Run a workflow from YAML")
    p_run.add_argument("path", help="Path to workflow YAML file")
    p_run.add_argument("--input", "-i", default="{}", help="Input context as JSON")

    args = parser.parse_args()

    if args.command == "sync":
        result = sync_all_workflows()
        print(f"\nSynced {len(result)} workflows")

    elif args.command == "validate":
        path = Path(args.path)
        if not path.exists():
            print(f"File not found: {path}", file=sys.stderr)
            sys.exit(1)
        text = path.read_text("utf-8")
        import yaml
        data = yaml.safe_load(text)
        v = validate_workflow(data)
        if v["valid"]:
            print(f"Valid: {path.name} ({len(data.get('steps', []))} steps, {len(data.get('connections', []))} connections)")
        else:
            print(f"Invalid: {path.name}")
            for e in v["errors"]:
                print(f"  - {e}")
            sys.exit(1)

    elif args.command == "export":
        result = export_agent_to_yaml(args.agent, args.output)
        if args.output:
            print(f"Exported to: {result}")
        else:
            print(result)

    elif args.command == "export-all":
        export_all_agents()

    elif args.command == "run":
        from workflow_executor import WorkflowExecutor
        agent = sync_workflow(args.path)
        executor = WorkflowExecutor()
        input_ctx = json.loads(args.input)
        run_result = executor.execute(agent["id"], input_ctx)
        print(json.dumps(run_result, indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
