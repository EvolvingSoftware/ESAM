#!/usr/bin/env python3
"""ES Agent Management — Accounting Software Integration Layer.

Bidirectional sync with Xero, QuickBooks Online, and MYOB AccountRight.
Provides OAuth2 authentication, data mapping, webhook handling, and
scheduled sync for the Tether collections workflow.

Architecture:
  ┌─────────────────────────────────────────────┐
  │              Sync Engine                     │
  │  (orchestrates all platforms on schedule)    │
  ├─────────┬──────────┬──────────┬─────────────┤
  │  Xero   │ QuickBooks│  MYOB    │ Webhook     │
  │  Client │  Client  │  Client  │  Handler    │
  └─────────┴──────────┴──────────┴─────────────┘
                    │
  ┌─────────────────▼───────────────────────────┐
  │           Tether Engine / Database            │
  │  (debtors, invoices, payments, sync_log)     │
  └─────────────────────────────────────────────┘
"""

from __future__ import annotations

import json
import os
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode, urljoin


# ── Data Models ─────────────────────────────────────────────────────

@dataclass
class AccountingConnection:
    """Stored OAuth2 / API connection for a business."""
    id: str = ""
    business_id: str = ""
    platform: str = ""  # xero | quickbooks | myob
    access_token: str = ""
    refresh_token: str = ""
    token_type: str = "bearer"
    scope: str = ""
    expires_at: str = ""         # ISO 8601
    refresh_expires_at: str = ""  # ISO 8601
    tenant_id: str = ""          # Xero tenant / QuickBooks realm / MYOB company
    tenant_name: str = ""
    tenant_details: str = "{}"   # JSON extra info
    is_active: bool = True
    last_sync_at: str = ""
    last_error: str = ""
    created_at: str = ""
    updated_at: str = ""

    def is_expired(self) -> bool:
        """Check if the access token is expired."""
        if not self.expires_at:
            return True
        try:
            exp = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            return datetime.now(timezone.utc) >= exp
        except (ValueError, TypeError):
            return True

    def to_dict(self) -> dict:
        return asdict(self)

    def from_dict(self, d: dict) -> AccountingConnection:
        for k, v in d.items():
            if hasattr(self, k):
                setattr(self, k, v)
        return self


@dataclass
class AccountingInvoice:
    """Normalised invoice from any accounting platform."""
    id: str = ""
    platform: str = ""            # xero | quickbooks | myob
    platform_id: str = ""         # Native ID in accounting system
    business_id: str = ""
    invoice_number: str = ""
    contact_name: str = ""
    contact_email: str = ""
    contact_phone: str = ""
    amount_cents: int = 0
    currency: str = "AUD"
    issue_date: str = ""
    due_date: str = ""
    status: str = ""              # draft | issued | overdue | paid | void
    description: str = ""
    line_items: str = "[]"        # JSON array
    debtor_id: str = ""           # Linked Tether debtor (if exists)
    last_synced_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SyncResult:
    """Result of a single platform sync."""
    platform: str = ""
    business_id: str = ""
    started_at: str = ""
    completed_at: str = ""
    invoices_fetched: int = 0
    invoices_created: int = 0
    invoices_updated: int = 0
    payments_pushed: int = 0
    errors: list[str] = field(default_factory=list)
    success: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


# ── OAuth2 / API Client Base ────────────────────────────────────────

