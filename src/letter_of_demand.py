"""Tether Australian Debt Collections - Letter of Demand Module.

Generates formal letters of demand with proper Australian statutory wording.
"""

import datetime
import os
import re
from typing import Any

try:
    from fpdf import FPDF

    FPDF_AVAILABLE = True
except ImportError:
    FPDF_AVAILABLE = False


VALID_STATES = ["NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"]

STATE_LEGISLATION = {
    "NSW": {
        "limitation_years": 6,
        "penalty_interest_rate": 10.0,
        "small_claims_limit": 40000,
        "court_name": "NSW Civil and Administrative Tribunal (NCAT)",
        "legislation_ref": "Civil Procedure Act 2005 (NSW)",
    },
    "VIC": {
        "limitation_years": 6,
        "penalty_interest_rate": 10.0,
        "small_claims_limit": 40000,
        "court_name": "Victorian Civil and Administrative Tribunal (VCAT)",
        "legislation_ref": "Civil Procedure Act 2010 (Vic)",
    },
    "QLD": {
        "limitation_years": 6,
        "penalty_interest_rate": 10.0,
        "small_claims_limit": 25000,
        "court_name": "QCAT (Queensland Civil and Administrative Tribunal)",
        "legislation_ref": "Uniform Civil Procedure Rules 1999 (Qld)",
    },
    "WA": {
        "limitation_years": 6,
        "penalty_interest_rate": 10.0,
        "small_claims_limit": 75000,
        "court_name": "Magistrates Court of Western Australia",
        "legislation_ref": "Commonwealth debt recovery provisions",
    },
    "SA": {
        "limitation_years": 6,
        "penalty_interest_rate": 10.0,
        "small_claims_limit": 25000,
        "court_name": "Magistrates Court of South Australia",
        "legislation_ref": "Commonwealth debt recovery provisions",
    },
    "TAS": {
        "limitation_years": 6,
        "penalty_interest_rate": 10.0,
        "small_claims_limit": 25000,
        "court_name": "Magistrates Court of Tasmania",
        "legislation_ref": "Commonwealth debt recovery provisions",
    },
    "ACT": {
        "limitation_years": 6,
        "penalty_interest_rate": 10.0,
        "small_claims_limit": 25000,
        "court_name": "ACT Civil and Administrative Tribunal (ACAT)",
        "legislation_ref": "Commonwealth debt recovery provisions",
    },
    "NT": {
        "limitation_years": 3,
        "penalty_interest_rate": 10.0,
        "small_claims_limit": 50000,
        "court_name": "Local Court of the Northern Territory",
        "legislation_ref": "Commonwealth debt recovery provisions",
    },
}

STATE_TIMEFRAMES = {
    "NSW": 21,
    "VIC": 14,
    "QLD": 14,
    "WA": 14,
    "SA": 14,
    "TAS": 14,
    "ACT": 14,
    "NT": 14,
}

STATE_WORDING = {
    "NSW": "under the Civil Procedure Act 2005 (NSW)",
    "VIC": "under the Civil Procedure Act 2010 (Vic)",
    "QLD": "under the Uniform Civil Procedure Rules 1999 (Qld)",
    "WA": "in accordance with applicable Commonwealth and state legislation",
    "SA": "in accordance with applicable Commonwealth and state legislation",
    "TAS": "in accordance with applicable Commonwealth and state legislation",
    "ACT": "in accordance with applicable Commonwealth and state legislation",
    "NT": "in accordance with applicable Commonwealth and state legislation",
}

DEFAULT_BUSINESS_ADDRESS = "Level 1, 100 Collins Street, Melbourne VIC 3000"
DEFAULT_BSB = "063-000"
DEFAULT_ACCOUNT = "1234 5678"
DEFAULT_ACCOUNT_NAME = "Tether Collections Pty Ltd"
DEFAULT_ABN = "12 345 678 901"


def _format_cents(cents: int) -> str:
    dollars = cents // 100
    remainder = cents % 100
    return f"${dollars:,}.{remainder:02d}"


