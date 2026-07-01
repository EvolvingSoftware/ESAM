#!/usr/bin/env python3
"""Tether Letter Generator — AI letter writing + PDF generation (local, no API calls).

Generates personalized debt collection letters using local Gemma 4-12B
via the JIT Model Pool, then renders them as PDF attachments via fpdf2.
All processing is local — no external API calls for generation.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# ── Data Types ──────────────────────────────────────────────────────

@dataclass
class Debtor:
    """A debtor record in the Tether system."""
    id: str
    name: str
    business_name: str
    email: str = ""
    phone: str = ""
    invoice_number: str = ""
    amount_cents: int = 0
    due_date: str = ""
    days_overdue: int = 0
    status: str = "pending"         # pending | contacted | paid | disputed | escalated
    escalation_tier: str = "standard"  # standard | high_value | disputed
    preferred_channel: str = "email"
    previous_tones: list[str] = field(default_factory=list)
    notes: str = ""

    @property
    def amount_dollars(self) -> str:
        return f"${self.amount_cents / 100:,.2f}"

    @property
    def amount_cents_str(self) -> str:
        return str(self.amount_cents)


@dataclass
class Letter:
    """A generated letter ready for sending."""
    debtor_id: str
    step: int                          # 1, 7, 14, 30
    channel: str                       # email | sms | letter
    tone: str                          # friendly | professional | firm | formal
    subject: str                       # email subject line
    body_text: str                     # plain text body
    body_html: str                     # optional HTML version
    pdf_path: str | None = None        # path to generated PDF (for Day 14, 30)
    stripe_link: str = ""
    bpay_info: dict | None = None      # BPAY payment info dict
    model: str = "gemma-4-12b-it"
    tokens_in: int = 0
    tokens_out: int = 0
    generated_at: str = ""

    def __post_init__(self):
        if not self.generated_at:
            self.generated_at = datetime.utcnow().isoformat()


# ── Tone & Escalation Mapping ──────────────────────────────────────

TIER_TONE_MAP = {
    "standard": {
        1:   {"tone": "friendly",    "channel": "email", "type": "friendly reminder"},
        7:   {"tone": "direct",      "channel": "sms",   "type": "SMS nudge"},
        14:  {"tone": "professional","channel": "email", "type": "second notice"},
        30:  {"tone": "formal",      "channel": "email", "type": "final notice"},
    },
    "high_value": {
        1:   {"tone": "professional","channel": "email", "type": "payment reminder"},
        7:   {"tone": "firm",        "channel": "email", "type": "overdue notice"},
        14:  {"tone": "firm",        "channel": "email", "type": "second notice"},
        30:  {"tone": "formal",      "channel": "email", "type": "final notice"},
    },
    "disputed": {
        1:   {"tone": "neutral",     "channel": "email", "type": "payment acknowledgment"},
        7:   {"tone": "neutral",     "channel": "email", "type": "follow-up"},
        14:  {"tone": "professional","channel": "email", "type": "review notice"},
        30:  {"tone": "formal",      "channel": "email", "type": "final review"},
    },
}

# ── Prompt Templates ────────────────────────────────────────────────

def build_prompt(debtor: Debtor, tone: str, step: int, letter_type: str, stripe_link: str = "",
                 bpay_info: dict | None = None) -> str:
    """Build the LLM prompt for generating a collection letter.
    Includes both Stripe and BPAY payment options."""
    prompt = f"""You are a payment follow-up assistant for {debtor.business_name}.
Write a {tone} {letter_type} to {debtor.name} regarding invoice #{debtor.invoice_number}.

Invoice details:
- Amount: {debtor.amount_dollars}
- Days overdue: {debtor.days_overdue}
- Invoice date: {debtor.due_date}

Tone: {tone}

Response format:
SUBJECT: [one-line subject]
BODY:
[letter body, under 150 words, include payment options prominently]

