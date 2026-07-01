"""Baseline Manager — Store and compare quality baselines for editions."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from database import get_connection

__all__ = ["BaselineManager"]


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BaselineManager:
    """Manage quality baselines for edition regression detection.

    A baseline captures the quality score of a reference edition
    (typically the first edition of a newsletter series, or a manually
    accepted "good" edition).  Subsequent editions are compared against
    this baseline to detect regression.
    """

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create(
        self,
        edition_id: str,
        quality_score: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Store a quality baseline for *edition_id*.

        Parameters
        ----------
        edition_id : str
            The edition being baselined.
        quality_score : dict
            The full quality score dict returned by
            :meth:`QualityScorer.score_edition`.
        metadata : dict or None
            Optional metadata (e.g. ``{"workflow_id": "...", "label": "first"}``).

        Returns
        -------
        dict
            The stored baseline record.
        """
        baseline_id = _new_id()
        now = _now()

        run_id = quality_score.get("run_id", "")
        baseline_json = {
            "edition_id": edition_id,
            "quality_score": quality_score,
            "metadata": metadata or {},
            "created_at": now,
        }

        conn = get_connection()
        conn.execute(
            """INSERT INTO wf_quality_baselines
               (id, edition_id, run_id, baseline_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (baseline_id, edition_id, run_id, json.dumps(baseline_json), now),
        )
        conn.commit()

        return {
            "id": baseline_id,
            "edition_id": edition_id,
            "run_id": run_id,
            "baseline_json": baseline_json,
            "created_at": now,
        }

    # ------------------------------------------------------------------
    # Get
    # ------------------------------------------------------------------

    def get(self, baseline_id: str) -> dict[str, Any] | None:
        """Retrieve a baseline by its ID.

        Returns
        -------
        dict or None
            The baseline record, or ``None`` if not found.
        """
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM wf_quality_baselines WHERE id = ?",
            (baseline_id,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        raw = result.get("baseline_json", "{}")
        if isinstance(raw, str):
            try:
                result["baseline_json"] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                result["baseline_json"] = {}
        return result

    # ------------------------------------------------------------------
    # Get Latest
    # ------------------------------------------------------------------

    def get_latest(self) -> dict[str, Any] | None:
        """Retrieve the most recently created baseline.

        Returns
        -------
        dict or None
            The latest baseline record, or ``None`` if none exist.
        """
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM wf_quality_baselines ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        raw = result.get("baseline_json", "{}")
        if isinstance(raw, str):
            try:
                result["baseline_json"] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                result["baseline_json"] = {}
        return result

    # ------------------------------------------------------------------
    # Compare
    # ------------------------------------------------------------------

    def compare(
        self, edition_id: str, baseline_id: str
    ) -> dict[str, Any]:
        """Compare an edition's quality score against a baseline.

        Parameters
        ----------
        edition_id : str
            The edition to compare.
        baseline_id : str
            The baseline to compare against.

        Returns
        -------
        dict
            ``{metric_deltas: dict, regressed: bool, recommendation: str}``
        """
        from quality.scorer import QualityScorer

        scorer = QualityScorer()
        edition_score = scorer.get_score(edition_id)
        baseline_record = self.get(baseline_id)

        if not edition_score:
            return {
                "metric_deltas": {},
                "regressed": False,
                "recommendation": "Edition score not found — cannot compare.",
            }

        if not baseline_record:
            return {
                "metric_deltas": {},
                "regressed": False,
                "recommendation": "Baseline not found — cannot compare.",
            }

        baseline_data = baseline_record.get("baseline_json", {})
        if isinstance(baseline_data, str):
            try:
                baseline_data = json.loads(baseline_data)
            except (json.JSONDecodeError, TypeError):
                baseline_data = {}

        baseline_score = baseline_data.get("quality_score", {})

        metrics = [
            "citation_validity",
            "signal_density",
            "narrative_continuity",
            "brand_voice",
            "composite_score",
        ]

        metric_deltas: dict[str, float] = {}
        regressed_metrics: list[str] = []

        for metric in metrics:
            current = edition_score.get(metric, 0.0) or 0.0
            base = baseline_score.get(metric, 0.0) or 0.0
            delta = current - base
            metric_deltas[metric] = round(delta, 4)
            if delta < -0.05:  # more than 5% drop is regression
                regressed_metrics.append(metric)

        regressed = len(regressed_metrics) > 0

        if regressed:
            recommendation = (
                f"Regression detected in {len(regressed_metrics)} metric(s): "
                f"{', '.join(regressed_metrics)}. "
                "Consider reviewing the edition pipeline for quality degradation."
            )
        elif all(abs(d) < 0.01 for d in metric_deltas.values()):
            recommendation = "Edition quality is stable — no significant changes."
        else:
            improved = [m for m in metrics if metric_deltas.get(m, 0) > 0.05]
            if improved:
                recommendation = (
                    f"Edition quality has improved in {len(improved)} metric(s): "
                    f"{', '.join(improved)}."
                )
            else:
                recommendation = "Minor quality fluctuations within acceptable thresholds."

        return {
            "metric_deltas": metric_deltas,
            "regressed": regressed,
            "recommendation": recommendation,
        }
