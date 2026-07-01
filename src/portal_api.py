#!/usr/bin/env python3
"""Debtor Self-Service Portal — authentication, payment plans, disputes, messaging.

Provides the business logic for the debtor self-service portal:
- Token-based authentication (HMAC-signed per-debtor tokens)
- Dashboard data: view balance, invoices, letter history
- AI-negotiated payment plan proposals
- Dispute filing and general messaging
- Stripe payment link generation
- PDF letter download
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from database import get_connection, new_id, utc_now, transaction

# ── Config ───────────────────────────────────────────────────────────

# Secret key for signing portal tokens — set via env var or defaults to a dev secret
PORTAL_SECRET_KEY = os.environ.get("PORTAL_SECRET_KEY", "tether-dev-portal-secret-key-2026").encode()
TOKEN_EXPIRY_DAYS = int(os.environ.get("TOKEN_EXPIRY_DAYS", "365"))
PLAN_INTEREST_RATE = float(os.environ.get("PLAN_INTEREST_RATE", "0.0"))  # 0% for demo


# ═══════════════════════════════════════════════════════════════════════
# Token Authentication
# ═══════════════════════════════════════════════════════════════════════

def generate_portal_token(debtor_id: str, email: str) -> str:
    """Generate a signed portal token for a debtor.

    Format: <debtor_id>:<expiry_timestamp>:<hmac_signature>
    The raw token is returned (to embed in portal URLs). Only the hash
    is stored in the database for verification.
    """
    expiry = int((datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRY_DAYS)).timestamp())
    message = f"{debtor_id}:{expiry}:{email}"
    signature = hmac.new(PORTAL_SECRET_KEY, message.encode(), hashlib.sha256).hexdigest()[:16]
    raw_token = f"{debtor_id}:{expiry}:{signature}"
    return raw_token


def verify_portal_token(raw_token: str) -> str | None:
    """Verify a portal token. Returns debtor_id if valid, None otherwise.

    Verifies:
    1. Format is correct (3 parts, debtor_id:expiry:signature)
    2. Signature matches (HMAC verification)
    3. Token is not expired
    4. Token exists in DB and is active
    """
    try:
        parts = raw_token.split(":")
        if len(parts) != 3:
            return None
        debtor_id, expiry_str, signature = parts
        expiry = int(expiry_str)

        # Check expiry
        if datetime.now(timezone.utc).timestamp() > expiry:
            return None

        # Verify signature — we need the stored email hash to reconstruct
        conn = get_connection()
        row = conn.execute(
            "SELECT d.email FROM debtors d WHERE d.id = ?", (debtor_id,)
        ).fetchone()
        if not row:
            return None

        email = row["email"]
        message = f"{debtor_id}:{expiry}:{email}"
        expected = hmac.new(PORTAL_SECRET_KEY, message.encode(), hashlib.sha256).hexdigest()[:16]

        if not hmac.compare_digest(signature, expected):
            return None

        # Check token is active in DB
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        token_row = conn.execute(
            "SELECT id FROM portal_tokens WHERE debtor_id = ? AND token_hash = ? AND is_active = 1",
            (debtor_id, token_hash),
        ).fetchone()
        if not token_row:
            return None

        # Update last_accessed
        conn.execute(
            "UPDATE portal_tokens SET last_accessed = ? WHERE id = ?",
            (utc_now(), token_row["id"]),
        )
        conn.commit()

        return debtor_id
    except (ValueError, IndexError):
        return None


def create_debtor_portal_token(debtor_id: str, email: str) -> str:
    """Store a portal token for a debtor and return the raw token.

    The raw token is sent to the debtor via email/SMS. The hash is stored in DB.
    """
    raw_token = generate_portal_token(debtor_id, email)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expiry = datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRY_DAYS)

    with transaction() as conn:
        conn.execute(
            "INSERT INTO portal_tokens (id, debtor_id, token_hash, expires_at) VALUES (?, ?, ?, ?)",
            (new_id("pt-"), debtor_id, token_hash, expiry.strftime("%Y-%m-%dT%H:%M:%S")),
        )

    return raw_token


# ═══════════════════════════════════════════════════════════════════════
# Debtor Dashboard
# ═══════════════════════════════════════════════════════════════════════

def get_debtor_dashboard(debtor_id: str) -> dict[str, Any]:
    """Get the full dashboard data for a debtor.

    Returns outstanding balance, invoice details, letter history,
    payment plan status, messages, and available actions.
    """
    conn = get_connection()

    # Get debtor from overview view
    row = conn.execute(
        "SELECT * FROM debtor_overview WHERE id = ?", (debtor_id,)
    ).fetchone()
    if not row:
        return {"error": "Debtor not found"}

    debtor = dict(row)

    # Parse payment plans JSON
    plans = []
    if debtor.get("payment_plans_json"):
        try:
            plans = json.loads(debtor["payment_plans_json"])
        except (json.JSONDecodeError, TypeError):
            plans = []

    # Get letter history (from letters table or reconstructed)
    letters = get_debtor_letters(debtor_id)

    # Get payment options (Stripe links)
    payment_links = get_payment_links(debtor_id)

    # Determine what actions are available
    available_actions = _get_available_actions(debtor)

    return {
        "debtor": {
            "id": debtor["id"],
            "name": debtor["name"],
            "email": debtor["email"],
            "phone": debtor["phone"],
            "invoice_number": debtor["invoice_number"],
            "amount_dollars": f"${debtor['amount_cents']/100:,.2f}",
            "amount_cents": debtor["amount_cents"],
            "due_date": debtor["due_date"],
            "days_overdue": debtor["days_overdue"],
            "state": debtor["state"],
            "escalation_tier": debtor["escalation_tier"],
            "dispute_reason": debtor["dispute_reason"],
            "paid_at": debtor["paid_at"],
            "paid_amount_cents": debtor["paid_amount_cents"],
            "current_step": debtor["current_step"],
            "last_action_at": debtor["last_action_at"],
            "created_at": debtor["created_at"],
            "message_count": debtor.get("message_count", 0),
            "unread_count": debtor.get("unread_count", 0),
        },
        "payment_plans": plans,
        "letters": letters,
        "payment_links": payment_links,
        "available_actions": available_actions,
    }


def _get_available_actions(debtor: dict) -> list[str]:
    """Determine which actions are available based on debtor state."""
    actions = []
    if debtor["state"] in ("pending", "active"):
        actions.extend(["make_payment", "propose_payment_plan", "send_message"])
    if debtor["state"] not in ("paid", "disputed", "written_off"):
        actions.append("file_dispute")
    if debtor["state"] == "disputed":
        actions.extend(["send_message", "view_dispute"])
    if debtor["state"] == "paid":
        actions.append("view_receipt")
    if debtor["state"] in ("manual_review",):
        actions.append("send_message")
    return actions


# ═══════════════════════════════════════════════════════════════════════
# Payment Plan Negotiation (AI-powered)
# ═══════════════════════════════════════════════════════════════════════

def propose_payment_plans(debtor_id: str) -> list[dict[str, Any]]:
    """Generate AI-negotiated payment plan options for a debtor.

    Produces 3 options with different instalment structures:
    - Quick (3 months): higher payments, less total
    - Balanced (6 months): moderate payments
    - Extended (12 months): lower payments

    Can optionally call local Gemma for personalised negotiation,
    with a calculation-based fallback.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT amount_cents, name FROM debtors WHERE id = ?", (debtor_id,)
    ).fetchone()
    if not row:
        return []
    amount_cents = row["amount_cents"]
    name = row["name"]

    # Generate plan options
    plans = _calculate_plan_options(debtor_id, amount_cents)

    # Try AI-personalised negotiation via local Gemma
    ai_plans = _ai_negotiate_plan(debtor_id, name, amount_cents)
    if ai_plans:
        # Use AI plans as primary, calculation as fallback info
        return ai_plans

    return plans


