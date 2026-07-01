#!/usr/bin/env python3
"""Tether Reply Pipeline — bridges reply classification with database persistence
and escalation engine integration.

Uses the existing ReplyHandler for classification logic (regex-based with
structured data extraction), then adds:
- Database persistence (debtor_replies table)
- Integration with the escalation engine's reply-aware state machine
- Audit logging
- API-facing convenience methods
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

# Import the existing classification module
from reply_handler import ReplyHandler as Classifier


class ReplyPipeline:
    """Full reply lifecycle: classify → persist → action → audit.

    Three-tier architecture:
    1. Classifier: regex-based classification with structured data extraction
    2. Persister: stores to debtor_replies table in the ESAM database
    3. Actioner: applies the right escalation engine state transition
    """

    def __init__(self):
        self._classifier = Classifier()

    # ── Classification (delegates to existing ReplyHandler) ─────────

    def classify(self, text: str) -> dict[str, Any]:
        """Classify a reply text using the regex-based classifier.

        Returns dict with category, confidence, reason, extracted_data.
        """
        result = self._classifier.classify(text)

        # Normalise 'other' to 'general'
        if result["category"] == "other":
            result["category"] = "general"
            result["confidence"] = 0.1

        return result

    # ── Category → Action Mapping ───────────────────────────────────

    CATEGORY_ACTION_MAP = {
        "dispute":              "halt_collection",
        "promise_to_pay":       "pause_escalation",
        "out_of_office":        "skip_retry",
        "query":                "route_to_human",
        "payment_confirmation": "log_only",
        "unsubscribe":          "halt_collection",
        "general":              "log_only",
    }

    CATEGORY_HUMAN_LABELS = {
        "dispute":              "Dispute — halt collection",
        "promise_to_pay":       "Promise to pay — pause escalation",
        "out_of_office":        "Out of office — skip, retry later",
        "query":                "Query — route to human",
        "payment_confirmation": "Payment confirmation — verify & update",
        "unsubscribe":          "Unsubscribe — halt collection",
        "general":              "General reply — log only",
    }

    def _map_action(self, category: str) -> str:
        """Map a classification category to the action string used in the DB."""
        return self.CATEGORY_ACTION_MAP.get(category, "log_only")

    def _build_summary(self, category: str, classification: dict) -> str:
        """Build a one-line human-readable summary."""
        label = self.CATEGORY_HUMAN_LABELS.get(category, "Unknown")
        extra = classification.get("extracted_data", {})
        details = []

        if category == "dispute":
            reasons = extra.get("reasons", [])
            if reasons:
                details.append(", ".join(reasons[:2]))
        elif category == "promise_to_pay":
            dates = extra.get("promised_dates", [])
            if dates:
                details.append(f"Promised: {dates[0]}")
        elif category == "out_of_office":
            dates = extra.get("return_dates", [])
            if dates:
                details.append(f"Returns: {dates[0]}")
        elif category == "query":
            queries = extra.get("queries", [])
            if queries:
                details.append(", ".join(queries[:2]))

        if details:
            return f"{label}: {'; '.join(details)}"
        return label

    # ── Persistence ─────────────────────────────────────────────────

    def _persist_reply(
        self,
        debtor_id: str,
        subject: str,
        body: str,
        email_from: str,
        classification: dict,
        category: str,
        action: str,
        summary: str,
    ) -> str:
        """Store a classified reply in the debtor_replies table.

        Returns the reply_id.
        """
        from database import get_connection

        reply_id = f"rpy-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        confidence = classification.get("confidence", 0.0)
        extracted = classification.get("extracted_data", {})

        # Calculate retry date for OOO
        retry_after = ""
        if category == "out_of_office":
            return_dates = extracted.get("return_dates", [])
            if return_dates:
                retry_after = return_dates[0]

        # Extract matched keywords from extracted_data
        matched_keywords = []
        if category == "dispute":
            matched_keywords = extracted.get("reasons", [])
        elif category == "promise_to_pay":
            matched_keywords = extracted.get("promised_dates", [])
        elif category == "query":
            matched_keywords = extracted.get("queries", [])

        conn = get_connection()
        conn.execute(
            """INSERT INTO debtor_replies
               (id, debtor_id, email_from, subject, body, body_truncated,
                category, confidence, action_taken,
                matched_keywords, summary, ai_explanation,
                suggested_response, resolution, retry_after, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                reply_id,
                debtor_id,
                email_from,
                subject or "",
                (body or "")[:500],
                1 if len(body or "") > 500 else 0,
                category,
                confidence,
                action,
                json.dumps(matched_keywords),
                summary,
                json.dumps(extracted),  # Store full extracted data as ai_explanation
                "",
                "pending",
                retry_after,
                now,
            ),
        )
        conn.commit()
        return reply_id

    # ── Action Application ──────────────────────────────────────────

    def _apply_action(self, debtor_id: str, category: str, summary: str) -> str:
        """Update the debtor's state in the database based on the reply category.

        Returns a human-readable description of the action taken.
        """
        from database import get_connection

        conn = get_connection()

        if category == "dispute":
            conn.execute(
                """UPDATE debtors SET state = 'disputed',
                   dispute_reason = ?,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                   WHERE id = ?""",
                (summary[:200], debtor_id),
            )
            conn.commit()
            return f"Collection halted for {debtor_id}: dispute detected"

        if category == "promise_to_pay":
            conn.execute(
                """UPDATE debtors SET state = 'active',
                   updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                   WHERE id = ?""",
                (debtor_id,),
            )
            conn.commit()
            return f"Escalation paused for {debtor_id}: promise to pay"

        if category == "out_of_office":
            return f"Skipping {debtor_id}: out of office (will retry)"

        if category == "query":
            conn.execute(
                """UPDATE debtors SET state = 'manual_review',
                   updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                   WHERE id = ?""",
                (debtor_id,),
            )
            conn.commit()
            return f"Routed to human for {debtor_id}: query needs response"

        return f"Logged reply for {debtor_id}: no escalation action"

    # ── Public API ──────────────────────────────────────────────────

    def ingest_reply(
        self,
        debtor_id: str,
        subject: str,
        body: str,
        email_from: str = "",
    ) -> dict[str, Any]:
        """Full pipeline: classify → persist → action → return result.

        Returns a dict with the classification result, action taken, and reply_id.
        """
        # 1. Classify
        classification = self.classify(body)
        category = classification["category"]
        action = self._map_action(category)
        summary = self._build_summary(category, classification)

        # 2. Persist
        reply_id = self._persist_reply(
            debtor_id=debtor_id,
            subject=subject,
            body=body,
            email_from=email_from,
            classification=classification,
            category=category,
            action=action,
            summary=summary,
        )

        # 3. Apply action
        action_result = self._apply_action(debtor_id, category, summary)

        return {
            "reply_id": reply_id,
            "debtor_id": debtor_id,
            "category": category,
            "confidence": classification.get("confidence", 0.0),
            "action": action,
            "action_result": action_result,
            "summary": summary,
            "extracted_data": classification.get("extracted_data", {}),
        }

    def resolve_reply(self, reply_id: str, resolution: str, resolved_by: str = "") -> bool:
        """Mark a reply as resolved."""
        from database import get_connection

        conn = get_connection()
        cursor = conn.execute(
            """UPDATE debtor_replies
               SET resolution = ?, resolved_at = ?, resolved_by = ?
               WHERE id = ?""",
            (resolution, datetime.now(timezone.utc).isoformat(), resolved_by, reply_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    def get_replies(
        self,
        debtor_id: str | None = None,
        category: str | None = None,
        resolution: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query classified replies from the database."""
        from database import get_connection

        conn = get_connection()
        conditions = []
        params = []

        if debtor_id:
            conditions.append("debtor_id = ?")
            params.append(debtor_id)
        if category:
            conditions.append("category = ?")
            params.append(category)
        if resolution:
            conditions.append("resolution = ?")
            params.append(resolution)

        where = " AND ".join(conditions) if conditions else "1=1"
        rows = conn.execute(
            f"SELECT * FROM debtor_replies WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_pending_actions(self) -> list[dict[str, Any]]:
        """Get all unresolved replies, ordered by priority."""
        from database import get_connection

        conn = get_connection()
        rows = conn.execute(
            """SELECT r.*, d.name as debtor_name, d.invoice_number,
                      d.amount_cents, d.state as debtor_state
               FROM debtor_replies r
               JOIN debtors d ON d.id = r.debtor_id
               WHERE r.resolution = 'pending'
               ORDER BY
                   CASE r.category
                       WHEN 'dispute' THEN 0
                       WHEN 'query' THEN 1
                       WHEN 'unsubscribe' THEN 2
                       ELSE 3
                   END,
                   r.created_at DESC
               LIMIT 50"""
        ).fetchall()
        return [dict(r) for r in rows]

    def get_summary(self) -> dict[str, Any]:
        """Get summary stats about all classified replies."""
        from database import get_connection

        conn = get_connection()
        total = conn.execute("SELECT COUNT(*) FROM debtor_replies").fetchone()[0]

        by_category = {}
        rows = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM debtor_replies GROUP BY category"
        ).fetchall()
        for r in rows:
            by_category[r["category"]] = r["cnt"]

        pending = conn.execute(
            "SELECT COUNT(*) FROM debtor_replies WHERE resolution = 'pending'"
        ).fetchone()[0]

        disputes_pending = conn.execute(
            "SELECT COUNT(*) FROM debtor_replies WHERE category = 'dispute' AND resolution = 'pending'"
        ).fetchone()[0]

        avg_c = conn.execute(
            "SELECT COALESCE(AVG(confidence), 0) FROM debtor_replies"
        ).fetchone()[0]

        return {
            "total": total,
            "by_category": by_category,
            "pending_resolution": pending,
            "disputes_pending": disputes_pending,
            "avg_confidence": round(avg_c, 2),
        }

    def get_acknowledgment_template(self, category: str, debtor_name: str, business_name: str) -> str:
        """Get an acknowledgment email template for a reply category."""
        return self._classifier.acknowledgment_template(category, debtor_name, business_name)


# ── CLI Demo ────────────────────────────────────────────────────────

def main():
    """Run a demo of the reply pipeline with sample replies."""
    print(f"\n{'='*75}")
    print(f"  TETHER — REPLY PIPELINE DEMO")
    print(f"{'='*75}\n")

    pipeline = ReplyPipeline()

    samples = [
        ("d-004", "Re: Invoice INV-2026-045",
         "We returned the goods on May 20, 2026. This invoice is incorrect. Please review and cancel immediately.",
         "finance@delta.com"),
        ("d-001", "Re: Payment reminder",
         "Sorry for the delay. I will make the payment by this Friday. Please send bank details.",
         "ap@acme.com"),
        ("d-006", "Out of office",
         "Thank you for your email. I am out of the office until June 30 with limited access.",
         "billing@zeta.com"),
        ("d-005", "Re: Outstanding invoice",
         "Can you please send me a copy of the original invoice and a statement?",
         "accounts@epsilon.com"),
        ("d-002", "Re: Overdue notice",
         "Thanks for the reminder. We'll process this with our next payment run on Monday.",
         "billing@beta.com"),
    ]

    for debtor_id, subject, body, email_from in samples:
        # Classify only (dry run)
        klass = pipeline.classify(body)
        print(f"  [{debtor_id}] {subject}")
        print(f"         {klass['category']:<20}  {klass['confidence']:.0%}  —  {klass['reason']}")
        if klass.get("extracted_data"):
            ed = klass["extracted_data"]
            if ed:
                print(f"         Data: {json.dumps(ed, default=str)[:100]}")
        print()

    print(f"{'─'*75}")
    cats = {}
    for _, _, body, _ in samples:
        c = pipeline.classify(body)["category"]
        cats[c] = cats.get(c, 0) + 1
    print(f"  Categories:")
    for cat, count in sorted(cats.items()):
        print(f"    {cat:<20} {count}")
    print(f"{'─'*75}\n")


if __name__ == "__main__":
    main()
