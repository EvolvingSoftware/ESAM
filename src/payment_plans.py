#!/usr/bin/env python3
"""Payment Plan Negotiation Engine.

Provides AI-driven payment plan proposals, agreement generation,
instalment tracking, and default monitoring for the Tether collections system.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any


class PlanStatus(Enum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    COMPLETED = "completed"
    DEFAULTED = "defaulted"
    CANCELLED = "cancelled"


@dataclass
class Instalment:
    number: int
    amount_cents: int
    due_date: str
    paid: bool = False
    paid_date: str = ""
    paid_amount_cents: int = 0

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "amount_cents": self.amount_cents,
            "due_date": self.due_date,
            "paid": self.paid,
            "paid_date": self.paid_date,
            "paid_amount_cents": self.paid_amount_cents,
        }


@dataclass
class PaymentPlan:
    plan_id: str
    debtor_id: str
    debtor_name: str
    business_name: str
    total_cents: int
    instalments: list[Instalment]
    frequency: str  # weekly | fortnightly | monthly
    status: PlanStatus
    created_at: str
    accepted_at: str = ""
    completed_at: str = ""
    notes: str = ""
    late_fee_pct: float = 0.0
    grace_days: int = 7

    @property
    def paid_cents(self) -> int:
        return sum(i.paid_amount_cents for i in self.instalments)

    @property
    def remaining_cents(self) -> int:
        return self.total_cents - self.paid_cents

    @property
    def is_fully_paid(self) -> bool:
        return all(i.paid for i in self.instalments)

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "debtor_id": self.debtor_id,
            "debtor_name": self.debtor_name,
            "business_name": self.business_name,
            "total_cents": self.total_cents,
            "instalments": [i.to_dict() for i in self.instalments],
            "frequency": self.frequency,
            "status": self.status.value,
            "paid_cents": self.paid_cents,
            "remaining_cents": self.remaining_cents,
            "created_at": self.created_at,
            "accepted_at": self.accepted_at,
            "completed_at": self.completed_at,
        }


class PaymentPlanNegotiator:
    """AI-driven payment plan negotiation engine."""

    def __init__(self):
        self._plans: dict[str, PaymentPlan] = {}
        self._debtor_plans: dict[str, list[str]] = {}

    def _now(self) -> str:
        return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    def propose_plans(
        self,
        amount_cents: int,
        financial_notes: str = "",
    ) -> list[dict[str, Any]]:
        """Propose payment plan options based on amount and financial situation."""
        if amount_cents <= 0:
            raise ValueError("Amount must be positive")

        base = datetime.utcnow()
        plans = []

        # Determine options based on amount
        hardship = any(w in financial_notes.lower() for w in ["hardship", "can't pay", "financial", "difficulty"])
        options = [(3, "weekly", 7), (3, "monthly", 30), (6, "monthly", 30)]
        if hardship or amount_cents >= 500000:  # $5k+
            options.append((12, "monthly", 30))

        for num_instalments, freq, days_interval in options:
            per_instalment = amount_cents // num_instalments
            remainder = amount_cents - (per_instalment * num_instalments)

            instalments = []
            for i in range(num_instalments):
                amt = per_instalment + (1 if i < remainder else 0)
                due = base + timedelta(days=days_interval * (i + 1))
                instalments.append(Instalment(
                    number=i + 1,
                    amount_cents=amt,
                    due_date=due.strftime("%Y-%m-%d"),
                ))

            plans.append({
                "instalments": num_instalments,
                "frequency": freq,
                "per_instalment_cents": per_instalment,
                "total_cents": amount_cents,
                "schedule": [i.to_dict() for i in instalments],
                "duration_days": days_interval * num_instalments,
                "first_payment": instalments[0].due_date if instalments else "",
            })

        return plans

    def accept_plan(
        self,
        debtor_id: str,
        debtor_name: str,
        business_name: str,
        amount_cents: int,
        num_instalments: int,
        frequency: str = "monthly",
    ) -> dict[str, Any]:
        """Accept a payment plan and generate the agreement."""
        days_interval = {"weekly": 7, "fortnightly": 14, "monthly": 30}.get(frequency, 30)
        base = datetime.utcnow()
        per = amount_cents // num_instalments
        remainder = amount_cents - (per * num_instalments)

        instalments = []
        for i in range(num_instalments):
            amt = per + (1 if i < remainder else 0)
            due = base + timedelta(days=days_interval * (i + 1))
            instalments.append(Instalment(number=i + 1, amount_cents=amt, due_date=due.strftime("%Y-%m-%d")))

        plan = PaymentPlan(
            plan_id=f"plan_{uuid.uuid4().hex[:10]}",
            debtor_id=debtor_id,
            debtor_name=debtor_name,
            business_name=business_name,
            total_cents=amount_cents,
            instalments=instalments,
            frequency=frequency,
            status=PlanStatus.ACTIVE,
            created_at=self._now(),
            accepted_at=self._now(),
        )

        self._plans[plan.plan_id] = plan
        if debtor_id not in self._debtor_plans:
            self._debtor_plans[debtor_id] = []
        self._debtor_plans[debtor_id].append(plan.plan_id)

        return plan.to_dict()

    def get_active_plan(self, debtor_id: str) -> dict[str, Any] | None:
        """Get the active payment plan for a debtor."""
        plan_ids = self._debtor_plans.get(debtor_id, [])
        for pid in reversed(plan_ids):
            plan = self._plans.get(pid)
            if plan and plan.status == PlanStatus.ACTIVE:
                return plan.to_dict()
            if plan and plan.status == PlanStatus.COMPLETED:
                return plan.to_dict()
        return None

    def record_payment(self, plan_id: str, amount_cents: int) -> dict[str, Any]:
        """Record a payment against an active plan."""
        plan = self._plans.get(plan_id)
        if not plan:
            raise ValueError(f"Plan {plan_id} not found")
        if plan.status != PlanStatus.ACTIVE:
            raise ValueError("Plan is not active")

        unpaid = [i for i in plan.instalments if not i.paid]
        if not unpaid:
            raise ValueError("All instalments already paid")

        remaining = amount_cents
        now = self._now()
        for inst in unpaid:
            if remaining <= 0:
                break
            pay_amt = min(remaining, inst.amount_cents)
            inst.paid = True
            inst.paid_date = now
            inst.paid_amount_cents = inst.amount_cents
            remaining -= pay_amt

        if plan.is_fully_paid:
            plan.status = PlanStatus.COMPLETED
            plan.completed_at = now

        return plan.to_dict()

    def check_overdue(self, debtor_id: str | None = None) -> list[dict[str, Any]]:
        """Check for missed payments."""
        now = datetime.utcnow()
        overdue = []

        plans = list(self._plans.values())
        if debtor_id:
            pids = self._debtor_plans.get(debtor_id, [])
            plans = [self._plans[pid] for pid in pids if pid in self._plans]

        for plan in plans:
            if plan.status != PlanStatus.ACTIVE:
                continue
            for inst in plan.instalments:
                if inst.paid:
                    continue
                due = datetime.strptime(inst.due_date, "%Y-%m-%d")
                grace_end = due + timedelta(days=plan.grace_days)
                if now > grace_end:
                    days_late = (now - grace_end).days
                    late_fee = int(inst.amount_cents * (plan.late_fee_pct / 100))
                    overdue.append({
                        "plan_id": plan.plan_id,
                        "debtor_id": plan.debtor_id,
                        "debtor_name": plan.debtor_name,
                        "instalment": inst.number,
                        "amount_cents": inst.amount_cents,
                        "days_late": days_late,
                        "late_fee_cents": late_fee,
                        "total_due_cents": inst.amount_cents + late_fee,
                    })
            # Auto-default if significantly overdue
            if len(overdue) >= len(plan.instalments):
                plan.status = PlanStatus.DEFAULTED

        return overdue

    def get_all_active_plans(self) -> list[dict[str, Any]]:
        """Get all active payment plans."""
        return [p.to_dict() for p in self._plans.values() if p.status == PlanStatus.ACTIVE]

    def get_plan_summary(self) -> dict[str, Any]:
        """Get aggregated payment plan statistics."""
        total_active = 0
        total_completed = 0
        total_defaulted = 0
        total_under_plan_cents = 0
        collected_via_plan_cents = 0

        for plan in self._plans.values():
            if plan.status == PlanStatus.ACTIVE:
                total_active += 1
                total_under_plan_cents += plan.total_cents
                collected_via_plan_cents += plan.paid_cents
            elif plan.status == PlanStatus.COMPLETED:
                total_completed += 1
                collected_via_plan_cents += plan.total_cents
            elif plan.status == PlanStatus.DEFAULTED:
                total_defaulted += 1

        return {
            "total_active": total_active,
            "total_completed": total_completed,
            "total_defaulted": total_defaulted,
            "total_under_plan_cents": total_under_plan_cents,
            "collected_via_plan_cents": collected_via_plan_cents,
            "completion_rate": round(total_completed / max(total_completed + total_defaulted, 1) * 100, 1),
        }

    def generate_agreement_pdf(self, plan_id: str, output_dir: str = "output/agreements") -> str:
        """Generate a PDF payment agreement."""
        plan = self._plans.get(plan_id)
        if not plan:
            raise ValueError(f"Plan {plan_id} not found")

        try:
            from fpdf import FPDF
        except ImportError:
            raise ImportError("fpdf2 required: pip install fpdf2")

        import os as _os
        _os.makedirs(output_dir, exist_ok=True)
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 18)
        pdf.set_text_color(229, 57, 53)
        pdf.cell(0, 12, "PAYMENT AGREEMENT", align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(60, 60, 60)
        pdf.ln(4)
        pdf.cell(0, 6, f"Agreement: {plan.plan_id}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 6, f"Date: {plan.accepted_at[:10] if plan.accepted_at else plan.created_at[:10]}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)
        pdf.cell(0, 6, f"Between: {plan.business_name}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 6, f"And:     {plan.debtor_name} ({plan.debtor_id})", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, f"Total Amount: ${plan.total_cents / 100:,.2f}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 6, f"Instalments: {len(plan.instalments)} x ${plan.instalments[0].amount_cents / 100:,.2f}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "Payment Schedule:", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(10, 6, "#", border=1)
        pdf.cell(40, 6, "Amount", border=1)
        pdf.cell(40, 6, "Due Date", border=1)
        pdf.cell(30, 6, "Status", border=1)
        pdf.ln()
        pdf.set_font("Helvetica", "", 9)
        for i in plan.instalments:
            pdf.cell(10, 6, str(i.number), border=1)
            pdf.cell(40, 6, f"${i.amount_cents / 100:,.2f}", border=1)
            pdf.cell(40, 6, i.due_date, border=1)
            pdf.cell(30, 6, "PAID" if i.paid else "PENDING", border=1)
            pdf.ln()
        pdf.ln(10)
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 6, "Terms: The debtor agrees to make all scheduled payments by the due dates. Late payments may incur additional fees. This agreement is governed by the laws of New South Wales, Australia.")
        pdf.ln(20)
        pdf.cell(80, 6, "____________________________", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(80, 6, f"Signed for {plan.business_name}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(10)
        pdf.cell(80, 6, "____________________________", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(80, 6, f"Signed by {plan.debtor_name}", new_x="LMARGIN", new_y="NEXT")

        path = _os.path.join(output_dir, f"{plan.plan_id}.pdf")
        pdf.output(path)
        return path
