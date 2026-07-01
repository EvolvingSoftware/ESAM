#!/usr/bin/env python3
"""Tether Escalation Engine — configurable rules, dispute handling, manual intervention.

Manages the lifecycle of each debtor through the collections workflow,
applying configurable escalation rules, handling disputes, and triggering
manual intervention points when thresholds are crossed.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable


class DebtorState(Enum):
    PENDING = "pending"
    ACTIVE = "active"
    PAID = "paid"
    DISPUTED = "disputed"
    MANUAL_REVIEW = "manual_review"
    WRITTEN_OFF = "written_off"
    ESCALATED_EXTERNAL = "escalated_external"
    PROMISE_PAUSED = "promise_paused"       # Paused because debtor promised to pay
    OOO_SKIPPED = "ooo_skipped"             # Skipped current step due to OOO


@dataclass
class EscalationRule:
    """A single escalation rule defining what happens at a given step."""
    day: int
    channel: str
    tone: str
    action: str                 # send_email | send_sms | generate_pdf | notify_owner | halt
    require_human: bool = False # requires manual intervention before proceeding
    max_attempts: int = 1
    cooldown_days: int = 0


@dataclass
class DebtorRecord:
    """Full lifecycle record for a single debtor."""
    id: str
    name: str
    business_name: str
    email: str = ""
    phone: str = ""
    invoice_number: str = ""
    amount_cents: int = 0
    due_date: str = ""
    
    # Lifecycle
    state: DebtorState = DebtorState.PENDING
    escalation_tier: str = "standard"
    days_overdue: int = 0
    current_step: int = 0       # 0 = pre-escalation
    last_action_at: str = ""
    created_at: str = ""
    
    # Tracking
    communication_log: list[dict] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)
    dispute_reason: str = ""
    paid_at: str = ""
    paid_amount_cents: int = 0


# ── Default Escalation Rules ────────────────────────────────────────

DEFAULT_ESCALATION = {
    "standard": [
        EscalationRule(day=1,  channel="email", tone="friendly",     action="send_email"),
        EscalationRule(day=7,  channel="sms",   tone="direct",       action="send_sms"),
        EscalationRule(day=14, channel="email", tone="firm",         action="generate_pdf", require_human=False),
        EscalationRule(day=30, channel="email", tone="formal",       action="generate_pdf"),
        EscalationRule(day=45, channel="email", tone="formal",       action="notify_owner", require_human=True),
    ],
    "high_value": [
        EscalationRule(day=1,  channel="email", tone="professional", action="send_email"),
        EscalationRule(day=7,  channel="email", tone="firm",         action="send_email"),
        EscalationRule(day=14, channel="email", tone="firm",         action="generate_pdf"),
        EscalationRule(day=21, channel="email", tone="formal",       action="notify_owner", require_human=True),
        EscalationRule(day=30, channel="email", tone="formal",       action="notify_owner", require_human=True),
    ],
    "disputed": [
        EscalationRule(day=1,  channel="email", tone="neutral",      action="halt", require_human=True),
    ],
}

# ── Escalation Engine ───────────────────────────────────────────────

class EscalationEngine:
    """Manages debtor escalation through configurable rules with hooks for:
    - Dispute detection and handling
    - Manual intervention points
    - Business owner notification triggers
    - Grace periods and cooldowns
    """

    def __init__(self, rules: dict[str, list[EscalationRule]] | None = None):
        self.rules = rules or DEFAULT_ESCALATION
        self._action_handlers: dict[str, Callable] = {}

    def register_handler(self, action: str, handler: Callable):
        """Register a handler function for a specific action type."""
        self._action_handlers[action] = handler

    def get_next_action(self, debtor: DebtorRecord) -> tuple[EscalationRule | None, str]:
        """Determine the next action for a debtor based on their current state.
        
        Returns:
            (rule, reason) tuple where rule is None if no action needed.
        """
        if debtor.state == DebtorState.PAID:
            return None, "Debtor has already paid"

        if debtor.state == DebtorState.DISPUTED:
            return None, "Debtor has disputed — awaiting review"

        if debtor.state == DebtorState.WRITTEN_OFF:
            return None, "Debt has been written off"

        # Calculate current day of escalation
        try:
            due = datetime.strptime(debtor.due_date, "%Y-%m-%d")
        except (ValueError, TypeError):
            due = datetime.utcnow() - timedelta(days=debtor.days_overdue)

        days_since_due = (datetime.utcnow() - due).days
        debtor.days_overdue = max(0, days_since_due)

        # Find the applicable rule set
        tier_rules = self.rules.get(debtor.escalation_tier, self.rules["standard"])

        # Find the highest applicable rule
        applicable_rule = None
        for rule in sorted(tier_rules, key=lambda r: r.day, reverse=True):
            if debtor.current_step < rule.day <= days_since_due:
                applicable_rule = rule
                break

        if not applicable_rule:
            if days_since_due > max(r.day for r in tier_rules):
                return None, "All escalation steps completed — notify owner for external action"
            return None, "No escalation action due yet"
        
        if applicable_rule.require_human:
            debtor.state = DebtorState.MANUAL_REVIEW

        return applicable_rule, f"Step {applicable_rule.day} — {applicable_rule.tone} {applicable_rule.action}"

    def process_debtor(self, debtor: DebtorRecord) -> dict[str, Any]:
        """Process a single debtor through the escalation engine.
        
        Returns a result dict with the action taken (or reason for no action).
        """
        rule, reason = self.get_next_action(debtor)
        
        if not rule:
            return {"action": "none", "reason": reason, "debtor_id": debtor.id}

        # Find and call the handler
        handler = self._action_handlers.get(rule.action)
        if handler:
            try:
                result = handler(debtor, rule)
                debtor.current_step = rule.day
                debtor.last_action_at = datetime.utcnow().isoformat()
                debtor.communication_log.append({
                    "step": rule.day,
                    "action": rule.action,
                    "channel": rule.channel,
                    "tone": rule.tone,
                    "timestamp": debtor.last_action_at,
                    "result": str(result),
                })
                return {"action": rule.action, "rule": rule, "handler_result": result}
            except Exception as e:
                return {"action": "error", "error": str(e), "debtor_id": debtor.id}
        else:
            return {"action": "no_handler", "handler_needed": rule.action, "debtor_id": debtor.id}

    def mark_disputed(self, debtor: DebtorRecord, reason: str):
        """Handle a dispute — halt automated collection and flag for review."""
        debtor.state = DebtorState.DISPUTED
        debtor.dispute_reason = reason
        debtor.notes.append({
            "type": "dispute",
            "timestamp": datetime.utcnow().isoformat(),
            "content": reason,
        })

    def mark_paid(self, debtor: DebtorRecord, amount_cents: int):
        """Close out a debtor who has paid."""
        debtor.state = DebtorState.PAID
        debtor.paid_at = datetime.utcnow().isoformat()
        debtor.paid_amount_cents = amount_cents

    def request_manual_review(self, debtor: DebtorRecord, reason: str):
        """Flag a debtor for manual review by the business owner."""
        debtor.state = DebtorState.MANUAL_REVIEW
        debtor.notes.append({
            "type": "manual_review",
            "timestamp": datetime.utcnow().isoformat(),
            "content": reason,
        })

    # ── Reply-Aware Processing ─────────────────────────────────────

    PROMISE_GRACE_DAYS = 7     # How long to pause escalation after promise-to-pay
    OOO_RETRY_DAYS = 14        # How long to skip after OOO

    def check_reply_intervention(self, debtor: DebtorRecord) -> dict | None:
        """Check if a debtor has recent replies that affect escalation.

        Returns an action dict if intervention is needed, None otherwise.
        """
        from database import get_connection

        conn = get_connection()
        pending = conn.execute(
            """SELECT id, category, action_taken, summary, created_at
               FROM debtor_replies
               WHERE debtor_id = ? AND resolution = 'pending'
               ORDER BY created_at DESC
               LIMIT 1""",
            (debtor.id,),
        ).fetchone()

        if not pending:
            return None

        category = pending["category"]
        action = pending["action_taken"]
        reply_id = pending["id"]
        summary = pending["summary"]

        if category == "dispute":
            debtor.state = DebtorState.DISPUTED
            debtor.dispute_reason = summary
            return {
                "intervention": "reply_dispute",
                "reply_id": reply_id,
                "reason": f"Debtor replied with dispute: {summary}",
                "stop_escalation": True,
            }

        if category == "promise_to_pay":
            # Pause escalation for grace period
            debtor.state = DebtorState.PROMISE_PAUSED
            return {
                "intervention": "reply_promise_to_pay",
                "reply_id": reply_id,
                "reason": f"Debtor promised to pay: {summary}",
                "grace_days": self.PROMISE_GRACE_DAYS,
                "stop_escalation": True,
            }

        if category == "out_of_office":
            # Skip current step, schedule retry
            debtor.state = DebtorState.OOO_SKIPPED
            return {
                "intervention": "reply_ooo",
                "reply_id": reply_id,
                "reason": f"Debtor out of office: {summary}",
                "skip_days": self.OOO_RETRY_DAYS,
                "stop_escalation": True,
            }

        if category == "query":
            debtor.state = DebtorState.MANUAL_REVIEW
            return {
                "intervention": "reply_query",
                "reply_id": reply_id,
                "reason": f"Debtor has a question: {summary}",
                "stop_escalation": True,
            }

        return None

    def process_with_reply_check(self, debtor: DebtorRecord) -> dict:
        """Process a debtor but first check for reply interventions.

        If a debtor has a pending classified reply, the reply action
        takes priority over normal escalation.
        """
        intervention = self.check_reply_intervention(debtor)
        if intervention:
            return {
                "action": intervention.get("intervention", "reply_intervention"),
                "reason": intervention["reason"],
                "debtor_id": debtor.id,
                "reply_id": intervention["reply_id"],
            }
        return self.process_debtor(debtor)

    def is_eligible_for_escalation(self, debtor: DebtorRecord) -> tuple[bool, str]:
        """Check if debtor is eligible for normal escalation processing.

        Takes reply-based states into account:
        - PROMISE_PAUSED: skip if within grace period
        - OOO_SKIPPED: skip if within retry period
        """
        if debtor.state == DebtorState.PROMISE_PAUSED:
            # Check if grace period has expired
            if debtor.last_action_at:
                try:
                    paused = datetime.strptime(debtor.last_action_at.split(".")[0],
                                               "%Y-%m-%dT%H:%M:%S")
                    elapsed = (datetime.utcnow() - paused).days
                    if elapsed < self.PROMISE_GRACE_DAYS:
                        remaining = self.PROMISE_GRACE_DAYS - elapsed
                        return False, f"Debtor promised to pay — {remaining} days grace remaining"
                    # Grace expired — re-activate
                    debtor.state = DebtorState.ACTIVE
                    return True, "Promise-to-pay grace period expired — resuming escalation"
                except (ValueError, IndexError):
                    pass
            return False, "Debtor promised to pay — within grace period"

        if debtor.state == DebtorState.OOO_SKIPPED:
            if debtor.last_action_at:
                try:
                    skipped = datetime.strptime(debtor.last_action_at.split(".")[0],
                                                "%Y-%m-%dT%H:%M:%S")
                    elapsed = (datetime.utcnow() - skipped).days
                    if elapsed < self.OOO_RETRY_DAYS:
                        remaining = self.OOO_RETRY_DAYS - elapsed
                        return False, f"Debtor out of office — {remaining} days until retry"
                    # OOO period expired — re-activate
                    debtor.state = DebtorState.ACTIVE
                    return True, "OOO period expired — resuming escalation"
                except (ValueError, IndexError):
                    pass
            return False, "Debtor out of office — within skip period"

        if debtor.state in (DebtorState.DISPUTED, DebtorState.MANUAL_REVIEW,
                            DebtorState.WRITTEN_OFF, DebtorState.PAID):
            return False, f"Debtor is {debtor.state.value} — escalation paused"

        return True, "Eligible for escalation"

    def get_summary(self, debtors: list[DebtorRecord]) -> dict[str, Any]:
        """Get a summary of all debtors for the business owner dashboard."""
        summary = {
            "total": len(debtors),
            "by_state": {},
            "total_outstanding_cents": 0,
            "by_tier": {},
            "pending_review": [],
        }
        for d in debtors:
            state = d.state.value
            summary["by_state"][state] = summary["by_state"].get(state, 0) + 1
            summary["by_tier"][d.escalation_tier] = summary["by_tier"].get(d.escalation_tier, 0) + 1
            if d.state == DebtorState.PENDING or d.state == DebtorState.ACTIVE:
                summary["total_outstanding_cents"] += d.amount_cents
            if d.state in (DebtorState.MANUAL_REVIEW, DebtorState.DISPUTED,
                           DebtorState.PROMISE_PAUSED, DebtorState.OOO_SKIPPED):
                summary["pending_review"].append({
                    "id": d.id,
                    "name": d.name,
                    "state": d.state.value,
                    "amount_dollars": f"${d.amount_cents/100:,.2f}",
                })
        return summary


# ── CLI Demo ────────────────────────────────────────────────────────

def main():
    """Run a demo of the escalation engine with sample debtors."""
    engine = EscalationEngine()

    # Register a no-op handler for demo
    def demo_handler(debtor, rule):
        print(f"  → Would execute: {rule.action} ({rule.channel}, {rule.tone})")
        return f"Demo: {rule.action} sent"

    for action in ["send_email", "send_sms", "generate_pdf", "notify_owner", "halt"]:
        engine.register_handler(action, demo_handler)

    # Create sample debtors at different stages
    debtors = [
        DebtorRecord(id="d-001", name="Acme Corp", business_name="Evolving Software",
                     email="ap@acme.com", invoice_number="INV-1042", amount_cents=345000,
                     due_date="2026-06-01", created_at="2026-06-01",
                     days_overdue=18, escalation_tier="standard", state=DebtorState.ACTIVE),
        DebtorRecord(id="d-002", name="Beta LLC", business_name="Evolving Software",
                     email="billing@beta.com", invoice_number="INV-1043", amount_cents=1200000,
                     due_date="2026-05-01", created_at="2026-05-01",
                     days_overdue=45, escalation_tier="high_value", state=DebtorState.ACTIVE),
        DebtorRecord(id="d-003", name="Gamma Inc", business_name="Evolving Software",
                     email="ap@gamma.com", invoice_number="INV-1044", amount_cents=85000,
                     due_date="2026-06-12", created_at="2026-06-12",
                     days_overdue=6, escalation_tier="standard", state=DebtorState.PENDING),
        DebtorRecord(id="d-004", name="Delta Co", business_name="Evolving Software",
                     email="finance@delta.com", invoice_number="INV-1045", amount_cents=2500000,
                     due_date="2026-04-15", created_at="2026-04-15",
                     days_overdue=60, escalation_tier="high_value", state=DebtorState.DISPUTED,
                     dispute_reason="Claims goods were returned"),
    ]

    print(f"\n{'='*65}")
    print(f"  TETHER ESCALATION ENGINE — DEMO")
    print(f"{'='*65}\n")

    for d in debtors:
        print(f"  [{d.id}] {d.name:<12}  ${d.amount_cents/100:>8,.2f}  {d.days_overdue:3d}d  {d.state.value:<14}  tier={d.escalation_tier}")
        result = engine.process_debtor(d)
        print(f"         → {result['action']}: {result.get('reason', result.get('handler_result',''))}")
        print()

    summary = engine.get_summary(debtors)
    print(f"\n{'─'*65}")
    print(f"  SUMMARY")
    print(f"{'─'*65}")
    print(f"  Total debtors:        {summary['total']}")
    print(f"  Outstanding:          ${summary['total_outstanding_cents']/100:,.2f}")
    print(f"  By state:             {summary['by_state']}")
    print(f"  Pending review:       {len(summary['pending_review'])}")
    for pr in summary['pending_review']:
        print(f"    - {pr['name']:<12}  {pr['state']:<14}  {pr['amount_dollars']}")
    print(f"{'─'*65}\n")


if __name__ == "__main__":
    main()
