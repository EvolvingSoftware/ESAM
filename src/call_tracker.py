import datetime
import json
import uuid
from typing import Any, Optional


class CallTracker:
    VALID_OUTCOMES = [
        "promised_to_pay",
        "left_message",
        "no_answer",
        "spoke_to_debtor",
        "spoke_to_other",
        "dispute",
        "wrong_number",
        "callback_requested",
    ]

    VALID_DIRECTIONS = ["outbound", "inbound"]

    def __init__(self):
        self.calls: list[dict] = []
        self.scheduled_calls: list[dict] = []

    def log_call(
        self,
        debtor_id: str,
        caller_name: str,
        direction: str = "outbound",
        duration_seconds: int = 0,
        notes: str = "",
        outcome: str = "",
    ) -> dict:
        if direction not in self.VALID_DIRECTIONS:
            raise ValueError(
                f"Invalid direction '{direction}'. Must be one of: {self.VALID_DIRECTIONS}"
            )
        if outcome and outcome not in self.VALID_OUTCOMES:
            raise ValueError(
                f"Invalid outcome '{outcome}'. Must be one of: {self.VALID_OUTCOMES}"
            )
        if duration_seconds < 0:
            raise ValueError("duration_seconds must be non-negative")

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        call = {
            "id": str(uuid.uuid4()),
            "debtor_id": debtor_id,
            "caller_name": caller_name,
            "direction": direction,
            "duration_seconds": duration_seconds,
            "notes": notes,
            "outcome": outcome,
            "timestamp": now,
            "created_at": now,
        }
        self.calls.append(call)
        return call

    def get_call_history(self, debtor_id: str, limit: int = 20) -> list[dict]:
        debtor_calls = [c for c in self.calls if c["debtor_id"] == debtor_id]
        debtor_calls.sort(key=lambda c: c["timestamp"], reverse=True)
        return debtor_calls[:limit]

    def get_call_summary(self, debtor_id: str) -> dict:
        debtor_calls = [c for c in self.calls if c["debtor_id"] == debtor_id]
        total_calls = len(debtor_calls)
        total_duration = sum(c["duration_seconds"] for c in debtor_calls)
        last_call_at = None
        if debtor_calls:
            sorted_calls = sorted(debtor_calls, key=lambda c: c["timestamp"], reverse=True)
            last_call_at = sorted_calls[0]["timestamp"]

        outcomes_breakdown: dict[str, int] = {}
        for c in debtor_calls:
            outcome = c.get("outcome", "")
            if outcome:
                outcomes_breakdown[outcome] = outcomes_breakdown.get(outcome, 0) + 1

        return {
            "debtor_id": debtor_id,
            "total_calls": total_calls,
            "total_duration": total_duration,
            "last_call_at": last_call_at,
            "outcomes_breakdown": outcomes_breakdown,
        }

    def get_pending_callbacks(self, date: str = "") -> list[dict]:
        if not date:
            date = datetime.date.today().isoformat()

        callbacks = [
            c
            for c in self.calls
            if c["outcome"] == "callback_requested"
            and c["timestamp"].startswith(date)
        ]
        callbacks.sort(key=lambda c: c["timestamp"])
        return callbacks

    def schedule_call(
        self,
        debtor_id: str,
        scheduled_at: str,
        purpose: str = "follow_up",
        caller: str = "",
    ) -> dict:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        scheduled = {
            "id": str(uuid.uuid4()),
            "debtor_id": debtor_id,
            "scheduled_at": scheduled_at,
            "purpose": purpose,
            "caller": caller,
            "status": "pending",
            "created_at": now,
        }
        self.scheduled_calls.append(scheduled)
        return scheduled

    def get_today_calls(self) -> list[dict]:
        today = datetime.date.today().isoformat()
        today_calls = [c for c in self.calls if c["timestamp"].startswith(today)]
        today_calls.sort(key=lambda c: c["timestamp"], reverse=True)
        return today_calls

    def get_outcome_stats(self, since_days: int = 30) -> dict:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=since_days
        )
        recent_calls = [
            c
            for c in self.calls
            if datetime.datetime.fromisoformat(c["timestamp"]) >= cutoff
        ]

        total_calls = len(recent_calls)
        if total_calls == 0:
            return {
                "total_calls": 0,
                "by_outcome": {},
                "avg_duration": 0,
                "contact_rate": 0.0,
            }

        by_outcome: dict[str, int] = {}
        total_duration = 0
        for c in recent_calls:
            outcome = c.get("outcome", "")
            if outcome:
                by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
            total_duration += c["duration_seconds"]

        avg_duration = total_duration / total_calls if total_calls else 0

        contact_count = by_outcome.get("spoke_to_debtor", 0) + by_outcome.get(
            "spoke_to_other", 0
        )
        contact_rate = contact_count / total_calls if total_calls else 0.0

        return {
            "total_calls": total_calls,
            "by_outcome": by_outcome,
            "avg_duration": round(avg_duration, 2),
            "contact_rate": round(contact_rate, 4),
        }

    @staticmethod
    def generate_call_script(
        debtor_name: str,
        business_name: str,
        invoice_number: str,
        amount_cents: int,
        days_overdue: int,
        tone: str = "professional",
    ) -> str:
        amount_dollars = amount_cents / 100
        formatted_amount = f"${amount_dollars:,.2f}"

        if tone == "firm":
            greeting = f"Good day, this is {business_name} calling regarding an overdue account."
            opening = (
                f"I'm calling about invoice {invoice_number} for {formatted_amount}, "
                f"which is now {days_overdue} days overdue. This requires immediate attention."
            )
        elif tone == "empathetic":
            greeting = f"Hello, this is {business_name} calling. How are you today?"
            opening = (
                f"I'm reaching out about invoice {invoice_number} for {formatted_amount}, "
                f"which is {days_overdue} days overdue. I understand things can be difficult, "
                f"and I'd like to help find a solution."
            )
        else:
            greeting = f"Good day, this is {business_name} calling regarding account matter."
            opening = (
                f"I'm calling about invoice {invoice_number} for {formatted_amount}, "
                f"which is currently {days_overdue} days overdue. We'd like to arrange "
                f"payment as soon as possible."
            )

        purpose = (
            f"We're calling to discuss the outstanding balance of {formatted_amount} "
            f"on invoice {invoice_number}. We'd like to understand your circumstances "
            f"and work towards resolving this matter."
        )

        key_points = (
            "Key points:\n"
            f"  - Outstanding amount: {formatted_amount}\n"
            f"  - Invoice number: {invoice_number}\n"
            f"  - Days overdue: {days_overdue}\n"
            "  - Payment is expected to be arranged today\n"
            "  - Multiple payment options are available"
        )

        objection_hardship = (
            "If the debtor mentions financial hardship:\n"
            "  - Acknowledge their situation with empathy\n"
            "  - Offer a payment plan option\n"
            "  - Suggest they may provide supporting documentation\n"
            "  - Emphasise that ignoring the debt does not resolve it"
        )

        objection_dispute = (
            "If the debtor disputes the debt:\n"
            "  - Remain calm and professional\n"
            "  - Ask them to clarify the nature of the dispute\n"
            "  - Explain the dispute process and required documentation\n"
            "  - Do not acknowledge or deny the validity of the dispute on the call\n"
            "  - Document the dispute and escalate as required"
        )

        objection_delay = (
            "If the debtor says they will pay later:\n"
            "  - Ask for a specific date and commitment\n"
            "  - Offer to schedule a follow-up call\n"
            "  - Explain the consequences of continued non-payment\n"
            "  - Document the promised payment date"
        )

        closing = (
            "Closing:\n"
            "  - Summarise any agreements made during the call\n"
            "  - Confirm contact details and next steps\n"
            "  - Thank the debtor for their time\n"
            "  - Log the call outcome and notes immediately"
        )

        script_sections = [
            "=" * 60,
            "TELEPHONE COLLECTION SCRIPT",
            f"Business: {business_name}",
            f"Debtor: {debtor_name}",
            f"Invoice: {invoice_number}",
            f"Amount: {formatted_amount}",
            f"Days Overdue: {days_overdue}",
            f"Tone: {tone.title()}",
            "=" * 60,
            "",
            "GREETING",
            "-" * 40,
            greeting,
            "",
            "OPENING / PURPOSE",
            "-" * 40,
            opening,
            "",
            "PURPOSE OF CALL",
            "-" * 40,
            purpose,
            "",
            "KEY POINTS",
            "-" * 40,
            key_points,
            "",
            "OBJECTION HANDLING",
            "-" * 40,
            objection_hardship,
            "",
            objection_dispute,
            "",
            objection_delay,
            "",
            "CLOSING",
            "-" * 40,
            closing,
            "",
            "=" * 60,
            "END OF SCRIPT",
            "=" * 60,
        ]

        return "\n".join(script_sections)

    def get_call_by_id(self, call_id: str) -> Optional[dict]:
        for call in self.calls:
            if call["id"] == call_id:
                return call
        return None

    def update_call_notes(self, call_id: str, notes: str) -> Optional[dict]:
        call = self.get_call_by_id(call_id)
        if call is None:
            return None
        call["notes"] = notes
        return call

    def delete_call(self, call_id: str) -> bool:
        for i, call in enumerate(self.calls):
            if call["id"] == call_id:
                self.calls.pop(i)
                return True
        return False

    def get_scheduled_calls_for_debtor(self, debtor_id: str) -> list[dict]:
        return [
            s
            for s in self.scheduled_calls
            if s["debtor_id"] == debtor_id and s["status"] == "pending"
        ]

    def cancel_scheduled_call(self, scheduled_id: str) -> bool:
        for s in self.scheduled_calls:
            if s["id"] == scheduled_id:
                s["status"] = "cancelled"
                return True
        return False

    def mark_scheduled_call_completed(self, scheduled_id: str) -> bool:
        for s in self.scheduled_calls:
            if s["id"] == scheduled_id:
                s["status"] = "completed"
                return True
        return False

    def export_calls_json(self, debtor_id: Optional[str] = None) -> str:
        if debtor_id:
            data = [c for c in self.calls if c["debtor_id"] == debtor_id]
        else:
            data = list(self.calls)
        return json.dumps(data, indent=2, default=str)

    def __repr__(self) -> str:
        return (
            f"CallTracker(calls={len(self.calls)}, "
            f"scheduled={len(self.scheduled_calls)})"
        )
