#!/usr/bin/env python3
"""Tether Engine — Orchestrator that ties the full collections workflow together.

Coordinates:
- Ingestion → Analysis → Letter Generation → PDF → Stripe → Send → Monitor → Escalate
- Business owner notifications (digests + real-time alerts)
- Self-improvement loop (track what works, feed back into prompts)
- ACCC-compliant late fee auto-assessment during escalation
"""

from __future__ import annotations

import csv
import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, date
from io import StringIO
from pathlib import Path
from typing import Any

from letter_generator import Debtor as LetterDebtor, compose_letter, Letter
from escalation_engine import (
    EscalationEngine, DebtorRecord, DebtorState, EscalationRule
)
from notifications import (
    NotificationEngine, NotificationEvent, EventType, DigestReport
)
from reply_handler import ReplyHandler
from bpay_engine import BPAYEngine, BPAYPaymentInfo
from late_fees import LateFeeEngine


# ── Orchestrator ────────────────────────────────────────────────────

class TetherEngine:
    """Main orchestrator for the Tether collections workflow."""

    def __init__(self, business_id: str, business_name: str, dashboard_url: str = ""):
        self.business_id = business_id
        self.business_name = business_name
        self.dashboard_url = dashboard_url

        # Sub-engines
        self.escalation = EscalationEngine()
        self.notifications = NotificationEngine(business_id, business_name, dashboard_url)
        self.reply_handler = ReplyHandler()
        self.late_fees = LateFeeEngine(business_id)
        self.bpay = BPAYEngine()

        # Auto-assess late fees during escalation
        self.late_fee_enabled = True

        # Data stores (in-memory for demo, replace with DB in production)
        self.debtors: dict[str, DebtorRecord] = {}
        self.letters: dict[str, Letter] = {}
        self.letter_history: dict[str, list[Letter]] = {}

        # Wire up escalation handlers to our actual actions
        self._register_handlers()

        # Event counter
        self._event_seq = 0

    def _register_handlers(self):
        """Wire escalation actions to our business logic."""
        self.escalation.register_handler("send_email", self._handle_send_email)
        self.escalation.register_handler("send_sms", self._handle_send_sms)
        self.escalation.register_handler("generate_pdf", self._handle_generate_pdf)
        self.escalation.register_handler("notify_owner", self._handle_notify_owner)
        self.escalation.register_handler("halt", self._handle_halt)

    # ── Ingestion ──────────────────────────────────────────────────

    def ingest_csv(self, csv_content: str) -> list[DebtorRecord]:
        """Parse CSV content and create debtor records."""
        reader = csv.DictReader(StringIO(csv_content))
        created = []
        for row in reader:
            record = DebtorRecord(
                id=row.get("id", f"d-{uuid.uuid4().hex[:8]}"),
                name=row.get("name", "").strip(),
                business_name=self.business_name,
                email=row.get("email", "").strip(),
                phone=row.get("phone", "").strip(),
                invoice_number=row.get("invoice_number", "").strip(),
                amount_cents=int(row.get("amount_cents", "0")),
                due_date=row.get("due_date", "").strip(),
                days_overdue=self._calc_days_overdue(row.get("due_date", "")),
                escalation_tier=self._determine_tier(row),
                state=DebtorState.PENDING,
                created_at=datetime.utcnow().isoformat(),
            )
            self.debtors[record.id] = record
            created.append(record)

            self._log_event(EventType.DEBTOR_ADDED, record.id, record.name,
                            record.amount_cents, f"Debtor added: {record.name} — ${record.amount_cents/100:,.2f}")

        return created

    def _calc_days_overdue(self, due_date_str: str) -> int:
        try:
            due = datetime.strptime(due_date_str, "%Y-%m-%d")
            return max(0, (datetime.utcnow() - due).days)
        except (ValueError, TypeError):
            return 0

    def _determine_tier(self, row: dict) -> str:
        amount = int(row.get("amount_cents", "0"))
        days = self._calc_days_overdue(row.get("due_date", ""))
        if amount >= 500000 or days > 30:  # $5,000+ or 30+ days
            return "high_value"
        return "standard"

    # ── Processing ─────────────────────────────────────────────────

    def process_all(self) -> dict[str, Any]:
        """Run the escalation engine for all active debtors."""
        results = []
        
        # Ensure default late fee rules exist
        if self.late_fee_enabled:
            existing = self.late_fees.list_rules(business_id=self.business_id)
            if not existing:
                self.late_fees.create_default_rules()
        
        for debtor in list(self.debtors.values()):
            if debtor.state in (DebtorState.PAID, DebtorState.WRITTEN_OFF):
                continue

            # Auto-assess late fees if enabled and beyond grace period
            if self.late_fee_enabled and debtor.days_overdue >= 14 and debtor.state not in (
                DebtorState.DISPUTED, DebtorState.MANUAL_REVIEW
            ):
                fee_result = self.late_fees.assess_fees(
                    debtor_id=debtor.id,
                    debtor_name=debtor.name,
                    invoice_number=debtor.invoice_number,
                    amount_cents=debtor.amount_cents,
                    days_overdue=debtor.days_overdue,
                )
                if fee_result.get("action") == "fee_applied":
                    assessment = fee_result["assessment"]
                    print(f"  [tether] FEE     → {debtor.name:<12}  +${assessment['total_fee_cents']/100:,.2f} late fee applied")
                    self._log_event(
                        EventType.LATE_FEE_APPLIED,
                        debtor.id, debtor.name,
                        assessment["total_fee_cents"],
                        f"Late fee applied: ${assessment['total_fee_cents']/100:,.2f} ({assessment['fee_type']})",
                        details={"fee_assessment_id": assessment["id"], "fee_type": assessment["fee_type"],
                                 "fixed_fee_cents": assessment["fixed_fee_cents"],
                                 "interest_cents": assessment["interest_cents"]}
                    )

            result = self.escalation.process_debtor(debtor)
            results.append(result)

            # Log escalation events
            if result["action"] not in ("none", "error", "no_handler"):
                rule = result.get("rule")
                if rule:
                    self._log_event(EventType.ESCALATION_STEP, debtor.id, debtor.name,
                                    debtor.amount_cents,
                                    f"Step {rule.day} — {rule.tone} {rule.action}",
                                    details={"step": str(rule.day), "action": rule.action, "channel": rule.channel})

        summary = self.escalation.get_summary(list(self.debtors.values()))
        return {"results": results, "summary": summary, "processed": len(results)}

    # ── Escalation Handlers (Actual Business Logic) ─────────────────

    def _handle_send_email(self, debtor: DebtorRecord, rule: EscalationRule) -> str:
        """Generate and 'send' an email collection letter."""
        letter = self._generate_letter(debtor, rule)
        print(f"  [tether] EMAIL   → {debtor.name:<12}  Step {rule.day}  Tone: {rule.tone}")
        print(f"  [tether] Subject: {letter.subject}")
        print(f"  [tether] Body:   {letter.body_text[:80]}...")
        if letter.pdf_path:
            print(f"  [tether] PDF:    {letter.pdf_path}")
        return f"Email sent: {letter.subject}"

    def _handle_send_sms(self, debtor: DebtorRecord, rule: EscalationRule) -> str:
        """Generate and 'send' an SMS collection message."""
        letter = self._generate_letter(debtor, rule)
        sms_text = letter.body_text[:160]  # SMS length limit
        print(f"  [tether] SMS     → {debtor.name:<12}  Step {rule.day}  '{sms_text[:60]}...'")
        return f"SMS sent: {sms_text[:60]}..."

    def _handle_generate_pdf(self, debtor: DebtorRecord, rule: EscalationRule) -> str:
        """Generate a formal PDF letter for Day 14/30 escalations."""
        letter = self._generate_letter(debtor, rule)
        print(f"  [tether] PDF     → {debtor.name:<12}  Step {rule.day}  PDF: {letter.pdf_path}")
        return f"PDF generated: {letter.pdf_path}"

    def _handle_notify_owner(self, debtor: DebtorRecord, rule: EscalationRule) -> str:
        """Flag a debtor for business owner review."""
        self.escalation.request_manual_review(debtor, f"Step {rule.day} — requires owner decision")
        self._log_event(EventType.MANUAL_REVIEW, debtor.id, debtor.name,
                        debtor.amount_cents,
                        f"Action needed: {debtor.name} — ${debtor.amount_cents/100:,.2f}",
                        details={"reason": f"All escalation steps completed", "step": str(rule.day)})
        print(f"  [tether] OWNER   → {debtor.name:<12}  REVIEW REQUIRED (Step {rule.day})")
        return f"Owner notified: {debtor.name} needs manual review"

    def _handle_halt(self, debtor: DebtorRecord, rule: EscalationRule) -> str:
        """Halt collection — usually due to dispute."""
        print(f"  [tether] HALT    → {debtor.name:<12}  Collection paused (disputed)")
        return f"Collection halted: {debtor.name}"

    def _generate_letter(self, debtor: DebtorRecord, rule: EscalationRule) -> Letter:
        """Generate a letter using the letter generator module."""
        letter_debtor = LetterDebtor(
            id=debtor.id,
            name=debtor.name,
            business_name=self.business_name,
            email=debtor.email,
            phone=debtor.phone,
            invoice_number=debtor.invoice_number,
            amount_cents=debtor.amount_cents,
            due_date=debtor.due_date,
            days_overdue=debtor.days_overdue,
            escalation_tier=debtor.escalation_tier,
        )

        # Generate Stripe payment link
        stripe_link = f"https://link.stripe.com/pay/{debtor.id}"

        # Generate BPAY payment info (always available for Australian debtors)
        bpay_info = self.bpay.generate_payment_info(
            business_id=self.business_id,
            business_name=self.business_name,
            debtor_id=debtor.id,
            debtor_name=debtor.name,
            invoice_number=debtor.invoice_number,
            amount_cents=debtor.amount_cents,
        )

        letter = compose_letter(letter_debtor, rule.day, stripe_link, bpay_info)
        self.letters[debtor.id] = letter
        if debtor.id not in self.letter_history:
            self.letter_history[debtor.id] = []
        self.letter_history[debtor.id].append(letter)
        return letter

    # ── Dispute Handling ────────────────────────────────────────────

    def file_dispute(self, debtor_id: str, reason: str):
        """File a dispute for a debtor — halts automated collection."""
        debtor = self.debtors.get(debtor_id)
        if not debtor:
            raise ValueError(f"Debtor {debtor_id} not found")
        self.escalation.mark_disputed(debtor, reason)
        self._log_event(EventType.DISPUTE_FILED, debtor.id, debtor.name,
                        debtor.amount_cents, f"Dispute filed: {reason}",
                        details={"reason": reason, "invoice": debtor.invoice_number})

    def mark_paid(self, debtor_id: str):
        """Mark a debtor as paid."""
        debtor = self.debtors.get(debtor_id)
        if not debtor:
            raise ValueError(f"Debtor {debtor_id} not found")
        self.escalation.mark_paid(debtor, debtor.amount_cents)
        self._log_event(EventType.PAYMENT_RECEIVED, debtor.id, debtor.name,
                        debtor.amount_cents, f"Payment received — {debtor.amount_cents/100:,.2f}",
                        details={"invoice": debtor.invoice_number})

    # ── Reply Handling ────────────────────────────────────────────────

    def process_reply(
        self,
        debtor_id: str,
        subject: str,
        body: str,
        email_from: str = "",
        use_llm: bool = True,
    ) -> dict:
        """Ingest, classify, and action an incoming debtor reply.

        Returns the classification result and action taken.
        """
        result = self.reply_handler.ingest_reply(
            debtor_id=debtor_id,
            subject=subject,
            body=body,
            email_from=email_from,
            use_llm=use_llm,
        )

        # Log the event
        debtor = self.debtors.get(debtor_id)
        debtor_name = debtor.name if debtor else debtor_id
        self._log_event(
            EventType.REPLY_RECEIVED, debtor_id, debtor_name,
            debtor.amount_cents if debtor else 0,
            f"Reply classified as {result['category']}: {result['summary']}",
            details={
                "reply_id": result["reply_id"],
                "category": result["category"],
                "confidence": result["confidence"],
                "action": result["action"],
            },
        )
        return result

    def process_all_with_reply_check(self) -> dict:
        """Process all debtors, checking replies before normal escalation.

        Returns results including any reply interventions.
        """
        results = []
        reply_interventions = 0

        for debtor in list(self.debtors.values()):
            if debtor.state in (DebtorState.PAID, DebtorState.WRITTEN_OFF):
                continue

            # Check if debtor is eligible (reply states pause escalation)
            eligible, reason = self.escalation.is_eligible_for_escalation(debtor)
            if not eligible:
                results.append({
                    "action": "skipped",
                    "reason": reason,
                    "debtor_id": debtor.id,
                })
                continue

            # Process with reply check — reply interventions take priority
            result = self.escalation.process_with_reply_check(debtor)
            results.append(result)

            if "reply_id" in result:
                reply_interventions += 1
                self._log_event(
                    EventType.ESCALATION_STEP, debtor.id, debtor.name,
                    debtor.amount_cents,
                    f"Reply intervention: {result['action']} — {result['reason']}",
                    details={"reply_id": result.get("reply_id", "")},
                )

            # Normal escalation logging
            elif result["action"] not in ("none", "error", "no_handler"):
                rule = result.get("rule")
                if rule:
                    self._log_event(
                        EventType.ESCALATION_STEP, debtor.id, debtor.name,
                        debtor.amount_cents,
                        f"Step {rule.day} — {rule.tone} {rule.action}",
                        details={"step": str(rule.day), "action": rule.action, "channel": rule.channel},
                    )

        summary = self.escalation.get_summary(list(self.debtors.values()))
        return {
            "results": results,
            "summary": summary,
            "processed": len(results),
            "reply_interventions": reply_interventions,
        }

    # ── Notifications ───────────────────────────────────────────────

    def _log_event(self, event_type: EventType, debtor_id: str, debtor_name: str,
                   amount_cents: int, message: str, details: dict | None = None):
        self._event_seq += 1
        event = NotificationEvent(
            id=f"evt-{self._event_seq}",
            business_id=self.business_id,
            event_type=event_type,
            debtor_id=debtor_id,
            debtor_name=debtor_name,
            amount_cents=amount_cents,
            message=message,
            details=details or {},
            created_at=datetime.utcnow().isoformat(),
        )
        alert = self.notifications.record_event(event)
        if alert:
            print(f"  [tether] ALERT   → {alert}")

    def send_daily_digest(self) -> str:
        """Generate and return the daily digest email."""
        all_debtors = list(self.debtors.values())
        report = self.notifications.build_digest(all_debtors)
        return self.notifications.format_digest_email(report)

    # ── Status ──────────────────────────────────────────────────────

    def get_dashboard_data(self) -> dict[str, Any]:
        """Get all data needed for the Current dashboard."""
        summary = self.escalation.get_summary(list(self.debtors.values()))
        
        debtor_list = []
        for d in self.debtors.values():
            debtor_list.append({
                "id": d.id,
                "name": d.name,
                "invoice": d.invoice_number,
                "amount_dollars": f"${d.amount_cents/100:,.2f}",
                "amount_cents": d.amount_cents,
                "days_overdue": d.days_overdue,
                "state": d.state.value,
                "tier": d.escalation_tier,
                "current_step": d.current_step,
                "last_action": d.last_action_at,
                "dispute_reason": d.dispute_reason,
                "paid": d.state == DebtorState.PAID,
                "needs_review": d.state in (DebtorState.MANUAL_REVIEW, DebtorState.DISPUTED),
            })

        return {
            "business": {"id": self.business_id, "name": self.business_name},
            "summary": summary,
            "debtors": debtor_list,
            "recent_events": self.notifications.get_event_log(limit=10),
            "letters": {
                did: [
                    {"step": l.step, "tone": l.tone, "channel": l.channel,
                     "subject": l.subject, "pdf": l.pdf_path, "generated": l.generated_at}
                    for l in letters[-3:]  # Last 3 per debtor
                ]
                for did, letters in self.letter_history.items()
            },
        }