Guidelines:
- {_get_tone_guidelines(tone)}
- Keep the body concise and under 150 words
- Offer BOTH card payment (Stripe) and BPAY (Australia's bank-based bill payment) options
- {_get_compliance_note(step)}
- If this is a final notice, state that failure to pay may result in further action
"""
    return prompt


def _get_tone_guidelines(tone: str) -> str:
    guidelines = {
        "friendly":    "Assume the payment was simply overlooked. Be warm and personal.",
        "professional":"Be direct but respectful. Reference the invoice number and due date clearly.",
        "firm":        "Be clear about the overdue status. Use 'must' and 'requires immediate attention' language.",
        "formal":      "Be authoritative but not threatening. State consequences clearly. Reference prior attempts to contact.",
        "neutral":     "Be factual. Acknowledge the dispute and reference any previous correspondence.",
        "direct":      "Be brief and to the point. SMS format — under 160 characters.",
    }
    return guidelines.get(tone, "Be professional and respectful.")


def _get_compliance_note(step: int) -> str:
    if step >= 30:
        return "This is a final notice. State clearly that this is the last opportunity to pay before further action."
    elif step >= 14:
        return "Reference that this is a follow-up to previous attempts to contact."
    else:
        return "Do not imply any legal consequences. This is a friendly payment reminder."


# ── LLM Interface (Local Gemma via JIT Pool) ────────────────────────

def generate_letter(debtor: Debtor, tone: str, step: int, letter_type: str, stripe_link: str = "",
                    bpay_info: dict | None = None) -> str:
    """Call local Gemma 4-12B to generate a letter. Returns raw response text."""
    prompt = build_prompt(debtor, tone, step, letter_type, stripe_link, bpay_info)

    # Try the JIT Model Pool proxy first
    gemma_url = os.environ.get("GEMMA_URL", "http://localhost:8000/v1/chat/completions")

    payload = {
        "model": "gemma-4-12b-it",
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 300,
        "temperature": 0.7,
    }

    try:
        import urllib.request
        req = urllib.request.Request(
            gemma_url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            return result["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"  [letter_generator] Gemma call failed: {e}")
        print(f"  [letter_generator] Falling back to template-based generation")
        return _template_fallback(debtor, tone, step, letter_type, stripe_link, bpay_info)


def _bpay_template_block(bpay: dict | None) -> str:
    """Generate BPAY payment instructions block for template fallback."""
    if not bpay:
        return ""
    return (
        f"\n\n\u2500\u2500 BPAY Payment \u2500\u2500\n"
        f"Biller Code: {bpay.get('biller_code', '')}\n"
        f"CRN:         {bpay.get('crn', '')}\n"
        f"Reference:   {bpay.get('reference', '')}\n"
        f"\n"
        f"To pay via BPAY, log in to your banking app, select BPAY,\n"
        f"and enter the Biller Code and CRN above."
    )


def _template_fallback(debtor: Debtor, tone: str, step: int, letter_type: str,
                       stripe_link: str = "", bpay_info: dict | None = None) -> str:
    """Template-based fallback when LLM is unavailable.
    Includes BPAY payment instructions for Australian debtors."""
    bpay_block = _bpay_template_block(bpay_info)
    
    templates = {
        "friendly": (
            f"SUBJECT: Friendly reminder: Invoice #{debtor.invoice_number}\n"
            f"BODY:\n"
            f"Hi {debtor.name},\n\n"
            f"This is a quick reminder that invoice #{debtor.invoice_number} for {debtor.amount_dollars} "
            f"was due on {debtor.due_date} and is now {debtor.days_overdue} days overdue.\n\n"
            f"We'd really appreciate it if you could arrange payment at your earliest convenience.\n\n"
            f"\u2500\u2500 Pay Online (Card) \u2500\u2500\n"
            f"Pay now: {stripe_link or '[payment link]'}"
            f"{bpay_block}\n\n"
            f"Thanks,\n{debtor.business_name}"
        ),
        "professional": (
            f"SUBJECT: Overdue Notice \u2014 Invoice #{debtor.invoice_number}\n"
            f"BODY:\n"
            f"Dear {debtor.name},\n\n"
            f"This is a notice that invoice #{debtor.invoice_number} for {debtor.amount_dollars} "
            f"remains unpaid. It is now {debtor.days_overdue} days overdue.\n\n"
            f"We kindly but firmly request that payment be made promptly.\n\n"
            f"\u2500\u2500 Pay Online (Card) \u2500\u2500\n"
            f"Pay now: {stripe_link or '[payment link]'}"
            f"{bpay_block}\n\n"
            f"Please contact us if you have any questions.\n\n"
            f"Regards,\n{debtor.business_name}"
        ),
        "firm": (
            f"SUBJECT: Second Notice \u2014 Invoice #{debtor.invoice_number}\n"
            f"BODY:\n"
            f"Dear {debtor.name},\n\n"
            f"This is the second notice regarding invoice #{debtor.invoice_number} for {debtor.amount_dollars}, "
            f"now {debtor.days_overdue} days overdue.\n\n"
            f"We must insist on immediate payment to avoid further escalation.\n\n"
            f"\u2500\u2500 Pay Online (Card) \u2500\u2500\n"
            f"Pay now: {stripe_link or '[payment link]'}"
            f"{bpay_block}\n\n"
            f"Please remit payment within 7 days.\n\n"
            f"Sincerely,\n{debtor.business_name}"
        ),
        "formal": (
            f"SUBJECT: Final Notice \u2014 Invoice #{debtor.invoice_number}\n"
            f"BODY:\n"
            f"Dear {debtor.name},\n\n"
            f"FINAL NOTICE: Invoice #{debtor.invoice_number} for {debtor.amount_dollars} "
            f"remains unpaid after {debtor.days_overdue} days. This is the last opportunity "
            f"to pay this amount before we consider further action.\n\n"
            f"\u2500\u2500 Pay Online (Card) \u2500\u2500\n"
            f"Pay now: {stripe_link or '[payment link]'}"
            f"{bpay_block}\n\n"
            f"Please arrange payment within 14 days.\n\n"
            f"Yours faithfully,\n{debtor.business_name}"
        ),
        "direct": (
            f"Hi {debtor.name}, invoice #{debtor.invoice_number} for {debtor.amount_dollars} "
            f"is overdue. Pay online: {stripe_link or '[link]'}"
            f"{'  BPAY: Biller ' + bpay_info['biller_code'] + ' CRN ' + bpay_info['crn'] if bpay_info else ''}"
            f" \u2014 {debtor.business_name}"
        ),
        "neutral": (
            f"SUBJECT: Regarding Invoice #{debtor.invoice_number}\n"
            f"BODY:\n"
            f"Dear {debtor.name},\n\n"
            f"We are writing regarding invoice #{debtor.invoice_number} for {debtor.amount_dollars}.\n\n"
            f"We understand this matter may be under review. Please contact us to discuss.\n\n"
            f"Regards,\n{debtor.business_name}"
        ),
    }
    return templates.get(tone, templates["professional"])


# ── PDF Generation (Local, via fpdf2) ───────────────────────────────

def generate_pdf_letter(
    debtor: Debtor,
    letter_text: str,
    output_dir: str | Path = "output/letters",
) -> str:
    """Generate a professional PDF letter using fpdf2. Returns path to PDF file.

    All processing is local — no API calls. The PDF includes:
    - Business letterhead styling
    - Debtor address block
    - Date and reference line
    - Letter body
    - Payment link/instructions callout
    - Compliance footer
    """
    from fpdf import FPDF

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    filename = f"{debtor.id}_step_{letter_text[:30]}.pdf"
    # Clean filename
    filename = re.sub(r'[^\w\-\s]', '', filename).strip().replace(' ', '_')[:80] + ".pdf"
    filepath = output_path / filename

    pdf = FPDF()
    pdf.add_page()

    # ── Letterhead ──
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(229, 57, 53)  # Evolving Software red
    pdf.cell(0, 12, debtor.business_name.upper(), align="L")
    pdf.ln(4)

    pdf.set_draw_color(229, 57, 53)
    pdf.set_line_width(0.8)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(6)

    # ── Compliance notice ──
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(128, 128, 128)
    pdf.cell(0, 4, "This is an automated payment reminder generated by Tether on behalf of the above business.", align="L")
    pdf.ln(10)

    # ── Date and reference ──
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(60, 60, 60)
    today = datetime.utcnow().strftime("%d %B %Y")
    pdf.cell(0, 6, f"Date: {today}")
    pdf.ln(6)
    pdf.cell(0, 6, f"Reference: {debtor.invoice_number}")
    pdf.ln(6)
    pdf.cell(0, 6, f"Amount Outstanding: {debtor.amount_dollars}")
    pdf.ln(12)

    # ── Address block ──
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(0, 6, debtor.name)
    pdf.ln(12)

    # ── Extract and render body ──
    body = letter_text
    if "BODY:" in body:
        body = body.split("BODY:", 1)[1].strip()
    if "SUBJECT:" in body:
        lines = body.split("\n")
        body = "\n".join(l for l in lines if not l.startswith("SUBJECT:"))

    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(30, 30, 30)
    pdf.multi_cell(0, 6, body.strip())
    pdf.ln(6)

    # ── Payment callout box ──
    pdf.set_fill_color(240, 240, 240)
    pdf.set_draw_color(229, 57, 53)
    pdf.set_line_width(0.5)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(60, 60, 60)
    pdf.cell(0, 8, "  PAYMENT OPTIONS INCLUDED ABOVE", border=0)
    pdf.ln(10)

    # ── Compliance footer ──
    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(128, 128, 128)
    pdf.cell(0, 4, f"This communication is from {debtor.business_name}, sent via Tether (Evolving Software Agent Management).", align="C")
    pdf.ln(3)
    pdf.cell(0, 4, "If you believe there has been an error or wish to dispute this notice, please contact the sender directly.", align="C")

    pdf.output(str(filepath))
    return str(filepath)


# ── Main Driver ─────────────────────────────────────────────────────

def compose_letter(debtor: Debtor, step: int, stripe_link: str = "",
                   bpay_info: Any | None = None) -> Letter:
    """Compose a complete letter for a debtor at a given escalation step.

    Includes both Stripe and BPAY payment options where applicable.
    BPAY info should be a BPAYPaymentInfo object from bpay_engine.
    """
    tone_map = TIER_TONE_MAP.get(debtor.escalation_tier, TIER_TONE_MAP["standard"])
    config = tone_map.get(step, tone_map[1])

    tone = config["tone"]
    channel = config["channel"]
    letter_type = config["type"]

    # Convert BPAYPaymentInfo object to dict if needed
    bpay_dict = None
    if bpay_info is not None:
        if hasattr(bpay_info, '__dict__'):
            bpay_dict = bpay_info.__dict__
        else:
            bpay_dict = dict(bpay_info)

    # Generate text
    raw = generate_letter(debtor, tone, step, letter_type, stripe_link, bpay_dict)

    # Parse subject and body
    subject = f"Regarding Invoice #{debtor.invoice_number}"
    body_text = raw
    if "SUBJECT:" in raw:
        subject_line = raw.split("SUBJECT:")[1].split("\n")[0].strip()
        if subject_line:
            subject = subject_line
    if "BODY:" in raw:
        body_text = raw.split("BODY:", 1)[1].strip()

    # Generate PDF for Day 14+ (formal letters)
    pdf_path = None
    if step >= 14 and channel != "sms":
        pdf_path = generate_pdf_letter(debtor, raw)

    return Letter(
        debtor_id=debtor.id,
        step=step,
        channel=channel,
        tone=tone,
        subject=subject,
        body_text=body_text,
        body_html="",
        pdf_path=pdf_path,
        stripe_link=stripe_link,
        bpay_info=bpay_dict,
    )


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    """Demo: generate letters for sample debtors at different escalation steps."""
    import argparse

    parser = argparse.ArgumentParser(description="Tether Letter Generator")
    parser.add_argument("--debtor", default="d-001", help="Debtor ID")
    parser.add_argument("--step", type=int, default=1, choices=[1, 7, 14, 30], help="Escalation step")
    parser.add_argument("--stripe-link", default="https://link.stripe.com/test_link", help="Stripe payment URL")
    args = parser.parse_args()

    sample = Debtor(
        id=args.debtor,
        name="Acme Corp",
        business_name="Evolving Software",
        email="ap@acme.com",
        phone="+155****4567",
        invoice_number="INV-2026-042",
        amount_cents=345000,  # $3,450
        due_date="2026-06-01",
        days_overdue=18,
        status="pending",
        escalation_tier="standard",
    )

    letter = compose_letter(sample, args.step, args.stripe_link)
    print(f"\n{'='*60}")
    print(f"  Step {letter.step} — {letter.tone.upper()} — {letter.channel}")
    print(f"{'='*60}")
    print(f"  Subject: {letter.subject}")
    print(f"  PDF:     {letter.pdf_path or 'N/A'}")
    print(f"\n  {letter.body_text}")
    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
