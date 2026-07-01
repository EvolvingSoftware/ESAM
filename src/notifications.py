#!/usr/bin/env python3
"""Tether Notifications — keeps the business owner informed of all activity.

Provides:
- Daily digest emails summarizing debtor status
- Real-time alerts for key events (payment received, dispute filed, manual review needed)
- Configurable notification preferences per business
- Multi-channel delivery (email, SMS, dashboard in-app)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any


class EventType(Enum):
    PAYMENT_RECEIVED = "payment_received"
    DISPUTE_FILED = "dispute_filed"
    MANUAL_REVIEW = "manual_review"
    ESCALATION_STEP = "escalation_step"
    DEBTOR_ADDED = "debtor_added"
    LATE_FEE_APPLIED = "late_fee_applied"
    DIGEST_SENT = "digest_sent"
    ERROR = "error"
    THRESHOLD_CROSSED = "threshold_crossed"
    REPLY_RECEIVED = "reply_received"


@dataclass
class NotificationEvent:
    """A single notification event in the system."""
    id: str
    business_id: str
    event_type: EventType
    debtor_id: str = ""
    debtor_name: str = ""
    amount_cents: int = 0
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    acknowledged: bool = False
    priority: str = "normal"  # low | normal | high | urgent

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.utcnow().isoformat()


@dataclass
class DigestReport:
    """A daily digest report for a business owner."""
    business_id: str
    date: str
    total_debtors: int = 0
    active_debtors: int = 0
    paid_today: int = 0
    paid_today_cents: int = 0
    new_disputes: int = 0
    pending_review: int = 0
    escalations_today: int = 0
    total_outstanding_cents: int = 0
    by_tier: dict[str, int] = field(default_factory=dict)
    recent_events: list[dict] = field(default_factory=list)
    debtors_needing_attention: list[dict] = field(default_factory=list)


# ── Template Helpers ────────────────────────────────────────────────

def _currency(cents: int) -> str:
    return f"${cents/100:,.2f}"


DIGEST_EMAIL_TEMPLATE = """Subject: Tether Daily Digest — {business_name} — {date}

Hi {business_name} team,

Here's your daily accounts receivable summary for {date}.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  OVERVIEW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Total debtors tracked:  {total_debtors}
  Active follow-ups:      {active_debtors}
  Paid today:             {paid_today} ({paid_today_amount})
  New disputes:           {new_disputes}
  Pending your review:    {pending_review}
  Total outstanding:      {total_outstanding}
  Escalations triggered:  {escalations_today}

  By tier:
{by_tier}

{debtors_needing}

