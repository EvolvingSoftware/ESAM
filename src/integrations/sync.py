#!/usr/bin/env python3
"""Accounting Sync Engine — Orchestrates Xero, QuickBooks, and MYOB sync.

Responsible for:
  1. Scheduled sync of invoices from all connected accounting platforms
  2. Mapping fetched data to Tether debtors/invoices
  3. Pushing payment status back to accounting platforms (bidirectional)
  4. Webhook handling for real-time updates
  5. Connection lifecycle management (token refresh, health checks)

Architecture:
  ┌─────────────────────────────────────────────┐
  │            SyncEngine                        │
  │  ┌──────────────────────────────────────┐   │
  │  │  schedule_sync()  │  handle_webhook()│   │
  │  │  sync_platform()  │  push_payment()  │   │
  │  └──────────┬───────────────────────────┘   │
  │             │                                │
  │  ┌──────────▼──────────┐                    │
  │  │  Platform Proxies   │                    │
  │  │  Xero | QB | MYOB   │                    │
  │  └─────────────────────┘                    │
  └─────────────────────────────────────────────┘
                    │
  ┌─────────────────▼───────────────────────────┐
  │          Tether Engine / Database            │
  │  (debtors, invoices, audit log, sync_log)   │
  └─────────────────────────────────────────────┘
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from io import StringIO
from pathlib import Path
from typing import Any, Optional

from . import (
    AccountingClient, AccountingConnection, AccountingInvoice,
    SyncResult, utc_now, new_id, get_config, SUPPORTED_PLATFORMS,
)
from .xero import XeroClient
from .quickbooks import QuickBooksClient
from .myob import MYOBClient


# ── Client Factory ──────────────────────────────────────────────────

_CLIENTS = {
    "xero": lambda: XeroClient(),
    "quickbooks": lambda: QuickBooksClient(),
    "myob": lambda: MYOBClient(),
}


def get_client(platform: str) -> AccountingClient:
    """Get an API client for the given platform."""
    factory = _CLIENTS.get(platform)
    if not factory:
        raise ValueError(f"Unsupported platform: {platform}. Supported: {SUPPORTED_PLATFORMS}")
    return factory()


# ── Sync Engine ─────────────────────────────────────────────────────

class SyncEngine:
    """Coordinated sync engine for all accounting platforms."""

    def __init__(self):
        self.config = get_config()

    # ── Connection Management ─────────────────────────────────────────

    def get_connections(self, db_conn, business_id: str = "",
                        platform: str = "") -> list[dict]:
        """Get active accounting connections from the database."""
        query = "SELECT * FROM accounting_connections WHERE is_active = 1"
        params = []
        if business_id:
            query += " AND business_id = ?"
            params.append(business_id)
        if platform:
            query += " AND platform = ?"
            params.append(platform)

        rows = db_conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def save_connection(self, db_conn, conn: AccountingConnection):
        """Save or update an accounting connection."""
        existing = db_conn.execute(
            "SELECT id FROM accounting_connections WHERE id = ?",
            (conn.id,),
        ).fetchone()

        data = conn.to_dict()
        data["updated_at"] = utc_now()

        if existing:
            cols = ", ".join(f"{k} = ?" for k in data)
            vals = list(data.values()) + [conn.id]
            db_conn.execute(f"UPDATE accounting_connections SET {cols} WHERE id = ?", vals)
        else:
            data["created_at"] = data.get("created_at", utc_now())
            cols = ", ".join(data.keys())
            placeholders = ", ".join("?" for _ in data)
            db_conn.execute(
                f"INSERT INTO accounting_connections ({cols}) VALUES ({placeholders})",
                list(data.values()),
            )
        db_conn.commit()

    def delete_connection(self, db_conn, connection_id: str):
        """Soft-delete an accounting connection."""
        db_conn.execute(
            "UPDATE accounting_connections SET is_active = 0, updated_at = ? WHERE id = ?",
            (utc_now(), connection_id),
        )
        db_conn.commit()

    # ── Synchronisation ──────────────────────────────────────────────

    def sync_all(self, db_conn, business_id: str = "") -> list[SyncResult]:
        """Sync all active accounting connections.

        Returns list of SyncResult, one per connection.
        """
        connections = self.get_connections(db_conn, business_id=business_id)
        results = []

        for conn_dict in connections:
            conn = AccountingConnection().from_dict(conn_dict)
            result = self.sync_platform(db_conn, conn)
            results.append(result)

        return results

    def sync_platform(self, db_conn, connection: AccountingConnection) -> SyncResult:
        """Sync a single platform connection.

        1. Fetch invoices from the accounting platform
        2. Map them to Tether debtors/invoices
        3. Record sync log
        4. Update connection last_sync_at
        """
        result = SyncResult(
            platform=connection.platform,
            business_id=connection.business_id,
            started_at=utc_now(),
        )

        try:
            client = get_client(connection.platform)
            connection.business_id = connection.business_id or "biz-001"

            # Get last sync time for incremental sync
            last_sync = connection.last_sync_at or ""

            # Log the sync attempt
            self._log_sync(db_conn, connection, "started", {})

            # ── Fetch invoices ──────────────────────────────────────
            raw_invoices = client.fetch_invoices(connection, since=last_sync)
            result.invoices_fetched = len(raw_invoices)

            # ── Map to our format ───────────────────────────────────
            for raw in raw_invoices:
                invoice = AccountingInvoice(
                    id=new_id("inv-"),
                    platform=connection.platform,
                    platform_id=raw.get("platform_id", ""),
                    business_id=connection.business_id,
                    invoice_number=raw.get("invoice_number", ""),
                    contact_name=raw.get("contact_name", ""),
                    contact_email=raw.get("contact_email", ""),
                    contact_phone=raw.get("contact_phone", ""),
                    amount_cents=raw.get("amount_cents", 0),
                    currency=raw.get("currency", "AUD"),
                    issue_date=raw.get("issue_date", ""),
                    due_date=raw.get("due_date", ""),
                    status=raw.get("status", "issued"),
                    description=raw.get("description", ""),
                    line_items=raw.get("line_items", "[]"),
                    last_synced_at=utc_now(),
                )

                # Check if this invoice already exists in our DB
                existing = db_conn.execute(
                    "SELECT id FROM debtors WHERE invoice_number = ? AND business_id = ?",
                    (invoice.invoice_number, invoice.business_id),
                ).fetchone()

                if existing:
                    # Update existing debtor
                    self._update_debtor_from_invoice(db_conn, existing["id"], invoice)
                    result.invoices_updated += 1
                    invoice.debtor_id = existing["id"]
                else:
                    # Create new debtor
                    debtor_id = self._create_debtor_from_invoice(db_conn, invoice)
                    result.invoices_created += 1
                    invoice.debtor_id = debtor_id

                # Save the mapping
                self._save_invoice_mapping(db_conn, invoice)

            # ── Update timestamps ───────────────────────────────────
            connection.last_sync_at = utc_now()
            connection.last_error = ""
            self.save_connection(db_conn, connection)

            result.completed_at = utc_now()
            result.success = True

            self._log_sync(db_conn, connection, "completed", result.to_dict())

        except Exception as e:
            error_msg = str(e)
            result.completed_at = utc_now()
            result.success = False
            result.errors.append(error_msg)
            connection.last_error = error_msg
            self.save_connection(db_conn, connection)

            self._log_sync(db_conn, connection, "failed", {"error": error_msg})

        return result

    def push_payment(self, db_conn, debtor_id: str, paid_amount_cents: int,
                     paid_at: str = "") -> bool:
        """Push a payment back to the accounting platform.

        Called after a debtor pays via Stripe (or manual payment).
        This is the 'bidirectional' half — Tether payment -> accounting software.
        """
        # Find the debtor and their linked accounting invoice
        debtor = db_conn.execute(
            "SELECT * FROM debtors WHERE id = ?", (debtor_id,)
        ).fetchone()
        if not debtor:
            return False

        debtor = dict(debtor)
        invoice_number = debtor.get("invoice_number", "")

        # Find the accounting invoice mapping
        mapping = db_conn.execute(
            "SELECT * FROM invoice_mappings WHERE invoice_number = ? AND business_id = ?",
            (invoice_number, debtor.get("business_id", "")),
        ).fetchone()
        if not mapping:
            return False  # No accounting link — can't push back

        mapping = dict(mapping)
        platform = mapping.get("platform", "")

        # Get the connection for this platform
        connections = self.get_connections(
            db_conn,
            business_id=debtor.get("business_id", ""),
            platform=platform,
        )
        if not connections:
            return False

        conn = AccountingConnection().from_dict(connections[0])

        try:
            client = get_client(platform)
            success = client.update_invoice_status(
                conn, mapping["platform_invoice_id"],
                "paid", paid_amount_cents, paid_at,
            )

            if success:
                # Update our DB
                db_conn.execute(
                    "UPDATE accounting_connections SET last_sync_at = ?, updated_at = ? WHERE id = ?",
                    (utc_now(), utc_now(), conn.id),
                )
                db_conn.commit()

                # Log the push
                self._log_sync(db_conn, conn, "payment_pushed", {
                    "debtor_id": debtor_id,
                    "invoice_number": invoice_number,
                    "amount_cents": paid_amount_cents,
                    "platform": platform,
                    "platform_invoice_id": mapping["platform_invoice_id"],
                })

            return success

        except Exception as e:
            self._log_sync(db_conn, conn, "payment_failed", {
                "debtor_id": debtor_id,
                "invoice_number": invoice_number,
                "error": str(e),
            })
            return False

    # ── Webhook Handling ────────────────────────────────────────────

    def handle_webhook(self, db_conn, platform: str,
                       payload: dict, headers: dict) -> SyncResult | None:
        """Handle an incoming webhook from an accounting platform.

        Triggers a targeted sync for the affected data.
        Returns a SyncResult if data was synced, None if unhandled.
        """
        # Log the webhook
        db_conn.execute(
            """INSERT INTO sync_log (id, platform, business_id, status, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (new_id("sync-"), platform, "", "webhook_received",
             json.dumps({"payload": payload, "headers": dict(headers)}),
             utc_now()),
        )
        db_conn.commit()

        # Find relevant connections
        connections = self.get_connections(db_conn, platform=platform)

        for conn_dict in connections:
            conn = AccountingConnection().from_dict(conn_dict)
            # Trigger a sync for this connection
            result = self.sync_platform(db_conn, conn)

            if result.invoices_fetched > 0:
                return result

        return None

    # ── Debtor Mapping ──────────────────────────────────────────────

    def _create_debtor_from_invoice(self, db_conn, invoice: AccountingInvoice) -> str:
        """Create a Tether debtor from an accounting invoice."""
        debtor_id = new_id("d-")
        now = utc_now()

        # Calculate days overdue
        days_overdue = 0
        if invoice.due_date:
            try:
                due = datetime.fromisoformat(invoice.due_date.replace("Z", "+00:00"))
                days_overdue = (datetime.now(timezone.utc) - due).days
            except (ValueError, TypeError):
                pass

        db_conn.execute(
            """INSERT INTO debtors
               (id, business_id, name, email, phone, invoice_number,
                amount_cents, due_date, days_overdue, state,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (debtor_id, invoice.business_id,
             invoice.contact_name, invoice.contact_email, invoice.contact_phone,
             invoice.invoice_number, invoice.amount_cents,
             invoice.due_date, max(days_overdue, 0),
             "pending" if invoice.status in ("issued", "overdue") else invoice.status,
             now, now),
        )
        db_conn.commit()
        return debtor_id

    def _update_debtor_from_invoice(self, db_conn, debtor_id: str,
                                    invoice: AccountingInvoice):
        """Update an existing debtor from an accounting invoice."""
        now = utc_now()
        days_overdue = 0
        if invoice.due_date:
            try:
                due = datetime.fromisoformat(invoice.due_date.replace("Z", "+00:00"))
                days_overdue = (datetime.now(timezone.utc) - due).days
            except (ValueError, TypeError):
                pass

        db_conn.execute(
            """UPDATE debtors SET
               name = ?, email = ?, phone = ?, amount_cents = ?,
               due_date = ?, days_overdue = ?, state = ?,
               updated_at = ?
               WHERE id = ?""",
            (invoice.contact_name, invoice.contact_email, invoice.contact_phone,
             invoice.amount_cents, invoice.due_date, max(days_overdue, 0),
             "pending" if invoice.status in ("issued", "overdue") else invoice.status,
             now, debtor_id),
        )
        db_conn.commit()

    def _save_invoice_mapping(self, db_conn, invoice: AccountingInvoice):
        """Save the mapping between accounting invoice and Tether debtor."""
        now = utc_now()
        existing = db_conn.execute(
            "SELECT id FROM invoice_mappings WHERE platform = ? AND platform_invoice_id = ?",
            (invoice.platform, invoice.platform_id),
        ).fetchone()

        if existing:
            db_conn.execute(
                """UPDATE invoice_mappings SET
                   invoice_number = ?, business_id = ?, debtor_id = ?,
                   updated_at = ?
                   WHERE id = ?""",
                (invoice.invoice_number, invoice.business_id,
                 invoice.debtor_id, now, existing["id"]),
            )
        else:
            db_conn.execute(
                """INSERT INTO invoice_mappings
                   (id, platform, platform_invoice_id, invoice_number,
                    business_id, debtor_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (new_id("map-"), invoice.platform, invoice.platform_id,
                 invoice.invoice_number, invoice.business_id,
                 invoice.debtor_id, now, now),
            )
        db_conn.commit()

    def _log_sync(self, db_conn, connection: AccountingConnection,
                  status: str, details: dict):
        """Record a sync log entry."""
        db_conn.execute(
            """INSERT INTO sync_log
               (id, platform, business_id, connection_id, status, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (new_id("sync-"), connection.platform, connection.business_id,
             connection.id, status, json.dumps(details), utc_now()),
        )
        db_conn.commit()


# ── Sync Runner (for cron) ──────────────────────────────────────────

def run_scheduled_sync(business_id: str = "") -> str:
    """Run a full sync of all connected accounting platforms.

    Designed to be called from Hermes cron.
    Returns a summary string for the cron log.
    """
    from database import get_connection, init_db

    init_db()
    conn = get_connection()
    engine = SyncEngine()

    results = engine.sync_all(conn, business_id=business_id)

    lines = []
    for r in results:
        status = "OK" if r.success else "FAIL"
        lines.append(
            f"  [{status}] {r.platform}: "
            f"{r.invoices_fetched} fetched, "
            f"{r.invoices_created} created, "
            f"{r.invoices_updated} updated"
        )
        if r.errors:
            lines.append(f"         Errors: {'; '.join(r.errors)}")

    if not results:
        return "No active accounting connections configured."

    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    """CLI entry point for manual sync."""
    import argparse

    parser = argparse.ArgumentParser(description="Accounting Sync Engine")
    parser.add_argument("--sync", action="store_true", help="Run sync")
    parser.add_argument("--business", default="", help="Business ID filter")
    args = parser.parse_args()

    if args.sync:
        result = run_scheduled_sync(business_id=args.business)
        print(result)


if __name__ == "__main__":
    main()
