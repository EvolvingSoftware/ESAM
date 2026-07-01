#!/usr/bin/env python3
"""Xero Integration — OAuth2 client, data sync, webhook handler.

API Docs: https://developer.xero.com/documentation/api/accounting/overview
OAuth2:   https://developer.xero.com/documentation/oauth2/auth-flow

Environment variables:
  XERO_CLIENT_ID       — OAuth2 client ID (required for OAuth2 flow)
  XERO_CLIENT_SECRET   — OAuth2 client secret (required for OAuth2 flow)

For the hackathon/demo, tokens can also be manually configured via
the API to bypass the full OAuth2 redirect dance.
"""

from __future__ import annotations

import hashlib
import json
import os
import base64
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Optional

from . import (
    AccountingClient, AccountingConnection, AccountingInvoice,
    make_api_request, encode_credentials, utc_now, new_id,
)


# ── Constants ───────────────────────────────────────────────────────

XERO_AUTH_URL = "https://login.xero.com/identity/connect/authorize"
XERO_TOKEN_URL = "https://identity.xero.com/connect/token"
XERO_CONNECTIONS_URL = "https://api.xero.com/connections"
XERO_API_BASE = "https://api.xero.com/api.xro/2.0"
XERO_PKCE_METHOD = "S256"

# Required scopes for Tether operation
XERO_SCOPES = "openid profile email accounting.transactions accounting.contacts offline_access"


# ── PKCE Helpers ────────────────────────────────────────────────────