{recent_activity}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  View full dashboard: {dashboard_url}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Powered by Tether — Evolving Software Agent Management
"""

ALERT_TEMPLATES = {
    EventType.PAYMENT_RECEIVED: "🟢 Payment received: {debtor_name} paid {amount} for invoice #{invoice}",
    EventType.DISPUTE_FILED: "🟡 Dispute filed: {debtor_name} disputes invoice #{invoice} — review required",
    EventType.MANUAL_REVIEW: "🔴 Attention needed: {debtor_name} ({amount}) requires manual review — {reason}",
    EventType.ESCALATION_STEP: "ℹ️ Escalation: {debtor_name} moved to step {step} — {channel} {action} sent",
    EventType.THRESHOLD_CROSSED: "🔴 Threshold crossed: Outstanding balance ({amount}) exceeds {threshold} — review recommended",
}


# ── Notification Engine ─────────────────────────────────────────────

class NotificationEngine:
    """Manages business owner notifications — digests, alerts, and event tracking."""

    def __init__(self, business_id: str, business_name: str, dashboard_url: str = ""):
        self.business_id = business_id
        self.business_name = business_name
        self.dashboard_url = dashboard_url or "http://localhost:8000"
        self._event_log: list[NotificationEvent] = []
        self._preferences: dict[str, Any] = {
            "digest_enabled": True,
            "digest_time": "08:00",
            "digest_frequency": "daily",  # daily | weekly | never
            "real_time_alerts": True,
            "alert_channels": ["dashboard"],  # dashboard | email | sms
            "alert_on": ["payment_received", "dispute_filed", "manual_review", "threshold"],
        }

    def record_event(self, event: NotificationEvent):
        """Record a notification event and return formatted alert text if applicable."""
        self._event_log.append(event)

        # Check if this event type should trigger a real-time alert
        if self._preferences["real_time_alerts"] and event.event_type.value in self._preferences["alert_on"]:
            return self._format_alert(event)
        return None

    def _format_alert(self, event: NotificationEvent) -> str:
        """Format an event as a human-readable alert."""
        template = ALERT_TEMPLATES.get(event.event_type, "{message}")
        return template.format(
            debtor_name=event.debtor_name,
            amount=_currency(event.amount_cents),
            invoice=event.details.get("invoice", ""),
            reason=event.details.get("reason", ""),
            step=event.details.get("step", ""),
            channel=event.details.get("channel", ""),
            action=event.details.get("action", ""),
            threshold=event.details.get("threshold", ""),
            message=event.message,
        )

    def build_digest(self, debtors: list[Any], recent_events: list[NotificationEvent] | None = None) -> DigestReport:
        """Build a daily digest report from debtor data and recent events."""
        from datetime import date as date_type
        
        today = date_type.today().isoformat()
        events = recent_events or self._event_log

        paid_today = [d for d in debtors if getattr(d, 'paid_at', '') and d.paid_at.startswith(today)]
        disputes = [e for e in events if e.event_type == EventType.DISPUTE_FILED and e.created_at.startswith(today)]
        escalations = [e for e in events if e.event_type == EventType.ESCALATION_STEP and e.created_at.startswith(today)]
        pending = [d for d in debtors if getattr(d, 'state', None) and d.state.value in ('manual_review', 'disputed')]

        tiers = {}
        for d in debtors:
            tier = getattr(d, 'escalation_tier', 'standard')
            tiers[tier] = tiers.get(tier, 0) + 1

        outstanding = sum(
            getattr(d, 'amount_cents', 0) for d in debtors
            if getattr(d, 'state', None) and d.state.value not in ('paid', 'written_off')
        )

        report = DigestReport(
            business_id=self.business_id,
            date=today,
            total_debtors=len(debtors),
            active_debtors=sum(1 for d in debtors if getattr(d, 'state', None) and d.state.value in ('active', 'pending')),
            paid_today=len(paid_today),
            paid_today_cents=sum(getattr(d, 'paid_amount_cents', 0) or getattr(d, 'amount_cents', 0) for d in paid_today),
            new_disputes=len(disputes),
            pending_review=len(pending),
            escalations_today=len(escalations),
            total_outstanding_cents=outstanding,
            by_tier=tiers,
        )

        # Recent events (last 5)
        for e in sorted(events, key=lambda x: x.created_at, reverse=True)[:5]:
            report.recent_events.append({
                "type": e.event_type.value,
                "debtor": e.debtor_name,
                "message": e.message,
                "time": e.created_at,
            })

        # Debtors needing attention
        for d in pending:
            report.debtors_needing_attention.append({
                "id": getattr(d, 'id', ''),
                "name": getattr(d, 'name', ''),
                "state": str(getattr(d, 'state', '')),
                "amount": _currency(getattr(d, 'amount_cents', 0)),
            })

        return report

    def format_digest_email(self, report: DigestReport) -> str:
        """Format a digest report as an email body."""
        by_tier_lines = "\n".join(f"    {tier}: {count}" for tier, count in sorted(report.by_tier.items()))
        
        debtors_needing_section = ""
        if report.debtors_needing_attention:
            items = "\n".join(
                f"    · {d['name']:<16}  {d['state']:<14}  {d['amount']}"
                for d in report.debtors_needing_attention
            )
            debtors_needing_section = f"  DEBTORS NEEDING ATTENTION:\n{items}\n"

        recent_section = ""
        if report.recent_events:
            items = "\n".join(
                f"    · [{e['type']}] {e['debtor']}: {e['message']}" 
                for e in report.recent_events
            )
            recent_section = f"  RECENT ACTIVITY:\n{items}\n"

        return DIGEST_EMAIL_TEMPLATE.format(
            business_name=self.business_name,
            date=report.date,
            total_debtors=report.total_debtors,
            active_debtors=report.active_debtors,
            paid_today=report.paid_today,
            paid_today_amount=_currency(report.paid_today_cents),
            new_disputes=report.new_disputes,
            pending_review=report.pending_review,
            total_outstanding=_currency(report.total_outstanding_cents),
            escalations_today=report.escalations_today,
            by_tier=by_tier_lines,
            debtors_needing=debtors_needing_section,
            recent_activity=recent_section,
            dashboard_url=self.dashboard_url,
        )

    def get_event_log(self, since: str | None = None, limit: int = 50) -> list[dict]:
        """Get notification events, optionally filtered by time."""
        events = self._event_log
        if since:
            events = [e for e in events if e.created_at >= since]
        return [
            {
                "id": e.id,
                "type": e.event_type.value,
                "debtor": e.debtor_name,
                "message": e.message,
                "priority": e.priority,
                "acknowledged": e.acknowledged,
                "created_at": e.created_at,
            }
            for e in sorted(events, key=lambda x: x.created_at, reverse=True)[:limit]
        ]


# ── CLI Demo ────────────────────────────────────────────────────────

def main():
    """Run a demo of the notification engine with sample events."""
    from escalation_engine import DebtorRecord, DebtorState

    engine = NotificationEngine("biz-001", "Evolving Software", "http://localhost:8000")

    # Sample debtors
    debtors = [
        DebtorRecord(id="d-001", name="Acme Corp", business_name="Evolving Software",
                     invoice_number="INV-1042", amount_cents=345000,
                     due_date="2026-06-01", escalation_tier="standard",
                     state=DebtorState.ACTIVE, days_overdue=18),
        DebtorRecord(id="d-002", name="Beta LLC", business_name="Evolving Software",
                     invoice_number="INV-1043", amount_cents=1200000,
                     due_date="2026-05-01", escalation_tier="high_value",
                     state=DebtorState.ACTIVE, days_overdue=45),
        DebtorRecord(id="d-003", name="Gamma Inc", business_name="Evolving Software",
                     invoice_number="INV-1044", amount_cents=85000,
                     due_date="2026-06-12", escalation_tier="standard",
                     state=DebtorState.PAID, days_overdue=6, paid_at="2026-06-18T09:15:00", paid_amount_cents=85000),
        DebtorRecord(id="d-004", name="Delta Co", business_name="Evolving Software",
                     invoice_number="INV-1045", amount_cents=2500000,
                     due_date="2026-04-15", escalation_tier="high_value",
                     state=DebtorState.DISPUTED, days_overdue=60, dispute_reason="Claims goods returned"),
    ]

    # Simulate events
    import uuid
    events = [
        NotificationEvent(id=str(uuid.uuid4())[:8], business_id="biz-001",
                          event_type=EventType.PAYMENT_RECEIVED, debtor_id="d-003",
                          debtor_name="Gamma Inc", amount_cents=85000,
                          message="Paid in full", details={"invoice": "INV-1044"}),
        NotificationEvent(id=str(uuid.uuid4())[:8], business_id="biz-001",
                          event_type=EventType.DISPUTE_FILED, debtor_id="d-004",
                          debtor_name="Delta Co", amount_cents=2500000,
                          message="Disputes debt — claims goods returned",
                          details={"invoice": "INV-1045", "reason": "Goods returned"}),
        NotificationEvent(id=str(uuid.uuid4())[:8], business_id="biz-001",
                          event_type=EventType.ESCALATION_STEP, debtor_id="d-002",
                          debtor_name="Beta LLC", amount_cents=1200000,
                          message="Escalated to Day 45 — notify owner",
                          details={"step": "45", "action": "notify_owner", "channel": "email"}),
    ]

    print(f"\n{'='*65}")
    print(f"  TETHER NOTIFICATIONS — DEMO")
    print(f"{'='*65}\n")

    print("  Real-time alerts:")
    for ev in events:
        alert = engine.record_event(ev)
        if alert:
            print(f"    {alert}")

    print()
    report = engine.build_digest(debtors)
    print(engine.format_digest_email(report))


if __name__ == "__main__":
    main()
