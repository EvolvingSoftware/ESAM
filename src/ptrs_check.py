#!/usr/bin/env python3
"""PTRS Credit Checking Module — Payment Times Reports Register.

Integrates with the Australian Payment Times Reporting Scheme, where
large businesses (>$100M revenue) must publicly report how quickly they
pay small business suppliers.

Data model:
  - PTRSReport: A single filed payment times report (6-month period)
  - PTRSCreditCheck: Credit risk evaluation based on PTRS history
  - Payment behavior scoring and risk classification

For demo purposes, contains a curated dataset of known Australian businesses
with realistic PTRS data. In production, would scrape or integrate with
the register at https://register.paymenttimes.gov.au/

Reference:
  https://treasury.gov.au/small-business/PTRS
  https://register.paymenttimes.gov.au/
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

from database import get_connection, new_id, utc_now

log = logging.getLogger("ptrs_check")

# ── Config ───────────────────────────────────────────────────────────

# Enable PTRS web scraping (requires requests/html support)
# When False, uses demo data only
PTRS_SCRAPE_ENABLED = os.environ.get("PTRS_SCRAPE_ENABLED", "").lower() in (
    "1",
    "true",
    "yes",
)

# ── Data Models ──────────────────────────────────────────────────────


@dataclass
class PTRSReport:
    """A single Payment Times Report as filed on the register.

    Large entities report every 6 months: what their standard payment
    terms are and how quickly they actually pay small business suppliers.
    """

    # Identity
    reporting_entity_name: str
    abn: str = ""
    acn: str = ""

    # Reporting period
    period_start: str = ""  # "2025-01-01"
    period_end: str = ""  # "2025-06-30"
    report_filed_date: str = ""
    report_late: bool = False  # Filed after deadline

    # Payment terms (what they SAY)
    standard_terms_days: int = 30
    has_small_business_policy: bool = False
    small_business_policy_url: str = ""

    # Actual payment behaviour (what they DO)
    total_small_business_invoices: int = 0
    total_small_business_spend_cents: int = 0

    # Payment speed
    median_payment_days: float = 0.0  # Median days to pay small biz invoices
    mean_payment_days: float = 0.0
    percentage_paid_within_30_days: float = 0.0
    percentage_paid_late: float = 0.0

    # eInvoicing
    percentage_einvoiced: float = 0.0
    has_peppol: bool = False

    # Data source
    source: str = "demo"  # "demo" | "scraped" | "cached"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PTRSCreditScore:
    """Credit risk score derived from PTRS payment history."""

    # Business identity
    business_name: str
    abn: str = ""

    # PTRS data
    reports: list[PTRSReport] = field(default_factory=list)
    report_count: int = 0
    most_recent_report_date: str = ""

    # Credit score (0-100, higher = better payment behaviour)
    score: int = 50
    score_label: str = "Medium"  # Excellent | Good | Medium | Poor | Critical

    # Payment behaviour metrics
    median_payment_days_avg: float = 0.0
    late_payment_percent_avg: float = 0.0
    within_30_days_percent_avg: float = 0.0
    reports_on_time: bool = True

    # Risk assessment
    risk_level: str = "medium"  # low | medium | high | critical | unknown
    risk_factors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # Recommendation
    credit_recommendation: str = ""  # "offer_credit" | "review" | "require_deposit" | "prepay_only" | "no_data"
    recommended_credit_limit_cents: int = 0
    recommended_terms_days: int = 30

    # Metadata
    check_time: str = ""
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["reports"] = [r.to_dict() for r in self.reports]
        return d


# ── Demo PTRS Data ──────────────────────────────────────────────────

# Curated dataset of realistic PTRS reports for demo purposes
DEMO_PTRS_DATA: dict[str, list[dict[str, Any]]] = {
    "16075067359": [  # Tether Tech Pty Ltd
        {
            "reporting_entity_name": "Tether Tech Pty Ltd",
            "abn": "16 075 067 359",
            "period_start": "2025-01-01",
            "period_end": "2025-06-30",
            "report_filed_date": "2025-07-28",
            "report_late": False,
            "standard_terms_days": 14,
            "has_small_business_policy": True,
            "small_business_policy_url": "https://tethertech.com/supplier-terms",
            "total_small_business_invoices": 120,
            "total_small_business_spend_cents": 180000000,
            "median_payment_days": 10.0,
            "mean_payment_days": 11.5,
            "percentage_paid_within_30_days": 99.0,
            "percentage_paid_late": 1.0,
            "percentage_einvoiced": 75.0,
            "has_peppol": True,
        },
    ],
    "28882582750": [  # Coastal Creative Studio
        {
            "reporting_entity_name": "Coastal Creative Studio Pty Ltd",
            "abn": "28 882 582 750",
            "period_start": "2025-01-01",
            "period_end": "2025-06-30",
            "report_filed_date": "2025-08-28",
            "report_late": True,
            "standard_terms_days": 30,
            "has_small_business_policy": False,
            "total_small_business_invoices": 450,
            "total_small_business_spend_cents": 240000000,
            "median_payment_days": 22.0,
            "mean_payment_days": 25.3,
            "percentage_paid_within_30_days": 85.0,
            "percentage_paid_late": 15.0,
            "percentage_einvoiced": 30.0,
            "has_peppol": False,
        },
    ],
    "73228615104": [  # Acme Corp Australia
        {
            "reporting_entity_name": "Acme Corp Australia Pty Ltd",
            "abn": "73 228 615 104",
            "period_start": "2025-01-01",
            "period_end": "2025-06-30",
            "report_filed_date": "2025-08-01",
            "report_late": False,
            "standard_terms_days": 30,
            "has_small_business_policy": True,
            "small_business_policy_url": "https://acmecorp.com/suppliers",
            "total_small_business_invoices": 3400,
            "total_small_business_spend_cents": 4500000000,
            "median_payment_days": 28.0,
            "mean_payment_days": 31.2,
            "percentage_paid_within_30_days": 72.0,
            "percentage_paid_late": 28.0,
            "percentage_einvoiced": 45.0,
            "has_peppol": True,
        },
        {
            "reporting_entity_name": "Acme Corp Australia Pty Ltd",
            "abn": "73 228 615 104",
            "period_start": "2024-07-01",
            "period_end": "2024-12-31",
            "report_filed_date": "2025-02-15",
            "report_late": False,
            "standard_terms_days": 30,
            "has_small_business_policy": True,
            "total_small_business_invoices": 3200,
            "total_small_business_spend_cents": 4200000000,
            "median_payment_days": 32.0,
            "mean_payment_days": 35.0,
            "percentage_paid_within_30_days": 65.0,
            "percentage_paid_late": 35.0,
            "percentage_einvoiced": 40.0,
            "has_peppol": True,
        },
    ],
    "85404358189": [  # Beta Logistics Pty Ltd
        {
            "reporting_entity_name": "Beta Logistics Pty Ltd",
            "abn": "85 404 358 189",
            "period_start": "2025-01-01",
            "period_end": "2025-06-30",
            "report_filed_date": "2025-09-15",
            "report_late": True,
            "standard_terms_days": 45,
            "has_small_business_policy": False,
            "total_small_business_invoices": 890,
            "total_small_business_spend_cents": 1500000000,
            "median_payment_days": 45.0,
            "mean_payment_days": 52.0,
            "percentage_paid_within_30_days": 35.0,
            "percentage_paid_late": 65.0,
            "percentage_einvoiced": 15.0,
            "has_peppol": False,
        },
    ],
    "62083802450": [  # Nair Dental Group
        {
            "reporting_entity_name": "Nair Dental Group",
            "abn": "62 083 802 450",
            "period_start": "2025-01-01",
            "period_end": "2025-06-30",
            "report_filed_date": "2025-08-22",
            "report_late": True,
            "standard_terms_days": 30,
            "has_small_business_policy": False,
            "total_small_business_invoices": 280,
            "total_small_business_spend_cents": 520000000,
            "median_payment_days": 35.0,
            "mean_payment_days": 38.0,
            "percentage_paid_within_30_days": 55.0,
            "percentage_paid_late": 45.0,
            "percentage_einvoiced": 20.0,
            "has_peppol": False,
        },
    ],
    "39340026746": [  # Webb Landscaping (Sole Trader — PTRS exempt, but has voluntarily reported)
        {
            "reporting_entity_name": "Webb Landscaping",
            "abn": "39 340 026 746",
            "period_start": "2025-01-01",
            "period_end": "2025-06-30",
            "report_filed_date": "2025-07-15",
            "report_late": False,
            "standard_terms_days": 14,
            "has_small_business_policy": False,
            "total_small_business_invoices": 15,
            "total_small_business_spend_cents": 45000000,
            "median_payment_days": 7.0,
            "mean_payment_days": 8.2,
            "percentage_paid_within_30_days": 100.0,
            "percentage_paid_late": 0.0,
            "percentage_einvoiced": 10.0,
            "has_peppol": False,
        },
    ],
    "51204161213": [  # Rivera's Bakery (Sole Trader — exempt)
        # No reports — too small for PTRS threshold
    ],
}

# PTRS-exempt entities (small businesses not required to report)
PTRS_EXEMPT_NAMES: set[str] = {
    "Rivera's Bakery",
}


# ── PTRS Credit Checking ────────────────────────────────────────────


def check_ptrs(abn: str, business_name: str = "") -> PTRSCreditScore:
    """Check PTRS payment history for a business.

    Args:
        abn: ABN to check
        business_name: Optional business name for matching

    Returns:
        PTRSCreditScore with payment history and credit recommendation
    """
    clean_abn = re.sub(r"\D", "", abn)

    # Check cache first
    cached = _check_ptrs_cache(clean_abn)
    if cached:
        return cached

    # Get reports
    reports = _get_ptrs_reports(clean_abn, business_name)

    score = _calculate_credit_score(reports, business_name)

    # Cache result
    _save_ptrs_cache(clean_abn, score)

    return score


def _get_ptrs_reports(abn: str, business_name: str = "") -> list[PTRSReport]:
    """Retrieve PTRS reports for a business.

    Attempts: scraped register > demo data > empty result.
    """
    # Try demo data first
    clean_abn = re.sub(r"\D", "", abn)
    reports_data = DEMO_PTRS_DATA.get(clean_abn, [])

    if reports_data:
        return [PTRSReport(**r) for r in reports_data]

    # Try web scraping (if enabled)
    if PTRS_SCRAPE_ENABLED:
        try:
            scraped = _scrape_ptrs_register(clean_abn, business_name)
            if scraped:
                return scraped
        except Exception as e:
            log.warning(f"PTRS scrape failed for {abn}: {e}")

    return []


def _scrape_ptrs_register(abn: str, business_name: str) -> list[PTRSReport]:
    """Scrape the Payment Times Reports Register for a business.

    The register is at https://register.paymenttimes.gov.au/ and uses
    search-by-entity-name. This is a best-effort scrape.

    Note: The register requires JavaScript rendering, so this is
    a placeholder for a Playwright/Selenium-based scraper.
    """
    # Placeholder — would implement Playwright-based scrape here
    log.info(f"PTRS scraping requested for {abn}/{business_name} but not implemented")
    return []


# ── Credit Score Calculation ────────────────────────────────────────


def _calculate_credit_score(
    reports: list[PTRSReport],
    business_name: str = "",
) -> PTRSCreditScore:
    """Calculate a credit risk score from PTRS reports.

    Scoring model (0-100):
      - Payment speed (0-40 points): faster = better
      - Payment compliance (0-30 points): % paid within 30 days
      - Reporting compliance (0-15 points): filed on time
      - Policy & practices (0-15 points): has small biz policy, uses eInvoicing

    Risk levels:
      80-100: Low risk — excellent payer, offer credit
      60-79:  Medium-Low — good history, standard terms
      40-59:  Medium — mixed history, monitor closely
      20-39:  High — slow payer, require prepayment
      0-19:   Critical — systemic late payer, cash-only
    """
    name = business_name or (
        reports[0].reporting_entity_name if reports else "Unknown"
    )

    score = PTRSCreditScore(
        business_name=name,
        abn=reports[0].abn if reports else "",
        reports=reports,
        report_count=len(reports),
        check_time=utc_now(),
    )

    if not reports:
        entity_type = _infer_entity_type(name)
        score.source = "demo"
        score.risk_level = "unknown"

        if entity_type == "small_business":
            score.score_label = "Exempt"
            score.risk_factors.append(
                "Small business — PTRS-exempt (below $100M revenue threshold)"
            )
            score.credit_recommendation = "no_data"
            score.recommended_terms_days = 14
            score.recommended_credit_limit_cents = 5000000  # $5k default
            score.warnings.append(
                "No PTRS data available — small businesses aren't required to report"
            )
        else:
            score.risk_factors.append("No PTRS reports found for this entity")
            score.credit_recommendation = "no_data"
            score.recommended_terms_days = 14
            score.recommended_credit_limit_cents = 50000000  # $500k default
            score.warnings.append(
                "No PTRS payment history available — verify through alternative sources"
            )

        return score

    score.source = reports[0].source

    # Calculate aggregate metrics
    total_invoices = sum(r.total_small_business_invoices for r in reports)
    median_days_list = [r.median_payment_days for r in reports if r.median_payment_days > 0]
    late_pct_list = [r.percentage_paid_late for r in reports]
    within_30_list = [r.percentage_paid_within_30_days for r in reports]
    late_reports = [r for r in reports if r.report_late]

    score.median_payment_days_avg = (
        sum(median_days_list) / len(median_days_list) if median_days_list else 0
    )
    score.late_payment_percent_avg = (
        sum(late_pct_list) / len(late_pct_list) if late_pct_list else 0
    )
    score.within_30_days_percent_avg = (
        sum(within_30_list) / len(within_30_list) if within_30_list else 0
    )
    score.reports_on_time = len(late_reports) == 0
    score.most_recent_report_date = max(r.period_end for r in reports)

    # Calculate score
    points = 0

    # 1. Payment speed (0-40 points)
    med = score.median_payment_days_avg
    if med <= 7:
        points += 40
    elif med <= 14:
        points += 35
    elif med <= 21:
        points += 25
    elif med <= 30:
        points += 15
    elif med <= 45:
        points += 5
    # else 0 points

    # 2. Payment compliance (0-30 points)
    within_30 = score.within_30_days_percent_avg
    if within_30 >= 95:
        points += 30
    elif within_30 >= 85:
        points += 25
    elif within_30 >= 70:
        points += 15
    elif within_30 >= 50:
        points += 8
    elif within_30 >= 30:
        points += 3
    # else 0

    # 3. Reporting compliance (0-15 points)
    if score.reports_on_time:
        points += 15
    elif len(late_reports) < len(reports):
        points += 5
    # else 0

    # 4. Policy & practices (0-15 points)
    has_policy = any(r.has_small_business_policy for r in reports)
    uses_peppol = any(r.has_peppol for r in reports)
    avg_einvoice = sum(r.percentage_einvoiced for r in reports) / len(reports)
    std_terms = min(r.standard_terms_days for r in reports) if reports else 30

    if has_policy:
        points += 8
    if uses_peppol:
        points += 4
    if avg_einvoice >= 50:
        points += 3
    elif avg_einvoice >= 20:
        points += 2

    # Score
    score.score = min(100, max(0, points))

    # Label
    if score.score >= 80:
        score.score_label = "Excellent"
        score.risk_level = "low"
    elif score.score >= 60:
        score.score_label = "Good"
        score.risk_level = "low"
    elif score.score >= 40:
        score.score_label = "Medium"
        score.risk_level = "medium"
    elif score.score >= 20:
        score.score_label = "Poor"
        score.risk_level = "high"
    else:
        score.score_label = "Critical"
        score.risk_level = "critical"

    # Risk factors
    if score.late_payment_percent_avg > 40:
        score.risk_factors.append(
            f"Late payment rate: {score.late_payment_percent_avg:.0f}%"
        )
    if score.median_payment_days_avg > 30:
        score.risk_factors.append(
            f"Median payment time: {score.median_payment_days_avg:.0f} days"
        )
    if not score.reports_on_time:
        score.risk_factors.append(
            f"{len(late_reports)} report(s) filed late — compliance concern"
        )

    # Warnings
    if score.median_payment_days_avg > std_terms:
        score.warnings.append(
            f"Median actual payment ({score.median_payment_days_avg:.0f}d) exceeds "
            f"stated terms ({std_terms}d)"
        )

    # Credit recommendation
    if score.risk_level == "low" and score.score >= 80:
        score.credit_recommendation = "offer_credit"
        score.recommended_terms_days = max(14, std_terms)
        score.recommended_credit_limit_cents = _estimate_credit_limit(
            reports, score.risk_level
        )
    elif score.risk_level == "low":
        score.credit_recommendation = "offer_credit"
        score.recommended_terms_days = max(7, std_terms)
        score.recommended_credit_limit_cents = _estimate_credit_limit(
            reports, score.risk_level
        )
    elif score.risk_level == "medium":
        score.credit_recommendation = "review"
        score.recommended_terms_days = min(14, std_terms)
        score.recommended_credit_limit_cents = _estimate_credit_limit(
            reports, score.risk_level
        )
        score.warnings.append("Consider reducing credit limit and monitoring closely")
    elif score.risk_level == "high":
        score.credit_recommendation = "require_deposit"
        score.recommended_terms_days = 7
        score.recommended_credit_limit_cents = _estimate_credit_limit(
            reports, score.risk_level
        )
        score.warnings.append(
            "Require 50% deposit or personal guarantee before extending credit"
        )
    elif score.risk_level == "critical":
        score.credit_recommendation = "prepay_only"
        score.recommended_terms_days = 0
        score.recommended_credit_limit_cents = 0
        score.warnings.append("Cash-on-delivery or prepayment required only")

    return score


def _estimate_credit_limit(
    reports: list[PTRSReport], risk_level: str
) -> int:
    """Estimate a recommended credit limit based on PTRS data.

    Uses the business's total small business spend and risk level.
    """
    if not reports:
        return 5000000  # $5k default

    avg_spend = max(
        r.total_small_business_spend_cents for r in reports
    )

    multipliers = {
        "low": 0.05,  # 5% of quarterly spend
        "medium": 0.025,
        "high": 0.01,
        "critical": 0,
    }

    return int(avg_spend * multipliers.get(risk_level, 0.025))


def _infer_entity_type(business_name: str) -> str:
    """Infer whether a business is likely a small business (PTRS-exempt)."""
    name_lower = business_name.lower().strip()
    if name_lower in PTRS_EXEMPT_NAMES or any(
        name_lower == e.lower() for e in PTRS_EXEMPT_NAMES
    ):
        return "small_business"
    # Heuristic: businesses known to be small
    return "unknown"


# ── Caching ──────────────────────────────────────────────────────────


def _check_ptrs_cache(abn: str) -> PTRSCreditScore | None:
    """Check for cached PTRS credit score."""
    try:
        conn = get_connection()
        row = conn.execute(
            """SELECT * FROM ptrs_checks WHERE abn = ?
               ORDER BY check_time DESC LIMIT 1""",
            (abn,),
        ).fetchone()
        if row:
            score = PTRSCreditScore(
                business_name=row["business_name"],
                abn=row["abn"],
                score=row["score"],
                score_label=row["score_label"],
                risk_level=row["risk_level"],
                risk_factors=json.loads(row["risk_factors"]),
                warnings=json.loads(row["warnings"]),
                credit_recommendation=row["credit_recommendation"],
                recommended_credit_limit_cents=row["recommended_limit_cents"],
                recommended_terms_days=row["recommended_terms_days"],
                check_time=row["check_time"],
                source="cached",
            )
            return score
    except Exception:
        pass
    return None


def _save_ptrs_cache(abn: str, score: PTRSCreditScore) -> None:
    """Cache a PTRS credit score in the database."""
    try:
        conn = get_connection()
        conn.execute(
            """INSERT OR REPLACE INTO ptrs_checks
               (id, abn, business_name, score, score_label, risk_level,
                risk_factors, warnings, credit_recommendation,
                recommended_limit_cents, recommended_terms_days,
                result_json, check_time)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id(),
                score.abn or abn,
                score.business_name,
                score.score,
                score.score_label,
                score.risk_level,
                json.dumps(score.risk_factors),
                json.dumps(score.warnings),
                score.credit_recommendation,
                score.recommended_credit_limit_cents,
                score.recommended_terms_days,
                json.dumps(score.to_dict()),
                score.check_time,
            ),
        )
        conn.commit()
    except Exception as e:
        log.warning(f"Failed to cache PTRS result: {e}")