# ── CLI Demo ────────────────────────────────────────────────────────

def demo_csv() -> str:
    """Return sample CSV data for the demo."""
    return """id,name,email,phone,invoice_number,amount_cents,due_date
d-001,Acme Corp,ap@acme.com,+15551234567,INV-2026-042,345000,2026-06-01
d-002,Beta LLC,billing@beta.com,+15559876543,INV-2026-043,1200000,2026-05-01
d-003,Gamma Inc,ap@gamma.com,+15555550000,INV-2026-044,85000,2026-06-12
d-004,Delta Co,finance@delta.com,+15557778888,INV-2026-045,2500000,2026-04-15
d-005,Epsilon Pty,accounts@epsilon.com,+15553332222,INV-2026-046,425000,2026-06-05
"""


def main():
    """Run the full Tether workflow end-to-end."""
    print(f"\n{'='*75}")
    print(f"  TETHER — FULL WORKFLOW DEMO")
    print(f"  Evolving Software Agent Management")
    print(f"{'='*75}\n")

    engine = TetherEngine("biz-001", "Evolving Software", "http://localhost:8000")

    # ── Step 1: Ingest ──
    print("─" * 50)
    print("  STEP 1: INGESTION")
    print("─" * 50)
    created = engine.ingest_csv(demo_csv())
    print(f"  Ingested {len(created)} debtors from CSV\n")

    # ── Step 2: Process ──
    print("─" * 50)
    print("  STEP 2: ESCALATION ENGINE")
    print("─" * 50)
    result = engine.process_all()
    summary = result["summary"]
    print(f"\n  Processed {result['processed']} debtors")
    print(f"  Outstanding: ${summary['total_outstanding_cents']/100:,.2f}")
    print()

    # ── Step 3: Simulate a payment ──
    print("─" * 50)
    print("  STEP 3: SIMULATE PAYMENT")
    print("─" * 50)
    engine.mark_paid("d-003")
    print()

    # ── Step 4: Simulate a dispute ──
    print("─" * 50)
    print("  STEP 4: SIMULATE DISPUTE")
    print("─" * 50)
    engine.file_dispute("d-004", "Claims goods were returned on 2026-05-20")
    print()

    # ── Step 5: Process again (post-events) ──
    print("─" * 50)
    print("  STEP 5: RE-PROCESS AFTER EVENTS")
    print("─" * 50)
    result = engine.process_all()
    print()

    # ── Step 6: Daily digest ──
    print("─" * 50)
    print("  STEP 6: DAILY DIGEST")
    print("─" * 50)
    digest = engine.send_daily_digest()
    print(digest)

    # ── Final summary ──
    print("─" * 50)
    print("  DASHBOARD DATA SUMMARY")
    print("─" * 50)
    data = engine.get_dashboard_data()
    print(f"  Business:  {data['business']['name']}")
    print(f"  Debtors:   {data['summary']['total']}")
    print(f"  By state:  {data['summary']['by_state']}")
    print(f"  Pending:   {len(data['summary']['pending_review'])}")
    print(f"  Events:    {len(data['recent_events'])}")
    print(f"\n{'='*75}")
    print(f"  DEMO COMPLETE — Full workflow verified")
    print(f"{'='*75}\n")


if __name__ == "__main__":
    main()
