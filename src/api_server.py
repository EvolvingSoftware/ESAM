#!/usr/bin/env python3
"""ES Agent Management — FastAPI Server.

Serves the Current dashboard with real data from the database.
Provides REST endpoints for all six pillars.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add src to path
SRC = Path(__file__).parent
sys.path.insert(0, str(SRC))

from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

from database import init_db, get_connection
from agent_registry import AgentRegistry
from audit_trail import AuditTrail
from policy_engine import PolicyEngine
from late_fees import LateFeeEngine
from tether_engine import TetherEngine
from audit_log import ensure_schema, record_event, query_events
from abn_verification import (
    lookup_abn, validate_abn_format, verify_business,
    search_business_name, check_gst_registration,
)
from ptrs_check import check_ptrs
from reply_pipeline import ReplyPipeline
from auth import create_users_table, create_user, authenticate_user, create_access_token, get_current_user, get_current_entity_id, list_user_entities, create_api_keys_table, create_api_key, list_api_keys, revoke_api_key, validate_api_key
from seed import seed_all, clear_all
from agent_workflow import AgentWorkflowDB
from credential_store import CredentialStore
from workflow_executor import WorkflowExecutor
from tracing import TraceStore
from replay import ReplayEngine
from prompt_versioning import PromptVersionManager
from evaluator import Evaluator
from yaml_pipeline import sync_agent_to_yaml
from job_queue import get_worker
from audit_log import ensure_schema as ensure_audit_schema, record_event, query_events
from scheduler.routes import router as scheduler_router

logger = logging.getLogger(__name__)

# ── Logging Setup ─────────────────────────────────────────────────────

from logging_config import configure_logging

configure_logging()

# ── Schema Setup ─────────────────────────────────────────────────────

# Ensure audit log schema exists on startup
try:
    ensure_audit_schema()
except Exception:
    pass

# ── App ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="ES Agent Management",
    version="0.1.0",
    description="Evolving Software Agent Management — Current API",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Health / Readiness ─────────────────────────────────────────
@app.get("/health")
def health_check():
    from datetime import datetime, timezone
    try:
        conn = get_connection()
        conn.execute("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "version": "0.1.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database": "connected" if db_ok else "disconnected",
    }

@app.get("/ready")
def ready_check():
    from datetime import datetime, timezone
    try:
        conn = get_connection()
        count = conn.execute("SELECT COUNT(*) as c FROM wf_agent_runs").fetchone()["c"]
        db_ok = True
    except Exception:
        count = 0
        db_ok = False
    return {
        "status": "ready" if db_ok else "not_ready",
        "database": "connected" if db_ok else "disconnected",
        "runs_completed": count,
    }

from auth import decode_access_token, get_user_by_id
from starlette.requests import Request
from starlette.responses import JSONResponse

@app.middleware("http")
async def api_auth_middleware(request: Request, call_next):
    """Protect all /api/ routes (except auth, health, docs)."""
    path = request.url.path
    # Public routes
    # Public routes
    public_prefixes = ["/api/auth/", "/api/portal/", "/api/seed", "/api/workflow/", "/api/audit", "/api/jobs", "/api/escalations", "/api/tools", "/api/health/", "/api/metrics", "/api/archive/", "/api/editions/", "/designer", "/agents", "/health", "/docs", "/openapi.json", "/redoc", "/swagger", "/favicon"]
    if not path.startswith("/api/"):
        return await call_next(request)
    for prefix in public_prefixes:
        if path.startswith(prefix):
            return await call_next(request)
    
    # Check Authorization header
    auth_header = request.headers.get("Authorization", "")

    # API key authentication: if the header starts with "esam_", validate as API key
    if auth_header.startswith("esam_"):
        key_data = validate_api_key(auth_header)
        if key_data:
            request.state.current_user = {
                "id": key_data["id"],
                "name": key_data["name"],
                "email": "",
                "is_active": True,
            }
            request.state.current_entity_id = key_data.get("entity_id", "")
            request.state.actor_type = "api_key"
            response = await call_next(request)
            return response
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or expired API key"}
        )

    # Standard Bearer JWT authentication
    if not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={"detail": "Not authenticated"}
        )
    token = auth_header[7:]
    payload = decode_access_token(token)
    if payload is None:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or expired token"}
        )
    user_id = payload.get("sub")
    if user_id is None:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid token payload"}
        )
    user = get_user_by_id(user_id)
    if user is None or not user["is_active"]:
        return JSONResponse(
            status_code=401,
            content={"detail": "User not found or inactive"}
        )
    # Attach user to request state
    request.state.current_user = user
    request.state.current_entity_id = payload.get("entity_id")
    
    response = await call_next(request)
    return response


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Log each request with correlation ID and timing."""
    from logging_config import set_request_id, clear_request_id
    import time

    set_request_id()
    start = time.time()

    response = await call_next(request)

    duration_ms = int((time.time() - start) * 1000)
    req_logger = logging.getLogger("esam.request")
    req_logger.info(
        "method=%s path=%s status=%d duration=%dms",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        extra={
            "extra_fields": {
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "ip": request.client.host if request.client else "",
            }
        },
    )

    clear_request_id()
    return response


# ── Startup ─────────────────────────────────────────────────────────

@app.on_event("startup")
def startup():
    init_db()
    # Run schema migrations (version-tracked, additive to existing ensure_schema calls)
    from migration import ensure_meta_schema, run_pending_migrations
    ensure_meta_schema()
    run_pending_migrations()
    create_users_table()
    create_api_keys_table()
    ensure_schema()
    from multi_entity import EntityManager
    EntityManager().ensure_tables()
    # Register local Hermes if not already done
    registry = AgentRegistry()
    existing = registry.list()
    if not existing:
        registry.register_hermes_profiles()
        logger.info("Registered Hermes profiles as agents")
    logger.info("ES Agent Management API server started")


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


# ── Auth Routes ──────────────────────────────────────────────────────

@app.post("/api/auth/register")
def register(data: dict):
    """Register a new user account."""
    email = data.get("email", "").strip().lower()
    name = data.get("name", "").strip()
    password = data.get("password", "")
    if not email or not name or not password:
        raise HTTPException(400, "email, name, and password are required")
    if len(password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    try:
        user = create_user(email, name, password)
        token = create_access_token({"sub": user["id"]})
        record_event(
            actor_id=user["id"], actor_type="user",
            action="register", resource_type="user",
            resource_id=user["id"], entity_id=user["id"],
        )
        return {"user": user, "token": token, "token_type": "bearer"}
    except ValueError as e:
        raise HTTPException(409, str(e))

@app.post("/api/auth/login")
def login(request: Request, data: dict):
    """Authenticate and return JWT token."""
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    user = authenticate_user(email, password)
    if not user:
        raise HTTPException(401, "Invalid email or password")
    entities = list_user_entities(user["id"])
    token = create_access_token({"sub": user["id"]})
    record_event(
        actor_id=user["id"], actor_type="user",
        action="login", resource_type="user",
        resource_id=user["id"], entity_id=user["id"],
        ip_address=request.client.host if request.client else "",
        user_agent=request.headers.get("User-Agent", ""),
    )
    return {
        "user": {"id": user["id"], "email": user["email"], "name": user["name"]},
        "token": token,
        "token_type": "bearer",
        "entities": entities,
    }

@app.get("/api/auth/me")
def get_me():
    """Return current user profile."""
    entities = list_user_entities(current_user["id"])
    return {
        "user": {"id": current_user["id"], "email": current_user["email"], "name": current_user["name"]},
        "entities": entities,
    }

@app.get("/api/auth/entities")
def get_my_entities():
    """Return entities the current user has access to."""
    entities = list_user_entities(current_user["id"])
    return {"entities": entities}


# ── API Key Management ──────────────────────────────────────────


@app.post("/api/auth/keys")
def create_api_key_endpoint(data: dict):
    """Create a new API key.

    Body: {name: "my-key", scopes: ["run"], expires_at: "2026-12-31"}
    Returns the full key ONCE — store it securely.
    """
    name = data.get("name", "")
    if not name:
        raise HTTPException(400, "name is required")
    scopes = data.get("scopes", ["run"])
    expires = data.get("expires_at")
    result = create_api_key(name, entity_id="", scopes=scopes, expires_at=expires)
    return result


@app.get("/api/auth/keys")
def list_api_keys_endpoint():
    """List all API keys (prefixes only, no full keys)."""
    return {"keys": list_api_keys()}


@app.delete("/api/auth/keys/{key_id}")
def delete_api_key_endpoint(key_id: str):
    """Revoke an API key."""
    revoked = revoke_api_key(key_id)
    if not revoked:
        raise HTTPException(404, "API key not found or already revoked")
    return {"revoked": True}


# ── Dashboard ───────────────────────────────────────────────────────

@app.get("/")
def root():
    """Serve the Current dashboard SPA."""
    html_path = SRC / ".." / "current" / "dashboard.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return {"status": "ok", "message": "ES Agent Management API"}


@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "ok", "version": "0.1.0"}


# ── Observability Routes ──────────────────────────────────────────


@app.get("/api/health/liveness")
def api_health_liveness():
    """Lightweight liveness probe."""
    from observability.health import HealthChecker
    hc = HealthChecker()
    return hc.check_liveness()


@app.get("/api/health/readiness")
def api_health_readiness():
    """Readiness probe with dependency checks."""
    from observability.health import HealthChecker
    hc = HealthChecker()
    return hc.check_readiness()


@app.get("/api/health/diagnostics")
def api_health_diagnostics():
    """Full diagnostic report."""
    from observability.health import HealthChecker
    hc = HealthChecker()
    return {"diagnostics": hc.run_diagnostics()}


@app.get("/api/metrics")
def api_metrics_prometheus():
    """Prometheus-formatted metrics."""
    from observability.metrics import MetricsCollector
    from fastapi.responses import PlainTextResponse
    m = MetricsCollector()
    return PlainTextResponse(m.export_prometheus(), media_type="text/plain")


@app.get("/api/metrics/steps")
def api_metrics_steps():
    """Step-level metrics."""
    from observability.metrics import MetricsCollector
    m = MetricsCollector()
    step_types = []
    with m._lock:
        for step_type in m._step_durations:
            step_types.append({
                "step_type": step_type,
                "p50_ms": m.get_step_latency_p50(step_type),
                "p95_ms": m.get_step_latency_p95(step_type),
                "error_rate": m.get_step_error_rate(step_type),
                "total_executions": m._total_counts.get(step_type, 0),
                "total_errors": m._error_counts.get(step_type, 0),
            })
    return {"steps": step_types, "count": len(step_types)}


@app.get("/api/metrics/tokens")
def api_metrics_tokens():
    """Token usage metrics."""
    from observability.metrics import MetricsCollector
    m = MetricsCollector()
    models = []
    with m._lock:
        for model, events in m._token_counts.items():
            total_in = sum(e[1] for e in events)
            total_out = sum(e[2] for e in events)
            models.append({
                "model": model,
                "total_input_tokens": total_in,
                "total_output_tokens": total_out,
                "total_tokens": total_in + total_out,
                "call_count": len(events),
            })
    return {"models": models, "count": len(models)}


@app.get("/designer")
def workflow_designer():
    """Serve the workflow designer SPA."""
    html_path = SRC / ".." / "current" / "workflow-designer.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return {"error": "Workflow designer not found"}


@app.get("/eval")
def evaluation_dashboard():
    """Serve the evaluation results dashboard SPA."""
    html_path = SRC / ".." / "current" / "eval.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return {"error": "Evaluation dashboard not found"}


@app.get("/schedules")
def schedules_dashboard():
    """Serve the schedules / visual cron editor SPA."""
    html_path = SRC / ".." / "current" / "schedules.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return {"error": "Schedules dashboard not found"}


@app.get("/api/summary")
def get_summary():
    """Get the dashboard summary (all pillars rolled up)."""
    conn = get_connection()
    
    # Dashboard summary view
    ds = conn.execute("SELECT * FROM dashboard_summary").fetchone()
    summary = dict(ds) if ds else {}

    # Pillar coverage
    pillars = {
        "agent_registry": {
            "status": "implemented",
            "agents": summary.get("agents_total", 0),
            "running": summary.get("agents_running", 0),
        },
        "observability": {
            "status": "implemented",
            "workflows": summary.get("workflows_active", 0),
            "active_runs": summary.get("runs_active", 0),
        },
        "audit_trail": {
            "status": "implemented",
            "entries_24h": summary.get("audit_entries_24h", 0),
            "chain_integrity": True,
        },
        "policy_engine": {
            "status": "implemented",
            "evaluations": conn.execute("SELECT COUNT(*) FROM policy_evaluations").fetchone()[0],
        },
        "conversations": {
            "status": "implemented",
            "total": conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0],
        },
        "cost_attribution": {
            "status": "implemented",
            "cost_7d": summary.get("cost_7d", 0),
            "cost_total": summary.get("cost_total", 0),
        },
    }

    return {
        "summary": summary,
        "pillars": pillars,
        "alerts_24h": summary.get("alerts_24h", 0),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Agents ──────────────────────────────────────────────────────────

@app.get("/api/agents")
def list_agents(status: str | None = None):
    """List registered agents."""
    return AgentRegistry().list(status=status)


@app.get("/api/agents/{agent_id}")
def get_agent(agent_id: str):
    """Get a single agent."""
    agent = AgentRegistry().get(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} not found")
    return agent


@app.post("/api/agents/{agent_id}/heartbeat")
def agent_heartbeat(agent_id: str, data: dict):
    """Record an agent heartbeat."""
    return AgentRegistry().heartbeat(agent_id, **data)


# ── Audit Trail ─────────────────────────────────────────────────────

@app.get("/api/audit")
def query_audit(
    agent_id: str | None = None,
    category: str | None = None,
    limit: int = Query(50, le=500),
):
    """Query audit trail entries."""
    return AuditTrail().query(agent_id=agent_id, category=category, limit=limit)


@app.get("/api/audit/verify")
def verify_audit_chain():
    """Verify audit hash chain integrity."""
    return AuditTrail().verify_chain()


@app.get("/api/audit/export/{framework}")
def export_compliance(framework: str = "all"):
    """Export audit data mapped to compliance frameworks."""
    valid = {"all", "eu_ai_act", "nist_ai_rmf", "iso_42001"}
    if framework not in valid:
        raise HTTPException(400, f"Framework must be one of: {', '.join(sorted(valid))}")
    return AuditTrail().export_compliance(framework)


@app.get("/api/audit/platform")
def query_platform_audit(
    actor: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    action: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
):
    """Query platform audit log (user/admin actions for compliance)."""
    events = query_events(
        actor_id=actor,
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        offset=offset,
    )
    return {"events": events, "count": len(events), "limit": limit, "offset": offset}


# ── Policies ────────────────────────────────────────────────────────

@app.get("/api/policies")
def list_policies(agent_id: str | None = None):
    """List all policies."""
    return PolicyEngine().list_policies(agent_id=agent_id)


@app.post("/api/policies/evaluate")
def evaluate_policy(data: dict):
    """Evaluate a resource against policies."""
    resource = data.get("resource", "")
    agent_id = data.get("agent_id", "")
    context = data.get("context", {})
    if not resource:
        raise HTTPException(400, "resource is required")
    return PolicyEngine().evaluate(resource, agent_id=agent_id, context=context)


# ── Workflows (Tether) ──────────────────────────────────────────────

@app.get("/api/workflows")
def list_workflows():
    """List all workflows."""
    conn = get_connection()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM workflows ORDER BY created_at DESC"
    ).fetchall()]


@app.post("/api/tether/ingest")
def tether_ingest(data: dict):
    """Ingest debtors for the Tether collections workflow."""
    engine = TetherEngine("api-biz", data.get("business_name", "Demo Business"))
    csv_content = data.get("csv", "")
    if not csv_content:
        raise HTTPException(400, "csv field is required")
    debtors = engine.ingest_csv(csv_content)
    return {"ingested": len(debtors), "debtors": [d.id for d in debtors]}


@app.post("/api/tether/process")
def tether_process(data: dict):
    """Run the escalation engine on all debtors."""
    engine = TetherEngine("api-biz", "Demo")
    result = engine.process_all()
    return result


# ── Email Renderer ────────────────────────────────────────────────────


def _get_renderer():
    """Lazy-import the EmailRenderer to avoid startup dependency issues."""
    from renderer.email_engine import EmailRenderer
    return EmailRenderer()


@app.post("/api/renderer/render-email")
def render_email(data: dict):
    """Render markdown to HTML email."""
    body_markdown = data.get("body_markdown", "")
    template_name = data.get("template_name", "daily-signal")
    dark_mode = data.get("dark_mode", False)

    if not body_markdown:
        raise HTTPException(400, "body_markdown is required")

    try:
        renderer = _get_renderer()
        result = renderer.render(body_markdown, template_name=template_name, dark_mode=dark_mode)
        return result
    except Exception as e:
        raise HTTPException(500, f"Render failed: {e}")


@app.post("/api/renderer/preview")
def preview_render(data: dict):
    """Preview email rendering (same as render-email but returns only HTML)."""
    body_markdown = data.get("body_markdown", "")
    template_name = data.get("template_name", "daily-signal")
    dark_mode = data.get("dark_mode", False)

    if not body_markdown:
        raise HTTPException(400, "body_markdown is required")

    try:
        renderer = _get_renderer()
        result = renderer.render(body_markdown, template_name=template_name, dark_mode=dark_mode)
        return {"html": result["html"], "subject": result["subject"]}
    except Exception as e:
        raise HTTPException(500, f"Preview failed: {e}")


@app.get("/api/renderer/templates")
def list_renderer_templates():
    """List available email templates."""
    from renderer.section_templates import SECTION_TEMPLATES
    templates = []
    for key, label in SECTION_TEMPLATES.items():
        templates.append({
            "id": key,
            "name": key.replace("-", " ").title(),
            "description": f"Section template for {label}",
        })
    # Add the main template
    templates.insert(0, {
        "id": "daily-signal",
        "name": "Daily Signal",
        "description": "Full newsletter with Evolving Software branding",
    })
    return {"templates": templates}


# ── Portal (Debtor Self-Service) ─────────────────────────────────────

PORTAL_HTML = None  # Lazy-loaded from current/portal.html


def _get_portal_html() -> str:
    global PORTAL_HTML
    if PORTAL_HTML is None:
        html_path = SRC / ".." / "current" / "portal.html"
        if html_path.exists():
            PORTAL_HTML = html_path.read_text()
        else:
            PORTAL_HTML = "<html><body><h1>Portal not found</h1></body></html>"
    return PORTAL_HTML


@app.get("/portal/{raw_token}")
def portal_view(raw_token: str):
    """Serve the debtor self-service portal SPA. Token-authenticated."""
    from portal_api import verify_portal_token, get_debtor_dashboard
    debtor_id = verify_portal_token(raw_token)
    if not debtor_id:
        return HTMLResponse(
            "<html><body style='background:#0d0d0d;color:#e0e0e0;font-family:sans-serif;padding:40px;text-align:center;'>"
            "<h1>Invalid or Expired Link</h1>"
            "<p>This portal link is no longer valid. Please contact the business for assistance.</p>"
            "</body></html>",
            status_code=404,
        )
    html = _get_portal_html()
    # Inject token into HTML for JavaScript
    html = html.replace("<!-- PORTAL_TOKEN -->", raw_token)
    return HTMLResponse(html)


