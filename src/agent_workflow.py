"""Agent Workflow — Visual Agent Builder data model.

Manages the visual agent builder's data model: agents, workflow steps,
connections, credentials, runs, and step logs.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from database import get_connection

__all__ = ["AgentWorkflowDB"]

# Model pricing per 1K tokens for cost estimation
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


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row) if row else {}


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS wf_agents (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    status          TEXT DEFAULT 'draft',
    total_cost_cents INTEGER DEFAULT 0,
    total_runs      INTEGER DEFAULT 0,
    tool_instances_json TEXT NOT NULL DEFAULT '{}',
    credentials_json    TEXT NOT NULL DEFAULT '[]',
    input_schema_json   TEXT NOT NULL DEFAULT '{}',
    authority_json      TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wf_steps (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL REFERENCES wf_agents(id) ON DELETE CASCADE,
    label           TEXT DEFAULT '',
    step_type       TEXT DEFAULT 'llm_call',
    prompt_template TEXT DEFAULT '',
    tools_json      TEXT DEFAULT '[]',
    model_name      TEXT DEFAULT '',
    next_step_id    TEXT REFERENCES wf_steps(id) ON DELETE SET NULL,
    loop_config_json TEXT DEFAULT '{}',
    subworkflow_config_json TEXT DEFAULT '{}',
    escalation_config_json TEXT DEFAULT '{}',
    authority_json  TEXT DEFAULT '{}',
    yaml_step_id    TEXT DEFAULT '',
    position_x      REAL DEFAULT 0,
    position_y      REAL DEFAULT 0,
    config_json     TEXT DEFAULT '{}',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wf_steps_agent ON wf_steps(agent_id);

CREATE TABLE IF NOT EXISTS wf_step_connections (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL REFERENCES wf_agents(id) ON DELETE CASCADE,
    from_step_id    TEXT NOT NULL REFERENCES wf_steps(id) ON DELETE CASCADE,
    to_step_id      TEXT NOT NULL REFERENCES wf_steps(id) ON DELETE CASCADE,
    label           TEXT DEFAULT '',
    condition_expr  TEXT DEFAULT '',
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wf_conns_agent ON wf_step_connections(agent_id);
CREATE INDEX IF NOT EXISTS idx_wf_conns_from ON wf_step_connections(from_step_id);
CREATE INDEX IF NOT EXISTS idx_wf_conns_to ON wf_step_connections(to_step_id);

CREATE TABLE IF NOT EXISTS wf_agent_credentials (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL REFERENCES wf_agents(id) ON DELETE CASCADE,
    credential_key  TEXT NOT NULL,
    encrypted_value TEXT NOT NULL,
    scope_step_id   TEXT REFERENCES wf_steps(id) ON DELETE SET NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wf_creds_agent ON wf_agent_credentials(agent_id);

CREATE TABLE IF NOT EXISTS wf_agent_runs (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL REFERENCES wf_agents(id) ON DELETE CASCADE,
    status          TEXT DEFAULT 'pending',
    started_at      TEXT,
    completed_at    TEXT,
    total_cost_cents INTEGER DEFAULT 0,
    total_tokens    INTEGER DEFAULT 0,
    total_steps     INTEGER DEFAULT 0,
    trigger         TEXT DEFAULT 'manual',
    input_context   TEXT DEFAULT '{}',
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wf_runs_agent ON wf_agent_runs(agent_id);
CREATE INDEX IF NOT EXISTS idx_wf_runs_status ON wf_agent_runs(status);

CREATE TABLE IF NOT EXISTS wf_run_step_logs (
    id                  TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES wf_agent_runs(id) ON DELETE CASCADE,
    step_id             TEXT NOT NULL REFERENCES wf_steps(id) ON DELETE CASCADE,
    sequence            INTEGER DEFAULT 0,
    input_data          TEXT DEFAULT '{}',
    prompt_sent         TEXT DEFAULT '',
    output_data         TEXT DEFAULT '{}',
    tokens_input        INTEGER DEFAULT 0,
    tokens_output       INTEGER DEFAULT 0,
    cost_cents          INTEGER DEFAULT 0,
    model_used          TEXT DEFAULT '',
    started_at          TEXT,
    completed_at        TEXT,
    status              TEXT DEFAULT 'pending',
    error_message       TEXT DEFAULT '',
    reasoning_trace     TEXT DEFAULT '{}',
    credential_ids_used TEXT DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_wf_logs_run ON wf_run_step_logs(run_id);
CREATE INDEX IF NOT EXISTS idx_wf_logs_step ON wf_run_step_logs(step_id);

CREATE TABLE IF NOT EXISTS wf_run_state (
    run_id  TEXT PRIMARY KEY REFERENCES wf_agent_runs(id),
    state   TEXT NOT NULL DEFAULT '{}',
    version INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS wf_escalations (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES wf_agent_runs(id) ON DELETE CASCADE,
    step_id         TEXT REFERENCES wf_steps(id) ON DELETE SET NULL,
    step_log_id     TEXT REFERENCES wf_run_step_logs(id) ON DELETE SET NULL,
    status          TEXT DEFAULT 'pending',
    escalation_config_json TEXT DEFAULT '{}',
    context_json    TEXT DEFAULT '{}',
    responded_at    TEXT,
    response_action TEXT DEFAULT '',
    response_text   TEXT DEFAULT '',
    responded_by    TEXT DEFAULT '',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wf_esc_run ON wf_escalations(run_id);
CREATE INDEX IF NOT EXISTS idx_wf_esc_status ON wf_escalations(status);

CREATE TABLE IF NOT EXISTS wf_memory (
    id          TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    run_id      TEXT,
    key         TEXT NOT NULL,
    value_json  TEXT NOT NULL,
    tags        TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(workflow_id, key)
);

CREATE INDEX IF NOT EXISTS idx_wf_memory_workflow ON wf_memory(workflow_id);
CREATE INDEX IF NOT EXISTS idx_wf_memory_run ON wf_memory(workflow_id, run_id);
CREATE INDEX IF NOT EXISTS idx_wf_memory_tags ON wf_memory(workflow_id, tags);

CREATE TABLE IF NOT EXISTS wf_seen_store (
    id              TEXT PRIMARY KEY,
    workflow_id     TEXT NOT NULL,
    url_hash        TEXT NOT NULL,
    title_hash      TEXT NOT NULL,
    url             TEXT NOT NULL,
    title           TEXT,
    first_seen_run_id TEXT NOT NULL,
    last_seen_run_id TEXT NOT NULL,
    hit_count       INTEGER DEFAULT 1,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    UNIQUE(workflow_id, url_hash)
);

CREATE INDEX IF NOT EXISTS idx_wf_seen_store_workflow ON wf_seen_store(workflow_id);
CREATE INDEX IF NOT EXISTS idx_wf_seen_store_url_hash ON wf_seen_store(url_hash);

CREATE TABLE IF NOT EXISTS wf_scoring_rules (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL REFERENCES wf_agents(id) ON DELETE CASCADE,
    step_id         TEXT REFERENCES wf_steps(id) ON DELETE SET NULL,
    rule_key        TEXT NOT NULL,
    weight          REAL NOT NULL,
    source_field    TEXT NOT NULL,
    transform       TEXT DEFAULT 'identity',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wf_scoring_rules_agent ON wf_scoring_rules(agent_id);

CREATE TABLE IF NOT EXISTS wf_stories (
    id                  TEXT PRIMARY KEY,
    workflow_id         TEXT NOT NULL,
    title               TEXT NOT NULL,
    title_hash          TEXT NOT NULL,
    first_seen_run_id   TEXT NOT NULL,
    last_seen_run_id    TEXT NOT NULL,
    edition_count       INTEGER DEFAULT 1,
    signal_strength     REAL DEFAULT 0.5,
    change_log_json     TEXT DEFAULT '[]',
    last_headline       TEXT,
    last_body_snippet   TEXT,
    sources_json        TEXT DEFAULT '[]',
    tags                TEXT DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE(workflow_id, title_hash)
);

CREATE INDEX IF NOT EXISTS idx_wf_stories_workflow ON wf_stories(workflow_id);
CREATE INDEX IF NOT EXISTS idx_wf_stories_signal ON wf_stories(workflow_id, signal_strength DESC);
CREATE INDEX IF NOT EXISTS idx_wf_stories_title_hash ON wf_stories(workflow_id, title_hash);

CREATE TABLE IF NOT EXISTS wf_prompt_patterns (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    version INTEGER DEFAULT 1,
    pattern_config_json TEXT NOT NULL DEFAULT '{}',
    category TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wf_step_pattern_refs (
    id TEXT PRIMARY KEY,
    step_id TEXT NOT NULL REFERENCES wf_steps(id) ON DELETE CASCADE,
    pattern_id TEXT NOT NULL REFERENCES wf_prompt_patterns(id) ON DELETE CASCADE,
    override_config_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wf_pattern_refs_step ON wf_step_pattern_refs(step_id);
CREATE INDEX IF NOT EXISTS idx_wf_pattern_refs_pattern ON wf_step_pattern_refs(pattern_id);

CREATE TABLE IF NOT EXISTS wf_email_templates (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    template_html   TEXT DEFAULT '',
    template_css    TEXT DEFAULT '',
    dark_mode_css   TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wf_delivery_log (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    to_addr TEXT NOT NULL,
    subject TEXT,
    provider TEXT,
    status TEXT,
    message_id TEXT,
    error TEXT,
    sent_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wf_delivery_log_run ON wf_delivery_log(run_id);
CREATE INDEX IF NOT EXISTS idx_wf_delivery_log_message ON wf_delivery_log(message_id);
CREATE INDEX IF NOT EXISTS idx_wf_delivery_log_status ON wf_delivery_log(status);

CREATE TABLE IF NOT EXISTS wf_content_sources (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    type                TEXT NOT NULL CHECK(type IN ('rss','http_api','http_html','http_xml')),
    source_config_json  TEXT NOT NULL DEFAULT '{}',
    authority_tier      TEXT NOT NULL DEFAULT 'B' CHECK(authority_tier IN ('A','B','C')),
    interval_minutes    INTEGER NOT NULL DEFAULT 60,
    last_fetched        TEXT,
    enabled             INTEGER NOT NULL DEFAULT 1,
    rate_limit_per_minute INTEGER DEFAULT 0,
    rate_limit_burst      INTEGER DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wf_content_sources_enabled ON wf_content_sources(enabled);
CREATE INDEX IF NOT EXISTS idx_wf_content_sources_authority ON wf_content_sources(authority_tier);
CREATE INDEX IF NOT EXISTS idx_wf_content_sources_type ON wf_content_sources(type);

CREATE TABLE IF NOT EXISTS wf_content_source_items (
    id                TEXT PRIMARY KEY,
    source_id         TEXT NOT NULL REFERENCES wf_content_sources(id) ON DELETE CASCADE,
    fetch_run_id      TEXT NOT NULL DEFAULT '',
    url               TEXT NOT NULL UNIQUE,
    title             TEXT NOT NULL DEFAULT '',
    author            TEXT DEFAULT '',
    body_raw          TEXT DEFAULT '',
    body_extracted    TEXT DEFAULT '',
    entities_json     TEXT DEFAULT '[]',
    keywords_json     TEXT DEFAULT '[]',
    published_date    TEXT,
    fetched_at        TEXT NOT NULL,
    source_hash       TEXT DEFAULT '',
    citation_id       TEXT DEFAULT '',
    score             REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_wf_content_items_source ON wf_content_source_items(source_id);
CREATE INDEX IF NOT EXISTS idx_wf_content_items_url ON wf_content_source_items(url);
CREATE INDEX IF NOT EXISTS idx_wf_content_items_score ON wf_content_source_items(score);
CREATE INDEX IF NOT EXISTS idx_wf_content_items_fetch_run ON wf_content_source_items(fetch_run_id);

CREATE TABLE IF NOT EXISTS wf_raw_items (
    id TEXT PRIMARY KEY,
    source_id TEXT,
    item_url TEXT UNIQUE,
    title TEXT,
    author TEXT,
    published_date TEXT,
    body_raw TEXT,
    body_extracted TEXT,
    excerpt TEXT,
    word_count INTEGER,
    reading_time_seconds REAL,
    metadata_json TEXT,
    extraction_errors TEXT,
    fetch_run_id TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_wf_raw_items_url ON wf_raw_items(item_url);
CREATE INDEX IF NOT EXISTS idx_wf_raw_items_source ON wf_raw_items(source_id);
CREATE INDEX IF NOT EXISTS idx_wf_raw_items_fetch_run ON wf_raw_items(fetch_run_id);

CREATE TABLE IF NOT EXISTS wf_archived_editions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    edition_number INTEGER,
    date TEXT,
    subject TEXT,
    body_html TEXT,
    body_markdown TEXT,
    archive_path TEXT,
    permalink TEXT,
    citation_count INTEGER,
    source_count INTEGER,
    item_count INTEGER,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_wf_archived_editions_run ON wf_archived_editions(run_id);
CREATE INDEX IF NOT EXISTS idx_wf_archived_editions_created ON wf_archived_editions(created_at);

CREATE TABLE IF NOT EXISTS wf_entity_dictionary (
    id              TEXT PRIMARY KEY,
    entity          TEXT NOT NULL,
    type            TEXT NOT NULL CHECK(type IN ('company','product','person','concept','org')),
    aliases         TEXT DEFAULT '',
    category        TEXT DEFAULT '',
    authority_tier  TEXT DEFAULT 'B',
    source          TEXT DEFAULT '',
    created_at      TEXT,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS wf_citation_map (
    id TEXT PRIMARY KEY,
    source_id TEXT, item_id TEXT,
    citation_id TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL, title TEXT DEFAULT '',
    content_hash TEXT DEFAULT '', fetch_run_id TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_wf_citation_map_ids ON wf_citation_map(citation_id);
CREATE INDEX IF NOT EXISTS idx_wf_citation_map_run ON wf_citation_map(fetch_run_id);

CREATE TABLE IF NOT EXISTS wf_editions (
    id TEXT PRIMARY KEY,
    workflow_id TEXT, run_id TEXT, edition_number INTEGER UNIQUE,
    date TEXT, subject TEXT,
    signal_ids TEXT DEFAULT '[]',
    citation_ids TEXT DEFAULT '[]',
    narrative_json TEXT DEFAULT '{}',
    quality_score REAL,
    source_count INTEGER DEFAULT 0,
    item_count INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    duration_seconds REAL DEFAULT 0.0,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_wf_editions_number ON wf_editions(edition_number);
CREATE INDEX IF NOT EXISTS idx_wf_editions_run ON wf_editions(run_id);
"""

