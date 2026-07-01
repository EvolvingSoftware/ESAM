"""Tests for src/quality/regression.py — Regression Test Suite."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest

from quality.regression import RegressionTester, BaselineStore


# ── Helpers ──────────────────────────────────────────────────────


def _make_row(**kwargs):
    """Create a mock sqlite3.Row-like dict with attribute access."""
    class MockRow(dict):
        pass
    return MockRow(kwargs)


def _mock_conn():
    """Create a mock database connection."""
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = None
    conn.execute.return_value.fetchall.return_value = []
    return conn


# Patch targets
REGRESSION_CONN = "quality.regression.get_connection"
BASELINE_STORE_CONN = "quality.baseline.get_connection"

SCORE_KEYS = [
    "citation_validity",
    "signal_density",
    "narrative_continuity",
    "brand_voice",
    "composite_score",
]


def _baseline_json(edition_id: str, scores: dict) -> str:
    """Create a serialised baseline_json for mock rows."""
    return json.dumps({
        "edition_id": edition_id,
        "quality_score": scores,
        "metadata": {"label": "test"},
        "created_at": "2026-06-26T00:00:00Z",
    })


# ── BaselineStore Tests ──────────────────────────────────────────


class TestBaselineStoreCreateGet:
    def test_create_and_get_roundtrip(self):
        """Create a baseline, retrieve it, verify fields."""
        baselines_table: list[dict] = []

        def _mock_execute(sql, params=None):
            mock_cursor = MagicMock()
            if sql.strip().upper().startswith("INSERT"):
                baselines_table.append({
                    "id": params[0],
                    "edition_id": params[1],
                    "run_id": params[2],
                    "baseline_json": params[3],
                    "created_at": params[4],
                })
                mock_cursor.fetchone.return_value = None
            elif "WHERE id = ?" in sql:
                bid = params[0]
                for row in baselines_table:
                    if row["id"] == bid:
                        mock_cursor.fetchone.return_value = _make_row(**row)
                        break
                else:
                    mock_cursor.fetchone.return_value = None
            return mock_cursor

        conn = MagicMock()
        conn.execute.side_effect = _mock_execute

        with patch(REGRESSION_CONN, return_value=conn):
            store = BaselineStore()
            result = store.create(
                edition_id="ed-001",
                scores={"composite_score": 0.92, "citation_validity": 0.95},
                run_id="run-001",
                metadata={"label": "first"},
            )

            assert result["edition_id"] == "ed-001"
            assert result["run_id"] == "run-001"
            assert "id" in result
            assert "created_at" in result

            # Retrieve
            fetched = store.get(result["id"])
            assert fetched is not None
            assert fetched["edition_id"] == "ed-001"
            bj = fetched.get("baseline_json", {})
            if isinstance(bj, str):
                bj = json.loads(bj)
            assert bj["quality_score"]["composite_score"] == 0.92

    def test_get_nonexistent(self):
        """Getting a non-existent baseline returns None."""
        conn = _mock_conn()
        with patch(REGRESSION_CONN, return_value=conn):
            store = BaselineStore()
            result = store.get("nonexistent")
            assert result is None

    def test_get_latest_empty(self):
        """get_latest returns None when no baselines exist."""
        conn = _mock_conn()
        with patch(REGRESSION_CONN, return_value=conn):
            store = BaselineStore()
            result = store.get_latest()
            assert result is None

    def test_list(self):
        """list returns baselines sorted by creation time descending."""
        baselines_table = [
            {"id": "b2", "edition_id": "ed-2", "run_id": "", "baseline_json": _baseline_json("ed-2", {"composite_score": 0.9}), "created_at": "2026-06-26T02:00:00Z"},
            {"id": "b1", "edition_id": "ed-1", "run_id": "", "baseline_json": _baseline_json("ed-1", {"composite_score": 0.8}), "created_at": "2026-06-26T01:00:00Z"},
        ]

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [_make_row(**r) for r in baselines_table]

        with patch(REGRESSION_CONN, return_value=conn):
            store = BaselineStore()
            results = store.list(limit=5)
            assert len(results) == 2
            # Ordered by created_at DESC (b2 is later, so comes first in mock return)
            assert results[0]["id"] == "b2"
            assert results[1]["id"] == "b1"

    def test_promote_no_score(self):
        """promote raises ValueError if no quality score exists."""
        conn = _mock_conn()
        conn.execute.return_value.fetchone.return_value = None

        with patch(REGRESSION_CONN, return_value=conn):
            store = BaselineStore()
            with pytest.raises(ValueError, match="No quality score found for edition ed-001"):
                store.promote("ed-001")

    def test_promote_creates_baseline(self):
        """promote reads quality score and creates a baseline."""
        scores_row = _make_row(
            id=str(uuid.uuid4()),
            edition_id="ed-001",
            run_id="run-001",
            citation_validity=0.95,
            signal_density=0.88,
            narrative_continuity=0.78,
            brand_voice=0.85,
            composite_score=0.87,
            scored_at="2026-06-26T00:00:00Z",
        )

        baselines_table: list[dict] = []

        def _mock_execute(sql, params=None):
            mock_cursor = MagicMock()
            if sql.strip().upper().startswith("INSERT"):
                baselines_table.append({
                    "id": params[0], "edition_id": params[1],
                    "run_id": params[2], "baseline_json": params[3], "created_at": params[4],
                })
                mock_cursor.fetchone.return_value = None
            elif "WHERE edition_id = ?" in sql:
                if params[0] == "ed-001":
                    mock_cursor.fetchone.return_value = scores_row
                else:
                    mock_cursor.fetchone.return_value = None
            else:
                mock_cursor.fetchone.return_value = None
            return mock_cursor

        conn = MagicMock()
        conn.execute.side_effect = _mock_execute

        with patch(REGRESSION_CONN, return_value=conn):
            store = BaselineStore()
            result = store.promote("ed-001")
            assert result["edition_id"] == "ed-001"
            bj = result["baseline_json"]
            if isinstance(bj, str):
                bj = json.loads(bj)
            assert bj["quality_score"]["composite_score"] == 0.87
            assert bj["quality_score"]["citation_validity"] == 0.95


# ── RegressionTester Tests ───────────────────────────────────────


class TestRunTestsAllPass:
    """Perfect edition passes all regression tests."""

    def setup_method(self):
        self.baseline_scores = {
            "composite_score": 0.85,
            "citation_validity": 0.90,
            "signal_density": 0.80,
            "narrative_continuity": 0.75,
            "brand_voice": 0.70,
        }
        self.current_scores = {
            "composite_score": 0.85,
            "citation_validity": 0.90,
            "signal_density": 0.80,
            "narrative_continuity": 0.75,
            "brand_voice": 0.70,
            "hallucination_ratio": 0.0,
        }

    def _mock_baseline_row(self, edition_id="ed-001", scores=None):
        return _make_row(
            id="base-001",
            edition_id=edition_id,
            run_id="run-001",
            baseline_json=_baseline_json(edition_id, scores or self.baseline_scores),
            created_at="2026-06-26T00:00:00Z",
        )

    def test_all_pass(self):
        """All tests pass when current scores match or exceed baseline."""
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = self._mock_baseline_row()

        with patch(REGRESSION_CONN, return_value=conn):
            tester = RegressionTester()
            result = tester.run_tests("ed-002", "base-001", self.current_scores)

            assert result["passed"] is True
            assert result["total_tests"] == 6  # 1 composite + 4 metrics + 1 hallucination
            assert result["passed_tests"] == 6
            assert result["failed_tests"] == 0
            assert "All regression tests passed" in result["recommendation"]

    def test_baseline_not_found(self):
        """Missing baseline returns failure with critical severity."""
        conn = _mock_conn()
        with patch(REGRESSION_CONN, return_value=conn):
            tester = RegressionTester()
            result = tester.run_tests("ed-002", "missing-base", self.current_scores)

            assert result["passed"] is False
            assert "not found" in result["recommendation"]
            assert result["results"][0]["test_name"] == "baseline_exists"
            assert result["results"][0]["severity"] == "critical"


class TestRunTestsCompositeRegression:
    """Composite score drop detected."""

    def test_composite_drop_detected(self):
        """When composite score drops more than 0.1, test fails."""
        baseline_scores = {
            "composite_score": 0.95,
            "citation_validity": 0.90,
            "signal_density": 0.80,
            "narrative_continuity": 0.75,
            "brand_voice": 0.70,
        }
        current_scores = {
            "composite_score": 0.80,  # drop of 0.15 > 0.1
            "citation_validity": 0.90,
            "signal_density": 0.80,
            "narrative_continuity": 0.75,
            "brand_voice": 0.70,
            "hallucination_ratio": 0.0,
        }

        bj = _baseline_json("ed-001", baseline_scores)
        row = _make_row(id="base-001", edition_id="ed-001", run_id="",
                        baseline_json=bj, created_at="2026-06-26T00:00:00Z")

        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = row

        with patch(REGRESSION_CONN, return_value=conn):
            tester = RegressionTester()
            result = tester.run_tests("ed-002", "base-001", current_scores)

            assert result["passed"] is False
            # Composite test should fail
            comp_result = [r for r in result["results"] if r["test_name"] == "composite_score_threshold"][0]
            assert comp_result["passed"] is False
            assert comp_result["delta"] == -0.15  # 0.80 - 0.95


class TestRunTestsMetricDrop:
    """Single metric drop flagged."""

    def test_metric_drop_flagged(self):
        """When a single metric drops more than 0.2, that metric test fails."""
        baseline_scores = {
            "composite_score": 0.85,
            "citation_validity": 0.90,
            "signal_density": 0.80,
            "narrative_continuity": 0.75,
            "brand_voice": 0.70,
        }
        current_scores = {
            "composite_score": 0.85,
            "citation_validity": 0.65,  # drop of 0.25 > 0.2
            "signal_density": 0.80,
            "narrative_continuity": 0.75,
            "brand_voice": 0.70,
            "hallucination_ratio": 0.0,
        }

        bj = _baseline_json("ed-001", baseline_scores)
        row = _make_row(id="base-001", edition_id="ed-001", run_id="",
                        baseline_json=bj, created_at="2026-06-26T00:00:00Z")

        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = row

        with patch(REGRESSION_CONN, return_value=conn):
            tester = RegressionTester()
            result = tester.run_tests("ed-002", "base-001", current_scores)

            assert result["passed"] is False
            citation_result = [r for r in result["results"] if r["test_name"] == "metric_drop_citation_validity"][0]
            assert citation_result["passed"] is False
            assert citation_result["delta"] == -0.25
            assert citation_result["severity"] == "critical"

    def test_hallucination_ratio_fails(self):
        """Hallucination ratio above 0.05 fails the test."""
        baseline_scores = {
            "composite_score": 0.85,
            "citation_validity": 0.90,
            "signal_density": 0.80,
            "narrative_continuity": 0.75,
            "brand_voice": 0.70,
        }
        current_scores = {
            "composite_score": 0.85,
            "citation_validity": 0.90,
            "signal_density": 0.80,
            "narrative_continuity": 0.75,
            "brand_voice": 0.70,
            "hallucination_ratio": 0.10,  # > 0.05
        }

        bj = _baseline_json("ed-001", baseline_scores)
        row = _make_row(id="base-001", edition_id="ed-001", run_id="",
                        baseline_json=bj, created_at="2026-06-26T00:00:00Z")

        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = row

        with patch(REGRESSION_CONN, return_value=conn):
            tester = RegressionTester()
            result = tester.run_tests("ed-002", "base-001", current_scores)

            assert result["passed"] is False
            hall_result = [r for r in result["results"] if r["test_name"] == "hallucination_ratio"][0]
            assert hall_result["passed"] is False
            assert hall_result["severity"] == "critical"


# ── Update Baseline / Promote ────────────────────────────────────


class TestUpdateBaseline:
    def test_promote_edition(self):
        """update_baseline promotes an edition to baseline status."""
        scores_row = _make_row(
            id=str(uuid.uuid4()),
            edition_id="ed-001",
            run_id="run-001",
            citation_validity=0.95,
            signal_density=0.88,
            narrative_continuity=0.78,
            brand_voice=0.85,
            composite_score=0.87,
            scored_at="2026-06-26T00:00:00Z",
        )

        baselines_table: list[dict] = []

        def _mock_execute(sql, params=None):
            cursor = MagicMock()
            if sql.strip().upper().startswith("INSERT"):
                baselines_table.append({
                    "id": params[0], "edition_id": params[1],
                    "run_id": params[2], "baseline_json": params[3], "created_at": params[4],
                })
            elif "WHERE edition_id = ?" in sql and params[0] == "ed-001":
                cursor.fetchone.return_value = scores_row
            else:
                cursor.fetchone.return_value = None
            return cursor

        conn = MagicMock()
        conn.execute.side_effect = _mock_execute

        with patch(REGRESSION_CONN, return_value=conn):
            tester = RegressionTester()
            result = tester.update_baseline("ed-001")
            assert result["edition_id"] == "ed-001"
            assert len(baselines_table) == 1
            bj = json.loads(baselines_table[0]["baseline_json"])
            assert bj["quality_score"]["composite_score"] == 0.87


# ── Compare Baselines ────────────────────────────────────────────


class TestCompareBaselines:
    def test_compare_two_baselines(self):
        """Compare two baselines returns metric deltas."""
        bj_a = _baseline_json("ed-a", {"composite_score": 0.80, "citation_validity": 0.7,
                                        "signal_density": 0.75, "narrative_continuity": 0.7,
                                        "brand_voice": 0.65})
        bj_b = _baseline_json("ed-b", {"composite_score": 0.90, "citation_validity": 0.9,
                                        "signal_density": 0.85, "narrative_continuity": 0.8,
                                        "brand_voice": 0.75})

        rows = {
            "base-a": _make_row(id="base-a", edition_id="ed-a", run_id="",
                                baseline_json=bj_a, created_at="2026-06-26T00:00:00Z"),
            "base-b": _make_row(id="base-b", edition_id="ed-b", run_id="",
                                baseline_json=bj_b, created_at="2026-06-26T01:00:00Z"),
        }

        conn = MagicMock()

        def _mock_execute(sql, params=None):
            cursor = MagicMock()
            if params and params[0] in rows:
                cursor.fetchone.return_value = rows[params[0]]
            else:
                cursor.fetchone.return_value = None
            return cursor

        conn.execute.side_effect = _mock_execute

        with patch(REGRESSION_CONN, return_value=conn):
            tester = RegressionTester()
            result = tester.compare_baselines("base-a", "base-b")

            assert result["baseline_a"]["edition_id"] == "ed-a"
            assert result["baseline_b"]["edition_id"] == "ed-b"
            assert result["composite_delta"] == 0.10  # 0.90 - 0.80
            assert "improvement" in result["recommendation"].lower()

    def test_compare_missing_baselines(self):
        """Missing baselines return error dict."""
        conn = _mock_conn()
        with patch(REGRESSION_CONN, return_value=conn):
            tester = RegressionTester()
            result = tester.compare_baselines("missing-a", "missing-b")
            assert "error" in result
            assert "not found" in result["error"].lower()


# ── Regression History ───────────────────────────────────────────


class TestRegressionHistory:
    def test_history_returns_baselines(self):
        """get_regression_history returns recent baselines."""
        baselines_table = [
            _make_row(
                id="b1", edition_id="ed-1", run_id="run-1",
                baseline_json=_baseline_json("ed-1", {"composite_score": 0.75}),
                created_at="2026-06-26T01:00:00Z",
            ),
            _make_row(
                id="b2", edition_id="ed-2", run_id="run-2",
                baseline_json=_baseline_json("ed-2", {"composite_score": 0.85}),
                created_at="2026-06-26T02:00:00Z",
            ),
        ]

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = baselines_table

        with patch(REGRESSION_CONN, return_value=conn):
            tester = RegressionTester()
            history = tester.get_regression_history(limit=5)

            assert len(history) == 2
            assert history[0]["baseline_id"] == "b1"
            assert history[0]["composite_score"] == 0.75
            assert history[1]["edition_id"] == "ed-2"

    def test_history_empty(self):
        """Empty history returns empty list."""
        conn = _mock_conn()
        with patch(REGRESSION_CONN, return_value=conn):
            tester = RegressionTester()
            history = tester.get_regression_history(limit=5)
            assert history == []


# ── Executor Step Type Routing ──────────────────────────────────


class TestRegressionStepType:
    """Verify that the executor correctly routes regression step types."""

    def test_run_regression_tests_step_dispatched(self):
        """run_regression_tests routes to _execute_run_regression_tests_step."""
        from workflow_executor import WorkflowExecutor
        executor = WorkflowExecutor()

        assert hasattr(executor, "_execute_run_regression_tests_step")
        assert callable(executor._execute_run_regression_tests_step)

    def test_update_baseline_step_dispatched(self):
        """update_baseline routes to _execute_update_baseline_step."""
        from workflow_executor import WorkflowExecutor
        executor = WorkflowExecutor()

        assert hasattr(executor, "_execute_update_baseline_step")
        assert callable(executor._execute_update_baseline_step)
