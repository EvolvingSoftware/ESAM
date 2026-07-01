"""Regression Test Suite — Capture known-good baselines and detect regressions.

Provides :class:`RegressionTester` for comparing edition quality scores
against baselines with configurable thresholds, and :class:`BaselineStore`
for persisting, retrieving, and promoting baseline records in the
``wf_quality_baselines`` table.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from database import get_connection

__all__ = ["RegressionTester", "BaselineStore"]


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Baseline Store
# ---------------------------------------------------------------------------


class BaselineStore:
    """Thin persistence wrapper over ``wf_quality_baselines``.

    Each baseline stores a JSON blob with the full quality-score dict
    alongside edition and run metadata.
    """

    def create(
        self,
        edition_id: str,
        scores: dict[str, Any],
        run_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Store a new baseline for *edition_id*.

        Parameters
        ----------
        edition_id : str
            The edition being baselined.
        scores : dict
            Quality scores (including ``composite_score`` and per-metric keys).
        run_id : str
            Optional workflow run identifier.
        metadata : dict or None
            Optional metadata (e.g. ``{"workflow_id": "...", "label": "v1"}``).

        Returns
        -------
        dict
            The stored baseline record.
        """
        baseline_id = _new_id()
        now = _now()

        baseline_json = {
            "edition_id": edition_id,
            "quality_score": scores,
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

    def get(self, baseline_id: str) -> dict[str, Any] | None:
        """Retrieve a baseline by its ID.

        Returns
        -------
        dict or None
            The baseline record (with ``baseline_json`` deserialised), or
            ``None`` if not found.
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

    def list(self, limit: int = 10) -> list[dict[str, Any]]:
        """List baselines ordered by creation time descending.

        Parameters
        ----------
        limit : int
            Max baselines to return (default: 10).

        Returns
        -------
        list[dict]
            Baseline records.
        """
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM wf_quality_baselines ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        results = []
        for row in rows:
            result = dict(row)
            raw = result.get("baseline_json", "{}")
            if isinstance(raw, str):
                try:
                    result["baseline_json"] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    result["baseline_json"] = {}
            results.append(result)
        return results

    def promote(self, edition_id: str) -> dict[str, Any]:
        """Promote an existing edition's score to become the active baseline.

        Creates a new baseline record using the most recent quality score
        for *edition_id* in ``wf_quality_scores``.

        Parameters
        ----------
        edition_id : str
            The edition to promote to baseline status.

        Returns
        -------
        dict
            The newly created baseline record.

        Raises
        ------
        ValueError
            If no quality score exists for *edition_id*.
        """
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM wf_quality_scores WHERE edition_id = ? ORDER BY scored_at DESC LIMIT 1",
            (edition_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"No quality score found for edition {edition_id}")

        score_row = dict(row)
        run_id = score_row.get("run_id", "")

        scores = {
            "citation_validity": score_row.get("citation_validity", 0.0),
            "signal_density": score_row.get("signal_density", 0.0),
            "narrative_continuity": score_row.get("narrative_continuity", 0.0),
            "brand_voice": score_row.get("brand_voice", 0.0),
            "composite_score": score_row.get("composite_score", 0.0),
        }

        return self.create(
            edition_id=edition_id,
            scores=scores,
            run_id=run_id,
            metadata={"promoted": True, "source": "BaselineStore.promote"},
        )


# ---------------------------------------------------------------------------
# Regression Tester
# ---------------------------------------------------------------------------


class RegressionTester:
    """Test edition quality against a baseline and detect regressions.

    Runs three categories of test:

    1. **Composite threshold** -- ``composite_score`` must not drop more
       than 0.1 below the baseline.
    2. **Per-metric drop** -- No individual metric may drop more than 0.2
       below its baseline value.
    3. **Hallucination ratio** -- The ``hallucination_ratio`` (if present
       in the scores dict) must be below 0.05.

    Usage::

        tester = RegressionTester()
        result = tester.run_tests("ed-002", "base-001", scores)
    """

    def __init__(self, db_conn=None):
        self.db = db_conn
        self._store = BaselineStore()

    # ------------------------------------------------------------------
    # Run Tests
    # ------------------------------------------------------------------

    def run_tests(
        self,
        edition_id: str,
        baseline_id: str,
        quality_scores: dict[str, Any],
    ) -> dict[str, Any]:
        """Run regression test suite against a baseline.

        Parameters
        ----------
        edition_id : str
            The edition being tested (informational).
        baseline_id : str
            The baseline ID to compare against.
        quality_scores : dict
            The current quality scores dict (including ``composite_score``,
            per-metric keys, and optionally ``hallucination_ratio``).

        Returns
        -------
        dict
            ``{passed, total_tests, passed_tests, failed_tests, results,
            recommendation}``
        """
        baseline_record = self._store.get(baseline_id)
        if not baseline_record:
            return {
                "passed": False,
                "total_tests": 1,
                "passed_tests": 0,
                "failed_tests": 1,
                "results": [
                    {
                        "test_name": "baseline_exists",
                        "passed": False,
                        "expected": "baseline record",
                        "actual": None,
                        "delta": None,
                        "severity": "critical",
                    }
                ],
                "recommendation": f"Baseline '{baseline_id}' not found — cannot run regression tests.",
            }

        baseline_json = baseline_record.get("baseline_json", {})
        if isinstance(baseline_json, str):
            try:
                baseline_json = json.loads(baseline_json)
            except (json.JSONDecodeError, TypeError):
                baseline_json = {}

        baseline_scores = baseline_json.get("quality_score", {})
        if isinstance(baseline_scores, str):
            try:
                baseline_scores = json.loads(baseline_scores)
            except (json.JSONDecodeError, TypeError):
                baseline_scores = {}

        results: list[dict[str, Any]] = []
        passed_count = 0
        failed_count = 0

        # ── Test 1: Composite score within 0.1 of baseline ────────
        b_composite = float(baseline_scores.get("composite_score", 0.0) or 0.0)
        c_composite = float(quality_scores.get("composite_score", 0.0) or 0.0)
        composite_delta = c_composite - b_composite
        test_passed = c_composite >= b_composite - 0.1
        severity = "pass" if test_passed else ("warning" if composite_delta >= -0.15 else "critical")
        results.append({
            "test_name": "composite_score_threshold",
            "passed": test_passed,
            "expected": round(b_composite - 0.1, 4),
            "actual": round(c_composite, 4),
            "delta": round(composite_delta, 4),
            "severity": severity,
        })
        if test_passed:
            passed_count += 1
        else:
            failed_count += 1

        # ── Test 2: No single metric drops more than 0.2 ──────────
        metrics = ["citation_validity", "signal_density", "narrative_continuity", "brand_voice"]
        for metric in metrics:
            b_val = float(baseline_scores.get(metric, 0.0) or 0.0)
            c_val = float(quality_scores.get(metric, 0.0) or 0.0)
            delta = c_val - b_val
            test_passed = delta > -0.2  # drop no larger than 0.2
            sev = "pass" if test_passed else "critical"
            results.append({
                "test_name": f"metric_drop_{metric}",
                "passed": test_passed,
                "expected": round(b_val - 0.2, 4),
                "actual": round(c_val, 4),
                "delta": round(delta, 4),
                "severity": sev,
            })
            if test_passed:
                passed_count += 1
            else:
                failed_count += 1

        # ── Test 3: Hallucination ratio below 0.05 ────────────────
        hall_ratio = float(quality_scores.get("hallucination_ratio", 0.0) or 0.0)
        test_passed = hall_ratio < 0.05
        sev = "pass" if test_passed else "critical"
        results.append({
            "test_name": "hallucination_ratio",
            "passed": test_passed,
            "expected": 0.05,
            "actual": round(hall_ratio, 4),
            "delta": round(hall_ratio - 0.05, 4),
            "severity": sev,
        })
        if test_passed:
            passed_count += 1
        else:
            failed_count += 1

        total = len(results)
        overall_passed = failed_count == 0

        # Build recommendation
        if overall_passed:
            recommendation = "All regression tests passed — edition quality is acceptable."
        else:
            failed_names = [r["test_name"] for r in results if not r["passed"]]
            recommendation = (
                f"{failed_count} regression test(s) failed: {', '.join(failed_names)}. "
                "Review the edition pipeline for quality degradation."
            )

        return {
            "passed": overall_passed,
            "total_tests": total,
            "passed_tests": passed_count,
            "failed_tests": failed_count,
            "results": results,
            "recommendation": recommendation,
            "edition_id": edition_id,
            "baseline_id": baseline_id,
        }

    # ------------------------------------------------------------------
    # Update / Promote Baseline
    # ------------------------------------------------------------------

    def update_baseline(self, edition_id: str) -> dict[str, Any]:
        """Promote an edition to become the new active baseline.

        Reads the latest quality score for *edition_id* from the database
        and creates a baseline record via :meth:`BaselineStore.promote`.

        Parameters
        ----------
        edition_id : str
            The edition to promote.

        Returns
        -------
        dict
            The new baseline record.
        """
        return self._store.promote(edition_id)

    # ------------------------------------------------------------------
    # Compare Baselines
    # ------------------------------------------------------------------

    def compare_baselines(
        self,
        baseline_a_id: str,
        baseline_b_id: str,
    ) -> dict[str, Any]:
        """Compare two baselines and return per-metric deltas.

        Parameters
        ----------
        baseline_a_id : str
            The reference baseline ID.
        baseline_b_id : str
            The comparison baseline ID.

        Returns
        -------
        dict
            ``{baseline_a, baseline_b, metric_deltas, composite_delta,
            recommendation}``
        """
        rec_a = self._store.get(baseline_a_id)
        rec_b = self._store.get(baseline_b_id)

        if not rec_a or not rec_b:
            missing = []
            if not rec_a:
                missing.append(baseline_a_id)
            if not rec_b:
                missing.append(baseline_b_id)
            return {
                "error": f"Baseline(s) not found: {', '.join(missing)}",
                "metric_deltas": {},
                "composite_delta": 0.0,
                "recommendation": "Cannot compare — one or both baselines missing.",
            }

        def _scores(rec: dict) -> dict[str, float]:
            bj = rec.get("baseline_json", {})
            if isinstance(bj, str):
                try:
                    bj = json.loads(bj)
                except Exception:
                    bj = {}
            qs = bj.get("quality_score", {})
            if isinstance(qs, str):
                try:
                    qs = json.loads(qs)
                except Exception:
                    qs = {}
            return qs

        scores_a = _scores(rec_a)
        scores_b = _scores(rec_b)

        metrics = [
            "citation_validity",
            "signal_density",
            "narrative_continuity",
            "brand_voice",
        ]
        metric_deltas: dict[str, float] = {}
        for m in metrics:
            va = float(scores_a.get(m, 0.0) or 0.0)
            vb = float(scores_b.get(m, 0.0) or 0.0)
            metric_deltas[m] = round(vb - va, 4)

        ca = float(scores_a.get("composite_score", 0.0) or 0.0)
        cb = float(scores_b.get("composite_score", 0.0) or 0.0)
        composite_delta = round(cb - ca, 4)

        if composite_delta < -0.05:
            recommendation = f"Baseline B shows a composite drop of {composite_delta} vs Baseline A — regression detected."
        elif composite_delta > 0.05:
            recommendation = f"Baseline B shows a composite improvement of {composite_delta} vs Baseline A."
        else:
            recommendation = "Baselines are comparable — no significant composite change."

        return {
            "baseline_a": {"id": baseline_a_id, "edition_id": rec_a.get("edition_id", "")},
            "baseline_b": {"id": baseline_b_id, "edition_id": rec_b.get("edition_id", "")},
            "metric_deltas": metric_deltas,
            "composite_delta": composite_delta,
            "recommendation": recommendation,
        }

    # ------------------------------------------------------------------
    # Regression History
    # ------------------------------------------------------------------

    def get_regression_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the most recent baseline records as a history log.

        Because regression tests are not individually persisted (they are
        ephemeral analysis), this method returns the list of baselines
        (ordered by creation time descending) as a proxy for the regression
        history timeline.

        Parameters
        ----------
        limit : int
            Max entries to return (default: 20).

        Returns
        -------
        list[dict]
            Recent baseline records as a history log.
        """
        baselines = self._store.list(limit=limit)
        history = []
        for bl in baselines:
            bj = bl.get("baseline_json", {})
            if isinstance(bj, str):
                try:
                    bj = json.loads(bj)
                except Exception:
                    bj = {}
            qs = bj.get("quality_score", {})
            if isinstance(qs, str):
                try:
                    qs = json.loads(qs)
                except Exception:
                    qs = {}
            history.append({
                "baseline_id": bl.get("id", ""),
                "edition_id": bl.get("edition_id", ""),
                "composite_score": qs.get("composite_score", 0.0),
                "created_at": bl.get("created_at", ""),
                "metadata": bj.get("metadata", {}),
            })
        return history
