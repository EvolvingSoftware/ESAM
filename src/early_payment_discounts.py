"""
Early Payment Discounts Module for Tether Debt Collections System.

Manages early payment discount offers for Australian debt collections,
including rule creation, applicability checks, incentive generation,
usage tracking, and analytics.
"""

from datetime import datetime, timedelta
from typing import Optional


class EarlyPaymentDiscount:
    """Manages early payment discount offers for Australian debt collections."""

    def __init__(self) -> None:
        self._discount_rules: dict[str, dict] = {}
        self._discount_usage: list[dict] = []
        self._discount_offered: list[dict] = []
        self._rules_counter: int = 0
        self._seed_default_rules()

    def _seed_default_rules(self) -> None:
        """Seed the system with default discount rules."""
        defaults = [
            {
                "name": "Early Bird",
                "discount_percent": 2.0,
                "min_days_early": 1,
                "max_days_early": 7,
                "description": "2% off if paid within 7 days of invoice date",
                "conditions": {},
            },
            {
                "name": "Prompt Payer",
                "discount_percent": 1.5,
                "min_days_early": 1,
                "max_days_early": 14,
                "description": "1.5% off if paid within 14 days",
                "conditions": {},
            },
            {
                "name": "Loyalty",
                "discount_percent": 1.0,
                "min_days_early": 1,
                "max_days_early": 30,
                "description": "1% off for repeat customers (paid 3+ invoices on time)",
                "conditions": {"min_on_time_payments": 3},
            },
        ]
        for rule_data in defaults:
            self._rules_counter += 1
            rule_id = f"RULE-{self._rules_counter:04d}"
            self._discount_rules[rule_id] = {
                "rule_id": rule_id,
                "name": rule_data["name"],
                "entity_id": "",
                "discount_percent": rule_data["discount_percent"],
                "min_days_early": rule_data["min_days_early"],
                "max_days_early": rule_data["max_days_early"],
                "active": True,
                "description": rule_data["description"],
                "conditions": rule_data["conditions"],
                "created_at": datetime.now().isoformat(),
            }

    def create_discount_rule(
        self,
        name: str,
        entity_id: str,
        discount_percent: float,
        min_days_early: int,
        max_days_early: int,
        active: bool = True,
    ) -> dict:
        """Create a new discount rule.

        Args:
            name: Name of the discount rule.
            entity_id: Entity identifier (empty string for global rules).
            discount_percent: Discount percentage (e.g. 2.0 for 2%).
            min_days_early: Minimum days before invoice due date for eligibility.
            max_days_early: Maximum days before invoice due date for eligibility.
            active: Whether the rule is active.

        Returns:
            The created discount rule dictionary.
        """
        self._rules_counter += 1
        rule_id = f"RULE-{self._rules_counter:04d}"

        rule = {
            "rule_id": rule_id,
            "name": name,
            "entity_id": entity_id,
            "discount_percent": discount_percent,
            "min_days_early": min_days_early,
            "max_days_early": max_days_early,
            "active": active,
            "description": f"{discount_percent}% off if paid within {max_days_early} days",
            "conditions": {},
            "created_at": datetime.now().isoformat(),
        }

        self._discount_rules[rule_id] = rule
        return rule

    def list_discount_rules(self, entity_id: str = "") -> list[dict]:
        """List all discount rules, optionally filtered by entity.

        Args:
            entity_id: If provided, filter rules to this entity (empty = all).

        Returns:
            List of discount rule dictionaries.
        """
        rules = list(self._discount_rules.values())
        if entity_id:
            rules = [r for r in rules if r["entity_id"] == entity_id or r["entity_id"] == ""]
        return rules

    def get_applicable_discount(
        self,
        invoice_amount_cents: int,
        invoice_date: str,
        debtor_history: list,
    ) -> dict:
        """Check if any discount rules apply to this invoice/debtor.

        Args:
            invoice_amount_cents: Invoice amount in AUD cents.
            invoice_date: Invoice date in YYYY-MM-DD format.
            debtor_history: List of past payment records for the debtor.
                Each record should have 'paid_on_time' (bool) and 'paid_date' (str).

        Returns:
            Dictionary with discount applicability details:
                - applies: bool
                - rule_name: str
                - discount_percent: float
                - savings_cents: int
                - deadline_date: str
                - discounted_amount_cents: int
        """
        result = {
            "applies": False,
            "rule_name": "",
            "discount_percent": 0.0,
            "savings_cents": 0,
            "deadline_date": "",
            "discounted_amount_cents": invoice_amount_cents,
        }

        invoice_dt = datetime.strptime(invoice_date, "%Y-%m-%d")
        today = datetime.now()
        days_since_invoice = (today - invoice_dt).days

        on_time_count = sum(
            1 for record in debtor_history if record.get("paid_on_time", False)
        )

        best_discount = 0.0
        best_rule_name = ""
        best_deadline = ""
        best_max_days = 0

        for rule in self._discount_rules.values():
            if not rule["active"]:
                continue

            if days_since_invoice < rule["min_days_early"]:
                continue

            if days_since_invoice > rule["max_days_early"]:
                continue

            conditions = rule.get("conditions", {})
            if "min_on_time_payments" in conditions:
                if on_time_count < conditions["min_on_time_payments"]:
                    continue

            if rule["discount_percent"] > best_discount:
                best_discount = rule["discount_percent"]
                best_rule_name = rule["name"]
                best_max_days = rule["max_days_early"]
                best_deadline = (invoice_dt + timedelta(days=best_max_days)).strftime(
                    "%Y-%m-%d"
                )

        if best_discount > 0:
            savings = int(invoice_amount_cents * best_discount / 100)
            result = {
                "applies": True,
                "rule_name": best_rule_name,
                "discount_percent": best_discount,
                "savings_cents": savings,
                "deadline_date": best_deadline,
                "discounted_amount_cents": invoice_amount_cents - savings,
            }

            self._discount_offered.append({
                "rule_name": best_rule_name,
                "invoice_amount_cents": invoice_amount_cents,
                "savings_cents": savings,
                "offered_at": datetime.now().isoformat(),
            })

        return result

    def generate_discount_incentive(
        self,
        debtor_name: str,
        invoice_number: str,
        amount_cents: int,
        discount: dict,
    ) -> str:
        """Generate email/SMS text offering the discount.

        Args:
            debtor_name: Name of the debtor.
            invoice_number: Invoice number identifier.
            amount_cents: Original invoice amount in AUD cents.
            discount: Discount dict from get_applicable_discount().

        Returns:
            Formatted incentive message string.
        """
        if not discount.get("applies", False):
            return ""

        savings_dollars = discount["savings_cents"] / 100
        discounted_amount = discount["discounted_amount_cents"] / 100
        days_remaining = ""

        if discount.get("deadline_date"):
            deadline = datetime.strptime(discount["deadline_date"], "%Y-%m-%d")
            today = datetime.now()
            days_left = (deadline - today).days
            if days_left > 0:
                days_remaining = f" within {days_left} days"

        message = (
            f"Hi {debtor_name},\n\n"
            f"Pay invoice #{invoice_number}{days_remaining} and save "
            f"${savings_dollars:.2f} ({discount['discount_percent']:.1f}% off)!\n\n"
            f"Original amount: ${amount_cents / 100:.2f}\n"
            f"Discounted amount: ${discounted_amount:.2f}\n"
            f"Deadline: {discount.get('deadline_date', 'N/A')}\n\n"
            f"Don't miss out on this opportunity to reduce your payment.\n\n"
            f"Kind regards,\nTether Debt Collections"
        )

        return message

    def track_discount_usage(
        self,
        rule_id: str,
        debtor_id: str,
        invoice_number: str,
        savings_cents: int,
    ) -> dict:
        """Log when a discount was accepted.

        Args:
            rule_id: The rule identifier.
            debtor_id: The debtor identifier.
            invoice_number: Invoice number.
            savings_cents: Amount saved in AUD cents.

        Returns:
            Dictionary with usage tracking details.
        """
        usage_record = {
            "rule_id": rule_id,
            "debtor_id": debtor_id,
            "invoice_number": invoice_number,
            "savings_cents": savings_cents,
            "accepted_at": datetime.now().isoformat(),
        }

        self._discount_usage.append(usage_record)
        return usage_record

    def get_discount_analytics(self, entity_id: str = "") -> dict:
        """Return analytics on discount usage.

        Args:
            entity_id: If provided, filter analytics to this entity.

        Returns:
            Dictionary with analytics:
                - total_discounts_offered: int
                - total_accepted: int
                - total_savings_cents: int
                - acceptance_rate: float
                - most_popular_rule: str
        """
        offered = len(self._discount_offered)
        accepted = len(self._discount_usage)
        total_savings = sum(u["savings_cents"] for u in self._discount_usage)
        acceptance_rate = (accepted / offered * 100) if offered > 0 else 0.0

        rule_counts: dict[str, int] = {}
        for usage in self._discount_usage:
            rule_name = usage.get("rule_id", "")
            for rule in self._discount_rules.values():
                if rule["rule_id"] == usage.get("rule_id"):
                    rule_name = rule["name"]
                    break
            rule_counts[rule_name] = rule_counts.get(rule_name, 0) + 1

        most_popular = max(rule_counts, key=rule_counts.get) if rule_counts else "N/A"

        savings_dollars = total_savings / 100
        summary = (
            f"Discounts offered: {offered}, "
            f"Accepted: {accepted} ({acceptance_rate:.0f}%), "
            f"Total savings: ${savings_dollars:.2f}, "
            f"Most popular: {most_popular}"
        )

        return {
            "total_discounts_offered": offered,
            "total_accepted": accepted,
            "total_savings_cents": total_savings,
            "acceptance_rate": round(acceptance_rate, 2),
            "most_popular_rule": most_popular,
            "summary": summary,
        }

    def is_discount_available(self, invoice_date: str, current_date: str) -> bool:
        """Check if a discount is still available based on days from invoice date.

        Args:
            invoice_date: Invoice date in YYYY-MM-DD format.
            current_date: Current date in YYYY-MM-DD format.

        Returns:
            True if any discount rule still applies, False otherwise.
        """
        invoice_dt = datetime.strptime(invoice_date, "%Y-%m-%d")
        current_dt = datetime.strptime(current_date, "%Y-%m-%d")
        days_since_invoice = (current_dt - invoice_dt).days

        if days_since_invoice < 0:
            return False

        for rule in self._discount_rules.values():
            if not rule["active"]:
                continue
            if rule["min_days_early"] <= days_since_invoice <= rule["max_days_early"]:
                return True

        return False
