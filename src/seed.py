#!/usr/bin/env python3
"""Seed database with demo data for hackathon demo.

Provides seed_all() and clear_all() for the POST /api/seed endpoints.
Idempotent: skips if demo data already exists.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from database import get_connection, init_db, new_id, utc_now


# ═══════════════════════════════════════════════════════════════════════
# Demo Data Constants
# ═══════════════════════════════════════════════════════════════════════

DEMO_USER_EMAIL = "demo@evolving.software"
DEMO_USER_NAME = "Demo Business"
DEMO_USER_PASSWORD = "demo123"

DEMO_ENTITY_NAME = "Evolving Solutions Pty Ltd"
DEMO_ENTITY_ABN = "12 345 678 901"

DEMO_DEBTORS = [
    {
        "name": "James Mitchell",
        "email": "james.mitchell@outlook.com.au",
        "phone": "0412 345 678",
        "invoice_number": "INV-2026-101",
        "amount_cents": 450000,
        "due_date": "2026-05-01",
        "days_overdue": 49,
        "state": "active",
    },
    {
        "name": "Sarah Chen",
        "email": "sarah.chen@gmail.com",
        "phone": "0423 456 789",
        "invoice_number": "INV-2026-102",
        "amount_cents": 125000,
        "due_date": "2026-06-01",
        "days_overdue": 18,
        "state": "active",
    },
    {
        "name": "Michael O'Brien",
        "email": "mobrien@brisbaneplumbing.com.au",
        "phone": "0434 567 890",
        "invoice_number": "INV-2026-103",
        "amount_cents": 890000,
        "due_date": "2026-04-15",
        "days_overdue": 65,
        "state": "active",
    },
    {
        "name": "Priya Sharma",
        "email": "priya@melbourneconsulting.com.au",
        "phone": "0445 678 901",
        "invoice_number": "INV-2026-104",
        "amount_cents": 210000,
        "due_date": "2026-06-10",
        "days_overdue": 9,
        "state": "active",
    },
    {
        "name": "David Tanaka",
        "email": "david.t@sydneylogistics.com.au",
        "phone": "0456 789 012",
        "invoice_number": "INV-2026-105",
        "amount_cents": 675000,
        "due_date": "2026-05-20",
        "days_overdue": 30,
        "state": "active",
    },
]


# ═══════════════════════════════════════════════════════════════════════
# Ensure helper tables exist
# ═══════════════════════════════════════════════════════════════════════

def _ensure_seed_tables():
    """Create payment_links and letter_of_demand tables if they don't exist."""
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payment_links (
            id TEXT PRIMARY KEY,
            debtor_id TEXT NOT NULL,
            business_id TEXT NOT NULL DEFAULT 'biz-001',
            amount_cents INTEGER NOT NULL,
            currency TEXT DEFAULT 'aud',
            description TEXT DEFAULT '',
            stripe_url TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS letter_of_demand (
            id TEXT PRIMARY KEY,
            debtor_id TEXT NOT NULL,
            debtor_name TEXT NOT NULL,
            business_name TEXT NOT NULL,
            invoice_number TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            days_overdue INTEGER NOT NULL,
            state TEXT DEFAULT 'NSW',
            status TEXT DEFAULT 'draft',
            generated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
    """)
    conn.commit()


# ═══════════════════════════════════════════════════════════════════════
# Seed
# ═══════════════════════════════════════════════════════════════════════

def seed_all() -> dict:
    """Create all demo data. Returns counts of what was created.

    Idempotent: if demo email already exists, skips creation and returns
    the existing state.
    """
    conn = get_connection()
    _ensure_seed_tables()
    from auth import create_users_table, hash_password
    create_users_table()

    result = {
        "users": 0,
        "entities": 0,
        "entity_users": 0,
        "debtors": 0,
        "audit_logs": 0,
        "payment_links": 0,
        "letters_of_demand": 0,
    }

    now = utc_now()

    # 1. User
    existing_user = conn.execute(
        "SELECT id FROM users WHERE email = ?", (DEMO_USER_EMAIL,)
    ).fetchone()
    if existing_user:
        user_id = existing_user["id"]
    else:
        user_id = new_id("usr-")
        conn.execute(
            "INSERT INTO users (id, email, name, password_hash, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 1, ?, ?)",
            (user_id, DEMO_USER_EMAIL, DEMO_USER_NAME,
             hash_password(DEMO_USER_PASSWORD), now, now),
        )
        result["users"] += 1

    # 2. Entity
    existing_entity = conn.execute(
        "SELECT id FROM entities WHERE abn = ?", (DEMO_ENTITY_ABN,)
    ).fetchone()
    if existing_entity:
        entity_id = existing_entity["id"]
    else:
        entity_id = new_id("ent-")
        conn.execute(
            "INSERT INTO entities (id, name, abn, created_at) VALUES (?, ?, ?, ?)",
            (entity_id, DEMO_ENTITY_NAME, DEMO_ENTITY_ABN, now),
        )
        result["entities"] += 1

    # 3. Link user → entity as admin
    existing_link = conn.execute(
        "SELECT id FROM entity_users WHERE user_id = ? AND entity_id = ?",
        (user_id, entity_id),
    ).fetchone()
    if not existing_link:
        conn.execute(
            "INSERT INTO entity_users (id, entity_id, user_id, role, permissions_json, created_at) "
            "VALUES (?, ?, ?, 'admin', ?, ?)",
            (new_id("ue-"), entity_id, user_id,
             '{"create":true,"read":true,"update":true,"delete":true,"manage_users":true}', now),
        )
        result["entity_users"] += 1

    # 4. Debtors
    for d in DEMO_DEBTORS:
        existing_debtor = conn.execute(
            "SELECT id FROM debtors WHERE invoice_number = ?",
            (d["invoice_number"],),
        ).fetchone()
        if existing_debtor:
            debtor_id = existing_debtor["id"]
        else:
            debtor_id = new_id("deb-")
            conn.execute(
                "INSERT INTO debtors (id, business_id, name, email, phone, invoice_number, "
                "amount_cents, due_date, days_overdue, state, created_at, updated_at) "
                "VALUES (?, 'biz-001', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (debtor_id, d["name"], d["email"], d["phone"],
                 d["invoice_number"], d["amount_cents"], d["due_date"],
                 d["days_overdue"], d["state"], now, now),
            )
            result["debtors"] += 1

    # 5. Audit entries
    audit_count = conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
    if audit_count < 2:
        for idx, (category, action, resource, summary) in enumerate([
            ("tool_call", "execute", "tether::escalation::process",
             "Processed 5 debtors through escalation engine"),
            ("data_access", "read", "debtors::overview",
             "Fetched debtor overview for dashboard"),
        ], start=1):
            prev = conn.execute(
                "SELECT hash FROM audit_logs ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            prev_hash = prev["hash"] if prev else ""
            entry = {
                "id": new_id("audit-"),
                "agent_id": "tether-collections",
                "workflow_id": "wf-tether",
                "run_id": "",
                "category": category,
                "action": action,
                "resource": resource,
                "resource_type": "",
                "summary": summary,
                "input_snapshot": "{}",
                "output_snapshot": "{}",
                "reasoning_trace": "",
                "policy_id": "",
                "policy_decision": "",
                "policy_evidence": "{}",
                "actor": "tether-collections",
                "human_approver": "",
                "previous_hash": prev_hash,
                "hash": "",
                "created_at": now,
            }
            import hashlib, json
            entry["hash"] = hashlib.sha256(
                json.dumps(entry, sort_keys=True, default=str).encode()
            ).hexdigest()
            conn.execute(
                "INSERT INTO audit_logs (id, agent_id, workflow_id, run_id, category, "
                "action, resource, resource_type, summary, input_snapshot, output_snapshot, "
                "reasoning_trace, policy_id, policy_decision, policy_evidence, actor, "
                "human_approver, previous_hash, hash, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(entry[k] for k in [
                    "id", "agent_id", "workflow_id", "run_id", "category",
                    "action", "resource", "resource_type", "summary",
                    "input_snapshot", "output_snapshot", "reasoning_trace",
                    "policy_id", "policy_decision", "policy_evidence", "actor",
                    "human_approver", "previous_hash", "hash", "created_at",
                ]),
            )
            result["audit_logs"] += 1

    # 6. Payment link (for the first debtor)
    existing_pl = conn.execute(
        "SELECT id FROM payment_links WHERE debtor_id = ?",
        (DEMO_DEBTORS[0]["invoice_number"],),
    ).fetchone()
    if not existing_pl:
        first_debtor_row = conn.execute(
            "SELECT id FROM debtors WHERE invoice_number = ?",
            (DEMO_DEBTORS[0]["invoice_number"],),
        ).fetchone()
        if first_debtor_row:
            pl_debtor_id = first_debtor_row["id"]
            conn.execute(
                "INSERT INTO payment_links (id, debtor_id, business_id, amount_cents, "
                "currency, description, stripe_url, status, created_at) "
                "VALUES (?, ?, 'biz-001', ?, 'aud', ?, ?, 'active', ?)",
                (new_id("pl-"), pl_debtor_id,
                 DEMO_DEBTORS[0]["amount_cents"],
                 f"Payment for {DEMO_DEBTORS[0]['invoice_number']}",
                 f"https://link.stripe.com/pay/demo-{pl_debtor_id}",
                 now),
            )
            result["payment_links"] += 1

    # 7. Letter of demand (for the most overdue debtor)
    existing_lod = conn.execute(
        "SELECT id FROM letter_of_demand WHERE invoice_number = ?",
        (DEMO_DEBTORS[2]["invoice_number"],),
    ).fetchone()
    if not existing_lod:
        lod_debtor = conn.execute(
            "SELECT id FROM debtors WHERE invoice_number = ?",
            (DEMO_DEBTORS[2]["invoice_number"],),
        ).fetchone()
        if lod_debtor:
            conn.execute(
                "INSERT INTO letter_of_demand (id, debtor_id, debtor_name, business_name, "
                "invoice_number, amount_cents, days_overdue, state, status, generated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'NSW', 'generated', ?)",
                (new_id("lod-"), lod_debtor["id"],
                 DEMO_DEBTORS[2]["name"], DEMO_ENTITY_NAME,
                 DEMO_DEBTORS[2]["invoice_number"],
                 DEMO_DEBTORS[2]["amount_cents"],
                 DEMO_DEBTORS[2]["days_overdue"], now),
            )
            result["letters_of_demand"] += 1

    conn.commit()
    return result


# ═══════════════════════════════════════════════════════════════════════
# Clear
# ═══════════════════════════════════════════════════════════════════════

def clear_all() -> dict:
    """Delete all seed/demo data. Returns counts of what was removed."""
    conn = get_connection()
    _ensure_seed_tables()

    result = {
        "users_deleted": 0,
        "entities_deleted": 0,
        "entity_users_deleted": 0,
        "debtors_deleted": 0,
        "audit_logs_deleted": 0,
        "payment_links_deleted": 0,
        "letters_of_demand_deleted": 0,
    }

    # Demo debtors by invoice number
    invoice_numbers = [d["invoice_number"] for d in DEMO_DEBTORS]

    # 1. Payment links for demo debtors
    r = conn.execute("DELETE FROM payment_links").rowcount
    result["payment_links_deleted"] = r

    # 2. Letters of demand for demo debtors
    r = conn.execute("DELETE FROM letter_of_demand").rowcount
    result["letters_of_demand_deleted"] = r

    # 3. Audit logs
    r = conn.execute("DELETE FROM audit_logs").rowcount
    result["audit_logs_deleted"] = r

    # 4. Debtors
    r = conn.execute(
        "DELETE FROM debtors WHERE invoice_number IN ({})".format(
            ",".join("?" * len(invoice_numbers))
        ),
        invoice_numbers,
    ).rowcount
    result["debtors_deleted"] = r

    # 5. Entity users
    r = conn.execute("DELETE FROM entity_users").rowcount
    result["entity_users_deleted"] = r

    # 6. Entities
    r = conn.execute(
        "DELETE FROM entities WHERE abn = ?", (DEMO_ENTITY_ABN,)
    ).rowcount
    result["entities_deleted"] = r

    # 7. Demo user
    r = conn.execute(
        "DELETE FROM users WHERE email = ?", (DEMO_USER_EMAIL,)
    ).rowcount
    result["users_deleted"] = r

    conn.commit()
    return result


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Seed demo data")
    parser.add_argument("--clear", action="store_true", help="Clear all seed data")
    args = parser.parse_args()

    if args.clear:
        result = clear_all()
        print(f"Cleared: {result}")
    else:
        result = seed_all()
        print(f"Seeded: {result}")
