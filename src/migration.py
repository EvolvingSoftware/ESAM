"""Schema migration system for ES Agent Management.

Provides version-tracked, safe schema migrations.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from database import get_connection

logger = logging.getLogger(__name__)

META_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_versions (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    description TEXT NOT NULL
);
"""


def _run_sql(sql: str):
    """Execute SQL script."""
    conn = get_connection()
    conn.executescript(sql)
    conn.commit()


def _safe_alter_table(table: str, columns: dict[str, str]):
    """Add columns to a table only if they don't already exist."""
    conn = get_connection()
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for col_name, col_type in columns.items():
        if col_name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
    conn.commit()


def ensure_meta_schema():
    """Create the schema_versions tracking table."""
    conn = get_connection()
    conn.execute(META_SCHEMA)
    conn.commit()


def get_current_version() -> int:
    """Get the latest applied schema version. Returns 0 if none."""
    conn = get_connection()
    row = conn.execute("SELECT MAX(version) FROM schema_versions").fetchone()
    return row[0] if row[0] else 0


def get_pending_migrations() -> list[dict]:
    """Get all unapplied migrations, ordered by version."""
    current = get_current_version()
    pending = []
    for migration in MIGRATIONS:
        if migration["version"] > current:
            pending.append(migration)
    return sorted(pending, key=lambda m: m["version"])


def run_pending_migrations() -> list[dict]:
    """Run all pending migrations. Returns list of applied migrations."""
    applied = []
    for migration in get_pending_migrations():
        try:
            conn = get_connection()
            migration["up"]()
            conn.execute(
                "INSERT OR IGNORE INTO schema_versions (version, applied_at, description) VALUES (?, ?, ?)",
                (migration["version"], datetime.now(timezone.utc).isoformat(), migration["description"]),
            )
            conn.commit()
            applied.append(migration)
            logger.info("Applied migration v%d: %s", migration["version"], migration["description"])
        except Exception:
            logger.exception("Failed to apply migration v%d: %s", migration["version"], migration["description"])
            raise
    return applied


def rollback(version: int) -> bool:
    """Rollback to a specific version. Returns True if successful."""
    current = get_current_version()
    if version >= current:
        return False
    for migration in reversed(MIGRATIONS):
        if migration["version"] > version:
            try:
                conn = get_connection()
                # Delete version record BEFORE running down (down may drop the tracking table)
                version_to_delete = migration["version"]
                conn.execute("DELETE FROM schema_versions WHERE version = ?", (version_to_delete,))
                migration["down"]()
                conn.commit()
                logger.info("Rolled back migration v%d: %s", migration["version"], migration["description"])
            except Exception:
                logger.exception("Failed to rollback migration v%d", migration["version"])
                raise
    return True


def status() -> list[dict]:
    """Get full migration status: what's been applied and what's pending."""
    current = get_current_version()
    result = []
    for m in MIGRATIONS:
        result.append({
            "version": m["version"],
            "description": m["description"],
            "applied": m["version"] <= current,
        })
    return result