def _ordinal_suffix(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return "th"
    remainder = n % 10
    if remainder == 1:
        return "st"
    if remainder == 2:
        return "nd"
    if remainder == 3:
        return "rd"
    return "th"


def _format_date_long(dt: datetime.datetime) -> str:
    day = dt.day
    suffix = _ordinal_suffix(day)
    return f"{day}{suffix} {dt.strftime('%B %Y')}"


class LetterOfDemand:
    """Generates formal Australian letters of demand for Tether debt collection."""

    def __init__(
        self,
        business_name: str = "Tether Collections Pty Ltd",
        business_address: str = DEFAULT_BUSINESS_ADDRESS,
        abn: str = DEFAULT_ABN,
        bsb: str = DEFAULT_BSB,
        account_number: str = DEFAULT_ACCOUNT,
        account_name: str = DEFAULT_ACCOUNT_NAME,
        contact_phone: str = "1300 555 019",
        contact_email: str = "demands@tethercollections.com.au",
    ) -> None:
        self.business_name = business_name
        self.business_address = business_address
        self.abn = abn
        self.bsb = bsb
        self.account_number = account_number
        self.account_name = account_name
        self.contact_phone = contact_phone
        self.contact_email = contact_email

    def get_state_info(self, state: str) -> dict[str, Any]:
        """Return info about a state's debt recovery laws.

        Args:
            state: Australian state/territory code.

        Returns:
            Dictionary with limitation_years, penalty_interest_rate,
            small_claims_limit, court_name, legislation_ref.

        Raises:
            ValueError: If state is not a supported Australian state/territory.
        """
        state = state.upper().strip()
        if state not in STATE_LEGISLATION:
            raise ValueError(
                f"Unsupported state '{state}'. Valid states: {', '.join(VALID_STATES)}"
            )
        return dict(STATE_LEGISLATION[state])

    def get_state_default_timeframes(self) -> dict[str, int]:
        """Return dict mapping state -> days to pay in demand letter."""
        return dict(STATE_TIMEFRAMES)

    def _build_demand_text(
        self,
        debtor_name: str,
        debtor_address: str,
        business_name: str,
        business_address: str,
        invoice_number: str,
        amount_cents: int,
        days_overdue: int,
        state: str,
        abn: str,
    ) -> str:
        state = state.upper().strip()
        amount_str = _format_cents(amount_cents)
        today = datetime.date.today()
        timeframe_days = STATE_TIMEFRAMES.get(state, 14)
        deadline = today + datetime.timedelta(days=timeframe_days)
        deadline_str = _format_date_long(datetime.datetime.combine(deadline, datetime.time()))
        state_wording = STATE_WORDING.get(
            state, "in accordance with applicable Commonwealth and state legislation"
        )
        reference = f"LOD-{today.strftime('%Y%m%d')}-{invoice_number}"
        date_str = _format_date_long(datetime.datetime.combine(today, datetime.time()))

        lines: list[str] = []
        lines.append(self.business_name.upper())
        lines.append(self.business_address)
        lines.append(f"ABN: {abn or self.abn}")
        lines.append(f"Tel: {self.contact_phone}")
        lines.append(f"Email: {self.contact_email}")
        lines.append("")
        lines.append("")
        lines.append(date_str)
        lines.append("")
        lines.append(f"BY REGISTERED POST AND EMAIL")
        lines.append("")
        lines.append(debtor_name)
        if debtor_address:
            for addr_line in debtor_address.split("\n"):
                lines.append(addr_line)
        lines.append("")
        lines.append(f"Dear {debtor_name},")
        lines.append("")
        lines.append(f"RE: DEMAND FOR PAYMENT — UNPAID INVOICE #{invoice_number}")
        lines.append(f"    Reference: {reference}")
        lines.append("")
        lines.append("We write on behalf of " + business_name + " (" + ("ABN " + abn if abn else "the creditor") + ").")
        lines.append("")
        lines.append(
            f"Our records show that you owe the sum of {amount_str} being the amount "
            f"outstanding under invoice #{invoice_number} issued by {business_name}."
        )
        lines.append("")
        lines.append(
            f"This amount has been overdue for {days_overdue} day{'s' if days_overdue != 1 else ''} "
            f"as at the date of this letter."
        )
        lines.append("")
        lines.append("WE DEMAND payment of the sum of " + amount_str + " being the amount "
                      f"outstanding under invoice #{invoice_number}.")
        lines.append("")
        lines.append(
            f"Payment must be received within {timeframe_days} days of the date of this letter, "
            f"that is, on or before {deadline_str} {state_wording}."
        )
        lines.append("")
        lines.append("If payment is not received by the above date, we are instructed to:")
        lines.append("")
        lines.append(
            "  (a) commence legal proceedings against you " + state_wording + ";"
        )
        lines.append(
            "  (b) seek recovery of the full amount owing together with interest "
            "at the rate prescribed by law;"
        )
        lines.append(
            "  (c) claim all costs and disbursements incurred in connection with the "
            "recovery of this debt on a solicitor-client or indemnity basis;"
        )
        lines.append(
            "  (d) obtain a judgment or order against you which may be enforced by "
            "garnishee orders, writs of execution, or other enforcement measures "
            "available under the relevant legislation."
        )
        lines.append("")
        lines.append(
            "We note that this letter constitutes a formal demand as required "
            "prior to the commencement of proceedings and may be relied upon "
            "in any subsequent cost assessment."
        )
        lines.append("")
        lines.append("Payment should be made by electronic funds transfer to:")
        lines.append("")
        lines.append(f"  Account Name:  {self.account_name}")
        lines.append(f"  BSB:           {self.bsb}")
        lines.append(f"  Account No:    {self.account_number}")
        lines.append(f"  Reference:     {reference}")
        lines.append("")
        lines.append(
            "Please ensure your name and the invoice number are included as "
            "the payment reference to ensure proper allocation."
        )
        lines.append("")
        lines.append(
            "If you dispute any part of this amount, you must notify us in writing "
            "within the above timeframe. Failure to respond will be taken as "
            "acceptance of the amount claimed."
        )
        lines.append("")
        lines.append(
            "If you have already made payment, please disregard this letter and "
            "accept our apologies for any inconvenience."
        )
        lines.append("")
        lines.append("Yours faithfully,")
        lines.append("")
        lines.append("")
        lines.append("______________________________")
        lines.append("Debt Recovery Team")
        lines.append(self.business_name)
        lines.append(f"Tel: {self.contact_phone}")
        lines.append(f"Email: {self.contact_email}")
        lines.append("")
        lines.append("— — —")
        lines.append("IMPORTANT: This letter is a formal demand for payment. If you do not")
        lines.append("pay the amount demanded or notify us of a dispute within the specified")
        lines.append("timeframe, legal proceedings may be commenced without further notice.")
        lines.append("This communication is intended for the named recipient only. If you")
        lines.append("have received this letter in error, please notify us immediately.")

        return "\n".join(lines)

    def generate_demand_letter(
        self,
        debtor_name: str,
        debtor_address: str,
        business_name: str,
        business_address: str,
        invoice_number: str,
        amount_cents: int,
        days_overdue: int,
        state: str = "NSW",
        abn: str = "",
    ) -> dict[str, Any]:
        """Generate a formal letter of demand in plain text.

        Args:
            debtor_name: Full name of the debtor.
            debtor_address: Multi-line postal address of the debtor.
            business_name: Name of the creditor business.
            business_address: Postal address of the creditor.
            invoice_number: The unpaid invoice reference number.
            amount_cents: Amount owing in cents.
            days_overdue: Number of days the invoice is overdue.
            state: Australian state/territory code (default NSW).
            abn: Australian Business Number.

        Returns:
            Dict with text, state_wording, payment_deadline_days, deadline_date, word_count.
        """
        state = state.upper().strip()
        if state not in STATE_TIMEFRAMES:
            raise ValueError(
                f"Unsupported state '{state}'. Valid states: {', '.join(VALID_STATES)}"
            )
        if amount_cents <= 0:
            raise ValueError("amount_cents must be positive")
        if days_overdue < 0:
            raise ValueError("days_overdue must be non-negative")

        text = self._build_demand_text(
            debtor_name, debtor_address, business_name, business_address,
            invoice_number, amount_cents, days_overdue, state, abn,
        )

        timeframe_days = STATE_TIMEFRAMES[state]
        deadline = datetime.date.today() + datetime.timedelta(days=timeframe_days)
        state_wording = STATE_WORDING[state]

        return {
            "text": text,
            "state_wording": state_wording,
            "payment_deadline_days": timeframe_days,
            "deadline_date": deadline.isoformat(),
            "word_count": len(text.split()),
        }

    def generate_for_state(
        self,
        debtor_name: str,
        business_name: str,
        invoice_number: str,
        amount_cents: int,
        state: str,
    ) -> str:
        """Shortcut that returns just the letter text for a given state.

        Uses default values for missing parameters.
        """
        letter = self.generate_demand_letter(
            debtor_name=debtor_name,
            debtor_address="",
            business_name=business_name,
            business_address=self.business_address,
            invoice_number=invoice_number,
            amount_cents=amount_cents,
            days_overdue=30,
            state=state,
            abn=self.abn,
        )
        return letter["text"]

    def validate_letter(self, letter_text: str) -> list[str]:
        """Check the generated letter for required elements.

        Args:
            letter_text: The plain text letter content.

        Returns:
            List of missing elements (empty list means complete).
        """
        missing: list[str] = []
        text_lower = letter_text.lower()

        has_demand = bool(re.search(r"we demand", text_lower))
        has_amount = bool(re.search(r"\$[\d,]+\.\d{2}", letter_text))
        has_invoice = bool(re.search(r"invoice\s*#?\s*\S+", text_lower))
        has_timeframe = bool(re.search(r"\d+\s*days?", text_lower))
        has_consequences = bool(re.search(r"(legal proceedings|enforcement|garnishee|writ)", text_lower))
        has_payment_details = bool(re.search(r"(bsb|account|electronic funds)", text_lower))
        has_business_name = bool(re.search(self.business_name.lower(), text_lower))

        if not has_demand:
            missing.append("demand wording (expected: 'WE DEMAND')")
        if not has_amount:
            missing.append("dollar amount (expected: $X.XX format)")
        if not has_invoice:
            missing.append("invoice reference (expected: 'invoice #...')")
        if not has_timeframe:
            missing.append("timeframe (expected: N days)")
        if not has_consequences:
            missing.append("consequences of non-payment")
        if not has_payment_details:
            missing.append("payment details (BSB/account)")
        if not has_business_name:
            missing.append(f"business name (expected: '{self.business_name}')")

        return missing

    def generate_pdf_demand(
        self,
        letter_data: dict[str, Any],
        output_dir: str = "output/demand_letters",
    ) -> str:
        """Create a PDF of the demand letter using fpdf2.

        Args:
            letter_data: Output from generate_demand_letter().
            output_dir: Directory to write the PDF to.

        Returns:
            Path to the generated PDF file.

        Raises:
            RuntimeError: If fpdf2 is not installed.
        """
        if not FPDF_AVAILABLE:
            raise RuntimeError(
                "fpdf2 is not installed. Install it with: pip install fpdf2. "
                "Alternatively, use the 'text' key from letter_data for plain text output."
            )

        os.makedirs(output_dir, exist_ok=True)

        text = letter_data["text"]
        deadline = letter_data.get("deadline_date", "")
        state_wording = letter_data.get("state_wording", "")

        filename = f"demand_letter_{deadline}.pdf"
        filepath = os.path.join(output_dir, filename)

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=25)
        pdf.add_page()

        pdf.set_font("Helvetica", "B", 18)
        pdf.set_text_color(180, 30, 30)
        pdf.cell(0, 12, self.business_name.upper(), new_x="LMARGIN", new_y="NEXT", align="L")

        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(80, 80, 80)
        pdf.cell(0, 5, self.business_address, new_x="LMARGIN", new_y="NEXT", align="L")
        pdf.cell(0, 5, f"ABN: {self.abn}", new_x="LMARGIN", new_y="NEXT", align="L")
        pdf.cell(0, 5, f"Tel: {self.contact_phone}  |  Email: {self.contact_email}", new_x="LMARGIN", new_y="NEXT", align="L")

        pdf.set_draw_color(180, 30, 30)
        pdf.set_line_width(0.8)
        pdf.line(10, pdf.get_y() + 3, 200, pdf.get_y() + 3)
        pdf.ln(8)

        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 10)

        for line in text.split("\n"):
            stripped = line.strip()

            if stripped.startswith("WE DEMAND"):
                pdf.ln(4)
                pdf.set_font("Helvetica", "B", 11)
                pdf.set_text_color(180, 30, 30)
                pdf.multi_cell(0, 6, stripped)
                pdf.set_font("Helvetica", "", 10)
                pdf.set_text_color(0, 0, 0)
            elif stripped.startswith("RE: DEMAND"):
                pdf.set_font("Helvetica", "B", 11)
                pdf.cell(0, 7, stripped, new_x="LMARGIN", new_y="NEXT", align="L")
                pdf.set_font("Helvetica", "", 10)
            elif stripped.startswith("IMPORTANT:"):
                pdf.ln(4)
                pdf.set_font("Helvetica", "BI", 9)
                pdf.set_text_color(100, 100, 100)
                pdf.multi_cell(0, 5, stripped)
                pdf.set_font("Helvetica", "", 10)
                pdf.set_text_color(0, 0, 0)
            elif stripped.startswith(("Account Name:", "BSB:", "Account No:", "Reference:")):
                pdf.set_font("Courier", "", 10)
                pdf.cell(0, 6, stripped, new_x="LMARGIN", new_y="NEXT", align="L")
                pdf.set_font("Helvetica", "", 10)
            elif stripped.startswith(("  (a)", "  (b)", "  (c)", "  (d)")):
                pdf.set_font("Helvetica", "", 10)
                pdf.multi_cell(0, 6, stripped)
            elif stripped == "______________________________":
                pdf.ln(2)
                pdf.cell(0, 6, stripped, new_x="LMARGIN", new_y="NEXT", align="L")
            elif stripped == "":
                pdf.ln(3)
            else:
                pdf.multi_cell(0, 6, stripped)

        pdf.set_draw_color(180, 30, 30)
        pdf.set_line_width(0.4)
        pdf.line(10, 275, 200, 275)

        pdf.output(filepath)
        return filepath

    def generate_batch(self, demands: list[dict[str, Any]]) -> list[str]:
        """Generate multiple demand letters at once.

        Args:
            demands: List of dicts, each with keys: debtor_name, debtor_address,
                business_name, business_address, invoice_number, amount_cents,
                days_overdue, state (optional).

        Returns:
            List of generated file paths (PDFs when fpdf2 available, else .txt files).
        """
        results: list[str] = []

        for demand in demands:
            debtor_name = demand.get("debtor_name", "")
            debtor_address = demand.get("debtor_address", "")
            business_name = demand.get("business_name", self.business_name)
            business_address = demand.get("business_address", self.business_address)
            invoice_number = demand.get("invoice_number", "")
            amount_cents = demand.get("amount_cents", 0)
            days_overdue = demand.get("days_overdue", 0)
            state = demand.get("state", "NSW")

            letter_data = self.generate_demand_letter(
                debtor_name=debtor_name,
                debtor_address=debtor_address,
                business_name=business_name,
                business_address=business_address,
                invoice_number=invoice_number,
                amount_cents=amount_cents,
                days_overdue=days_overdue,
                state=state,
            )

            if FPDF_AVAILABLE:
                output_dir = "output/demand_letters"
                os.makedirs(output_dir, exist_ok=True)
                safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", debtor_name)
                deadline_date = letter_data.get("deadline_date", "unknown")
                filename = f"demand_{safe_name}_{deadline_date}.pdf"
                filepath = os.path.join(output_dir, filename)
                try:
                    result = self.generate_pdf_demand(letter_data, output_dir)
                    results.append(result)
                except Exception:
                    txt_path = filepath.replace(".pdf", ".txt")
                    with open(txt_path, "w") as f:
                        f.write(letter_data["text"])
                    results.append(txt_path)
            else:
                output_dir = "output/demand_letters"
                os.makedirs(output_dir, exist_ok=True)
                safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", debtor_name)
                deadline_date = letter_data.get("deadline_date", "unknown")
                filename = f"demand_{safe_name}_{deadline_date}.txt"
                filepath = os.path.join(output_dir, filename)
                with open(filepath, "w") as f:
                    f.write(letter_data["text"])
                results.append(filepath)

        return results


