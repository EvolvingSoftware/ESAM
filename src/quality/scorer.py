"""Quality Scorer — Composite edition quality scoring.

Computes a weighted composite quality score for newsletter editions
across four dimensions: citation validity, signal density, narrative
continuity, and brand voice adherence.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from database import get_connection
from quality.metrics import QualityMetrics
from quality.baseline import BaselineManager

__all__ = ["QualityScorer"]

# Composite weights
W_CITATION = 0.35
W_SIGNAL = 0.25
W_NARRATIVE = 0.25
W_BRAND = 0.15


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class QualityScorer:
    """Composite quality scorer for newsletter editions.

    Computes and persists quality scores by combining four metrics
    via weighted average.  Supports trend queries and regression
    detection against baselines.

    Usage::

        scorer = QualityScorer()
        result = scorer.score_edition(
            edition_id="ed-001",
            citation_report={"valid": True, "missing_ids": [], "hallucination_count": 0, "total_claims": 5},
            signal_data={"items": [...], "source_count": 3},
            narrative_data={"story_diffs": [...], "trajectories": [...]},
        )
    """

    def __init__(self, db_conn=None):
        self.db = db_conn
        self.metrics = QualityMetrics()
        self.baselines = BaselineManager()

    # ------------------------------------------------------------------
    # Score Edition
    # ------------------------------------------------------------------

    def score_edition(
        self,
        edition_id: str,
        citation_report: dict[str, Any] | None = None,
        signal_data: dict[str, Any] | None = None,
        narrative_data: dict[str, Any] | None = None,
        brand_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compute a composite quality score for *edition_id*.

        Parameters
        ----------
        edition_id : str
            The edition being scored.
        citation_report : dict or None
            Citation validation report.  Passed to
            :meth:`QualityMetrics.citation_validity`.
        signal_data : dict or None
            Expected keys: ``items`` (list), ``source_count`` (int).
            Passed to :meth:`QualityMetrics.signal_density`.
        narrative_data : dict or None
            Expected keys: ``story_diffs`` (list), ``trajectories`` (list).
            Passed to :meth:`QualityMetrics.narrative_continuity`.
        brand_data : dict or None
            Expected keys: ``output_text`` (str), ``brand_patterns`` (list, optional).
            Passed to :meth:`QualityMetrics.brand_voice`.

        Returns
        -------
        dict
            ``{edition_id, citation_validity, signal_density,
            narrative_continuity, brand_voice, composite_score, scored_at}``
        """
        citation_report = citation_report or {}
        signal_data = signal_data or {}
        narrative_data = narrative_data or {}
        brand_data = brand_data or {}

        citation_score = self.metrics.citation_validity(citation_report)
        signal_score = self.metrics.signal_density(
            signal_data.get("items", []),
            signal_data.get("source_count", 0),
        )
        narrative_score = self.metrics.narrative_continuity(
            narrative_data.get("story_diffs"),
            narrative_data.get("trajectories"),
        )
        brand_score = self.metrics.brand_voice(
            brand_data.get("output_text", ""),
            brand_data.get("brand_patterns"),
        )

        composite = (
            W_CITATION * citation_score
            + W_SIGNAL * signal_score
            + W_NARRATIVE * narrative_score
            + W_BRAND * brand_score
        )

        now = _now()
        score_id = _new_id()
        run_id = citation_report.get("run_id", "")

        result = {
            "edition_id": edition_id,
            "citation_validity": round(citation_score, 4),
            "signal_density": round(signal_score, 4),
            "narrative_continuity": round(narrative_score, 4),
            "brand_voice": round(brand_score, 4),
            "composite_score": round(composite, 4),
            "scored_at": now,
            "run_id": run_id,
        }

        # Persist to database
        conn = self.db or get_connection()
        conn.execute(
            """INSERT OR REPLACE INTO wf_quality_scores
               (id, edition_id, run_id,
                citation_validity, signal_density,
                narrative_continuity, brand_voice,
                composite_score, scored_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                score_id,
                edition_id,
                run_id,
                result["citation_validity"],
                result["signal_density"],
                result["narrative_continuity"],
                result["brand_voice"],
                result["composite_score"],
                now,
            ),
        )
        conn.commit()

        return result

    # ------------------------------------------------------------------
    # Get Score
    # ------------------------------------------------------------------

    def get_score(self, edition_id: str) -> dict[str, Any] | None:
        """Retrieve the quality score for *edition_id*.

        Returns
        -------
        dict or None
            The quality score record, or ``None`` if not scored.
        """
        conn = self.db or get_connection()
        row = conn.execute(
            "SELECT * FROM wf_quality_scores WHERE edition_id = ? ORDER BY scored_at DESC LIMIT 1",
            (edition_id,),
        ).fetchone()
        if not row:
            return None
        return dict(row)

    # ------------------------------------------------------------------
    # Get Trend
    # ------------------------------------------------------------------

    def get_trend(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get quality scores over recent editions.

        Parameters
        ----------
        limit : int
            Max number of editions to return (default: 10).

        Returns
        -------
        list[dict]
            Quality score records ordered by ``scored_at`` descending.
        """
        conn = self.db or get_connection()
        rows = conn.execute(
            "SELECT * FROM wf_quality_scores ORDER BY scored_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Check Regression
    # ------------------------------------------------------------------

    def check_regression(
        self, edition_id: str, baseline_id: str
    ) -> dict[str, Any]:
        """Check if *edition_id* regressed against a baseline.

        Parameters
        ----------
        edition_id : str
            The edition to check.
        baseline_id : str
            The baseline edition ID to compare against.

        Returns
        -------
        dict
            ``{regressed: bool, score_delta: float, regressed_metrics: [str]}``
        """
        current = self.get_score(edition_id)
        baseline = self.get_score(baseline_id)

        if not current:
            return {
                "regressed": False,
                "score_delta": 0.0,
                "regressed_metrics": [],
                "error": "Current edition score not found.",
            }

        if not baseline:
            return {
                "regressed": False,
                "score_delta": 0.0,
                "regressed_metrics": [],
                "error": "Baseline edition score not found.",
            }

        current_composite = current.get("composite_score", 0.0) or 0.0
        baseline_composite = baseline.get("composite_score", 0.0) or 0.0
        score_delta = round(current_composite - baseline_composite, 4)

        metrics = [
            "citation_validity",
            "signal_density",
            "narrative_continuity",
            "brand_voice",
        ]
        regressed_metrics = []
        for metric in metrics:
            curr_val = current.get(metric, 0.0) or 0.0
            base_val = baseline.get(metric, 0.0) or 0.0
            if curr_val < base_val - 0.05:
                regressed_metrics.append(metric)

        return {
            "regressed": score_delta < -0.05,
            "score_delta": score_delta,
            "regressed_metrics": regressed_metrics,
        }