def _migrate_v2_up():
    """Add columns to wf_agents if they don't already exist."""
    conn = get_connection()
    existing = {row[1] for row in conn.execute("PRAGMA table_info(wf_agents)").fetchall()}
    cols = [
        ("tool_instances_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("credentials_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("input_schema_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("authority_json", "TEXT NOT NULL DEFAULT '{}'"),
    ]
    for name, typ in cols:
        if name not in existing:
            conn.executescript(f"ALTER TABLE wf_agents ADD COLUMN {name} {typ};")
    conn.commit()


def _migrate_v3_up():
    """Add yaml_step_id column to wf_steps."""
    conn = get_connection()
    existing = {row[1] for row in conn.execute("PRAGMA table_info(wf_steps)").fetchall()}
    if "yaml_step_id" not in existing:
        conn.executescript("ALTER TABLE wf_steps ADD COLUMN yaml_step_id TEXT;")
    conn.commit()


def _migrate_v4_scoring_rules_up():
    """Create wf_scoring_rules table for the deterministic scoring engine."""
    conn = get_connection()
    conn.executescript("""
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
    """)
    conn.commit()


def _migrate_v6_up():
    """Create wf_memory table for cross-run persistent state."""
    conn = get_connection()
    conn.executescript("""
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
    """)
    conn.commit()


def _migrate_v5_up():
    """Create wf_seen_store table for URL dedup."""
    conn = get_connection()
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS wf_seen_store ("
        "  id TEXT PRIMARY KEY,"
        "  workflow_id TEXT NOT NULL,"
        "  url_hash TEXT NOT NULL,"
        "  title_hash TEXT NOT NULL,"
        "  url TEXT NOT NULL,"
        "  title TEXT,"
        "  first_seen_run_id TEXT NOT NULL,"
        "  last_seen_run_id TEXT NOT NULL,"
        "  hit_count INTEGER DEFAULT 1,"
        "  first_seen_at TEXT NOT NULL,"
        "  last_seen_at TEXT NOT NULL,"
        "  UNIQUE(workflow_id, url_hash)"
        ");"
        "CREATE INDEX IF NOT EXISTS idx_wf_seen_store_workflow ON wf_seen_store(workflow_id);"
        "CREATE INDEX IF NOT EXISTS idx_wf_seen_store_url_hash ON wf_seen_store(url_hash);"
    )
    conn.commit()


def _migrate_v7_up():
    """Create wf_stories table for cross-run story/entity tracking."""
    conn = get_connection()
    conn.executescript("""
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
    """)
    conn.commit()


# ── Migration Definitions ────────────────────────────────────────

MIGRATIONS = [
    {
        "version": 1,
        "description": "Initial schema — all existing tables",
        "up": lambda: _run_sql("""
            CREATE TABLE IF NOT EXISTS schema_versions (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, description TEXT NOT NULL);
        """),
        "down": lambda: _run_sql("DROP TABLE IF EXISTS schema_versions"),
    },
    {
        "version": 2,
        "description": "Add tool_instances_json, credentials_json, input_schema_json, authority_json columns to wf_agents",
        "up": lambda: _migrate_v2_up(),
        "down": lambda: _run_sql("""
            CREATE TABLE wf_agents_v1 AS SELECT id, name, description, status, total_cost_cents, total_runs, created_at, updated_at FROM wf_agents;
            DROP TABLE wf_agents;
            ALTER TABLE wf_agents_v1 RENAME TO wf_agents;
        """),
    },
    {
        "version": 3,
        "description": "Add yaml_step_id column to wf_steps",
        "up": lambda: _migrate_v3_up(),
        "down": lambda: _run_sql("""
            CREATE TABLE wf_steps_v2 AS SELECT id, agent_id, label, step_type, prompt_template, tools_json, model_name, next_step_id, loop_config_json, subworkflow_config_json, escalation_config_json, authority_json, position_x, position_y, created_at, updated_at FROM wf_steps;
            DROP TABLE wf_steps;
            ALTER TABLE wf_steps_v2 RENAME TO wf_steps;
        """),
    },
    {
        "version": 4,
        "description": "Create wf_scoring_rules table for deterministic scoring engine",
        "up": lambda: _migrate_v4_scoring_rules_up(),
        "down": lambda: _run_sql("DROP TABLE IF EXISTS wf_scoring_rules"),
    },
    {
        "version": 6,
        "description": "Create wf_memory table for cross-run persistent state",
        "up": lambda: _migrate_v6_up(),
        "down": lambda: _run_sql("DROP TABLE IF EXISTS wf_memory"),
    },
    {
        "version": 5,
        "description": "Create wf_seen_store table for URL hash dedup across workflow runs",
        "up": lambda: _migrate_v5_up(),
        "down": lambda: _run_sql("DROP TABLE IF EXISTS wf_seen_store"),
    },
    {
        "version": 7,
        "description": "Create wf_stories table for cross-run story/entity tracking",
        "up": lambda: _migrate_v7_up(),
        "down": lambda: _run_sql("DROP TABLE IF EXISTS wf_stories"),
    },
    {
        "version": 8,
        "description": "Add config_json column to wf_steps for step-type-specific config",
        "up": lambda: _run_sql("ALTER TABLE wf_steps ADD COLUMN config_json TEXT DEFAULT '{}';"),
        "down": lambda: _run_sql("CREATE TABLE wf_steps_v7 AS SELECT id, agent_id, label, step_type, prompt_template, tools_json, model_name, next_step_id, loop_config_json, subworkflow_config_json, escalation_config_json, authority_json, yaml_step_id, position_x, position_y, created_at, updated_at FROM wf_steps; DROP TABLE wf_steps; ALTER TABLE wf_steps_v7 RENAME TO wf_steps;"),
    },
    {
        "version": 9,
        "description": "Create wf_email_templates table for HTML email template storage",
        "up": lambda: _run_sql("""
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
        """),
        "down": lambda: _run_sql("DROP TABLE IF EXISTS wf_email_templates;"),
    },
    {
        "version": 10,
        "description": "Create wf_prompt_patterns and wf_step_pattern_refs tables for Prompt Pattern Library",
        "up": lambda: _run_sql("""
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
        """),
        "down": lambda: _run_sql("DROP TABLE IF EXISTS wf_step_pattern_refs; DROP TABLE IF EXISTS wf_prompt_patterns;"),
    },
    {
        "version": 11,
        "description": "Record http_fetch step type support (stateless — no new tables needed)",
        "up": lambda: _run_sql(
            "INSERT OR IGNORE INTO schema_versions (version, applied_at, description) "
            "VALUES (11, datetime('now'), 'Record http_fetch step type support');"
        ),
        "down": lambda: _run_sql("DELETE FROM schema_versions WHERE version = 11;"),
    },
    {
        "version": 12,
        "description": "Create wf_delivery_log table for SMTP delivery tracking",
        "up": lambda: _run_sql("""
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
        """),
        "down": lambda: _run_sql("DROP TABLE IF EXISTS wf_delivery_log;"),
    },
    {
        "version": 13,
        "description": "Record parser step types support (parse_rss, parse_jsonpath, parse_xpath, parse_html, resolve_id_list — stateless, no new tables needed)",
        "up": lambda: _run_sql(
            "INSERT OR IGNORE INTO schema_versions (version, applied_at, description) "
            "VALUES (13, datetime('now'), 'Record parser step types support');"
        ),
        "down": lambda: _run_sql("DELETE FROM schema_versions WHERE version = 13;"),
    },
    {
        "version": 14,
        "description": "Create wf_content_sources and wf_content_source_items tables for YAML-driven content source definitions",
        "up": lambda: _run_sql("""
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
        """),
        "down": lambda: _run_sql("DROP TABLE IF EXISTS wf_content_source_items; DROP TABLE IF EXISTS wf_content_sources;"),
    },
    {
        "version": 15,
        "description": "Record verifier step types support (verify_claims, grade_citations, reject_if_invalid — stateless, no new tables needed)",
        "up": lambda: _run_sql(
            "INSERT OR IGNORE INTO schema_versions (version, applied_at, description) "
            "VALUES (15, datetime('now'), 'Record verifier step types support');"
        ),
        "down": lambda: _run_sql("DELETE FROM schema_versions WHERE version = 15;"),
    },
    {
        "version": 16,
        "description": "Create wf_raw_items table for content extraction (readability + metadata)",
        "up": lambda: _run_sql("""
            CREATE TABLE IF NOT EXISTS wf_raw_items (
                id TEXT PRIMARY KEY,
                source_id TEXT, item_url TEXT UNIQUE,
                title TEXT, author TEXT, published_date TEXT,
                body_raw TEXT, body_extracted TEXT, excerpt TEXT,
                word_count INTEGER, reading_time_seconds REAL,
                metadata_json TEXT, extraction_errors TEXT,
                fetch_run_id TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_wf_raw_items_url ON wf_raw_items(item_url);
            CREATE INDEX IF NOT EXISTS idx_wf_raw_items_source ON wf_raw_items(source_id);
            CREATE INDEX IF NOT EXISTS idx_wf_raw_items_fetch_run ON wf_raw_items(fetch_run_id);
        """),
        "down": lambda: _run_sql("DROP TABLE IF EXISTS wf_raw_items;"),
    },
    {
        "version": 17,
        "description": "Create wf_archived_editions table for archive system",
        "up": lambda: _run_sql("""
            CREATE TABLE IF NOT EXISTS wf_archived_editions (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                edition_number INTEGER,
                date TEXT, subject TEXT,
                body_html TEXT, body_markdown TEXT,
                archive_path TEXT, permalink TEXT,
                citation_count INTEGER, source_count INTEGER, item_count INTEGER,
                created_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_wf_archived_editions_run ON wf_archived_editions(run_id);
            CREATE INDEX IF NOT EXISTS idx_wf_archived_editions_date ON wf_archived_editions(date);
        """),
        "down": lambda: _run_sql("DROP TABLE IF EXISTS wf_archived_editions;"),
    },
    {
        "version": 18,
        "description": "Create wf_citation_map table for sequential citation ID storage and resolution",
        "up": lambda: _run_sql("""
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
        """),
        "down": lambda: _run_sql("DROP TABLE IF EXISTS wf_citation_map;"),
    },
    {
        "version": 19,
        "description": "Add narrative_summary, signal_trajectory, related_story_ids columns to wf_stories for Story Diff Engine",
        "up": lambda: _safe_alter_table("wf_stories", {
            "narrative_summary": "TEXT DEFAULT ''",
            "signal_trajectory": "TEXT DEFAULT 'new'",
            "related_story_ids": "TEXT DEFAULT '[]'",
        }),
        "down": lambda: _run_sql("""
            CREATE TABLE wf_stories_v18 AS SELECT
                id, workflow_id, title, title_hash,
                first_seen_run_id, last_seen_run_id,
                edition_count, signal_strength,
                change_log_json, last_headline, last_body_snippet,
                sources_json, tags, created_at, updated_at
            FROM wf_stories;
            DROP TABLE wf_stories;
            ALTER TABLE wf_stories_v18 RENAME TO wf_stories;
            CREATE INDEX IF NOT EXISTS idx_wf_stories_workflow ON wf_stories(workflow_id);
            CREATE INDEX IF NOT EXISTS idx_wf_stories_signal ON wf_stories(workflow_id, signal_strength DESC);
            CREATE INDEX IF NOT EXISTS idx_wf_stories_title_hash ON wf_stories(workflow_id, title_hash);
        """),
    },
    {
        "version": 18,
        "description": "Create wf_entity_dictionary table for entity extraction (dictionary-based entity definitions)",
        "up": lambda: _run_sql("""
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
        """),
        "down": lambda: _run_sql("DROP TABLE IF EXISTS wf_entity_dictionary;"),
    },
    {
        "version": 22,
        "description": "Record cross-ref step types support (detect_cross_references, boost_multi_sourced, cluster_by_topic — stateless, no new tables needed)",
        "up": lambda: _run_sql(
            "INSERT OR IGNORE INTO schema_versions (version, applied_at, description) "
            "VALUES (22, datetime('now'), 'Record cross-ref step types support');"
        ),
        "down": lambda: _run_sql("DELETE FROM schema_versions WHERE version = 22;"),
    },
    {
        "version": 23,
        "description": "Create wf_editions table for edition metadata registry (edition comparison, stats, trend)",
        "up": lambda: _run_sql("""\
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
        """),
        "down": lambda: _run_sql("DROP TABLE IF EXISTS wf_editions;"),
    },
    {
        "version": 24,
        "description": "Add synthesize_narrative, detect_narrative_arcs, generate_article_ideas step types (stateless)",
        "up": lambda: _run_sql(
            "INSERT OR IGNORE INTO schema_versions (version, applied_at, description) "
            "VALUES (24, datetime('now'), 'Add synthesize_narrative, detect_narrative_arcs, generate_article_ideas step types');"
        ),
        "down": lambda: _run_sql("DELETE FROM schema_versions WHERE version = 24;"),
    },
    {
        "version": 26,
        "description": "Add pattern renderer/sandbox step types (render_pattern_with_version, sandbox_verify_pattern — stateless)",
        "up": lambda: _run_sql(
            "INSERT OR IGNORE INTO schema_versions (version, applied_at, description) "
            "VALUES (26, datetime('now'), 'Add pattern renderer/sandbox step types: render_pattern_with_version, sandbox_verify_pattern');"
        ),
        "down": lambda: _run_sql("DELETE FROM schema_versions WHERE version = 26;"),
    },
    {
        "version": 27,
        "description": "Add regression step types (run_regression_tests, update_baseline — stateless, no new tables needed)",
        "up": lambda: _run_sql(
            "INSERT OR IGNORE INTO schema_versions (version, applied_at, description) "
            "VALUES (27, datetime('now'), 'Add regression step types: run_regression_tests, update_baseline');"
        ),
        "down": lambda: _run_sql("DELETE FROM schema_versions WHERE version = 27;"),
    },
    {
        "version": 28,
        "description": "Create wf_schedule_meta and wf_schedule_history tables for visual cron editor with classifications",
        "up": lambda: _run_sql("""
            CREATE TABLE IF NOT EXISTS wf_schedule_meta (
                id              TEXT PRIMARY KEY,
                cron_job_id     TEXT NOT NULL UNIQUE,
                name            TEXT NOT NULL,
                description     TEXT NOT NULL DEFAULT '',
                department      TEXT NOT NULL DEFAULT '',
                team            TEXT NOT NULL DEFAULT '',
                project         TEXT NOT NULL DEFAULT '',
                task_type       TEXT NOT NULL DEFAULT '',
                tags            TEXT NOT NULL DEFAULT '[]',
                schedule_type   TEXT NOT NULL DEFAULT 'cron',
                status          TEXT NOT NULL DEFAULT 'unknown',
                next_run        TEXT DEFAULT '',
                last_run        TEXT DEFAULT '',
                last_status     TEXT DEFAULT '',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_wf_schedule_meta_cron_job
                ON wf_schedule_meta(cron_job_id);
            CREATE INDEX IF NOT EXISTS idx_wf_schedule_meta_department
                ON wf_schedule_meta(department);
            CREATE INDEX IF NOT EXISTS idx_wf_schedule_meta_team
                ON wf_schedule_meta(team);
            CREATE INDEX IF NOT EXISTS idx_wf_schedule_meta_project
                ON wf_schedule_meta(project);
            CREATE INDEX IF NOT EXISTS idx_wf_schedule_meta_task_type
                ON wf_schedule_meta(task_type);
            CREATE INDEX IF NOT EXISTS idx_wf_schedule_meta_status
                ON wf_schedule_meta(status);

            CREATE TABLE IF NOT EXISTS wf_schedule_history (
                id              TEXT PRIMARY KEY,
                schedule_id     TEXT NOT NULL REFERENCES wf_schedule_meta(id) ON DELETE CASCADE,
                cron_job_id     TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                started_at      TEXT DEFAULT '',
                finished_at     TEXT DEFAULT '',
                duration_sec    REAL DEFAULT 0.0,
                output_summary  TEXT DEFAULT '',
                error_message   TEXT DEFAULT '',
                created_at      TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_wf_schedule_history_schedule
                ON wf_schedule_history(schedule_id);
            CREATE INDEX IF NOT EXISTS idx_wf_schedule_history_cron
                ON wf_schedule_history(cron_job_id);
        """),
        "down": lambda: _run_sql(
            "DROP TABLE IF EXISTS wf_schedule_history; DROP TABLE IF EXISTS wf_schedule_meta;"
        ),
    },
]