class AgentWorkflowDB:

    def __init__(self) -> None:
        self._ensure_schema()
        self._ensure_source_registry_schema()

    def _ensure_schema(self) -> None:
        conn = get_connection()
        conn.executescript(SCHEMA_SQL)
        # Add subworkflow_config_json column if not present
        try:
            conn.execute("ALTER TABLE wf_steps ADD COLUMN subworkflow_config_json TEXT DEFAULT '{}'")
        except Exception:
            pass  # Column already exists
        # Add escalation_config_json column if not present
        try:
            conn.execute("ALTER TABLE wf_steps ADD COLUMN escalation_config_json TEXT DEFAULT '{}'")
        except Exception:
            pass  # Column already exists
        # Add authority_json column if not present
        try:
            conn.execute("ALTER TABLE wf_steps ADD COLUMN authority_json TEXT DEFAULT '{}'")
        except Exception:
            pass  # Column already exists
        # Add yaml_step_id column if not present
        try:
            conn.execute("ALTER TABLE wf_steps ADD COLUMN yaml_step_id TEXT DEFAULT ''")
        except Exception:
            pass  # Column already exists
        # Add tool_instances_json column if not present
        try:
            conn.execute("ALTER TABLE wf_agents ADD COLUMN tool_instances_json TEXT NOT NULL DEFAULT '{}'")
        except Exception:
            pass  # Column already exists
        # Add credentials_json column if not present
        try:
            conn.execute("ALTER TABLE wf_agents ADD COLUMN credentials_json TEXT NOT NULL DEFAULT '[]'")
        except Exception:
            pass  # Column already exists
        # Add input_schema_json column if not present
        try:
            conn.execute("ALTER TABLE wf_agents ADD COLUMN input_schema_json TEXT NOT NULL DEFAULT '{}'")
        except Exception:
            pass  # Column already exists
        # Add authority_json column if not present (agent-level)
        try:
            conn.execute("ALTER TABLE wf_agents ADD COLUMN authority_json TEXT NOT NULL DEFAULT '{}'")
        except Exception:
            pass  # Column already exists
        # Add narrative_summary, signal_trajectory, related_story_ids columns to wf_stories
        for col_spec in [
            ("narrative_summary", "TEXT DEFAULT ''"),
            ("signal_trajectory", "TEXT DEFAULT 'new'"),
            ("related_story_ids", "TEXT DEFAULT '[]'"),
        ]:
            try:
                conn.execute(f"ALTER TABLE wf_stories ADD COLUMN {col_spec[0]} {col_spec[1]}")
            except Exception:
                pass  # Column already exists
        conn.commit()

    def _ensure_source_registry_schema(self) -> None:
        """Ensure source registry tables exist (wf_sources, wf_source_articles)."""
        from source_registry import SOURCE_REGISTRY_SCHEMA
        conn = get_connection()
        conn.executescript(SOURCE_REGISTRY_SCHEMA)
        conn.commit()

    # ------------------------------------------------------------------
    # Agent CRUD
    # ------------------------------------------------------------------

    def create_agent(
        self,
        name: str,
        description: str = "",
        tool_instances_json: str = "{}",
        credentials_json: str = "[]",
        input_schema_json: str = "{}",
        authority_json: str = "{}",
    ) -> dict:
        conn = get_connection()
        now = _now()
        aid = _new_id()
        conn.execute(
            "INSERT INTO wf_agents (id, name, description, tool_instances_json, credentials_json, input_schema_json, authority_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (aid, name, description, tool_instances_json, credentials_json, input_schema_json, authority_json, now, now),
        )
        conn.commit()
        return self.get_agent(aid)  # type: ignore

    def get_agent(self, agent_id: str) -> Optional[dict]:
        conn = get_connection()
        row = conn.execute("SELECT * FROM wf_agents WHERE id = ?", (agent_id,)).fetchone()
        return _row_to_dict(row)

    def list_agents(self, status: Optional[str] = None) -> list[dict]:
        conn = get_connection()
        if status:
            rows = conn.execute(
                "SELECT * FROM wf_agents WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM wf_agents ORDER BY created_at DESC").fetchall()
        return _rows_to_dicts(rows)

    def update_agent(self, agent_id: str, **kwargs: Any) -> dict:
        conn = get_connection()
        allowed = {"name", "description", "status", "total_cost_cents", "total_runs", "tool_instances_json", "credentials_json", "input_schema_json", "authority_json"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return self.get_agent(agent_id)  # type: ignore
        updates["updated_at"] = _now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [agent_id]
        conn.execute(f"UPDATE wf_agents SET {set_clause} WHERE id = ?", vals)
        conn.commit()
        return self.get_agent(agent_id)  # type: ignore

    def delete_agent(self, agent_id: str) -> bool:
        conn = get_connection()
        cur = conn.execute("DELETE FROM wf_agents WHERE id = ?", (agent_id,))
        conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Step CRUD
    # ------------------------------------------------------------------

    def create_step(
        self,
        agent_id: str,
        step_type: str = "llm_call",
        label: str = "",
        prompt_template: str = "",
        tools_json: str = "[]",
        model_name: str = "",
        yaml_step_id: str = "",
        loop_config_json: str = "{}",
        subworkflow_config_json: str = "{}",
        escalation_config_json: str = "{}",
        authority_json: str = "",
        config_json: str = "{}",
        position_x: float = 0,
        position_y: float = 0,
    ) -> dict:
        if not authority_json:
            authority_json = '{"level": "standard", "cost_limit_cents": 10, "hard_gate": true, "model_allowlist": [], "output_schema": "", "escalation_contact": ""}'
        conn = get_connection()
        now = _now()
        sid = _new_id()
        conn.execute(
            """INSERT INTO wf_steps
               (id, agent_id, label, step_type, prompt_template, tools_json,
                model_name, yaml_step_id, loop_config_json, subworkflow_config_json,
                escalation_config_json, authority_json, config_json,
                position_x, position_y, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid, agent_id, label, step_type, prompt_template, tools_json,
             model_name, yaml_step_id, loop_config_json, subworkflow_config_json,
             escalation_config_json, authority_json, config_json,
             position_x, position_y, now, now),
        )
        conn.commit()
        return self.get_step(sid)  # type: ignore

    def get_step(self, step_id: str) -> Optional[dict]:
        conn = get_connection()
        row = conn.execute("SELECT * FROM wf_steps WHERE id = ?", (step_id,)).fetchone()
        return _row_to_dict(row)

    def list_steps(self, agent_id: str) -> list[dict]:
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM wf_steps WHERE agent_id = ? ORDER BY position_y ASC, position_x ASC",
            (agent_id,),
        ).fetchall()
        return _rows_to_dicts(rows)

    def update_step(self, step_id: str, **kwargs: Any) -> dict:
        conn = get_connection()
        allowed = {
            "label", "step_type", "prompt_template", "tools_json",
            "model_name", "next_step_id", "loop_config_json",
            "subworkflow_config_json", "escalation_config_json",
            "authority_json",
            "position_x", "position_y",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return self.get_step(step_id)  # type: ignore
        updates["updated_at"] = _now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [step_id]
        conn.execute(f"UPDATE wf_steps SET {set_clause} WHERE id = ?", vals)
        conn.commit()
        return self.get_step(step_id)  # type: ignore

    def delete_step(self, step_id: str) -> bool:
        conn = get_connection()
        conn.execute("DELETE FROM wf_step_connections WHERE from_step_id = ? OR to_step_id = ?", (step_id, step_id))
        cur = conn.execute("DELETE FROM wf_steps WHERE id = ?", (step_id,))
        conn.commit()
        return cur.rowcount > 0

    def reorder_steps(self, agent_id: str, step_ids: list[str]) -> bool:
        conn = get_connection()
        for idx, sid in enumerate(step_ids):
            conn.execute(
                "UPDATE wf_steps SET position_x = ?, updated_at = ? WHERE id = ? AND agent_id = ?",
                (float(idx * 200), _now(), sid, agent_id),
            )
        conn.commit()
        return True

    # ------------------------------------------------------------------
    # Connection CRUD
    # ------------------------------------------------------------------

    def create_connection(
        self,
        agent_id: str,
        from_step_id: str,
        to_step_id: str,
        label: str = "",
        condition_expr: str = "",
    ) -> dict:
        conn = get_connection()
        cid = _new_id()
        now = _now()
        conn.execute(
            """INSERT INTO wf_step_connections
               (id, agent_id, from_step_id, to_step_id, label, condition_expr, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (cid, agent_id, from_step_id, to_step_id, label, condition_expr, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM wf_step_connections WHERE id = ?", (cid,)).fetchone()
        return _row_to_dict(row)

    def list_connections(self, agent_id: str) -> list[dict]:
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM wf_step_connections WHERE agent_id = ? ORDER BY created_at",
            (agent_id,),
        ).fetchall()
        return _rows_to_dicts(rows)

    def delete_connection(self, conn_id: str) -> bool:
        conn = get_connection()
        cur = conn.execute("DELETE FROM wf_step_connections WHERE id = ?", (conn_id,))
        conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Credential CRUD
    # ------------------------------------------------------------------

    def create_credential(
        self,
        agent_id: str,
        credential_key: str,
        encrypted_value: str,
        scope_step_id: Optional[str] = None,
    ) -> dict:
        conn = get_connection()
        cid = _new_id()
        now = _now()
        conn.execute(
            """INSERT INTO wf_agent_credentials
               (id, agent_id, credential_key, encrypted_value, scope_step_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (cid, agent_id, credential_key, encrypted_value, scope_step_id, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM wf_agent_credentials WHERE id = ?", (cid,)).fetchone()
        return _row_to_dict(row)

    def list_credentials(self, agent_id: str) -> list[dict]:
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM wf_agent_credentials WHERE agent_id = ? ORDER BY created_at",
            (agent_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["encrypted_value"] = "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022"
            result.append(d)
        return result

    def get_credential(self, cred_id: str) -> Optional[dict]:
        conn = get_connection()
        row = conn.execute("SELECT * FROM wf_agent_credentials WHERE id = ?", (cred_id,)).fetchone()
        return _row_to_dict(row)

    def delete_credential(self, cred_id: str) -> bool:
        conn = get_connection()
        cur = conn.execute("DELETE FROM wf_agent_credentials WHERE id = ?", (cred_id,))
        conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Run CRUD
    # ------------------------------------------------------------------

    def create_run(self, agent_id: str, trigger: str = "manual", input_context: str = "{}") -> dict:
        conn = get_connection()
        rid = _new_id()
        now = _now()
        conn.execute(
            """INSERT INTO wf_agent_runs
               (id, agent_id, status, started_at, trigger, input_context, created_at)
               VALUES (?, ?, 'pending', ?, ?, ?, ?)""",
            (rid, agent_id, now, trigger, input_context, now),
        )
        conn.execute(
            "UPDATE wf_agents SET total_runs = total_runs + 1, updated_at = ? WHERE id = ?",
            (now, agent_id),
        )
        conn.commit()
        return self.get_run(rid)  # type: ignore

    def get_run(self, run_id: str) -> Optional[dict]:
        conn = get_connection()
        row = conn.execute("SELECT * FROM wf_agent_runs WHERE id = ?", (run_id,)).fetchone()
        return _row_to_dict(row)

    def list_runs(self, agent_id: str, limit: int = 50) -> list[dict]:
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM wf_agent_runs WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?",
            (agent_id, limit),
        ).fetchall()
        return _rows_to_dicts(rows)

    def update_run_status(self, run_id: str, status: str, **kwargs: Any) -> dict:
        conn = get_connection()
        allowed = {
            "started_at", "completed_at", "total_cost_cents",
            "total_tokens", "total_steps", "status",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        updates["status"] = status
        if status in ("completed", "failed", "cancelled"):
            updates.setdefault("completed_at", _now())
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [run_id]
        conn.execute(f"UPDATE wf_agent_runs SET {set_clause} WHERE id = ?", vals)
        conn.commit()
        return self.get_run(run_id)  # type: ignore

    # ------------------------------------------------------------------
    # Step Log CRUD
    # ------------------------------------------------------------------

    def create_step_log(self, run_id: str, step_id: str, sequence: int = 0) -> dict:
        conn = get_connection()
        lid = _new_id()
        now = _now()
        conn.execute(
            """INSERT INTO wf_run_step_logs
               (id, run_id, step_id, sequence, started_at, status)
               VALUES (?, ?, ?, ?, ?, 'pending')""",
            (lid, run_id, step_id, sequence, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM wf_run_step_logs WHERE id = ?", (lid,)).fetchone()
        return _row_to_dict(row)

    def update_step_log(self, log_id: str, **kwargs: Any) -> dict:
        conn = get_connection()
        allowed = {
            "input_data", "prompt_sent", "output_data",
            "tokens_input", "tokens_output", "cost_cents",
            "model_used", "started_at", "completed_at",
            "status", "error_message", "reasoning_trace",
            "credential_ids_used",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            row = conn.execute("SELECT * FROM wf_run_step_logs WHERE id = ?", (log_id,)).fetchone()
            return _row_to_dict(row)
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [log_id]
        conn.execute(f"UPDATE wf_run_step_logs SET {set_clause} WHERE id = ?", vals)
        conn.commit()
        row = conn.execute("SELECT * FROM wf_run_step_logs WHERE id = ?", (log_id,)).fetchone()
        return _row_to_dict(row)

    def list_step_logs(self, run_id: str) -> list[dict]:
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM wf_run_step_logs WHERE run_id = ? ORDER BY sequence ASC",
            (run_id,),
        ).fetchall()
        return _rows_to_dicts(rows)

    # ------------------------------------------------------------------
    # Escalation CRUD
    # ------------------------------------------------------------------

    def create_escalation(
        self,
        run_id: str,
        step_id: str,
        step_log_id: str,
        escalation_config_json: str = "{}",
        context_json: str = "{}",
    ) -> dict:
        conn = get_connection()
        eid = _new_id()
        now = _now()
        conn.execute(
            "INSERT INTO wf_escalations (id, run_id, step_id, step_log_id, status, escalation_config_json, context_json, created_at) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)",
            (eid, run_id, step_id, step_log_id, escalation_config_json, context_json, now),
        )
        conn.commit()
        return self.get_escalation(eid)

    def get_escalation(self, escalation_id: str) -> Optional[dict]:
        conn = get_connection()
        row = conn.execute("SELECT * FROM wf_escalations WHERE id = ?", (escalation_id,)).fetchone()
        return _row_to_dict(row)

    def list_escalations(self, status: Optional[str] = None) -> list[dict]:
        conn = get_connection()
        if status:
            rows = conn.execute(
                "SELECT * FROM wf_escalations WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM wf_escalations ORDER BY created_at DESC"
            ).fetchall()
        return _rows_to_dicts(rows)

    def respond_to_escalation(
        self,
        escalation_id: str,
        response_action: str,
        response_text: str = "",
        responded_by: str = "",
    ) -> Optional[dict]:
        conn = get_connection()
        now = _now()
        conn.execute(
            "UPDATE wf_escalations SET status = 'responded', responded_at = ?, response_action = ?, response_text = ?, responded_by = ? WHERE id = ? AND status = 'pending'",
            (now, response_action, response_text, responded_by, escalation_id),
        )
        if conn.total_changes == 0:
            return None
        conn.commit()
        return self.get_escalation(escalation_id)

    def save_run_state(self, run_id: str, state_json: str) -> dict:
        """Save or update structured workflow state for a run."""
        conn = get_connection()
        existing = conn.execute(
            "SELECT version FROM wf_run_state WHERE run_id = ?", (run_id,)
        ).fetchone()
        if existing:
            version = existing["version"] + 1
            conn.execute(
                "UPDATE wf_run_state SET state = ?, version = ? WHERE run_id = ?",
                (state_json, version, run_id),
            )
        else:
            version = 1
            conn.execute(
                "INSERT INTO wf_run_state (run_id, state, version) VALUES (?, ?, ?)",
                (run_id, state_json, version),
            )
        conn.commit()
        return {"run_id": run_id, "version": version}

    def get_run_state(self, run_id: str) -> dict:
        """Get structured workflow state for a run. Returns empty dict if not found."""
        conn = get_connection()
        row = conn.execute(
            "SELECT state, version FROM wf_run_state WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row:
            return {"run_id": run_id, "state": row["state"], "version": row["version"]}
        return {"run_id": run_id, "state": "{}", "version": 0}

    # ------------------------------------------------------------------
    # Workflow Graph
    # ------------------------------------------------------------------

    def get_workflow_graph(self, agent_id: str) -> dict:
        agent = self.get_agent(agent_id)
        if not agent:
            return {"agent": None, "steps": [], "connections": []}
        import json
        return {
            "agent": agent,
            "steps": self.list_steps(agent_id),
            "connections": self.list_connections(agent_id),
            "tool_instances": json.loads(agent.get("tool_instances_json", "{}")),
            "credentials": json.loads(agent.get("credentials_json", "[]")),
            "input_schema": json.loads(agent.get("input_schema_json", "{}")),
            "authority": json.loads(agent.get("authority_json", "{}")),
        }

    # ------------------------------------------------------------------
    # Escalation CRUD
    # ------------------------------------------------------------------

    def create_escalation(
        self,
        run_id: str,
        step_id: str,
        step_log_id: str,
        escalation_config_json: str = "{}",
        context_json: str = "{}",
    ) -> dict:
        conn = get_connection()
        eid = _new_id()
        now = _now()
        conn.execute(
            """INSERT INTO wf_escalations
               (id, run_id, step_id, step_log_id, status, escalation_config_json,
                context_json, created_at)
               VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (eid, run_id, step_id, step_log_id, escalation_config_json, context_json, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM wf_escalations WHERE id = ?", (eid,)).fetchone()
        return _row_to_dict(row)

    def get_escalation(self, escalation_id: str) -> Optional[dict]:
        conn = get_connection()
        row = conn.execute("SELECT * FROM wf_escalations WHERE id = ?", (escalation_id,)).fetchone()
        return _row_to_dict(row)

    def list_escalations(self, status: Optional[str] = None) -> list[dict]:
        conn = get_connection()
        if status:
            rows = conn.execute(
                "SELECT * FROM wf_escalations WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM wf_escalations ORDER BY created_at DESC").fetchall()
        return _rows_to_dicts(rows)

    def respond_to_escalation(
        self,
        escalation_id: str,
        response_action: str,
        response_text: str = "",
        responded_by: str = "",
    ) -> Optional[dict]:
        conn = get_connection()
        now = _now()
        existing = conn.execute(
            "SELECT * FROM wf_escalations WHERE id = ? AND status = 'pending'",
            (escalation_id,),
        ).fetchone()
        if not existing:
            return None
        conn.execute(
            """UPDATE wf_escalations SET
               status = 'responded',
               response_action = ?,
               response_text = ?,
               responded_by = ?,
               responded_at = ?
               WHERE id = ?""",
            (response_action, response_text, responded_by, now, escalation_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM wf_escalations WHERE id = ?", (escalation_id,)).fetchone()
        return _row_to_dict(row)

    # ------------------------------------------------------------------
    # Cost Estimation
    # ------------------------------------------------------------------

    def estimate_run_cost(self, agent_id: str) -> dict:
        """Estimate cost for all steps in a workflow."""
        steps = self.list_steps(agent_id)
        estimates = []
        total_est_cents = 0.0
        for step in steps:
            st = step.get("step_type", "llm_call")
            if st == "llm_call":
                prompt = step.get("prompt_template", "")
                model = step.get("model_name", "gemma-4-12b")
                pricing = MODEL_PRICING.get(model, DEFAULT_PRICING)
                tokens_in = max(100, len(prompt) // 4)
                tokens_out = step.get("estimated_output_tokens", 200)
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
            elif st in ("tool_call", "condition"):
                estimates.append({
                    "step_id": step["id"],
                    "label": step.get("label", ""),
                    "step_type": st,
                    "estimated_cost_cents": 0,
                })
        return {
            "steps": estimates,
            "total_estimated_cost_cents": round(total_est_cents, 4),
        }

    # ------------------------------------------------------------------
    # Clone Agent
    # ------------------------------------------------------------------

    def clone_agent(self, agent_id: str, new_name: Optional[str] = None) -> Optional[dict]:
        source = self.get_agent(agent_id)
        if not source:
            return None

        conn = get_connection()
        now = _now()
        new_agent_id = _new_id()
        clone_name = new_name or f"{source['name']} (copy)"

        conn.execute(
            """INSERT INTO wf_agents
               (id, name, description, status, total_cost_cents, total_runs, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 0, ?, ?)""",
            (new_agent_id, clone_name, source["description"], source["status"],
             source["total_cost_cents"], now, now),
        )

        old_steps = self.list_steps(agent_id)
        old_to_new: dict[str, str] = {}
        for s in old_steps:
            new_sid = _new_id()
            old_to_new[s["id"]] = new_sid
            conn.execute(
                """INSERT INTO wf_steps
                   (id, agent_id, label, step_type, prompt_template, tools_json,
                    model_name, next_step_id, yaml_step_id, loop_config_json,
                    subworkflow_config_json, escalation_config_json,
                    authority_json,
                    position_x, position_y, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_sid, new_agent_id, s["label"], s["step_type"],
                    s["prompt_template"], s["tools_json"], s["model_name"],
                    old_to_new.get(s["next_step_id"]) if s["next_step_id"] else None,
                    s.get("yaml_step_id", ""),
                    s["loop_config_json"],
                    s.get("subworkflow_config_json", "{}"),
                    s.get("escalation_config_json", "{}"),
                    s.get("authority_json", "{}"),
                    s["position_x"], s["position_y"], now, now,
                ),
            )

        old_conns = self.list_connections(agent_id)
        for c in old_conns:
            new_from = old_to_new.get(c["from_step_id"])
            new_to = old_to_new.get(c["to_step_id"])
            if new_from and new_to:
                conn.execute(
                    """INSERT INTO wf_step_connections
                       (id, agent_id, from_step_id, to_step_id, label, condition_expr, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (_new_id(), new_agent_id, new_from, new_to, c["label"], c["condition_expr"], now),
                )

        conn.commit()
        return self.get_workflow_graph(new_agent_id)