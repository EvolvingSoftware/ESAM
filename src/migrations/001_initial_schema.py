"""
Migration 001: Initial schema capture.
Generated from current database state.
"""
UP_SQL = """\
CREATE TABLE abn_verifications (
    id              TEXT PRIMARY KEY,
    abn             TEXT NOT NULL,
    entity_name     TEXT DEFAULT '',
    result_json     TEXT NOT NULL,
    verified_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
)

CREATE TABLE accounting_connections (
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
)

CREATE TABLE agents (
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
)

CREATE TABLE audit_logs (
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
)

CREATE TABLE bpay_payments (
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
)

CREATE TABLE conversations (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL REFERENCES agents(id),
    workflow_id     TEXT DEFAULT '',
    run_id          TEXT DEFAULT '',
    
    title           TEXT DEFAULT '',
    status          TEXT DEFAULT 'active',  -- active | archived | exported
    message_count   INTEGER DEFAULT 0,
    
    started_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ended_at        TEXT DEFAULT ''
)

CREATE TABLE cost_entries (
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
)

CREATE TABLE credit_checks (
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
)

CREATE TABLE debtor_replies (
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
)

CREATE TABLE debtors (
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
)

CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            abn TEXT,
            address TEXT,
            phone TEXT,
            email TEXT,
            logo_url TEXT,
            payment_terms_days INTEGER DEFAULT 30,
            late_fee_percent REAL DEFAULT 2.0,
            late_fee_days_grace INTEGER DEFAULT 7,
            stripe_account_id TEXT,
            settings_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )

CREATE TABLE entity_debtors (
                id TEXT PRIMARY KEY,
                entity_id TEXT NOT NULL,
                debtor_id TEXT NOT NULL,
                FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE,
                UNIQUE(entity_id, debtor_id)
            )

CREATE TABLE entity_users (
            id TEXT PRIMARY KEY,
            entity_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            role TEXT CHECK(role IN ('admin', 'manager', 'viewer')) NOT NULL,
            permissions_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE
        )

CREATE TABLE eval_datasets (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
)

CREATE TABLE eval_items (
    id              TEXT PRIMARY KEY,
    dataset_id      TEXT NOT NULL,
    input_text      TEXT NOT NULL,
    expected_output TEXT DEFAULT '',
    metadata_json   TEXT DEFAULT '{}',
    created_at      TEXT NOT NULL,
    FOREIGN KEY (dataset_id) REFERENCES eval_datasets(id) ON DELETE CASCADE
)

CREATE TABLE eval_results (
    id              TEXT PRIMARY KEY,
    eval_run_id     TEXT NOT NULL,
    eval_item_id    TEXT NOT NULL,
    agent_output    TEXT DEFAULT '',
    exact_match     INTEGER,
    contains_match  INTEGER,
    score           REAL,
    llm_accuracy    REAL,
    llm_completeness REAL,
    llm_clarity     REAL,
    llm_overall     REAL,
    llm_feedback    TEXT DEFAULT '',
    duration_ms     INTEGER,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (eval_run_id) REFERENCES eval_runs(id) ON DELETE CASCADE,
    FOREIGN KEY (eval_item_id) REFERENCES eval_items(id)
)

CREATE TABLE eval_runs (
    id          TEXT PRIMARY KEY,
    dataset_id  TEXT NOT NULL,
    agent_id    TEXT DEFAULT '',
    run_id      TEXT DEFAULT '',
    notes       TEXT DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending',
    started_at  TEXT,
    finished_at TEXT,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (dataset_id) REFERENCES eval_datasets(id)
)

CREATE TABLE events (
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
)

CREATE TABLE fee_assessments (
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
)

CREATE TABLE fee_revenue (
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
)

CREATE TABLE invoice_mappings (
    id                  TEXT PRIMARY KEY,
    platform            TEXT NOT NULL,           -- xero | quickbooks | myob
    platform_invoice_id TEXT NOT NULL,           -- Native ID in source system
    invoice_number      TEXT NOT NULL,
    business_id         TEXT NOT NULL DEFAULT 'biz-001',
    debtor_id           TEXT DEFAULT '',          -- Linked Tether debtor
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
)

CREATE TABLE late_fee_rules (
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
)

CREATE TABLE messages (
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
)

CREATE TABLE payment_plans (
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
)

CREATE TABLE policies (
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
)

CREATE TABLE policy_evaluations (
    id              TEXT PRIMARY KEY,
    policy_id       TEXT NOT NULL REFERENCES policies(id),
    audit_log_id    TEXT REFERENCES audit_logs(id),
    
    resource        TEXT NOT NULL,
    decision        TEXT NOT NULL,  -- allowed | denied | requires_review
    matched_conditions TEXT DEFAULT '{}',
    
    evaluated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
)

CREATE TABLE portal_messages (
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
)

CREATE TABLE portal_tokens (
    id              TEXT PRIMARY KEY,
    debtor_id       TEXT NOT NULL REFERENCES debtors(id),
    token_hash      TEXT NOT NULL UNIQUE,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    expires_at      TEXT NOT NULL,
    last_accessed   TEXT DEFAULT '',
    is_active       INTEGER DEFAULT 1
)

CREATE TABLE prompt_versions (
    id              TEXT PRIMARY KEY,
    step_id         TEXT NOT NULL,
    version         INTEGER NOT NULL,
    prompt_template TEXT NOT NULL DEFAULT '',
    rendered_prompt TEXT DEFAULT '',
    run_id          TEXT DEFAULT '',
    context_data    TEXT DEFAULT '{}',
    output_data     TEXT DEFAULT '{}',
    tokens_input    INTEGER DEFAULT 0,
    tokens_output   INTEGER DEFAULT 0,
    model_used      TEXT DEFAULT '',
    notes           TEXT DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
)

CREATE TABLE ptrs_checks (
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
)

CREATE TABLE sync_log (
    id              TEXT PRIMARY KEY,
    platform        TEXT NOT NULL,
    business_id     TEXT DEFAULT '',
    connection_id   TEXT DEFAULT '',
    status          TEXT NOT NULL,         -- started | completed | failed | webhook_received | payment_pushed | payment_failed
    details         TEXT DEFAULT '{}',     -- JSON
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
)

CREATE TABLE trace_spans (
    id              TEXT PRIMARY KEY,
    trace_id        TEXT NOT NULL,
    span_name       TEXT NOT NULL,
    span_type       TEXT DEFAULT 'step',
    parent_span_id  TEXT,
    step_id         TEXT,
    run_id          TEXT,
    input_data      TEXT DEFAULT '{}',
    output_data     TEXT DEFAULT '{}',
    tokens_input    INTEGER DEFAULT 0,
    tokens_output   INTEGER DEFAULT 0,
    cost_cents      INTEGER DEFAULT 0,
    duration_ms     INTEGER DEFAULT 0,
    model_used      TEXT DEFAULT '',
    status          TEXT DEFAULT 'pending',
    error_message   TEXT DEFAULT '',
    started_at      TEXT NOT NULL,
    ended_at        TEXT
)

CREATE TABLE users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )

CREATE TABLE wf_agent_credentials (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL REFERENCES wf_agents(id) ON DELETE CASCADE,
    credential_key  TEXT NOT NULL,
    encrypted_value TEXT NOT NULL,
    scope_step_id   TEXT REFERENCES wf_steps(id) ON DELETE SET NULL,
    created_at      TEXT NOT NULL
)

CREATE TABLE wf_agent_runs (
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
)

CREATE TABLE wf_agents (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    status          TEXT DEFAULT 'draft',
    total_cost_cents INTEGER DEFAULT 0,
    total_runs      INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
)

CREATE TABLE wf_api_keys (
    id           TEXT PRIMARY KEY,
    entity_id    TEXT NOT NULL DEFAULT '',
    name         TEXT NOT NULL,
    key_prefix   TEXT NOT NULL,
    key_hash     TEXT NOT NULL,
    scopes       TEXT NOT NULL DEFAULT '["run"]',
    expires_at   TEXT,
    last_used_at TEXT,
    created_at   TEXT NOT NULL,
    revoked_at   TEXT DEFAULT NULL
)

CREATE TABLE wf_audit_events (
    id            TEXT PRIMARY KEY,
    actor_id      TEXT NOT NULL,
    actor_type    TEXT NOT NULL DEFAULT 'user',  -- user, api_key, system
    action        TEXT NOT NULL,  -- create, update, delete, export, login, logout
    resource_type TEXT NOT NULL,  -- agent, step, credential, workflow, eval_dataset
    resource_id   TEXT NOT NULL,
    old_state     TEXT DEFAULT '{}',  -- JSON snapshot before
    new_state     TEXT DEFAULT '{}',  -- JSON snapshot after
    ip_address    TEXT DEFAULT '',
    user_agent    TEXT DEFAULT '',
    entity_id     TEXT NOT NULL,
    created_at    TEXT NOT NULL
)

CREATE TABLE wf_jobs (
    id              TEXT PRIMARY KEY,
    type            TEXT NOT NULL,           -- 'workflow_run', 'eval_run'
    agent_id        TEXT,
    input_json      TEXT DEFAULT '{}',
    status          TEXT DEFAULT 'queued',   -- queued, running, completed, failed, cancelled
    progress        INTEGER DEFAULT 0,       -- completed steps
    total           INTEGER DEFAULT 0,       -- total steps
    result_json     TEXT DEFAULT '',
    error_msg       TEXT DEFAULT '',
    idempotency_key TEXT DEFAULT '',
    timeout_s       INTEGER DEFAULT 300,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    completed_at    TEXT
)

CREATE TABLE wf_run_state (
    run_id  TEXT PRIMARY KEY REFERENCES wf_agent_runs(id),
    state   TEXT NOT NULL DEFAULT '{}',
    version INTEGER DEFAULT 1
)

CREATE TABLE wf_run_step_logs (
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
)

CREATE TABLE wf_step_connections (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL REFERENCES wf_agents(id) ON DELETE CASCADE,
    from_step_id    TEXT NOT NULL REFERENCES wf_steps(id) ON DELETE CASCADE,
    to_step_id      TEXT NOT NULL REFERENCES wf_steps(id) ON DELETE CASCADE,
    label           TEXT DEFAULT '',
    condition_expr  TEXT DEFAULT '',
    created_at      TEXT NOT NULL
)

CREATE TABLE wf_steps (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL REFERENCES wf_agents(id) ON DELETE CASCADE,
    label           TEXT DEFAULT '',
    step_type       TEXT DEFAULT 'llm_call',
    prompt_template TEXT DEFAULT '',
    tools_json      TEXT DEFAULT '[]',
    model_name      TEXT DEFAULT '',
    next_step_id    TEXT REFERENCES wf_steps(id) ON DELETE SET NULL,
    loop_config_json TEXT DEFAULT '{}',
    position_x      REAL DEFAULT 0,
    position_y      REAL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
, subworkflow_config_json TEXT DEFAULT '{}')

CREATE TABLE workflow_runs (
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
)

CREATE TABLE workflow_steps (
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
)

CREATE TABLE workflows (
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
)
"""
DOWN_SQL = """\
DROP TABLE IF EXISTS CREATE TABLE abn_verifications;
DROP TABLE IF EXISTS CREATE TABLE accounting_connections;
DROP TABLE IF EXISTS CREATE TABLE agents;
DROP TABLE IF EXISTS CREATE TABLE audit_logs;
DROP TABLE IF EXISTS CREATE TABLE bpay_payments;
DROP TABLE IF EXISTS CREATE TABLE conversations;
DROP TABLE IF EXISTS CREATE TABLE cost_entries;
DROP TABLE IF EXISTS CREATE TABLE credit_checks;
DROP TABLE IF EXISTS CREATE TABLE debtor_replies;
DROP TABLE IF EXISTS CREATE TABLE debtors;
DROP TABLE IF EXISTS CREATE TABLE entities;
DROP TABLE IF EXISTS CREATE TABLE entity_debtors;
DROP TABLE IF EXISTS CREATE TABLE entity_users;
DROP TABLE IF EXISTS CREATE TABLE eval_datasets;
DROP TABLE IF EXISTS CREATE TABLE eval_items;
DROP TABLE IF EXISTS CREATE TABLE eval_results;
DROP TABLE IF EXISTS CREATE TABLE eval_runs;
DROP TABLE IF EXISTS CREATE TABLE events;
DROP TABLE IF EXISTS CREATE TABLE fee_assessments;
DROP TABLE IF EXISTS CREATE TABLE fee_revenue;
DROP TABLE IF EXISTS CREATE TABLE invoice_mappings;
DROP TABLE IF EXISTS CREATE TABLE late_fee_rules;
DROP TABLE IF EXISTS CREATE TABLE messages;
DROP TABLE IF EXISTS CREATE TABLE payment_plans;
DROP TABLE IF EXISTS CREATE TABLE policies;
DROP TABLE IF EXISTS CREATE TABLE policy_evaluations;
DROP TABLE IF EXISTS CREATE TABLE portal_messages;
DROP TABLE IF EXISTS CREATE TABLE portal_tokens;
DROP TABLE IF EXISTS CREATE TABLE prompt_versions;
DROP TABLE IF EXISTS CREATE TABLE ptrs_checks;
DROP TABLE IF EXISTS CREATE TABLE sync_log;
DROP TABLE IF EXISTS CREATE TABLE trace_spans;
DROP TABLE IF EXISTS CREATE TABLE users;
DROP TABLE IF EXISTS CREATE TABLE wf_agent_credentials;
DROP TABLE IF EXISTS CREATE TABLE wf_agent_runs;
DROP TABLE IF EXISTS CREATE TABLE wf_agents;
DROP TABLE IF EXISTS CREATE TABLE wf_api_keys;
DROP TABLE IF EXISTS CREATE TABLE wf_audit_events;
DROP TABLE IF EXISTS CREATE TABLE wf_jobs;
DROP TABLE IF EXISTS CREATE TABLE wf_run_state;
DROP TABLE IF EXISTS CREATE TABLE wf_run_step_logs;
DROP TABLE IF EXISTS CREATE TABLE wf_step_connections;
DROP TABLE IF EXISTS CREATE TABLE wf_steps;
DROP TABLE IF EXISTS CREATE TABLE workflow_runs;
DROP TABLE IF EXISTS CREATE TABLE workflow_steps;
DROP TABLE IF EXISTS CREATE TABLE workflows;
"""
