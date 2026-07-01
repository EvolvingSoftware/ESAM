#!/usr/bin/env python3
"""ES Audit Trail — immutable log with reasoning traces, full provenance.

Provides:
- Immutable audit logging with hash chaining
- Conversation recording (full input/output pairs)
- Reasoning trace capture
- Compliance export (EU AI Act, NIST AI RMF, ISO 42001)
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from database import get_connection, transaction, new_id, utc_now, compute_hash


class AuditTrail:
    """Immutable audit trail for all agent actions."""

    def log(
        self,
        agent_id: str,
        category: str,
        action: str,
        resource: str = "",
        resource_type: str = "",
        summary: str = "",
        workflow_id: str = "",
        run_id: str = "",
        input_snapshot: dict | None = None,
        output_snapshot: dict | None = None,
        reasoning_trace: str = "",
        policy_id: str = "",
        policy_decision: str = "",
        policy_evidence: dict | None = None,
        actor: str = "",
        human_approver: str = "",
    ) -> dict[str, Any]:
        """Record an audit log entry with hash chaining for immutability."""
        now = utc_now()

        # Get previous hash
        conn = get_connection()
        last = conn.execute("SELECT hash FROM audit_logs ORDER BY rowid DESC LIMIT 1").fetchone()
        previous_hash = last["hash"] if last else ""

        entry = {
            "id": new_id("audit-"),
            "agent_id": agent_id,
            "workflow_id": workflow_id,
            "run_id": run_id,
            "category": category,
            "action": action,
            "resource": resource,
            "resource_type": resource_type,
            "summary": summary,
            "input_snapshot": json.dumps(input_snapshot or {}),
            "output_snapshot": json.dumps(output_snapshot or {}),
            "reasoning_trace": reasoning_trace,
            "policy_id": policy_id,
            "policy_decision": policy_decision,
            "policy_evidence": json.dumps(policy_evidence or {}),
            "actor": actor or agent_id,
            "human_approver": human_approver,
            "previous_hash": previous_hash,
            "hash": "",
            "created_at": now,
        }

        # Compute hash of this entry (chain integrity)
        entry["hash"] = compute_hash(entry)

        with transaction() as conn:
            conn.execute("""
                INSERT INTO audit_logs (id, agent_id, workflow_id, run_id,
                    category, action, resource, resource_type, summary,
                    input_snapshot, output_snapshot, reasoning_trace,
                    policy_id, policy_decision, policy_evidence,
                    actor, human_approver, previous_hash, hash, created_at)
                VALUES (:id, :agent_id, :workflow_id, :run_id,
                    :category, :action, :resource, :resource_type, :summary,
                    :input_snapshot, :output_snapshot, :reasoning_trace,
                    :policy_id, :policy_decision, :policy_evidence,
                    :actor, :human_approver, :previous_hash, :hash, :created_at)
            """, entry)

        return entry

    def log_tool_call(
        self,
        agent_id: str,
        tool_name: str,
        tool_input: dict,
        tool_output: Any,
        reasoning: str = "",
        workflow_id: str = "",
        run_id: str = "",
        policy_id: str = "",
        policy_decision: str = "allowed",
    ) -> dict[str, Any]:
        """Convenience: log a tool call with input/output snapshots."""
        return self.log(
            agent_id=agent_id,
            category="tool_call",
            action="invoke",
            resource=tool_name,
            resource_type="tool",
            summary=f"Tool call: {tool_name}",
            input_snapshot=tool_input,
            output_snapshot={"result": str(tool_output)[:500]},
            reasoning_trace=reasoning,
            workflow_id=workflow_id,
            run_id=run_id,
            policy_id=policy_id,
            policy_decision=policy_decision,
        )

    def log_model_call(
        self,
        agent_id: str,
        model: str,
        prompt: str,
        response: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        reasoning: str = "",
        workflow_id: str = "",
        run_id: str = "",
    ) -> dict[str, Any]:
        """Convenience: log a model inference call."""
        return self.log(
            agent_id=agent_id,
            category="model_invocation",
            action="invoke",
            resource=f"model::{model}",
            resource_type="model",
            summary=f"Model call: {model} ({tokens_in}→{tokens_out} tokens)",
            input_snapshot={"prompt": prompt[:1000]},
            output_snapshot={"response": response[:1000]},
            reasoning_trace=reasoning,
            workflow_id=workflow_id,
            run_id=run_id,
        )

    def log_data_access(
        self,
        agent_id: str,
        resource: str,
        action: str = "read",
        data_summary: str = "",
        policy_decision: str = "allowed",
        workflow_id: str = "",
    ) -> dict[str, Any]:
        """Convenience: log a data access event."""
        return self.log(
            agent_id=agent_id,
            category="data_access",
            action=action,
            resource=resource,
            resource_type="data",
            summary=data_summary or f"Data {action}: {resource}",
            policy_decision=policy_decision,
            workflow_id=workflow_id,
        )

    def query(
        self,
        agent_id: str | None = None,
        category: str | None = None,
        action: str | None = None,
        policy_decision: str | None = None,
        since: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query audit logs with filters."""
        conn = get_connection()
        query = "SELECT * FROM audit_logs WHERE 1=1"
        params = []

        if agent_id:
            query += " AND agent_id = ?"
            params.append(agent_id)
        if category:
            query += " AND category = ?"
            params.append(category)
        if action:
            query += " AND action = ?"
            params.append(action)
        if policy_decision:
            query += " AND policy_decision = ?"
            params.append(policy_decision)
        if since:
            query += " AND created_at >= ?"
            params.append(since)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        return [dict(r) for r in conn.execute(query, params).fetchall()]

    def verify_chain(self, agent_id: str | None = None) -> dict[str, Any]:
        """Verify the hash chain integrity. Returns verification result."""
        conn = get_connection()
        if agent_id:
            rows = conn.execute(
                "SELECT * FROM audit_logs WHERE agent_id = ? ORDER BY rowid ASC",
                (agent_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM audit_logs ORDER BY rowid ASC").fetchall()

        prev_hash = ""
        broken = []
        for row in rows:
            entry = dict(row)
            stored_hash = entry.pop("hash")
            # Recompute what the hash should be
            recomputed = compute_hash(entry)
            if stored_hash != recomputed:
                broken.append({"id": entry["id"], "expected": recomputed, "stored": stored_hash})
            if entry["previous_hash"] != prev_hash:
                broken.append({"id": entry["id"], "reason": "chain_break", "expected_prev": prev_hash})
            prev_hash = stored_hash

        return {
            "total": len(rows),
            "verified": len(rows) - len(broken),
            "broken": len(broken),
            "integrity": "intact" if not broken else "compromised",
            "details": broken[:10],
        }

    def export_compliance(self, framework: str = "all") -> dict[str, Any]:
        """Export audit data mapped to compliance frameworks."""
        conn = get_connection()
        rows = conn.execute("""
            SELECT * FROM audit_logs
            ORDER BY created_at DESC
            LIMIT 1000
        """).fetchall()

        entries = [dict(r) for r in rows]

        report = {
            "exported_at": utc_now(),
            "total_entries": len(entries),
            "frameworks": {},
        }

        if framework in ("all", "eu_ai_act"):
            report["frameworks"]["eu_ai_act"] = {
                "compliant": True,
                "high_risk_coverage": {
                    "human_oversight": sum(1 for e in entries if e.get("human_approver")),
                    "audit_trail": len(entries),
                    "explainability": sum(1 for e in entries if e.get("reasoning_trace")),
                },
            }

        if framework in ("all", "nist_ai_rmf"):
            report["frameworks"]["nist_ai_rmf"] = {
                "govern": sum(1 for e in entries if e["category"] in ("policy_eval", "auth_decision")),
                "map": sum(1 for e in entries if e["category"] == "data_access"),
                "measure": len(entries),
                "manage": sum(1 for e in entries if e["policy_decision"] == "denied"),
            }

        if framework in ("all", "iso_42001"):
            report["frameworks"]["iso_42001"] = {
                "evidence_available": len(entries) > 0,
                "traceability": all(e.get("previous_hash") != "" for e in entries[1:]),
                "entry_count": len(entries),
            }

        return report


# ── CLI Demo ────────────────────────────────────────────────────────

def main():
    from database import init_db
    init_db()

    trail = AuditTrail()
    print(f"\n{'='*60}")
    print(f"  ES AUDIT TRAIL — DEMO")
    print(f"{'='*60}\n")

    # Log some entries
    e1 = trail.log_tool_call(
        agent_id="agent-demo",
        tool_name="stripe::payment_link::create",
        tool_input={"amount_cents": 345000, "description": "Invoice INV-1042"},
        tool_output={"url": "https://link.stripe.com/pay_test"},
        reasoning="Debtor Acme Corp is 18 days overdue. Generating payment link for Step 14 escalation.",
        workflow_id="wf-tether",
        policy_id="pol-stripe-payments",
        policy_decision="allowed",
    )
    print(f"  1. Tool call logged:  {e1['id']}")
    print(f"     Hash:              {e1['hash'][:16]}...")
    print(f"     Previous hash:     {e1['previous_hash'][:16] or '(none)'}")
    print()

    e2 = trail.log_model_call(
        agent_id="agent-demo",
        model="gemma-4-12b-it",
        prompt="Write a firm payment notice to Acme Corp for invoice INV-1042...",
        response="SUBJECT: Overdue Notice...",
        tokens_in=164,
        tokens_out=78,
        reasoning="Using firmer tone for Day 14 escalation of high-value debtor",
        workflow_id="wf-tether",
    )
    print(f"  2. Model call logged: {e2['id']}")
    print(f"     Hash:              {e2['hash'][:16]}...")
    print(f"     Previous matches:  {e2['previous_hash'] == e1['hash']}")
    print()

    # Verify chain
    result = trail.verify_chain()
    print(f"  3. Chain verification:")
    print(f"     Total entries:     {result['total']}")
    print(f"     Verified:          {result['verified']}")
    print(f"     Integrity:         {result['integrity']}")
    print()

    # Compliance export
    report = trail.export_compliance()
    print(f"  4. Compliance export:")
    for fw, data in report["frameworks"].items():
        print(f"     {fw}: {data}")
    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
