#!/usr/bin/env python3
"""ABN/GST Verification Module — Australian Business Number lookup and validation.

Integrates with the Australian Business Register (ABR) web services:
  - ABN format validation (check-digit algorithm per ATO spec)
  - ABN Lookup via ABR JSON API (free, GUID-keyed)
  - GST registration status check
  - Business name, entity type, and address lookup
  - Results caching in the local database

API reference:
  https://abr.business.gov.au/json/AbnDetails.aspx?abn={ABN}&guid={GUID}

Demo mode: when ABR_GUID is not set, returns mock results for known ABNs
and a graceful fallback for unknown ones.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

from database import get_connection, new_id, utc_now

log = logging.getLogger("abn_verification")

# ── Config ───────────────────────────────────────────────────────────

# Free GUID from https://abr.business.gov.au/Tools/WebServicesRegistration
ABR_GUID = os.environ.get("ABR_GUID", "")

ABR_JSON_BASE = "https://abr.business.gov.au/json/"

# Demo ABNs mapped to known businesses (for demo mode without a GUID)
DEMO_ABNS: dict[str, dict[str, Any]] = {
    "74172177893": {
        "abn": "74 172 177 893",
        "entity_name": "Australian Taxation Office",
        "entity_type_code": "GOV",
        "entity_type_name": "Australian Government Entity",
        "abn_status": "Active",
        "abn_status_from": "2000-07-01",
        "gst_registered": True,
        "gst_from": "2000-07-01",
        "address": {
            "state": "ACT",
            "postcode": "2600",
            "suburb": "CANBERRA",
        },
        "main_business_name": "Australian Taxation Office",
        "business_names": [],
    },
    "16075067359": {
        "abn": "16 075 067 359",
        "entity_name": "Tether Tech Pty Ltd",
        "entity_type_code": "PRV",
        "entity_type_name": "Australian Private Company",
        "abn_status": "Active",
        "abn_status_from": "2020-03-15",
        "gst_registered": True,
        "gst_from": "2020-03-15",
        "address": {
            "state": "NSW",
            "postcode": "2000",
            "suburb": "SYDNEY",
        },
        "main_business_name": "",
        "business_names": ["Tether Collections"],
    },
    "28882582750": {
        "abn": "28 882 582 750",
        "entity_name": "Coastal Creative Studio",
        "entity_type_code": "PRV",
        "entity_type_name": "Australian Private Company",
        "abn_status": "Active",
        "abn_status_from": "2018-08-01",
        "gst_registered": True,
        "gst_from": "2018-08-01",
        "address": {
            "state": "VIC",
            "postcode": "3000",
            "suburb": "MELBOURNE",
        },
        "main_business_name": "Coastal Creative Studio",
        "business_names": [],
    },
    "39340026746": {
        "abn": "39 340 026 746",
        "entity_name": "Webb Landscaping",
        "entity_type_code": "SOL",
        "entity_type_name": "Sole Trader",
        "abn_status": "Active",
        "abn_status_from": "2019-11-20",
        "gst_registered": True,
        "gst_from": "2021-06-01",
        "address": {
            "state": "QLD",
            "postcode": "4000",
            "suburb": "BRISBANE",
        },
        "main_business_name": "Webb Landscaping",
        "business_names": [],
    },
    "51204161213": {
        "abn": "51 204 161 213",
        "entity_name": "Rivera's Bakery",
        "entity_type_code": "SOL",
        "entity_type_name": "Sole Trader",
        "abn_status": "Active",
        "abn_status_from": "2020-02-14",
        "gst_registered": False,
        "gst_from": "",
        "address": {
            "state": "NSW",
            "postcode": "2010",
            "suburb": "SURRY HILLS",
        },
        "main_business_name": "Rivera's Bakery",
        "business_names": [],
    },
    "62083802450": {
        "abn": "62 083 802 450",
        "entity_name": "Nair Dental Group",
        "entity_type_code": "PRV",
        "entity_type_name": "Australian Private Company",
        "abn_status": "Active",
        "abn_status_from": "2022-04-01",
        "gst_registered": True,
        "gst_from": "2022-04-01",
        "address": {
            "state": "VIC",
            "postcode": "3121",
            "suburb": "RICHMOND",
        },
        "main_business_name": "Nair Dental Group",
        "business_names": [],
    },
    "73228615104": {
        "abn": "73 228 615 104",
        "entity_name": "Acme Corp Australia",
        "entity_type_code": "PRV",
        "entity_type_name": "Australian Private Company",
        "abn_status": "Active",
        "abn_status_from": "2015-07-01",
        "gst_registered": True,
        "gst_from": "2015-07-01",
        "address": {
            "state": "NSW",
            "postcode": "2000",
            "suburb": "SYDNEY",
        },
        "main_business_name": "Acme Corp",
        "business_names": [],
    },
    "85404358189": {
        "abn": "85 404 358 189",
        "entity_name": "Beta Logistics Pty Ltd",
        "entity_type_code": "PRV",
        "entity_type_name": "Australian Private Company",
        "abn_status": "Active",
        "abn_status_from": "2017-03-10",
        "gst_registered": True,
        "gst_from": "2017-03-10",
        "address": {
            "state": "VIC",
            "postcode": "3000",
            "suburb": "MELBOURNE",
        },
        "main_business_name": "Beta Logistics",
        "business_names": [],
    },
    # Business with cancelled/wound-up status for demo
    "96656937866": {
        "abn": "96 656 937 866",
        "entity_name": "Defunct Pty Ltd",
        "entity_type_code": "PRV",
        "entity_type_name": "Australian Private Company",
        "abn_status": "Cancelled",
        "abn_status_from": "2005-01-01",
        "cancelled_from": "2024-12-01",
        "gst_registered": False,
        "gst_from": "",
        "address": {
            "state": "QLD",
            "postcode": "4000",
            "suburb": "BRISBANE",
        },
        "main_business_name": "",
        "business_names": [],
    },
}

# ── Data Models ──────────────────────────────────────────────────────


@dataclass
class ABNVerificationResult:
    """Result of an ABN/GST verification lookup."""

    abn: str
    abn_formatted: str = ""
    is_valid_format: bool = False
    entity_name: str = ""
    entity_type_code: str = ""
    entity_type_name: str = ""
    abn_status: str = ""
    abn_status_from: str = ""
    cancelled_from: str = ""
    is_gst_registered: bool = False
    gst_from: str = ""
    address_state: str = ""
    address_postcode: str = ""
    address_suburb: str = ""
    main_business_name: str = ""
    business_names: list[str] = field(default_factory=list)
    lookup_source: str = ""  # "abr_api" | "demo" | "cached" | "format_only"
    error_message: str = ""
    lookup_time: str = ""

    @property
    def is_active(self) -> bool:
        return self.abn_status in ("Active", "Active (current)")

    @property
    def is_gst_registered_prop(self) -> bool:
        return self.is_gst_registered

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def error(cls, abn: str, message: str) -> "ABNVerificationResult":
        return cls(abn=abn, error_message=message, lookup_source="error")

    @classmethod
    def from_abr_json(cls, abn: str, data: dict[str, Any]) -> "ABNVerificationResult":
        """Parse ABR JSON API response into our result model."""
        business_names = []
        main_name = ""
        # Get business names from the response
        bn_list = data.get("businessNames", data.get("business_names", []))
        if isinstance(bn_list, list):
            for bn in bn_list:
                if isinstance(bn, dict):
                    name_val = bn.get("businessName", bn.get("name", ""))
                    if name_val:
                        if not main_name:
                            main_name = name_val
                        business_names.append(name_val)

        address = data.get("address", data.get("mainAddressPhysical", {}))

        return cls(
            abn=abn,
            abn_formatted=data.get("abnFormatted", data.get("abn_formatted", "")),
            is_valid_format=True,
            entity_name=data.get("entityName", data.get("entity_name", "")),
            entity_type_code=data.get(
                "entityTypeCode",
                data.get("entity_type_code", ""),
            ),
            entity_type_name=data.get(
                "entityTypeName",
                data.get("entity_type_name", ""),
            ),
            abn_status=data.get("abnStatus", data.get("abn_status", "")),
            abn_status_from=data.get(
                "abnStatusEffectiveFrom",
                data.get("abn_status_from", ""),
            ),
            cancelled_from=data.get(
                "cancelledFrom",
                data.get("cancelled_from", ""),
            ),
            is_gst_registered=bool(
                data.get("gstRegistered", data.get("gst_registered", False))
            ),
            gst_from=data.get("gstFrom", data.get("gst_from", "")),
            address_state=address.get("state", address.get("state", "")),
            address_postcode=address.get("postcode", address.get("postcode", "")),
            address_suburb=address.get("suburb", address.get("suburb", "")),
            main_business_name=main_name or data.get("main_business_name", ""),
            business_names=business_names,
            lookup_source=data.get("lookup_source", "abr_api"),
            lookup_time=utc_now(),
        )


@dataclass
class BusinessVerificationReport:
    """Complete business verification report combining ABN + GST + PTRS."""

    abn: str
    abn_check: ABNVerificationResult
    gst_verified: bool = False
    credit_check_id: str = ""
    overall_risk: str = "unknown"  # low | medium | high | unknown
    risk_factors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    passed_checks: int = 0
    total_checks: int = 0
    verified_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["abn_check"] = self.abn_check.to_dict()
        return d


# ── ABN Format Validation ───────────────────────────────────────────

ABN_WEIGHTS = [10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19]


def validate_abn_format(abn: str) -> bool:
    """Validate an ABN using the official check-digit algorithm.

    Args:
        abn: 11-digit ABN string (with or without spaces)

    Returns:
        True if the ABN passes the modulus 89 check

    Algorithm:
        1. Strip all non-digit characters
        2. Subtract 1 from the first digit
        3. Multiply each of the 11 digits by its weighting factor
        4. Sum the products
        5. If sum % 89 == 0, the ABN is valid
    """
    digits = re.sub(r"\D", "", abn)
    if len(digits) != 11:
        return False

    try:
        nums = [int(d) for d in digits]
    except (ValueError, TypeError):
        return False

    # Subtract 1 from the first digit
    nums[0] -= 1

    # Apply weighting factors and sum
    total = sum(n * w for n, w in zip(nums, ABN_WEIGHTS))

    return total % 89 == 0


def format_abn(abn: str) -> str:
    """Format an ABN with spaces: XX XXX XXX XXX"""
    digits = re.sub(r"\D", "", abn)
    if len(digits) != 11:
        return abn
    return f"{digits[0:2]} {digits[2:5]} {digits[5:8]} {digits[8:11]}"


def parse_abn(abn: str) -> str:
    """Parse an ABN string to 11 digits."""
    return re.sub(r"\D", "", abn)


# ── ABR API Lookup ──────────────────────────────────────────────────


def lookup_abn(abn: str, force_live: bool = False) -> ABNVerificationResult:
    """Look up an ABN via the ABR JSON API or demo data.

    Args:
        abn: ABN to look up (11 digits, with or without formatting)
        force_live: If True, skip demo data and attempt live API

    Returns:
        ABNVerificationResult with details from the register
    """
    clean_abn = parse_abn(abn)

    if not validate_abn_format(clean_abn):
        return ABNVerificationResult.error(clean_abn, "Invalid ABN check-digit")

    # Check for cached result in DB first
    cached = _check_cache(clean_abn)
    if cached and not force_live:
        cached.lookup_source = "cached"
        return cached

    # Try demo data (when GUID not set)
    if not ABR_GUID and not force_live:
        mock = DEMO_ABNS.get(clean_abn)
        if mock:
            log.info(f"ABN lookup {clean_abn}: using demo data")
            result = ABNVerificationResult.from_abr_json(clean_abn, mock)
            result.lookup_source = "demo"
            _save_to_cache(clean_abn, result)
            return result
        return ABNVerificationResult.error(
            clean_abn,
            "No ABR API key configured (set ABR_GUID env var) "
            "and ABN not in demo data",
        )

    # Live ABR API call
    try:
        result = _abr_api_lookup(clean_abn)
        _save_to_cache(clean_abn, result)
        return result
    except Exception as e:
        log.warning(f"ABR API lookup failed for {clean_abn}: {e}")
        # Fallback: check demo data
        mock = DEMO_ABNS.get(clean_abn)
        if mock:
            result = ABNVerificationResult.from_abr_json(clean_abn, mock)
            result.lookup_source = "demo_fallback"
            return result
        return ABNVerificationResult.error(clean_abn, f"ABR API error: {e}")


def _abr_api_lookup(abn: str) -> ABNVerificationResult:
    """Call the live ABR JSON API."""
    url = f"{ABR_JSON_BASE}AbnDetails.aspx?abn={abn}&guid={ABR_GUID}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read().decode("utf-8")

    # The ABR JSON API returns JSONP — extract the JSON
    # Format: callback({...})
    if raw.startswith("callback("):
        raw = raw[len("callback(") : -1]

    data = json.loads(raw)
    result = ABNVerificationResult.from_abr_json(abn, data)
    result.lookup_source = "abr_api"
    return result


# ── Name Search ──────────────────────────────────────────────────────


def search_business_name(
    name: str, max_results: int = 10
) -> list[ABNVerificationResult]:
    """Search the ABR by business name.

    Args:
        name: Business name to search
        max_results: Maximum number of results to return

    Returns:
        List of matching ABNVerificationResult objects
    """
    # For demo, fuzzy-match against demo data
    if not ABR_GUID:
        results = []
        name_lower = name.lower().strip()
        for abn, data in DEMO_ABNS.items():
            if name_lower in data.get("entity_name", "").lower() or \
               name_lower in data.get("main_business_name", "").lower() or \
               any(name_lower in bn.lower() for bn in data.get("business_names", [])):
                results.append(
                    ABNVerificationResult.from_abr_json(abn, data)
                )
            if len(results) >= max_results:
                break
        return results

    # Live API call
    try:
        return _abr_name_search(name, max_results)
    except Exception as e:
        log.warning(f"ABR name search failed: {e}")
        return []


def _abr_name_search(name: str, max_results: int) -> list[ABNVerificationResult]:
    """Call ABR JSON name search API."""
    from urllib.parse import quote

    encoded = quote(name)
    url = (
        f"{ABR_JSON_BASE}MatchingNames.aspx"
        f"?name={encoded}&maxResults={max_results}&guid={ABR_GUID}"
    )
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read().decode("utf-8")

    if raw.startswith("callback("):
        raw = raw[len("callback(") : -1]

    data = json.loads(raw)
    names_list = data.get("names", data.get("matchingNames", []))
    return [
        ABNVerificationResult.from_abr_json(n.get("abn", ""), n)
        for n in names_list
    ]


# ── GST Verification ────────────────────────────────────────────────


def check_gst_registration(abn: str, force_live: bool = False) -> dict[str, Any]:
    """Check if a business is registered for GST.

    GST registration is looked up as part of ABN details
    (the ABN Lookup returns the GST registration date).

    Args:
        abn: ABN to check
        force_live: Bypass cache

    Returns:
        Dict with: is_registered, gst_from, abn, entity_name
    """
    result = lookup_abn(abn, force_live=force_live)
    if result.error_message:
        return {
            "is_registered": False,
            "gst_from": "",
            "abn": parse_abn(abn),
            "entity_name": "",
            "error": result.error_message,
        }
    return {
        "is_registered": result.is_gst_registered,
        "gst_from": result.gst_from,
        "abn": parse_abn(abn),
        "formatted_abn": result.abn_formatted,
        "entity_name": result.entity_name,
    }


# ── Caching ──────────────────────────────────────────────────────────


def _check_cache(abn: str) -> ABNVerificationResult | None:
    """Check for a cached ABN verification result."""
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM abn_verifications WHERE abn = ? ORDER BY verified_at DESC LIMIT 1",
            (abn,),
        ).fetchone()
        if row:
            data = json.loads(row["result_json"])
            result = ABNVerificationResult.from_abr_json(abn, data)
            result.lookup_source = "cached"
            return result
    except Exception:
        pass
    return None


def _save_to_cache(abn: str, result: ABNVerificationResult) -> None:
    """Cache an ABN verification result in the database."""
    try:
        conn = get_connection()
        conn.execute(
            """INSERT OR REPLACE INTO abn_verifications
               (id, abn, result_json, verified_at)
               VALUES (?, ?, ?, ?)""",
            (new_id(), abn, json.dumps(result.to_dict()), utc_now()),
        )
        conn.commit()
    except Exception as e:
        log.warning(f"Failed to cache ABN result: {e}")


# ── Business Verification Report ────────────────────────────────────


def verify_business(
    abn: str,
    business_name: str = "",
    require_gst: bool = False,
) -> BusinessVerificationReport:
    """Perform a full business verification.

    Checks:
    1. ABN format validity
    2. ABN is active and not cancelled
    3. Entity name matches (if business_name provided)
    4. GST registration status (if require_gst)

    Args:
        abn: ABN to verify
        business_name: Optional business name to match against
        require_gst: If True, fail if business is not GST-registered

    Returns:
        BusinessVerificationReport with all checks and overall risk assessment
    """
    abn_check = lookup_abn(abn)
    report = BusinessVerificationReport(
        abn=parse_abn(abn),
        abn_check=abn_check,
        verified_at=utc_now(),
    )

    checks = []
    risk_factors = []
    warnings = []

    # Check 1: ABN format
    if not abn_check.is_valid_format:
        checks.append(("abn_format", False, "ABN check-digit validation failed"))
        risk_factors.append("Invalid ABN format")
    else:
        checks.append(("abn_format", True, "ABN check-digit validation passed"))

    # Check 2: ABN active status
    if abn_check.error_message:
        checks.append(
            ("abn_status", False, f"Lookup error: {abn_check.error_message}")
        )
        risk_factors.append("ABN lookup failed")
    elif abn_check.is_active:
        checks.append(
            ("abn_status", True, f"ABN is {abn_check.abn_status} since {abn_check.abn_status_from}")
        )
    else:
        status = abn_check.abn_status
        cancelled_info = ""
        if abn_check.cancelled_from:
            cancelled_info = f" (cancelled {abn_check.cancelled_from})"
        checks.append(
            ("abn_status", False, f"ABN is {status}{cancelled_info}")
        )
        risk_factors.append(f"ABN is {status}")

    # Check 3: Business name match
    if business_name:
        name_lower = business_name.lower().strip()
        entity_lower = abn_check.entity_name.lower().strip()
        main_biz_lower = abn_check.main_business_name.lower().strip()
        all_names = [entity_lower, main_biz_lower] + [
            bn.lower() for bn in abn_check.business_names
        ]

        if any(name_lower == n for n in all_names):
            checks.append(
                ("name_match", True, f"Name '{business_name}' matches register")
            )
        elif any(name_lower in n for n in all_names):
            checks.append(
                ("name_match", True, f"Name matches partially: '{abn_check.entity_name}'")
            )
            warnings.append(f"Name '{business_name}' is a partial match for '{abn_check.entity_name}'")
            risk_factors.append("Partial business name match")
        else:
            checks.append(
                ("name_match", False, f"Name '{business_name}' does not match '{abn_check.entity_name}'")
            )
            risk_factors.append("Business name does not match ABN register")
    else:
        checks.append(("name_match", "skipped", "No business name provided for matching"))

    # Check 4: GST registration
    if require_gst and abn_check.is_gst_registered:
        checks.append(
            ("gst_registration", True, f"GST registered since {abn_check.gst_from}")
        )
    elif require_gst and not abn_check.is_gst_registered:
        checks.append(
            ("gst_registration", False, "Business is not GST registered")
        )
        risk_factors.append("Business not GST-registered")
    else:
        gst_status = "GST registered" if abn_check.is_gst_registered else "Not GST registered"
        checks.append(("gst_registration", True, gst_status))

    # Compile report
    report.passed_checks = sum(1 for _, ok, _ in checks if ok is True)
    report.total_checks = sum(1 for _, ok, _ in checks if ok is not None)
    report.risk_factors = risk_factors
    report.warnings = warnings

    # Overall risk
    if abn_check.error_message:
        report.overall_risk = "unknown"
    elif risk_factors:
        bad_checks = sum(1 for _, ok, _ in checks if ok is False)
        if bad_checks >= 2:
            report.overall_risk = "high"
        else:
            report.overall_risk = "medium"
    else:
        report.overall_risk = "low"

    # Save credit check record
    report.credit_check_id = _save_credit_check(report)

    return report


def _save_credit_check(report: BusinessVerificationReport) -> str:
    """Save a credit check record to the database."""
    try:
        conn = get_connection()
        check_id = new_id()
        conn.execute(
            """INSERT INTO credit_checks
               (id, abn, entity_name, overall_risk, risk_factors,
                warnings, passed_checks, total_checks, result_json, verified_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                check_id,
                report.abn,
                report.abn_check.entity_name,
                report.overall_risk,
                json.dumps(report.risk_factors),
                json.dumps(report.warnings),
                report.passed_checks,
                report.total_checks,
                json.dumps(report.to_dict()),
                report.verified_at,
            ),
        )
        conn.commit()
        return check_id
    except Exception as e:
        log.warning(f"Failed to save credit check: {e}")
        return ""


