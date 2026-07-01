#!/usr/bin/env python3
"""Auth routes — /api/auth/* (login, register, keys)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Depends, Request

from database import get_connection
from auth import (
    create_users_table, create_user, authenticate_user,
    create_access_token, get_current_user, get_current_entity_id,
    list_user_entities, create_api_keys_table, create_api_key,
    list_api_keys, revoke_api_key, validate_api_key,
)
from audit_log import record_event

logger = logging.getLogger(__name__)


def register(app: FastAPI):
    """Register all /api/auth/* routes."""

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
