"""Cash flow forecasting module for Tether - Australian debt collections system."""

from __future__ import annotations

import math
import statistics
from datetime import date, datetime, timedelta
from typing import Any

AU_INDUSTRY_DSO_DAYS = 44


class CashFlowForecaster:
    """Predicts when overdue invoices will be paid and projects cash flow."""

    OVERDUE_BANDS = [
        (1, 7, 0.70, 7),
        (8, 30, 0.40, 14),
        (31, 60, 0.20, 30),
        (61, 365 * 10, 0.10, 60),
    ]

    TIER_ADJUSTMENTS: dict[str, float] = {
        "standard": 0.10,
        "escalated": 0.0,
        "disputed": -0.25,
        "legal": -0.30,
    }

    PAYMENT_HISTORY_BONUS = 0.15
    MIN_PROBABILITY = 0.01
    MAX_PROBABILITY = 0.99

    def _today(self) -> date:
        return date.today()

    def _clamp_probability(self, p: float) -> float:
        return max(self.MIN_PROBABILITY, min(self.MAX_PROBABILITY, p))

    def _debtor_base_probability(self, debtor: dict[str, Any]) -> tuple[float, int]:
        days_overdue = debtor.get("days_overdue", 0)
        for low, high, prob, _window in self.OVERDUE_BANDS:
            if low <= days_overdue <= high:
                return prob, _window
        return 0.10, 60

    def _debtor_adjusted_probability(self, debtor: dict[str, Any]) -> tuple[float, str]:
        base, window = self._debtor_base_probability(debtor)

        tier = debtor.get("escalation_tier", "standard")
        adjustment = self.TIER_ADJUSTMENTS.get(tier, 0.0)

        payment_history = debtor.get("payment_history", [])
        if payment_history:
            paid_count = sum(1 for p in payment_history if p.get("status") == "paid")
            total_count = len(payment_history)
            paid_ratio = paid_count / total_count if total_count > 0 else 0
            history_adj = self.PAYMENT_HISTORY_BONUS * paid_ratio
            adjustment += history_adj

        status = debtor.get("status", "overdue")
        if status == "paid":
            return 1.0, "today"
        if status == "closed":
            return 0.0, "n/a"

        adjusted = self._clamp_probability(base + adjustment)

        if adjusted >= 0.65:
            confidence = "high"
        elif adjusted >= 0.35:
            confidence = "medium"
        else:
            confidence = "low"

        return adjusted, confidence

    def _predict_date(self, debtor: dict[str, Any], probability: float) -> date:
        today = self._today()
        days_overdue = debtor.get("days_overdue", 0)

        for low, high, _prob, window in self.OVERDUE_BANDS:
            if low <= days_overdue <= high:
                estimated_days = max(1, int(window * (1 - probability * 0.5)))
                return today + timedelta(days=estimated_days)

        return today + timedelta(days=60)

    def forecast(self, debtors: list[dict[str, Any]]) -> dict[str, Any]:
        """Predict expected payment date and probability for each debtor."""
        today = self._today()
        by_debtor: list[dict[str, Any]] = []
        probabilities: list[float] = []
        expected_amounts: list[int] = []

        for debtor in debtors:
            amount = debtor.get("amount_cents", 0)
            probability, confidence = self._debtor_adjusted_probability(debtor)

            if debtor.get("status") == "paid":
                predicted = today
            else:
                predicted = self._predict_date(debtor, probability)

            by_debtor.append({
                "name": debtor.get("name", "Unknown"),
                "amount_cents": amount,
                "predicted_date": predicted.isoformat(),
                "probability": round(probability, 4),
                "confidence": confidence if debtor.get("status") != "paid" else "high",
            })
            probabilities.append(probability)
            expected_amounts.append(int(amount * probability))

        overall_probability = (
            statistics.mean(probabilities) if probabilities else 0.0
        )
        total_expected = sum(expected_amounts)

        if overall_probability >= 0.60:
            overall_confidence = "high"
        elif overall_probability >= 0.35:
            overall_confidence = "medium"
        else:
            overall_confidence = "low"

        return {
            "forecast_date": today.isoformat(),
            "expected_amount_cents": total_expected,
            "probability": round(overall_probability, 4),
            "confidence": overall_confidence,
            "by_debtor": by_debtor,
        }

    def aggregate_forecast(
        self,
        debtors: list[dict[str, Any]],
        horizon_days: int = 90,
    ) -> dict[str, Any]:
        """Aggregate debtor forecasts into a cash flow projection grouped by week."""
        today = self._today()
        forecast_result = self.forecast(debtors)

        weekly: dict[str, dict[str, Any]] = {}
        total_probable_cents = 0
        at_risk_cents = 0

        for entry in forecast_result["by_debtor"]:
            predicted = date.fromisoformat(entry["predicted_date"])
            prob = entry["probability"]
            amount = entry["amount_cents"]

            if predicted > today + timedelta(days=horizon_days):
                week_key = (today + timedelta(days=horizon_days)).isoformat()
            else:
                days_from_monday = predicted.weekday()
                week_start = predicted - timedelta(days=days_from_monday)
                week_key = week_start.isoformat()

            if week_key not in weekly:
                weekly[week_key] = {
                    "expected_cents": 0,
                    "probable_cents": 0,
                    "debtor_count": 0,
                }

            weekly[week_key]["expected_cents"] += amount
            weekly[week_key]["probable_cents"] += int(amount * prob)
            weekly[week_key]["debtor_count"] += 1

            total_probable_cents += int(amount * prob)

            if prob < 0.50:
                at_risk_cents += amount

        sorted_weeks = sorted(weekly.items())
        weekly_breakdown = [
            {
                "week_start": wk,
                "expected_cents": v["expected_cents"],
                "probable_cents": v["probable_cents"],
                "debtor_count": v["debtor_count"],
            }
            for wk, v in sorted_weeks
        ]

        return {
            "total_expected_cents": forecast_result["expected_amount_cents"],
            "total_probable_cents": total_probable_cents,
            "weekly_breakdown": weekly_breakdown,
            "at_risk_cents": at_risk_cents,
            "forecast_date": today.isoformat(),
        }

    def calculate_dso(self, debtors: list[dict[str, Any]]) -> dict[str, Any]:
        """Calculate Days Sales Outstanding from debtor data."""
        today = self._today()
        overdue_amounts: list[float] = []
        overdue_days_list: list[float] = []

        for debtor in debtors:
            if debtor.get("status") == "paid":
                continue
            amount = debtor.get("amount_cents", 0)
            days = debtor.get("days_overdue", 0)
            overdue_amounts.append(amount)
            overdue_days_list.append(days)

        total_overdue = sum(overdue_amounts)

        if total_overdue == 0:
            return {
                "current_dso": 0.0,
                "trend": "stable",
                "comparison": f"vs industry avg {AU_INDUSTRY_DSO_DAYS} days (AU SMB)",
            }

        weighted_days = 0.0
        for amount, days in zip(overdue_amounts, overdue_days_list):
            weighted_days += (amount / total_overdue) * days

        current_dso = round(weighted_days, 1)

        if len(overdue_days_list) >= 3:
            first_half = overdue_days_list[: len(overdue_days_list) // 2]
            second_half = overdue_days_list[len(overdue_days_list) // 2 :]
            avg_first = statistics.mean(first_half)
            avg_second = statistics.mean(second_half)
            diff = avg_second - avg_first

            if diff < -3:
                trend = "improving"
            elif diff > 3:
                trend = "worsening"
            else:
                trend = "stable"
        else:
            if current_dso > AU_INDUSTRY_DSO_DAYS:
                trend = "worsening"
            elif current_dso < AU_INDUSTRY_DSO_DAYS:
                trend = "improving"
            else:
                trend = "stable"

        return {
            "current_dso": current_dso,
            "trend": trend,
            "comparison": f"vs industry avg {AU_INDUSTRY_DSO_DAYS} days (AU SMB)",
        }

    def what_if_scenario(
        self,
        debtors: list[dict[str, Any]],
        improvement: float,
    ) -> dict[str, Any]:
        """Show cash impact if collection probability improves by a percentage."""
        current = self.forecast(debtors)

        current_total_cents = 0
        improved_total_cents = 0
        total_days_weighted_current = 0.0
        total_days_weighted_improved = 0.0

        today = self._today()

        for entry in current["by_debtor"]:
            amount = entry["amount_cents"]
            base_prob = entry["probability"]
            predicted = date.fromisoformat(entry["predicted_date"])
            days_out = (predicted - today).days

            improved_prob = self._clamp_probability(base_prob + improvement)

            current_total_cents += int(amount * base_prob)
            improved_total_cents += int(amount * improved_prob)

            total_days_weighted_current += days_out * base_prob
            total_days_weighted_improved += days_out * improved_prob

        if current_total_cents > 0:
            avg_days_current = (
                total_days_weighted_current / (current_total_cents or 1)
            ) * current_total_cents / max(current_total_cents, 1)
            avg_days_improved = (
                total_days_weighted_improved / (improved_total_cents or 1)
            ) * improved_total_cents / max(improved_total_cents, 1)
            difference_days = round(avg_days_current - avg_days_improved, 1)
        else:
            difference_days = 0.0

        return {
            "current_forecast_cents": current_total_cents,
            "improved_forecast_cents": improved_total_cents,
            "difference_cents": improved_total_cents - current_total_cents,
            "difference_days": max(0.0, difference_days),
        }