# ── CLI Demo ─────────────────────────────────────────────────────────


def demo():
    """Run the ABN verification demo."""
    print(f"\n{'='*75}")
    print(f"  ABN/GST VERIFICATION DEMO")
    print(f"  Evolving Software Agent Management")
    print(f"{'='*75}\n")

    test_abns = [
        ("16075067359", "Tether Tech Pty Ltd"),
        ("28882582750", "Coastal Creative Studio"),
        ("39340026746", "Webb Landscaping"),
        ("51204161213", "Rivera's Bakery"),
        ("96656937866", "Defunct Pty Ltd"),
        ("12345678901", "Nonexistent Co"),  # Invalid format
    ]

    for abn, name in test_abns:
        print(f"─" * 50)
        print(f"  Verifying: {abn} ({name})")
        print(f"  Format valid: {validate_abn_format(abn)}")

        if validate_abn_format(abn):
            result = lookup_abn(abn)
            print(f"  Source:      {result.lookup_source}")
            print(f"  Entity:      {result.entity_name}")
            print(f"  Type:        {result.entity_type_name} ({result.entity_type_code})")
            print(f"  Status:      {result.abn_status}")
            if result.is_gst_registered:
                print(f"  GST:         Registered since {result.gst_from}")
            else:
                print(f"  GST:         Not registered")
            if result.main_business_name:
                print(f"  Business:    {result.main_business_name}")
            if result.address_suburb:
                print(f"  Location:    {result.address_suburb}, {result.address_state} {result.address_postcode}")
            if result.error_message:
                print(f"  Error:       {result.error_message}")

        report = verify_business(abn, business_name=name, require_gst=True)
        print(f"  Risk:        {report.overall_risk.upper()}")
        print(f"  Passed:      {report.passed_checks}/{report.total_checks}")
        if report.warnings:
            for w in report.warnings:
                print(f"  Warning:     {w}")
        if report.risk_factors:
            for r in report.risk_factors:
                print(f"  Risk:        {r}")
        print()

    print(f"{'='*75}")
    print(f"  DEMO COMPLETE")
    print(f"{'='*75}\n")


if __name__ == "__main__":
    demo()