class AccountingClient(ABC):
    """Abstract base for accounting platform clients."""

    @property
    @abstractmethod
    def platform(self) -> str:
        """Platform identifier: xero | quickbooks | myob"""
        ...

    @abstractmethod
    def get_authorization_url(self, redirect_uri: str, state: str) -> str:
        """Build the OAuth2 authorization URL for user consent."""
        ...

    @abstractmethod
    def exchange_code(self, code: str, redirect_uri: str) -> AccountingConnection:
        """Exchange an authorization code for tokens."""
        ...

    @abstractmethod
    def refresh_access_token(self, connection: AccountingConnection) -> AccountingConnection:
        """Refresh an expired access token."""
        ...

    @abstractmethod
    def fetch_invoices(self, connection: AccountingConnection, since: str = "") -> list[dict]:
        """Fetch invoices/overdues from the accounting platform.
        
        Returns normalised dicts for mapping to AccountingInvoice.
        """
        ...

    @abstractmethod
    def fetch_contacts(self, connection: AccountingConnection) -> list[dict]:
        """Fetch contacts / customers from the accounting platform."""
        ...

    @abstractmethod
    def update_invoice_status(self, connection: AccountingConnection,
                              invoice_id: str, status: str,
                              paid_amount_cents: int = 0,
                              paid_at: str = "") -> bool:
        """Push payment/status update back to the accounting platform.
        
        This is the 'bidirectional' half — when Tether marks an invoice
        as paid (via Stripe), we push that back to Xero/QB/MYOB.
        """
        ...


# ── Token Storage ───────────────────────────────────────────────────

# Tokens are stored in the ESAM database (accounting_connections table).
# The API server has endpoints to manage connections.
# For OAuth2 flows, a local HTTP server handles redirects during setup.


# ── Utilities ───────────────────────────────────────────────────────

def new_id(prefix: str = "") -> str:
    uid = uuid.uuid4().hex[:12]
    return f"{prefix}{uid}" if prefix else uid


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
           f"{datetime.now(timezone.utc).microsecond:06d}Z"


def make_api_request(method: str, url: str, headers: dict = None,
                     data: Any = None, timeout: int = 30) -> dict:
    """Make an HTTP request, returning parsed JSON or error dict."""
    import urllib.request
    import urllib.error

    if headers is None:
        headers = {}
    req = urllib.request.Request(url, method=method, headers=headers)
    if data is not None:
        if isinstance(data, dict):
            data = json.dumps(data).encode("utf-8")
            if "Content-Type" not in headers:
                req.add_header("Content-Type", "application/json")
        elif isinstance(data, str):
            data = data.encode("utf-8")
        req.data = data

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            content_type = resp.headers.get("Content-Type", "")
            if "application/json" in content_type or body.startswith("{"):
                return json.loads(body)
            return {"raw": body, "status": resp.status}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"error": f"HTTP {e.code}", "detail": body, "status": e.code}
    except urllib.error.URLError as e:
        return {"error": f"Connection failed: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


def encode_credentials(client_id: str, client_secret: str) -> str:
    """Base64 encode client credentials for Basic auth."""
    import base64
    raw = f"{client_id}:{client_secret}"
    return base64.b64encode(raw.encode("utf-8")).decode("utf-8")


# ── Config ──────────────────────────────────────────────────────────

def get_config() -> dict:
    """Load accounting integration config from environment.
    
    Expected env vars:
      XERO_CLIENT_ID, XERO_CLIENT_SECRET
      QUICKBOOKS_CLIENT_ID, QUICKBOOKS_CLIENT_SECRET
      MYOB_CLIENT_ID, MYOB_CLIENT_SECRET
      ACCOUNTING_REDIRECT_URI (default: http://localhost:8008/api/accounting/callback)
    """
    return {
        "xero": {
            "client_id": os.environ.get("XERO_CLIENT_ID", ""),
            "client_secret": os.environ.get("XERO_CLIENT_SECRET", ""),
        },
        "quickbooks": {
            "client_id": os.environ.get("QUICKBOOKS_CLIENT_ID", ""),
            "client_secret": os.environ.get("QUICKBOOKS_CLIENT_SECRET", ""),
        },
        "myob": {
            "client_id": os.environ.get("MYOB_CLIENT_ID", ""),
            "client_secret": os.environ.get("MYOB_CLIENT_SECRET", ""),
        },
        "redirect_uri": os.environ.get(
            "ACCOUNTING_REDIRECT_URI",
            "http://localhost:8008/api/accounting/callback"
        ),
    }


# ── Platform Enum ───────────────────────────────────────────────────

PLATFORMS = {
    "xero": "Xero",
    "quickbooks": "QuickBooks Online",
    "myob": "MYOB AccountRight",
}

SUPPORTED_PLATFORMS = list(PLATFORMS.keys())