@app.get("/api/portal/{raw_token}/dashboard")
def portal_dashboard(raw_token: str):
    """Get the debtor's dashboard data (token-authenticated)."""
    from portal_api import verify_portal_token, get_debtor_dashboard
    debtor_id = verify_portal_token(raw_token)
    if not debtor_id:
        raise HTTPException(401, "Invalid or expired portal token")
    data = get_debtor_dashboard(debtor_id)
    if "error" in data:
        raise HTTPException(404, data["error"])
    return data


@app.post("/api/portal/{raw_token}/dispute")
def portal_file_dispute(raw_token: str, data: dict):
    """File a dispute via the self-service portal."""
    from portal_api import verify_portal_token, file_dispute
    debtor_id = verify_portal_token(raw_token)
    if not debtor_id:
        raise HTTPException(401, "Invalid or expired portal token")
    reason = data.get("reason", "")
    if not reason:
        raise HTTPException(400, "Dispute reason is required")
    return file_dispute(debtor_id, reason)


@app.post("/api/portal/{raw_token}/message")
def portal_send_message(raw_token: str, data: dict):
    """Send a message via the self-service portal."""
    from portal_api import verify_portal_token, send_message
    debtor_id = verify_portal_token(raw_token)
    if not debtor_id:
        raise HTTPException(401, "Invalid or expired portal token")
    subject = data.get("subject", "")
    body = data.get("body", "")
    msg_type = data.get("message_type", "general")
    if not body:
        raise HTTPException(400, "Message body is required")
    return send_message(debtor_id, subject, body, msg_type)


@app.get("/api/portal/{raw_token}/messages")
def portal_get_messages(raw_token: str):
    """Get messages for the debtor."""
    from portal_api import verify_portal_token, get_messages
    debtor_id = verify_portal_token(raw_token)
    if not debtor_id:
        raise HTTPException(401, "Invalid or expired portal token")
    return get_messages(debtor_id)


@app.get("/api/portal/{raw_token}/payment-plans")
def portal_get_payment_plans(raw_token: str):
    """Get proposed payment plan options for the debtor."""
    from portal_api import verify_portal_token, propose_payment_plans
    debtor_id = verify_portal_token(raw_token)
    if not debtor_id:
        raise HTTPException(401, "Invalid or expired portal token")
    plans = propose_payment_plans(debtor_id)
    return {"plans": plans}


@app.post("/api/portal/{raw_token}/payment-plans/accept")
def portal_accept_payment_plan(raw_token: str, data: dict):
    """Debtor accepts a payment plan."""
    from portal_api import verify_portal_token, accept_payment_plan
    debtor_id = verify_portal_token(raw_token)
    if not debtor_id:
        raise HTTPException(401, "Invalid or expired portal token")
    instalments = data.get("instalments", 3)
    result = accept_payment_plan(debtor_id, instalments)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.get("/api/portal/{raw_token}/active-plan")
def portal_get_active_plan(raw_token: str):
    """Get the debtor's active payment plan."""
    from portal_api import verify_portal_token, get_active_payment_plan
    debtor_id = verify_portal_token(raw_token)
    if not debtor_id:
        raise HTTPException(401, "Invalid or expired portal token")
    plan = get_active_payment_plan(debtor_id)
    if not plan:
        return {"plan": None}
    return {"plan": plan}


# ── Late Fee Automation ──────────────────────────────────────────────

_late_fee_engine: LateFeeEngine | None = None


def _get_lfee() -> LateFeeEngine:
    global _late_fee_engine
    if _late_fee_engine is None:
        _late_fee_engine = LateFeeEngine("biz-001")
    return _late_fee_engine


@app.get("/api/late-fees/rules")
def list_late_fee_rules():
    """List all late fee rules."""
    return _get_lfee().list_rules(active_only=False)


@app.post("/api/late-fees/rules")
def create_late_fee_rule(data: dict):
    """Create a new late fee rule."""
    return _get_lfee().create_rule(**data)


@app.get("/api/late-fees/rules/{rule_id}")
def get_late_fee_rule(rule_id: str):
    """Get a single late fee rule."""
    rule = _get_lfee().get_rule(rule_id)
    if not rule:
        raise HTTPException(404, f"Late fee rule {rule_id} not found")
    return rule


@app.put("/api/late-fees/rules/{rule_id}")
def update_late_fee_rule(rule_id: str, data: dict):
    """Update a late fee rule."""
    # Verify exists
    existing = _get_lfee().get_rule(rule_id)
    if not existing:
        raise HTTPException(404, f"Late fee rule {rule_id} not found")
    return _get_lfee().update_rule(rule_id, **data)


@app.delete("/api/late-fees/rules/{rule_id}")
def delete_late_fee_rule(rule_id: str):
    """Delete a late fee rule."""
    existing = _get_lfee().get_rule(rule_id)
    if not existing:
        raise HTTPException(404, f"Late fee rule {rule_id} not found")
    _get_lfee().delete_rule(rule_id)
    return {"status": "deleted", "rule_id": rule_id}


@app.post("/api/late-fees/defaults")
def create_default_rules():
    """Create ACCC-compliant default late fee rules."""
    return _get_lfee().create_default_rules()


@app.post("/api/late-fees/assess")
def assess_late_fees(data: dict):
    """Assess late fees for a debtor."""
    debtor_id = data.get("debtor_id", "")
    debtor_name = data.get("debtor_name", "")
    invoice_number = data.get("invoice_number", "")
    amount_cents = data.get("amount_cents", 0)
    days_overdue = data.get("days_overdue", 0)
    rule_id = data.get("rule_id", None)

    if not debtor_id or not amount_cents:
        raise HTTPException(400, "debtor_id and amount_cents are required")

    result = _get_lfee().assess_fees(
        debtor_id=debtor_id,
        debtor_name=debtor_name,
        invoice_number=invoice_number,
        amount_cents=amount_cents,
        days_overdue=days_overdue,
        rule_id=rule_id,
    )
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.get("/api/late-fees/assessments")
def list_fee_assessments(
    debtor_id: str | None = None,
    status: str | None = None,
    limit: int = Query(50, le=500),
):
    """List fee assessments."""
    return _get_lfee().get_assessments(debtor_id=debtor_id, status=status, limit=limit)


@app.get("/api/late-fees/assessments/{assessment_id}")
def get_fee_assessment(assessment_id: str):
    """Get a single fee assessment."""
    assessment = _get_lfee().get_assessment(assessment_id)
    if not assessment:
        raise HTTPException(404, f"Fee assessment {assessment_id} not found")
    return assessment


@app.post("/api/late-fees/assessments/{assessment_id}/waive")
def waive_fee_assessment(assessment_id: str, data: dict):
    """Waive a fee assessment."""
    reason = data.get("reason", "")
    return _get_lfee().waive_fee(assessment_id, reason)


@app.post("/api/late-fees/assessments/{assessment_id}/pay")
def mark_fee_paid(assessment_id: str, data: dict):
    """Mark a fee assessment as paid."""
    amount_cents = data.get("amount_cents", 0)
    return _get_lfee().mark_fee_paid(assessment_id, amount_cents)


@app.get("/api/late-fees/revenue")
def get_fee_revenue():
    """Get aggregated fee revenue statistics."""
    return _get_lfee().get_revenue()


@app.post("/api/late-fees/assessments/{assessment_id}/notice")
def generate_fee_notice(assessment_id: str):
    """Generate a fee notice text for an assessment."""
    result = _get_lfee().generate_fee_notice(assessment_id)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@app.post("/api/late-fees/batch-assess")
def batch_assess_fees(data: dict):
    """Assess late fees for multiple debtors at once."""
    debtors = data.get("debtors", [])
    if not debtors:
        raise HTTPException(400, "debtors list is required")

    engine = _get_lfee()
    results = []
    for d in debtors:
        result = engine.assess_fees(
            debtor_id=d.get("debtor_id", ""),
            debtor_name=d.get("debtor_name", ""),
            invoice_number=d.get("invoice_number", ""),
            amount_cents=d.get("amount_cents", 0),
            days_overdue=d.get("days_overdue", 0),
            rule_id=d.get("rule_id", None),
        )
        results.append(result)

    return {"results": results, "total": len(results), "fees_applied": sum(1 for r in results if r.get("action") == "fee_applied")}


# ── Reply Handling ──────────────────────────────────────────────────

_reply_handler: ReplyPipeline | None = None


def _get_rhandler() -> ReplyPipeline:
    global _reply_handler
    if _reply_handler is None:
        _reply_handler = ReplyPipeline()
    return _reply_handler


@app.post("/api/tether/reply/classify")
def classify_debtor_reply(data: dict):
    """Submit an incoming debtor email reply for AI classification and auto-action.

    Body: {
        \"debtor_id\": \"d-001\",
        \"subject\": \"Re: Payment reminder\",
        \"body\": \"I dispute this invoice...\",
        \"email_from\": \"debtor@example.com\"
    }

    Returns the classification result and action taken.
    """
    debtor_id = data.get("debtor_id", "")
    subject = data.get("subject", "")
    body = data.get("body", "")
    email_from = data.get("email_from", "")

    if not debtor_id:
        raise HTTPException(400, "debtor_id is required")
    if not body:
        raise HTTPException(400, "body is required")

    # Verify debtor exists
    conn = get_connection()
    existing = conn.execute("SELECT id FROM debtors WHERE id = ?", (debtor_id,)).fetchone()
    if not existing:
        raise HTTPException(404, f"Debtor {debtor_id} not found")

    handler = _get_rhandler()
    result = handler.ingest_reply(
        debtor_id=debtor_id,
        subject=subject,
        body=body,
        email_from=email_from,
    )

    # Log to audit trail
    try:
        from audit_trail import AuditTrail
        AuditTrail().log_tool_call(
            agent_id="tether-collections",
            tool_name="reply_pipeline::classify",
            tool_input={
                "debtor_id": debtor_id,
                "subject": subject[:100],
            },
            tool_output=result,
            reasoning=f"Reply classification: {result['category']} ({int(result['confidence']*100)}%) → {result['action']}",
            workflow_id="wf-tether-replies",
        )
    except Exception:
        pass  # Audit logging is best-effort

    return result


