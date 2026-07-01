"""
Credit Limit Management Module for Tether - Australian Debt Collections System.

This module provides comprehensive credit limit management capabilities
for managing customer credit limits, approvals, and escalations.
"""

import datetime
import logging
from typing import Dict, List, Optional, Tuple, Any
import uuid


logger = logging.getLogger(__name__)


class CreditLimitError(Exception):
    """Base exception for credit limit operations."""
    pass


class CustomerNotFoundError(CreditLimitError):
    """Raised when a customer is not found."""
    pass


class CreditLimitExceededError(CreditLimitError):
    """Raised when credit limit is exceeded."""
    pass


class CreditSuspendedError(CreditLimitError):
    """Raised when credit is suspended."""
    pass


class CreditLimitManager:
    """
    Manages credit limits per customer for an Australian debt collections system.

    This class handles:
    - Setting and updating credit limits
    - Checking credit status and invoice approvals
    - Managing customer balances
    - Escalation of over-limit customers
    - Entity-level credit summaries

    Attributes:
        APPROACHING_LIMIT_THRESHOLD: Percentage threshold for approaching limit (default 80%)
        OVER_LIMIT_THRESHOLD: Percentage threshold for over limit (default 100%)
    """

    APPROACHING_LIMIT_THRESHOLD = 0.8
    OVER_LIMIT_THRESHOLD = 1.0

    def __init__(self):
        """Initialize CreditLimitManager with in-memory storage."""
        self._credit_records: Dict[str, dict] = {}
        self._balance_history: Dict[str, List[dict]] = {}

    def _generate_credit_limit_id(self) -> str:
        """Generate a unique credit limit ID with 'cl-' prefix."""
        return f"cl-{uuid.uuid4().hex[:12]}"

    def _get_current_timestamp(self) -> str:
        """Get current UTC timestamp in ISO format."""
        return datetime.datetime.utcnow().isoformat()

    def _validate_limit_cents(self, limit_cents: int) -> None:
        """Validate that limit_cents is a positive integer."""
        if not isinstance(limit_cents, int):
            raise ValueError("limit_cents must be an integer")
        if limit_cents < 0:
            raise ValueError("limit_cents must be non-negative")

    def _validate_terms_days(self, terms_days: int) -> None:
        """Validate that terms_days is a positive integer."""
        if not isinstance(terms_days, int):
            raise ValueError("terms_days must be an integer")
        if terms_days <= 0:
            raise ValueError("terms_days must be positive")

    def _validate_customer_id(self, customer_id: str) -> None:
        """Validate customer_id is not empty."""
        if not customer_id or not customer_id.strip():
            raise ValueError("customer_id cannot be empty")

    def _validate_entity_id(self, entity_id: str) -> None:
        """Validate entity_id is not empty."""
        if not entity_id or not entity_id.strip():
            raise ValueError("entity_id cannot be empty")

    def _calculate_usage_percent(self, current_balance_cents: int, limit_cents: int) -> float:
        """Calculate usage percentage."""
        if limit_cents <= 0:
            return 0.0
        return (current_balance_cents / limit_cents) * 100

    def _is_over_limit(self, current_balance_cents: int, limit_cents: int) -> bool:
        """Check if current balance exceeds credit limit."""
        return current_balance_cents > limit_cents

    def _is_approaching_limit(self, current_balance_cents: int, limit_cents: int) -> bool:
        """Check if current balance is approaching credit limit."""
        if limit_cents <= 0:
            return False
        return (current_balance_cents / limit_cents) > self.APPROACHING_LIMIT_THRESHOLD

    def set_credit_limit(
        self,
        customer_id: str,
        entity_id: str,
        limit_cents: int,
        terms_days: int = 30,
        notes: str = ""
    ) -> dict:
        """
        Set or update a customer's credit limit.

        Args:
            customer_id: Unique identifier for the customer
            entity_id: Unique identifier for the business entity
            limit_cents: Credit limit in cents (AUD)
            terms_days: Payment terms in days (default: 30)
            notes: Additional notes about the credit limit

        Returns:
            dict: Credit record containing:
                - credit_limit_id: Unique ID with 'cl-' prefix
                - customer_id: Customer identifier
                - entity_id: Entity identifier
                - limit_cents: Credit limit in cents
                - terms_days: Payment terms
                - current_balance_cents: Current balance (initially 0)
                - status: Credit status (active|suspended|closed)
                - notes: Additional notes
                - updated_at: Timestamp of last update
        """
        self._validate_customer_id(customer_id)
        self._validate_entity_id(entity_id)
        self._validate_limit_cents(limit_cents)
        self._validate_terms_days(terms_days)

        now = self._get_current_timestamp()

        if customer_id in self._credit_records:
            existing = self._credit_records[customer_id]
            existing["limit_cents"] = limit_cents
            existing["terms_days"] = terms_days
            existing["notes"] = notes
            existing["updated_at"] = now
            credit_record = existing
        else:
            credit_limit_id = self._generate_credit_limit_id()
            credit_record = {
                "credit_limit_id": credit_limit_id,
                "customer_id": customer_id,
                "entity_id": entity_id,
                "limit_cents": limit_cents,
                "terms_days": terms_days,
                "current_balance_cents": 0,
                "status": "active",
                "notes": notes,
                "updated_at": now
            }
            self._credit_records[customer_id] = credit_record
            self._balance_history[customer_id] = []

        logger.info(
            f"Credit limit set for customer {customer_id}: "
            f"${limit_cents / 100:.2f} with {terms_days} day terms"
        )

        return credit_record

    def get_credit_status(self, customer_id: str) -> dict:
        """
        Return credit status for a customer.

        Args:
            customer_id: Unique identifier for the customer

        Returns:
            dict: Credit status containing:
                - customer_id: Customer identifier
                - limit_cents: Credit limit in cents
                - current_balance_cents: Current balance in cents
                - available_cents: Available credit in cents
                - usage_percent: Usage percentage
                - status: Credit status
                - is_over_limit: Whether customer is over limit

        Raises:
            CustomerNotFoundError: If customer not found
        """
        self._validate_customer_id(customer_id)

        if customer_id not in self._credit_records:
            raise CustomerNotFoundError(f"Customer {customer_id} not found")

        customer_data = self._credit_records[customer_id]

        available_cents = customer_data["limit_cents"] - customer_data["current_balance_cents"]
        usage_percent = self._calculate_usage_percent(
            customer_data["current_balance_cents"],
            customer_data["limit_cents"]
        )
        is_over_limit = self._is_over_limit(
            customer_data["current_balance_cents"],
            customer_data["limit_cents"]
        )

        return {
            "customer_id": customer_id,
            "limit_cents": customer_data["limit_cents"],
            "current_balance_cents": customer_data["current_balance_cents"],
            "available_cents": available_cents,
            "usage_percent": usage_percent,
            "status": customer_data["status"],
            "is_over_limit": is_over_limit
        }

    def check_invoice_approval(
        self,
        customer_id: str,
        invoice_amount_cents: int
    ) -> dict:
        """
        Check if a new invoice can be issued against the credit limit.

        Args:
            customer_id: Unique identifier for the customer
            invoice_amount_cents: Invoice amount in cents (AUD)

        Returns:
            dict: Approval result containing:
                - approved: Whether invoice is approved
                - reason: Reason for approval/rejection
                - current_balance: Current balance before invoice
                - new_balance: Balance after invoice
                - limit: Credit limit
                - available: Available credit before invoice
                - would_exceed_by: Amount by which invoice would exceed limit
        """
        self._validate_customer_id(customer_id)

        if invoice_amount_cents < 0:
            raise ValueError("invoice_amount_cents must be non-negative")

        status = self.get_credit_status(customer_id)

        current_balance = status["current_balance_cents"]
        limit = status["limit_cents"]
        new_balance = current_balance + invoice_amount_cents
        available = limit - current_balance
        would_exceed_by = max(0, new_balance - limit)

        approved = (
            status["status"] == "active" and
            new_balance <= limit
        )

        reason = ""
        if status["status"] != "active":
            reason = f"Credit status is {status['status']}"
        elif new_balance > limit:
            reason = f"Invoice would exceed credit limit by ${would_exceed_by / 100:.2f}"
        else:
            reason = "Invoice approved within credit limit"

        logger.info(
            f"Invoice approval check for customer {customer_id}: "
            f"{'APPROVED' if approved else 'REJECTED'} - {reason}"
        )

        return {
            "approved": approved,
            "reason": reason,
            "current_balance": current_balance,
            "new_balance": new_balance,
            "limit": limit,
            "available": available,
            "would_exceed_by": would_exceed_by
        }

    def update_balance(self, customer_id: str, change_cents: int) -> dict:
        """
        Increase (new invoice) or decrease (payment received) the current balance.

        Args:
            customer_id: Unique identifier for the customer
            change_cents: Amount to change balance (positive for invoice, negative for payment)

        Returns:
            dict: Balance update result containing:
                - customer_id: Customer identifier
                - previous_balance: Balance before change
                - change_cents: Amount changed
                - new_balance: Balance after change
                - flags: List of flags (over_limit, approaching_limit)
                - updated_at: Timestamp of update
        """
        self._validate_customer_id(customer_id)

        if customer_id not in self._credit_records:
            raise CustomerNotFoundError(f"Customer {customer_id} not found")

        customer_data = self._credit_records[customer_id]
        current_balance = customer_data["current_balance_cents"]
        new_balance = current_balance + change_cents
        limit = customer_data["limit_cents"]

        customer_data["current_balance_cents"] = new_balance
        customer_data["updated_at"] = self._get_current_timestamp()

        self._balance_history[customer_id].append({
            "timestamp": self._get_current_timestamp(),
            "change_cents": change_cents,
            "balance_after": new_balance
        })

        flags = []
        if limit > 0:
            usage_percent = (new_balance / limit) * 100
            if usage_percent > self.OVER_LIMIT_THRESHOLD * 100:
                flags.append("over_limit")
                logger.warning(
                    f"Customer {customer_id} is now over limit: "
                    f"${new_balance / 100:.2f} / ${limit / 100:.2f}"
                )
            elif usage_percent > self.APPROACHING_LIMIT_THRESHOLD * 100:
                flags.append("approaching_limit")
                logger.info(
                    f"Customer {customer_id} is approaching limit: "
                    f"${new_balance / 100:.2f} / ${limit / 100:.2f}"
                )

        return {
            "customer_id": customer_id,
            "previous_balance": current_balance,
            "change_cents": change_cents,
            "new_balance": new_balance,
            "flags": flags,
            "updated_at": self._get_current_timestamp()
        }

    def list_customers_near_limit(
        self,
        entity_id: str = "",
        threshold: float = 0.8
    ) -> List[dict]:
        """
        List all customers whose balance exceeds threshold% of their credit limit.

        Args:
            entity_id: Filter by entity ID (optional, empty string for all)
            threshold: Usage threshold (0.0 to 1.0, default 0.8 for 80%)

        Returns:
            List[dict]: List of customer credit records near limit
        """
        if threshold < 0 or threshold > 1:
            raise ValueError("threshold must be between 0.0 and 1.0")

        results = []
        for customer_id, record in self._credit_records.items():
            if entity_id and record["entity_id"] != entity_id:
                continue
            if record["status"] != "active":
                continue
            if record["limit_cents"] <= 0:
                continue
            usage = record["current_balance_cents"] / record["limit_cents"]
            if usage > threshold:
                results.append({
                    "customer_id": customer_id,
                    "entity_id": record["entity_id"],
                    "limit_cents": record["limit_cents"],
                    "current_balance_cents": record["current_balance_cents"],
                    "usage_percent": usage * 100
                })

        logger.info(
            f"Listed {len(results)} customers near limit "
            f"(threshold: {threshold * 100:.0f}%)"
        )

        return results

    def list_over_limit_customers(self, entity_id: str = "") -> List[dict]:
        """
        List all customers currently over their credit limit.

        Args:
            entity_id: Filter by entity ID (optional, empty string for all)

        Returns:
            List[dict]: List of customer credit records over limit
        """
        results = []
        for customer_id, record in self._credit_records.items():
            if entity_id and record["entity_id"] != entity_id:
                continue
            if record["status"] != "active":
                continue
            if record["current_balance_cents"] > record["limit_cents"]:
                results.append({
                    "customer_id": customer_id,
                    "entity_id": record["entity_id"],
                    "limit_cents": record["limit_cents"],
                    "current_balance_cents": record["current_balance_cents"],
                    "exceeded_by_cents": record["current_balance_cents"] - record["limit_cents"]
                })

        logger.info(f"Listed {len(results)} customers over limit")

        return results

    def get_entity_credit_summary(self, entity_id: str) -> dict:
        """
        Return credit summary for an entity.

        Args:
            entity_id: Unique identifier for the business entity

        Returns:
            dict: Entity credit summary containing:
                - entity_id: Entity identifier
                - total_credit_extended_cents: Total credit extended in cents
                - total_balance_outstanding_cents: Total outstanding balance in cents
                - total_available_cents: Total available credit in cents
                - utilization_percent: Overall utilization percentage
                - customers_near_limit: Number of customers near limit
                - customers_over_limit: Number of customers over limit
                - total_exposure_at_risk: Total exposure at risk in cents
        """
        self._validate_entity_id(entity_id)

        total_credit_extended_cents = 0
        total_balance_outstanding_cents = 0
        customers_near_limit = 0
        customers_over_limit = 0

        for record in self._credit_records.values():
            if record["entity_id"] != entity_id:
                continue
            if record["status"] != "active":
                continue

            total_credit_extended_cents += record["limit_cents"]
            total_balance_outstanding_cents += record["current_balance_cents"]

            if record["limit_cents"] > 0:
                usage = record["current_balance_cents"] / record["limit_cents"]
                if usage > self.OVER_LIMIT_THRESHOLD:
                    customers_over_limit += 1
                elif usage > self.APPROACHING_LIMIT_THRESHOLD:
                    customers_near_limit += 1

        total_available_cents = total_credit_extended_cents - total_balance_outstanding_cents
        utilization_percent = self._calculate_usage_percent(
            total_balance_outstanding_cents,
            total_credit_extended_cents
        )

        summary = {
            "entity_id": entity_id,
            "total_credit_extended_cents": total_credit_extended_cents,
            "total_balance_outstanding_cents": total_balance_outstanding_cents,
            "total_available_cents": total_available_cents,
            "utilization_percent": utilization_percent,
            "customers_near_limit": customers_near_limit,
            "customers_over_limit": customers_over_limit,
            "total_exposure_at_risk": total_balance_outstanding_cents
        }

        logger.info(
            f"Entity credit summary for {entity_id}: "
            f"Extended: ${total_credit_extended_cents / 100:.2f}, "
            f"Outstanding: ${total_balance_outstanding_cents / 100:.2f}"
        )

        return summary

    def suspend_credit(self, customer_id: str, reason: str) -> dict:
        """
        Suspend a customer's credit (block new invoices).

        Args:
            customer_id: Unique identifier for the customer
            reason: Reason for suspension

        Returns:
            dict: Suspension result containing:
                - customer_id: Customer identifier
                - status: New status (suspended)
                - reason: Reason for suspension
                - suspended_at: Timestamp of suspension
                - notification_sent: Whether notification was sent
        """
        self._validate_customer_id(customer_id)

        if not reason or not reason.strip():
            raise ValueError("reason cannot be empty")

        if customer_id not in self._credit_records:
            raise CustomerNotFoundError(f"Customer {customer_id} not found")

        customer_data = self._credit_records[customer_id]
        customer_data["status"] = "suspended"
        customer_data["updated_at"] = self._get_current_timestamp()

        now = self._get_current_timestamp()
        notification_sent = True

        logger.warning(f"Credit suspended for customer {customer_id}: {reason}")

        return {
            "customer_id": customer_id,
            "status": "suspended",
            "reason": reason,
            "suspended_at": now,
            "notification_sent": notification_sent
        }

    def auto_escalate_over_limit(self, entity_id: str) -> List[dict]:
        """
        Find all over-limit customers and trigger escalation actions.

        For each over-limit customer, this method:
        1. Sends over-limit notice to customer
        2. Flags for business owner review
        3. Blocks further credit (suspends credit)

        Args:
            entity_id: Unique identifier for the business entity

        Returns:
            List[dict]: List of actions taken for each customer
        """
        self._validate_entity_id(entity_id)

        over_limit_customers = self.list_over_limit_customers(entity_id)
        actions_taken = []

        for customer in over_limit_customers:
            customer_id = customer["customer_id"]

            notice_action = {
                "action": "send_over_limit_notice",
                "customer_id": customer_id,
                "sent_at": self._get_current_timestamp(),
                "details": {
                    "notice_type": "over_limit_warning",
                    "method": "email",
                    "template": "credit_limit_exceeded"
                }
            }
            actions_taken.append(notice_action)

            flag_action = {
                "action": "flag_for_review",
                "customer_id": customer_id,
                "flagged_at": self._get_current_timestamp(),
                "details": {
                    "review_type": "over_limit",
                    "priority": "high",
                    "assigned_to": "business_owner"
                }
            }
            actions_taken.append(flag_action)

            suspension = self.suspend_credit(
                customer_id,
                "Auto-escalated due to over-limit status"
            )
            actions_taken.append({
                "action": "suspend_credit",
                "customer_id": customer_id,
                "details": suspension
            })

        logger.info(
            f"Auto-escalation completed for entity {entity_id}: "
            f"{len(over_limit_customers)} customers escalated"
        )

        return actions_taken

    def reactivate_credit(self, customer_id: str, reason: str = "") -> dict:
        """
        Reactivate a suspended customer's credit.

        Args:
            customer_id: Unique identifier for the customer
            reason: Reason for reactivation (optional)

        Returns:
            dict: Reactivation result
        """
        self._validate_customer_id(customer_id)

        if customer_id not in self._credit_records:
            raise CustomerNotFoundError(f"Customer {customer_id} not found")

        customer_data = self._credit_records[customer_id]
        customer_data["status"] = "active"
        customer_data["updated_at"] = self._get_current_timestamp()

        now = self._get_current_timestamp()

        logger.info(f"Credit reactivated for customer {customer_id}")

        return {
            "customer_id": customer_id,
            "status": "active",
            "reason": reason,
            "reactivated_at": now
        }

    def close_credit(self, customer_id: str, reason: str = "") -> dict:
        """
        Close a customer's credit account.

        Args:
            customer_id: Unique identifier for the customer
            reason: Reason for closing (optional)

        Returns:
            dict: Closure result
        """
        self._validate_customer_id(customer_id)

        if customer_id not in self._credit_records:
            raise CustomerNotFoundError(f"Customer {customer_id} not found")

        customer_data = self._credit_records[customer_id]
        customer_data["status"] = "closed"
        customer_data["updated_at"] = self._get_current_timestamp()

        now = self._get_current_timestamp()

        logger.info(f"Credit closed for customer {customer_id}")

        return {
            "customer_id": customer_id,
            "status": "closed",
            "reason": reason,
            "closed_at": now
        }

    def get_payment_terms(self, customer_id: str) -> dict:
        """
        Get payment terms for a customer.

        Args:
            customer_id: Unique identifier for the customer

        Returns:
            dict: Payment terms containing:
                - terms_days: Number of days for payment
                - due_date: Calculated due date (placeholder)
        """
        self._validate_customer_id(customer_id)

        if customer_id not in self._credit_records:
            raise CustomerNotFoundError(f"Customer {customer_id} not found")

        customer_data = self._credit_records[customer_id]
        terms_days = customer_data["terms_days"]

        return {
            "customer_id": customer_id,
            "terms_days": terms_days,
            "due_date": "placeholder_date"
        }

    def calculate_days_overdue(self, customer_id: str, invoice_date: str) -> int:
        """
        Calculate days an invoice is overdue.

        Args:
            customer_id: Unique identifier for the customer
            invoice_date: Date of invoice in ISO format

        Returns:
            int: Number of days overdue (0 if not overdue)
        """
        self._validate_customer_id(customer_id)

        terms = self.get_payment_terms(customer_id)
        terms_days = terms["terms_days"]

        invoice_dt = datetime.datetime.fromisoformat(invoice_date)
        due_date = invoice_dt + datetime.timedelta(days=terms_days)

        now = datetime.datetime.utcnow()
        if now > due_date:
            return (now - due_date).days
        return 0

    def get_balance_history(self, customer_id: str) -> List[dict]:
        """
        Get balance change history for a customer.

        Args:
            customer_id: Unique identifier for the customer

        Returns:
            List[dict]: List of balance changes with timestamps
        """
        self._validate_customer_id(customer_id)

        if customer_id not in self._balance_history:
            raise CustomerNotFoundError(f"Customer {customer_id} not found")

        return self._balance_history[customer_id]