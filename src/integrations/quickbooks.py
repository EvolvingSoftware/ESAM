#!/usr/bin/env python3
"""QuickBooks Online Integration — OAuth2 client, data sync, webhook.

API Docs: https://developer.intuit.com/app/developer/qbo/docs/api/accounting
OAuth2:   https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization

Environment variables:
  QUICKBOOKS_CLIENT_ID     — OAuth2 client ID
  QUICKBOOKS_CLIENT_SECRET — OAuth2 client secret
  QUICKBOOKS_SANDBOX       — Set 'true' to use sandbox endpoints (default: true for dev)

For the hackathon/demo, tokens can be manually configured via the API.
"""

from __future__ import annotations

import json
import os
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Optional

from . import (
    AccountingClient, AccountingConnection, AccountingInvoice,
    make_api_request, encode_credentials, utc_now, new_id,
)


# ── Constants ───────────────────────────────────────────────────────

QB_SANDBOX_BASE = "https://sandbox-quickbooks.api.intuit.com"
QB_PRODUCTION_BASE = "https://quickbooks.api.intuit.com"
QB_AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
QB_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
QB_DISCOVERY_URL = "https://developer.api.intuit.com/.well-known/openid_sandbox_configuration"

# Required scopes
QB_SCOPES = "com.intuit.quickbooks.accounting openid profile email"


# ── Client ──────────────────────────────────────────────────────────