@app.get("/api/tether/replies")
def list_replies(
    debtor_id: str | None = None,
    category: str | None = None,
    resolution: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """List classified debtor replies with optional filters."""
    handler = _get_rhandler()
    return handler.get_replies(
        debtor_id=debtor_id,
        category=category,
        resolution=resolution,
        limit=limit,
        offset=offset,
    )


@app.get("/api/tether/replies/pending")
def list_pending_replies():
    """Get all pending (unresolved) replies ordered by priority.

    Disputes come first, then queries, then others.
    Includes debtor name and invoice info via JOIN.
    """
    handler = _get_rhandler()
    return handler.get_pending_actions()


@app.get("/api/tether/replies/summary")
def reply_handler_summary():
    """Get summary statistics about all classified replies."""
    handler = _get_rhandler()
    return handler.get_summary()


@app.post("/api/tether/replies/{reply_id}/resolve")
def resolve_reply(reply_id: str, data: dict):
    """Mark a classified reply as resolved.

    Body: {
        \"resolution\": \"acknowledged|responded|resolved|ignored\",
        \"resolved_by\": \"agent_id or human name\" (optional)
    }
    """
    resolution = data.get("resolution", "")
    if resolution not in ("acknowledged", "responded", "resolved", "ignored"):
        raise HTTPException(400, "resolution must be: acknowledged, responded, resolved, or ignored")

    resolved_by = data.get("resolved_by", "")
    handler = _get_rhandler()
    success = handler.resolve_reply(reply_id, resolution, resolved_by)
    if not success:
        raise HTTPException(404, f"Reply {reply_id} not found")
    return {"status": "ok", "reply_id": reply_id, "resolution": resolution}


# ── Health ──────────────────────────────────────────────────────────

@app.get("/api/accounting/connections")
def list_accounting_connections(business_id: str | None = None):
    """List all active accounting connections."""
    from integrations.sync import SyncEngine
    conn = get_connection()
    engine = SyncEngine()
    return engine.get_connections(conn, business_id=business_id or "")


@app.post("/api/accounting/connections")
def add_accounting_connection(data: dict):
    """Add a new accounting connection (manual token entry).

    Required fields: platform, access_token, tenant_id
    Optional fields: tenant_name, business_id, refresh_token, expires_at
    """
    from integrations.sync import SyncEngine
    from integrations import AccountingConnection, utc_now, new_id

    platform = data.get("platform", "")
    if platform not in ("xero", "quickbooks", "myob"):
        raise HTTPException(400, f"Unsupported platform: {platform}. Choose: xero, quickbooks, myob")

    conn = get_connection()
    engine = SyncEngine()

    connection = AccountingConnection(
        id=new_id(f"{platform[:3]}-"),
        business_id=data.get("business_id", "biz-001"),
        platform=platform,
        access_token=data.get("access_token", ""),
        refresh_token=data.get("refresh_token", ""),
        tenant_id=data.get("tenant_id", ""),
        tenant_name=data.get("tenant_name", ""),
        scope=data.get("scope", ""),
        expires_at=data.get("expires_at", "9999-12-31T23:59:59Z"),
        is_active=True,
        created_at=utc_now(),
        updated_at=utc_now(),
    )

    engine.save_connection(conn, connection)

    # Trigger initial sync
    result = engine.sync_platform(conn, connection)

    return {
        "connection": connection.to_dict(),
        "initial_sync": result.to_dict(),
    }


@app.delete("/api/accounting/connections/{connection_id}")
def remove_accounting_connection(connection_id: str):
    """Soft-delete an accounting connection."""
    from integrations.sync import SyncEngine
    conn = get_connection()
    SyncEngine().delete_connection(conn, connection_id)
    return {"status": "deleted", "id": connection_id}


@app.post("/api/accounting/sync")
def trigger_accounting_sync(data: dict):
    """Trigger a sync of all (or specific) accounting connections."""
    from integrations.sync import SyncEngine
    conn = get_connection()
    engine = SyncEngine()
    business_id = data.get("business_id", "")
    results = engine.sync_all(conn, business_id=business_id)
    return {
        "results": [r.to_dict() for r in results],
        "total": len(results),
        "successful": sum(1 for r in results if r.success),
    }


@app.post("/api/accounting/sync/{connection_id}")
def trigger_single_sync(connection_id: str):
    """Trigger a sync for a specific connection."""
    from integrations.sync import SyncEngine
    from integrations import AccountingConnection
    conn = get_connection()
    engine = SyncEngine()

    row = conn.execute(
        "SELECT * FROM accounting_connections WHERE id = ? AND is_active = 1",
        (connection_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Connection {connection_id} not found")

    connection = AccountingConnection().from_dict(dict(row))
    result = engine.sync_platform(conn, connection)
    return result.to_dict()


@app.get("/api/accounting/sync-log")
def get_sync_log(platform: str | None = None, limit: int = Query(50, le=200)):
    """Get the sync activity log."""
    conn = get_connection()
    query = "SELECT * FROM sync_log"
    params = []
    if platform:
        query += " WHERE platform = ?"
        params.append(platform)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(query, params).fetchall()]


@app.get("/api/accounting/platforms")
def list_accounting_platforms():
    """Get supported platforms and their config status."""
    from integrations import get_config, PLATFORMS, SUPPORTED_PLATFORMS
    config = get_config()

    platforms = []
    for key in SUPPORTED_PLATFORMS:
        cfg = config.get(key, {})
        configured = bool(cfg.get("client_id")) and bool(cfg.get("client_secret"))
        platforms.append({
            "id": key,
            "name": PLATFORMS[key],
            "configured": configured,
            "has_client_id": bool(cfg.get("client_id")),
        })

    conn = get_connection()
    connections = conn.execute(
        "SELECT platform, COUNT(*) as count FROM accounting_connections WHERE is_active = 1 GROUP BY platform"
    ).fetchall()
    conn_counts = {r["platform"]: r["count"] for r in connections}

    for p in platforms:
        p["active_connections"] = conn_counts.get(p["id"], 0)

    return {
        "platforms": platforms,
        "redirect_uri": config.get("redirect_uri", ""),
    }


@app.post("/api/accounting/oauth-url/{platform}")
def get_oauth_url(platform: str, data: dict):
    """Get the OAuth2 authorization URL for a platform.

    Returns the URL the user should visit in their browser to authorize
    the Tether connection.
    """
    from integrations.sync import get_client
    from integrations import get_config, new_id

    if platform not in ("xero", "quickbooks", "myob"):
        raise HTTPException(400, f"Unsupported platform: {platform}")

    config = get_config()
    client = get_client(platform)
    redirect_uri = data.get("redirect_uri", config.get("redirect_uri", ""))
    state = new_id("oauth-")

    try:
        auth_url = client.get_authorization_url(redirect_uri, state)
        return {
            "url": auth_url,
            "state": state,
            "redirect_uri": redirect_uri,
            "platform": platform,
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to generate OAuth URL: {e}")


@app.post("/api/accounting/callback/{platform}")
def handle_oauth_callback(platform: str, data: dict):
    """Handle OAuth2 callback (exchange code for tokens).

    Called by the user after they authorize via the OAuth URL.
    The user pastes the callback URL or code here.
    """
    from integrations.sync import get_client, SyncEngine
    from integrations import get_config

    if platform not in ("xero", "quickbooks", "myob"):
        raise HTTPException(400, f"Unsupported platform: {platform}")

    code = data.get("code", "")
    redirect_uri = data.get("redirect_uri", get_config().get("redirect_uri", ""))
    business_id = data.get("business_id", "biz-001")

    if not code:
        raise HTTPException(400, "Authorization code is required")

    try:
        client = get_client(platform)
        connection = client.exchange_code(code, redirect_uri)
        connection.business_id = business_id

        # Save to database
        db = get_connection()
        engine = SyncEngine()
        engine.save_connection(db, connection)

        # Trigger initial sync
        result = engine.sync_platform(db, connection)

        return {
            "connection": connection.to_dict(),
            "initial_sync": result.to_dict(),
        }
    except Exception as e:
        raise HTTPException(500, f"OAuth callback failed: {e}")


@app.post("/api/accounting/push-payment")
def push_payment_to_accounting(data: dict):
    """Push a payment back to the accounting platform.

    Called after a debtor pays — this creates the payment record
    in Xero/QuickBooks/MYOB.
    """
    from integrations.sync import SyncEngine

    debtor_id = data.get("debtor_id", "")
    amount_cents = data.get("amount_cents", 0)
    paid_at = data.get("paid_at", "")

    if not debtor_id or not amount_cents:
        raise HTTPException(400, "debtor_id and amount_cents are required")

    conn = get_connection()
    engine = SyncEngine()
    success = engine.push_payment(conn, debtor_id, amount_cents, paid_at)

    return {
        "success": success,
        "debtor_id": debtor_id,
        "amount_cents": amount_cents,
    }


@app.get("/api/accounting/summary")
def get_accounting_summary():
    """Get accounting integration summary."""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM accounting_summary").fetchall()
    return [dict(r) for r in rows]


# ── BPAY Payment Endpoints ────────────────────────────────────────────

from bpay_engine import BPAYEngine

_bpay_engine: BPAYEngine | None = None


def _get_bpay() -> BPAYEngine:
    global _bpay_engine
    if _bpay_engine is None:
        _bpay_engine = BPAYEngine()
    return _bpay_engine


@app.get("/api/bpay/info/{debtor_id}")
def get_bpay_info(debtor_id: str):
    """Get BPAY payment information for a debtor.

    Returns Biller Code, CRN, and formatted payment instructions
    for the debtor to pay via their Australian banking app.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT id, name, invoice_number, amount_cents, "
        "business_id FROM debtors WHERE id = ?",
        (debtor_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Debtor {debtor_id} not found")

    debtor = dict(row)
    bpay = _get_bpay()
    business_name = conn.execute(
        "SELECT name FROM agents WHERE id = 'tether-collections'"
    ).fetchone()
    biz_name = business_name["name"] if business_name else "Demo Business"

    info = bpay.generate_payment_info(
        business_id=debtor.get("business_id", "biz-001"),
        business_name=biz_name,
        debtor_id=debtor["id"],
        debtor_name=debtor["name"],
        invoice_number=debtor["invoice_number"],
        amount_cents=debtor["amount_cents"],
    )

    return {
        "biller_code": info.biller_code,
        "crn": info.crn,
        "reference": info.reference,
        "amount_dollars": info.amount_dollars,
        "amount_cents": info.amount_cents,
        "payment_instructions": info.payment_instructions,
        "generated_at": info.generated_at,
    }


@app.post("/api/bpay/initiate")
def initiate_bpay_payment(data: dict):
    """Record that a BPAY payment has been initiated (CRN provided to debtor).

    This does NOT take a real payment — BPAY is processed through the
    debtor's banking app. This endpoint tracks the initiation for
    reconciliation purposes.
    """
    debtor_id = data.get("debtor_id", "")
    conn = get_connection()
    row = conn.execute(
        "SELECT id, name, invoice_number, amount_cents, "
        "bpay_biller_code, bpay_crn FROM debtors WHERE id = ?",
        (debtor_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Debtor {debtor_id} not found")

    d = dict(row)
    bpay = _get_bpay()
    payment = bpay.record_initiation(
        debtor_id=d["id"],
        debtor_name=d["name"],
        invoice_number=d["invoice_number"],
        amount_cents=d["amount_cents"],
        biller_code=d["bpay_biller_code"] or "",
        crn=d["bpay_crn"] or "",
    )

    # Record in database
    import uuid
    pid = f"bpay-{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO bpay_payments (id, debtor_id, business_id, biller_code, crn, "
        "reference, amount_cents, status, initiated_at, expected_settlement) "
        "VALUES (?, ?, 'biz-001', ?, ?, ?, ?, 'pending', datetime('now'), datetime('now', '+2 days'))",
        (pid, debtor_id, payment.biller_code, payment.crn,
         d["invoice_number"], d["amount_cents"])
    )
    conn.commit()

    return {
        "status": "initiated",
        "payment_id": pid,
        "biller_code": payment.biller_code,
        "crn": payment.crn,
        "amount_dollars": f"${d['amount_cents'] / 100:,.2f}",
        "expected_settlement": "2 business days",
    }


@app.get("/api/bpay/pending")
def list_pending_bpay():
    """List all pending BPAY payments awaiting settlement."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM bpay_payments WHERE status IN ('pending', 'processing') "
        "ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/bpay/settled")
def list_settled_bpay():
    """List all settled BPAY payments."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM bpay_payments WHERE status = 'cleared' "
        "ORDER BY settled_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/bpay/confirm/{payment_id}")
def confirm_bpay_payment(payment_id: str):
    """Confirm a BPAY payment as settled (simulates bank notification).

    In production, this would be a webhook from the bank's payment gateway.
    For demo purposes, it manually confirms settlement.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM bpay_payments WHERE id = ?", (payment_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, f"BPAY payment {payment_id} not found")

    p = dict(row)
    conn.execute(
        "UPDATE bpay_payments SET status = 'cleared', settled_at = datetime('now'), "
        "amount_paid_cents = ?, updated_at = datetime('now') WHERE id = ?",
        (p["amount_cents"], payment_id)
    )

    # Also update the debtor record
    conn.execute(
        "UPDATE debtors SET state = 'paid', paid_at = datetime('now'), "
        "paid_amount_cents = ?, bpay_paid_at = datetime('now'), "
        "bpay_paid_cents = ? WHERE id = ?",
        (p["amount_cents"], p["amount_cents"], p["debtor_id"])
    )
    conn.commit()

    return {
        "status": "confirmed",
        "payment_id": payment_id,
        "debtor_id": p["debtor_id"],
        "amount_dollars": f"${p['amount_cents'] / 100:,.2f}",
        "settled_at": "now",
    }


@app.get("/api/bpay/summary")
def get_bpay_summary():
    """Get BPAY payment summary statistics."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM bpay_summary").fetchone()
    summary = dict(row) if row else {}
    return summary


# ── Health ──────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """Health check."""
    conn = get_connection()
    conn.execute("SELECT 1")
    return {
        "status": "ok",
        "database": str(conn.execute("PRAGMA integrity_check").fetchone()[0]),
    }


@app.post("/api/seed")
def seed_database():
    """Seed demo data for hackathon. Idempotent."""
    result = seed_all()
    return {"status": "ok", "message": "Demo data seeded", "created": result}


@app.post("/api/seed/clear")
def clear_seed_data():
    """Clear all demo/seed data."""
    result = clear_all()
    return {"status": "ok", "message": "Seed data cleared", "deleted": result}


# ── Workflow Designer — Agents ──────────────────────────────────────

@app.get("/api/workflow/agents")
def wf_list_agents():
    """List all workflow agents."""
    db = AgentWorkflowDB()
    return db.list_agents()

@app.post("/api/workflow/agents")
def wf_create_agent(request: Request, data: dict):
    """Create a new workflow agent."""
    db = AgentWorkflowDB()
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
    db = AgentWorkflowDB()
    graph = db.get_workflow_graph(agent_id)
    if not graph or not graph.get("agent"):
        raise HTTPException(404, "Agent not found")

    return graph


@app.get("/api/workflow/agents/{agent_id}/export", response_class=HTMLResponse)
def wf_export_agent(agent_id: str):
    """Render a workflow as a deterministic pipeline export page — one card per step."""
    db = AgentWorkflowDB()
    graph = db.get_workflow_graph(agent_id)
    if not graph or not graph.get("agent"):
        raise HTTPException(404, "Agent not found")

    agent = graph["agent"]
    steps = graph.get("steps", [])
    connections = graph.get("connections", [])

    # Build connection map to determine flow order
    step_map = {s["id"]: s for s in steps}
    from_map = {}
    for c in connections:
        from_map.setdefault(c["from_step_id"], []).append(c)

    # Find the start step (input type or first in list)
    start_id = None
    for s in steps:
        if s.get("step_type") == "input":
            start_id = s["id"]
            break
    if not start_id and steps:
        start_id = steps[0]["id"]

    # Topological order following connections
    ordered = []
    visited = set()
    cur = start_id
    while cur and cur not in visited:
        visited.add(cur)
        s = step_map.get(cur)
        if s:
            ordered.append(s)
        next_conns = from_map.get(cur, [])
        cur = next_conns[0]["to_step_id"] if next_conns else None

    # If any steps are disconnected / not in the chain, append them
    for s in steps:
        if s["id"] not in visited:
            ordered.append(s)

    # Color mapping by step type
    type_colors = {
        "input": "#666666",
        "llm_call": "#FF1F3C",
        "tool_call": "#3B82F6",
        "human_escalation": "#F59E0B",
        "loop": "#10B981",
        "condition": "#8B5CF6",
        "email_gateway": "#3B82F6",
    }
    type_icons = {
        "input": "⬅",
        "llm_call": "🧠",
        "tool_call": "🔧",
        "human_escalation": "👤",
        "loop": "🔄",
        "condition": "❓",
        "email_gateway": "📧",
    }

    def esc(t):
        """HTML-escape a value."""
        if not t:
            return ""
        return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    def authority_badge(auth_json):
        """Render a Delegation of Authority badge."""
        try:
            auth = json.loads(auth_json or "{}")
        except Exception:
            auth = {}
        level = auth.get("level", "standard")
        level_colors = {"readonly": "#666", "standard": "#3B82F6", "elevated": "#F59E0B", "admin": "#EF4444"}
        color = level_colors.get(level, "#3B82F6")
        return f'<span style="display:inline-block;padding:2px 8px;border-radius:3px;font-size:10px;font-weight:600;color:{color};border:1px solid {color}40;background:{color}15;">{level.upper()}</span>'

    def render_tools(tools_json):
        """Render tool list."""
        try:
            tools = json.loads(tools_json or "[]")
        except Exception:
            tools = []
        if not tools:
            return ""
        parts = []
        for t in tools:
            if isinstance(t, dict):
                name = t.get("name", t.get("tool", "?"))
            else:
                name = str(t)
            parts.append(f'<span class="export-tool-badge">{esc(name)}</span>')
        return '<div style="margin:6px 0 0;display:flex;gap:4px;flex-wrap:wrap;">' + "".join(parts) + "</div>"

    # Build step cards HTML
    step_cards = ""
    for i, s in enumerate(ordered):
        stype = s.get("step_type", "llm_call")
        color = type_colors.get(stype, "#666")
        icon = type_icons.get(stype, "⚙")
        label = esc(s.get("label", ""))
        prompt = esc(s.get("prompt_template", ""))
        model = esc(s.get("model_name", ""))
        tools_html = render_tools(s.get("tools_json", "[]"))
        auth_html = authority_badge(s.get("authority_json", ""))

        # Loop config
        loop_info = ""
        try:
            lc = json.loads(s.get("loop_config_json", "{}"))
        except Exception:
            lc = {}
        if lc:
            fields = []
            for k, v in lc.items():
                fields.append(f"<span class='export-param'><span class='export-param-key'>{esc(k)}</span><span class='export-param-val'>{esc(v)}</span></span>")
            loop_info = '<div style="margin-top:6px;padding-top:6px;border-top:1px solid #222;"><span class="export-label">Loop Config</span><div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:4px;">' + "".join(fields) + "</div></div>"

        # Tool params
        tool_params = ""
        try:
            tp = json.loads(s.get("tool_params_json", "[]"))
        except Exception:
            tp = []
        if tp:
            fields = []
            for p in tp:
                fields.append(f"<span class='export-param'><span class='export-param-key'>{esc(p.get('key',''))}</span><span class='export-param-val'>{esc(p.get('source',''))}</span></span>")
            tool_params = '<div style="margin-top:6px;padding-top:6px;border-top:1px solid #222;"><span class="export-label">Params</span><div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:4px;">' + "".join(fields) + "</div></div>"

        step_cards += f"""
<div class="export-step" style="border-left-color: {color};">
  <div class="export-step-header">
    <div class="export-step-number">{i + 1}</div>
    <div class="export-step-label">
      <span class="export-step-icon">{icon}</span>
      <span class="export-step-title">{label or 'Unnamed Step'}</span>
      <span class="export-step-type" style="color:{color};">{esc(stype)}</span>
    </div>
    <div>{auth_html}</div>
  </div>
  <div class="export-step-body">
    {f'''
    <div class="export-field">
      <span class="export-label">Prompt</span>
      <div class="export-prompt">{prompt}</div>
    </div>
    ''' if prompt else ''}
    {f'''
    <div class="export-field">
      <span class="export-label">Model</span>
      <div class="export-model">{model}</div>
    </div>
    ''' if model else ''}
    {tools_html}
    {loop_info}
    {tool_params}
  </div>
</div>"""

    # Build flow arrows
    flow_arrows = ""
    for i in range(len(ordered) - 1):
        curr = ordered[i]
        nxt = ordered[i + 1]
        curr_label = esc(curr.get("label", "") or "?")
        nxt_label = esc(nxt.get("label", "") or "?")
        flow_arrows += f'<div class="export-arrow"><span class="export-arrow-label">{curr_label}</span><span class="export-arrow-symbol">→</span><span class="export-arrow-label">{nxt_label}</span></div>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Workflow Export — {esc(agent.get('name',''))}</title>
<style>
:root {{
  --bg: #000000;
  --surface: #111111;
  --border: #333333;
  --text: #FFFFFF;
  --secondary: #AAAAAA;
  --dim: #666666;
  --red: #FF1F3C;
  --font-logo: 'Bebas Neue', cursive;
  --font-nav: 'Barlow Condensed', sans-serif;
  --font-body: 'Source Serif 4', Georgia, serif;
  --font-mono: 'IBM Plex Mono', monospace;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  background: var(--bg); color: var(--text);
  font-family: var(--font-body); padding: 0 0 80px;
}}
::-webkit-scrollbar {{ width: 6px; }}
::-webkit-scrollbar-track {{ background: var(--bg); }}
::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 3px; }}
h1 {{ font-family: var(--font-logo); font-size: 32px; letter-spacing: 2px; }}
h2 {{ font-family: var(--font-nav); font-size: 16px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; color: var(--secondary); }}
.page-header {{
  position: sticky; top: 0; z-index: 100;
  display: flex; align-items: center; gap: 16px;
  padding: 16px 32px; background: var(--bg); border-bottom: 1px solid var(--border);
}}
.page-header .red {{ color: var(--red); }}
.page-header .meta {{ font-family: var(--font-mono); font-size: 12px; color: var(--dim); margin-left: auto; }}
.page-subheader {{
  display: flex; align-items: center; gap: 24px;
  padding: 12px 32px; border-bottom: 1px solid var(--border);
  font-family: var(--font-nav); font-size: 14px; color: var(--secondary);
}}
.export-arrow {{
  display: flex; align-items: center; justify-content: center; gap: 8px;
  padding: 6px 0; font-family: var(--font-nav); font-size: 13px; color: var(--dim);
}}
.export-arrow-symbol {{ font-size: 18px; color: var(--red); }}
.export-arrow-label {{ max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.export-container {{ max-width: 900px; margin: 0 auto; padding: 24px 32px; }}
.export-step {{
  background: var(--surface); border: 1px solid var(--border);
  border-left: 4px solid var(--red); border-radius: 8px;
  margin-bottom: 0;
}}
.export-step-header {{
  display: flex; align-items: center; gap: 12px;
  padding: 12px 16px; border-bottom: 1px solid var(--border);
}}
.export-step-number {{
  width: 28px; height: 28px; border-radius: 50%;
  background: var(--bg); border: 1px solid var(--border);
  display: flex; align-items: center; justify-content: center;
  font-family: var(--font-mono); font-size: 12px; font-weight: 600; color: var(--dim);
  flex-shrink: 0;
}}
.export-step-label {{
  display: flex; align-items: center; gap: 8px; flex: 1;
  min-width: 0;
}}
.export-step-icon {{ font-size: 16px; }}
.export-step-title {{
  font-family: var(--font-nav); font-size: 16px; font-weight: 600;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.export-step-type {{
  font-family: var(--font-mono); font-size: 10px; text-transform: uppercase;
  letter-spacing: 0.5px; padding: 2px 6px; border-radius: 3px;
  background: rgba(255,255,255,0.05);
}}
.export-step-body {{ padding: 12px 16px; }}
.export-field {{ margin-bottom: 8px; }}
.export-field:last-child {{ margin-bottom: 0; }}
.export-label {{
  font-family: var(--font-nav); font-size: 10px; text-transform: uppercase;
  letter-spacing: 0.5px; color: var(--dim); margin-bottom: 2px; display: block;
}}
.export-prompt {{
  font-family: var(--font-mono); font-size: 11px; color: var(--secondary);
  line-height: 1.5; max-height: 80px; overflow-y: auto;
  padding: 6px 8px; background: rgba(0,0,0,0.3); border-radius: 4px;
  white-space: pre-wrap; word-break: break-word;
}}
.export-model {{
  font-family: var(--font-mono); font-size: 11px; color: var(--secondary);
}}
.export-tool-badge {{
  font-family: var(--font-mono); font-size: 10px; font-weight: 500;
  padding: 2px 8px; border-radius: 3px; background: rgba(59,130,246,0.15);
  color: #3B82F6; border: 1px solid rgba(59,130,246,0.3);
}}
.export-param {{
  font-family: var(--font-mono); font-size: 10px;
  padding: 2px 8px; border-radius: 3px; background: rgba(255,255,255,0.04);
  display: inline-flex; align-items: center; gap: 4px;
}}
.export-param-key {{ color: var(--dim); }}
.export-param-val {{ color: var(--secondary); }}
@media print {{
  body {{ background: #111; }}
  .page-header {{ position: static; }}
  .export-arrow {{ break-inside: avoid; }}
  .export-step {{ break-inside: avoid; }}
}}
</style>
</head>
<body>
<div class="page-header">
  <h1>EVOLVING<span class="red">SOFTWARE</span></h1>
  <h2>{esc(agent.get('name',''))}</h2>
  <div class="meta">{esc(agent.get('description',''))} · {len(ordered)} steps</div>
</div>
<div class="page-subheader">
  <span>Deterministic Pipeline Export</span>
  <span>Flow: {flow_arrows}</span>
  <span style="margin-left:auto;">{len(ordered)} steps · {len(connections)} connections</span>
</div>
<div class="export-container">
  {step_cards}
</div>
</body>
</html>"""
    return HTMLResponse(html)

@app.put("/api/workflow/agents/{agent_id}")
def wf_update_agent(request: Request, agent_id: str, data: dict):
    """Update workflow agent fields."""
    db = AgentWorkflowDB()
    result = db.update_agent(agent_id, **data)
    if not result:
        raise HTTPException(404, "Agent not found")
    sync_agent_to_yaml(agent_id)
    audit_state_change(request, "update", "agent", agent_id, None, result)
    return result

@app.delete("/api/workflow/agents/{agent_id}")
def wf_delete_agent(request: Request, agent_id: str):
    """Delete a workflow agent and all its data."""
    db = AgentWorkflowDB()
    ok = db.delete_agent(agent_id)
    if not ok:
        raise HTTPException(404, "Agent not found")
    sync_agent_to_yaml(agent_id)
    audit_state_change(request, "delete", "agent", agent_id)
    return {"status": "deleted"}

@app.post("/api/workflow/agents/{agent_id}/clone")
def wf_clone_agent(request: Request, agent_id: str, data: dict):
    """Deep clone an agent workflow."""
    db = AgentWorkflowDB()
    result = db.clone_agent(agent_id, data.get("new_name"))
    if not result:
        raise HTTPException(404, "Agent not found")
    audit_state_change(request, "create", "agent", result["id"], None, result)
    return result

# ── Workflow Designer — Steps ──────────────────────────────────────

@app.get("/api/workflow/agents/{agent_id}/steps")
def wf_list_steps(agent_id: str):
    """List all steps for an agent workflow."""
    db = AgentWorkflowDB()
    return db.list_steps(agent_id)

@app.post("/api/workflow/agents/{agent_id}/steps")
async def wf_create_step_v2(request: Request, agent_id: str):
    """Create a new workflow step (v2 — route module)."""
    body = await request.json()
    data = body if body else {}
    db = AgentWorkflowDB()
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
    return result

@app.put("/api/workflow/agents/{agent_id}/steps/{step_id}")
async def wf_update_step_v2(request: Request, agent_id: str, step_id: str):
    """Update a workflow step."""
    body = await request.json()
    data = body if body else {}
    db = AgentWorkflowDB()
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
    db = AgentWorkflowDB()
    ok = db.delete_step(step_id)
    if not ok:
        raise HTTPException(404, "Step not found")
    sync_agent_to_yaml(agent_id)
    audit_state_change(request, "delete", "step", step_id)
    return {"status": "deleted"}

@app.put("/api/workflow/agents/{agent_id}/steps/reorder")
def wf_reorder_steps(agent_id: str, data: dict):
    """Bulk reorder/reposition steps. Expects {steps: [{id, position_x, position_y}, ...]}"""
    db = AgentWorkflowDB()
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
    db = AgentWorkflowDB()
    return db.list_connections(agent_id)

@app.post("/api/workflow/agents/{agent_id}/connections")
def wf_create_connection(request: Request, agent_id: str, data: dict):
    """Create a connection between two steps."""
    db = AgentWorkflowDB()
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
    db = AgentWorkflowDB()
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
    db = AgentWorkflowDB()
    return db.list_runs(agent_id, limit=limit)

@app.get("/api/workflow/agents/{agent_id}/runs/{run_id}")
def wf_get_run(agent_id: str, run_id: str):
    """Get a run with all its step logs."""
    db = AgentWorkflowDB()
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
    db = AgentWorkflowDB()
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
    db = AgentWorkflowDB()
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    db.update_run_status(run_id, "cancelled", completed_at=datetime.now(timezone.utc).isoformat())
    audit_state_change(request, "stop", "workflow_run", run_id)
    return {"status": "cancelled"}


@app.post("/api/workflow/{agent_id}/run")
async def run_workflow(request: Request, agent_id: str):
    """Execute a workflow end-to-end with real HTTP tool calls.

    Request body: { "input": { "topic": "...", "date": "...", ... } }

    Flow:
    1. Load agent from DB
    2. Create WorkflowExecutor and execute all steps in DAG order
    3. Return run result with per-step outputs

    This uses the real execution engine (not the simulated one at
    ``POST /api/workflow/agents/{agent_id}/run``).
    """
    body = await request.json()
    input_data = body.get("input", {}) if body else {}
    trigger = body.get("trigger", "api")

    # Load agent to verify it exists
    db = AgentWorkflowDB()
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} not found")

    # Execute workflow with the real engine
    executor = WorkflowExecutor()
    result = executor.execute(agent_id, input_context=input_data, trigger=trigger)

    if "error" in result:
        raise HTTPException(400, result["error"])

    # Enrich with step details for the response
    run_id = result.get("id", result.get("run_id", ""))
    if run_id:
        try:
            steps = db.list_step_logs(run_id)
            result["steps"] = steps
        except Exception:
            pass

    audit_state_change(
        request, "execute", "workflow_run",
        run_id or agent_id,
        None, {"agent_id": agent_id, "status": result.get("status", "unknown")},
    )
    return result

# ── Global Dashboard ───────────────────────────────────────────────

@app.get("/api/workflow/dashboard/summary")
def wf_dashboard_summary():
    """Global metrics across all workflow agents."""
    db = AgentWorkflowDB()
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
    db = AgentWorkflowDB()
    all_agents = db.list_agents()
    all_runs = []
    for a in all_agents:
        runs = db.list_runs(a["id"], limit=5)
        for r in runs:
            r["agent_name"] = a.get("name", "Unknown")
            all_runs.append(r)
    all_runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return all_runs[:limit]


# ── ABN / GST Verification ─────────────────────────────────────────

@app.post("/api/verify/abn")
def verify_abn(data: dict):
    """Verify a single ABN — check-digit validation + ABR lookup.

    Request body:
      { "abn": "16115191239" }

    Returns ABN status, entity name, GST registration, address.
    """
    abn = data.get("abn", "").strip()
    if not abn:
        raise HTTPException(400, "abn is required")

    # Format validation
    is_valid = validate_abn_format(abn)
    if not is_valid:
        return {
            "valid": False,
            "abn": abn,
            "error": "Invalid ABN check-digit. ABN must be 11 digits.",
        }

    # Full lookup
    result = lookup_abn(abn)
    if result.error_message:
        return {
            "valid": False,
            "abn": abn,
            "error": result.error_message,
            "lookup_source": result.lookup_source,
        }

    return {
        "valid": True,
        "abn": result.abn_formatted or result.abn,
        "entity_name": result.entity_name,
        "entity_type_code": result.entity_type_code,
        "entity_type_name": result.entity_type_name,
        "abn_status": result.abn_status,
        "abn_status_since": result.abn_status_from,
        "is_active": result.is_active,
        "gst_registered": result.is_gst_registered,
        "gst_from": result.gst_from,
        "address": {
            "suburb": result.address_suburb,
            "state": result.address_state,
            "postcode": result.address_postcode,
        },
        "main_business_name": result.main_business_name,
        "business_names": result.business_names,
        "lookup_source": result.lookup_source,
    }


@app.post("/api/verify/gst")
def check_gst(data: dict):
    """Check GST registration status for an ABN.

    Request body:
      { "abn": "16115191239" }

    Returns whether ABN is GST-registered and registration date.
    """
    abn = data.get("abn", "").strip()
    if not abn:
        raise HTTPException(400, "abn is required")

    result = check_gst_registration(abn)
    return result


@app.post("/api/verify/business")
def full_business_verification(data: dict):
    """Full business verification: ABN + name match + GST + risk assessment.

    Request body:
      {
        "abn": "16115191239",
        "business_name": "Tether Tech Pty Ltd",
        "require_gst": true
      }

    Returns comprehensive verification report with risk level.
    """
    abn = data.get("abn", "").strip()
    if not abn:
        raise HTTPException(400, "abn is required")

    business_name = data.get("business_name", "")
    require_gst = data.get("require_gst", False)

    report = verify_business(abn, business_name=business_name, require_gst=require_gst)
    return report.to_dict()


@app.get("/api/verify/business/search")
def search_business(query: str = Query("", description="Business name to search")):
    """Search the Australian Business Register by business name.

    Returns matching entities with ABN, status, GST registration.
    """
    if not query or len(query.strip()) < 2:
        raise HTTPException(400, "query must be at least 2 characters")

    results = search_business_name(query.strip())
    return {
        "query": query,
        "count": len(results),
        "results": [r.to_dict() for r in results],
    }


# ── PTRS Credit Checking ───────────────────────────────────────────-

@app.post("/api/verify/ptrs")
def ptrs_credit_check(data: dict):
    """Check PTRS (Payment Times Reports Register) credit history.

    Request body:
      {
        "abn": "16115191239",
        "business_name": "Tether Tech Pty Ltd"
      }

    Returns credit score, risk level, payment behaviour metrics,
    and a credit recommendation.
    """
    abn = data.get("abn", "").strip()
    if not abn:
        raise HTTPException(400, "abn is required")

    business_name = data.get("business_name", "")
    score = check_ptrs(abn, business_name)
    return score.to_dict()


# ── Credit Check History ──────────────────────────────────────────-

@app.get("/api/verify/credit-checks")
def list_credit_checks(
    limit: int = Query(20, le=100),
    risk_level: str | None = None,
):
    """List recent credit/business verification checks.

    Args:
        limit: Max results (default 20, max 100)
        risk_level: Filter by risk level (low | medium | high | critical | unknown)
    """
    conn = get_connection()
    if risk_level:
        rows = conn.execute(
            """SELECT id, abn, entity_name, overall_risk, passed_checks,
                      total_checks, verified_at
               FROM credit_checks
               WHERE overall_risk = ?
               ORDER BY verified_at DESC LIMIT ?""",
            (risk_level, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, abn, entity_name, overall_risk, passed_checks,
                      total_checks, verified_at
               FROM credit_checks
               ORDER BY verified_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()

    return [dict(r) for r in rows]


@app.get("/api/verify/ptrs-checks")
def list_ptrs_checks(
    limit: int = Query(20, le=100),
    risk_level: str | None = None,
):
    """List recent PTRS credit checks.

    Args:
        limit: Max results (default 20, max 100)
        risk_level: Filter by risk level (low | medium | high | critical | unknown)
    """
    conn = get_connection()
    if risk_level:
        rows = conn.execute(
            """SELECT id, abn, business_name, score, score_label, risk_level,
                      credit_recommendation, recommended_limit_cents,
                      recommended_terms_days, check_time
               FROM ptrs_checks
               WHERE risk_level = ?
               ORDER BY check_time DESC LIMIT ?""",
            (risk_level, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, abn, business_name, score, score_label, risk_level,
                      credit_recommendation, recommended_limit_cents,
                      recommended_terms_days, check_time
               FROM ptrs_checks
               ORDER BY check_time DESC LIMIT ?""",
            (limit,),
        ).fetchall()

    return [dict(r) for r in rows]


@app.get("/api/verify/summary")
def verification_summary():
    """Get verification summary statistics."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM verification_summary").fetchone()
    return dict(row) if row else {}


# ── Cash Flow Forecasting ────────────────────────────────────────────

from cashflow_forecast import CashFlowForecaster as _CashFlowForecaster

_forecaster: _CashFlowForecaster | None = None


def _get_forecaster() -> _CashFlowForecaster:
    global _forecaster
    if _forecaster is None:
        _forecaster = _CashFlowForecaster()
    return _forecaster


@app.get("/api/forecast")
def get_forecast():
    """Get cash flow forecast for all active debtors."""
    conn = get_connection()
    try:
        from tether_engine import DebtorRecord, DebtorState
        rows = conn.execute(
            "SELECT id, name, invoice_number, amount_cents, days_overdue, "
            "escalation_tier, state, paid_at, paid_amount_cents "
            "FROM debtors"
        ).fetchall()
    except Exception:
        rows = []
    
    debtors = [dict(r) for r in rows]
    forecaster = _get_forecaster()
    return forecaster.aggregate_forecast(debtors)


@app.get("/api/forecast/dso")
def get_dso():
    """Get Days Sales Outstanding calculation."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, amount_cents, days_overdue, state, paid_at, paid_amount_cents "
            "FROM debtors"
        ).fetchall()
    except Exception:
        rows = []
    debtors = [dict(r) for r in rows]
    return _get_forecaster().calculate_dso(debtors)


@app.post("/api/forecast/what-if")
def forecast_what_if(data: dict):
    """What-if: show cash impact of X% improvement in collection rate."""
    improvement = data.get("improvement", 10)
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, amount_cents, days_overdue, escalation_tier, state, paid_at, paid_amount_cents "
            "FROM debtors"
        ).fetchall()
    except Exception:
        rows = []
    debtors = [dict(r) for r in rows]
    return _get_forecaster().what_if_scenario(debtors, improvement)


# ── Multi-Entity Management ─────────────────────────────────────────

from multi_entity import EntityManager as _EntityManager

_entity_mgr: _EntityManager | None = None


def _get_entity_mgr() -> _EntityManager:
    global _entity_mgr
    if _entity_mgr is None:
        _entity_mgr = _EntityManager()
        _entity_mgr.ensure_tables()
    return _entity_mgr


@app.get("/api/entities")
def list_entities(user_id: str | None = None):
    """List all entities (businesses)."""
    return _get_entity_mgr().list_entities(user_id or "")


@app.post("/api/entities")
def create_entity(data: dict):
    """Create a new entity/business."""
    name = data.get("name", "")
    if not name:
        raise HTTPException(400, "name is required")
    return _get_entity_mgr().create_entity(name=name, abn=data.get("abn", ""), settings=data.get("settings"))


@app.get("/api/entities/{entity_id}")
def get_entity(entity_id: str):
    """Get entity details with stats."""
    entity = _get_entity_mgr().get_entity(entity_id)
    if not entity:
        raise HTTPException(404, f"Entity {entity_id} not found")
    return entity


@app.get("/api/entities/{entity_id}/summary")
def get_entity_summary(entity_id: str):
    """Get entity summary dashboard data."""
    return _get_entity_mgr().get_entity_summary(entity_id)


@app.get("/api/entities/stats/all")
def all_entity_stats():
    """Aggregate stats across all entities."""
    return _get_entity_mgr().list_entity_stats()


@app.post("/api/entities/{entity_id}/users")
def add_entity_user(entity_id: str, data: dict):
    """Add a user to an entity."""
    user_id = data.get("user_id", "")
    role = data.get("role", "viewer")
    if not user_id:
        raise HTTPException(400, "user_id is required")
    return _get_entity_mgr().add_user(entity_id, user_id, role)


# ── Early Payment Discounts ─────────────────────────────────────────

from early_payment_discounts import EarlyPaymentDiscount as _EarlyPaymentDiscount

_discount_engine: _EarlyPaymentDiscount | None = None


def _get_discounts() -> _EarlyPaymentDiscount:
    global _discount_engine
    if _discount_engine is None:
        _discount_engine = _EarlyPaymentDiscount()
    return _discount_engine


@app.get("/api/discounts/rules")
def list_discount_rules(entity_id: str | None = None):
    """List all early payment discount rules."""
    return _get_discounts().list_rules(entity_id or "")


@app.post("/api/discounts/rules")
def create_discount_rule(data: dict):
    """Create a new discount rule."""
    name = data.get("name", "")
    if not name:
        raise HTTPException(400, "name is required")
    return _get_discounts().create_rule(
        name=name,
        entity_id=data.get("entity_id", ""),
        discount_percent=data.get("discount_percent", 0),
        min_days_early=data.get("min_days_early", 0),
        max_days_early=data.get("max_days_early", 0),
    )


@app.post("/api/discounts/check")
def check_discount(data: dict):
    """Check if a discount applies to an invoice."""
    from datetime import datetime
    amount_cents = data.get("amount_cents", 0)
    invoice_date = data.get("invoice_date", datetime.now().strftime("%Y-%m-%d"))
    debtor_history = data.get("debtor_history", [])
    return _get_discounts().get_applicable_discount(amount_cents, invoice_date, debtor_history)


@app.post("/api/discounts/generate")
def generate_discount_incentive(data: dict):
    """Generate discount incentive text for a debtor."""
    debtor_name = data.get("debtor_name", "")
    invoice_number = data.get("invoice_number", "")
    amount_cents = data.get("amount_cents", 0)
    discount = data.get("discount", {})
    if not debtor_name or not invoice_number:
        raise HTTPException(400, "debtor_name and invoice_number are required")
    text = _get_discounts().generate_incentive(debtor_name, invoice_number, amount_cents, discount)
    return {"text": text}


@app.get("/api/discounts/analytics")
def discount_analytics(entity_id: str | None = None):
    """Get discount usage analytics."""
    return _get_discounts().get_analytics(entity_id or "")


# ── Credit Limit Management ─────────────────────────────────────────

from credit_limit import CreditLimitManager as _CreditLimitManager

_credit_mgr: _CreditLimitManager | None = None


def _get_credit() -> _CreditLimitManager:
    global _credit_mgr
    if _credit_mgr is None:
        _credit_mgr = _CreditLimitManager()
    return _credit_mgr


@app.post("/api/credit/limits")
def set_credit_limit(data: dict):
    """Set or update a customer's credit limit."""
    customer_id = data.get("customer_id", "")
    entity_id = data.get("entity_id", "biz-001")
    limit_cents = data.get("limit_cents", 0)
    if not customer_id or not limit_cents:
        raise HTTPException(400, "customer_id and limit_cents are required")
    return _get_credit().set_limit(customer_id, entity_id, limit_cents,
                                   terms_days=data.get("terms_days", 30),
                                   notes=data.get("notes", ""))


@app.get("/api/credit/status/{customer_id}")
def credit_status(customer_id: str):
    """Get credit limit status for a customer."""
    status = _get_credit().get_status(customer_id)
    if not status:
        raise HTTPException(404, f"Customer {customer_id} not found")
    return status


@app.post("/api/credit/check")
def check_invoice_credit(data: dict):
    """Check if a new invoice can be issued against credit limit."""
    customer_id = data.get("customer_id", "")
    amount_cents = data.get("amount_cents", 0)
    if not customer_id or not amount_cents:
        raise HTTPException(400, "customer_id and amount_cents are required")
    return _get_credit().check_invoice(customer_id, amount_cents)


@app.get("/api/credit/near-limit")
def near_limit_customers(entity_id: str | None = None, threshold: float = 0.8):
    """List customers near or over their credit limit."""
    return _get_credit().list_near_limit(entity_id or "", threshold)


@app.get("/api/credit/over-limit")
def over_limit_customers(entity_id: str | None = None):
    """List customers currently over their credit limit."""
    return _get_credit().list_over_limit(entity_id or "")


@app.get("/api/credit/summary/{entity_id}")
def credit_summary(entity_id: str):
    """Get entity-level credit summary."""
    return _get_credit().get_entity_summary(entity_id)


@app.post("/api/credit/suspend/{customer_id}")
def suspend_credit(customer_id: str, data: dict):
    """Suspend a customer's credit."""
    reason = data.get("reason", "")
    return _get_credit().suspend(customer_id, reason)


# ── Letter of Demand ────────────────────────────────────────────────

from letter_of_demand import LetterOfDemand as _LetterOfDemand

_demand_gen: _LetterOfDemand | None = None


def _get_demand() -> _LetterOfDemand:
    global _demand_gen
    if _demand_gen is None:
        _demand_gen = _LetterOfDemand()
    return _demand_gen


@app.post("/api/demand/generate")
def generate_demand_letter(data: dict):
    """Generate a formal letter of demand with Australian statutory wording."""
    debtor_name = data.get("debtor_name", "")
    business_name = data.get("business_name", "")
    invoice_number = data.get("invoice_number", "")
    amount_cents = data.get("amount_cents", 0)
    days_overdue = data.get("days_overdue", 0)
    state = data.get("state", "NSW")

    if not debtor_name or not business_name or not invoice_number:
        raise HTTPException(400, "debtor_name, business_name, and invoice_number are required")

    letter = _get_demand().generate(
        debtor_name=debtor_name,
        debtor_address=data.get("debtor_address", ""),
        business_name=business_name,
        business_address=data.get("business_address", ""),
        invoice_number=invoice_number,
        amount_cents=amount_cents,
        days_overdue=days_overdue,
        state=state,
        abn=data.get("abn", ""),
    )
    return letter


@app.post("/api/demand/generate-pdf")
def generate_demand_pdf(data: dict):
    """Generate a formal letter of demand as a PDF."""
    letter_data = _get_demand().generate(
        debtor_name=data.get("debtor_name", ""),
        debtor_address=data.get("debtor_address", ""),
        business_name=data.get("business_name", ""),
        business_address=data.get("business_address", ""),
        invoice_number=data.get("invoice_number", ""),
        amount_cents=data.get("amount_cents", 0),
        days_overdue=data.get("days_overdue", 0),
        state=data.get("state", "NSW"),
        abn=data.get("abn", ""),
    )
    pdf_path = _get_demand().generate_pdf(letter_data)
    return {"text": letter_data, "pdf_path": pdf_path}


@app.get("/api/demand/state-info/{state}")
def demand_state_info(state: str):
    """Get debt recovery legal info for a specific Australian state."""
    info = _get_demand().get_state_info(state.upper())
    if "error" in info:
        raise HTTPException(404, info["error"])
    return info


@app.get("/api/demand/validate")
def validate_demand_letter(text: str):
    """Validate a letter of demand has all required elements."""
    issues = _get_demand().validate(text)
    return {"valid": len(issues) == 0, "issues": issues}


# ── Legal Documents (Terms of Trade, Personal Guarantee, Credit App) ──

from legal_docs import LegalDocumentGenerator as _LegalDocGen

_doc_gen: _LegalDocGen | None = None


def _get_docgen() -> _LegalDocGen:
    global _doc_gen
    if _doc_gen is None:
        _doc_gen = _LegalDocGen()
    return _doc_gen


@app.get("/api/legal-docs/types")
def list_legal_doc_types():
    """List all available legal document templates."""
    return _get_docgen().list_available()


@app.post("/api/legal-docs/terms-of-trade")
def generate_terms_of_trade(data: dict):
    """Generate Terms of Trade document."""
    name = data.get("business_name", "")
    if not name:
        raise HTTPException(400, "business_name is required")
    return _get_docgen().generate_terms(
        business_name=name,
        business_address=data.get("business_address", ""),
        abn=data.get("abn", ""),
        payment_terms_days=data.get("payment_terms_days", 30),
        late_fee_percent=data.get("late_fee_percent", 2.0),
    )


@app.post("/api/legal-docs/personal-guarantee")
def generate_personal_guarantee(data: dict):
    """Generate a Director's Personal Guarantee."""
    name = data.get("business_name", "")
    guarantor = data.get("guarantor_name", "")
    if not name or not guarantor:
        raise HTTPException(400, "business_name and guarantor_name are required")
    return _get_docgen().generate_guarantee(
        business_name=name,
        guarantor_name=guarantor,
        guarantor_address=data.get("guarantor_address", ""),
        guarantee_limit_cents=data.get("guarantee_limit_cents", 0),
    )


@app.post("/api/legal-docs/credit-application")
def generate_credit_application(data: dict):
    """Generate a Credit Application form."""
    name = data.get("business_name", "")
    if not name:
        raise HTTPException(400, "business_name is required")
    return _get_docgen().generate_credit_app(
        business_name=name,
        abn=data.get("abn", ""),
        address=data.get("address", ""),
        trading_terms_days=data.get("trading_terms_days", 30),
        credit_limit_cents=data.get("credit_limit_cents", 500000),
    )


@app.post("/api/legal-docs/generate-all")
def generate_all_legal_docs(data: dict):
    """Generate all credit management documents at once."""
    name = data.get("business_name", "")
    if not name:
        raise HTTPException(400, "business_name is required")
    docs = _get_docgen().generate_all(
        business_name=name,
        abn=data.get("abn", ""),
        address=data.get("address", ""),
        director_name=data.get("director_name", ""),
    )
    return docs


@app.post("/api/legal-docs/generate-pdf")
def generate_legal_doc_pdf(data: dict):
    """Generate a PDF of a legal document."""
    doc = data.get("document", {})
    doc_type = data.get("document_type", "")
    if not doc_type:
        raise HTTPException(400, "document_type is required")
    path = _get_docgen().generate_pdf(doc, doc_type)
    return {"pdf_path": path}


# ── Phone Call Tracking ─────────────────────────────────────────────

from call_tracker import CallTracker as _CallTracker

_call_tracker: _CallTracker | None = None


def _get_calls() -> _CallTracker:
    global _call_tracker
    if _call_tracker is None:
        _call_tracker = _CallTracker()
    return _call_tracker


@app.get("/api/calls")
def list_calls(debtor_id: str | None = None, limit: int = Query(50, le=200)):
    """List phone calls, optionally filtered by debtor."""
    if debtor_id:
        return _get_calls().get_history(debtor_id, limit=limit)
    return _get_calls().get_today()


@app.post("/api/calls")
def log_call(data: dict):
    """Log a phone call to a debtor."""
    debtor_id = data.get("debtor_id", "")
    if not debtor_id:
        raise HTTPException(400, "debtor_id is required")
    return _get_calls().log_call(
        debtor_id=debtor_id,
        caller_name=data.get("caller_name", ""),
        direction=data.get("direction", "outbound"),
        duration_seconds=data.get("duration_seconds", 0),
        notes=data.get("notes", ""),
        outcome=data.get("outcome", ""),
    )


@app.get("/api/calls/summary/{debtor_id}")
def call_summary(debtor_id: str):
    """Get call summary stats for a debtor."""
    return _get_calls().get_summary(debtor_id)


@app.get("/api/calls/pending")
def pending_callbacks():
    """Get debtors with pending callbacks."""
    return _get_calls().get_pending()


@app.post("/api/calls/schedule")
def schedule_call(data: dict):
    """Schedule a future call to a debtor."""
    debtor_id = data.get("debtor_id", "")
    scheduled_at = data.get("scheduled_at", "")
    if not debtor_id or not scheduled_at:
        raise HTTPException(400, "debtor_id and scheduled_at are required")
    return _get_calls().schedule(
        debtor_id=debtor_id,
        scheduled_at=scheduled_at,
        purpose=data.get("purpose", "follow_up"),
        caller=data.get("caller", ""),
    )


@app.get("/api/calls/stats")
def call_stats():
    """Get aggregate call outcome statistics."""
    return _get_calls().get_stats()


@app.post("/api/calls/script")
def generate_call_script(data: dict):
    """Generate a phone call script for contacting a debtor."""
    debtor_name = data.get("debtor_name", "")
    if not debtor_name:
        raise HTTPException(400, "debtor_name is required")
    script = _get_calls().generate_script(
        debtor_name=debtor_name,
        business_name=data.get("business_name", ""),
        invoice_number=data.get("invoice_number", ""),
        amount_cents=data.get("amount_cents", 0),
        days_overdue=data.get("days_overdue", 0),
        tone=data.get("tone", "professional"),
    )
    return {"script": script}


# ── Skip Tracing ────────────────────────────────────────────────────

from skip_tracing import SkipTracer as _SkipTracer

_skip_tracer: _SkipTracer | None = None


def _get_tracer() -> _SkipTracer:
    global _skip_tracer
    if _skip_tracer is None:
        _skip_tracer = _SkipTracer()
    return _skip_tracer


@app.post("/api/skip-trace/abn")
def trace_by_abn(data: dict):
    """Look up a debtor by ABN."""
    abn = data.get("abn", "")
    if not abn:
        raise HTTPException(400, "abn is required")
    return _get_tracer().trace_by_abn(abn)


@app.post("/api/skip-trace/name")
def trace_by_name(data: dict):
    """Search for a debtor by business name."""
    name = data.get("business_name", "")
    if not name:
        raise HTTPException(400, "business_name is required")
    return _get_tracer().trace_by_name(name, state=data.get("state", ""))


@app.post("/api/skip-trace/report")
def generate_skip_report(data: dict):
    """Generate a comprehensive skip tracing report."""
    return _get_tracer().generate_report(
        debtor_name=data.get("debtor_name", ""),
        known_email=data.get("known_email", ""),
        known_phone=data.get("known_phone", ""),
        known_address=data.get("known_address", ""),
        known_abn=data.get("known_abn", ""),
    )


@app.post("/api/skip-trace/strategy")
def suggest_skip_strategy(data: dict):
    """Suggest a contact strategy based on skip report."""
    report = data.get("skip_report", {})
    if not report:
        raise HTTPException(400, "skip_report is required")
    return _get_tracer().suggest_strategy(report)


# ── Legal Escalation Pathway ────────────────────────────────────────

from legal_pathway import LegalEscalation as _LegalEscalation

_legal_esc: _LegalEscalation | None = None


def _get_legal() -> _LegalEscalation:
    global _legal_esc
    if _legal_esc is None:
        _legal_esc = _LegalEscalation()
    return _legal_esc


@app.get("/api/legal/pathway")
def get_legal_pathway(
    amount_cents: int = Query(0),
    jurisdiction: str = Query("NSW"),
    debtor_type: str = Query("business"),
):
    """Get the legal escalation pathway for a debt."""
    if not amount_cents:
        raise HTTPException(400, "amount_cents is required")
    return _get_legal().get_pathway(amount_cents, jurisdiction, debtor_type)


@app.get("/api/legal/tribunal/{jurisdiction}")
def tribunal_info(jurisdiction: str):
    """Get tribunal info for a state."""
    return _get_legal().get_tribunal_info(jurisdiction.upper())


@app.get("/api/legal/court/{jurisdiction}")
def court_info(jurisdiction: str):
    """Get court info for a state."""
    return _get_legal().get_court_info(jurisdiction.upper())


@app.post("/api/legal/referral")
def generate_legal_referral(data: dict):
    """Generate a legal referral summary for a solicitor."""
    business_name = data.get("business_name", "")
    debtor_name = data.get("debtor_name", "")
    if not business_name or not debtor_name:
        raise HTTPException(400, "business_name and debtor_name are required")
    return _get_legal().generate_referral(
        business_name=business_name,
        debtor_name=debtor_name,
        amount_cents=data.get("amount_cents", 0),
        jurisdiction=data.get("jurisdiction", "NSW"),
    )


@app.get("/api/legal/limitation")
def check_limitation(
    due_date: str = Query(""),
    jurisdiction: str = Query("NSW"),
):
    """Check if a debt is within the limitation period."""
    if not due_date:
        raise HTTPException(400, "due_date is required")
    return _get_legal().check_limitation(due_date, jurisdiction)


# ── Payment Plans ───────────────────────────────────────────────────

from payment_plans import PaymentPlanNegotiator as _PaymentPlanNegotiator

_plan_nego: _PaymentPlanNegotiator | None = None


def _get_plans() -> _PaymentPlanNegotiator:
    global _plan_nego
    if _plan_nego is None:
        _plan_nego = _PaymentPlanNegotiator()
    return _plan_nego


@app.post("/api/payment-plans/propose")
def propose_payment_plans(data: dict):
    """Propose payment plan options for a debt amount."""
    amount_cents = data.get("amount_cents", 0)
    if not amount_cents:
        raise HTTPException(400, "amount_cents is required")
    plans = _get_plans().propose_plans(
        amount_cents,
        financial_notes=data.get("financial_notes", ""),
    )
    return {"plans": plans, "total_cents": amount_cents}


@app.post("/api/payment-plans/accept")
def accept_payment_plan(data: dict):
    """Accept a payment plan."""
    debtor_id = data.get("debtor_id", "")
    debtor_name = data.get("debtor_name", "")
    if not debtor_id or not debtor_name:
        raise HTTPException(400, "debtor_id and debtor_name are required")
    plan = _get_plans().accept_plan(
        debtor_id=debtor_id,
        debtor_name=debtor_name,
        business_name=data.get("business_name", ""),
        amount_cents=data.get("amount_cents", 0),
        num_instalments=data.get("instalments", 3),
        frequency=data.get("frequency", "monthly"),
    )
    return plan


@app.get("/api/payment-plans/active/{debtor_id}")
def get_active_payment_plan(debtor_id: str):
    """Get the active plan for a debtor."""
    plan = _get_plans().get_active_plan(debtor_id)
    if not plan:
        return {"plan": None}
    return {"plan": plan}


@app.post("/api/payment-plans/{plan_id}/payment")
def record_plan_payment(plan_id: str, data: dict):
    """Record a payment against a plan."""
    amount_cents = data.get("amount_cents", 0)
    if not amount_cents:
        raise HTTPException(400, "amount_cents is required")
    result = _get_plans().record_payment(plan_id, amount_cents)
    return result


@app.get("/api/payment-plans/overdue")
def check_overdue_plans(debtor_id: str | None = None):
    """Check for overdue plan payments."""
    overdue = _get_plans().check_overdue(debtor_id)
    return {"overdue": overdue, "count": len(overdue)}


@app.get("/api/payment-plans")
def list_all_plans():
    """Get all active payment plans."""
    plans = _get_plans().get_all_active_plans()
    return {"plans": plans, "count": len(plans)}


@app.get("/api/payment-plans/summary")
def payment_plans_summary():
    """Get payment plan statistics."""
    return _get_plans().get_plan_summary()


@app.post("/api/payment-plans/{plan_id}/agreement")
def generate_plan_agreement(plan_id: str):
    """Generate a PDF payment agreement for a plan."""
    path = _get_plans().generate_agreement_pdf(plan_id)
    return {"pdf_path": path, "plan_id": plan_id}


# ── Agent Workflow Engine ────────────────────────────────────────

_wf_executor = WorkflowExecutor()
_trace_store = TraceStore()
_prompt_manager = PromptVersionManager()
_evaluator = Evaluator()

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

    from starlette.responses import JSONResponse
    return JSONResponse(
        status_code=202,
        content={
            "job_id": job["id"],
            "agent_id": agent_id,
            "status": job["status"],
            "created_at": job["created_at"],
        },
    )


# ── Job Queue API ───────────────────────────────────────────────────


@app.get("/api/jobs/{job_id}")
def get_job_status(job_id: str):
    """Get the status, progress, and result of a background job."""
    worker = get_worker()
    job = worker.get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")
    return job


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    """Cancel a running or queued background job."""
    worker = get_worker()
    ok = worker.cancel_job(job_id)
    if not ok:
        raise HTTPException(
            404, f"Job {job_id} not found or already finished"
        )
    return {"status": "cancelled", "job_id": job_id}


@app.get("/api/jobs")
def list_jobs(status: str | None = None):
    """List background jobs, optionally filtered by status."""
    worker = get_worker()
    jobs = worker.list_jobs(status=status)
    return {"jobs": jobs, "total": len(jobs)}


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

_replay_engine = ReplayEngine()


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


# ── Audit Log Endpoint ─────────────────────────────────────────────


@app.get("/api/audit")
def query_audit_events(
    actor: str = "",
    resource_type: str = "",
    resource_id: str = "",
    action: str = "",
    from_date: str = "",
    to_date: str = "",
    limit: int = 50,
    offset: int = 0,
):
    """Query audit events with optional filters.

    All filters are optional. Events are returned sorted by created_at DESC.
    """
    events = query_events(
        actor_id=actor or None,
        resource_type=resource_type or None,
        resource_id=resource_id or None,
        action=action or None,
        from_date=from_date or None,
        to_date=to_date or None,
        limit=limit,
        offset=offset,
    )
    return {"events": events, "total": len(events), "limit": limit, "offset": offset}


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


@app.get("/api/workflow/agents/{agent_id}/estimate-cost")
def get_cost_estimate(agent_id: str):
    """Estimate running cost for all steps in an agent's workflow."""
    return _wf_executor.db.estimate_run_cost(agent_id)


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
    if "authority" in data and isinstance(data["authority"], dict):
        data["authority_json"] = json.dumps(data["authority"])
        del data["authority"]
    if "authority_config" in data and isinstance(data["authority_config"], dict):
        data["authority_json"] = json.dumps(data["authority_config"])
        del data["authority_config"]
    if "tools" in data and isinstance(data["tools"], (list, dict)):
        data["tools_json"] = json.dumps(data["tools"])
        del data["tools"]
    if "loop_config" in data and isinstance(data["loop_config"], dict):
        data["loop_config_json"] = json.dumps(data["loop_config"])
        del data["loop_config"]
    result = _wf_executor.db.update_step(step_id, **data)
    if result:
        sync_agent_to_yaml(result["agent_id"])
    return result

@app.delete("/api/workflow/steps/{step_id}")
def delete_step(step_id: str):
    """Delete a step."""
    # Look up agent_id before deleting
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
    # Look up agent_id before deleting
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
    # Look up agent_id before deleting
    cred = _wf_executor.db.get_credential(cred_id)
    agent_id = cred["agent_id"] if cred else None
    ok = _wf_executor.db.delete_credential(cred_id)
    if not ok:
        raise HTTPException(404, "Credential not found")
    if agent_id:
        sync_agent_to_yaml(agent_id)
    return {"deleted": True}

# ── Tracing ──

@app.get("/api/workflow/traces/{run_id}")
def get_run_traces(run_id: str):
    """Get trace tree for a run."""
    return _trace_store.get_run_trace_tree(run_id)

@app.get("/api/workflow/traces/{run_id}/spans")
def get_run_trace_spans(run_id: str):
    """Get flat list of trace spans for a run."""
    return {"spans": _trace_store.get_run_traces(run_id)}

@app.get("/api/workflow/traces/tree/{trace_id}")
def get_trace_tree(trace_id: str):
    """Get nested trace tree by trace_id."""
    return _trace_store.get_trace_tree(trace_id)

# ── Prompt Versioning ──

@app.get("/api/workflow/prompts/versions/{step_id}")
def list_prompt_versions(step_id: str):
    """List all prompt versions for a step."""
    versions = _prompt_manager.list_versions(step_id)
    return {"versions": versions, "count": len(versions)}

@app.get("/api/workflow/prompts/version/{version_id}")
def get_prompt_version(version_id: str):
    """Get a specific prompt version."""
    v = _prompt_manager.get_version(version_id)
    if not v:
        raise HTTPException(404, "Version not found")
    return v

@app.post("/api/workflow/prompts/diff")
def diff_prompt_versions(data: dict):
    """Diff two prompt versions."""
    id_a = data.get("version_id_a", "")
    id_b = data.get("version_id_b", "")
    if not id_a or not id_b:
        raise HTTPException(400, "version_id_a and version_id_b are required")
    return _prompt_manager.diff_versions(id_a, id_b)

@app.post("/api/workflow/prompts/rollback/{step_id}")
def rollback_prompt(step_id: str, data: dict):
    """Rollback a step's prompt to a previous version."""
    target = data.get("target_version", 0)
    if not target:
        raise HTTPException(400, "target_version is required")
    try:
        return _prompt_manager.rollback(step_id, target)
    except ValueError as e:
        raise HTTPException(404, str(e))

@app.post("/api/workflow/prompts/record")
def record_prompt_version(data: dict):
    """Manually record a prompt version."""
    step_id = data.get("step_id", "")
    if not step_id:
        raise HTTPException(400, "step_id is required")
    return _prompt_manager.record_version(
        step_id=step_id,
        prompt_template=data.get("prompt_template", ""),
        rendered_prompt=data.get("rendered_prompt", ""),
        run_id=data.get("run_id", ""),
        context_data=json.dumps(data.get("context", {})),
        output_data=json.dumps(data.get("output", {})),
        tokens_input=data.get("tokens_input", 0),
        tokens_output=data.get("tokens_output", 0),
        model_used=data.get("model_used", ""),
        notes=data.get("notes", ""),
    )

# ── Evaluation ──

@app.post("/api/workflow/eval/datasets")
def create_eval_dataset(data: dict):
    """Create an evaluation dataset."""
    name = data.get("name", "")
    desc = data.get("description", "")
    if not name:
        raise HTTPException(400, "name is required")
    return _evaluator.create_dataset(name, desc)

@app.get("/api/workflow/eval/datasets")
def list_eval_datasets():
    """List all evaluation datasets."""
    return {"datasets": _evaluator.list_datasets()}

@app.get("/api/workflow/eval/datasets/{dataset_id}")
def get_eval_dataset(dataset_id: str):
    """Get a dataset with its items."""
    ds = _evaluator.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    items = _evaluator.list_items(dataset_id)
    return {"dataset": ds, "items": items}

@app.delete("/api/workflow/eval/datasets/{dataset_id}")
def delete_eval_dataset(dataset_id: str):
    """Delete a dataset."""
    ok = _evaluator.delete_dataset(dataset_id)
    if not ok:
        raise HTTPException(404, "Dataset not found")
    return {"deleted": True}

@app.post("/api/workflow/eval/datasets/{dataset_id}/items")
def add_eval_item(dataset_id: str, data: dict):
    """Add an item to a dataset."""
    input_text = data.get("input_text", "")
    if not input_text:
        raise HTTPException(400, "input_text is required")
    return _evaluator.add_item(
        dataset_id=dataset_id,
        input_text=input_text,
        expected_output=data.get("expected_output", ""),
        metadata_json=json.dumps(data.get("metadata", {})),
    )

@app.post("/api/workflow/eval/datasets/{dataset_id}/import")
def bulk_import_eval_items(dataset_id: str, data: dict):
    """Bulk import items into a dataset."""
    items = data.get("items", [])
    if not items:
        raise HTTPException(400, "items array is required")
    result = _evaluator.import_items(dataset_id, items)
    return {"imported": len(result), "items": result}

@app.post("/api/workflow/eval/run")
def run_evaluation(data: dict):
    """Run an evaluation against an agent in the background.

    Submits to the job queue and returns immediately with 202 Accepted.
    """
    dataset_id = data.get("dataset_id", "")
    agent_id = data.get("agent_id", "")
    notes = data.get("notes", "")
    idempotency_key = data.get("idempotency_key", "")
    if not dataset_id or not agent_id:
        raise HTTPException(400, "dataset_id and agent_id are required")

    worker = get_worker(max_workers=2)
    job = worker.submit(
        job_type="eval_run",
        agent_id=agent_id,
        input_json=json.dumps({
            "dataset_id": dataset_id,
            "notes": notes,
        }),
        idempotency_key=idempotency_key,
        timeout_s=data.get("timeout_s", 600),
    )

    from starlette.responses import JSONResponse
    return JSONResponse(
        status_code=202,
        content={
            "job_id": job["id"],
            "agent_id": agent_id,
            "dataset_id": dataset_id,
            "status": job["status"],
            "created_at": job["created_at"],
        },
    )

@app.post("/api/workflow/eval/run/{eval_run_id}/llm-judge")
def run_llm_judge(eval_run_id: str):
    """Run LLM-as-judge on an evaluation run."""
    return _evaluator.run_llm_judge(eval_run_id)

@app.get("/api/workflow/eval/runs")
def list_eval_runs():
    """List all evaluation runs."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM eval_runs ORDER BY created_at DESC LIMIT 50"
    ).fetchall()
    return {"runs": [dict(r) for r in rows]}

@app.get("/api/workflow/eval/runs/{eval_run_id}")
def get_eval_run(eval_run_id: str):
    """Get full evaluation run with results."""
    conn = get_connection()
    run = conn.execute("SELECT * FROM eval_runs WHERE id = ?", (eval_run_id,)).fetchone()
    if not run:
        raise HTTPException(404, "Eval run not found")
    results = conn.execute(
        "SELECT er.*, dei.input_text, dei.expected_output FROM eval_results er "
        "JOIN eval_items dei ON er.dataset_item_id = dei.id "
        "WHERE er.eval_run_id = ? ORDER BY er.created_at", (eval_run_id,)
    ).fetchall()
    return {"run": dict(run), "results": [dict(r) for r in results]}

@app.get("/api/workflow/eval/compare/{run_a}/{run_b}")
def compare_eval_runs(run_a: str, run_b: str):
    """Compare two evaluation runs."""
    return _evaluator.compare_runs(run_a, run_b)


# ── Invoice Notices ────────────────────────────────────────────────

from invoice_notices import InvoiceNoticeGenerator as _InvoiceNotices

_notice_gen: _InvoiceNotices | None = None


def _get_notices() -> _InvoiceNotices:
    global _notice_gen
    if _notice_gen is None:
        _notice_gen = _InvoiceNotices()
    return _notice_gen


@app.post("/api/notices/overlay")
def generate_overlay(data: dict):
    """Generate HTML overlay for an overdue invoice."""
    html = _get_notices().generate_overlay(
        overdue_days=data.get("overdue_days", 0),
        amount_cents=data.get("amount_cents", 0),
        business_name=data.get("business_name", ""),
        invoice_number=data.get("invoice_number", ""),
        payment_link=data.get("payment_link", ""),
        notice_level=data.get("notice_level", 1),
    )
    return {"html": html}


@app.post("/api/notices/printable")
def generate_printable_notice(data: dict):
    """Generate a full-page printable overdue notice."""
    notice = _get_notices().generate_printable_notice(
        debtor_name=data.get("debtor_name", ""),
        debtor_address=data.get("debtor_address", ""),
        business_name=data.get("business_name", ""),
        invoice_number=data.get("invoice_number", ""),
        amount_cents=data.get("amount_cents", 0),
        days_overdue=data.get("days_overdue", 0),
        notice_level=data.get("notice_level", 1),
    )
    return notice


@app.post("/api/notices/sticker")
def generate_sticker(data: dict):
    """Generate a 3x5 reminder sticker HTML for printing."""
    html = _get_notices().generate_reminder_sticker_html(
        amount_cents=data.get("amount_cents", 0),
        days_overdue=data.get("days_overdue", 0),
        business_name=data.get("business_name", ""),
        escalation_date=data.get("escalation_date", ""),
    )
    return {"html": html}


@app.post("/api/notices/batch")
def batch_generate_notices(data: dict):
    """Generate printable notices for multiple debtors."""
    debtors = data.get("debtors", [])
    paths = _get_notices().batch_generate_notices(debtors)
    return {"paths": paths, "count": len(paths)}


# ── Platform Primitives API Routes ──────────────────────────────
# Source Registry, Seen Store, Scoring Engine, Run Memory, Stories


@app.get("/api/workflow/agents/{agent_id}/sources")
def list_agent_sources(agent_id: str):
    """List curated sources for a workflow agent."""
    try:
        from source_registry import SourceRegistry
        reg = SourceRegistry()
        sources = reg.list(workflow_id=agent_id)
        return {"sources": sources}
    except Exception as e:
        return {"sources": [], "error": str(e)}


@app.post("/api/workflow/agents/{agent_id}/sources")
def create_agent_source(agent_id: str, data: dict):
    """Add a curated source for a workflow agent."""
    try:
        from source_registry import SourceRegistry
        reg = SourceRegistry()
        result = reg.create(
            name=data.get("name", ""),
            feed_url=data.get("feed_url", ""),
            domain=data.get("domain", ""),
            authority_tier=data.get("authority_tier", "C"),
            workflow_id=agent_id,
            fetch_interval_mins=data.get("fetch_interval_mins", 1440),
        )
        return result
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(400, str(e))


@app.delete("/api/workflow/agents/{agent_id}/sources/{source_id}")
def delete_agent_source(agent_id: str, source_id: str):
    """Remove a curated source."""
    try:
        from source_registry import SourceRegistry
        reg = SourceRegistry()
        ok = reg.delete(source_id)
        return {"deleted": ok}
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(404, str(e))


@app.post("/api/workflow/agents/{agent_id}/sources/fetch")
def fetch_agent_sources(agent_id: str):
    """Fetch all enabled sources (RSS) for a workflow agent."""
    try:
        from source_registry import SourceRegistry
        reg = SourceRegistry()
        results = reg.fetch_all(workflow_id=agent_id)
        return {"sources_fetched": len(results.get("articles", [])), "sources_checked": results.get("sources_checked", 0)}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/workflow/agents/{agent_id}/scoring-rules")
def list_scoring_rules(agent_id: str, step_id: str = ""):
    """Get scoring rules for an agent workflow."""
    try:
        from scoring_engine import ScoringEngine
        engine = ScoringEngine()
        step_id = step_id or None
        rules = engine.get_rules(agent_id, step_id=step_id)
        return {"rules": rules}
    except Exception as e:
        return {"rules": [], "error": str(e)}


@app.put("/api/workflow/agents/{agent_id}/scoring-rules")
def set_scoring_rules(agent_id: str, data: dict):
    """Set scoring rules for an agent workflow. Body: {rules: [...], step_id?: str}"""
    try:
        from scoring_engine import ScoringEngine
        engine = ScoringEngine()
        rules = data.get("rules", [])
        step_id = data.get("step_id")
        result = engine.set_rules(agent_id, rules, step_id=step_id)
        return result
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(400, str(e))


@app.get("/api/workflow/memory/{workflow_id}")
def list_memory_keys(workflow_id: str, tag_filter: str = ""):
    """List run memory keys for a workflow."""
    try:
        from run_memory import RunMemory
        mem = RunMemory()
        keys = mem.list_keys(workflow_id, tag_filter=tag_filter)
        data = {}
        for key in keys:
            data[key] = mem.get(workflow_id, key)
        return {"keys": data, "count": len(data)}
    except Exception as e:
        return {"keys": {}, "count": 0, "error": str(e)}


@app.post("/api/workflow/memory/{workflow_id}")
def set_memory_key(workflow_id: str, data: dict):
    """Set a run memory key. Body: {key, value, run_id?, tags?}"""
    try:
        from run_memory import RunMemory
        mem = RunMemory()
        result = mem.set(
            workflow_id=workflow_id,
            key=data["key"],
            value=data["value"],
            run_id=data.get("run_id"),
            tags=data.get("tags", ""),
        )
        return result
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(400, str(e))


@app.get("/api/workflow/seen/{workflow_id}/stats")
def seen_store_stats(workflow_id: str):
    """Get seen store stats for a workflow."""
    try:
        from seen_store import SeenStore
        store = SeenStore()
        stats = store.get_stats(workflow_id)
        return stats
    except Exception as e:
        return {"total": 0, "unique": 0, "seen_multiple": 0, "error": str(e)}


@app.get("/api/workflow/stories")
def list_stories(workflow_id: str = "", tag_filter: str = "", limit: int = 50):
    """List tracked stories across runs."""
    try:
        from stories_engine import StoriesEngine
        engine = StoriesEngine()
        return {"stories": engine.list_stories(workflow_id, tag_filter=tag_filter, limit=limit)}
    except ImportError:
        return {"stories": [], "error": "Stories engine not yet available"}
    except Exception as e:
        return {"stories": [], "error": str(e)}


# ── Delivery Routes ─────────────────────────────────────────────────


@app.post("/api/delivery/send")
def delivery_send(data: dict):
    """Send an email via the SMTP delivery service.

    Body::

        {
            "to": "recipient@example.com",
            "subject": "Hello",
            "html_body": "<h1>Hello</h1>",
            "text_body": "Hello plain text",
            "credential_ref": "my-smtp-creds",
            "run_id": "optional-run-id"
        }

    Returns the delivery result with message_id and status.
    """
    try:
        from delivery.smtp_engine import SMTPEngine
        from delivery.provider_router import ProviderRouter
        from delivery.tracker import DeliveryTracker

        to = data.get("to", "")
        if not to:
            from fastapi import HTTPException
            raise HTTPException(400, "to is required")

        subject = data.get("subject", "")
        html_body = data.get("html_body", "")
        text_body = data.get("text_body")
        credential_ref = data.get("credential_ref", "default-smtp")
        run_id = data.get("run_id", "api-direct")

        # Resolve SMTP config from credential reference
        router = ProviderRouter()
        config = router.resolve(credential_ref)

        # Send the email
        engine = SMTPEngine()
        result = engine.send(
            to=to,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            config=config,
        )

        # Track the delivery
        DeliveryTracker.record(
            run_id=run_id,
            to=to,
            subject=subject,
            provider=result.get("provider", "smtp"),
            status=result.get("status", "failed"),
            message_id=result.get("message_id", ""),
            error=result.get("error", ""),
        )

        return result
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(500, str(e))


@app.get("/api/delivery/status/{message_id}")
def delivery_status(message_id: str):
    """Get the delivery status for a given message ID."""
    try:
        from delivery.tracker import DeliveryTracker
        status = DeliveryTracker.get_status(message_id)
        return {"message_id": message_id, "status": status}
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(500, str(e))


@app.get("/api/delivery/log")
def delivery_log(run_id: str = "", limit: int = 50, offset: int = 0):
    """Get delivery log entries, optionally filtered by run_id."""
    try:
        from delivery.tracker import DeliveryTracker
        if run_id:
            logs = DeliveryTracker.get_log(run_id, limit=limit, offset=offset)
        else:
            # Return all logs if no run_id filter
            # (get_log requires run_id, so we fall back to a basic query)
            from database import get_connection
            conn = get_connection()
            rows = conn.execute(
                "SELECT * FROM wf_delivery_log ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            logs = [dict(r) for r in rows]
        return {"logs": logs, "count": len(logs)}
    except Exception as e:
        return {"logs": [], "count": 0, "error": str(e)}


# ── Prompt Pattern Library Routes ──────────────────────────────────


@app.get("/api/patterns")
def list_patterns(category: str = ""):
    """List all prompt patterns."""
    from patterns.engine import PatternRegistry
    reg = PatternRegistry()
    cat = category or None
    return {"patterns": reg.list(category=cat)}


@app.post("/api/patterns")
def create_pattern(data: dict):
    """Create a new prompt pattern."""
    from patterns.engine import PatternRegistry
    reg = PatternRegistry()
    try:
        pattern = reg.register(data)
        return pattern
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(400, str(e))


@app.put("/api/patterns/{pattern_id}")
def update_pattern(pattern_id: str, data: dict):
    """Update an existing prompt pattern."""
    from patterns.engine import PatternRegistry
    reg = PatternRegistry()
    try:
        pattern = reg.update(pattern_id, data)
        return pattern
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(404, str(e))


@app.delete("/api/patterns/{pattern_id}")
def delete_pattern(pattern_id: str):
    """Delete a prompt pattern."""
    from patterns.engine import PatternRegistry
    reg = PatternRegistry()
    ok = reg.delete(pattern_id)
    return {"deleted": ok}


@app.get("/api/patterns/{pattern_id}/render")
def render_pattern(pattern_id: str, context: str = "", data: str = ""):
    """Test-render a prompt pattern with optional context and data."""
    from patterns.engine import PatternRenderer
    renderer = PatternRenderer()
    try:
        ctx = json.loads(context) if context else {}
        d = json.loads(data) if data else {}
        result = renderer.render(pattern_id, ctx, d)
        return result
    except json.JSONDecodeError as e:
        from fastapi import HTTPException
        raise HTTPException(400, f"Invalid JSON: {e}")
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(404, str(e))


@app.get("/api/patterns/{pattern_id}/history")
def pattern_history(pattern_id: str):
    """Get version history for a prompt pattern."""
    from patterns.engine import PatternRegistry
    reg = PatternRegistry()
    history = reg.get_version_history(pattern_id)
    return {"history": history, "count": len(history)}


@app.get("/api/patterns/{pattern_id}/sandbox")
def pattern_sandbox(pattern_id: str):
    """Serve the Pattern Sandbox HTML page."""
    from fastapi.responses import HTMLResponse
    from pathlib import Path
    html_path = Path(__file__).parent.parent / "current" / "patterns-sandbox.html"
    if html_path.exists():
        html = html_path.read_text()
        # Pre-populate the pattern selector by passing pattern_id in URL
        return HTMLResponse(html)
    from fastapi import HTTPException
    raise HTTPException(404, "Sandbox page not found")


@app.post("/api/patterns/{pattern_id}/render-test")
def pattern_render_test(pattern_id: str, data: dict):
    """Render a pattern with test data, returning schema + warnings.

    Body::

        {
            "test_data": {...},       # Signals, sources, citations
            "context": {...},          # Context variables
            "version": 3,             # Optional version pin
            "validate_output": {...}   # Optional: validate this output instead
        }
    """
    from patterns.sandbox import PatternSandbox
    from patterns.renderer import PatternRenderer
    from patterns.engine import OutputSchemaValidator

    sandbox = PatternSandbox()

    # If validate_output is provided, do validation instead
    validate_output = data.get("validate_output")
    if validate_output is not None:
        validation = sandbox.validate_sandbox_output(validate_output, pattern_id)
        return validation

    test_data = data.get("test_data", {})
    context = data.get("context", {})
    version = data.get("version")

    try:
        result = sandbox.render_test(pattern_id, test_data=test_data, context=context)
        if "error" in result:
            from fastapi import HTTPException
            raise HTTPException(404, result["error"])
        return result
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(404, str(e))


@app.post("/api/patterns/{pattern_id}/publish-version")
def pattern_publish_version(pattern_id: str, data: dict):
    """Publish a new version of a prompt pattern.

    Body::

        {
            "name": "...",            # Optional: new name
            "description": "...",     # Optional: new description
            "sections": [...],        # Optional: updated sections
            "output_schema": {...},   # Optional: updated output schema
            "citation_rules": {...},  # Optional: updated citation rules
            "brand_voice": "...",     # Optional: updated brand voice
            "category": "...",        # Optional: new category
            "tags": [...]             # Optional: new tags
        }
    """
    from patterns.engine import PatternRegistry
    from fastapi import HTTPException

    reg = PatternRegistry()
    existing = reg.get(pattern_id)
    if not existing:
        raise HTTPException(404, f"Pattern not found: {pattern_id}")

    try:
        updated = reg.update(pattern_id, data)
        return {
            "pattern": updated,
            "old_version": existing["version"],
            "new_version": updated["version"],
            "message": f"Pattern '{pattern_id}' published as v{updated['version']}",
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/patterns/{pattern_id}/versions")
def pattern_versions(pattern_id: str):
    """List all versions of a prompt pattern."""
    from patterns.engine import PatternRegistry
    from fastapi import HTTPException

    reg = PatternRegistry()
    existing = reg.get(pattern_id)
    if not existing:
        raise HTTPException(404, f"Pattern not found: {pattern_id}")

    # Get version history
    history = reg.get_version_history(pattern_id)
    return {
        "pattern_id": pattern_id,
        "current_version": existing["version"],
        "versions": history,
        "count": len(history),
    }


# ── Connectors API ──────────────────────────────────────────────────


@app.get("/api/connectors")
def list_connectors_api():
    """List all available connectors."""
    from connectors import list_connectors
    from connectors.registry import ConnectorRegistry

    names = list_connectors()
    schemas = []
    for name in names:
        cls = ConnectorRegistry.get(name)
        if cls:
            schemas.append(cls.get_config_schema())
    return {"connectors": schemas, "count": len(schemas)}


@app.post("/api/connectors/fetch")
def fetch_connector(data: dict):
    """Fetch data from a connector by name + config.

    Body: {
        "name": "reddit",
        "config": {"subreddit": "python", "limit": 5}
    }
    """
    from connectors.registry import ConnectorRegistry

    name = data.get("name", "")
    config = data.get("config", {})
    if not name:
        raise HTTPException(400, "name is required")

    try:
        connector = ConnectorRegistry.create(name, config)
    except KeyError:
        raise HTTPException(404, f"Unknown connector '{name}'. Available: {ConnectorRegistry.list()}")

    try:
        results = connector.fetch()
        return {"connector": name, "count": len(results), "results": results}
    except Exception as e:
        raise HTTPException(500, f"Fetch failed: {e}")


@app.get("/api/connectors/{name}/schema")
def get_connector_schema(name: str):
    """Get a connector's config schema."""
    from connectors.registry import ConnectorRegistry

    cls = ConnectorRegistry.get(name)
    if cls is None:
        raise HTTPException(404, f"Unknown connector '{name}'. Available: {ConnectorRegistry.list()}")

    return cls.get_config_schema()


# ── Parser Engine Routes ────────────────────────────────────────────


@app.post("/api/parser/parse")
def parser_parse(data: dict):
    """Parse a response body with the given parser config.

    Body::

        {
            "response_body": "... raw text ...",
            "parser_config": {
                "type": "rss|jsonpath|xpath|html|id_list",
                "config": { ... }
            }
        }

    Returns ``{items: [...], errors: [...]}``.
    """
    from parser.engine import ParserEngine

    response_body = data.get("response_body", "")
    parser_config = data.get("parser_config", {})

    if not response_body:
        from fastapi import HTTPException
        raise HTTPException(400, "response_body is required")
    if not parser_config:
        from fastapi import HTTPException
        raise HTTPException(400, "parser_config is required")

    engine = ParserEngine()
    try:
        result = engine.parse(response_body, parser_config)
        return result
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(500, f"Parse failed: {e}")


@app.post("/api/parser/extract-items")
def parser_extract_items(data: dict):
    """Full parse + item extraction pipeline.

    Body::

        {
            "response_body": "... raw text ...",
            "parser_config": {
                "type": "rss|jsonpath|xpath|html|id_list",
                "config": { ... }
            },
            "normalize": true
        }

    Returns normalized items with standard fields (url, title, content,
    author, published_date, source_fields).
    """
    from parser.engine import ParserEngine

    response_body = data.get("response_body", "")
    parser_config = data.get("parser_config", {})

    if not response_body:
        from fastapi import HTTPException
        raise HTTPException(400, "response_body is required")
    if not parser_config:
        from fastapi import HTTPException
        raise HTTPException(400, "parser_config is required")

    engine = ParserEngine()
    try:
        result = engine.parse(response_body, parser_config)
        return {
            "items": result.get("items", []),
            "item_count": len(result.get("items", [])),
            "errors": result.get("errors", []),
        }
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(500, f"Extract failed: {e}")


# ── Content Source Routes ────────────────────────────────────────────


@app.get("/api/content-sources")
def list_content_sources():
    """List all YAML-driven content sources."""
    from source_registry import ContentSourceManager
    csm = ContentSourceManager()
    sources = csm.list()
    return {"sources": sources, "count": len(sources)}


@app.post("/api/content-sources")
def create_content_source(data: dict):
    """Create a content source from a YAML definition dict.

    Body::
        {
            "name": "Hacker News (Top)",
            "type": "http_api",
            "url": "https://hacker-news.firebaseio.com/v0/topstories.json",
            "response_type": "json_array",
            "fields": {"url": "url", "title": "title", "author": "by"},
            "authority_tier": "A",
            "interval_minutes": 30
        }
    """
    from source_registry import ContentSourceManager
    csm = ContentSourceManager()
    try:
        source = csm.create_from_yaml(data)
        return source
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.put("/api/content-sources/{source_id}")
def update_content_source(source_id: str, data: dict):
    """Update a content source from a YAML definition dict.

    Body: Same structure as create, with updated fields.
    """
    from source_registry import ContentSourceManager
    csm = ContentSourceManager()
    try:
        source = csm.update(source_id, data)
        return source
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.delete("/api/content-sources/{source_id}")
def delete_content_source(source_id: str):
    """Delete a content source and its items."""
    from source_registry import ContentSourceManager
    csm = ContentSourceManager()
    deleted = csm.delete(source_id)
    if not deleted:
        raise HTTPException(404, f"Content source {source_id} not found")
    return {"deleted": True, "source_id": source_id}


@app.post("/api/content-sources/{source_id}/fetch")
def test_fetch_content_source(source_id: str):
    """Test-fetch a content source: fetch URLs and parse responses.

    Returns items and also stores them in wf_content_source_items.
    """
    from source_registry import ContentSourceManager
    from fetcher.engine import FetcherEngine
    from parser.engine import ParserEngine

    csm = ContentSourceManager()
    try:
        items = csm.fetch_and_parse(
            source_id=source_id,
            fetcher_engine=FetcherEngine(),
            parser_engine=ParserEngine(),
        )
        return {"source_id": source_id, "items": items, "item_count": len(items)}
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"Fetch failed: {e}")


@app.post("/api/content-sources/import")
def bulk_import_content_sources(data: dict):
    """Bulk import content sources from a YAML file's ``content_sources`` section.

    Body::
        {
            "sources": [
                {
                    "name": "Hacker News (Top)",
                    "type": "http_api",
                    "url": "...",
                    ...
                },
                ...
            ]
        }

    Alternatively, pass a ``filepath`` to read a YAML file from disk::

        {"filepath": "/path/to/sources.yaml"}
    """
    from source_registry import ContentSourceManager

    filepath = data.get("filepath")
    if filepath:
        import yaml
        from pathlib import Path
        path = Path(filepath)
        if not path.exists():
            raise HTTPException(404, f"File not found: {filepath}")
        try:
            yaml_data = yaml.safe_load(path.read_text("utf-8"))
            sources_defs = yaml_data.get("content_sources", yaml_data.get("sources", []))
            if not sources_defs:
                raise HTTPException(400, f"No sources or content_sources section in {filepath}")
        except Exception as e:
            raise HTTPException(400, f"Failed to parse YAML: {e}")
    else:
        sources_defs = data.get("sources", [])

    if not sources_defs:
        raise HTTPException(400, "No sources provided")

    csm = ContentSourceManager()
    results = csm.bulk_import(sources_defs)
    return {"results": results, "total": len(results)}


# ── Verifier Routes ─────────────────────────────────────────────────

# In-memory verifier statistics (resets on server restart)
_verifier_stats: dict[str, Any] = {
    "total_graded": 0,
    "total_passed": 0,
    "total_failed": 0,
}


@app.post("/api/verifier/verify")
def verifier_verify(data: dict):
    """Run full claim verification on an output.

    Body:
        {
            "output_text": "...",
            "citation_map": {"S001": {"url": "...", "content": "...", "title": "..."}, ...},
            "items": [{"citation_id": "S001", "url": "...", "body_extracted": "...", "title": "..."}, ...]
        }
    """
    from verifier.engine import ClaimVerifier

    output_text = data.get("output_text", "")
    if not output_text:
        raise HTTPException(400, "output_text is required")

    citation_map = data.get("citation_map")
    items = data.get("items")

    verifier = ClaimVerifier(
        llm_endpoint=os.environ.get("LLM_ENDPOINT", "http://localhost:7999/v1/chat/completions"),
        llm_model=os.environ.get("LLM_MODEL", "gemma-12b"),
    )

    result = verifier.verify_claims(
        output_text=output_text,
        citation_map=citation_map,
        items=items if not citation_map else None,
    )

    # Update stats
    _verifier_stats["total_graded"] += len(result.get("claims", []))
    if result.get("passed", False):
        _verifier_stats["total_passed"] += 1
    else:
        _verifier_stats["total_failed"] += 1

    return result


@app.post("/api/verifier/grade")
def verifier_grade(data: dict):
    """Grade a single claim-citation pair.

    Body:
        {
            "claim_text": "The sky is blue.",
            "citation_id": "S001",
            "cited_url": "https://example.com/sky",
            "cited_content": "The sky is blue during the day.",
            "cited_title": "Sky Facts"
        }
    """
    from verifier.grader import CitationGrader

    claim_text = data.get("claim_text", "")
    citation_id = data.get("citation_id", "")
    cited_url = data.get("cited_url", "")

    if not claim_text or not citation_id or not cited_url:
        raise HTTPException(400, "claim_text, citation_id, and cited_url are required")

    cited_content = data.get("cited_content", "")
    cited_title = data.get("cited_title")

    grader = CitationGrader(
        llm_endpoint=os.environ.get("LLM_ENDPOINT", "http://localhost:7999/v1/chat/completions"),
        llm_model=os.environ.get("LLM_MODEL", "gemma-12b"),
    )

    result = grader.grade_claim(
        claim_text=claim_text,
        citation_id=citation_id,
        cited_url=cited_url,
        cited_content=cited_content,
        cited_title=cited_title,
    )

    # Update stats
    _verifier_stats["total_graded"] += 1

    return result


@app.get("/api/verifier/stats")
def verifier_stats():
    """Get verifier statistics (total graded, pass rate)."""
    total = _verifier_stats["total_graded"]
    total_decisions = _verifier_stats["total_passed"] + _verifier_stats["total_failed"]
    pass_rate = (
        round(_verifier_stats["total_passed"] / total_decisions, 4)
        if total_decisions > 0
        else 0.0
    )
    return {
        "total_graded": _verifier_stats["total_graded"],
        "total_passed": _verifier_stats["total_passed"],
        "total_failed": _verifier_stats["total_failed"],
        "total_decisions": total_decisions,
        "pass_rate": pass_rate,
    }


# ── Archive Routes ──────────────────────────────────────────────────


@app.post("/api/archive/store")
def archive_store(data: dict):
    """Store a newsletter edition.

    Body::

        {
            "edition_id": "nl-001",
            "subject": "Daily Signal: ...",
            "body_html": "<h1>...</h1>",
            "body_markdown": "# ...",
            "run_id": "run-abc123",
            "metadata": {"citation_count": 5, "source_count": 3}
        }
    """
    from archive.engine import ArchiveEngine
    engine = ArchiveEngine()
    result = engine.store(
        edition_id=data["edition_id"],
        subject=data.get("subject", ""),
        body_html=data.get("body_html", ""),
        body_markdown=data.get("body_markdown", ""),
        run_id=data.get("run_id", ""),
        metadata=data.get("metadata", {}),
    )
    return result


@app.get("/api/archive/list")
def archive_list(limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0)):
    """List archived editions."""
    from archive.engine import ArchiveEngine
    engine = ArchiveEngine()
    editions = engine.list(limit=limit, offset=offset)
    return {"editions": editions, "count": len(editions), "limit": limit, "offset": offset}


@app.get("/api/archive/{edition_id}")
def archive_get(edition_id: str):
    """Get a single archived edition."""
    from archive.engine import ArchiveEngine
    engine = ArchiveEngine()
    edition = engine.get(edition_id)
    if not edition:
        raise HTTPException(404, f"Edition {edition_id} not found")
    return edition


@app.get("/api/archive/rss")
def archive_rss():
    """RSS feed of past editions."""
    from archive.engine import ArchiveEngine
    from archive.rss import RSSFeed
    engine = ArchiveEngine()
    editions = engine.list(limit=100, offset=0)
    rss = RSSFeed().generate(editions)
    from fastapi.responses import Response
    return Response(content=rss, media_type="application/rss+xml")


@app.get("/api/archive/latest")
def archive_latest():
    """Get the latest archived edition."""
    from archive.engine import ArchiveEngine
    engine = ArchiveEngine()
    edition = engine.get_latest()
    if not edition:
        raise HTTPException(404, "No editions archived yet")
    return edition


@app.post("/api/archive/rebuild")
def archive_rebuild():
    """Rebuild the archive index.html."""
    from archive.index import ArchiveIndex
    from archive.engine import ArchiveEngine
    engine = ArchiveEngine()
    index = ArchiveIndex(archive_dir=str(engine.archive_dir))
    path = index.rebuild()
    return {"path": path, "status": "ok"}


# ── Edition Registry Routes ───────────────────────────────────────────


@app.post("/api/editions/create")
def edition_create(data: dict):
    """Create an edition record.

    Body::

        {
            "workflow_id": "wf-001",
            "run_id": "run-abc",
            "subject": "Daily Signal",
            "source_count": 5,
            "item_count": 20,
            "total_tokens": 10000,
            "duration_seconds": 45.5
        }
    """
    from registry.engine import EditionRegistry
    registry = EditionRegistry()
    edition = registry.create(
        workflow_id=data.get("workflow_id", ""),
        run_id=data.get("run_id", ""),
        subject=data.get("subject", ""),
        source_count=int(data.get("source_count", 0)),
        item_count=int(data.get("item_count", 0)),
        total_tokens=int(data.get("total_tokens", 0)),
        duration_seconds=float(data.get("duration_seconds", 0.0)),
    )
    return edition


@app.get("/api/editions/list")
def edition_list(limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0)):
    """List editions, newest first."""
    from registry.engine import EditionRegistry
    registry = EditionRegistry()
    editions = registry.list(limit=limit, offset=offset)
    return {"editions": editions, "count": len(editions), "limit": limit, "offset": offset}


@app.get("/api/editions/{edition_id}")
def edition_get(edition_id: str):
    """Get a single edition by ID."""
    from registry.engine import EditionRegistry
    registry = EditionRegistry()
    edition = registry.get(edition_id)
    if not edition:
        raise HTTPException(404, f"Edition {edition_id} not found")
    return edition


@app.get("/api/editions/compare/{id_a}/{id_b}")
def edition_compare(id_a: str, id_b: str):
    """Compare two editions."""
    from registry.comparer import EditionComparer
    comparer = EditionComparer()
    result = comparer.compare(id_a, id_b)
    return result


@app.get("/api/editions/stats/{edition_id}")
def edition_stats(edition_id: str):
    """Compute statistics for an edition."""
    from registry.stats import EditionStats
    stats = EditionStats()
    result = stats.compute(edition_id)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@app.get("/api/editions/trend")
def edition_trend():
    """Compute trend statistics across all editions."""
    from registry.stats import EditionStats
    stats = EditionStats()
    result = stats.compute_trend()
    return result


@app.get("/api/editions/latest")
def edition_latest():
    """Get the latest edition."""
    from registry.engine import EditionRegistry
    registry = EditionRegistry()
    edition = registry.get_latest()
    if not edition:
        raise HTTPException(404, "No editions found")
    return edition


# ── Extractor Routes ──────────────────────────────────────────────


@app.post("/api/extractor/extract")
def extract_content(data: dict):
    """Extract readable content from HTML.

    Body::
        {
            "url": "https://example.com/article",
            "html_body": "<html>...</html>"  # optional if url is provided
        }

    If ``html_body`` is not provided, the server will try to fetch the URL
    using FetcherEngine first.
    """
    url = data.get("url", "")
    html_body = data.get("html_body", data.get("body_html", ""))

    if not html_body and url:
        # Fetch the URL
        from fetcher.engine import FetcherEngine
        fetcher = FetcherEngine()
        fetch_result = fetcher.fetch(url)
        if fetch_result.get("error"):
            raise HTTPException(502, f"Fetch failed: {fetch_result['error']}")
        html_body = fetch_result.get("body_text", "")

    if not html_body:
        raise HTTPException(400, "html_body required (or url with fetchable content)")

    from extractor.engine import ContentExtractor
    engine = ContentExtractor()
    result = engine.extract(html_body, url)
    return result


@app.post("/api/extractor/batch")
def extract_batch(data: dict):
    """Batch extract readable content from multiple HTML sources.

    Body::
        {
            "items": [
                {"url": "...", "html_body": "..."},
                {"url": "...", "html_body": "..."}
            ],
            "max_workers": 5
        }
    """
    items = data.get("items", [])
    max_workers = int(data.get("max_workers", 5))

    if not items:
        raise HTTPException(400, "items required")

    from extractor.batch import BatchExtractor
    engine = BatchExtractor()
    results = engine.extract_batch(items, max_workers=max_workers)
    return {"results": results, "item_count": len(results)}


@app.post("/api/extractor/metadata")
def extract_metadata(data: dict):
    """Extract metadata from HTML (Open Graph, Twitter Cards, meta tags, JSON-LD).

    Body::
        {
            "url": "https://example.com/article",
            "html_body": "<html>...</html>"
        }
    """
    url = data.get("url", "")
    html_body = data.get("html_body", data.get("body_html", ""))

    if not html_body:
        raise HTTPException(400, "html_body required")

    from extractor.metadata import MetadataExtractor
    engine = MetadataExtractor()
    result = engine.extract_metadata(html_body, url)
    return result


# ── Entity Routes ─────────────────────────────────────────────────


@app.post("/api/entities/extract")
def entities_extract(data: dict):
    """Extract entities from text.

    Body::
        {
            "text": "OpenAI announced ChatGPT...",
            "source_item_id": "optional-item-id"
        }
    """
    text = data.get("text", "")
    if not text:
        raise HTTPException(400, "text required")
    from entities.engine import EntityExtractor
    engine = EntityExtractor()
    results = engine.extract(text, source_item_id=data.get("source_item_id"))
    return {"entities": results, "count": len(results)}


@app.post("/api/entities/batch-extract")
def entities_batch_extract(data: dict):
    """Batch extract entities from content items.

    Body::
        {
            "items": [{"id": "...", "body_extracted": "..."}],
            "text_field": "body_extracted"
        }
    """
    items = data.get("items", [])
    if not items:
        raise HTTPException(400, "items required")
    text_field = data.get("text_field", "body_extracted")
    from entities.engine import EntityExtractor
    engine = EntityExtractor()
    result = engine.extract_batch(items, text_field=text_field)
    return result


@app.post("/api/entities/extract-keywords")
def entities_extract_keywords(data: dict):
    """Extract keywords from text.

    Body::
        {
            "text": "...",
            "max_keywords": 20
        }
    """
    text = data.get("text", "")
    if not text:
        raise HTTPException(400, "text required")
    max_keywords = int(data.get("max_keywords", 20))
    from entities.engine import EntityExtractor
    engine = EntityExtractor()
    keywords = engine.extract_keywords(text, max_keywords=max_keywords)
    return {"keywords": keywords, "count": len(keywords)}


@app.get("/api/entities/dictionary")
def entities_dictionary_list(entity_type: str = None, category: str = None):
    """List known entities in the dictionary."""
    from entities.dictionary import EntityDictionary
    d = EntityDictionary()
    entities = d.list(entity_type=entity_type, category=category)
    return {"entities": entities, "count": len(entities)}


@app.post("/api/entities/dictionary")
def entities_dictionary_add(data: dict):
    """Add an entity to the dictionary.

    Body::
        {
            "entity": "OpenAI",
            "type": "company",
            "aliases": "",
            "category": "AI",
            "authority_tier": "A"
        }
    """
    entity = data.get("entity", "")
    entity_type = data.get("type", "")
    if not entity or not entity_type:
        raise HTTPException(400, "entity and type required")
    if entity_type not in ("company", "product", "person", "concept", "org"):
        raise HTTPException(400, f"Invalid type: {entity_type}")
    from entities.dictionary import EntityDictionary
    d = EntityDictionary()
    result = d.add(
        entity=entity,
        entity_type=entity_type,
        aliases=data.get("aliases", ""),
        category=data.get("category", ""),
        authority_tier=data.get("authority_tier", "B"),
    )
    return result


@app.post("/api/entities/dictionary/seed")
def entities_dictionary_seed():
    """Seed the dictionary with default AI entities."""
    from entities.dictionary import EntityDictionary
    d = EntityDictionary()
    d.seed_defaults()
    count = len(d.list())
    return {"status": "ok", "count": count}


@app.post("/api/entities/score")
def entities_score(data: dict):
    """Score items by entity matches.

    Body::
        {
            "items": [{"id": "...", "body_extracted": "...", "score": 0.0}],
            "text_field": "body_extracted"
        }
    """
    items = data.get("items", [])
    if not items:
        raise HTTPException(400, "items required")
    from entities.engine import EntityExtractor
    engine = EntityExtractor()
    scored = engine.score_by_entity(items)
    return {"scored_items": scored, "count": len(scored)}


# ── Citation Routes ──────────────────────────────────────────────


@app.post("/api/citations/generate")
def citations_generate(data: dict):
    """Assign sequential citation IDs to content items.

    Body::

        {
            "items": [{"url": "...", "title": "...", "content": "...", "source_id": "..."}],
            "prefix": "S",
            "start_number": 1
        }

    Returns the items with ``citation_id`` added.
    """
    from citation.engine import CitationEngine

    items = data.get("items", [])
    prefix = data.get("prefix", "S")
    start_number = data.get("start_number")

    if not items:
        raise HTTPException(400, "items required")

    engine = CitationEngine()
    result = engine.generate_ids(items, prefix=prefix, start_number=start_number)
    return {"citations": result, "count": len(result)}


@app.get("/api/citations/map")
def citations_get_map(fetch_run_id: str | None = Query(None)):
    """Get the citation map, optionally filtered by fetch_run_id."""
    from citation.engine import CitationEngine

    engine = CitationEngine()
    rows = engine.get_map(fetch_run_id=fetch_run_id)
    return {"citations": rows, "count": len(rows)}


@app.get("/api/citations/{citation_id}")
def citations_resolve(citation_id: str):
    """Resolve a citation ID (e.g. S042) to its full metadata."""
    from citation.engine import CitationEngine

    engine = CitationEngine()
    entry = engine.resolve(citation_id)
    if not entry:
        raise HTTPException(404, f"Citation {citation_id} not found")
    return entry


@app.post("/api/citations/export")
def citations_export(data: dict):
    """Export citation map for prompt context.

    Body::

        {
            "fetch_run_id": "run-001",  # optional
            "format": "prompt"  # "prompt" or "dict"
        }

    Returns ``{S001: {url, title}}`` in dict format,
    or a string like ``S001: url "Title"`` in prompt format.
    """
    from citation.engine import CitationEngine
    from citation.map import CitationMap

    fetch_run_id = data.get("fetch_run_id")
    output_format = data.get("format", "prompt")

    engine = CitationEngine()
    raw_map = engine.get_map(fetch_run_id=fetch_run_id)
    cmap = CitationMap.build_map(raw_map)

    if output_format == "dict":
        exported = engine.export_map(fetch_run_id=fetch_run_id)
    else:
        exported = CitationMap.format_for_prompt(cmap)

    return {"export": exported, "count": len(raw_map), "format": output_format}


@app.post("/api/citations/validate")
def citations_validate(data: dict):
    """Validate an output text against a citation map.

    Body::

        {
            "output_text": "Claim [S001] here and [S002] there.",
            "citation_map": {"S001": {"url": "...", "title": "..."}, ...}
        }

    Returns the validation result including ``valid``, ``missing_ids``,
    ``extra_ids``, and ``warnings``.
    """
    from citation.validator import CitationValidator

    output_text = data.get("output_text", "")
    citation_map = data.get("citation_map", {})

    if not output_text:
        raise HTTPException(400, "output_text is required")
    if not citation_map:
        raise HTTPException(400, "citation_map is required")

    validator = CitationValidator()
    result = validator.validate_output(output_text, citation_map)
    return result


@app.post("/api/citations/hallucination-check")
def citations_hallucination_check(data: dict):
    """Run hallucination detection on an output text.

    Body::

        {
            "output_text": "Claim without citation. Another [S001] cited claim.",
            "citation_map": {"S001": {"url": "...", "title": "..."}, ...},
            "item_bodies": []  # optional, reserved for future use
        }

    Returns the detection result including ``hallucinated_claims``,
    ``hallucination_ratio``, ``supported_claims``, ``unverifiable_claims``,
    and ``confidence``.
    """
    from citation.validator import HallucinationDetector

    output_text = data.get("output_text", "")
    citation_map = data.get("citation_map", {})
    item_bodies = data.get("item_bodies")

    if not output_text:
        raise HTTPException(400, "output_text is required")
    if not citation_map:
        raise HTTPException(400, "citation_map is required")

    detector = HallucinationDetector()
    result = detector.detect(output_text, citation_map, item_bodies=item_bodies)
    return result


# ── Story Diff Routes ──────────────────────────────────────────────


@app.post("/api/stories/diff")
def story_diff(data: dict):
    """Diff current edition items vs prior tracked stories.

    Body::

        {
            "workflow_id": "wf-abc123",
            "current_items": [
                {"title": "...", "headline": "...", "body": "...", "urls": [...]},
                ...
            ],
            "threshold": "medium"  # optional, for significance filter
        }

    Returns a list of diffs with type (new/updated/continued/resolved),
    significance, and detailed diffs.
    """
    workflow_id = data.get("workflow_id", "")
    current_items = data.get("current_items", [])
    threshold = data.get("threshold", "medium")

    if not workflow_id:
        raise HTTPException(400, "workflow_id is required")
    if not current_items:
        raise HTTPException(400, "current_items is required")

    from stories.diff_engine import DiffEngine
    from stories_engine import StoriesEngine

    stories_engine = StoriesEngine()
    prior_stories = stories_engine.list_stories(workflow_id=workflow_id, limit=500)

    engine = DiffEngine()
    diffs = engine.diff_stories(current_items, prior_stories, workflow_id)

    # Filter by significance if requested
    if threshold:
        diffs = engine.get_significant_diffs(diffs, threshold)

    # Count by type
    counts: dict[str, int] = {}
    for d in diffs:
        dt = d.get("diff_type", "unknown")
        counts[dt] = counts.get(dt, 0) + 1

    return {
        "diffs": diffs,
        "count": len(diffs),
        "counts": counts,
        "workflow_id": workflow_id,
    }


@app.get("/api/stories/{story_id}/trajectory")
def story_trajectory(story_id: str, workflow_id: str = Query("", description="Workflow ID")):
    """Get trajectory history for a specific story.

    Returns the computed trajectory, edition count, and signal history.
    """
    if not workflow_id:
        raise HTTPException(400, "workflow_id query parameter is required")

    from stories_engine import StoriesEngine
    from stories.trajectory import TrajectoryComputer

    stories_engine = StoriesEngine()
    story = stories_engine.get(workflow_id, story_id)

    if not story:
        raise HTTPException(404, f"Story {story_id} not found in workflow {workflow_id}")

    # Build prior editions from change_log
    import json
    changes_raw = story.get("change_log_json", "[]")
    try:
        changes = json.loads(changes_raw) if isinstance(changes_raw, str) else changes_raw
    except (json.JSONDecodeError, TypeError):
        changes = []
    for entry in changes:
        entry["story_id"] = story["id"]
        entry["seen_in_current"] = True

    computer = TrajectoryComputer()
    trajectory = computer.compute(story, changes)

    return {
        "story_id": story_id,
        "workflow_id": workflow_id,
        "title": story.get("title", ""),
        "trajectory": trajectory,
        "edition_count": story.get("edition_count", 0),
        "signal_strength": story.get("signal_strength", 0.0),
        "change_log": changes,
    }


@app.post("/api/stories/{story_id}/compute-trajectory")
def story_compute_trajectory(story_id: str, data: dict):
    """Recompute trajectory for a story.

    Body::

        {
            "workflow_id": "wf-abc123"
        }

    Recalculates and updates the signal_trajectory field.
    """
    workflow_id = data.get("workflow_id", "")
    if not workflow_id:
        raise HTTPException(400, "workflow_id is required")

    from database import get_connection
    from stories_engine import StoriesEngine
    from stories.trajectory import TrajectoryComputer
    import json

    stories_engine = StoriesEngine()
    story = stories_engine.get(workflow_id, story_id)

    if not story:
        raise HTTPException(404, f"Story {story_id} not found in workflow {workflow_id}")

    changes_raw = story.get("change_log_json", "[]")
    try:
        changes = json.loads(changes_raw) if isinstance(changes_raw, str) else changes_raw
    except (json.JSONDecodeError, TypeError):
        changes = []
    for entry in changes:
        entry["story_id"] = story["id"]
        entry["seen_in_current"] = True

    computer = TrajectoryComputer()
    trajectory = computer.compute(story, changes)

    # Update the story's signal_trajectory field
    conn = get_connection()
    conn.execute(
        "UPDATE wf_stories SET signal_trajectory = ?, updated_at = ? WHERE id = ?",
        (trajectory["trajectory"], datetime.now(timezone.utc).isoformat(), story_id),
    )
    conn.commit()

    return {
        "story_id": story_id,
        "workflow_id": workflow_id,
        "title": story.get("title", ""),
        "trajectory": trajectory,
    }

# ── Cross-Reference API Routes ──────────────────────────────────────────


@app.post("/api/crossref/detect")
def api_crossref_detect(data: dict):
    """Detect cross-references in an item set.

    Body::
        {
            "items": [
                {"id": "1", "entities": ["AI"], "source_name": "HN"},
                {"id": "2", "entities": ["AI", "agents"], "source_name": "Reddit"},
            ]
        }

    Returns list of cross-reference topics that appear in 2+ sources.
    """
    from crossref.engine import CrossReferenceEngine

    items = data.get("items", [])
    if not items:
        raise HTTPException(400, "items list is required")

    engine = CrossReferenceEngine()
    cross_refs = engine.detect(items)
    return {
        "cross_refs": cross_refs,
        "count": len(cross_refs),
    }


@app.post("/api/crossref/boost")
def api_crossref_boost(data: dict):
    """Boost scores based on cross-references.

    Body::
        {
            "items": [{"id": "1", "combined_score": 0.5}, ...],
            "cross_refs": [...],
            "boost_factor": 1.3
        }

    Returns items with updated ``combined_score``.
    """
    from crossref.engine import CrossReferenceEngine

    items = data.get("items", [])
    cross_refs = data.get("cross_refs", [])
    boost_factor = float(data.get("boost_factor", 1.3))

    if not items:
        raise HTTPException(400, "items list is required")

    engine = CrossReferenceEngine()
    boosted = engine.boost_scores(items, cross_refs, boost_factor=boost_factor)
    return {
        "boosted_items": boosted,
        "count": len(boosted),
    }


@app.post("/api/crossref/cluster")
def api_crossref_cluster(data: dict):
    """Cluster items by topic.

    Body::
        {
            "items": [{"id": "1", "entities": ["AI"], "source_name": "HN"}, ...],
            "max_clusters": 10
        }

    Returns topic clusters with items, sources, and keywords.
    """
    from crossref.clusterer import TopicClusterer

    items = data.get("items", [])
    max_clusters = int(data.get("max_clusters", 10))

    if not items:
        raise HTTPException(400, "items list is required")

    clusterer = TopicClusterer()
    clusters = clusterer.cluster(items, max_clusters=max_clusters)
    return {
        "clusters": clusters,
        "count": len(clusters),
    }


# ── Narrative API Routes ──────────────────────────────────────────────


@app.post("/api/narrative/synthesize")
def narrative_synthesize(data: dict):
    """Generate narrative text from story diffs and trajectories.

    Body::

        {
            "story_diffs": [
                {"story_id": "s1", "title": "AI agents", "diff_type": "continued", "signal_strength": 0.7, "edition_count": 3},
            ],
            "trajectories": {"s1": "rising"},
            "prior_narratives": []  # optional
        }

    Returns formatted markdown narrative text.
    """
    story_diffs = data.get("story_diffs", [])
    trajectories = data.get("trajectories", {})
    prior_narratives = data.get("prior_narratives")

    if not story_diffs:
        raise HTTPException(400, "story_diffs is required")

    from narrative.engine import NarrativeEngine
    engine = NarrativeEngine()
    narrative = engine.synthesize(story_diffs, trajectories, prior_narratives=prior_narratives)

    return {
        "narrative": narrative,
        "length": len(narrative),
        "story_count": len(story_diffs),
    }


@app.post("/api/narrative/arcs")
def narrative_detect_arcs(data: dict):
    """Detect narrative arcs across editions.

    Body::

        {
            "stories": [{"id": "s1", "title": "...", "edition_count": 3, "signal_strength": 0.8, ...}],
            "min_arc_length": 2,  # optional
            "prior_arcs": []  # optional, for detect_new_arcs
        }

    Returns list of detected narrative arcs.
    """
    stories = data.get("stories", [])
    min_arc_length = int(data.get("min_arc_length", 2))
    prior_arcs = data.get("prior_arcs")

    if not stories:
        raise HTTPException(400, "stories is required")

    from narrative.arc_detector import ArcDetector
    detector = ArcDetector()

    if prior_arcs:
        arcs = detector.detect_new_arcs(stories, prior_arcs)
    else:
        arcs = detector.detect(stories, min_arc_length=min_arc_length)

    return {
        "arcs": arcs,
        "count": len(arcs),
    }


@app.post("/api/narrative/article-ideas")
def narrative_generate_article_ideas(data: dict):
    """Generate article ideas from signals.

    Body::

        {
            "signals": [{"title": "...", "signal_strength": 0.85, "trajectory": "rising", ...}],
            "narratives": [],  # optional narrative context
            "max_ideas": 5  # optional
        }

    Returns list of article idea dicts with title, rationale, signals_involved, target_audience.
    """
    signals = data.get("signals", [])
    narratives = data.get("narratives")
    max_ideas = int(data.get("max_ideas", 5))

    if not signals:
        raise HTTPException(400, "signals is required")

    from narrative.ideas import ArticleIdeaGenerator
    generator = ArticleIdeaGenerator()
    ideas = generator.generate(signals, narratives=narratives, max_ideas=max_ideas)

    return {
        "ideas": ideas,
        "count": len(ideas),
    }


# ── Quality API Routes ────────────────────────────────────────────


@app.post("/api/quality/score-edition")
def quality_score_edition(data: dict):
    """Score an edition's quality.

    Body::
        {
            "edition_id": "ed-001",
            "citation_report": {"valid": true, "missing_ids": [], "hallucination_count": 0, "total_claims": 5},
            "signal_data": {"items": [...], "source_count": 3},
            "narrative_data": {"story_diffs": [...], "trajectories": [...]},
            "brand_data": {"output_text": "...", "brand_patterns": [...]}
        }

    Returns the composite quality score.
    """
    edition_id = data.get("edition_id", "")
    if not edition_id:
        raise HTTPException(400, "edition_id is required")

    from quality.scorer import QualityScorer

    scorer = QualityScorer()
    result = scorer.score_edition(
        edition_id=edition_id,
        citation_report=data.get("citation_report", {}),
        signal_data=data.get("signal_data", {}),
        narrative_data=data.get("narrative_data", {}),
        brand_data=data.get("brand_data", {}),
    )
    return result


@app.get("/api/quality/trend")
def quality_trend(limit: int = Query(10, ge=1, le=100)):
    """Get quality scores over recent editions.

    Query params:
        * ``limit`` — Max editions to return (default: 10).

    Returns list of quality score records ordered by ``scored_at`` descending.
    """
    from quality.scorer import QualityScorer

    scorer = QualityScorer()
    trend = scorer.get_trend(limit=limit)
    return {"trend": trend, "count": len(trend)}


@app.get("/api/quality/score/{edition_id}")
def quality_get_score(edition_id: str):
    """Get the quality score for a specific edition.

    Returns the quality score record or 404.
    """
    from quality.scorer import QualityScorer

    scorer = QualityScorer()
    score = scorer.get_score(edition_id)
    if not score:
        raise HTTPException(404, f"No quality score found for edition {edition_id}")
    return score


@app.post("/api/quality/baseline/create")
def quality_baseline_create(data: dict):
    """Create a quality baseline for an edition.

    Body::
        {
            "edition_id": "ed-001",
            "quality_score": {...},
            "metadata": {"workflow_id": "...", "label": "first"}
        }

    Returns the stored baseline record.
    """
    edition_id = data.get("edition_id", "")
    quality_score = data.get("quality_score", {})
    metadata = data.get("metadata")

    if not edition_id:
        raise HTTPException(400, "edition_id is required")
    if not quality_score:
        raise HTTPException(400, "quality_score is required")

    from quality.baseline import BaselineManager

    manager = BaselineManager()
    result = manager.create(edition_id, quality_score, metadata)
    return result


@app.post("/api/quality/regression/check")
def quality_regression_check(data: dict):
    """Check quality regression against a baseline.

    Body::
        {
            "edition_id": "ed-002",
            "baseline_id": "bl-001"
        }

    Returns regression analysis.
    """
    edition_id = data.get("edition_id", "")
    baseline_id = data.get("baseline_id", "")

    if not edition_id or not baseline_id:
        raise HTTPException(400, "edition_id and baseline_id are required")

    from quality.scorer import QualityScorer

    scorer = QualityScorer()
    result = scorer.check_regression(edition_id, baseline_id)
    return result


@app.post("/api/quality/regression/run")
def quality_regression_run(data: dict):
    """Run full regression test suite against a baseline.

    Body::
        {
            "edition_id": "ed-002",
            "baseline_id": "base-001",
            "quality_scores": {"composite_score": 0.85, "citation_validity": 0.9, ...}
        }

    Returns regression test results with per-test pass/fail and recommendation.
    """
    edition_id = data.get("edition_id", "")
    baseline_id = data.get("baseline_id", "")
    quality_scores = data.get("quality_scores", {})

    if not edition_id or not baseline_id:
        raise HTTPException(400, "edition_id and baseline_id are required")
    if not quality_scores:
        raise HTTPException(400, "quality_scores is required")

    from quality.regression import RegressionTester

    tester = RegressionTester()
    result = tester.run_tests(edition_id, baseline_id, quality_scores)
    return result


@app.post("/api/quality/regression/promote")
def quality_regression_promote(data: dict):
    """Promote an edition to become the active baseline.

    Body::
        {
            "edition_id": "ed-001"
        }

    Returns the newly created baseline record.
    """
    edition_id = data.get("edition_id", "")
    if not edition_id:
        raise HTTPException(400, "edition_id is required")

    from quality.regression import RegressionTester

    tester = RegressionTester()
    try:
        baseline = tester.update_baseline(edition_id)
        return baseline
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/quality/regression/history")
def quality_regression_history(limit: int = Query(20, ge=1, le=100)):
    """Get regression history (recent baseline promotions).

    Query params:
        * ``limit`` — Max entries to return (default: 20).

    Returns list of baseline records sorted by creation time descending.
    """
    from quality.regression import RegressionTester

    tester = RegressionTester()
    history = tester.get_regression_history(limit=limit)
    return {"history": history, "count": len(history)}


@app.get("/api/quality/regression/baselines")
def quality_regression_baselines(limit: int = Query(10, ge=1, le=100)):
    """List all quality baselines.

    Query params:
        * ``limit`` — Max baselines to return (default: 10).

    Returns list of baseline records ordered by creation time descending.
    """
    from quality.regression import BaselineStore

    store = BaselineStore()
    baselines = store.list(limit=limit)
    return {"baselines": baselines, "count": len(baselines)}


def main():
    import uvicorn
    
    # Register route modules (additive to existing api_server.py routes)
    try:
        from routes import register_routes
        register_routes(app)
    except ImportError:
        pass  # routes/ not available in all contexts
    
# Mount scheduler routes (visual cron editor with classifications)
app.include_router(scheduler_router, prefix="/api")

if __name__ == "__main__":
    import os
    print(f"\n  ES Agent Management API")
    print(f"  Listening on http://localhost:{port}")
    print(f"  API docs at http://localhost:{port}/docs")
    print()
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