# ── CLI Demo ─────────────────────────────────────────────────────────


def demo():
    """Run the PTRS credit checking demo."""
    print(f"\n{'='*75}")
    print(f"  PTRS CREDIT CHECKING DEMO")
    print(f"  Payment Times Reports Register — Credit Risk Scoring")
    print(f"  Evolving Software Agent Management")
    print(f"{'='*75}\n")

    test_cases = [
        ("16075067359", "Tether Tech Pty Ltd"),
        ("73228615104", "Acme Corp Australia Pty Ltd"),
        ("85404358189", "Beta Logistics Pty Ltd"),
        ("62083802450", "Nair Dental Group"),
        ("39340026746", "Webb Landscaping"),
        ("51204161213", "Rivera's Bakery"),
        ("00000000000", "Unknown Business"),  # No data
    ]

    for abn, name in test_cases:
        print(f"─" * 50)
        print(f"  Entity:          {name}")
        print(f"  ABN:             {abn}")

        score = check_ptrs(abn, name)
        print(f"  Source:          {score.source}")
        print(f"  Reports:         {score.report_count}")
        print(f"  Credit Score:    {score.score}/100  ({score.score_label})")
        print(f"  Risk Level:      {score.risk_level.upper()}")
        print(f"  Recommendation:  {score.credit_recommendation}")

        if score.median_payment_days_avg > 0:
            print(f"  Avg Payment:    {score.median_payment_days_avg:.1f} days (median)")
        if score.late_payment_percent_avg > 0:
            print(f"  Late Rate:       {score.late_payment_percent_avg:.0f}%")
        if score.recommended_credit_limit_cents > 0:
            print(f"  Credit Limit:    ${score.recommended_credit_limit_cents/100:,.2f}")
        if score.recommended_terms_days > 0:
            print(f"  Terms:           {score.recommended_terms_days} days")

        for w in score.warnings:
            print(f"  ⚠  {w}")
        for r in score.risk_factors:
            print(f"  ⚡ {r}")

        print()

    print(f"{'='*75}")
    print(f"  DEMO COMPLETE")
    print(f"{'='*75}\n")


if __name__ == "__main__":
    demo()
