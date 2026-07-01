#!/usr/bin/env python3
"""Tether routes — /api/tether/* routes (debt collection specific) and related tether routes."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request

from database import get_connection
from tether_engine import TetherEngine
from reply_pipeline import ReplyPipeline
from bpay_engine import BPAYEngine

logger = logging.getLogger(__name__)


def register(app: FastAPI):
    """Register all /api/tether/* and related collection routes."""

    # ── Tether Engine ──────────────────────────────────────────────────

    @app.post("/api/tether/ingest")
    def tether_ingest(data: dict):
        """Ingest debtors for the Tether collections workflow."""
        engine = TetherEngine("api-biz", data.get("business_name", "Demo Business"))
        csv_content = data.get("csv", "")
        if not csv_content:
            raise HTTPException(400, "csv field is required")
        debtors = engine.ingest_csv(csv_content)
        return {"ingested": len(debtors), "debtors": [d.id for d in debtors]}

    @app.post("/api/tether/process")
    def tether_process(data: dict):
        """Run the escalation engine on all debtors."""
        engine = TetherEngine("api-biz", "Demo")
        result = engine.process_all()
        return result

    # ── Reply Handling ──────────────────────────────────────────────────

    _reply_handler: ReplyPipeline | None = None

    def _get_rhandler() -> ReplyPipeline:
        global _reply_handler
        if _reply_handler is None:
            _reply_handler = ReplyPipeline()
        return _reply_handler

    @app.post("/api/tether/reply/classify")
    def classify_debtor_reply(data: dict):
        """Submit an incoming debtor email reply for AI classification and auto-action.

        Body: {
            "debtor_id": "d-001",
            "subject": "Re: Payment reminder",
            "body": "I dispute this invoice...",
            "email_from": "debtor@example.com"
        }

        Returns the classification result and action taken.
        """
        debtor_id = data.get("debtor_id", "")
        subject = data.get("subject", "")
        body = data.get("body", "")
        email_from = data.get("email_from", "")

        if not debtor_id:
            raise HTTPException(400, "debtor_id is required")
        if not body:
            raise HTTPException(400, "body is required")

        # Verify debtor exists
        conn = get_connection()
        existing = conn.execute("SELECT id FROM debtors WHERE id = ?", (debtor_id,)).fetchone()
        if not existing:
            raise HTTPException(404, f"Debtor {debtor_id} not found")

        handler = _get_rhandler()
        result = handler.ingest_reply(
            debtor_id=debtor_id,
            subject=subject,
            body=body,
            email_from=email_from,
        )

        # Log to audit trail
        try:
            from audit_trail import AuditTrail
            AuditTrail().log_tool_call(
                agent_id="tether-collections",
                tool_name="reply_pipeline::classify",
                tool_input={
                    "debtor_id": debtor_id,
                    "subject": subject[:100],
                },
                tool_output=result,
                reasoning=f"Reply classification: {result['category']} ({int(result['confidence']*100)}%) → {result['action']}",
                workflow_id="wf-tether-replies",
            )
        except Exception:
            pass  # Audit logging is best-effort

        return result

    @app.get("/api/tether/replies")
    def list_replies(
        debtor_id: str | None = None,
        category: str | None = None,
        resolution: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ):
        """List classified debtor replies with optional filters."""
        handler = _get_rhandler()
        return handler.get_replies(
            debtor_id=debtor_id,
            category=category,
            resolution=resolution,
            limit=limit,
            offset=offset,
        )

    @app.get("/api/tether/replies/pending")
    def list_pending_replies():
        """Get all pending (unresolved) replies ordered by priority.

        Disputes come first, then queries, then others.
        Includes debtor name and invoice info via JOIN.
        """
        handler = _get_rhandler()
        return handler.get_pending_actions()

    @app.get("/api/tether/replies/summary")
    def reply_handler_summary():
        """Get summary statistics about all classified replies."""
        handler = _get_rhandler()
        return handler.get_summary()

    @app.post("/api/tether/replies/{reply_id}/resolve")
    def resolve_reply(reply_id: str, data: dict):
        """Mark a classified reply as resolved.

        Body: {
            "resolution": "acknowledged|responded|resolved|ignored",
            "resolved_by": "agent_id or human name" (optional)
        }
        """
        resolution = data.get("resolution", "")
        if resolution not in ("acknowledged", "responded", "resolved", "ignored"):
            raise HTTPException(400, "resolution must be: acknowledged, responded, resolved, or ignored")

        resolved_by = data.get("resolved_by", "")
        handler = _get_rhandler()
        success = handler.resolve_reply(reply_id, resolution, resolved_by)
        if not success:
            raise HTTPException(404, f"Reply {reply_id} not found")
        return {"status": "ok", "reply_id": reply_id, "resolution": resolution}

    # ── BPAY Payment Endpoints ────────────────────────────────────────────

    _bpay_engine: BPAYEngine | None = None

    def _get_bpay() -> BPAYEngine:
        global _bpay_engine
        if _bpay_engine is None:
            _bpay_engine = BPAYEngine()
        return _bpay_engine

    @app.get("/api/bpay/info/{debtor_id}")
    def get_bpay_info(debtor_id: str):
        """Get BPAY payment information for a debtor.

        Returns Biller Code, CRN, and formatted payment instructions
        for the debtor to pay via their Australian banking app.
        """
        conn = get_connection()
        row = conn.execute(
            "SELECT id, name, invoice_number, amount_cents, "
            "business_id FROM debtors WHERE id = ?",
            (debtor_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, f"Debtor {debtor_id} not found")

        debtor = dict(row)
        bpay = _get_bpay()
        business_name = conn.execute(
            "SELECT name FROM agents WHERE id = 'tether-collections'"
        ).fetchone()
        biz_name = business_name["name"] if business_name else "Demo Business"

        info = bpay.generate_payment_info(
            business_id=debtor.get("business_id", "biz-001"),
            business_name=biz_name,
            debtor_id=debtor["id"],
            debtor_name=debtor["name"],
            invoice_number=debtor["invoice_number"],
            amount_cents=debtor["amount_cents"],
        )

        return {
            "biller_code": info.biller_code,
            "crn": info.crn,
            "reference": info.reference,
            "amount_dollars": info.amount_dollars,
            "amount_cents": info.amount_cents,
            "payment_instructions": info.payment_instructions,
            "generated_at": info.generated_at,
        }

    @app.post("/api/bpay/initiate")
    def initiate_bpay_payment(data: dict):
        """Record that a BPAY payment has been initiated (CRN provided to debtor).

        This does NOT take a real payment — BPAY is processed through the
        debtor's banking app. This endpoint tracks the initiation for
        reconciliation purposes.
        """
        debtor_id = data.get("debtor_id", "")
        conn = get_connection()
        row = conn.execute(
            "SELECT id, name, invoice_number, amount_cents, "
            "bpay_biller_code, bpay_crn FROM debtors WHERE id = ?",
            (debtor_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, f"Debtor {debtor_id} not found")

        d = dict(row)
        bpay = _get_bpay()
        payment = bpay.record_initiation(
            debtor_id=d["id"],
            debtor_name=d["name"],
            invoice_number=d["invoice_number"],
            amount_cents=d["amount_cents"],
            biller_code=d["bpay_biller_code"] or "",
            crn=d["bpay_crn"] or "",
        )

        # Record in database
        import uuid
        pid = f"bpay-{uuid.uuid4().hex[:12]}"
        conn.execute(
            "INSERT INTO bpay_payments (id, debtor_id, business_id, biller_code, crn, "
            "reference, amount_cents, status, initiated_at, expected_settlement) "
            "VALUES (?, ?, 'biz-001', ?, ?, ?, ?, 'pending', datetime('now'), datetime('now', '+2 days'))",
            (pid, debtor_id, payment.biller_code, payment.crn,
             d["invoice_number"], d["amount_cents"])
        )
        conn.commit()

        return {
            "status": "initiated",
            "payment_id": pid,
            "biller_code": payment.biller_code,
            "crn": payment.crn,
            "amount_dollars": f"${d['amount_cents'] / 100:,.2f}",
            "expected_settlement": "2 business days",
        }

    @app.get("/api/bpay/pending")
    def list_pending_bpay():
        """List all pending BPAY payments awaiting settlement."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM bpay_payments WHERE status IN ('pending', 'processing') "
            "ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    @app.get("/api/bpay/settled")
    def list_settled_bpay():
        """List all settled BPAY payments."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM bpay_payments WHERE status = 'cleared' "
            "ORDER BY settled_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    @app.post("/api/bpay/confirm/{payment_id}")
    def confirm_bpay_payment(payment_id: str):
        """Confirm a BPAY payment as settled (simulates bank notification).

        In production, this would be a webhook from the bank's payment gateway.
        For demo purposes, it manually confirms settlement.
        """
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM bpay_payments WHERE id = ?", (payment_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, f"BPAY payment {payment_id} not found")

        p = dict(row)
        conn.execute(
            "UPDATE bpay_payments SET status = 'cleared', settled_at = datetime('now'), "
            "amount_paid_cents = ?, updated_at = datetime('now') WHERE id = ?",
            (p["amount_cents"], payment_id)
        )

        # Also update the debtor record
        conn.execute(
            "UPDATE debtors SET state = 'paid', paid_at = datetime('now'), "
            "paid_amount_cents = ?, bpay_paid_at = datetime('now'), "
            "bpay_paid_cents = ? WHERE id = ?",
            (p["amount_cents"], p["amount_cents"], p["debtor_id"])
        )
        conn.commit()

        return {
            "status": "confirmed",
            "payment_id": payment_id,
            "debtor_id": p["debtor_id"],
            "amount_dollars": f"${p['amount_cents'] / 100:,.2f}",
            "settled_at": "now",
        }

    @app.get("/api/bpay/summary")
    def get_bpay_summary():
        """Get BPAY payment summary statistics."""
        conn = get_connection()
        row = conn.execute("SELECT * FROM bpay_summary").fetchone()
        summary = dict(row) if row else {}
        return summary

    # ── Late Fee Automation ──────────────────────────────────────────────

    from late_fees import LateFeeEngine as _LateFeeEngine

    _late_fee_engine: _LateFeeEngine | None = None

    def _get_lfee() -> _LateFeeEngine:
        global _late_fee_engine
        if _late_fee_engine is None:
            _late_fee_engine = _LateFeeEngine("biz-001")
        return _late_fee_engine

    @app.get("/api/late-fees/rules")
    def list_late_fee_rules():
        """List all late fee rules."""
        return _get_lfee().list_rules(active_only=False)

    @app.post("/api/late-fees/rules")
    def create_late_fee_rule(data: dict):
        """Create a new late fee rule."""
        return _get_lfee().create_rule(**data)

    @app.get("/api/late-fees/rules/{rule_id}")
    def get_late_fee_rule(rule_id: str):
        """Get a single late fee rule."""
        rule = _get_lfee().get_rule(rule_id)
        if not rule:
            raise HTTPException(404, f"Late fee rule {rule_id} not found")
        return rule

    @app.put("/api/late-fees/rules/{rule_id}")
    def update_late_fee_rule(rule_id: str, data: dict):
        """Update a late fee rule."""
        existing = _get_lfee().get_rule(rule_id)
        if not existing:
            raise HTTPException(404, f"Late fee rule {rule_id} not found")
        return _get_lfee().update_rule(rule_id, **data)

    @app.delete("/api/late-fees/rules/{rule_id}")
    def delete_late_fee_rule(rule_id: str):
        """Delete a late fee rule."""
        existing = _get_lfee().get_rule(rule_id)
        if not existing:
            raise HTTPException(404, f"Late fee rule {rule_id} not found")
        _get_lfee().delete_rule(rule_id)
        return {"status": "deleted", "rule_id": rule_id}

    @app.post("/api/late-fees/defaults")
    def create_default_rules():
        """Create ACCC-compliant default late fee rules."""
        return _get_lfee().create_default_rules()

    @app.post("/api/late-fees/assess")
    def assess_late_fees(data: dict):
        """Assess late fees for a debtor."""
        debtor_id = data.get("debtor_id", "")
        debtor_name = data.get("debtor_name", "")
        invoice_number = data.get("invoice_number", "")
        amount_cents = data.get("amount_cents", 0)
        days_overdue = data.get("days_overdue", 0)
        rule_id = data.get("rule_id", None)

        if not debtor_id or not amount_cents:
            raise HTTPException(400, "debtor_id and amount_cents are required")

        result = _get_lfee().assess_fees(
            debtor_id=debtor_id,
            debtor_name=debtor_name,
            invoice_number=invoice_number,
            amount_cents=amount_cents,
            days_overdue=days_overdue,
            rule_id=rule_id,
        )
        if "error" in result:
            raise HTTPException(400, result["error"])
        return result

    @app.get("/api/late-fees/assessments")
    def list_fee_assessments(
        debtor_id: str | None = None,
        status: str | None = None,
        limit: int = Query(50, le=500),
    ):
        """List fee assessments."""
        return _get_lfee().get_assessments(debtor_id=debtor_id, status=status, limit=limit)

    @app.get("/api/late-fees/assessments/{assessment_id}")
    def get_fee_assessment(assessment_id: str):
        """Get a single fee assessment."""
        assessment = _get_lfee().get_assessment(assessment_id)
        if not assessment:
            raise HTTPException(404, f"Fee assessment {assessment_id} not found")
        return assessment

    @app.post("/api/late-fees/assessments/{assessment_id}/waive")
    def waive_fee_assessment(assessment_id: str, data: dict):
        """Waive a fee assessment."""
        reason = data.get("reason", "")
        return _get_lfee().waive_fee(assessment_id, reason)

    @app.post("/api/late-fees/assessments/{assessment_id}/pay")
    def mark_fee_paid(assessment_id: str, data: dict):
        """Mark a fee assessment as paid."""
        amount_cents = data.get("amount_cents", 0)
        return _get_lfee().mark_fee_paid(assessment_id, amount_cents)

    @app.get("/api/late-fees/revenue")
    def get_fee_revenue():
        """Get aggregated fee revenue statistics."""
        return _get_lfee().get_revenue()

    @app.post("/api/late-fees/assessments/{assessment_id}/notice")
    def generate_fee_notice(assessment_id: str):
        """Generate a fee notice text for an assessment."""
        result = _get_lfee().generate_fee_notice(assessment_id)
        if "error" in result:
            raise HTTPException(404, result["error"])
        return result

    @app.post("/api/late-fees/batch-assess")
    def batch_assess_fees(data: dict):
        """Assess late fees for multiple debtors at once."""
        debtors = data.get("debtors", [])
        if not debtors:
            raise HTTPException(400, "debtors list is required")

        engine = _get_lfee()
        results = []
        for d in debtors:
            result = engine.assess_fees(
                debtor_id=d.get("debtor_id", ""),
                debtor_name=d.get("debtor_name", ""),
                invoice_number=d.get("invoice_number", ""),
                amount_cents=d.get("amount_cents", 0),
                days_overdue=d.get("days_overdue", 0),
                rule_id=d.get("rule_id", None),
            )
            results.append(result)

        return {"results": results, "total": len(results), "fees_applied": sum(1 for r in results if r.get("action") == "fee_applied")}
