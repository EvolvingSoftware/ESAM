#!/usr/bin/env python3
"""BPAY Engine — Australia's dominant bill payment system integration.

BPAY is used by ~75% of Australian businesses for bill payments. This module
generates Biller Codes and Customer Reference Numbers (CRNs) that debtors
use to pay via their online banking.

Key concepts:
- **Biller Code**: A 5-digit number identifying the business receiving payment.
  Each business gets one Biller Code.
- **CRN (Customer Reference Number)**: A unique reference per invoice, up to 20 digits.
  The debtor enters this in their banking app to identify which invoice they're paying.
- **Settlement**: BPAY payments settle in 1-2 business days (T+1 or T+2).
- **No public API**: BPAY is bank-integrated. We generate the structured payment
  references that debtors use in their banking apps.

Usage:
    engine = BPAYEngine()
    info = engine.generate_payment_info("biz-001", "Evolving Software")
    # Returns biller_code, crn, payment_instructions
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ── Constants ───────────────────────────────────────────────────────

BPAY_MIN_DIGITS = 10        # Minimum CRN length
BPAY_MAX_DIGITS = 20        # Maximum CRN length per BPAY spec
BPAY_BILLER_LENGTH = 5      # Biller codes are always 5 digits
BPAY_REFERENCE_PREFIX = "INV-"  # Reference prefix for human-readable refs


# ── Data Types ──────────────────────────────────────────────────────

@dataclass
class BPAYPaymentInfo:
    """Complete BPAY payment information for a debtor invoice."""
    business_id: str
    business_name: str
    biller_code: str           # 5-digit Biller Code
    crn: str                   # Customer Reference Number (up to 20 digits)
    reference: str             # Human-readable reference (invoice number)
    amount_cents: int = 0
    amount_dollars: str = ""
    payment_instructions: str = ""  # Pre-formatted payment instructions
    generated_at: str = ""


@dataclass
class BPAYPendingPayment:
    """Tracking record for a BPAY payment awaiting settlement."""
    id: str
    debtor_id: str
    debtor_name: str
    invoice_number: str
    amount_cents: int
    biller_code: str
    crn: str
    status: str = "pending"      # pending | processing | cleared | failed
    initiated_at: str = ""
    expected_settlement: str = ""
    settled_at: str = ""
    notes: str = ""


# ── BPAY Engine ─────────────────────────────────────────────────────

class BPAYEngine:
    """Generates and manages BPAY payment information for the Tether system."""

    def __init__(self):
        self._pending_payments: dict[str, BPAYPendingPayment] = {}
        self._settled_payments: dict[str, BPAYPendingPayment] = {}
        self._biller_registry: dict[str, str] = {}  # business_id -> biller_code

    def generate_biller_code(self, business_id: str) -> str:
        """Generate a deterministic 5-digit Biller Code from a business ID.

        Uses a hash of the business_id so codes are stable across runs.
        Real BPAY Biller Codes are assigned by the bank; this generates
        plausible demo codes with proper format.
        """
        if business_id in self._biller_registry:
            return self._biller_registry[business_id]

        # Generate from hash — produce 5 digits, leading digit 3-9 (valid range)
        hash_bytes = hashlib.sha256(business_id.encode()).digest()
        first_digit = 3 + (hash_bytes[0] % 7)  # 3-9
        rest = int.from_bytes(hash_bytes[1:4], "big") % 10000
        code = f"{first_digit}{rest:04d}"
        self._biller_registry[business_id] = code
        return code

    def generate_crn(self, business_id: str, debtor_id: str,
                     invoice_number: str, amount_cents: int) -> str:
        """Generate a unique Customer Reference Number (CRN) for an invoice.

        Format: [business_hash:4][debtor_hash:4][amount_check:4][seq:4]
        Total: 16 digits — within BPAY's 20-digit max, includes check digit.

        The CRN is deterministic for the same inputs, so regenerating
        payment info always yields the same CRN for an invoice.
        """
        # Create a deterministic seed from the inputs
        seed = f"{business_id}:{debtor_id}:{invoice_number}:{amount_cents}"
        raw_hash = hashlib.sha256(seed.encode()).hexdigest()

        # Build 16-digit CRN
        biz_part = int(raw_hash[:8], 16) % 10000
        debtor_part = int(raw_hash[8:16], 16) % 10000
        amount_part = int(raw_hash[16:24], 16) % 10000
        seq_part = int(raw_hash[24:32], 16) % 10000

        # Concatenate and add Luhn check digit
        crn_digits = f"{biz_part:04d}{debtor_part:04d}{amount_part:04d}{seq_part:04d}"
        check_digit = self._luhn_check_digit(crn_digits)
        return f"{crn_digits}{check_digit}"

    def _luhn_check_digit(self, digits: str) -> str:
        """Calculate Luhn check digit (as used by BPAY for CRN validation)."""
        total = 0
        alternate = True
        for d in reversed(digits):
            n = int(d)
            if alternate:
                n *= 2
                if n > 9:
                    n -= 9
            total += n
            alternate = not alternate
        return str((10 - (total % 10)) % 10)

    def generate_payment_info(self, business_id: str, business_name: str,
                              debtor_id: str, debtor_name: str,
                              invoice_number: str, amount_cents: int) -> BPAYPaymentInfo:
        """Generate complete BPAY payment information for a debtor invoice."""
        biller_code = self.generate_biller_code(business_id)
        crn = self.generate_crn(business_id, debtor_id, invoice_number, amount_cents)

        amount_dollars = f"${amount_cents / 100:,.2f}"

        payment_instructions = self._format_payment_instructions(
            business_name, biller_code, crn, invoice_number, amount_dollars
        )

        return BPAYPaymentInfo(
            business_id=business_id,
            business_name=business_name,
            biller_code=biller_code,
            crn=crn,
            reference=invoice_number,
            amount_cents=amount_cents,
            amount_dollars=amount_dollars,
            payment_instructions=payment_instructions,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    def _format_payment_instructions(self, business_name: str,
                                     biller_code: str, crn: str,
                                     invoice_number: str,
                                     amount_dollars: str) -> str:
        """Format BPAY payment instructions as they appear on a bill."""
        return (
            f"BPAY Payment — {business_name}\n"
            f"{'─' * 40}\n"
            f"  Biller Code:  {biller_code}\n"
            f"  CRN:          {crn}\n"
            f"  Reference:    {invoice_number}\n"
            f"  Amount:       {amount_dollars}\n"
            f"{'─' * 40}\n"
            f"\n"
            f"To pay via BPAY:\n"
            f"  1. Log in to your online banking or banking app\n"
            f"  2. Select 'BPAY' or 'Pay a bill'\n"
            f"  3. Enter Biller Code: {biller_code}\n"
            f"  4. Enter CRN:         {crn}\n"
            f"  5. Enter Amount:      {amount_dollars}\n"
            f"  6. Confirm payment\n"
            f"\n"
            f"Payments typically settle within 1-2 business days.\n"
        )

    def format_bpay_block_html(self, info: BPAYPaymentInfo) -> str:
        """Format BPAY payment information as an HTML block for emails/dashboard."""
        return (
            f'<div style="background:#1a1a2e;border:2px solid #4a90d9;'
            f'border-radius:8px;padding:20px;margin:16px 0;font-family:monospace;">'
            f'<div style="font-size:14px;font-weight:bold;color:#4a90d9;'
            f'margin-bottom:12px;">🏦 BPAY Payment — {info.business_name}</div>'
            f'<table style="font-size:13px;line-height:1.8;color:#e0e0e0;">'
            f'<tr><td style="padding-right:16px;color:#808080;">Biller Code</td>'
            f'<td style="font-weight:bold;letter-spacing:2px;">{info.biller_code}</td></tr>'
            f'<tr><td style="padding-right:16px;color:#808080;">CRN</td>'
            f'<td style="font-weight:bold;letter-spacing:1px;">{info.crn}</td></tr>'
            f'<tr><td style="padding-right:16px;color:#808080;">Reference</td>'
            f'<td>{info.reference}</td></tr>'
            f'<tr><td style="padding-right:16px;color:#808080;">Amount</td>'
            f'<td style="font-weight:bold;">{info.amount_dollars}</td></tr>'
            f'</table>'
            f'<div style="margin-top:12px;padding-top:12px;border-top:1px solid #2a2a4e;'
            f'font-size:11px;color:#808080;">'
            f'Log in to your banking app → BPAY → enter Biller Code and CRN above'
            f'</div>'
            f'</div>'
        )

    def format_bpay_block_text(self, info: BPAYPaymentInfo) -> str:
        """Format BPAY payment information as plain text for SMS/letters."""
        return (
            f"BPAY: Biller {info.biller_code}  CRN {info.crn}  "
            f"Ref {info.reference}  Amount {info.amount_dollars}"
        )

    # ── Payment Tracking ───────────────────────────────────────────

    def record_initiation(self, debtor_id: str, debtor_name: str,
                          invoice_number: str, amount_cents: int,
                          biller_code: str, crn: str) -> BPAYPendingPayment:
        """Record a BPAY payment initiation (biller code+CRN provided to debtor)."""
        import uuid
        uid = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc)
        # BPAY settlement is T+1 to T+2 business days
        expected = now.isoformat()  # Simplified — real BPAY has business day logic
        payment = BPAYPendingPayment(
            id=f"bpay-{uid}",
            debtor_id=debtor_id,
            debtor_name=debtor_name,
            invoice_number=invoice_number,
            amount_cents=amount_cents,
            biller_code=biller_code,
            crn=crn,
            status="pending",
            initiated_at=now.isoformat(),
            expected_settlement=expected,
        )
        self._pending_payments[payment.id] = payment
        return payment

    def confirm_payment(self, payment_id: str) -> BPAYPendingPayment | None:
        """Confirm a BPAY payment as settled."""
        payment = self._pending_payments.get(payment_id)
        if not payment:
            return None
        payment.status = "cleared"
        payment.settled_at = datetime.now(timezone.utc).isoformat()
        self._settled_payments[payment_id] = payment
        del self._pending_payments[payment_id]
        return payment

    def get_pending(self) -> list[BPAYPendingPayment]:
        """Get all pending BPAY payments awaiting settlement."""
        return list(self._pending_payments.values())

    def get_settled(self) -> list[BPAYPendingPayment]:
        """Get all settled BPAY payments."""
        return list(self._settled_payments.values())

    def get_payment_summary(self) -> dict[str, Any]:
        """Get summary stats for BPAY payments."""
        pending_total = sum(p.amount_cents for p in self._pending_payments.values())
        settled_total = sum(p.amount_cents for p in self._settled_payments.values())
        return {
            "pending_count": len(self._pending_payments),
            "pending_total_cents": pending_total,
            "pending_total_dollars": f"${pending_total / 100:,.2f}",
            "settled_count": len(self._settled_payments),
            "settled_total_cents": settled_total,
            "settled_total_dollars": f"${settled_total / 100:,.2f}",
        }


# ── CLI Demo ────────────────────────────────────────────────────────

def main():
    """Demo: generate BPAY payment info for sample debtors."""
    engine = BPAYEngine()

    print(f"\n{'='*65}")
    print(f"  BPAY ENGINE — DEMO")
    print(f"  Australia's dominant bill payment system")
    print(f"{'='*65}\n")

    samples = [
        ("biz-001", "Evolving Software", "d-001", "Acme Corp",
         "INV-2026-042", 345000),
        ("biz-001", "Evolving Software", "d-002", "Beta LLC",
         "INV-2026-043", 1200000),
        ("biz-001", "Evolving Software", "d-003", "Gamma Inc",
         "INV-2026-044", 85000),
    ]

    for biz_id, biz_name, debtor_id, debtor_name, inv, amt in samples:
        info = engine.generate_payment_info(
            biz_id, biz_name, debtor_id, debtor_name, inv, amt
        )
        print(f"  ── {debtor_name:<12}  {info.amount_dollars:>10} ──")
        print(f"    Biller Code:  {info.biller_code}")
        print(f"    CRN:          {info.crn}")
        print(f"    Reference:    {info.reference}")
        print()

        # Record it as pending
        engine.record_initiation(
            debtor_id, debtor_name, inv, amt,
            info.biller_code, info.crn
        )

    print(f"  {'─'*50}")
    summary = engine.get_payment_summary()
    print(f"  Pending:  {summary['pending_count']} payments ({summary['pending_total_dollars']})")
    print(f"  Settled:  {summary['settled_count']} payments ({summary['settled_total_dollars']})")
    print()

    # Show payment instructions for the first debtor
    info = engine.generate_payment_info(
        "biz-001", "Evolving Software", "d-001", "Acme Corp",
        "INV-2026-042", 345000
    )
    print(f"  Payment Instructions Sample:")
    print(f"  {info.payment_instructions}")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    main()