def _calculate_plan_options(debtor_id: str, amount_cents: int) -> list[dict[str, Any]]:
    """Calculate payment plan options with different instalment structures.

    Each option shows:
    - Number of instalments
    - Amount per instalment
    - Total amount (with interest if applicable)
    - First payment date
    """
    base = amount_cents

    # Option 1: Quick — 3 monthly payments
    opt1_instalments = 3
    opt1_per = round(base / opt1_instalments)

    # Option 2: Balanced — 6 monthly payments
    opt2_instalments = 6
    opt2_per = round(base / opt2_instalments)

    # Option 3: Extended — 12 monthly payments
    opt3_instalments = 12
    opt3_per = round(base / opt3_instalments)

    first_payment = (datetime.now(timezone.utc) + timedelta(days=14)).strftime("%Y-%m-%d")

    return [
        {
            "id": new_id("plan-"),
            "debtor_id": debtor_id,
            "instalments": opt1_instalments,
            "instalment_cents": opt1_per,
            "instalment_dollars": f"${opt1_per/100:,.2f}",
            "total_cents": opt1_per * opt1_instalments,
            "total_dollars": f"${(opt1_per * opt1_instalments)/100:,.2f}",
            "frequency_days": 30,
            "first_payment": first_payment,
            "label": f"Quick — {opt1_instalments} monthly payments of ${opt1_per/100:,.2f}",
            "description": "Pay off the full amount faster with higher monthly payments.",
        },
        {
            "id": new_id("plan-"),
            "debtor_id": debtor_id,
            "instalments": opt2_instalments,
            "instalment_cents": opt2_per,
            "instalment_dollars": f"${opt2_per/100:,.2f}",
            "total_cents": opt2_per * opt2_instalments,
            "total_dollars": f"${(opt2_per * opt2_instalments)/100:,.2f}",
            "frequency_days": 30,
            "first_payment": first_payment,
            "label": f"Balanced — {opt2_instalments} monthly payments of ${opt2_per/100:,.2f}",
            "description": "Spread the cost evenly over 6 months.",
        },
        {
            "id": new_id("plan-"),
            "debtor_id": debtor_id,
            "instalments": opt3_instalments,
            "instalment_cents": opt3_per,
            "instalment_dollars": f"${opt3_per/100:,.2f}",
            "total_cents": opt3_per * opt3_instalments,
            "total_dollars": f"${(opt3_per * opt3_instalments)/100:,.2f}",
            "frequency_days": 30,
            "first_payment": first_payment,
            "label": f"Extended — {opt3_instalments} monthly payments of ${opt3_per/100:,.2f}",
            "description": "Minimum monthly commitment over 12 months.",
        },
    ]


