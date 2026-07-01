#!/usr/bin/env python3
"""Australian debt collection invoice notice generator module."""

from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
import json
import os
import uuid


@dataclass
class NoticeRecord:
    """Record of a generated notice."""
    notice_id: str
    debtor_id: str
    notice_type: str
    notice_level: int
    format: str
    generated_at: datetime
    file_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "notice_id": self.notice_id,
            "debtor_id": self.debtor_id,
            "notice_type": self.notice_type,
            "notice_level": self.notice_level,
            "format": self.format,
            "generated_at": self.generated_at.isoformat(),
            "file_path": self.file_path,
        }


NOTICE_TEMPLATES = {
    1: {
        "title": "PAYMENT OVERDUE — REMINDER",
        "message": (
            "Your account with {business_name} is now overdue.\n\n"
            "Invoice: {invoice_number}\n"
            "Amount Due: ${amount}\n"
            "Days Overdue: {days_overdue}\n\n"
            "Please arrange payment within 7 days to avoid further action.\n\n"
            "If you have already paid, please disregard this notice."
        ),
        "sticker": "OVERDUE — Please pay within 7 days to avoid escalation.",
    },
    2: {
        "title": "SECOND NOTICE — ACCOUNT OVERDUE",
        "message": (
            "This account with {business_name} is now overdue.\n\n"
            "Invoice: {invoice_number}\n"
            "Amount Due: ${amount}\n"
            "Days Overdue: {days_overdue}\n\n"
            "WE REQUIRE PAYMENT WITHIN 7 DAYS or this account will be referred for recovery.\n\n"
            "Payment link: {payment_link}\n\n"
            "Contact us immediately if you wish to discuss this matter."
        ),
        "sticker": "THIS ACCOUNT IS NOW OVERDUE. Pay within 7 days or action will be taken.",
    },
    3: {
        "title": "FINAL NOTICE — IMMEDIATE ACTION REQUIRED",
        "message": (
            "FINAL NOTICE\n\n"
            "Despite previous notices, invoice {invoice_number} with {business_name}\n"
            "for ${amount} remains unpaid after {days_overdue} days.\n\n"
            "WE REQUIRE IMMEDIATE PAYMENT to prevent further escalation.\n\n"
            "Payment link: {payment_link}\n\n"
            "If payment is not received, this matter may be referred for legal action."
        ),
        "sticker": "FINAL NOTICE — Immediate payment required to prevent legal action.",
    },
}


