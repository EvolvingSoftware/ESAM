#!/usr/bin/env python3
"""MYOB AccountRight Integration — OAuth2 client, data sync, webhook.

API Docs: https://developer.myob.com/api/accountright/api-overview/
OAuth2:   https://developer.myob.com/api/accountright/authentication-and-authorization/

Environment variables:
  MYOB_CLIENT_ID       — OAuth2 client ID
  MYOB_CLIENT_SECRET   — OAuth2 client secret

Key differences from Xero/QB:
  - MYOB uses company file GUIDs (not realm IDs)
  - API base: https://api.myob.com/accountright/{cf_guid}/
  - Requires listing company files first, then connecting to one
  - OAuth2 uses client credentials + authorization code flow
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

MYOB_AUTH_URL = "https://secure.myob.com/oauth2/account/authorize"
MYOB_TOKEN_URL = "https://secure.myob.com/oauth2/v1/authorize"
MYOB_LOGIN_URL = "https://secure.myob.com/oauth2/v1/login"
MYOB_API_BASE = "https://api.myob.com/accountright"

# Required scopes
MYOB_SCOPES = "la.global"

# Demo company file GUID (MYOB sample company)
MYOB_DEMO_CF_GUID = ""


# ── Client ──────────────────────────────────────────────────────────

class MYOBClient(AccountingClient):
    """MYOB AccountRight accounting platform integration."""

    @property
    def platform(self) -> str:
        return "myob"

    def __init__(self, client_id: str = "", client_secret: str = ""):
        self.client_id = client_id or os.environ.get("MYOB_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("MYOB_CLIENT_SECRET", "")

    def get_authorization_url(self, redirect_uri: str, state: str) -> str:
        """Build the MYOB OAuth2 authorization URL."""
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": MYOB_SCOPES,
            "state": state,
        }
        return f"{MYOB_AUTH_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str) -> AccountingConnection:
        """Exchange authorization code for tokens."""
        data = urllib.parse.urlencode({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

        result = make_api_request("POST", MYOB_TOKEN_URL, headers=headers, data=data)
        if "error" in result:
            raise RuntimeError(f"MYOB token exchange failed: {result.get('error')} — {result.get('detail', '')}")

        conn = AccountingConnection(
            id=new_id("myob-"),
            platform="myob",
            access_token=result.get("access_token", ""),
            refresh_token=result.get("refresh_token", ""),
            scope=result.get("scope", ""),
            token_type="bearer",
            expires_at=self._expires_iso(result.get("expires_in", 3600)),
            created_at=utc_now(),
            updated_at=utc_now(),
        )

        # Fetch company files (MYOB-specific)
        self._populate_company_file(conn)
        return conn

    def refresh_access_token(self, connection: AccountingConnection) -> AccountingConnection:
        """Refresh an expired MYOB access token."""
        data = urllib.parse.urlencode({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": connection.refresh_token,
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

        result = make_api_request("POST", MYOB_TOKEN_URL, headers=headers, data=data)
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
        """Fetch invoices from MYOB AccountRight.

        MYOB uses the Sale/Invoice endpoint.
        Filters via query parameters.
        """
        headers = self._build_headers(connection)
        cf_guid = connection.tenant_id
        url = f"{MYOB_API_BASE}/{cf_guid}/Sale/Invoice/Item"

        result = make_api_request("GET", url, headers=headers)
        if "error" in result:
            raise RuntimeError(f"MYOB fetch invoices failed: {result.get('error')} — {result.get('detail', '')}")

        invoices = result.get("Items", [])
        return [self._normalise_invoice(inv, connection.business_id) for inv in invoices]

    def fetch_contacts(self, connection: AccountingConnection) -> list[dict]:
        """Fetch contacts/cards from MYOB."""
        headers = self._build_headers(connection)
        cf_guid = connection.tenant_id
        url = f"{MYOB_API_BASE}/{cf_guid}/Contact/Customer"

        result = make_api_request("GET", url, headers=headers)
        if "error" in result:
            raise RuntimeError(f"MYOB fetch contacts failed: {result.get('error')}")

        customers = result.get("Items", [])
        return [self._normalise_contact(c) for c in customers]

    def update_invoice_status(self, connection: AccountingConnection,
                              invoice_id: str, status: str,
                              paid_amount_cents: int = 0,
                              paid_at: str = "") -> bool:
        """Push payment/status update to MYOB.

        MYOB payments are created via the Sale/CustomerPayment endpoint.
        """
        headers = self._build_headers(connection)
        headers["Content-Type"] = "application/json"
        cf_guid = connection.tenant_id

        if status == "paid" and paid_amount_cents > 0:
            # MYOB requires knowing the customer UID for the invoice
            # First fetch the invoice to get customer info
            invoice = self._get_invoice(connection, invoice_id)
            if not invoice:
                return False

            customer = invoice.get("Customer", {})
            payment_data = {
                "Customer": customer,
                "Date": paid_at[:10] if paid_at else utc_now()[:10],
                "Amount": float(paid_amount_cents) / 100.0,
                "PaymentMethod": "BankTransfer",
                "Memo": "Payment via Tether",
                "ApplicationType": "Invoice",
                "Lines": [{
                    "Type": "Transaction",
                    "SourceID": invoice_id,
                    "Amount": float(paid_amount_cents) / 100.0,
                }],
            }

            result = make_api_request(
                "POST",
                f"{MYOB_API_BASE}/{cf_guid}/Sale/CustomerPayment",
                headers=headers, data=payment_data,
            )
            if "error" in result:
                raise RuntimeError(f"MYOB payment push failed: {result}")
            return True

        return False

    def _build_headers(self, connection: AccountingConnection) -> dict:
        """Build auth headers for MYOB API calls."""
        if connection.is_expired() and connection.refresh_token:
            connection = self.refresh_access_token(connection)

        return {
            "Authorization": f"Bearer {connection.access_token}",
            "Accept": "application/json",
            "x-myobapi-key": self.client_id,
            "x-myobapi-version": "v2",
        }

    def _populate_company_file(self, connection: AccountingConnection):
        """Fetch MYOB company files and set the first one as the tenant."""
        headers = {
            "Authorization": f"Bearer {connection.access_token}",
            "Accept": "application/json",
            "x-myobapi-key": self.client_id,
            "x-myobapi-version": "v2",
        }
        url = f"{MYOB_API_BASE}/"
        result = make_api_request("GET", url, headers=headers)
        if "error" in result:
            connection.last_error = f"Failed to fetch company files: {result.get('error')}"
            return False

        items = result.get("Items", [])
        if items:
            first = items[0]
            connection.tenant_id = first.get("Id", "")
            connection.tenant_name = first.get("Name", first.get("CompanyName", ""))
            connection.tenant_details = json.dumps(first)
            return True
        return False

    def _get_invoice(self, connection: AccountingConnection,
                     invoice_id: str) -> dict | None:
        """Fetch a single invoice by its MYOB UID."""
        headers = self._build_headers(connection)
        cf_guid = connection.tenant_id
        url = f"{MYOB_API_BASE}/{cf_guid}/Sale/Invoice/Item/{invoice_id}"
        result = make_api_request("GET", url, headers=headers)
        if "error" in result:
            return None
        return result.get("Items", [None])[0] if isinstance(result.get("Items"), list) else result

    def fetch_company_files(self, connection: AccountingConnection) -> list[dict]:
        """Fetch all accessible company files for a token."""
        headers = {
            "Authorization": f"Bearer {connection.access_token}",
            "Accept": "application/json",
            "x-myobapi-key": self.client_id,
            "x-myobapi-version": "v2",
        }
        url = f"{MYOB_API_BASE}/"
        result = make_api_request("GET", url, headers=headers)
        if "error" in result:
            return []
        return result.get("Items", [])

    def _normalise_invoice(self, inv: dict, business_id: str) -> dict:
        """Convert a MYOB invoice to our normalised format."""
        customer = inv.get("Customer", {})
        billing_address = inv.get("BillingAddress", {}) or inv.get("ShippingAddress", {}) or {}

        # MYOB returns totals in the form: TotalTax (including tax), TotalAmount (excl tax)
        total = float(inv.get("TotalAmount", 0))
        total_tax = float(inv.get("TotalTax", 0))

        return {
            "platform_id": inv.get("UID", ""),
            "invoice_number": inv.get("Number", ""),
            "contact_name": customer.get("Name", ""),
            "contact_email": billing_address.get("Email", ""),
            "contact_phone": billing_address.get("Phone1", billing_address.get("Phone", "")),
            "amount_cents": int(round((total + total_tax) * 100)),
            "currency": "AUD",  # MYOB is Australian-only
            "issue_date": inv.get("Date", ""),
            "due_date": inv.get("Terms", {}).get("DueDate", ""),
            "status": self._map_status(inv.get("Status", "")),
            "description": inv.get("Description", ""),
            "line_items": json.dumps(inv.get("Lines", [])),
        }

    def _normalise_contact(self, c: dict) -> dict:
        """Normalise a MYOB customer."""
        addresses = c.get("Addresses", [])
        address = addresses[0] if addresses else {}

        return {
            "name": c.get("Name", ""),
            "email": address.get("Email", ""),
            "phone": address.get("Phone1", ""),
            "contact_id": c.get("UID", ""),
        }

    def _map_status(self, myob_status: str) -> str:
        """Map MYOB status to internal status."""
        mapping = {
            "Open": "issued",
            "Overdue": "overdue",
            "Paid": "paid",
            "Partial": "issued",
            "WrittenOff": "written_off",
        }
        return mapping.get(myob_status, myob_status.lower())

    def _expires_iso(self, expires_in: int) -> str:
        dt = datetime.now(timezone.utc) + __import__("datetime").timedelta(seconds=int(expires_in))
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond:06d}Z"


# ── Demo / Manual Token Setup ───────────────────────────────────────

def create_manual_connection(access_token: str, company_file_guid: str,
                             company_name: str = "",
                             business_id: str = "biz-001") -> AccountingConnection:
    """Create a connection from manually provided token (no OAuth2 flow)."""
    return AccountingConnection(
        id=new_id("myob-"),
        business_id=business_id,
        platform="myob",
        access_token=access_token,
        token_type="bearer",
        scope=MYOB_SCOPES,
        expires_at="9999-12-31T23:59:59Z",
        tenant_id=company_file_guid,
        tenant_name=company_name or f"MYOB Company ({company_file_guid[:8]}...)",
        is_active=True,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
