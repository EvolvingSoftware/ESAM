"""Platform audit log — tracks user/admin actions for compliance.

Append-only. Immutable. Never deleted or updated.
Designed for Australian credit law (NCCP Act, Privacy Act) compliance.
"""
from __future__ import annotations
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from database import get_connection

logger = logging.getLogger(__name__)

AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS wf_audit_events (
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
);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON wf_audit_events(actor_id);
CREATE INDEX IF NOT EXISTS idx_audit_resource ON wf_audit_events(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON wf_audit_events(created_at);
"""


def ensure_schema():
    """Create audit_events table if not exists."""
    try:
        conn = get_connection()
        conn.executescript(AUDIT_SCHEMA)
        conn.commit()
        logger.debug("Audit schema ensured.")
    except Exception:
        logger.exception("Failed to ensure audit schema")


def record_event(
    actor_id: str,
    actor_type: str = "user",
    action: str = "",
    resource_type: str = "",
    resource_id: str = "",
    old_state: dict | None = None,
    new_state: dict | None = None,
    ip_address: str = "",
    user_agent: str = "",
    entity_id: str = "",
) -> dict:
    """Record an audit event. Returns the event dict.
    Always succeeds — errors are logged, never raised.
    """
    event_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    event = {
        "id": event_id,
        "actor_id": actor_id,
        "actor_type": actor_type,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "old_state": json.dumps(old_state or {}, default=str),
        "new_state": json.dumps(new_state or {}, default=str),
        "ip_address": ip_address or "",
        "user_agent": user_agent or "",
        "entity_id": entity_id or actor_id,
        "created_at": now,
    }
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO wf_audit_events
               (id, actor_id, actor_type, action, resource_type, resource_id,
                old_state, new_state, ip_address, user_agent, entity_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event["id"],
                event["actor_id"],
                event["actor_type"],
                event["action"],
                event["resource_type"],
                event["resource_id"],
                event["old_state"],
                event["new_state"],
                event["ip_address"],
                event["user_agent"],
                event["entity_id"],
                event["created_at"],
            ),
        )
        conn.commit()
        logger.debug("Audit event recorded: %s", event_id)
    except Exception:
        logger.exception("Failed to record audit event (id=%s)", event_id)
    return event


def query_events(
    actor_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    action: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """Query audit events with filters. Returns events ordered by created_at DESC."""
    conditions = []
    params = []

    if actor_id:
        conditions.append("actor_id = ?")
        params.append(actor_id)
    if resource_type:
        conditions.append("resource_type = ?")
        params.append(resource_type)
    if resource_id:
        conditions.append("resource_id = ?")
        params.append(resource_id)
    if action:
        conditions.append("action = ?")
        params.append(action)
    if from_date:
        conditions.append("created_at >= ?")
        params.append(from_date)
    if to_date:
        conditions.append("created_at <= ?")
        params.append(to_date)

    where_clause = ""
    if conditions:
        where_clause = " WHERE " + " AND ".join(conditions)

    sql = (
        "SELECT id, actor_id, actor_type, action, resource_type, resource_id, "
        "old_state, new_state, ip_address, user_agent, entity_id, created_at "
        "FROM wf_audit_events"
        + where_clause
        + " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    )

    events = []
    try:
        conn = get_connection()
        rows = conn.execute(sql, params + [limit, offset]).fetchall()
        for row in rows:
            events.append(
                {
                    "id": row[0],
                    "actor_id": row[1],
                    "actor_type": row[2],
                    "action": row[3],
                    "resource_type": row[4],
                    "resource_id": row[5],
                    "old_state": json.loads(row[6]) if row[6] else {},
                    "new_state": json.loads(row[7]) if row[7] else {},
                    "ip_address": row[8],
                    "user_agent": row[9],
                    "entity_id": row[10],
                    "created_at": row[11],
                }
            )
    except Exception:
        logger.exception("Failed to query audit events")
    return events


def get_recent(entity_id: str, limit: int = 20) -> list[dict]:
    """Get recent events for an entity."""
    return query_events(actor_id=entity_id, limit=limit)