def main():
    lod = LetterOfDemand()

    letter = lod.generate_demand_letter(
        debtor_name="John Smith",
        debtor_address="42 George Street\nSydney NSW 2000",
        business_name="Tether Collections Pty Ltd",
        business_address=DEFAULT_BUSINESS_ADDRESS,
        invoice_number="INV-2026-00142",
        amount_cents=547500,
        days_overdue=45,
        state="NSW",
        abn="12 345 678 901",
    )

    print("=" * 60)
    print(letter["text"])
    print("=" * 60)
    print(f"State wording: {letter['state_wording']}")
    print(f"Deadline: {letter['deadline_date']} ({letter['payment_deadline_days']} days)")
    print(f"Word count: {letter['word_count']}")

    issues = lod.validate_letter(letter["text"])
    if issues:
        print(f"\nValidation issues: {issues}")
    else:
        print("\nLetter validation: PASSED — all required elements present.")

    print("\n--- State timeframes ---")
    for st, days in lod.get_state_default_timeframes().items():
        info = lod.get_state_info(st)
        print(f"  {st}: {days} days | {info['legislation_ref']} | "
              f"Limitation: {info['limitation_years']}y | "
              f"Small claims: ${info['small_claims_limit']:,}")

    print("\n--- Batch generation ---")
    batch_results = lod.generate_batch([
        {
            "debtor_name": "Jane Doe",
            "debtor_address": "10 Smith St, Melbourne VIC 3000",
            "business_name": "Tether Collections Pty Ltd",
            "invoice_number": "INV-2026-00201",
            "amount_cents": 1250000,
            "days_overdue": 60,
            "state": "VIC",
        },
        {
            "debtor_name": "Acme Corp Pty Ltd",
            "debtor_address": "55 Adelaide Tce, Perth WA 6000",
            "business_name": "Tether Collections Pty Ltd",
            "invoice_number": "INV-2026-00315",
            "amount_cents": 330000,
            "days_overdue": 30,
            "state": "WA",
        },
    ])
    for path in batch_results:
        print(f"  Generated: {path}")


if __name__ == "__main__":
    main()