class QuickBooksClient(AccountingClient):
    """QuickBooks Online accounting platform integration."""

    @property
    def platform(self) -> str:
        return "quickbooks"

    def __init__(self, client_id: str = "", client_secret: str = ""):
        self.client_id = client_id or os.environ.get("QUICKBOOKS_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("QUICKBOOKS_CLIENT_SECRET", "")
        self.sandbox = os.environ.get("QUICKBOOKS_SANDBOX", "true").lower() == "true"

    @property
    def api_base(self) -> str:
        return QB_SANDBOX_BASE if self.sandbox else QB_PRODUCTION_BASE

    def get_authorization_url(self, redirect_uri: str, state: str) -> str:
        """Build the QuickBooks OAuth2 authorization URL."""
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "scope": QB_SCOPES,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        return f"{QB_AUTH_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str) -> AccountingConnection:
        """Exchange authorization code for tokens."""
        data = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }).encode("utf-8")

        headers = {
            "Authorization": f"Basic {encode_credentials(self.client_id, self.client_secret)}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

        result = make_api_request("POST", QB_TOKEN_URL, headers=headers, data=data)
        if "error" in result:
            raise RuntimeError(f"QuickBooks token exchange failed: {result.get('error')} — {result.get('detail', '')}")

        conn = AccountingConnection(
            id=new_id("qb-"),
            platform="quickbooks",
            access_token=result.get("access_token", ""),
            refresh_token=result.get("refresh_token", ""),
            scope=result.get("scope", ""),
            token_type=result.get("token_type", "bearer"),
            expires_at=self._expires_iso(result.get("expires_in", 3600)),
            tenant_id=result.get("realmId", ""),
            tenant_name=result.get("realmId", ""),
            created_at=utc_now(),
            updated_at=utc_now(),
        )

        # Try to get company info for tenant name
        self._enrich_tenant(conn)
        return conn

    def refresh_access_token(self, connection: AccountingConnection) -> AccountingConnection:
        """Refresh an expired QuickBooks access token."""
        data = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": connection.refresh_token,
        }).encode("utf-8")

        headers = {
            "Authorization": f"Basic {encode_credentials(self.client_id, self.client_secret)}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

        result = make_api_request("POST", QB_TOKEN_URL, headers=headers, data=data)
        if "error" in result:
            connection.last_error = f"Refresh failed: {result.get('error')} — {result.get('detail', '')}"
            connection.updated_at = utc_now()
            return connection

        connection.access_token = result.get("access_token", connection.access_token)
        connection.refresh_token = result.get("refresh_token", connection.refresh_token)
        connection.expires_at = self._expires_iso(result.get("expires_in", 3600))
        connection.updated_at = utc_now()
        connection.last_error = ""
        return connection

    def fetch_invoices(self, connection: AccountingConnection,
                       since: str = "") -> list[dict]:
        """Fetch invoices from QuickBooks Online.

        Uses QBO's query API (SQL-like).
        """
        headers = self._build_headers(connection)

        # QBO uses a SQL-like query language
        query = "SELECT * FROM Invoice WHERE Metadata.LastUpdatedTime > '2010-01-01'"
        if since:
            # Convert ISO to QBO format: YYYY-MM-DDT00:00:00.000-00:00
            dt = since.replace("Z", "+00:00")[:19] + ".000" + since.replace("Z", "+00:00")[19:]
            query = f"SELECT * FROM Invoice WHERE Metadata.LastUpdatedTime > '{dt}'"

        url = f"{self._realm_url(connection)}/query?query={urllib.parse.quote(query)}"

        result = make_api_request("GET", url, headers=headers)
        if "error" in result:
            raise RuntimeError(f"QuickBooks fetch invoices failed: {result.get('error')} — {result.get('detail', '')}")

        qb_response = result.get("QueryResponse", {})
        invoices = qb_response.get("Invoice", [])
        return [self._normalise_invoice(inv, connection.business_id) for inv in invoices]

    def fetch_contacts(self, connection: AccountingConnection) -> list[dict]:
        """Fetch customers from QuickBooks."""
        headers = self._build_headers(connection)
        query = "SELECT * FROM Customer"
        url = f"{self._realm_url(connection)}/query?query={urllib.parse.quote(query)}"

        result = make_api_request("GET", url, headers=headers)
        if "error" in result:
            raise RuntimeError(f"QuickBooks fetch customers failed: {result.get('error')}")

        qb_response = result.get("QueryResponse", {})
        customers = qb_response.get("Customer", [])
        return [self._normalise_contact(c) for c in customers]

    def update_invoice_status(self, connection: AccountingConnection,
                              invoice_id: str, status: str,
                              paid_amount_cents: int = 0,
                              paid_at: str = "") -> bool:
        """Push payment/status update to QuickBooks.

        For payments, we create a Payment entity linked to the invoice.
        """
        headers = self._build_headers(connection)
        headers["Content-Type"] = "application/json"

        if status == "paid" and paid_amount_cents > 0:
            # Create a Payment in QuickBooks
            payment_data = {
                "TotalAmt": paid_amount_cents / 100.0,
                "CustomerRef": {"value": self._get_customer_ref(invoice_id, connection)},
                "Line": [{
                    "Amount": paid_amount_cents / 100.0,
                    "LinkedTxn": [{
                        "TxnId": invoice_id,
                        "TxnType": "Invoice",
                    }],
                }],
            }
            if paid_at:
                payment_data["TxnDate"] = paid_at[:10]

            result = make_api_request(
                "POST", f"{self._realm_url(connection)}/payment",
                headers=headers, data=payment_data,
            )
            if "error" in result:
                raise RuntimeError(f"QuickBooks payment push failed: {result}")
            if "Fault" in result:
                fault = result.get("Fault", {}).get("Error", [{}])[0]
                raise RuntimeError(f"QuickBooks API error: {fault.get('Message', '')} — {fault.get('Detail', '')}")
            return True

        return False

    def _build_headers(self, connection: AccountingConnection) -> dict:
        """Build auth headers for QuickBooks API calls."""
        if connection.is_expired() and connection.refresh_token:
            connection = self.refresh_access_token(connection)

        return {
            "Authorization": f"Bearer {connection.access_token}",
            "Accept": "application/json",
        }

    def _realm_url(self, connection: AccountingConnection) -> str:
        """Build the realm-specific API URL."""
        realm_id = connection.tenant_id
        return f"{self.api_base}/v3/company/{realm_id}"

    def _enrich_tenant(self, connection: AccountingConnection):
        """Fetch company info to populate tenant_name."""
        headers = self._build_headers(connection)
        url = f"{self._realm_url(connection)}/companyinfo/{connection.tenant_id}"
        result = make_api_request("GET", url, headers=headers)
        if "error" not in result:
            company = result.get("CompanyInfo", {})
            connection.tenant_name = company.get("CompanyName", connection.tenant_id)
            connection.tenant_details = json.dumps(company)

    def _get_customer_ref(self, invoice_id: str,
                          connection: AccountingConnection) -> str:
        """Get the customer ref from an invoice for payment creation."""
        headers = self._build_headers(connection)
        url = f"{self._realm_url(connection)}/invoice/{invoice_id}"
        result = make_api_request("GET", url, headers=headers)
        if "error" not in result:
            invoice = result.get("Invoice", {})
            customer_ref = invoice.get("CustomerRef", {})
            return customer_ref.get("value", "")
        return ""

    def _normalise_invoice(self, inv: dict, business_id: str) -> dict:
        """Convert a QuickBooks invoice to our normalised format."""
        customer = inv.get("CustomerRef", {})
        bill_addr = inv.get("BillAddr", {}) or {}
        amount = float(inv.get("Balance", inv.get("TotalAmt", 0)))

        return {
            "platform_id": inv.get("Id", ""),
            "invoice_number": inv.get("DocNumber", inv.get("Id", "")),
            "contact_name": customer.get("name", ""),
            "contact_email": bill_addr.get("Email", ""),
            "contact_phone": bill_addr.get("Phone", ""),
            "amount_cents": int(round(max(amount, 0) * 100)),
            "currency": inv.get("CurrencyRef", {}).get("value", "AUD"),
            "issue_date": inv.get("TxnDate", ""),
            "due_date": inv.get("DueDate", ""),
            "status": self._map_status(inv.get("PrivateNote", "") or inv.get("Balance", 0)),
            "description": inv.get("CustomerMemo", {}).get("value", ""),
            "line_items": json.dumps(inv.get("Line", [])),
        }

    def _normalise_contact(self, c: dict) -> dict:
        """Normalise a QuickBooks customer."""
        primary_phone = c.get("PrimaryPhone", {}) or {}
        primary_email = c.get("PrimaryEmailAddr", {}) or {}

        return {
            "name": c.get("DisplayName", c.get("FullyQualifiedName", "")),
            "email": primary_email.get("Address", ""),
            "phone": primary_phone.get("FreeFormNumber", ""),
            "contact_id": c.get("Id", ""),
        }

    def _map_status(self, qb_status_or_balance) -> str:
        """Map QuickBooks status to our internal status."""
        if isinstance(qb_status_or_balance, (int, float)):
            return "overdue" if qb_status_or_balance > 0 else "paid"
        if isinstance(qb_status_or_balance, str):
            lower = qb_status_or_balance.lower()
            if "paid" in lower:
                return "paid"
            if "overdue" in lower:
                return "overdue"
        return "issued"

    def _expires_iso(self, expires_in: int) -> str:
        dt = datetime.now(timezone.utc) + __import__("datetime").timedelta(seconds=int(expires_in))
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond:06d}Z"


# ── Demo / Manual Token Setup ───────────────────────────────────────

def create_manual_connection(access_token: str, realm_id: str,
                             company_name: str = "",
                             business_id: str = "biz-001") -> AccountingConnection:
    """Create a connection from manually provided token (no OAuth2 flow)."""
    return AccountingConnection(
        id=new_id("qb-"),
        business_id=business_id,
        platform="quickbooks",
        access_token=access_token,
        token_type="bearer",
        scope=QB_SCOPES,
        expires_at="9999-12-31T23:59:59Z",
        tenant_id=realm_id,
        tenant_name=company_name or f"QB Company ({realm_id[:8]}...)",
        is_active=True,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
