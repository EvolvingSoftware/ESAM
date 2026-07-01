#!/usr/bin/env python3
"""ES Policy Engine — define guardrails as code, evaluate at query time, enforce at runtime.

Supports:
- Pattern-based allow/deny/require_review rules
- Glob-style resource matching
- Conditional rules with JSON conditions
- Framework compliance mapping (EU AI Act, NIST AI RMF, ISO 42001)
- Per-agent and global policies
"""

from __future__ import annotations

import fnmatch
import json
import re
from typing import Any

from database import get_connection, transaction, new_id, utc_now


class PolicyEngine:
    """Define, store, and evaluate guardrail policies."""

    def create_policy(
        self,
        name: str,
        resource_pattern: str,
        policy_type: str = "allow",
        description: str = "",
        agent_id: str = "",
        conditions: dict | None = None,
        priority: int = 100,
        framework_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new policy rule."""
        policy_id = new_id("pol-")
        with transaction() as conn:
            conn.execute("""
                INSERT INTO policies (id, name, description, agent_id,
                    policy_type, resource_pattern, conditions, priority,
                    framework_refs)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                policy_id, name, description, agent_id,
                policy_type, resource_pattern,
                json.dumps(conditions or {}),
                priority,
                json.dumps(framework_refs or []),
            ))
        return self.get_policy(policy_id)

    def get_policy(self, policy_id: str) -> dict[str, Any] | None:
        conn = get_connection()
        row = conn.execute("SELECT * FROM policies WHERE id = ?", (policy_id,)).fetchone()
        return dict(row) if row else None

    def list_policies(self, agent_id: str | None = None, enabled_only: bool = True) -> list[dict[str, Any]]:
        """List policies, optionally filtered by agent (or global only)."""
        conn = get_connection()
        query = "SELECT * FROM policies WHERE 1=1"
        params = []
        if agent_id:
            query += " AND (agent_id = ? OR agent_id = '')"
            params.append(agent_id)
        if enabled_only:
            query += " AND enabled = 1"
        query += " ORDER BY priority ASC"
        return [dict(r) for r in conn.execute(query, params).fetchall()]

    def update_policy(self, policy_id: str, **fields) -> dict[str, Any]:
        """Update a policy's fields."""
        allowed = {"name", "description", "policy_type", "resource_pattern",
                    "conditions", "priority", "enabled", "framework_refs"}
        updates = []
        params = []
        for k, v in fields.items():
            if k in allowed:
                if isinstance(v, (list, dict)):
                    v = json.dumps(v)
                updates.append(f"{k} = ?")
                params.append(v)
        if not updates:
            return self.get_policy(policy_id)
        params.append(policy_id)
        with transaction() as conn:
            conn.execute(f"UPDATE policies SET {', '.join(updates)}, updated_at = ? WHERE id = ?",
                         params + [utc_now()])
        return self.get_policy(policy_id)

    def delete_policy(self, policy_id: str):
        """Delete a policy rule."""
        with transaction() as conn:
            conn.execute("DELETE FROM policies WHERE id = ?", (policy_id,))

    def evaluate(self, resource: str, agent_id: str = "", context: dict | None = None) -> dict[str, Any]:
        """Evaluate a resource access against all applicable policies.
        
        Returns:
            decision: allowed | denied | requires_review
            matched_policy: the policy that made the decision
            all_matches: list of all policies that matched
        """
        context = context or {}
        policies = self.list_policies(agent_id=agent_id)

        result = {
            "decision": "allowed",
            "matched_policy": None,
            "all_matches": [],
            "reason": "No matching policy — default allow",
        }

        for policy in policies:
            if not fnmatch.fnmatch(resource, policy["resource_pattern"]):
                continue

            # Check conditions
            conditions = json.loads(policy["conditions"] or "{}")
            if conditions:
                if not self._evaluate_conditions(conditions, context):
                    continue

            match = {
                "policy_id": policy["id"],
                "policy_name": policy["name"],
                "policy_type": policy["policy_type"],
                "resource_pattern": policy["resource_pattern"],
            }
            result["all_matches"].append(match)

            # Deny and require_review override allow
            if policy["policy_type"] == "deny":
                result["decision"] = "denied"
                result["matched_policy"] = match
                result["reason"] = f"Denied by policy: {policy['name']}"
                # Log evaluation
                self._log_evaluation(policy["id"], resource, "denied", match)
                return result

            if policy["policy_type"] == "require_review" and not result.get("matched_policy"):
                result["decision"] = "requires_review"
                result["matched_policy"] = match
                result["reason"] = f"Requires human review: {policy['name']}"
                self._log_evaluation(policy["id"], resource, "requires_review", match)

            # Allow — keep going (lower priority policies might deny)
            if policy["policy_type"] == "allow":
                result["decision"] = "allowed"
                result["matched_policy"] = match
                result["reason"] = f"Allowed by policy: {policy['name']}"
                self._log_evaluation(policy["id"], resource, "allowed", match)

        return result

    def _evaluate_conditions(self, conditions: dict, context: dict) -> bool:
        """Evaluate conditions against context. Simple key=value matching."""
        for key, expected in conditions.items():
            value = context.get(key)
            if isinstance(expected, (int, float)):
                if isinstance(value, (int, float)):
                    if value < expected:
                        return False
                else:
                    try:
                        if float(value) < expected:
                            return False
                    except (ValueError, TypeError):
                        return False
            elif value != expected:
                return False
        return True

    def _log_evaluation(self, policy_id: str, resource: str, decision: str, details: dict):
        """Record a policy evaluation in the database."""
        with transaction() as conn:
            conn.execute("""
                INSERT INTO policy_evaluations (id, policy_id, resource, decision, matched_conditions)
                VALUES (?, ?, ?, ?, ?)
            """, (new_id("peval-"), policy_id, resource, decision, json.dumps(details)))

    def get_evaluation_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get recent policy evaluations."""
        conn = get_connection()
        return [dict(r) for r in conn.execute(
            "SELECT * FROM policy_evaluations ORDER BY evaluated_at DESC LIMIT ?",
            (limit,)
        ).fetchall()]

    def create_default_tether_policies(self) -> list[dict[str, Any]]:
        """Create default guardrails for the Tether collections workflow."""
        policies = []

        policies.append(self.create_policy(
            name="Allow Stripe payment links",
            resource_pattern="stripe::payment_link::*",
            policy_type="allow",
            description="Allow creating and querying Stripe payment links",
            framework_refs=["eu_ai_act:low_risk", "nist_ai_rmf:map:3"],
        ))

        policies.append(self.create_policy(
            name="Allow local model inference",
            resource_pattern="model::gemma*",
            policy_type="allow",
            description="Allow local Gemma model inference for letter generation",
            framework_refs=["eu_ai_act:low_risk"],
        ))

        policies.append(self.create_policy(
            name="Deny external model calls over threshold",
            resource_pattern="model::external::*",
            policy_type="require_review",
            description="External model calls over $0.01 require human approval",
            conditions={"max_cost_usd": 0.01},
            priority=50,
            framework_refs=["eu_ai_act:high_risk:human_oversight"],
        ))

        policies.append(self.create_policy(
            name="Block data exfiltration",
            resource_pattern="network::outbound::*",
            policy_type="deny",
            description="Block all outbound network access except whitelisted endpoints",
            priority=10,
            framework_refs=["nist_ai_rmf:manage:5", "iso_42001:A.8.2"],
        ))

        policies.append(self.create_policy(
            name="Require review for disputes",
            resource_pattern="debtor::dispute::*",
            policy_type="require_review",
            description="Any dispute handling must be reviewed by a human",
            priority=30,
            framework_refs=["eu_ai_act:high_risk:human_oversight"],
        ))

        policies.append(self.create_policy(
            name="ACCC Late Fee Compliance",
            resource_pattern="debtor::late_fee::*",
            policy_type="allow",
            description="Allow ACCC-compliant late fee assessment and notices. Fees must be disclosed upfront, reasonable, applied consistently.",
            framework_refs=["accc:late_fees", "australian_consumer_law:unfair_contract_terms"],
        ))

        return policies


# ── CLI Demo ────────────────────────────────────────────────────────

def main():
    from database import init_db
    init_db()

    engine = PolicyEngine()

    print(f"\n{'='*60}")
    print(f"  ES POLICY ENGINE — DEMO")
    print(f"{'='*60}\n")

    # Create default policies
    engine.create_default_tether_policies()
    print("  Created 5 default policies for Tether workflow\n")

    # Evaluate some resources
    tests = [
        ("stripe::payment_link::create", {"amount_cents": 345000}),
        ("model::gemma-4-12b-it::inference", {}),
        ("model::external::gpt-4::inference", {}),
        ("network::outbound::api.evil.com", {}),
        ("debtor::dispute::file", {"reason": "Goods returned"}),
    ]

    print("  Policy evaluations:")
    for resource, context in tests:
        result = engine.evaluate(resource, agent_id="agent-tether", context=context)
        decision = result["decision"]
        icon = {"allowed": "✅", "denied": "❌", "requires_review": "🟡"}.get(decision, "⚪")
        print(f"    {icon} {resource:<50} {decision}")
    print()

    # Show evaluation history
    history = engine.get_evaluation_history()
    print(f"  Evaluation history: {len(history)} entries logged")
    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
