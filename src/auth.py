import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext

from database import get_connection

SECRET_KEY = os.environ.get("ESAM_SECRET_KEY", "dev-secret-change-in-prod")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hash: str) -> bool:
    return pwd_context.verify(password, hash)


def create_users_table() -> None:
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.commit()


def create_user(email: str, name: str, password: str) -> dict:
    conn = get_connection()
    existing = conn.execute(
        "SELECT id FROM users WHERE email = ?", (email,)
    ).fetchone()
    if existing:
        raise ValueError("Email already registered")

    user_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO users (id, email, name, password_hash, is_active, created_at, updated_at)
        VALUES (?, ?, ?, ?, 1, ?, ?)
        """,
        (user_id, email, name, hash_password(password), now, now),
    )
    conn.commit()
    return {"id": user_id, "email": email, "name": name, "created_at": now}


def authenticate_user(email: str, password: str) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE email = ?", (email,)
    ).fetchone()
    if not row:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return dict(row)


def create_access_token(
    data: dict, expires_delta: Optional[timedelta] = None
) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


def get_user_by_id(user_id: str) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    return dict(row) if row else None


def get_user_by_email(email: str) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE email = ?", (email,)
    ).fetchone()
    return dict(row) if row else None


def list_user_entities(user_id: str) -> list[dict]:
    import sqlite3
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT e.* FROM entities e
            JOIN entity_users eu ON eu.entity_id = e.id
            WHERE eu.user_id = ?
            """,
            (user_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(row) for row in rows]


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    token = credentials.credentials
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )
    user = get_user_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    if not user["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is inactive",
        )
    return user


async def get_current_entity_id(
    x_entity_id: Optional[str] = Header(None),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[str]:
    if credentials is not None:
        payload = decode_access_token(credentials.credentials)
        if payload is not None:
            entity_id = payload.get("entity_id")
            if entity_id is not None:
                return entity_id
    if x_entity_id is not None:
        return x_entity_id
    return None


# ═══════════════════════════════════════════════════════════════
# API Key Management
# ═══════════════════════════════════════════════════════════════

API_KEYS_SCHEMA = """
CREATE TABLE IF NOT EXISTS wf_api_keys (
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
);
"""


def create_api_keys_table() -> None:
    """Create the API keys table if it doesn't exist."""
    conn = get_connection()
    conn.execute(API_KEYS_SCHEMA)
    conn.commit()


def generate_api_key() -> tuple[str, str, str]:
    """Generate a new API key. Returns (full_key, prefix, hashed_key).

    Format: esam_<random_32_hex>
    The prefix is the first 8 chars of the hex part.
    The hash is sha256 of the full key.
    """
    random_bytes = secrets.token_hex(16)  # 32 hex chars
    full_key = f"esam_{random_bytes}"
    prefix = random_bytes[:8]
    hashed = hashlib.sha256(full_key.encode()).hexdigest()
    return full_key, prefix, hashed


def create_api_key(
    name: str,
    entity_id: str = "",
    scopes: list[str] | None = None,
    expires_at: str | None = None,
) -> dict:
    """Create a new API key. Returns the key data (including the full key ONCE)."""
    key_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    full_key, prefix, key_hash = generate_api_key()
    scopes_json = json.dumps(scopes or ["run"])

    conn = get_connection()
    conn.execute(
        """INSERT INTO wf_api_keys (id, entity_id, name, key_prefix, key_hash, scopes, expires_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (key_id, entity_id, name, prefix, key_hash, scopes_json, expires_at, now),
    )
    conn.commit()

    return {
        "id": key_id,
        "name": name,
        "key": full_key,
        "key_prefix": prefix,
        "scopes": scopes or ["run"],
        "entity_id": entity_id,
        "expires_at": expires_at,
        "created_at": now,
    }


def validate_api_key(key: str) -> dict | None:
    """Validate an API key. Returns the key record if valid, None otherwise.

    Checks: key exists in DB, not revoked, not expired.
    Updates last_used_at on successful validation.
    """
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM wf_api_keys WHERE key_hash = ?", (key_hash,)
    ).fetchone()
    if not row:
        return None

    record = dict(row)

    # Check revoked
    if record.get("revoked_at"):
        return None

    # Check expired
    expires = record.get("expires_at")
    if expires:
        try:
            expires_dt = datetime.fromisoformat(expires)
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            if expires_dt < datetime.now(timezone.utc):
                return None
        except (ValueError, TypeError):
            return None

    # Update last_used_at
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE wf_api_keys SET last_used_at = ? WHERE id = ?",
        (now, record["id"]),
    )
    conn.commit()
    record["last_used_at"] = now

    return record


def list_api_keys(entity_id: str = "") -> list[dict]:
    """List API keys (prefixes only, never full keys)."""
    conn = get_connection()
    if entity_id:
        rows = conn.execute(
            "SELECT id, entity_id, name, key_prefix, scopes, expires_at, last_used_at, created_at, revoked_at "
            "FROM wf_api_keys WHERE entity_id = ? ORDER BY created_at DESC",
            (entity_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, entity_id, name, key_prefix, scopes, expires_at, last_used_at, created_at, revoked_at "
            "FROM wf_api_keys ORDER BY created_at DESC"
        ).fetchall()
    keys = []
    for row in rows:
        record = dict(row)
        # Parse scopes from JSON string
        if isinstance(record.get("scopes"), str):
            try:
                record["scopes"] = json.loads(record["scopes"])
            except (json.JSONDecodeError, TypeError):
                record["scopes"] = ["run"]
        keys.append(record)
    return keys


def revoke_api_key(key_id: str) -> bool:
    """Revoke an API key by setting revoked_at."""
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        "UPDATE wf_api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
        (now, key_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def delete_expired_keys() -> int:
    """Delete expired keys. Returns count deleted."""
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        "DELETE FROM wf_api_keys WHERE expires_at IS NOT NULL AND expires_at < ?",
        (now,),
    )
    conn.commit()
    return cursor.rowcount