def _generate_code_verifier() -> str:
    """Generate a PKCE code verifier (random 43-128 char string)."""
    import secrets
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def _generate_code_challenge(verifier: str) -> str:
    """Generate PKCE S256 code challenge from verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# ── Client ──────────────────────────────────────────────────────────

class XeroClient(AccountingClient):
    """Xero accounting platform integration."""

    @property
    def platform(self) -> str:
        return "xero"

    def __init__(self, client_id: str = "", client_secret: str = ""):
        self.client_id = client_id or os.environ.get("XERO_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("XERO_CLIENT_SECRET", "")
        # PKCE state stored per auth attempt
        self._verifier: str = ""

    def get_authorization_url(self, redirect_uri: str, state: str) -> str:
        """Build the Xero OAuth2 authorization URL with PKCE."""
        self._verifier = _generate_code_verifier()
        challenge = _generate_code_challenge(self._verifier)

        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": XERO_SCOPES,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": XERO_PKCE_METHOD,
        }
        return f"{XERO_AUTH_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str) -> AccountingConnection:
        """Exchange authorization code for tokens."""
        if not self._verifier:
            raise ValueError("No PKCE verifier — call get_authorization_url first")

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": self._verifier,
        }
        auth_header = f"Basic {encode_credentials(self.client_id, self.client_secret)}"
        headers = {
            "Authorization": auth_header,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        body = urllib.parse.urlencode(data).encode("utf-8")

        result = make_api_request("POST", XERO_TOKEN_URL, headers=headers,
                                  data=body)
        if "error" in result:
            raise RuntimeError(f"Xero token exchange failed: {result.get('error')} — {result.get('detail', '')}")

        # Build connection
        conn = AccountingConnection(
            id=new_id("xero-"),
            platform="xero",
            access_token=result.get("access_token", ""),
            refresh_token=result.get("refresh_token", ""),
            scope=result.get("scope", ""),
            token_type=result.get("token_type", "bearer"),
            expires_at=self._expires_in_to_iso(result.get("expires_in", 1800)),
            created_at=utc_now(),
            updated_at=utc_now(),
        )

        # Fetch connected tenants (Xero organisations)
        self._populate_tenants(conn)
        return conn

    def refresh_access_token(self, connection: AccountingConnection) -> AccountingConnection:
        """Refresh an expired Xero access token."""
        data = {
            "grant_type": "refresh_token",
            "refresh_token": connection.refresh_token,
        }
        auth_header = f"Basic {encode_credentials(self.client_id, self.client_secret)}"
        headers = {
            "Authorization": auth_header,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        body = urllib.parse.urlencode(data).encode("utf-8")

        result = make_api_request("POST", XERO_TOKEN_URL, headers=headers, data=body)
        if "error" in result:
            connection.last_error = f"Refresh failed: {result.get('error')} — {result.get('detail', '')}"
            connection.updated_at = utc_now()
            return connection

        connection.access_token = result.get("access_token", connection.access_token)
        connection.refresh_token = result.get("refresh_token", connection.refresh_token)
        connection.expires_at = self._expires_in_to_iso(result.get("expires_in", 1800))
        connection.updated_at = utc_now()
        connection.last_error = ""
        return connection

    def fetch_invoices(self, connection: AccountingConnection,
                       since: str = "") -> list[dict]:
        """Fetch invoices from Xero.

        Returns normalised dicts.
        Supports 'since' for incremental sync (ISO datetime).
        """
        headers = self._build_headers(connection)
        url = f"{XERO_API_BASE}/Invoices?Statuses=AUTHORISED,OVERDUE"
        if since:
            url += f"&If-Modified-Since={urllib.parse.quote(since)}"

        result = make_api_request("GET", url, headers=headers)
        if "error" in result:
            raise RuntimeError(f"Xero fetch invoices failed: {result.get('error')}")

        invoices = result.get("Invoices", [])
        return [self._normalise_invoice(inv, connection.business_id) for inv in invoices]

    def fetch_contacts(self, connection: AccountingConnection) -> list[dict]:
        """Fetch contacts from Xero."""
        headers = self._build_headers(connection)
        url = f"{XERO_API_BASE}/Contacts"

        result = make_api_request("GET", url, headers=headers)
        if "error" in result:
            raise RuntimeError(f"Xero fetch contacts failed: {result.get('error')}")

        contacts = result.get("Contacts", [])
        return [self._normalise_contact(c) for c in contacts]

    def update_invoice_status(self, connection: AccountingConnection,
                              invoice_id: str, status: str,
                              paid_amount_cents: int = 0,
                              paid_at: str = "") -> bool:
        """Push payment/status update back to Xero.

        For payments, we create a Payment record in Xero linked to the invoice.
        Status values: 'paid', 'void'
        """
        headers = self._build_headers(connection)
        headers["Content-Type"] = "application/json"

        if status == "paid" and paid_amount_cents > 0:
            # Create a Payment record in Xero
            payment_data = {
                "Invoice": {"InvoiceID": invoice_id},
                "Amount": paid_amount_cents / 100.0,
                "PaymentType": "ACCRECPAYMENT",
            }
            if paid_at:
                payment_data["Date"] = paid_at[:10]  # YYYY-MM-DD

            result = make_api_request(
                "POST", f"{XERO_API_BASE}/Payments",
                headers=headers, data=payment_data,
            )
            if "error" in result:
                raise RuntimeError(f"Xero payment push failed: {result}")
            return True

        elif status == "void":
            # Xero doesn't have a direct void — use Credit Note or manual
            # For now, log it and return False (requires manual intervention)
            return False

        return False

    def _build_headers(self, connection: AccountingConnection) -> dict:
        """Build auth headers for Xero API calls."""
        # Auto-refresh if token is expired
        if connection.is_expired() and connection.refresh_token:
            connection = self.refresh_access_token(connection)

        headers = {
            "Authorization": f"Bearer {connection.access_token}",
            "Accept": "application/json",
            "Xero-tenant-id": connection.tenant_id,
        }
        return headers

    def _populate_tenants(self, connection: AccountingConnection):
        """Fetch connected Xero organisations (tenants) and set the first one."""
        headers = {
            "Authorization": f"Bearer {connection.access_token}",
            "Accept": "application/json",
        }
        result = make_api_request("GET", XERO_CONNECTIONS_URL, headers=headers)
        if "error" in result:
            connection.last_error = f"Failed to fetch tenants: {result.get('error')}"
            return

        tenants = result if isinstance(result, list) else result.get("connections", [])
        if tenants:
            first = tenants[0]
            connection.tenant_id = first.get("tenantId", "")
            connection.tenant_name = first.get("tenantName", "")
            connection.tenant_details = json.dumps(first)

    def _normalise_invoice(self, inv: dict, business_id: str) -> dict:
        """Convert a Xero invoice to our normalised format."""
        contact = inv.get("Contact", {})
        amount_due = float(inv.get("AmountDue", 0))
        total = float(inv.get("Total", 0))

        return {
            "platform_id": inv.get("InvoiceID", ""),
            "invoice_number": inv.get("InvoiceNumber", ""),
            "contact_name": contact.get("Name", ""),
            "contact_email": contact.get("EmailAddress", ""),
            "contact_phone": self._get_contact_phone(contact),
            "amount_cents": int(round(max(amount_due, total) * 100)),
            "currency": inv.get("CurrencyCode", "AUD"),
            "issue_date": inv.get("Date", ""),
            "due_date": inv.get("DueDate", ""),
            "status": self._map_status(inv.get("Status", "")),
            "description": inv.get("Reference", "") or inv.get("LineAmountTypes", ""),
            "line_items": json.dumps(inv.get("LineItems", [])),
        }

    def _normalise_contact(self, c: dict) -> dict:
        """Normalise a Xero contact."""
        phones = c.get("Phones", [])
        email = ""
        phone = ""
        for p in phones:
            if p.get("PhoneType") == "DEFAULT":
                phone = p.get("PhoneNumber", "")
        for a in c.get("EmailAddresses", []):
            if a.get("EmailAddressType") == "DEFAULT":
                email = a.get("EmailAddress", "")

        return {
            "name": c.get("Name", ""),
            "email": email or c.get("EmailAddress", ""),
            "phone": phone,
            "contact_id": c.get("ContactID", ""),
        }

    def _get_contact_phone(self, contact: dict) -> str:
        """Extract phone from Xero contact."""
        phones = contact.get("Phones", [])
        for p in phones:
            if p.get("PhoneType") == "DEFAULT":
                return p.get("PhoneNumber", "")
        return ""

    def _map_status(self, xero_status: str) -> str:
        """Map Xero status to our internal status."""
        mapping = {
            "DRAFT": "draft",
            "SUBMITTED": "issued",
            "AUTHORISED": "issued",
            "OVERDUE": "overdue",
            "PAID": "paid",
            "VOID": "void",
            "DELETED": "void",
        }
        return mapping.get(xero_status.upper(), xero_status.lower())

    def _expires_in_to_iso(self, expires_in: int) -> str:
        """Convert expires_in seconds to ISO timestamp."""
        dt = datetime.now(timezone.utc) + __import__("datetime").timedelta(seconds=int(expires_in))
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond:06d}Z"


# ── Webhook Verification ───────────────────────────────────────────

def verify_xero_webhook(payload_bytes: bytes, signature_header: str,
                        webhook_key: str) -> bool:
    """Verify Xero webhook signature.

    Xero sends an x-xero-signature header that is HMACSHA256 of the raw
    payload using the webhook key as the secret.
    """
    import hmac
    expected = hmac.new(
        webhook_key.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).digest()
    provided = base64.b64decode(signature_header)
    return hmac.compare_digest(expected, provided)


# ── Demo / Manual Token Setup ───────────────────────────────────────

def create_manual_connection(access_token: str, tenant_id: str,
                             tenant_name: str = "",
                             business_id: str = "biz-001") -> AccountingConnection:
    """Create a connection from manually provided token (no OAuth2 flow).

    This is useful for hackathon/demo setups where the full OAuth2
    redirect dance isn't desirable. The token must already be valid
    with the right scopes.
    """
    return AccountingConnection(
        id=new_id("xero-"),
        business_id=business_id,
        platform="xero",
        access_token=access_token,
        token_type="bearer",
        scope=XERO_SCOPES,
        expires_at="9999-12-31T23:59:59Z",  # Assume valid (user will refresh manually)
        tenant_id=tenant_id,
        tenant_name=tenant_name or f"Xero Org ({tenant_id[:8]}...)",
        is_active=True,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
