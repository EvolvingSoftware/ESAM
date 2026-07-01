"""Legal document template generator for Tether debt collection SaaS (Australia)."""

import datetime
import json
import os
import textwrap
from typing import Any, Optional

try:
    from fpdf import FPDF
    HAS_FPDF = True
except ImportError:
    HAS_FPDF = False


_TETHER_ACCENT = (200, 33, 39)

SECTION_NUMBERS = {
    0: "1", 1: "2", 2: "3", 3: "4", 4: "5",
    5: "6", 6: "7", 7: "8", 8: "9", 9: "10",
    10: "11", 11: "12", 12: "13",
}


def _today() -> str:
    return datetime.date.today().strftime("%d %B %Y")


def _cent_to_dollar(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _build_numbered_sections(sections: list[str]) -> str:
    lines: list[str] = []
    for idx, heading in enumerate(sections):
        lines.append(f"{'─' * 60}")
        lines.append(f"  {SECTION_NUMBERS.get(idx, str(idx + 1))}. {heading.upper()}")
        lines.append(f"{'─' * 60}")
    return "\n".join(lines)


class LegalDocumentGenerator:
    """Generate legal document templates for Australian credit management."""

    def __init__(self, company_name: str = "Tether", company_address: str = "", company_abn: str = ""):
        self.company_name = company_name
        self.company_address = company_address
        self.company_abn = company_abn

    # ── Terms of Trade ──────────────────────────────────────────────────

    def generate_terms_of_trade(
        self,
        business_name: str,
        business_address: str,
        abn: str,
        payment_terms_days: int = 30,
        late_fee_percent: float = 2.0,
        late_fee_grace_days: int = 7,
        interest_rate_percent: float = 10.0,
    ) -> dict[str, Any]:
        """Generate a comprehensive Terms of Trade document."""
        effective_date = _today()
        sections = [
            {
                "title": "Definitions and Interpretation",
                "body": textwrap.dedent(f"""\
                In these Terms:
                • "Business" means {business_name} (ABN: {abn}), of {business_address}.
                • "Buyer" means any person or entity that purchases Goods or Services from the Business.
                • "Goods" means all products supplied by the Business to the Buyer.
                • "Services" means all services provided by the Business to the Buyer.
                • "Credit Account" means an account established for the Buyer to purchase on credit terms.
                • "PPSR" means the Personal Property Securities Register established under the Personal Property Securities Act 2009 (Cth).
                • "Privacy Act" means the Privacy Act 1988 (Cth) as amended.
                • "AML/CTF Act" means the Anti-Money Laundering and Counter-Terrorism Financing Act 2006 (Cth).
                • "Credit Reporting Body" has the meaning given in Part IIIA of the Privacy Act 1988 (Cth).
                • "GST" has the meaning given in the A New Tax System (Goods and Services Tax) Act 1999 (Cth).
                """),
            },
            {
                "title": "Application of Terms",
                "body": textwrap.dedent(f"""\
                1.1  These Terms apply to all sales of Goods and provision of Services by the Business to the Buyer.
                1.2  Any order placed by the Buyer constitutes acceptance of these Terms.
                1.3  These Terms prevail over any terms or conditions proposed by the Buyer.
                1.4  No variation of these Terms is effective unless in writing and signed by both parties.
                """),
            },
            {
                "title": "Credit Terms",
                "body": textwrap.dedent(f"""\
                2.1  The Business may, at its absolute discretion, extend credit facilities to the Buyer.
                2.2  Credit approval is subject to satisfactory credit checks, references, and the Buyer completing the Business's Credit Application form.
                2.3  The Business reserves the right to vary or revoke credit facilities at any time without notice.
                2.4  The Buyer must immediately notify the Business of any change in ownership, directors, or financial circumstances.
                2.5  Credit limits are indicative only and do not constitute a binding obligation on the Business to supply Goods or Services up to that limit.
                2.6  The Buyer shall not be entitled to set off any amounts owed to it by the Business against amounts owed under these Terms.
                """),
            },
            {
                "title": "Payment Terms",
                "body": textwrap.dedent(f"""\
                3.1  Payment is due within {payment_terms_days} days of the date of invoice unless otherwise agreed in writing.
                3.2  All invoices are payable in Australian Dollars (AUD) by direct deposit, EFT, or other method agreed by the Business.
                3.3  The Business may require payment in advance or on delivery at its discretion.
                3.4  Where multiple invoices remain outstanding, payment may be applied in the order the Business determines.
                3.5  Payments received will be applied first to interest, then to fees, then to the oldest outstanding invoices.
                """),
            },
            {
                "title": "Late Payment and Recovery",
                "body": textwrap.dedent(f"""\
                4.1  If the Buyer fails to make payment by the due date, a late fee of {late_fee_percent}% of the outstanding amount will be charged after a grace period of {late_fee_grace_days} days.
                4.2  Interest will accrue on all overdue amounts at a rate of {interest_rate_percent}% per annum, calculated daily from the due date until payment is received in full.
                4.3  The Buyer is liable for all costs and expenses (including legal costs on a solicitor-client basis) incurred by the Business in recovering overdue amounts.
                4.4  The Business may engage a debt collection agency to recover outstanding amounts. The Buyer will be liable for all collection costs.
                4.5  The Business may suspend or terminate the Buyer's credit facility upon any payment becoming overdue.
                4.6  A certificate signed by an authorised officer of the Business stating the amount owed by the Buyer is prima facie evidence of that amount.
                """),
            },
            {
                "title": "Personal Property Securities Register (PPSR)",
                "body": textwrap.dedent(f"""\
                5.1  The Buyer grants the Business a security interest in all Goods supplied (and their proceeds) to secure payment and performance of all obligations under these Terms.
                5.2  The Buyer consents to the Business registering a financing statement or financing change statement on the PPSR.
                5.3  The Buyer must not allow any other person to register a security interest over the Goods without the prior written consent of the Business.
                5.4  If the Buyer deals with the Goods before payment in full, the Buyer must disclose the Business's security interest to any subsequent purchaser.
                5.5  The Buyer must do anything required by the Business to ensure the security interest is a perfected security interest.
                """),
            },
            {
                "title": "Retention of Title",
                "body": textwrap.dedent(f"""\
                6.1  Property in the Goods does not pass to the Buyer until the Business has received payment in full for the Goods and all other amounts owing by the Buyer to the Business.
                6.2  Until payment in full, the Buyer holds the Goods as bailee for the Business and must keep the Goods separate, identifiable, and properly stored.
                6.3  The Business may enter the Buyer's premises to repossess Goods where payment has not been made in accordance with these Terms.
                6.4  The Buyer grants the Business an irrevocable licence to enter any premises where the Goods are stored to inspect or repossess them.
                """),
            },
            {
                "title": "Delivery and Risk",
                "body": textwrap.dedent(f"""\
                7.1  Delivery of Goods is deemed to take place when the Goods leave the Business's premises or when collected by the Buyer.
                7.2  Risk in the Goods passes to the Buyer upon delivery, regardless of whether property has passed.
                7.3  The Business is not liable for any loss or damage to the Goods during transit or storage by the Buyer.
                7.4  Delivery times are estimates only and the Business is not liable for any delay.
                """),
            },
            {
                "title": "Personal Guarantee",
                "body": textwrap.dedent(f"""\
                8.1  If the Buyer is a company, the director(s) of the Buyer personally and jointly and severally guarantee the due payment of all amounts owed by the Buyer to the Business.
                8.2  The guarantee extends to all present and future indebtedness of the Buyer to the Business.
                8.3  The guarantor(s) consent to being bound by the same terms as the Buyer.
                8.4  This guarantee is a continuing obligation and remains in effect until all amounts owing are paid in full.
                """),
            },
            {
                "title": "Dispute Resolution",
                "body": textwrap.dedent(f"""\
                9.1  If a dispute arises in relation to these Terms, either party must first attempt to resolve the dispute by negotiation in good faith.
                9.2  If the dispute is not resolved within 14 days, either party may refer the dispute to mediation administered by the Resolution Institute or similar body.
                9.3  The costs of mediation are to be borne equally unless otherwise agreed.
                9.4  If the dispute is not resolved through mediation within 28 days, either party may commence court proceedings.
                """),
            },
            {
                "title": "Governing Law and Jurisdiction",
                "body": textwrap.dedent(f"""\
                10.1  These Terms are governed by the laws of New South Wales, Australia.
                10.2  The parties submit to the non-exclusive jurisdiction of the courts of New South Wales and the Federal Court of Australia.
                10.3  If any provision of these Terms is void, unenforceable, or illegal, it is severed to the extent necessary without affecting the remaining provisions.
                """),
            },
            {
                "title": "Privacy Act Consent",
                "body": textwrap.dedent(f"""\
                11.1  The Buyer consents to the collection, use, and disclosure of personal information in accordance with the Privacy Act 1988 (Cth) and the Australian Privacy Principles.
                11.2  The Business may collect information from the Buyer for the purposes of credit management, debt recovery, and compliance with law.
                11.3  The Buyer may access the Business's Privacy Policy at any time.
                """),
            },
            {
                "title": "Credit Reporting Consent",
                "body": textwrap.dedent(f"""\
                12.1  The Buyer consents to the Business disclosing information about the Buyer to a Credit Reporting Body for the purposes of credit assessment, credit reporting, and debt collection.
                12.2  The Buyer authorises the Business to obtain credit reports and credit-related personal information from a Credit Reporting Body.
                12.3  The Buyer acknowledges that default information may be reported to a Credit Reporting Body and may affect the Buyer's credit rating.
                12.4  The Business will handle all credit-related information in accordance with Part IIIA of the Privacy Act 1988 (Cth).
                """),
            },
            {
                "title": "AML/CTF Compliance",
                "body": textwrap.dedent(f"""\
                13.1  The Buyer must comply with all applicable anti-money laundering and counter-terrorism financing laws.
                13.2  The Buyer must provide identification and other documents as required by the Business to comply with the AML/CTF Act.
                13.3  The Business may refuse to provide Goods or Services, suspend credit facilities, or report suspicious matters to AUSTRAC if compliance obligations are not met.
                13.4  The Buyer warrants that all information provided for AML/CTF purposes is true, correct, and complete.
                """),
            },
        ]

        full_text = self._render_terms_of_trade(business_name, abn, business_address, effective_date, sections)
        word_count = len(full_text.split())

        return {
            "title": f"Terms of Trade – {business_name}",
            "document_type": "terms_of_trade",
            "business_name": business_name,
            "abn": abn,
            "effective_date": effective_date,
            "sections": sections,
            "full_text": full_text,
            "word_count": word_count,
        }

    def _render_terms_of_trade(
        self,
        business_name: str,
        abn: str,
        address: str,
        effective_date: str,
        sections: list[dict[str, str]],
    ) -> str:
        lines = [
            f"{'═' * 60}",
            f"  TERMS OF TRADE",
            f"  {business_name}",
            f"  ABN: {abn}",
            f"  {address}",
            f"  Effective: {effective_date}",
            f"{'═' * 60}",
            "",
        ]
        for idx, section in enumerate(sections):
            num = SECTION_NUMBERS.get(idx, str(idx + 1))
            lines.append(f"{'─' * 60}")
            lines.append(f"  {num}. {section['title'].upper()}")
            lines.append(f"{'─' * 60}")
            lines.append(section["body"].strip())
            lines.append("")
        lines.append(f"{'═' * 60}")
        lines.append("  END OF TERMS OF TRADE")
        lines.append(f"{'═' * 60}")
        return "\n".join(lines)

    # ── Personal Guarantee ──────────────────────────────────────────────

    def generate_personal_guarantee(
        self,
        business_name: str,
        guarantor_name: str,
        guarantor_address: str,
        guarantee_limit_cents: int,
        date: str = "",
    ) -> dict[str, Any]:
        """Generate a Director's Personal Guarantee document."""
        date_str = date if date else _today()
        limit_display = _cent_to_dollar(guarantee_limit_cents)

        sections = [
            {
                "title": "Parties",
                "body": textwrap.dedent(f"""\
                The Guarantor: {guarantor_name} of {guarantor_address}
                The Creditor: {business_name}
                """),
            },
            {
                "title": "Definitions",
                "body": textwrap.dedent(f"""\
                In this Guarantee:
                • "Business" means {business_name}.
                • "Guaranteed Obligations" means all present and future debts, liabilities, and obligations owed by the Buyer to the Business.
                • "Guarantee Limit" means {limit_display}.
                • "Buyer" means the company or entity for which the Guarantor acts as director.
                • "Guarantor" means {guarantor_name}.
                """),
            },
            {
                "title": "Guarantee and Indemnity",
                "body": textwrap.dedent(f"""\
                1.  The Guarantor irrevocably and unconditionally guarantees to the Business the due payment of all Guaranteed Obligations up to the Guarantee Limit of {limit_display}.
                2.  The Guarantor indemnifies the Business against all losses, costs, damages, and expenses (including legal costs on a solicitor-client basis) arising from any default under the Guaranteed Obligations.
                3.  This Guarantee is a principal obligation and not a guarantee of collection.
                4.  The Guarantor's liability is not affected by:
                    (a) any variation or extension of time granted to the Buyer;
                    (b) any failure by the Business to enforce its rights against the Buyer;
                    (c) any compounding, release, or variation of the Guaranteed Obligations;
                    (d) any insolvency event affecting the Buyer.
                """),
            },
            {
                "title": "Continuing Obligation",
                "body": textwrap.dedent(f"""\
                5.  This Guarantee is a continuing obligation and extends to all present and future indebtedness of the Buyer to the Business.
                6.  This Guarantee is irrevocable and remains in full force and effect until all Guaranteed Obligations have been fully and finally satisfied.
                7.  This Guarantee is absolute and unconditional, and the Guarantor waives any right to require the Business to proceed against the Buyer first.
                """),
            },
            {
                "title": "Demand",
                "body": textwrap.dedent(f"""\
                8.  The Business may make demand on the Guarantor at any time for payment of amounts owing under this Guarantee.
                9.  Demand may be made by any reasonable means, including in writing to the Guarantor's address set out above.
                10. The Guarantor must pay any demanded amount within 14 days of receiving demand.
                11. Failure to pay a demanded amount constitutes a default under this Guarantee.
                """),
            },
            {
                "title": "Costs",
                "body": textwrap.dedent(f"""\
                12. The Guarantor is liable for all costs and expenses incurred by the Business in connection with this Guarantee, including but not limited to:
                    (a) legal costs on a full indemnity basis;
                    (b) debt collection costs;
                    (c) administrative costs.
                13. Any costs recovered by the Business are in addition to the Guarantee Limit.
                """),
            },
            {
                "title": "Governing Law",
                "body": textwrap.dedent(f"""\
                14. This Guarantee is governed by the laws of New South Wales, Australia.
                15. The Guarantor submits to the non-exclusive jurisdiction of the courts of New South Wales.
                16. If any provision of this Guarantee is found to be invalid or unenforceable, the remaining provisions continue in full force and effect.
                """),
            },
        ]

        full_text = self._render_personal_guarantee(
            business_name, guarantor_name, guarantor_address,
            guarantee_limit_cents, date_str, sections,
        )
        word_count = len(full_text.split())

        return {
            "title": f"Personal Guarantee – {guarantor_name}",
            "document_type": "personal_guarantee",
            "business_name": business_name,
            "guarantor_name": guarantor_name,
            "guarantor_address": guarantor_address,
            "guarantee_limit_cents": guarantee_limit_cents,
            "date": date_str,
            "sections": sections,
            "full_text": full_text,
            "word_count": word_count,
        }

    def _render_personal_guarantee(
        self,
        business_name: str,
        guarantor_name: str,
        guarantor_address: str,
        guarantee_limit_cents: int,
        date_str: str,
        sections: list[dict[str, str]],
    ) -> str:
        limit_display = _cent_to_dollar(guarantee_limit_cents)
        lines = [
            f"{'═' * 60}",
            f"  DIRECTOR'S PERSONAL GUARANTEE",
            f"  {business_name}",
            f"  Date: {date_str}",
            f"{'═' * 60}",
            "",
            f"  Guarantor: {guarantor_name}",
            f"  Address:   {guarantor_address}",
            f"  Guarantee Limit: {limit_display}",
            "",
        ]
        for idx, section in enumerate(sections):
            num = SECTION_NUMBERS.get(idx, str(idx + 1))
            lines.append(f"{'─' * 60}")
            lines.append(f"  {num}. {section['title'].upper()}")
            lines.append(f"{'─' * 60}")
            lines.append(section["body"].strip())
            lines.append("")

        lines.append("  EXECUTED as a deed.")
        lines.append("")
        lines.append("  Signed by the Guarantor:")
        lines.append("")
        lines.append("  ________________________________")
        lines.append(f"  {guarantor_name}")
        lines.append("")
        lines.append("  Date: _________________________")
        lines.append("")
        lines.append(f"{'═' * 60}")
        lines.append("  END OF GUARANTEE")
        lines.append(f"{'═' * 60}")
        return "\n".join(lines)

    # ── Credit Application ──────────────────────────────────────────────

    def generate_credit_application(
        self,
        business_name: str,
        abn: str,
        address: str,
        trading_terms_days: int = 30,
        credit_limit_cents: int = 500_000,
    ) -> dict[str, Any]:
        """Generate a Credit Application form."""
        date_str = _today()
        limit_display = _cent_to_dollar(credit_limit_cents)

        sections = [
            {
                "title": "Applicant Details",
                "body": textwrap.dedent(f"""\
                Company / Business Name: ________________________________________
                ABN / ACN:              ________________________________________
                Trading Name:           ________________________________________
                Registered Address:     ________________________________________
                Business Address:       ________________________________________
                Telephone:              ________________________________________
                Email:                  ________________________________________
                GST Registered:         □ Yes  □ No
                ABN for Tax Invoice:    ________________________________________
                """),
            },
            {
                "title": "Directors / Proprietors",
                "body": textwrap.dedent("""\
                Director / Owner Name                  Date of Birth      Residential Address
                ──────────────────────────────────────────────────────────────────────────────────
                1. __________________________________________________________________________
                2. __________________________________________________________________________
                3. __________________________________________________________________________
                """),
            },
            {
                "title": "Business Structure",
                "body": textwrap.dedent("""\
                □ Sole Trader    □ Partnership    □ Company    □ Trust    □ Other: __________

                If Company:  Year Established: __________  No. of Employees: __________
                """),
            },
            {
                "title": "Trade References",
                "body": textwrap.dedent("""\
                Reference 1:
                  Company Name:  ________________________________________
                  Contact:       ________________________________________
                  Phone:         ________________________________________
                  Account No.:   ________________________________________

                Reference 2:
                  Company Name:  ________________________________________
                  Contact:       ________________________________________
                  Phone:         ________________________________________
                  Account No.:   ________________________________________

                Reference 3:
                  Company Name:  ________________________________________
                  Contact:       ________________________________________
                  Phone:         ________________________________________
                  Account No.:   ________________________________________
                """),
            },
            {
                "title": "Bank Details",
                "body": textwrap.dedent("""\
                Bank Name:          ________________________________________
                Branch:             ________________________________________
                Account Name:       ________________________________________
                BSB:                ________________________________________
                Account Number:     ________________________________________
                Account Type:       □ Business Cheque  □ Business Savings
                """),
            },
            {
                "title": "Credit Limit Requested",
                "body": textwrap.dedent(f"""\
                Requested Credit Limit: {limit_display}
                Requested Trading Terms: {trading_terms_days} days

                Preferred payment method:  □ EFT  □ Cheque  □ Direct Debit  □ Credit Card
                """),
            },
            {
                "title": "Terms and Conditions Acceptance",
                "body": textwrap.dedent(f"""\
                By submitting this application, the Applicant:
                1.  Warrants that all information provided is true, correct, and complete.
                2.  Consents to the Business obtaining credit reports and information from credit reporting bodies.
                3.  Authorises the Business to contact the trade and bank references listed above.
                4.  Acknowledges that approval of credit is at the sole discretion of the Business.
                5.  Agrees to be bound by the Business's Terms of Trade as current from time to time.
                6.  Consents to the collection and use of personal information in accordance with the Privacy Act 1988 (Cth).
                7.  Understands that the Business may report payment information to credit reporting bodies.
                """),
            },
            {
                "title": "Declaration and Signature",
                "body": textwrap.dedent("""\
                I/We declare that the information provided in this application is true and correct.

                Authorised Signatory:
                Name:    ________________________________
                Title:   ________________________________
                Date:    ________________________________
                Signature: ________________________________

                Company Seal (if applicable): ________________
                """),
            },
        ]

        full_text = self._render_credit_application(
            business_name, abn, address, trading_terms_days,
            credit_limit_cents, date_str, sections,
        )
        word_count = len(full_text.split())

        return {
            "title": f"Credit Application – {business_name}",
            "document_type": "credit_application",
            "business_name": business_name,
            "abn": abn,
            "address": address,
            "trading_terms_days": trading_terms_days,
            "credit_limit_cents": credit_limit_cents,
            "date": date_str,
            "sections": sections,
            "full_text": full_text,
            "word_count": word_count,
        }

    def _render_credit_application(
        self,
        business_name: str,
        abn: str,
        address: str,
        trading_terms_days: int,
        credit_limit_cents: int,
        date_str: str,
        sections: list[dict[str, str]],
    ) -> str:
        limit_display = _cent_to_dollar(credit_limit_cents)
        lines = [
            f"{'═' * 60}",
            f"  CREDIT APPLICATION",
            f"  {business_name}",
            f"  ABN: {abn}",
            f"  {address}",
            f"  Date: {date_str}",
            f"{'═' * 60}",
            "",
            f"  Requested Credit Limit: {limit_display}",
            f"  Requested Trading Terms: {trading_terms_days} days",
            "",
        ]
        for idx, section in enumerate(sections):
            num = SECTION_NUMBERS.get(idx, str(idx + 1))
            lines.append(f"{'─' * 60}")
            lines.append(f"  {num}. {section['title'].upper()}")
            lines.append(f"{'─' * 60}")
            lines.append(section["body"].strip())
            lines.append("")
        lines.append(f"{'═' * 60}")
        lines.append("  END OF CREDIT APPLICATION")
        lines.append(f"{'═' * 60}")
        return "\n".join(lines)

    # ── Generate All ────────────────────────────────────────────────────

    def generate_all(
        self,
        business_name: str,
        abn: str,
        address: str,
        director_name: str = "",
    ) -> dict[str, Any]:
        """Generate all three documents at once."""
        terms = self.generate_terms_of_trade(
            business_name=business_name,
            business_address=address,
            abn=abn,
        )
        guarantee = self.generate_personal_guarantee(
            business_name=business_name,
            guarantor_name=director_name or "____________________",
            guarantor_address="____________________",
            guarantee_limit_cents=500_000,
        )
        application = self.generate_credit_application(
            business_name=business_name,
            abn=abn,
            address=address,
        )
        return {
            "terms_of_trade": terms,
            "personal_guarantee": guarantee,
            "credit_application": application,
        }

    # ── PDF Generation ──────────────────────────────────────────────────

    def generate_pdf(
        self,
        document: dict[str, Any],
        document_type: str,
        output_dir: str = "output/legal_docs",
    ) -> str:
        """Generate a professionally formatted PDF of any document."""
        if not HAS_FPDF:
            raise ImportError(
                "fpdf2 is required for PDF generation. "
                "Install it with: pip install fpdf2"
            )

        os.makedirs(output_dir, exist_ok=True)
        title = document.get("title", "Legal Document").replace(" ", "_")
        safe_title = "".join(c for c in title if c.isalnum() or c in ("_", "-"))
        filename = f"{safe_title}_{_today().replace(' ', '_')}.pdf"
        filepath = os.path.join(output_dir, filename)

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=25)
        pdf.add_page()

        pdf.set_font("Helvetica", "B", 18)
        pdf.set_text_color(*_TETHER_ACCENT)
        pdf.cell(0, 12, document.get("title", "Legal Document"), new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.set_draw_color(*_TETHER_ACCENT)
        pdf.set_line_width(0.8)
        pdf.line(20, pdf.get_y() + 2, 190, pdf.get_y() + 2)
        pdf.ln(6)

        meta_items = []
        if "business_name" in document:
            meta_items.append(f"Business: {document['business_name']}")
        if "abn" in document:
            meta_items.append(f"ABN: {document['abn']}")
        if "date" in document:
            meta_items.append(f"Date: {document['date']}")
        if "effective_date" in document:
            meta_items.append(f"Effective: {document['effective_date']}")

        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(80, 80, 80)
        for item in meta_items:
            pdf.cell(0, 6, item, new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.ln(4)

        pdf.set_draw_color(200, 200, 200)
        pdf.set_line_width(0.3)

        sections = document.get("sections", [])
        for idx, section in enumerate(sections):
            pdf.set_font("Helvetica", "B", 13)
            pdf.set_text_color(*_TETHER_ACCENT)
            sec_num = SECTION_NUMBERS.get(idx, str(idx + 1))
            heading = f"{sec_num}. {section['title'].upper()}"
            pdf.cell(0, 9, heading, new_x="LMARGIN", new_y="NEXT")
            pdf.line(20, pdf.get_y(), 190, pdf.get_y())
            pdf.ln(2)

            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(40, 40, 40)
            body = section.get("body", "").strip()
            for line in body.split("\n"):
                line = line.strip()
                if not line:
                    pdf.ln(3)
                    continue
                if line.startswith("•") or line.startswith("□"):
                    pdf.set_x(25)
                    pdf.multi_cell(160, 5, line)
                elif line[0:1].isdigit() and "." in line[:3]:
                    pdf.set_x(22)
                    pdf.multi_cell(163, 5, line)
                else:
                    pdf.set_x(22)
                    pdf.multi_cell(163, 5, line)
                pdf.ln(1)
            pdf.ln(4)

        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(*_TETHER_ACCENT)
        pdf.cell(0, 8, f"{'═' * 50}", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_text_color(100, 100, 100)
        footer_text = f"Generated by {self.company_name} Legal Document System"
        pdf.cell(0, 6, footer_text, new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.cell(0, 6, f"Document Type: {document_type}", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.cell(0, 6, f"Generated: {_today()}", new_x="LMARGIN", new_y="NEXT", align="C")

        pdf.output(filepath)
        return filepath

    # ── Document Listing ────────────────────────────────────────────────

    def list_available_documents(self) -> list[dict[str, str]]:
        """Return list of available document types with descriptions."""
        return [
            {
                "type": "terms_of_trade",
                "name": "Terms of Trade",
                "description": (
                    "Comprehensive trading terms covering credit terms, payment obligations, "
                    "late payment penalties, PPSR security interests, retention of title, "
                    "personal guarantees, dispute resolution, governing law (NSW), Privacy Act "
                    "consent, credit reporting consent, and AML/CTF compliance."
                ),
                "generator_method": "generate_terms_of_trade",
            },
            {
                "type": "personal_guarantee",
                "name": "Director's Personal Guarantee",
                "description": (
                    "Personal guarantee and indemnity from a company director for all "
                    "present and future debts owed by the company. Includes continuing "
                    "obligation, demand provisions, costs, and governing law."
                ),
                "generator_method": "generate_personal_guarantee",
            },
            {
                "type": "credit_application",
                "name": "Credit Application",
                "description": (
                    "Formal credit application collecting company details, director information, "
                    "three trade references, bank details, requested credit limit and trading terms, "
                    "and terms acceptance with privacy consent."
                ),
                "generator_method": "generate_credit_application",
            },
        ]


# ── Convenience function ────────────────────────────────────────────────

def generate_all_documents(
    business_name: str,
    abn: str,
    address: str,
    director_name: str = "",
    output_dir: str = "output/legal_docs",
) -> dict[str, Any]:
    """One-call helper: generate all three documents and PDFs."""
    generator = LegalDocumentGenerator()
    docs = generator.generate_all(business_name, abn, address, director_name)

    pdf_paths: dict[str, str] = {}
    for doc_type, doc in docs.items():
        try:
            path = generator.generate_pdf(doc, doc_type, output_dir)
            pdf_paths[doc_type] = path
        except ImportError:
            pdf_paths[doc_type] = "fpdf2 not installed"

    return {
        "documents": docs,
        "pdf_paths": pdf_paths,
        "generated_at": _today(),
    }
