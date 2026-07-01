#!/usr/bin/env python3
"""ES Agent Management — Core Database Layer.

SQLite-backed persistence for the six pillars of agent management:
1. Agent Registry (discovery & inventory)
2. Workflow & Event Tracking (observability)
3. Audit Trail (compliance)
4. Conversation Recording (transparency)
5. Policy Engine (guardrails)
6. Cost Attribution (economics)

All timestamps in ISO 8601 / UTC.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

# ── Config ──────────────────────────────────────────────────────────

DB_DIR = Path(os.environ.get("ESAM_DB_DIR", Path.home() / ".hermes" / "esam"))
DB_PATH = DB_DIR / "state.db"

# Ensure directory exists
DB_DIR.mkdir(parents=True, exist_ok=True)


# ── Schema ──────────────────────────────────────────────────────────

SCHEMA_SQL = """
-- ============================================================
-- PILLAR 1: AGENT REGISTRY
-- ============================================================

CREATE TABLE IF NOT EXISTS agents (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    agent_type      TEXT NOT NULL DEFAULT 'hermes',
    version         TEXT DEFAULT '',
    owner           TEXT DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'offline',
    -- status: offline | running | paused | error | retired

    -- Identity & Access
    identity_ref    TEXT DEFAULT '',       -- e.g. "hermes::profile::default"
    permissions     TEXT DEFAULT '{}',     -- JSON: { "tools": [...], "data": [...] }
    
    -- Metadata
    skills          TEXT DEFAULT '[]',     -- JSON array of skill names
    cron_jobs       TEXT DEFAULT '[]',     -- JSON array of cron job refs
    memory_enabled  INTEGER DEFAULT 1,
    profile         TEXT DEFAULT 'default',
    
    -- Lifecycle
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_seen_at    TEXT DEFAULT '',
    retired_at      TEXT DEFAULT '',

    -- Heartbeat
    host            TEXT DEFAULT '',
    platform        TEXT DEFAULT 'macos',
    model_provider  TEXT DEFAULT '',
    model_name      TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
CREATE INDEX IF NOT EXISTS idx_agents_owner ON agents(owner);

-- ============================================================
-- PILLAR 2: WORKFLOW & EVENT TRACKING (Observability)
-- ============================================================

CREATE TABLE IF NOT EXISTS workflows (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL REFERENCES agents(id),
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'active',
    -- status: active | paused | completed | failed | archived

    -- Schedule
    schedule_type   TEXT DEFAULT 'manual',  -- manual | cron | webhook | event
    schedule_config TEXT DEFAULT '{}',      -- JSON: cron expression, etc.
    
    -- Governance
    policy_ref      TEXT DEFAULT '',
    max_duration_s  INTEGER DEFAULT 0,
    max_steps       INTEGER DEFAULT 0,
    
    -- Stats
    total_runs      INTEGER DEFAULT 0,
    successful_runs INTEGER DEFAULT 0,
    failed_runs     INTEGER DEFAULT 0,
    last_run_at     TEXT DEFAULT '',
    
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS workflow_runs (
    id              TEXT PRIMARY KEY,
    workflow_id     TEXT NOT NULL REFERENCES workflows(id),
    trigger         TEXT DEFAULT 'manual',  -- manual | cron | webhook | agent
    status          TEXT NOT NULL DEFAULT 'running',
    -- status: running | completed | failed | halted | timed_out
    
    input_data      TEXT DEFAULT '{}',      -- JSON snapshot of input
    output_data     TEXT DEFAULT '{}',      -- JSON snapshot of output
    error_message   TEXT DEFAULT '',
    
    started_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    completed_at    TEXT DEFAULT '',
    duration_ms     INTEGER DEFAULT 0,
    
    total_tokens    INTEGER DEFAULT 0,
    total_cost_usd  REAL DEFAULT 0.0,
    
    -- Audit link
    audit_trail_id  TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_workflow_runs_workflow ON workflow_runs(workflow_id);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_status ON workflow_runs(status);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_started ON workflow_runs(started_at);

CREATE TABLE IF NOT EXISTS workflow_steps (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES workflow_runs(id),
    step_order      INTEGER NOT NULL,
    step_name       TEXT NOT NULL,
    step_type       TEXT DEFAULT 'action',   -- action | decision | human_review | policy_check
    
    input_context   TEXT DEFAULT '{}',       -- JSON
    output_context  TEXT DEFAULT '{}',       -- JSON
    reasoning       TEXT DEFAULT '',         -- agent's reasoning trace for this step
    
    model_used      TEXT DEFAULT '',
    tokens_in       INTEGER DEFAULT 0,
    tokens_out      INTEGER DEFAULT 0,
    cost_usd        REAL DEFAULT 0.0,
    
    status          TEXT NOT NULL DEFAULT 'pending',
    -- pending | running | completed | failed | skipped
    
    started_at      TEXT DEFAULT '',
    completed_at    TEXT DEFAULT '',
    duration_ms     INTEGER DEFAULT 0,
    
    -- Policy check result
    policy_decision TEXT DEFAULT 'not_checked',
    -- not_checked | allowed | denied | requires_review
    policy_rule_id  TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_workflow_steps_run ON workflow_steps(run_id);

-- ============================================================
-- PILLAR 3: EVENTS & OBSERVABILITY
-- ============================================================

CREATE TABLE IF NOT EXISTS events (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT REFERENCES agents(id),
    workflow_id     TEXT REFERENCES workflows(id),
    run_id          TEXT REFERENCES workflow_runs(id),
    
    event_type      TEXT NOT NULL,
    severity        TEXT NOT NULL DEFAULT 'info',
    -- severity: debug | info | warning | error | critical
    
    title           TEXT DEFAULT '',
    description     TEXT DEFAULT '',
    details         TEXT DEFAULT '{}',      -- JSON payload
    
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_severity ON events(severity);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_agent ON events(agent_id);

-- ============================================================
-- PILLAR 4: AUDIT TRAIL (Compliance)
-- ============================================================

CREATE TABLE IF NOT EXISTS audit_logs (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL,
    workflow_id     TEXT DEFAULT '',
    run_id          TEXT DEFAULT '',
    
    -- Categorization
    category        TEXT NOT NULL,  -- tool_call | model_invocation | data_access | auth_decision | policy_eval | configuration_change | human_action
    action          TEXT NOT NULL,  -- read | write | invoke | approve | deny | modify | execute
    
    -- What happened
    resource        TEXT DEFAULT '',    -- e.g. "stripe::payment_link::create", "gemma::chat::completion"
    resource_type   TEXT DEFAULT '',
    summary         TEXT DEFAULT '',
    
    -- Provenance
    input_snapshot  TEXT DEFAULT '{}',  -- JSON: what the agent saw
    output_snapshot TEXT DEFAULT '{}',  -- JSON: what the agent produced
    reasoning_trace TEXT DEFAULT '',    -- agent's chain of thought
    
    -- Policy context
    policy_id       TEXT DEFAULT '',
    policy_decision TEXT DEFAULT '',    -- allowed | denied | human_approved
    policy_evidence TEXT DEFAULT '{}',  -- JSON: which rules matched
    
    -- Identity
    actor           TEXT DEFAULT '',    -- agent_id or human user_id
    human_approver  TEXT DEFAULT '',
    
    -- Integrity (immutable chain)
    previous_hash   TEXT DEFAULT '',
    hash            TEXT DEFAULT '',
    
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_agent ON audit_logs(agent_id);
CREATE INDEX IF NOT EXISTS idx_audit_category ON audit_logs(category);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_workflow ON audit_logs(workflow_id);
CREATE INDEX IF NOT EXISTS idx_audit_policy ON audit_logs(policy_id);

-- ============================================================
-- PILLAR 5: CONVERSATION RECORDING (Transparency)
-- ============================================================

CREATE TABLE IF NOT EXISTS conversations (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL REFERENCES agents(id),
    workflow_id     TEXT DEFAULT '',
    run_id          TEXT DEFAULT '',
    
    title           TEXT DEFAULT '',
    status          TEXT DEFAULT 'active',  -- active | archived | exported
    message_count   INTEGER DEFAULT 0,
    
    started_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ended_at        TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role            TEXT NOT NULL,  -- user | agent | tool | system
    content         TEXT NOT NULL,
    
    -- For tool calls / results
    tool_name       TEXT DEFAULT '',
    tool_input      TEXT DEFAULT '',
    tool_result     TEXT DEFAULT '',
    
    -- Model info
    model_used      TEXT DEFAULT '',
    tokens_in       INTEGER DEFAULT 0,
    tokens_out      INTEGER DEFAULT 0,
    
    step_order      INTEGER NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_step ON messages(conversation_id, step_order);

-- ============================================================
-- PILLAR 6: POLICY ENGINE (Guardrails)
-- ============================================================

CREATE TABLE IF NOT EXISTS policies (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    agent_id        TEXT DEFAULT '',       -- empty = global policy
    
    -- Policy definition
    policy_type     TEXT NOT NULL DEFAULT 'allow',  -- allow | deny | require_review
    resource_pattern TEXT NOT NULL,        -- glob pattern like "stripe::payment_link::*"
    conditions      TEXT DEFAULT '{}',     -- JSON: conditions for this rule
    
    priority        INTEGER DEFAULT 100,
    enabled         INTEGER DEFAULT 1,
    
    -- Compliance mapping
    framework_refs  TEXT DEFAULT '[]',     -- JSON: ["eu_ai_act:risk:high", "nist_ai_rmf:govern:1"]
    
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS policy_evaluations (
    id              TEXT PRIMARY KEY,
    policy_id       TEXT NOT NULL REFERENCES policies(id),
    audit_log_id    TEXT REFERENCES audit_logs(id),
    
    resource        TEXT NOT NULL,
    decision        TEXT NOT NULL,  -- allowed | denied | requires_review
    matched_conditions TEXT DEFAULT '{}',
    
    evaluated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- ============================================================
-- PILLAR 7: COST ATTRIBUTION
-- ============================================================

CREATE TABLE IF NOT EXISTS cost_entries (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT REFERENCES agents(id),
    workflow_id     TEXT REFERENCES workflows(id),
    run_id          TEXT REFERENCES workflow_runs(id),
    
    cost_type       TEXT NOT NULL,  -- model_inference | api_call | storage | compute
    provider        TEXT DEFAULT '',
    model_name      TEXT DEFAULT '',
    
    tokens_in       INTEGER DEFAULT 0,
    tokens_out      INTEGER DEFAULT 0,
    api_calls       INTEGER DEFAULT 0,
    duration_ms     INTEGER DEFAULT 0,
    
    cost_usd        REAL NOT NULL DEFAULT 0.0,
    
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_cost_workflow ON cost_entries(workflow_id);
CREATE INDEX IF NOT EXISTS idx_cost_agent ON cost_entries(agent_id);
CREATE INDEX IF NOT EXISTS idx_cost_type ON cost_entries(cost_type);

-- ============================================================
-- PILLAR 9: ACCOUNTING SOFTWARE INTEGRATION
-- ============================================================

CREATE TABLE IF NOT EXISTS accounting_connections (
    id              TEXT PRIMARY KEY,
    business_id     TEXT NOT NULL DEFAULT 'biz-001',
    platform        TEXT NOT NULL,         -- xero | quickbooks | myob
    access_token    TEXT DEFAULT '',
    refresh_token   TEXT DEFAULT '',
    token_type      TEXT DEFAULT 'bearer',
    scope           TEXT DEFAULT '',
    expires_at      TEXT DEFAULT '',       -- ISO 8601
    refresh_expires_at TEXT DEFAULT '',
    tenant_id       TEXT DEFAULT '',       -- Xero tenant / QB realm / MYOB company file
    tenant_name     TEXT DEFAULT '',
    tenant_details  TEXT DEFAULT '{}',     -- JSON
    is_active       INTEGER DEFAULT 1,
    last_sync_at    TEXT DEFAULT '',
    last_error      TEXT DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_acct_conn_platform ON accounting_connections(platform);
CREATE INDEX IF NOT EXISTS idx_acct_conn_business ON accounting_connections(business_id);
CREATE INDEX IF NOT EXISTS idx_acct_conn_active ON accounting_connections(is_active);

CREATE TABLE IF NOT EXISTS invoice_mappings (
    id                  TEXT PRIMARY KEY,
    platform            TEXT NOT NULL,           -- xero | quickbooks | myob
    platform_invoice_id TEXT NOT NULL,           -- Native ID in source system
    invoice_number      TEXT NOT NULL,
    business_id         TEXT NOT NULL DEFAULT 'biz-001',
    debtor_id           TEXT DEFAULT '',          -- Linked Tether debtor
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_inv_map_platform ON invoice_mappings(platform, platform_invoice_id);
CREATE INDEX IF NOT EXISTS idx_inv_map_invoice ON invoice_mappings(invoice_number);
CREATE INDEX IF NOT EXISTS idx_inv_map_debtor ON invoice_mappings(debtor_id);

CREATE TABLE IF NOT EXISTS sync_log (
    id              TEXT PRIMARY KEY,
    platform        TEXT NOT NULL,
    business_id     TEXT DEFAULT '',
    connection_id   TEXT DEFAULT '',
    status          TEXT NOT NULL,         -- started | completed | failed | webhook_received | payment_pushed | payment_failed
    details         TEXT DEFAULT '{}',     -- JSON
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_sync_log_created ON sync_log(created_at);
CREATE INDEX IF NOT EXISTS idx_sync_log_platform ON sync_log(platform);
CREATE INDEX IF NOT EXISTS idx_sync_log_status ON sync_log(status);

-- Accounting overview view
CREATE VIEW IF NOT EXISTS accounting_summary AS
SELECT
    c.id AS connection_id,
    c.business_id,
    c.platform,
    c.tenant_name,
    c.is_active,
    c.last_sync_at,
    c.last_error,
    (SELECT COUNT(*) FROM invoice_mappings im WHERE im.business_id = c.business_id AND im.platform = c.platform) AS mapped_invoices,
    (SELECT COUNT(*) FROM debtors d WHERE d.business_id = c.business_id AND d.state IN ('pending', 'active')) AS active_debtors,
    (SELECT COUNT(*) FROM sync_log sl WHERE sl.connection_id = c.id AND sl.status = 'completed' AND sl.created_at > datetime('now', '-7 days')) AS syncs_7d,
    (SELECT COUNT(*) FROM sync_log sl WHERE sl.connection_id = c.id AND sl.status = 'failed' AND sl.created_at > datetime('now', '-7 days')) AS failures_7d
FROM accounting_connections c;

-- ============================================================
-- VIEWS (For Dashboard)
-- ============================================================

-- ============================================================
-- PILLAR 8: DEBTOR PORTAL (Self-service)
-- ============================================================

CREATE TABLE IF NOT EXISTS debtors (
    id              TEXT PRIMARY KEY,
    business_id     TEXT NOT NULL DEFAULT 'biz-001',
    name            TEXT NOT NULL,
    email           TEXT NOT NULL DEFAULT '',
    phone           TEXT DEFAULT '',
    invoice_number  TEXT NOT NULL,
    amount_cents    INTEGER NOT NULL DEFAULT 0,
    due_date        TEXT NOT NULL DEFAULT '',
    days_overdue    INTEGER DEFAULT 0,
    escalation_tier TEXT DEFAULT 'standard',
    state           TEXT NOT NULL DEFAULT 'pending',
    -- state: pending | active | paid | disputed | manual_review | written_off
    dispute_reason  TEXT DEFAULT '',
    paid_at         TEXT DEFAULT '',
    paid_amount_cents INTEGER DEFAULT 0,
    current_step    INTEGER DEFAULT 0,
    last_action_at  TEXT DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    -- BPAY payment fields
    bpay_enabled        INTEGER DEFAULT 1,      -- BPAY offered for this debtor
    bpay_biller_code    TEXT DEFAULT '',         -- 5-digit Biller Code
    bpay_crn            TEXT DEFAULT '',         -- Customer Reference Number
    bpay_paid_at        TEXT DEFAULT '',         -- When settled via BPAY
    bpay_paid_cents     INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_debtors_business ON debtors(business_id);
CREATE INDEX IF NOT EXISTS idx_debtors_state ON debtors(state);
CREATE INDEX IF NOT EXISTS idx_debtors_email ON debtors(email);

CREATE TABLE IF NOT EXISTS portal_tokens (
    id              TEXT PRIMARY KEY,
    debtor_id       TEXT NOT NULL REFERENCES debtors(id),
    token_hash      TEXT NOT NULL UNIQUE,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    expires_at      TEXT NOT NULL,
    last_accessed   TEXT DEFAULT '',
    is_active       INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_portal_tokens_debtor ON portal_tokens(debtor_id);
CREATE INDEX IF NOT EXISTS idx_portal_tokens_hash ON portal_tokens(token_hash);

CREATE TABLE IF NOT EXISTS payment_plans (
    id              TEXT PRIMARY KEY,
    debtor_id       TEXT NOT NULL REFERENCES debtors(id),
    status          TEXT NOT NULL DEFAULT 'pending',
    -- status: pending | proposed | accepted | active | completed | defaulted | cancelled
    total_cents     INTEGER NOT NULL,
    instalments     INTEGER NOT NULL DEFAULT 3,
    instalment_cents INTEGER NOT NULL,
    frequency_days  INTEGER NOT NULL DEFAULT 30,
    start_date      TEXT DEFAULT '',
    next_payment    TEXT DEFAULT '',
    notes           TEXT DEFAULT '',
    proposed_at     TEXT DEFAULT '',
    accepted_at     TEXT DEFAULT '',
    completed_at    TEXT DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_payment_plans_debtor ON payment_plans(debtor_id);
CREATE INDEX IF NOT EXISTS idx_payment_plans_status ON payment_plans(status);

CREATE TABLE IF NOT EXISTS portal_messages (
    id              TEXT PRIMARY KEY,
    debtor_id       TEXT NOT NULL REFERENCES debtors(id),
    direction       TEXT NOT NULL DEFAULT 'debtor_to_biz',
    -- direction: debtor_to_biz | biz_to_debtor
    message_type    TEXT NOT NULL DEFAULT 'general',
    -- message_type: general | dispute | query | payment_proof
    subject         TEXT DEFAULT '',
    body            TEXT NOT NULL,
    attachment_ref  TEXT DEFAULT '',
    read_at         TEXT DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_portal_messages_debtor ON portal_messages(debtor_id);
CREATE INDEX IF NOT EXISTS idx_portal_messages_type ON portal_messages(message_type);

-- ============================================================
-- PILLAR 9: AUTOMATED REPLY HANDLING
-- ============================================================

CREATE TABLE IF NOT EXISTS debtor_replies (
    id              TEXT PRIMARY KEY,
    debtor_id       TEXT NOT NULL REFERENCES debtors(id),
    
    -- Raw email data
    email_from      TEXT DEFAULT '',
    subject         TEXT DEFAULT '',
    body            TEXT NOT NULL,
    body_truncated  INTEGER DEFAULT 0,
    
    -- Classification
    category        TEXT NOT NULL DEFAULT 'unclassified',
    -- category: dispute | promise_to_pay | out_of_office | query | general | unclassified
    confidence      REAL NOT NULL DEFAULT 0.0,
    action_taken    TEXT NOT NULL DEFAULT 'none',
    -- action_taken: halt_collection | pause_escalation | skip_retry | route_to_human | log_only | none
    
    -- Analysis metadata
    matched_keywords TEXT DEFAULT '[]',    -- JSON array of matched patterns
    summary         TEXT DEFAULT '',
    ai_explanation  TEXT DEFAULT '',       -- Gemma's reasoning if LLM was used
    suggested_response TEXT DEFAULT '',
    
    -- Resolution
    resolution      TEXT DEFAULT 'pending',
    -- resolution: pending | acknowledged | responded | resolved | ignored
    resolved_at     TEXT DEFAULT '',
    resolved_by     TEXT DEFAULT '',       -- agent_id or human id
    
    -- Retry schedule (for OOO replies)
    retry_after     TEXT DEFAULT '',       -- ISO date to retry after OOO
    
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_debtor_replies_debtor ON debtor_replies(debtor_id);
CREATE INDEX IF NOT EXISTS idx_debtor_replies_category ON debtor_replies(category);
CREATE INDEX IF NOT EXISTS idx_debtor_replies_action ON debtor_replies(action_taken);
CREATE INDEX IF NOT EXISTS idx_debtor_replies_created ON debtor_replies(created_at);

-- ============================================================
-- BPAY PAYMENT TRACKING
-- ============================================================

CREATE TABLE IF NOT EXISTS bpay_payments (
    id              TEXT PRIMARY KEY,
    debtor_id       TEXT NOT NULL REFERENCES debtors(id),
    business_id     TEXT NOT NULL DEFAULT 'biz-001',

    -- BPAY identifiers
    biller_code     TEXT NOT NULL,
    crn             TEXT NOT NULL,
    reference       TEXT DEFAULT '',          -- invoice number / reference

    -- Payment details
    amount_cents    INTEGER NOT NULL,
    amount_paid_cents INTEGER DEFAULT 0,

    -- Lifecycle
    status          TEXT NOT NULL DEFAULT 'pending',
    -- status: pending | processing | cleared | failed | refunded
    initiated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    expected_settlement TEXT DEFAULT '',
    settled_at      TEXT DEFAULT '',

    -- Tracking
    notes           TEXT DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_bpay_debtor ON bpay_payments(debtor_id);
CREATE INDEX IF NOT EXISTS idx_bpay_status ON bpay_payments(status);
CREATE INDEX IF NOT EXISTS idx_bpay_crn ON bpay_payments(crn);

-- BPAY payment summary view
CREATE VIEW IF NOT EXISTS bpay_summary AS
SELECT
    COALESCE(SUM(CASE WHEN status = 'cleared' THEN 1 ELSE 0 END), 0) AS cleared_count,
    COALESCE(SUM(CASE WHEN status = 'cleared' THEN amount_paid_cents ELSE 0 END), 0) AS cleared_total_cents,
    COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0) AS pending_count,
    COALESCE(SUM(CASE WHEN status IN ('pending','processing') THEN amount_cents ELSE 0 END), 0) AS pending_total_cents,
    COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failed_count,
    COUNT(*) AS total_payments
FROM bpay_payments;

-- Debtor overview view
CREATE VIEW IF NOT EXISTS debtor_overview AS
SELECT
    d.id,
    d.name,
    d.email,
    d.phone,
    d.invoice_number,
    d.amount_cents,
    d.due_date,
    d.days_overdue,
    d.state,
    d.escalation_tier,
    d.dispute_reason,
    d.paid_at,
    d.paid_amount_cents,
    d.current_step,
    d.last_action_at,
    d.created_at,
    d.bpay_enabled,
    d.bpay_biller_code,
    d.bpay_crn,
    d.bpay_paid_at,
    d.bpay_paid_cents,
    (SELECT COUNT(*) FROM bpay_payments bp WHERE bp.debtor_id = d.id) AS bpay_payments_count,
    (SELECT COUNT(*) FROM bpay_payments bp WHERE bp.debtor_id = d.id AND bp.status = 'cleared') AS bpay_cleared_count,
    (SELECT COUNT(*) FROM portal_messages m WHERE m.debtor_id = d.id) AS message_count,
    (SELECT COUNT(*) FROM portal_messages m WHERE m.debtor_id = d.id AND m.read_at = '') AS unread_count,
    (SELECT COUNT(*) FROM debtor_replies r WHERE r.debtor_id = d.id) AS reply_count,
    (SELECT r.category FROM debtor_replies r WHERE r.debtor_id = d.id ORDER BY r.created_at DESC LIMIT 1) AS latest_reply_category,
    (SELECT r.action_taken FROM debtor_replies r WHERE r.debtor_id = d.id ORDER BY r.created_at DESC LIMIT 1) AS latest_reply_action,
    (SELECT r.summary FROM debtor_replies r WHERE r.debtor_id = d.id ORDER BY r.created_at DESC LIMIT 1) AS latest_reply_summary,
    (SELECT json_group_array(json_object('id', pp.id, 'status', pp.status, 'instalments', pp.instalments, 'instalment_cents', pp.instalment_cents, 'total_cents', pp.total_cents, 'accepted_at', pp.accepted_at)) FROM payment_plans pp WHERE pp.debtor_id = d.id) AS payment_plans_json,
    (SELECT id FROM portal_tokens pt WHERE pt.debtor_id = d.id AND pt.is_active = 1 LIMIT 1) AS active_token_id
FROM debtors d;

-- ============================================================
-- PILLAR 8: LATE FEE AUTOMATION (ACCC-Compliant)
-- ============================================================

CREATE TABLE IF NOT EXISTS late_fee_rules (
    id              TEXT PRIMARY KEY,
    business_id     TEXT NOT NULL DEFAULT '',
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    
    -- Fee calculation type
    fee_type        TEXT NOT NULL DEFAULT 'interest',
    -- 'fixed' = flat fee per overdue invoice
    -- 'interest' = percentage interest calculated daily
    -- 'combined' = fixed fee + interest
    
    -- Fixed fee config
    fixed_amount_cents    INTEGER DEFAULT 0,   -- e.g. 500 = $5 flat late fee
    fixed_per_invoice     INTEGER DEFAULT 1,   -- 0 = per day, 1 = once per invoice
    
    -- Interest config
    interest_rate_pa      REAL DEFAULT 10.0,   -- Annual interest rate percentage (e.g. 10.0 = 10% p.a.)
    interest_calc_days    INTEGER DEFAULT 365, -- Day count convention (365 actual)
    interest_compounding  TEXT DEFAULT 'simple', -- simple | daily | monthly
    interest_max_cap_cents INTEGER DEFAULT 0,  -- 0 = no cap
    
    -- Timing
    grace_period_days     INTEGER DEFAULT 14,  -- Days after due before fees apply
    apply_from_day        INTEGER DEFAULT 1,   -- Day of overdue to start applying
    apply_frequency       TEXT DEFAULT 'monthly', -- once | daily | weekly | monthly
    
    -- Combined fee: if fee_type = 'combined', apply both
    combined_order        TEXT DEFAULT 'interest_first', -- interest_first | fixed_first
    
    -- Governance
    requires_disclosure   INTEGER DEFAULT 1,   -- Must be disclosed in terms of trade
    is_active             INTEGER DEFAULT 1,
    
    -- Compliance
    compliance_notes      TEXT DEFAULT 'ACCC: Must be disclosed upfront, reasonable, applied consistently. Interest should not exceed ~10-15% p.a. Fixed fee should reflect admin costs.',
    framework_refs        TEXT DEFAULT '["accc:late_fees","australian_consumer_law:unfair_contract_terms"]',
    
    created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS fee_assessments (
    id                  TEXT PRIMARY KEY,
    rule_id             TEXT NOT NULL REFERENCES late_fee_rules(id),
    debtor_id           TEXT NOT NULL,
    debtor_name         TEXT DEFAULT '',
    invoice_number      TEXT DEFAULT '',
    business_id         TEXT DEFAULT '',
    
    -- Assessment details
    original_amount_cents   INTEGER NOT NULL,
    days_overdue            INTEGER NOT NULL,
    
    -- Fee breakdown
    fixed_fee_cents         INTEGER DEFAULT 0,
    interest_cents          INTEGER DEFAULT 0,
    total_fee_cents         INTEGER NOT NULL,
    new_balance_cents       INTEGER NOT NULL,
    
    -- Fee notice
    notice_generated        INTEGER DEFAULT 0,
    notice_sent_at          TEXT DEFAULT '',
    notice_pdf_path         TEXT DEFAULT '',
    
    -- Status
    status                  TEXT NOT NULL DEFAULT 'pending',
    -- pending | applied | waived | paid
    
    -- Payment tracking
    paid_at                 TEXT DEFAULT '',
    paid_amount_cents       INTEGER DEFAULT 0,
    
    created_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_fee_assessments_debtor ON fee_assessments(debtor_id);
CREATE INDEX IF NOT EXISTS idx_fee_assessments_status ON fee_assessments(status);
CREATE INDEX IF NOT EXISTS idx_fee_assessments_rule ON fee_assessments(rule_id);
CREATE INDEX IF NOT EXISTS idx_fee_assessments_created ON fee_assessments(created_at);

CREATE TABLE IF NOT EXISTS fee_revenue (
    id                  TEXT PRIMARY KEY,
    assessment_id       TEXT NOT NULL REFERENCES fee_assessments(id),
    business_id         TEXT DEFAULT '',
    
    -- Revenue breakdown
    total_fees_assessed_cents   INTEGER NOT NULL,
    total_fees_collected_cents  INTEGER DEFAULT 0,
    total_fees_waived_cents     INTEGER DEFAULT 0,
    total_fees_pending_cents    INTEGER DEFAULT 0,
    
    -- Period
    period_start        TEXT NOT NULL,
    period_end          TEXT NOT NULL,
    
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_fee_revenue_business ON fee_revenue(business_id);
CREATE INDEX IF NOT EXISTS idx_fee_revenue_period ON fee_revenue(period_start, period_end);

-- Dashboard view for late fee summary
CREATE TABLE IF NOT EXISTS abn_verifications (
    id              TEXT PRIMARY KEY,
    abn             TEXT NOT NULL,
    entity_name     TEXT DEFAULT '',
    result_json     TEXT NOT NULL,
    verified_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_abn_verifications_abn ON abn_verifications(abn);
CREATE INDEX IF NOT EXISTS idx_abn_verifications_time ON abn_verifications(verified_at);

CREATE TABLE IF NOT EXISTS ptrs_checks (
    id                      TEXT PRIMARY KEY,
    abn                     TEXT NOT NULL,
    business_name           TEXT DEFAULT '',
    score                   INTEGER DEFAULT 0,
    score_label             TEXT DEFAULT 'Medium',
    risk_level              TEXT DEFAULT 'medium',
    risk_factors            TEXT DEFAULT '[]',
    warnings                TEXT DEFAULT '[]',
    credit_recommendation   TEXT DEFAULT '',
    recommended_limit_cents INTEGER DEFAULT 0,
    recommended_terms_days  INTEGER DEFAULT 30,
    result_json             TEXT DEFAULT '{}',
    check_time              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_ptrs_checks_abn ON ptrs_checks(abn);
CREATE INDEX IF NOT EXISTS idx_ptrs_checks_time ON ptrs_checks(check_time);
CREATE INDEX IF NOT EXISTS idx_ptrs_checks_risk ON ptrs_checks(risk_level);

CREATE TABLE IF NOT EXISTS credit_checks (
    id              TEXT PRIMARY KEY,
    abn             TEXT NOT NULL,
    entity_name     TEXT DEFAULT '',
    overall_risk    TEXT DEFAULT 'unknown',
    risk_factors    TEXT DEFAULT '[]',
    warnings        TEXT DEFAULT '[]',
    passed_checks   INTEGER DEFAULT 0,
    total_checks    INTEGER DEFAULT 0,
    result_json     TEXT DEFAULT '{}',
    verified_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_credit_checks_abn ON credit_checks(abn);
CREATE INDEX IF NOT EXISTS idx_credit_checks_risk ON credit_checks(overall_risk);
CREATE INDEX IF NOT EXISTS idx_credit_checks_time ON credit_checks(verified_at);

CREATE VIEW IF NOT EXISTS verification_summary AS
SELECT
    (SELECT COUNT(*) FROM abn_verifications) AS total_abn_checks,
    (SELECT COUNT(*) FROM ptrs_checks) AS total_ptrs_checks,
    (SELECT COUNT(*) FROM credit_checks) AS total_credit_checks,
    (SELECT COUNT(*) FROM credit_checks WHERE overall_risk = 'high' OR overall_risk = 'critical') AS high_risk_businesses,
    (SELECT COUNT(*) FROM credit_checks WHERE overall_risk = 'low') AS low_risk_businesses,
    (SELECT COUNT(*) FROM credit_checks WHERE passed_checks = total_checks AND total_checks > 0) AS fully_verified,
    (SELECT COALESCE(AVG(score), 0) FROM ptrs_checks) AS avg_ptrs_score,
    (SELECT COUNT(*) FROM ptrs_checks WHERE risk_level = 'high' OR risk_level = 'critical') AS high_risk_ptrs
WHERE 1; -- ensure we always get a row even if empty

-- Add verification counts to dashboard summary
-- (already handled by having verification_summary as a separate view)

-- ============================================================
-- PILLAR 9: SELF-SERVICE PORTAL (Debtor portal views, continued)
-- ============================================================

CREATE VIEW IF NOT EXISTS fee_summary AS
SELECT
    COALESCE(SUM(total_fee_cents), 0) AS total_fees_assessed_cents,
    COALESCE(SUM(CASE WHEN status = 'paid' THEN total_fee_cents ELSE 0 END), 0) AS total_fees_collected_cents,
    COALESCE(SUM(CASE WHEN status = 'waived' THEN total_fee_cents ELSE 0 END), 0) AS total_fees_waived_cents,
    COALESCE(SUM(CASE WHEN status IN ('pending','applied') THEN total_fee_cents ELSE 0 END), 0) AS total_fees_outstanding_cents,
    COUNT(*) AS total_assessments,
    COUNT(CASE WHEN status = 'pending' THEN 1 END) AS pending_assessments,
    COUNT(CASE WHEN status = 'paid' THEN 1 END) AS paid_assessments,
    COUNT(CASE WHEN status = 'waived' THEN 1 END) AS waived_assessments
FROM fee_assessments;

CREATE VIEW IF NOT EXISTS dashboard_summary AS
SELECT
    (SELECT COUNT(*) FROM agents WHERE status = 'running') AS agents_running,
    (SELECT COUNT(*) FROM agents WHERE status != 'retired') AS agents_total,
    (SELECT COUNT(*) FROM workflows WHERE status = 'active') AS workflows_active,
    (SELECT COUNT(*) FROM workflow_runs WHERE status = 'running') AS runs_active,
    (SELECT COUNT(*) FROM events WHERE severity IN ('warning','error','critical') AND created_at > datetime('now', '-24 hours')) AS alerts_24h,
    (SELECT COUNT(*) FROM audit_logs WHERE created_at > datetime('now', '-24 hours')) AS audit_entries_24h,
    (SELECT COALESCE(SUM(cost_usd), 0) FROM cost_entries WHERE created_at > datetime('now', '-7 days')) AS cost_7d,
    (SELECT COALESCE(SUM(cost_usd), 0) FROM cost_entries) AS cost_total,
    (SELECT COALESCE(SUM(total_fees_assessed_cents), 0) FROM fee_summary) AS fees_assessed_cents,
    (SELECT COALESCE(SUM(total_fees_collected_cents), 0) FROM fee_summary) AS fees_collected_cents,
    (SELECT COALESCE(SUM(total_fees_outstanding_cents), 0) FROM fee_summary) AS fees_outstanding_cents,
    (SELECT COUNT(*) FROM debtor_replies WHERE resolution = 'pending') AS replies_pending,
    (SELECT COUNT(*) FROM debtor_replies WHERE category = 'dispute' AND resolution = 'pending') AS disputes_pending;

CREATE VIEW IF NOT EXISTS agent_summary AS
SELECT
    a.id,
    a.name,
    a.status,
    a.agent_type,
    a.profile,
    a.last_seen_at,
    a.model_name,
    (SELECT COUNT(*) FROM workflows w WHERE w.agent_id = a.id) AS workflow_count,
    (SELECT COUNT(*) FROM events e WHERE e.agent_id = a.id AND e.severity IN ('warning','error','critical')) AS alert_count,
    (SELECT COALESCE(SUM(c.cost_usd), 0) FROM cost_entries c WHERE c.agent_id = a.id) AS total_cost
FROM agents a;
"""


# ── Database Connection ────────────────────────────────────────────

_local = threading.local()


def get_connection() -> sqlite3.Connection:
    """Get thread-local database connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(DB_PATH))
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def init_db():
    """Initialize the database schema. Idempotent."""
    conn = get_connection()
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def reset_db():
    """Drop and recreate. For development only."""
    conn = get_connection()
    tables = [
        "fee_revenue", "fee_assessments", "late_fee_rules",
        "debtor_replies",
        "portal_messages", "payment_plans",
        "portal_sessions", "portal_tokens", "debtors",
        "cost_entries", "policy_evaluations", "policies",
        "messages", "conversations",
        "audit_logs", "events",
        "workflow_steps", "workflow_runs", "workflows",
        "invoice_mappings", "sync_log", "accounting_connections",
        "credit_checks", "ptrs_checks", "abn_verifications",
        "agents",
    ]
    views = ["accounting_summary", "fee_summary", "dashboard_summary", "agent_summary", "debtor_overview", "verification_summary"]
    for v in views:
        conn.execute(f"DROP VIEW IF EXISTS {v}")
    # Disable FK checks during teardown to avoid ordering issues
    conn.execute("PRAGMA foreign_keys=OFF")
    for t in tables:
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    init_db()


# ── Context Manager ────────────────────────────────────────────────

@contextmanager
def transaction() -> Generator[sqlite3.Connection, None, None]:
    """Use inside a `with` block for automatic commit/rollback."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ── ID Generator ───────────────────────────────────────────────────

def new_id(prefix: str = "") -> str:
    """Generate a unique ID with optional prefix."""
    uid = uuid.uuid4().hex[:12]
    return f"{prefix}{uid}" if prefix else uid


def utc_now() -> str:
    """Current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
           f"{datetime.now(timezone.utc).microsecond:06d}Z"


# ── Simple Hash for Audit Chain ────────────────────────────────────

import hashlib

def compute_hash(entry: dict) -> str:
    """Compute SHA-256 hash of an audit entry for chain integrity."""
    serialized = json.dumps(entry, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


# ── CLI ────────────────────────────────────────────────────────────

def main():
    """Initialize the database."""
    import argparse
    parser = argparse.ArgumentParser(description="ESAM Database")
    parser.add_argument("--reset", action="store_true", help="Reset database")
    args = parser.parse_args()

    if args.reset:
        reset_db()
        print(f"  Database reset: {DB_PATH}")
    else:
        init_db()
        print(f"  Database initialized: {DB_PATH}")
        print(f"  Schema version: 6 pillars + 2 views")


if __name__ == "__main__":
    main()
