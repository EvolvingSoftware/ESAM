"""
Legal Escalation Pathway Module for Tether - Australian Debt Collection SaaS.

Documents the legal escalation pathway when standard collection fails.
"""

from datetime import datetime, timedelta
from typing import Any


class LegalEscalation:
    """Manages legal escalation pathways for Australian debt collection."""

    JURISDICTION_TRIBUNALS: dict[str, dict[str, Any]] = {
        "NSW": {
            "name": "NSW Civil and Administrative Tribunal (NCAT)",
            "abbreviation": "NCAT",
            "jurisdiction_amount_max": 2000000,
            "filing_fee_cents": 10200,
            "website": "https://www.ncat.nsw.gov.au",
            "process_description": (
                "Application filed online or in person. Hearing scheduled within 28-90 days. "
                "Parties may attempt conciliation before hearing. Decision issued within "
                "28 days of hearing."
            ),
        },
        "VIC": {
            "name": "Victorian Civil and Administrative Tribunal (VCAT)",
            "abbreviation": "VCAT",
            "jurisdiction_amount_max": 1500000,
            "filing_fee_cents": 6500,
            "website": "https://www.vcat.vic.gov.au",
            "process_description": (
                "Application lodged via VCAT's online portal. Directions hearing held within "
                "30 days. Main hearing listed 60-90 days from filing. Orders issued promptly."
            ),
        },
        "QLD": {
            "name": "Queensland Civil and Administrative Tribunal (QCAT)",
            "abbreviation": "QCAT",
            "jurisdiction_amount_max": 2500000,
            "filing_fee_cents": 5300,
            "website": "https://www.qcat.qld.gov.au",
            "process_description": (
                "Application submitted online. Mediation or conference scheduled first. "
                "If unresolved, hearing listed within 60-120 days. Decision provided "
                "within 30 days of hearing."
            ),
        },
        "WA": {
            "name": "State Administrative Tribunal (SAT)",
            "abbreviation": "SAT",
            "jurisdiction_amount_max": 5000000,
            "filing_fee_cents": 10000,
            "website": "https://www.sat.wa.gov.au",
            "process_description": (
                "Application lodged with the Registrar. Directions hearing within 28 days. "
                "Main hearing typically listed within 90-180 days. Written reasons provided."
            ),
        },
        "SA": {
            "name": "South Australian Civil and Administrative Tribunal (SACAT)",
            "abbreviation": "SACAT",
            "jurisdiction_amount_max": 1000000,
            "filing_fee_cents": 3800,
            "website": "https://www.sacat.sa.gov.au",
            "process_description": (
                "Application filed online or at registry. Conciliation attempted first. "
                "If unresolved, hearing within 45-90 days. Orders made at hearing or "
                "within 14 days."
            ),
        },
        "TAS": {
            "name": "Magistrates Court Civil Division (MAG)",
            "abbreviation": "MAG",
            "jurisdiction_amount_max": 1000000,
            "filing_fee_cents": 3000,
            "website": "https://www.magistratescourt.tas.gov.au",
            "process_description": (
                "Claim filed at Magistrates Court. Mediation may be ordered. Hearing "
                "scheduled within 60-90 days. Judgment entered within 28 days."
            ),
        },
        "ACT": {
            "name": "ACT Civil and Administrative Tribunal (ACAT)",
            "abbreviation": "ACAT",
            "jurisdiction_amount_max": 2500000,
            "filing_fee_cents": 6000,
            "website": "https://www.acat.act.gov.au",
            "process_description": (
                "Application lodged online or at the Tribunal. Direction hearing within "
                "28 days. Final hearing 60-120 days from filing. Decision within "
                "30 days of hearing."
            ),
        },
        "NT": {
            "name": "Northern Territory Civil and Administrative Tribunal (NTCAT)",
            "abbreviation": "NTCAT",
            "jurisdiction_amount_max": 500000,
            "filing_fee_cents": 4000,
            "website": "https://www.ntcat.nt.gov.au",
            "process_description": (
                "Application filed at NTCAT registry. Mediation offered. Hearing "
                "listed within 60-120 days. Orders issued within 28 days of hearing."
            ),
        },
    }

    JURISDICTION_COURTS: dict[str, dict[str, Any]] = {
        "NSW": {
            "local": {
                "name": "NSW Local Court",
                "jurisdiction_amount_range": "Up to $100,000",
                "min_amount_cents": 0,
                "max_amount_cents": 10000000,
                "filing_fees": {
                    "up_to_10k": 6900,
                    "10k_to_25k": 9400,
                    "25k_to_50k": 11900,
                    "50k_to_100k": 14400,
                },
                "typical_timeframe": "3-6 months",
                "enforcement_options": [
                    "Warrant of enforcement",
                    "Garnishee order",
                    "Examination notice",
                    "Writ of execution",
                    "Charging order on property",
                ],
            },
            "district": {
                "name": "NSW District Court",
                "jurisdiction_amount_range": "$100,000 - $750,000",
                "min_amount_cents": 10000000,
                "max_amount_cents": 75000000,
                "filing_fees": {
                    "standard": 22000,
                },
                "typical_timeframe": "6-18 months",
                "enforcement_options": [
                    "Writ of possession",
                    "Garnishee order",
                    "Charging order",
                    "Appointment of receiver",
                    "Examination of judgment debtor",
                ],
            },
            "supreme": {
                "name": "NSW Supreme Court",
                "jurisdiction_amount_range": "$750,000+",
                "min_amount_cents": 75000000,
                "max_amount_cents": None,
                "filing_fees": {
                    "standard": 33000,
                },
                "typical_timeframe": "12-36 months",
                "enforcement_options": [
                    "Writ of possession",
                    "Garnishee order",
                    "Charging order",
                    "Appointment of receiver",
                    "Bankruptcy notice (personal debtors)",
                    "Winding up notice (corporate debtors)",
                ],
            },
        },
        "VIC": {
            "local": {
                "name": "Magistrates' Court of Victoria",
                "jurisdiction_amount_range": "Up to $100,000",
                "min_amount_cents": 0,
                "max_amount_cents": 10000000,
                "filing_fees": {
                    "up_to_10k": 6500,
                    "10k_to_25k": 9000,
                    "25k_to_50k": 11500,
                    "50k_to_100k": 14000,
                },
                "typical_timeframe": "3-6 months",
                "enforcement_options": [
                    "Warrant of possession",
                    "Garnishee order",
                    "Examination order",
                    "Warrant to seize property",
                    "Charging order",
                ],
            },
            "county": {
                "name": "County Court of Victoria",
                "jurisdiction_amount_range": "$100,000 - $500,000",
                "min_amount_cents": 10000000,
                "max_amount_cents": 50000000,
                "filing_fees": {
                    "standard": 20000,
                },
                "typical_timeframe": "6-18 months",
                "enforcement_options": [
                    "Writ of possession",
                    "Garnishee order",
                    "Charging order",
                    "Appointment of receiver",
                    "Examination of judgment debtor",
                ],
            },
            "supreme": {
                "name": "Supreme Court of Victoria",
                "jurisdiction_amount_range": "$500,000+",
                "min_amount_cents": 50000000,
                "max_amount_cents": None,
                "filing_fees": {
                    "standard": 30000,
                },
                "typical_timeframe": "12-36 months",
                "enforcement_options": [
                    "Writ of possession",
                    "Garnishee order",
                    "Charging order",
                    "Appointment of receiver",
                    "Bankruptcy notice (personal debtors)",
                    "Winding up notice (corporate debtors)",
                ],
            },
        },
        "QLD": {
            "local": {
                "name": "Magistrates Court of Queensland",
                "jurisdiction_amount_range": "Up to $100,000",
                "min_amount_cents": 0,
                "max_amount_cents": 10000000,
                "filing_fees": {
                    "up_to_10k": 5800,
                    "10k_to_25k": 8200,
                    "25k_to_50k": 10600,
                    "50k_to_100k": 13000,
                },
                "typical_timeframe": "3-6 months",
                "enforcement_options": [
                    "Writ of execution",
                    "Garnishee order",
                    "Examination order",
                    "Charging order",
                    "Warrant to seize property",
                ],
            },
            "district": {
                "name": "District Court of Queensland",
                "jurisdiction_amount_range": "$100,000 - $750,000",
                "min_amount_cents": 10000000,
                "max_amount_cents": 75000000,
                "filing_fees": {
                    "standard": 19500,
                },
                "typical_timeframe": "6-18 months",
                "enforcement_options": [
                    "Writ of possession",
                    "Garnishee order",
                    "Charging order",
                    "Appointment of receiver",
                    "Examination of judgment debtor",
                ],
            },
            "supreme": {
                "name": "Supreme Court of Queensland",
                "jurisdiction_amount_range": "$750,000+",
                "min_amount_cents": 75000000,
                "max_amount_cents": None,
                "filing_fees": {
                    "standard": 28000,
                },
                "typical_timeframe": "12-36 months",
                "enforcement_options": [
                    "Writ of possession",
                    "Garnishee order",
                    "Charging order",
                    "Appointment of receiver",
                    "Bankruptcy notice (personal debtors)",
                    "Winding up notice (corporate debtors)",
                ],
            },
        },
    }

    LIMITATION_PERIODS: dict[str, int] = {
        "NSW": 6,
        "VIC": 6,
        "QLD": 6,
        "WA": 6,
        "SA": 6,
        "TAS": 6,
        "ACT": 6,
        "NT": 3,
    }

    SOLICITOR_DATA: dict[str, list[dict[str, Any]]] = {
        "NSW": [
            {
                "firm_name": "Moss Legal",
                "specialty": "debt_recovery",
                "location": "Sydney CBD, NSW",
                "phone": "(02) 9230 1122",
                "website": "https://www.mosslegal.com.au",
            },
            {
                "firm_name": "RMB Lawyers",
                "specialty": "debt_recovery",
                "location": "Wollongong, NSW",
                "phone": "(02) 4221 6577",
                "website": "https://www.rmb-lawyers.com.au",
            },
            {
                "firm_name": "Turner Freeman Lawyers",
                "specialty": "debt_recovery",
                "location": "Parramatta, NSW",
                "phone": "(02) 9893 5555",
                "website": "https://www.turnerfreeman.com.au",
            },
        ],
        "VIC": [
            {
                "firm_name": "Holding Redlich",
                "specialty": "debt_recovery",
                "location": "Melbourne CBD, VIC",
                "phone": "(03) 9226 4211",
                "website": "https://www.holdingredlich.com",
            },
            {
                "firm_name": "Maddocks",
                "specialty": "debt_recovery",
                "location": "Melbourne, VIC",
                "phone": "(03) 9258 3555",
                "website": "https://www.maddocks.com.au",
            },
        ],
        "QLD": [
            {
                "firm_name": "Shine Lawyers",
                "specialty": "debt_recovery",
                "location": "Brisbane, QLD",
                "phone": "(07) 3350 2266",
                "website": "https://www.shine.com.au",
            },
            {
                "firm_name": "Hynes Legal",
                "specialty": "debt_recovery",
                "location": "Fortitude Valley, QLD",
                "phone": "(07) 3510 0793",
                "website": "https://www.hynes.net.au",
            },
        ],
        "WA": [
            {
                "firm_name": "Lavan",
                "specialty": "debt_recovery",
                "location": "Perth, WA",
                "phone": "(08) 9225 2233",
                "website": "https://www.lavan.com.au",
            },
            {
                "firm_name": "Corrs Chambers Westgarth",
                "specialty": "debt_recovery",
                "location": "Perth, WA",
                "phone": "(08) 9226 4500",
                "website": "https://www.corrs.com.au",
            },
        ],
        "SA": [
            {
                "firm_name": "Sparke Helmore Lawyers",
                "specialty": "debt_recovery",
                "location": "Adelaide, SA",
                "phone": "(08) 8414 3300",
                "website": "https://www.sparke.com.au",
            },
        ],
        "TAS": [
            {
                "firm_name": "Maurice Blackburn Lawyers",
                "specialty": "debt_recovery",
                "location": "Hobart, TAS",
                "phone": "(03) 6223 4500",
                "website": "https://www.mauriceblackburn.com.au",
            },
        ],
        "ACT": [
            {
                "firm_name": "Kingsford Legal Centre",
                "specialty": "debt_recovery",
                "location": "Canberra, ACT",
                "phone": "(02) 6106 2700",
                "website": "https://www.klc.asn.au",
            },
        ],
        "NT": [
            {
                "firm_name": "Ward Keller",
                "specialty": "debt_recovery",
                "location": "Darwin, NT",
                "phone": "(08) 8945 7477",
                "website": "https://www.wardkeller.com.au",
            },
        ],
    }

    LEGISLATION_REFS: dict[str, list[str]] = {
        "NSW": [
            "Civil Procedure Act 2005 (NSW)",
            "Uniform Civil Procedure Rules 2005 (NSW)",
            "Consumer Credit (Queensland) Act 1994",
            "Credit Reporting Act 2010 (Cth)",
            "Privacy Act 1988 (Cth)",
        ],
        "VIC": [
            "Civil Procedure Act 2010 (Vic)",
            "Magistrates' Court Civil Procedure Rules 2011 (Vic)",
            "Consumer Credit (Queensland) Act 1994",
            "Credit Reporting Act 2010 (Cth)",
            "Privacy Act 1988 (Cth)",
        ],
        "QLD": [
            "Uniform Civil Procedure Rules 1999 (Qld)",
            "Queensland Civil and Administrative Tribunal Act 2009 (Qld)",
            "Consumer Credit (Queensland) Act 1994",
            "Credit Reporting Act 2010 (Cth)",
            "Privacy Act 1988 (Cth)",
        ],
        "WA": [
            "District Court of Western Australia Act 1969 (WA)",
            "Magistrates Court (Civil Proceedings) Act 2004 (WA)",
            "State Administrative Tribunal Act 2004 (WA)",
            "Credit Reporting Act 2010 (Cth)",
            "Privacy Act 1988 (Cth)",
        ],
        "SA": [
            "District Court Act 1991 (SA)",
            "Magistrates Court Act 1991 (SA)",
            "South Australian Civil and Administrative Tribunal Act 2013 (SA)",
            "Credit Reporting Act 2010 (Cth)",
            "Privacy Act 1988 (Cth)",
        ],
        "TAS": [
            "Magistrates Court (Civil Division) Act 1986 (Tas)",
            "Supreme Court Civil Procedure Act 1932 (Tas)",
            "Credit Reporting Act 2010 (Cth)",
            "Privacy Act 1988 (Cth)",
        ],
        "ACT": [
            "Court Procedures Act 2004 (ACT)",
            "ACT Civil and Administrative Tribunal Act 2008 (ACT)",
            "Credit Reporting Act 2010 (Cth)",
            "Privacy Act 1988 (Cth)",
        ],
        "NT": {
            "Criminal Code Act 1983 (NT)",  # intentional set - not a list
            "Civil Evidentiary Provisions Act 2009 (NT)",
            "Local Court Act 1980 (NT)",
            "Credit Reporting Act 2010 (Cth)",
            "Privacy Act 1988 (Cth)",
        },
    }

    TRIBUNAL_LIMITS_CENTS: dict[str, int] = {
        "NSW": 2000000,
        "VIC": 1500000,
        "QLD": 2500000,
        "WA": 5000000,
        "SA": 1000000,
        "TAS": 1000000,
        "ACT": 2500000,
        "NT": 500000,
    }

    def _get_jurisdiction(self, jurisdiction: str) -> str:
        jurisdiction = jurisdiction.upper()
        if jurisdiction not in self.JURISDICTION_TRIBUNALS:
            raise ValueError(
                f"Unsupported jurisdiction: {jurisdiction}. "
                f"Supported: {', '.join(sorted(self.JURISDICTION_TRIBUNALS.keys()))}"
            )
        return jurisdiction

    def _format_cents(self, cents: int) -> str:
        return f"${cents / 100:,.2f}"

    def _get_limitation_years(self, jurisdiction: str) -> int:
        years = self.LIMITATION_PERIODS.get(jurisdiction, 6)
        return years

    def get_pathway(
        self,
        debt_amount_cents: int,
        jurisdiction: str = "NSW",
        debtor_type: str = "business",
    ) -> dict[str, Any]:
        jurisdiction = self._get_jurisdiction(jurisdiction)

        tribunal_limit = self.TRIBUNAL_LIMITS_CENTS.get(jurisdiction, 1000000)

        steps: list[dict[str, Any]] = []
        recommended_path = "tribunal"

        steps.append({
            "step": 1,
            "name": "Letter of Demand",
            "description": (
                "Formal letter demanding payment within 21-30 days. Must be sent before "
                "commencing legal proceedings. Often required as a prerequisite for court "
                "or tribunal filing."
            ),
            "cost_estimate_cents": 50000,
            "cost_range": "$200 - $800",
            "timeframe": "7-30 days",
            "requirements": [
                "Clear statement of debt amount and basis",
                "Payment deadline (typically 21-30 days)",
                "Consequences of non-payment",
                "Send via registered post or email with read receipt",
            ],
            "status": "mandatory_first_step",
        })

        if debtor_type == "business":
            steps.append({
                "step": 2,
                "name": "Mediation / Alternative Dispute Resolution (ADR)",
                "description": (
                    "Attempt to resolve the dispute through a neutral third-party mediator. "
                    "Required for many tribunal applications. Can be conducted in-person or "
                    "via video conference."
                ),
                "cost_estimate_cents": 150000,
                "cost_range": "$500 - $2,500",
                "timeframe": "14-45 days",
                "requirements": [
                    "Previous Letter of Demand must have been issued",
                    "Mediation service provider engagement",
                    "Prepare position statement",
                    "Attend mediation conference",
                ],
                "status": "recommended",
            })
        else:
            steps.append({
                "step": 2,
                "name": "Mediation / Alternative Dispute Resolution (ADR)",
                "description": (
                    "Optional but recommended mediation before tribunal or court proceedings. "
                    "Can demonstrate good faith to the tribunal or court."
                ),
                "cost_estimate_cents": 100000,
                "cost_range": "$300 - $1,500",
                "timeframe": "14-30 days",
                "requirements": [
                    "Previous Letter of Demand issued",
                    "Agreement of both parties to mediate",
                ],
                "status": "optional_recommended",
            })

        if debt_amount_cents <= tribunal_limit:
            tribunal_info = self.JURISDICTION_TRIBUNALS.get(jurisdiction, {})
            tribunal_name = tribunal_info.get("name", f"{jurisdiction} Tribunal")
            tribunal_fee = tribunal_info.get("filing_fee_cents", 5000)

            steps.append({
                "step": 3,
                "name": f"Tribunal Application ({tribunal_info.get('abbreviation', 'Tribunal')})",
                "description": (
                    f"File application with {tribunal_name}. "
                    f"Suitable for claims up to {self._format_cents(tribunal_limit)}. "
                    "Less formal and lower cost than court proceedings."
                ),
                "cost_estimate_cents": tribunal_fee + 300000,
                "cost_range": f"${tribunal_fee / 100 + 1000} - ${tribunal_fee / 100 + 5000}",
                "timeframe": "60-180 days",
                "requirements": [
                    "Complete application form",
                    "Pay filing fee",
                    "Provide supporting documentation",
                    "Serve respondent",
                    "Attend hearing",
                ],
                "status": "recommended_for_amount",
                "filing_fee_cents": tribunal_fee,
            })
            recommended_path = "tribunal"
        else:
            court_level = self._determine_court_level(debt_amount_cents, jurisdiction)
            court_info = court_level.get("info", {})
            court_fees = court_info.get("filing_fees", {})
            fee_keys = [k for k in court_fees.keys() if k != "standard"]
            filing_fee = court_fees.get("standard", 0)
            if fee_keys:
                filing_fee = max(court_fees.values())

            steps.append({
                "step": 3,
                "name": f"Court Filing ({court_info.get('name', 'Court')})",
                "description": (
                    f"File statement of claim with {court_info.get('name', 'the appropriate court')}. "
                    f"For claims in range {court_info.get('jurisdiction_amount_range', 'N/A')}."
                ),
                "cost_estimate_cents": filing_fee + 500000,
                "cost_range": f"${filing_fee / 100 + 2000} - ${filing_fee / 100 + 10000}",
                "timeframe": court_info.get("typical_timeframe", "6-18 months"),
                "requirements": [
                    "Prepare statement of claim",
                    "Pay filing fee",
                    "Serve defendant",
                    "Comply with court directions",
                    "Attend case management / hearing",
                ],
                "status": "required_for_amount",
                "filing_fee_cents": filing_fee,
                "court_level": court_level.get("level", "local"),
            })
            recommended_path = "court"

        if debtor_type == "business":
            steps.append({
                "step": 4,
                "name": "Statutory Demand (Corporate Debtors)",
                "description": (
                    "Issue a statutory demand under the Corporations Act 2001 (Cth) s 459E. "
                    "Failure to comply within 21 days creates a presumption of insolvency, "
                    "enabling winding-up proceedings."
                ),
                "cost_estimate_cents": 50000,
                "cost_range": "$200 - $500",
                "timeframe": "21-63 days",
                "requirements": [
                    "Debt must be at least $4,000",
                    "Served on registered office of company",
                    "21-day compliance period",
                    "If unpaid, application for winding-up within 3 months",
                ],
                "status": "conditional_on_amount_and_type",
            })

        steps.append({
            "step": len(steps) + 1,
            "name": "Enforcement",
            "description": (
                "Enforcement of judgment or tribunal order. Multiple enforcement mechanisms "
                "available depending on the debtor's assets and circumstances."
            ),
            "cost_estimate_cents": 200000,
            "cost_range": "$500 - $5,000+",
            "timeframe": "30-180 days",
            "enforcement_options": [
                "Warrant of enforcement / writ of execution",
                "Garnishee order (bank accounts, employer)",
                "Examination of debtor (discover assets)",
                "Charging order on real property",
                "Appointment of receiver",
                "Bankruptcy proceedings (individuals, debts over $10,000)",
                "Winding-up proceedings (companies, debts over $4,000)",
            ],
            "status": "post_judgment",
        })

        total_min_cost = 0
        total_max_cost = 0
        for step in steps:
            total_min_cost += step["cost_estimate_cents"]
            total_max_cost += step["cost_estimate_cents"] + 100000

        total_timeframe_min = 30
        total_timeframe_max = 365
        if debt_amount_cents <= tribunal_limit:
            total_timeframe_min = 90
            total_timeframe_max = 270
        else:
            total_timeframe_min = 180
            total_timeframe_max = 540

        return {
            "jurisdiction": jurisdiction,
            "debt_amount_cents": debt_amount_cents,
            "debt_amount": self._format_cents(debt_amount_cents),
            "debtor_type": debtor_type,
            "recommended_path": recommended_path,
            "pathway_steps": steps,
            "total_estimated_cost_cents": total_min_cost,
            "total_estimated_cost": f"${total_min_cost / 100:,.2f} - ${total_max_cost / 100:,.2f}",
            "total_estimated_timeline": f"{total_timeframe_min}-{total_timeframe_max} days",
            "notes": [
                "Costs are estimates only and may vary by complexity and location.",
                "Solicitor fees not included in tribunal step estimates.",
                "Interest and costs may be recoverable from the debtor.",
                "Consider the debtor's ability to pay before commencing proceedings.",
            ],
        }

    def _determine_court_level(
        self, debt_amount_cents: int, jurisdiction: str
    ) -> dict[str, Any]:
        courts = self.JURISDICTION_COURTS.get(jurisdiction, {})
        if not courts:
            return {
                "level": "local",
                "info": {
                    "name": f"{jurisdiction} Local/Magistrates Court",
                    "jurisdiction_amount_range": "Up to $100,000",
                    "min_amount_cents": 0,
                    "max_amount_cents": 10000000,
                    "filing_fees": {"standard": 10000},
                    "typical_timeframe": "3-6 months",
                    "enforcement_options": [],
                },
            }

        for level in ["local", "county", "district", "supreme"]:
            court = courts.get(level)
            if court:
                max_cents = court.get("max_amount_cents")
                if max_cents is None or debt_amount_cents <= max_cents:
                    return {"level": level, "info": court}

        supreme = courts.get("supreme", {})
        return {"level": "supreme", "info": supreme}

    def get_tribunal_info(self, jurisdiction: str) -> dict[str, Any]:
        jurisdiction = self._get_jurisdiction(jurisdiction)
        return self.JURISDICTION_TRIBUNALS[jurisdiction].copy()

    def get_court_info(self, jurisdiction: str) -> dict[str, Any]:
        jurisdiction = self._get_jurisdiction(jurisdiction)
        courts = self.JURISDICTION_COURTS.get(jurisdiction)

        if not courts:
            return {
                "jurisdiction": jurisdiction,
                "note": f"Detailed court data for {jurisdiction} is not yet available. "
                        "Refer to the relevant state court website.",
                "local": {
                    "name": f"{jurisdiction} Magistrates/Local Court",
                    "typical_timeframe": "3-6 months",
                },
                "supreme": {
                    "name": f"{jurisdiction} Supreme Court",
                    "typical_timeframe": "12-36 months",
                },
            }

        return {
            "jurisdiction": jurisdiction,
            "courts": courts,
        }

    def generate_legal_referral(
        self,
        business_name: str,
        debtor_name: str,
        amount_cents: int,
        jurisdiction: str = "NSW",
    ) -> dict[str, Any]:
        jurisdiction = self._get_jurisdiction(jurisdiction)

        limitation = self.check_limitation_period(
            datetime.now().strftime("%Y-%m-%d"), jurisdiction
        )

        pathway = self.get_pathway(amount_cents, jurisdiction, "business")

        legislation = self.LEGISLATION_REFS.get(jurisdiction, [])

        now = datetime.now().strftime("%Y-%m-%d")

        referral_text = f"""
LEGAL REFERRAL - DEBT RECOVERY
{'=' * 50}
Date: {now}
Prepared for: [Solicitor Name]
Re: Debt Recovery - {debtor_name}

CLIENT DETAILS
{'-' * 50}
Creditor:           {business_name}
Debtor:             {debtor_name}
Amount Owing:       {pathway['debt_amount']}
Jurisdiction:       {jurisdiction}

DEBT SUMMARY
{'-' * 50}
The creditor ({business_name}) instructs that {debtor_name} is indebted in the amount of
{pathway['debt_amount']} for goods/services provided.

Standard collection attempts have been made but remain unsuccessful. Legal action is now
being considered.

HISTORY OF COLLECTION ATTEMPTS
{'-' * 50}
1. Invoice issued and payment terms communicated
2. Follow-up communications sent (phone, email, letter)
3. Statement of account issued
4. Formal demand letters issued
5. Standard collection agency engagement attempted

RECOMMENDED PATHWAY
{'-' * 50}
Based on the amount ({pathway['debt_amount']}) and jurisdiction ({jurisdiction}), the
recommended pathway is: {pathway['recommended_path'].upper()}

Estimated total costs: {pathway['total_estimated_cost']}
Estimated timeline: {pathway['total_estimated_timeline']}

Pathway Steps:
"""
        for step in pathway["pathway_steps"]:
            referral_text += f"""
  Step {step['step']}: {step['name']}
    Description: {step['description']}
    Estimated Cost: {step['cost_range']}
    Timeframe: {step['timeframe']}
"""

        referral_text += f"""
LIMITATION PERIOD
{'-' * 50}
Limitation Period ({jurisdiction}): {limitation['expiry_date']}
Within Limit:       {'Yes' if limitation['within_limit'] else 'NO - EXPIRED'}
Days Remaining:     {limitation['days_remaining']}

{'*** WARNING: Limitation period is approaching expiry. Urgent action required. ***' if limitation.get('warning') else ''}
{'*** CRITICAL: Limitation period has expired. Legal action may not be possible. ***' if not limitation['within_limit'] else ''}

RELEVANT LEGISLATION
{'-' * 50}
"""
        for act in legislation:
            referral_text += f"  - {act}\n"

        referral_text += f"""
DOCUMENTS REQUIRED
{'-' * 50}
  - Original contract or agreement
  - Invoices and statements of account
  - Correspondence (all demand letters, responses)
  - Payment history records
  - Any security or guarantee documents
  - Corporate search results (if company debtor)

INSTRUCTIONS
{'-' * 50}
Please advise on:
1. Viability of recovery based on debtor's financial position
2. Recommended enforcement strategy
3. Likely costs and disbursements
4. Timeframe for resolution
5. Any matters affecting the creditor's ability to recover

This referral is prepared by the Tether debt collection platform.
For queries, contact the accounts team.

{'=' * 50}
END OF REFERRAL
{'=' * 50}
"""

        return {
            "referral_text": referral_text,
            "business_name": business_name,
            "debtor_name": debtor_name,
            "amount_cents": amount_cents,
            "amount": pathway["debt_amount"],
            "jurisdiction": jurisdiction,
            "recommended_pathway": pathway["recommended_path"],
            "limitation_period": limitation,
            "legislation_refs": legislation,
            "estimated_costs": pathway["total_estimated_cost"],
            "estimated_timeline": pathway["total_estimated_timeline"],
            "generated_at": now,
        }

    def estimate_costs(
        self,
        debt_amount_cents: int,
        pathway: str = "tribunal",
        jurisdiction: str = "NSW",
    ) -> dict[str, Any]:
        jurisdiction = self._get_jurisdiction(jurisdiction)
        tribunal_info = self.JURISDICTION_TRIBUNALS.get(jurisdiction, {})
        tribunal_fee = tribunal_info.get("filing_fee_cents", 5000)

        filing_fees_cents = 0
        solicitor_estimate_cents = 0
        process_server_cents = 12000

        if pathway == "tribunal":
            filing_fees_cents = tribunal_fee
            if debt_amount_cents <= 2000000:
                solicitor_estimate_cents = 2000000
            elif debt_amount_cents <= 5000000:
                solicitor_estimate_cents = 3500000
            else:
                solicitor_estimate_cents = 5000000
        elif pathway == "court":
            court_level = self._determine_court_level(debt_amount_cents, jurisdiction)
            court_info = court_level.get("info", {})
            fees = court_info.get("filing_fees", {})
            if fees:
                fee_keys = [k for k in fees.keys() if k != "standard"]
                if fee_keys:
                    filing_fees_cents = max(fees.values())
                else:
                    filing_fees_cents = fees.get("standard", 10000)
            else:
                filing_fees_cents = 10000

            court_level_type = court_level.get("level", "local")
            if court_level_type == "supreme":
                solicitor_estimate_cents = 15000000
            elif court_level_type in ("district", "county"):
                solicitor_estimate_cents = 8000000
            else:
                solicitor_estimate_cents = 3000000
        elif pathway == "enforcement":
            filing_fees_cents = 5000
            solicitor_estimate_cents = 1500000
            process_server_cents = 25000
        else:
            raise ValueError(
                f"Unknown pathway: {pathway}. Use 'tribunal', 'court', or 'enforcement'."
            )

        total_estimated_cents = filing_fees_cents + solicitor_estimate_cents + process_server_cents

        recovery_percentage = 0.0
        if debt_amount_cents > 0:
            recovery_percentage = (total_estimated_cents / debt_amount_cents) * 100

        return {
            "pathway": pathway,
            "jurisdiction": jurisdiction,
            "debt_amount_cents": debt_amount_cents,
            "debt_amount": self._format_cents(debt_amount_cents),
            "breakdown": {
                "filing_fees_cents": filing_fees_cents,
                "filing_fees": self._format_cents(filing_fees_cents),
                "solicitor_costs_estimate_cents": solicitor_estimate_cents,
                "solicitor_costs_estimate": self._format_cents(solicitor_estimate_cents),
                "process_server_cents": process_server_cents,
                "process_server": self._format_cents(process_server_cents),
            },
            "total_estimated_cents": total_estimated_cents,
            "total_estimated": self._format_cents(total_estimated_cents),
            "recovery_analysis": {
                "cost_as_percentage_of_debt": f"{recovery_percentage:.1f}%",
                "costs_recoverable": (
                    "Partially - filing fees and some solicitor costs may be "
                    "recoverable as costs of the proceedings."
                ),
                "recommendation": (
                    "Proceed" if recovery_percentage < 30
                    else "Consider settlement or negotiate" if recovery_percentage < 50
                    else "Unlikely to be cost-effective - explore alternative recovery"
                ),
            },
            "notes": [
                "Solicitor costs are estimates based on standard hourly rates.",
                "Costs may vary based on complexity, number of witnesses, and hearing days.",
                "Court-ordered costs may be recovered from the debtor.",
                "Disbursements (expert reports, travel, etc.) are not included.",
                "Consider the debtor's assets before proceeding.",
            ],
        }

    def list_solicitors(
        self, jurisdiction: str = "NSW", specialty: str = "debt_recovery"
    ) -> list[dict[str, str]]:
        jurisdiction = self._get_jurisdiction(jurisdiction)
        solicitors = self.SOLICITOR_DATA.get(jurisdiction, [])

        if not solicitors:
            return [{
                "firm_name": f"No firms available in {jurisdiction}",
                "specialty": specialty,
                "location": jurisdiction,
                "phone": "N/A",
                "website": "N/A",
                "note": f"Please contact the Law Society of {jurisdiction} for referrals.",
            }]

        filtered = [
            s for s in solicitors
            if s.get("specialty") == specialty or specialty == "all"
        ]

        if not filtered:
            filtered = solicitors

        return filtered

    def check_limitation_period(
        self, due_date: str, jurisdiction: str = "NSW"
    ) -> dict[str, Any]:
        jurisdiction = self._get_jurisdiction(jurisdiction)

        try:
            due = datetime.strptime(due_date, "%Y-%m-%d")
        except ValueError:
            try:
                due = datetime.strptime(due_date, "%d/%m/%Y")
            except ValueError:
                return {
                    "error": f"Invalid date format: {due_date}. Use YYYY-MM-DD or DD/MM/YYYY.",
                    "within_limit": False,
                    "days_remaining": 0,
                    "expiry_date": "Unknown",
                    "warning": True,
                }

        years = self.LIMITATION_PERIODS.get(jurisdiction, 6)
        expiry = due + timedelta(days=years * 365)
        now = datetime.now()
        days_remaining = (expiry - now).days

        within_limit = days_remaining > 0
        warning = 0 < days_remaining <= 90

        result: dict[str, Any] = {
            "jurisdiction": jurisdiction,
            "due_date": due_date,
            "limitation_years": years,
            "expiry_date": expiry.strftime("%Y-%m-%d"),
            "within_limit": within_limit,
            "days_remaining": max(0, days_remaining),
            "warning": warning,
        }

        if not within_limit:
            result["message"] = (
                f"The limitation period expired on {expiry.strftime('%d/%m/%Y')}. "
                "Legal action may no longer be possible for this debt."
            )
        elif warning:
            result["message"] = (
                f"The limitation period expires on {expiry.strftime('%d/%m/%Y')} "
                f"(in {days_remaining} days). Urgent action is required."
            )
        else:
            result["message"] = (
                f"The limitation period expires on {expiry.strftime('%d/%m/%Y')} "
                f"(in {days_remaining} days). Action should be taken promptly."
            )

        return result