class InvoiceNoticeGenerator:
    """Generates overdue invoice notices, overlays, and printable stickers.
    
    Digital equivalent of Marshall Freeman's physical reminder sticker system.
    """

    def __init__(self):
        self._notice_history: List[NoticeRecord] = []

    def generate_overlay(
        self,
        overdue_days: int,
        amount_cents: int,
        business_name: str,
        invoice_number: str,
        payment_link: str = "",
        state: str = "NSW",
        notice_level: int = 1,
    ) -> str:
        """Generate HTML overlay text for invoice/statement printing."""
        amount_dollars = f"{amount_cents / 100:,.2f}"
        template = NOTICE_TEMPLATES.get(notice_level, NOTICE_TEMPLATES[1])
        message = template["message"].format(
            business_name=business_name,
            invoice_number=invoice_number,
            amount=amount_dollars,
            days_overdue=overdue_days,
            payment_link=payment_link or "#",
        )
        level_colors = {1: "#ffb300", 2: "#e53935", 3: "#b71c1c"}
        color = level_colors.get(notice_level, "#ffb300")
        level_label = {1: "REMINDER", 2: "OVERDUE", 3: "FINAL NOTICE"}.get(notice_level, "NOTICE")

        html = f"""<div style="border:3px solid {color};border-radius:8px;padding:16px;margin:12px 0;
                    font-family:Arial,sans-serif;background:#fff9f0;">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
    <strong style="font-size:14px;color:{color};">⚠ {template['title']}</strong>
    <span style="font-size:11px;color:#666;">{business_name}</span>
  </div>
  <div style="font-size:13px;line-height:1.5;color:#333;white-space:pre-wrap;">{message}</div>
  {f'<div style="margin-top:10px;"><a href="{payment_link}" style="display:inline-block;padding:8px 20px;background:{color};color:#fff;text-decoration:none;border-radius:4px;font-weight:bold;">PAY NOW</a></div>' if payment_link else ''}
  <div style="margin-top:8px;font-size:10px;color:#999;border-top:1px solid #eee;padding-top:6px;">
    This is an automated notice from {business_name}. Reference: {invoice_number}
  </div>
</div>"""
        return html

    def generate_printable_notice(
        self,
        debtor_name: str,
        debtor_address: str,
        business_name: str,
        invoice_number: str,
        amount_cents: int,
        days_overdue: int,
        notice_level: int = 1,
        include_payment_slip: bool = True,
    ) -> Dict[str, Any]:
        """Generate a full-page printable overdue notice."""
        amount_dollars = f"{amount_cents / 100:,.2f}"
        template = NOTICE_TEMPLATES.get(notice_level, NOTICE_TEMPLATES[1])
        today = datetime.now().strftime("%d %B %Y")
        message = template["message"].format(
            business_name=business_name,
            invoice_number=invoice_number,
            amount=amount_dollars,
            days_overdue=days_overdue,
            payment_link="[See below]",
        )
        level_label = {1: "REMINDER", 2: "SECOND NOTICE", 3: "FINAL NOTICE"}.get(notice_level, "NOTICE")

        html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
  body {{ font-family: 'Helvetica', Arial, sans-serif; font-size: 12pt; line-height: 1.5; color: #222; margin: 40px; }}
  .letterhead {{ border-bottom: 3px solid #e53935; padding-bottom: 12px; margin-bottom: 24px; }}
  .letterhead h1 {{ font-size: 20pt; color: #e53935; margin: 0; }}
  .letterhead p {{ color: #666; font-size: 10pt; margin: 2px 0; }}
  .notice-title {{ font-size: 16pt; font-weight: bold; color: #e53935; margin: 16px 0; }}
  .debtor-block {{ margin-bottom: 20px; }}
  .message-body {{ margin: 16px 0; white-space: pre-wrap; }}
  .payment-slip {{ border: 2px dashed #e53935; padding: 16px; margin-top: 32px; background: #fff9f0; }}
  .payment-slip h3 {{ margin-top: 0; color: #e53935; }}
  .footer {{ margin-top: 48px; font-size: 9pt; color: #999; border-top: 1px solid #ccc; padding-top: 8px; }}
</style></head><body>
<div class="letterhead">
  <h1>{business_name}</h1>
  <p>ABN: [ABN]</p>
  <p>[Business Address]</p>
</div>
<div class="notice-title">{level_label}</div>
<p style="color:#666;">Date: {today}</p>
<div class="debtor-block">
  <p><strong>{debtor_name}</strong></p>
  <p>{debtor_address}</p>
</div>
<p><strong>RE: Invoice {invoice_number} — ${amount_dollars}</strong></p>
<div class="message-body">{message}</div>
{f'''
<div class="payment-slip">
  <h3>📌 PAYMENT SLIP — Please detach and return with payment</h3>
  <p><strong>Business:</strong> {business_name}</p>
  <p><strong>Invoice:</strong> {invoice_number}</p>
  <p><strong>Amount Due:</strong> ${amount_dollars}</p>
  <p><strong>Reference:</strong> {invoice_number}</p>
  <hr>
  <p style="font-size:10pt;color:#666;">Pay online or return this slip with your cheque. 
  Please include the invoice number as your payment reference.</p>
</div>
''' if include_payment_slip else ''}
<div class="footer">
  <p>This is a payment notice from {business_name}. If you believe there has been an error 
  or wish to discuss this matter, please contact us immediately.</p>
</div>
</body></html>"""

        return {"html": html, "text": message, "notice_type": level_label}

    def generate_pdf_notice(self, notice_data: Dict[str, Any], output_dir: str = "output/notices") -> str:
        """Generate a PDF of the printable notice using fpdf2."""
        try:
            from fpdf import FPDF
        except ImportError:
            return "PDF generation requires fpdf2: pip install fpdf2"

        os.makedirs(output_dir, exist_ok=True)
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 18)
        pdf.set_text_color(229, 57, 53)
        pdf.cell(0, 12, notice_data.get("notice_type", "NOTICE"), align="C", ln=True)
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(60, 60, 60)
        pdf.multi_cell(0, 6, notice_data.get("text", ""))
        filename = f"notice_{uuid.uuid4().hex[:8]}.pdf"
        path = os.path.join(output_dir, filename)
        pdf.output(path)
        return path

    def generate_reminder_sticker_html(
        self,
        amount_cents: int,
        days_overdue: int,
        business_name: str,
        escalation_date: str = "",
    ) -> str:
        """Generate small 3x5 sticker HTML for printing on sticker paper."""
        amount_dollars = f"{amount_cents / 100:,.2f}"
        level = 1 if days_overdue <= 7 else (2 if days_overdue <= 30 else 3)
        sticker_text = NOTICE_TEMPLATES[level]["sticker"]

        return f"""<div style="width:3in;height:5in;border:2px solid #e53935;padding:16px;
                    font-family:Arial,sans-serif;box-sizing:border-box;text-align:center;
                    background:#fff9f0;page-break-after:always;">
  <div style="font-size:14pt;font-weight:bold;color:#e53935;margin-bottom:8px;">⚠ OVERDUE NOTICE</div>
  <div style="font-size:12pt;margin:12px 0;color:#333;">{sticker_text}</div>
  <div style="border-top:2px solid #e53935;margin:12px 0;"></div>
  <div style="font-size:11pt;color:#555;">
    <p>Amount: <strong>${amount_dollars}</strong></p>
    <p>Days Overdue: <strong>{days_overdue}</strong></p>
    {f'<p>Deadline: <strong>{escalation_date}</strong></p>' if escalation_date else ''}
  </div>
  <div style="margin-top:16px;font-size:9pt;color:#999;">
    {business_name}
  </div>
</div>"""

    def generate_statement_overlay(
        self,
        debtor_name: str,
        outstanding_invoices: List[Dict[str, Any]],
        total_cents: int,
    ) -> str:
        """Generate HTML overlay for monthly statements."""
        amount_dollars = f"{total_cents / 100:,.2f}"
        rows = "".join(
            f"<tr><td>{inv.get('invoice_number', '')}</td>"
            f"<td>${inv.get('amount_cents', 0) / 100:,.2f}</td>"
            f"<td>{inv.get('days_overdue', 0)} days</td></tr>"
            for inv in outstanding_invoices
        )

        return f"""<div style="border:2px solid #e53935;border-radius:8px;padding:16px;margin:12px 0;">
  <div style="font-size:14px;font-weight:bold;color:#e53935;margin-bottom:8px;">
    ⚠ OVERDUE INVOICES — {debtor_name}
  </div>
  <table style="width:100%;border-collapse:collapse;font-size:12px;">
    <tr style="background:#f5f5f5;">
      <th style="padding:6px;text-align:left;border-bottom:1px solid #ddd;">Invoice</th>
      <th style="padding:6px;text-align:left;border-bottom:1px solid #ddd;">Amount</th>
      <th style="padding:6px;text-align:left;border-bottom:1px solid #ddd;">Overdue</th>
    </tr>
    {rows}
    <tr style="font-weight:bold;">
      <td style="padding:6px;border-top:2px solid #e53935;">TOTAL OVERDUE</td>
      <td style="padding:6px;border-top:2px solid #e53935;color:#e53935;">${amount_dollars}</td>
      <td style="padding:6px;border-top:2px solid #e53935;"></td>
    </tr>
  </table>
  <div style="margin-top:8px;font-size:10px;color:#999;">
    Please arrange payment to avoid further escalation.
  </div>
</div>"""

    def batch_generate_notices(self, debtors: List[Dict[str, Any]], output_dir: str = "output/notices") -> List[str]:
        """Generate printable notices for multiple debtors at once."""
        paths = []
        for d in debtors:
            notice = self.generate_printable_notice(
                debtor_name=d.get("debtor_name", ""),
                debtor_address=d.get("debtor_address", ""),
                business_name=d.get("business_name", ""),
                invoice_number=d.get("invoice_number", ""),
                amount_cents=d.get("amount_cents", 0),
                days_overdue=d.get("days_overdue", 0),
                notice_level=d.get("notice_level", 1),
            )
            path = self.generate_pdf_notice(notice, output_dir)
            paths.append(path)
        return paths

    def get_notice_history(self, debtor_id: str) -> List[Dict[str, Any]]:
        """Get notice generation history for a debtor."""
        return [r.to_dict() for r in self._notice_history if r.debtor_id == debtor_id]
