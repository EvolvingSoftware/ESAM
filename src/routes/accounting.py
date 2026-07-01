#!/usr/bin/env python3
"""Accounting routes — /api/accounting/* routes (Xero, QuickBooks, MYOB)."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from database import get_connection

logger = logging.getLogger(__name__)


def register(app: FastAPI):
    """Register all /api/accounting/* routes."""

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