def _ai_negotiate_plan(debtor_id: str, name: str, amount_cents: int) -> list[dict] | None:
    """Attempt AI-personalised payment plan negotiation via local Gemma.

    Falls back to None if the model is unavailable — caller uses
    calculated options instead.
    """
    # Construct a negotiation prompt for Gemma
    prompt = f"""You are an AI payment negotiator for Tether collections.
A debtor named {name} owes ${amount_cents/100:,.2f}.
Propose 3 personalised payment plan options with instalment breakdowns.

For each option, include:
- Number of instalments (3, 6, or 12)
- Amount per instalment in cents
- Total amount in cents
- A friendly label
- A short description explaining the benefit

Return JSON array only, no markdown:
[{{"instalments": N, "instalment_cents": N, "total_cents": N, "label": "...", "description": "..."}}]
"""

    try:
        import urllib.request
        gemma_url = os.environ.get("GEMMA_URL", "http://localhost:8000/v1/chat/completions")
        payload = {
            "model": "gemma-4-12b-it",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 500,
            "temperature": 0.7,
        }
        req = urllib.request.Request(
            gemma_url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            content = result["choices"][0]["message"]["content"]
            # Parse JSON from response
            import re
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if json_match:
                ai_plans = json.loads(json_match.group(0))
                first_payment = (datetime.now(timezone.utc) + timedelta(days=14)).strftime("%Y-%m-%d")
                for p in ai_plans:
                    p["id"] = new_id("plan-")
                    p["debtor_id"] = debtor_id
                    p["frequency_days"] = 30
                    p["first_payment"] = first_payment
                    p["instalment_dollars"] = f"${p['instalment_cents']/100:,.2f}"
                    p["total_dollars"] = f"${p['total_cents']/100:,.2f}"
                return ai_plans
            return None
    except Exception:
        return None


def accept_payment_plan(debtor_id: str, instalments: int = 3) -> dict[str, Any]:
    """Debtor accepts a payment plan with a given number of instalments.

    Calculates the instalment schedule directly and stores the accepted plan
    in the database.

    Args:
        debtor_id: The debtor's ID
        instalments: Number of instalments (3, 6, or 12)

    Returns the accepted plan details including instalment schedule.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT amount_cents FROM debtors WHERE id = ?", (debtor_id,)
    ).fetchone()
    if not row:
        return {"error": "Debtor not found"}
    amount_cents = row["amount_cents"]

    if instalments not in (3, 6, 12):
        return {"error": "Instalments must be 3, 6, or 12"}

    instalment_cents = round(amount_cents / instalments)
    total_cents = instalment_cents * instalments

    now = utc_now()
    first_payment = (datetime.now(timezone.utc) + timedelta(days=14)).strftime("%Y-%m-%d")

    plan_data = {
        "instalments": instalments,
        "instalment_cents": instalment_cents,
        "instalment_dollars": f"${instalment_cents/100:,.2f}",
        "total_cents": total_cents,
        "total_dollars": f"${total_cents/100:,.2f}",
        "frequency_days": 30,
        "first_payment": first_payment,
    }

    with transaction() as conn:
        conn.execute(
            """INSERT INTO payment_plans
               (id, debtor_id, status, total_cents, instalments, instalment_cents,
                frequency_days, start_date, next_payment, proposed_at, accepted_at)
               VALUES (?, ?, 'accepted', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (new_id("pp-"), debtor_id, plan_data["total_cents"], plan_data["instalments"],
             plan_data["instalment_cents"], plan_data["frequency_days"],
             first_payment, first_payment, now, now),
        )

    # Generate instalment schedule
    schedule = _generate_instalment_schedule2(plan_data)

    return {
        "status": "accepted",
        "plan": plan_data,
        "schedule": schedule,
        "message": f"Payment plan accepted! Your first payment of {plan_data['instalment_dollars']} is due {first_payment}.",
    }


def _generate_instalment_schedule2(plan: dict) -> list[dict]:
    """Generate a list of instalment due dates and amounts."""
    schedule = []
    current_date = datetime.strptime(plan["first_payment"], "%Y-%m-%d")
    for i in range(plan["instalments"]):
        due = current_date + timedelta(days=plan["frequency_days"] * i)
        schedule.append({
            "instalment": i + 1,
            "due_date": due.strftime("%Y-%m-%d"),
            "amount_cents": plan["instalment_cents"],
            "amount_dollars": f"${plan['instalment_cents']/100:,.2f}",
            "status": "pending" if i > 0 else "due_now",
        })
    return schedule


def get_active_payment_plan(debtor_id: str) -> dict | None:
    """Get the active (accepted) payment plan for a debtor."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM payment_plans WHERE debtor_id = ? AND status IN ('accepted', 'active') ORDER BY created_at DESC LIMIT 1",
        (debtor_id,),
    ).fetchone()
    if not row:
        return None
    plan = dict(row)
    plan["total_dollars"] = f"${plan['total_cents']/100:,.2f}"
    plan["instalment_dollars"] = f"${plan['instalment_cents']/100:,.2f}"
    return plan


# ═══════════════════════════════════════════════════════════════════════
# Disputes & Messaging
# ═══════════════════════════════════════════════════════════════════════

def file_dispute(debtor_id: str, reason: str) -> dict[str, Any]:
    """File a dispute on behalf of a debtor.

    This:
    1. Updates the debtor's state to 'disputed'
    2. Records the dispute reason
    3. Creates a portal message
    4. Logs an audit trail entry

    Returns the dispute confirmation.
    """
    now = utc_now()
    with transaction() as conn:
        conn.execute(
            "UPDATE debtors SET state = 'disputed', dispute_reason = ?, updated_at = ? WHERE id = ?",
            (reason, now, debtor_id),
        )
        # Create a portal message
        conn.execute(
            "INSERT INTO portal_messages (id, debtor_id, direction, message_type, subject, body, created_at) "
            "VALUES (?, ?, 'debtor_to_biz', 'dispute', 'Dispute Filed', ?, ?)",
            (new_id("msg-"), debtor_id, reason, now),
        )

    # Log to audit trail
    try:
        from audit_trail import AuditTrail
        trail = AuditTrail()
        trail.log_tool_call(
            agent_id="debtor-portal",
            tool_name="portal::dispute::file",
            tool_input={"debtor_id": debtor_id, "reason": reason},
            tool_output={"status": "dispute_filed"},
            reasoning=f"Debtor {debtor_id} filed dispute via self-service portal",
        )
    except Exception:
        pass  # Audit logging is non-critical for portal operations

    return {
        "status": "dispute_filed",
        "message": "Your dispute has been received and will be reviewed.",
        "debtor_id": debtor_id,
        "reason": reason,
        "acknowledged_at": now,
    }


def send_message(debtor_id: str, subject: str, body: str, message_type: str = "general") -> dict:
    """Send a message from debtor to the business."""
    with transaction() as conn:
        conn.execute(
            "INSERT INTO portal_messages (id, debtor_id, direction, message_type, subject, body) "
            "VALUES (?, ?, 'debtor_to_biz', ?, ?, ?)",
            (new_id("msg-"), debtor_id, message_type, subject, body),
        )
    return {
        "status": "sent",
        "message": "Your message has been sent. We'll respond shortly.",
    }


def get_messages(debtor_id: str, limit: int = 20) -> list[dict]:
    """Get all messages for a debtor."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM portal_messages WHERE debtor_id = ? ORDER BY created_at DESC LIMIT ?",
        (debtor_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════
# Payment Links
# ═══════════════════════════════════════════════════════════════════════

def get_payment_links(debtor_id: str) -> list[dict]:
    """Get Stripe payment links for a debtor.

    Returns the full amount link. If an active payment plan exists,
    also returns the next instalment link.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT amount_cents, name, invoice_number FROM debtors WHERE id = ?", (debtor_id,)
    ).fetchone()
    if not row:
        return []

    amount_cents = row["amount_cents"]
    name = row["name"]
    invoice = row["invoice_number"]

    links = []

    # Full amount payment link
    try:
        from stripe_link import create_payment_link
        full_link = create_payment_link(
            amount_cents,
            f"Tether — Invoice {invoice} — {name}",
        )
        links.append({
            "type": "full_amount",
            "label": f"Pay Full Balance (${amount_cents/100:,.2f})",
            "url": full_link,
            "amount_cents": amount_cents,
        })
    except Exception:
        # Fallback simulated link
        links.append({
            "type": "full_amount",
            "label": f"Pay Full Balance (${amount_cents/100:,.2f})",
            "url": f"https://link.stripe.com/pay/{debtor_id}",
            "amount_cents": amount_cents,
            "simulated": True,
        })

    # Check for active payment plan — get next instalment link
    plan = get_active_payment_plan(debtor_id)
    if plan:
        try:
            instal_link = create_payment_link(
                plan["instalment_cents"],
                f"Tether — Instalment — {name}",
            )
            links.append({
                "type": "instalment",
                "label": f"Pay Next Instalment ({plan['instalment_dollars']})",
                "url": instal_link,
                "amount_cents": plan["instalment_cents"],
            })
        except Exception:
            links.append({
                "type": "instalment",
                "label": f"Pay Next Instalment ({plan['instalment_dollars']})",
                "url": f"https://link.stripe.com/pay/{debtor_id}-instalment",
                "amount_cents": plan["instalment_cents"],
                "simulated": True,
            })

    return links


# ═══════════════════════════════════════════════════════════════════════
# Letters
# ═══════════════════════════════════════════════════════════════════════

def get_debtor_letters(debtor_id: str) -> list[dict]:
    """Get letter history for a debtor from PDF files on disk."""
    letters_dir = Path("output/letters")
    if not letters_dir.exists():
        return []

    letters = []
    for f in sorted(letters_dir.glob(f"{debtor_id}_*.pdf"), reverse=True):
        letters.append({
            "filename": f.name,
            "path": str(f.absolute()),
            "size_kb": round(f.stat().st_size / 1024, 1),
            "generated_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
        })

    return letters


# ═══════════════════════════════════════════════════════════════════════
# Debtor CRUD (for seeding & admin)
# ═══════════════════════════════════════════════════════════════════════

def upsert_debtor(
    debtor_id: str,
    name: str,
    email: str = "",
    phone: str = "",
    invoice_number: str = "",
    amount_cents: int = 0,
    due_date: str = "",
    days_overdue: int = 0,
    escalation_tier: str = "standard",
    state: str = "pending",
    business_id: str = "biz-001",
) -> str:
    """Insert or update a debtor record. Returns the debtor ID."""
    now = utc_now()
    with transaction() as conn:
        existing = conn.execute("SELECT id FROM debtors WHERE id = ?", (debtor_id,)).fetchone()
        if existing:
            conn.execute(
                """UPDATE debtors SET name=?, email=?, phone=?, invoice_number=?,
                   amount_cents=?, due_date=?, days_overdue=?, escalation_tier=?,
                   state=?, business_id=?, updated_at=?
                   WHERE id=?""",
                (name, email, phone, invoice_number, amount_cents, due_date,
                 days_overdue, escalation_tier, state, business_id, now, debtor_id),
            )
        else:
            conn.execute(
                """INSERT INTO debtors
                   (id, business_id, name, email, phone, invoice_number, amount_cents,
                    due_date, days_overdue, escalation_tier, state, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (debtor_id, business_id, name, email, phone, invoice_number, amount_cents,
                 due_date, days_overdue, escalation_tier, state, now, now),
            )
    return debtor_id


def update_debtor_state(debtor_id: str, state: str, **kwargs) -> None:
    """Update a debtor's state and optional fields."""
    now = utc_now()
    with transaction() as conn:
        fields = {"state": state, "updated_at": now}
        fields.update(kwargs)
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(
            f"UPDATE debtors SET {sets} WHERE id=?",
            list(fields.values()) + [debtor_id],
        )


# ═══════════════════════════════════════════════════════════════════════
# Portal URL Generation
# ═══════════════════════════════════════════════════════════════════════

def get_portal_login_url(token: str) -> str:
    """Generate the full portal URL for a token."""
    base_url = os.environ.get("PORTAL_BASE_URL", "http://localhost:8008")
    return f"{base_url}/portal/{token}"
