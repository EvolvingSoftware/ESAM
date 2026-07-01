#!/usr/bin/env python3
"""ACCC-Compliant Late Fee Automation for Tether.

Configurable late fee rules (fixed fee, percentage interest, or combined).
Automatically recalculate and apply. Generate fee notices. Track fee revenue.

Compliance with Australian Consumer Law:
- Fees must be disclosed upfront in terms of trade
- Must be proportional (not a penalty)
- Interest rate should be reasonable (~8-15% p.a.)
- Fixed fees should reflect admin costs
- Must apply consistently
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from database import get_connection, transaction, new_id, utc_now


# ── ACCC-Compliant Default Rules ────────────────────────────────────

ACCC_FIXED_FEE_DEFAULT = {
    "name": "ACCC-Compliant Fixed Late Fee",
    "description": "Modest $5 flat fee per overdue invoice — reflects admin costs of follow-up.",
    "fee_type": "fixed",
    "fixed_amount_cents": 500,
    "fixed_per_invoice": 1,
    "grace_period_days": 14,
    "apply_from_day": 15,
    "apply_frequency": "once",
    "compliance_notes": "Fixed fee of $5 reflects reasonable admin costs (reminder generation, follow-up time). ACCC: must be disclosed upfront in Terms of Trade.",
}

ACCC_INTEREST_DEFAULT = {
    "name": "ACCC-Compliant Late Payment Interest",
    "description": "10% p.a. simple interest calculated daily — compensates for time value of money.",
    "fee_type": "interest",
    "interest_rate_pa": 10.0,
    "interest_calc_days": 365,
    "interest_compounding": "simple",
    "interest_max_cap_cents": 0,
    "grace_period_days": 14,
    "apply_from_day": 15,
    "apply_frequency": "daily",
    "compliance_notes": "10% p.a. simple interest is within standard commercial range (8-15%). ACCC: interest must be reasonable, not excessive or unconscionable. Simple daily calculation avoids compounding concerns.",
}

ACCC_COMBINED_DEFAULT = {
    "name": "ACCC-Compliant Combined Fee (Fixed + Interest)",
    "description": "$5 fixed admin fee + 10% p.a. simple interest daily — total solution.",
    "fee_type": "combined",
    "fixed_amount_cents": 500,
    "fixed_per_invoice": 1,
    "interest_rate_pa": 10.0,
    "interest_calc_days": 365,
    "interest_compounding": "simple",
    "interest_max_cap_cents": 0,
    "grace_period_days": 14,
    "apply_from_day": 15,
    "apply_frequency": "daily",
    "combined_order": "interest_first",
    "compliance_notes": "Combined approach: $5 fixed admin fee (reasonable cost recovery) + 10% p.a. simple interest (compensation for delayed payment). Both must be disclosed upfront.",
}


# ── Fee Calculator ──────────────────────────────────────────────────

class LateFeeCalculator:
    """Calculate late fees according to configurable rules.

    Implements Australian Consumer Law principles:
    - Proportionality — fees must not be "out of all proportion" to legitimate interests
    - Reasonable rate — 10% p.a. is within standard commercial benchmarks
    - Simple daily calculation — transparent and easy to verify
    """

    @staticmethod
    def calculate(
        rule: dict[str, Any],
        amount_cents: int,
        days_overdue: int,
    ) -> dict[str, Any]:
        """Calculate late fees for a given rule, amount, and overdue period.

        Returns breakdown dict with fixed fee, interest, total, and new balance.
        """
        fee_type = rule.get("fee_type", "interest")
        grace = rule.get("grace_period_days", 0)
        apply_from = rule.get("apply_from_day", 1)

        # Days that actually count for fee calculation
        effective_days = max(0, days_overdue - grace)
        if effective_days <= 0:
            return {
                "fixed_fee_cents": 0,
                "interest_cents": 0,
                "total_fee_cents": 0,
                "new_balance_cents": amount_cents,
                "effective_days": 0,
                "days_overdue": days_overdue,
                "grace_remaining": grace - days_overdue if days_overdue < grace else 0,
            }

        fixed_fee_cents = 0
        interest_cents = 0

        if fee_type in ("fixed", "combined"):
            fixed_amount = rule.get("fixed_amount_cents", 0)
            per_invoice = rule.get("fixed_per_invoice", 1)
            if per_invoice:
                fixed_fee_cents = fixed_amount
            else:
                fixed_fee_cents = fixed_amount * effective_days

        if fee_type in ("interest", "combined"):
            rate_pa = rule.get("interest_rate_pa", 10.0)
            calc_days = rule.get("interest_calc_days", 365)
            compounding = rule.get("interest_compounding", "simple")
            max_cap = rule.get("interest_max_cap_cents", 0)

            if compounding == "simple":
                # Daily rate = Annual rate / 365
                # Interest = Amount × Daily rate × Days
                daily_rate = rate_pa / 100.0 / calc_days
                interest_cents = int(amount_cents * daily_rate * effective_days)
            elif compounding == "daily":
                daily_rate = rate_pa / 100.0 / calc_days
                balance = amount_cents
                interest_cents = 0
                for _ in range(effective_days):
                    day_int = int(balance * daily_rate)
                    interest_cents += day_int
                    balance += day_int
            elif compounding == "monthly":
                monthly_rate = rate_pa / 100.0 / 12
                months = max(1, effective_days // 30)
                interest_cents = int(amount_cents * monthly_rate * months)

            # Apply cap if configured
            if max_cap > 0 and interest_cents > max_cap:
                interest_cents = max_cap

        total_fee_cents = fixed_fee_cents + interest_cents
        new_balance = amount_cents + total_fee_cents

        return {
            "fixed_fee_cents": fixed_fee_cents,
            "interest_cents": interest_cents,
            "total_fee_cents": total_fee_cents,
            "new_balance_cents": new_balance,
            "effective_days": effective_days,
            "days_overdue": days_overdue,
            "grace_remaining": max(0, grace - days_overdue) if days_overdue < grace else 0,
        }


# ── Late Fee Engine ─────────────────────────────────────────────────

class LateFeeEngine:
    """Manage late fee rules, assessments, and revenue tracking.

    Integrates with the database for persistent storage.
    """

    def __init__(self, business_id: str = ""):
        self.business_id = business_id

    # ── Rule CRUD ──────────────────────────────────────────────────

    def create_rule(self, **fields) -> dict[str, Any]:
        """Create a new late fee rule."""
        rule_id = new_id("lfrule-")
        now = utc_now()

        defaults = {
            "business_id": self.business_id,
            "name": "Late Fee Rule",
            "description": "",
            "fee_type": "interest",
            "fixed_amount_cents": 0,
            "fixed_per_invoice": 1,
            "interest_rate_pa": 10.0,
            "interest_calc_days": 365,
            "interest_compounding": "simple",
            "interest_max_cap_cents": 0,
            "grace_period_days": 14,
            "apply_from_day": 15,
            "apply_frequency": "daily",
            "combined_order": "interest_first",
            "requires_disclosure": 1,
            "is_active": 1,
            "compliance_notes": "ACCC: Must be disclosed upfront, reasonable, applied consistently.",
            "framework_refs": json.dumps(["accc:late_fees", "australian_consumer_law:unfair_contract_terms"]),
        }

        values = {**defaults, **fields}
        # Ensure JSON fields are serialized
        if isinstance(values.get("framework_refs"), list):
            values["framework_refs"] = json.dumps(values["framework_refs"])

        with transaction() as conn:
            conn.execute("""
                INSERT INTO late_fee_rules (
                    id, business_id, name, description,
                    fee_type, fixed_amount_cents, fixed_per_invoice,
                    interest_rate_pa, interest_calc_days, interest_compounding,
                    interest_max_cap_cents, grace_period_days, apply_from_day,
                    apply_frequency, combined_order, requires_disclosure,
                    is_active, compliance_notes, framework_refs,
                    created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?
                )
            """, (
                rule_id,
                values["business_id"], values["name"], values["description"],
                values["fee_type"], values["fixed_amount_cents"], values["fixed_per_invoice"],
                values["interest_rate_pa"], values["interest_calc_days"], values["interest_compounding"],
                values["interest_max_cap_cents"], values["grace_period_days"], values["apply_from_day"],
                values["apply_frequency"], values["combined_order"], values["requires_disclosure"],
                values["is_active"], values["compliance_notes"], values["framework_refs"],
                now, now,
            ))

        return self.get_rule(rule_id)

    def get_rule(self, rule_id: str) -> dict[str, Any] | None:
        """Get a single rule by ID."""
        conn = get_connection()
        row = conn.execute("SELECT * FROM late_fee_rules WHERE id = ?", (rule_id,)).fetchone()
        if not row:
            return None
        r = dict(row)
        if isinstance(r.get("framework_refs"), str):
            r["framework_refs"] = json.loads(r["framework_refs"])
        return r

    def list_rules(self, business_id: str | None = None, active_only: bool = True) -> list[dict[str, Any]]:
        """List all late fee rules."""
        conn = get_connection()
        query = "SELECT * FROM late_fee_rules WHERE 1=1"
        params = []
        if business_id:
            query += " AND business_id = ?"
            params.append(business_id)
        if active_only:
            query += " AND is_active = 1"
        query += " ORDER BY created_at DESC"

        rules = []
        for row in conn.execute(query, params).fetchall():
            r = dict(row)
            if isinstance(r.get("framework_refs"), str):
                r["framework_refs"] = json.loads(r["framework_refs"])
            rules.append(r)
        return rules

    def update_rule(self, rule_id: str, **fields) -> dict[str, Any]:
        """Update a late fee rule."""
        allowed = {
            "name", "description", "fee_type",
            "fixed_amount_cents", "fixed_per_invoice",
            "interest_rate_pa", "interest_calc_days", "interest_compounding",
            "interest_max_cap_cents", "grace_period_days", "apply_from_day",
            "apply_frequency", "combined_order", "requires_disclosure",
            "is_active", "compliance_notes", "framework_refs",
        }
        updates = []
        params = []
        for k, v in fields.items():
            if k in allowed:
                if isinstance(v, list):
                    v = json.dumps(v)
                updates.append(f"{k} = ?")
                params.append(v)

        if not updates:
            return self.get_rule(rule_id)

        params.append(rule_id)
        with transaction() as conn:
            conn.execute(
                f"UPDATE late_fee_rules SET {', '.join(updates)}, updated_at = ? WHERE id = ?",
                params + [utc_now()]
            )

        return self.get_rule(rule_id)

    def delete_rule(self, rule_id: str) -> bool:
        """Delete a late fee rule."""
        with transaction() as conn:
            conn.execute("DELETE FROM late_fee_rules WHERE id = ?", (rule_id,))
        return True

    def create_default_rules(self) -> list[dict[str, Any]]:
        """Create ACCC-compliant default rules for a business."""
        rules = []
        for cfg in [ACCC_FIXED_FEE_DEFAULT, ACCC_INTEREST_DEFAULT, ACCC_COMBINED_DEFAULT]:
            cfg["business_id"] = self.business_id
            rules.append(self.create_rule(**cfg))
        return rules

    # ── Fee Assessment ──────────────────────────────────────────────

    def assess_fees(
        self,
        debtor_id: str,
        debtor_name: str,
        invoice_number: str,
        amount_cents: int,
        days_overdue: int,
        rule_id: str | None = None,
    ) -> dict[str, Any]:
        """Calculate and record a fee assessment for a debtor.

        If rule_id is None, uses the first active combined rule, then interest, then fixed.
        """
        rules = self.list_rules(business_id=self.business_id)
        if not rules:
            return {"error": "No active late fee rules configured"}

        # Find the best rule
        target_rule = None
        if rule_id:
            target_rule = self.get_rule(rule_id)
        else:
            # Prefer combined, then interest, then fixed
            for rtype in ("combined", "interest", "fixed"):
                for r in rules:
                    if r["fee_type"] == rtype:
                        target_rule = r
                        break
                if target_rule:
                    break

        if not target_rule:
            return {"error": "No suitable late fee rule found"}

        # Calculate fees
        calc = LateFeeCalculator.calculate(target_rule, amount_cents, days_overdue)

        if calc["total_fee_cents"] == 0:
            return {
                "action": "no_fee",
                "reason": f"Within grace period ({calc.get('grace_remaining', 0)} days remaining)" if calc.get("grace_remaining", 0) > 0 else "No fee applicable",
                "calculation": calc,
                "rule": target_rule["name"],
            }

        # Record assessment
        assessment_id = new_id("fee-")
        with transaction() as conn:
            conn.execute("""
                INSERT INTO fee_assessments (
                    id, rule_id, debtor_id, debtor_name, invoice_number,
                    business_id, original_amount_cents, days_overdue,
                    fixed_fee_cents, interest_cents, total_fee_cents,
                    new_balance_cents, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                assessment_id, target_rule["id"], debtor_id, debtor_name, invoice_number,
                self.business_id, amount_cents, days_overdue,
                calc["fixed_fee_cents"], calc["interest_cents"], calc["total_fee_cents"],
                calc["new_balance_cents"], "applied",
                utc_now(), utc_now(),
            ))

        assessment = {
            "id": assessment_id,
            "rule_id": target_rule["id"],
            "rule_name": target_rule["name"],
            "debtor_id": debtor_id,
            "debtor_name": debtor_name,
            "invoice_number": invoice_number,
            "original_amount_cents": amount_cents,
            "days_overdue": days_overdue,
            "fixed_fee_cents": calc["fixed_fee_cents"],
            "interest_cents": calc["interest_cents"],
            "total_fee_cents": calc["total_fee_cents"],
            "new_balance_cents": calc["new_balance_cents"],
            "fee_type": target_rule["fee_type"],
            "status": "applied",
        }

        return {"action": "fee_applied", "assessment": assessment, "calculation": calc}

    def get_assessments(
        self,
        debtor_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List fee assessments, optionally filtered."""
        conn = get_connection()
        query = """
            SELECT fa.*, lfr.name AS rule_name, lfr.fee_type
            FROM fee_assessments fa
            LEFT JOIN late_fee_rules lfr ON fa.rule_id = lfr.id
            WHERE 1=1
        """
        params = []
        if debtor_id:
            query += " AND fa.debtor_id = ?"
            params.append(debtor_id)
        if status:
            query += " AND fa.status = ?"
            params.append(status)
        query += " ORDER BY fa.created_at DESC LIMIT ?"
        params.append(limit)

        return [dict(r) for r in conn.execute(query, params).fetchall()]

    def waive_fee(self, assessment_id: str, reason: str = "") -> dict[str, Any]:
        """Waive a fee assessment."""
        with transaction() as conn:
            conn.execute(
                "UPDATE fee_assessments SET status = 'waived', updated_at = ? WHERE id = ?",
                (utc_now(), assessment_id)
            )
        return self.get_assessment(assessment_id)

    def mark_fee_paid(self, assessment_id: str, amount_cents: int) -> dict[str, Any]:
        """Mark a fee as paid."""
        with transaction() as conn:
            conn.execute(
                "UPDATE fee_assessments SET status = 'paid', paid_at = ?, paid_amount_cents = ?, updated_at = ? WHERE id = ?",
                (utc_now(), amount_cents, utc_now(), assessment_id)
            )
        return self.get_assessment(assessment_id)

    def get_assessment(self, assessment_id: str) -> dict[str, Any] | None:
        """Get a single fee assessment."""
        conn = get_connection()
        row = conn.execute("""
            SELECT fa.*, lfr.name AS rule_name, lfr.fee_type
            FROM fee_assessments fa
            LEFT JOIN late_fee_rules lfr ON fa.rule_id = lfr.id
            WHERE fa.id = ?
        """, (assessment_id,)).fetchone()
        return dict(row) if row else None

    # ── Revenue Tracking ────────────────────────────────────────────

    def get_revenue(self, business_id: str | None = None) -> dict[str, Any]:
        """Get aggregated fee revenue statistics."""
        conn = get_connection()
        row = conn.execute("SELECT * FROM fee_summary").fetchone()
        base = dict(row) if row else {
            "total_fees_assessed_cents": 0,
            "total_fees_collected_cents": 0,
            "total_fees_waived_cents": 0,
            "total_fees_outstanding_cents": 0,
            "total_assessments": 0,
            "pending_assessments": 0,
            "paid_assessments": 0,
            "waived_assessments": 0,
        }

        # Add formatted dollar values
        return {
            **base,
            "total_fees_assessed": f"${base['total_fees_assessed_cents']/100:,.2f}",
            "total_fees_collected": f"${base['total_fees_collected_cents']/100:,.2f}",
            "total_fees_waived": f"${base['total_fees_waived_cents']/100:,.2f}",
            "total_fees_outstanding": f"${base['total_fees_outstanding_cents']/100:,.2f}",
            "collection_rate": round(
                (base['total_fees_collected_cents'] / max(base['total_fees_assessed_cents'], 1)) * 100, 1
            ),
            "waive_rate": round(
                (base['total_fees_waived_cents'] / max(base['total_fees_assessed_cents'], 1)) * 100, 1
            ),
        }

    # ── Fee Notice Generation ───────────────────────────────────────

    def generate_fee_notice(self, assessment_id: str) -> dict[str, Any]:
        """Generate a fee notice text for an assessment.

        Returns a structured fee notice that can be rendered as text, email, or PDF.
        """
        assessment = self.get_assessment(assessment_id)
        if not assessment:
            return {"error": "Assessment not found"}

        days = assessment["days_overdue"]
        orig = assessment["original_amount_cents"]
        fixed = assessment["fixed_fee_cents"]
        interest = assessment["interest_cents"]
        total_fee = assessment["total_fee_cents"]
        new_balance = assessment["new_balance_cents"]

        lines = [
            f"LATE FEE NOTICE",
            f"=" * 60,
            f"",
            f"Debtor:          {assessment['debtor_name']}",
            f"Invoice:         {assessment['invoice_number']}",
            f"Days Overdue:    {days}",
            f"",
            f"ORIGINAL BALANCE:            ${orig/100:>10,.2f}",
        ]

        if fixed > 0:
            lines.append(f"Late Fee (Admin):            ${fixed/100:>10,.2f}")
        if interest > 0:
            rate = self.get_rule(assessment["rule_id"])
            rate_str = f"{rate['interest_rate_pa']}% p.a." if rate else "applied rate"
            lines.append(f"Interest ({rate_str}):         ${interest/100:>10,.2f}")

        lines.extend([
            f"─── ─── ─── ─── ─── ─── ─── ─── ─── ─── ─── ───",
            f"TOTAL LATE FEE:               ${total_fee/100:>10,.2f}",
            f"NEW BALANCE DUE:              ${new_balance/100:>10,.2f}",
            f"",
            f"Payment: https://link.stripe.com/pay/{assessment['debtor_id']}",
            f"",
            f"Note: Late fees are charged in accordance with your agreed",
            f"Terms of Trade. Fee calculation: simple daily interest at a",
            f"reasonable commercial rate, plus recovery of administrative costs.",
            f"",
            f"If you believe this notice is in error, please contact us immediately.",
            f"=" * 60,
        ])

        notice_text = "\n".join(lines)

        # Mark notice as generated
        with transaction() as conn:
            conn.execute(
                "UPDATE fee_assessments SET notice_generated = 1, updated_at = ? WHERE id = ?",
                (utc_now(), assessment_id)
            )

        return {
            "assessment_id": assessment_id,
            "notice_text": notice_text,
            "assessment": assessment,
        }

    def get_fee_notice_for_email(self, debtor_name: str, invoice: str,
                                  amount_cents: int, fee_cents: int,
                                  total_cents: int, days_overdue: int) -> str:
        """Generate a short fee notice suitable for email body."""
        return (
            f"LATE FEE APPLIED — {debtor_name}\n"
            f"Invoice: {invoice} ({days_overdue} days overdue)\n"
            f"Original: ${amount_cents/100:,.2f}\n"
            f"Late Fee: +${fee_cents/100:,.2f}\n"
            f"Total Due: ${total_cents/100:,.2f}\n"
            f"Pay now: https://link.stripe.com/pay/...\n"
        )


# ── CLI Demo ────────────────────────────────────────────────────────

def main():
    """Run a full demo of the late fee automation system."""
    from database import init_db
    init_db()

    print(f"\n{'='*65}")
    print(f"  ACCC-COMPLIANT LATE FEE AUTOMATION — DEMO")
    print(f"{'='*65}\n")

    engine = LateFeeEngine("biz-001")

    # ── Step 1: Create ACCC-compliant default rules ──
    print("─" * 50)
    print("  STEP 1: ACCC-COMPLIANT DEFAULT RULES")
    print("─" * 50)
    rules = engine.create_default_rules()
    for r in rules:
        print(f"  ✅ {r['fee_type'].upper():<10}  {r['name']:<45}  ${r.get('fixed_amount_cents', 0)/100:,.2f} / {r.get('interest_rate_pa', 0)}% p.a.")
    print()

    # ── Step 2: Assess fees for sample debtors ──
    print("─" * 50)
    print("  STEP 2: FEE ASSESSMENTS")
    print("─" * 50)
    test_debtors = [
        ("d-001", "Acme Corp", "INV-2026-042", 345000, 18),
        ("d-002", "Beta LLC", "INV-2026-043", 1200000, 45),
        ("d-003", "Gamma Inc", "INV-2026-044", 85000, 6),
        ("d-004", "Delta Co", "INV-2026-045", 2500000, 60),
        ("d-005", "Epsilon Pty", "INV-2026-046", 425000, 10),
    ]

    assessments = []
    for did, name, inv, amount, days in test_debtors:
        result = engine.assess_fees(did, name, inv, amount, days)
        if result.get("action") == "fee_applied":
            a = result["assessment"]
            assessments.append(a)
            print(f"  💰 {name:<12}  ${amount/100:>8,.2f}  {days:3d}d overdue  →  Fee: ${a['total_fee_cents']/100:>6,.2f}  (fixed=${a['fixed_fee_cents']/100:,.2f}, int=${a['interest_cents']/100:,.2f})")
        else:
            print(f"  ⏳ {name:<12}  ${amount/100:>8,.2f}  {days:3d}d overdue  →  {result.get('reason', 'No fee')}")

    print()

    # ── Step 3: Generate fee notices ──
    print("─" * 50)
    print("  STEP 3: FEE NOTICES")
    print("─" * 50)
    if assessments:
        notice = engine.generate_fee_notice(assessments[0]["id"])
        if "notice_text" in notice:
            print(f"  Sample fee notice for {notice['assessment']['debtor_name']}:")
            print()
            for line in notice["notice_text"].split("\n")[:15]:
                print(f"    {line}")
            print()
    print()

    # ── Step 4: Simulate fee payment ──
    print("─" * 50)
    print("  STEP 4: FEE PAYMENT SIMULATION")
    print("─" * 50)
    if len(assessments) >= 3:
        engine.mark_fee_paid(assessments[2]["id"], assessments[2]["total_fee_cents"])
        print(f"  ✅ Fee paid: {assessments[2]['debtor_name']} — ${assessments[2]['total_fee_cents']/100:,.2f}")
        engine.waive_fee(assessments[1]["id"], "Customer goodwill adjustment")
        print(f"  🆓 Fee waived: {assessments[1]['debtor_name']} — ${assessments[1]['total_fee_cents']/100:,.2f}")
    print()

    # ── Step 5: Revenue report ──
    print("─" * 50)
    print("  STEP 5: FEE REVENUE REPORT")
    print("─" * 50)
    revenue = engine.get_revenue()
    print(f"  Total Assessed:     {revenue['total_fees_assessed']}")
    print(f"  Total Collected:    {revenue['total_fees_collected']}")
    print(f"  Total Waived:       {revenue['total_fees_waived']}")
    print(f"  Total Outstanding:  {revenue['total_fees_outstanding']}")
    print(f"  Collection Rate:    {revenue['collection_rate']}%")
    print(f"  Waive Rate:         {revenue['waive_rate']}%")
    print()

    # ── Step 6: Compliance summary ──
    print("─" * 50)
    print("  ACCC COMPLIANCE NOTES")
    print("─" * 50)
    print("""
  1. Fees must be disclosed UPFRONT in Terms of Trade ✓
  2. Fees must be PROPORTIONAL (not a penalty) ✓
     - $5 fixed fee reflects admin costs
     - 10% p.a. interest is within reasonable range
  3. Apply CONSISTENTLY across all debtors ✓
  4. Grace period of 14 days before fees apply ✓
  5. Simple daily calculation — transparent ✓
  6. No compound interest — avoids unfairness claims ✓
  """)
    print(f"{'='*65}")
    print(f"  DEMO COMPLETE")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    main()
