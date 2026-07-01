"""Tests for src/quality/ — Edition Quality Scorer."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from quality.scorer import QualityScorer
from quality.metrics import QualityMetrics
from quality.baseline import BaselineManager


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


# Patch targets — quality.scorer and quality.baseline each import
# get_connection at module level, so we must patch their local refs.
SCORER_CONN = "quality.scorer.get_connection"
BASELINE_CONN = "quality.baseline.get_connection"


# ── QualityMetrics Tests ─────────────────────────────────────────


class TestCitationValidity:
    def test_perfect_all_verified(self):
        """All claims verified, no hallucination → 1.0."""
        metrics = QualityMetrics()
        score = metrics.citation_validity({
            "valid": True,
            "missing_ids": [],
            "hallucination_count": 0,
            "total_claims": 5,
        })
        assert score == 1.0

    def test_empty_report(self):
        """Empty/None report → 0.0."""
        metrics = QualityMetrics()
        assert metrics.citation_validity({}) == 0.0
        assert metrics.citation_validity(None) == 0.0

    def test_major_hallucination(self):
        """High hallucination count → 0.0."""
        metrics = QualityMetrics()
        score = metrics.citation_validity({
            "valid": False,
            "missing_ids": [],
            "hallucination_count": 4,
            "total_claims": 5,
        })
        assert score == 0.0

    def test_minor_hallucination(self):
        """Minor hallucination → ratio-based score (not clamped)."""
        metrics = QualityMetrics()
        score = metrics.citation_validity({
            "valid": False,
            "missing_ids": [],
            "hallucination_count": 1,
            "total_claims": 5,
        })
        # ratio = 1 - 1/5 = 0.8
        assert score == 0.8

    def test_minor_missing_citations(self):
        """A few missing citations → 0.5."""
        metrics = QualityMetrics()
        score = metrics.citation_validity({
            "valid": False,
            "missing_ids": ["S001"],
            "hallucination_count": 0,
            "total_claims": 5,
        })
        # valid_pct = 1 - 1/5 = 0.8 → 0.5
        assert score == 0.5


class TestSignalDensity:
    def test_high_density(self):
        """More signals than sources → 1.0."""
        metrics = QualityMetrics()
        score = metrics.signal_density(
            [{"id": "s1"}, {"id": "s2"}, {"id": "s3"}, {"id": "s4"}],
            edition_source_count=2,
        )
        assert score == 1.0

    def test_low_density(self):
        """Fewer signals than sources → low score."""
        metrics = QualityMetrics()
        score = metrics.signal_density(
            [{"id": "s1"}],
            edition_source_count=5,
        )
        assert score == 0.2  # 1/5

    def test_no_sources(self):
        """No sources → 0.0."""
        metrics = QualityMetrics()
        score = metrics.signal_density([], edition_source_count=0)
        assert score == 0.0

    def test_no_items(self):
        """No items but 1 source → 0.0."""
        metrics = QualityMetrics()
        score = metrics.signal_density([], edition_source_count=1)
        assert score == 0.0


class TestNarrativeContinuity:
    def test_all_continuing(self):
        """All stories are continuations → 1.0."""
        metrics = QualityMetrics()
        score = metrics.narrative_continuity(
            story_diffs=[{"type": "continued"}],
            trajectories=[
                {"trajectory": "continuing"},
                {"trajectory": "evolving"},
            ],
        )
        assert score == 1.0

    def test_all_new(self):
        """All stories are new → 0.0."""
        metrics = QualityMetrics()
        score = metrics.narrative_continuity(
            story_diffs=[{"type": "new"}],
            trajectories=[
                {"trajectory": "new"},
                {"trajectory": "new"},
            ],
        )
        assert score == 0.0

    def test_mixed_ratio(self):
        """Half continued, half new → 0.5."""
        metrics = QualityMetrics()
        score = metrics.narrative_continuity(
            story_diffs=[{"type": "new"}, {"type": "continued"}],
            trajectories=[
                {"trajectory": "continuing"},
                {"trajectory": "new"},
            ],
        )
        assert score == 0.5

    def test_no_data(self):
        """No story diffs/trajectories → 0.0."""
        metrics = QualityMetrics()
        assert metrics.narrative_continuity(None, None) == 0.0
        assert metrics.narrative_continuity([], []) == 0.0


class TestBrandVoice:
    def test_strong_match(self):
        """Output text with many brand phrases → near 1.0."""
        metrics = QualityMetrics()
        score = metrics.brand_voice(
            "Here's what we've curated for this edition. Dive into the insights. "
            "Stay tuned for exclusive content. Key takeaways: ..."
        )
        assert score > 0.5

    def test_no_match(self):
        """Generic text with no brand patterns → low score."""
        metrics = QualityMetrics()
        score = metrics.brand_voice("The sky is blue. Water is wet. Cats are nice.")
        assert score < 0.5

    def test_empty_text(self):
        """Empty text → 0.0."""
        metrics = QualityMetrics()
        assert metrics.brand_voice("") == 0.0

    def test_custom_patterns(self):
        """Custom brand patterns applied."""
        metrics = QualityMetrics()
        score = metrics.brand_voice(
            "Our product rocks never settle evolve faster",
            brand_patterns=[r"\brocks\b", r"\bnever settle\b", r"\bevolve\b"],
        )
        assert score > 0.5


# ── QualityScorer Tests ──────────────────────────────────────────


class TestCompositeWeighted:
    def test_weighted_average(self):
        """Composite is correctly weighted: 0.35*citation + 0.25*signal + 0.25*narrative + 0.15*brand."""
        with patch(SCORER_CONN, return_value=_mock_conn()):
            scorer = QualityScorer()
            # Override metrics to return fixed values
            scorer.metrics.citation_validity = lambda r: 0.9
            scorer.metrics.signal_density = lambda i, c: 0.8
            scorer.metrics.narrative_continuity = lambda d, t: 0.7
            scorer.metrics.brand_voice = lambda t, p=None: 0.6
            result = scorer.score_edition(
                edition_id="test-ed",
                citation_report={"valid": True},
                signal_data={"items": [{}], "source_count": 1},
                narrative_data={"story_diffs": [{}], "trajectories": [{"trajectory": "continuing"}]},
                brand_data={"output_text": "test"},
            )
            expected = 0.35 * 0.9 + 0.25 * 0.8 + 0.25 * 0.7 + 0.15 * 0.6
            assert result["composite_score"] == round(expected, 4)
            assert result["citation_validity"] == 0.9
            assert result["signal_density"] == 0.8
            assert result["narrative_continuity"] == 0.7
            assert result["brand_voice"] == 0.6
            assert result["edition_id"] == "test-ed"

    def test_score_edition_stores_in_db(self):
        """Score is persisted to the database."""
        mock_conn = _mock_conn()
        with patch(SCORER_CONN, return_value=mock_conn):
            scorer = QualityScorer()
            scorer.metrics.citation_validity = lambda r: 1.0
            scorer.metrics.signal_density = lambda i, c: 1.0
            scorer.metrics.narrative_continuity = lambda d, t: 1.0
            scorer.metrics.brand_voice = lambda t, p=None: 1.0
            result = scorer.score_edition("test-ed-store", {}, {}, {}, {})

            # Verify INSERT was called
            insert_calls = [
                c for c in mock_conn.execute.call_args_list
                if "INSERT OR REPLACE INTO wf_quality_scores" in str(c)
            ]
            assert len(insert_calls) >= 1


class TestGetScore:
    def test_get_score_found(self):
        """get_score returns stored score."""
        mock_conn = _mock_conn()
        mock_conn.execute.return_value.fetchone.return_value = _make_row(
            id="s1", edition_id="ed-001", run_id="",
            citation_validity=0.9, signal_density=0.8,
            narrative_continuity=0.7, brand_voice=0.6,
            composite_score=0.78, scored_at="2026-01-01T00:00:00",
        )
        with patch(SCORER_CONN, return_value=mock_conn):
            scorer = QualityScorer()
            score = scorer.get_score("ed-001")
            assert score is not None
            assert score["composite_score"] == 0.78

    def test_get_score_not_found(self):
        """get_score returns None when not found."""
        mock_conn = _mock_conn()
        mock_conn.execute.return_value.fetchone.return_value = None
        with patch(SCORER_CONN, return_value=mock_conn):
            scorer = QualityScorer()
            score = scorer.get_score("non-existent")
            assert score is None


class TestTrendQuery:
    def test_trend_multiple_editions(self):
        """get_trend returns multiple editions."""
        mock_conn = _mock_conn()
        mock_conn.execute.return_value.fetchall.return_value = [
            _make_row(id="s1", edition_id="ed-003", composite_score=0.9),
            _make_row(id="s2", edition_id="ed-002", composite_score=0.8),
            _make_row(id="s3", edition_id="ed-001", composite_score=0.7),
        ]
        with patch(SCORER_CONN, return_value=mock_conn):
            scorer = QualityScorer()
            trend = scorer.get_trend(limit=3)
            assert len(trend) == 3
            assert trend[0]["edition_id"] == "ed-003"

    def test_trend_empty(self):
        """get_trend returns empty list when no scores exist."""
        mock_conn = _mock_conn()
        mock_conn.execute.return_value.fetchall.return_value = []
        with patch(SCORER_CONN, return_value=mock_conn):
            scorer = QualityScorer()
            trend = scorer.get_trend()
            assert trend == []


class TestRegressionDetection:
    def _make_side_effect(self):
        def side_effect(sql, *args, **kwargs):
            sql_str = str(sql) if sql else ""
            # Check bound parameters to differentiate editions
            # args[0] is the params tuple passed to execute()
            params = args[0] if args else ()
            edition_param = params[0] if params else ""
            if edition_param == "ed-002":
                return MagicMock(fetchone=lambda: _make_row(
                    id="s1", edition_id="ed-002",
                    citation_validity=0.5, signal_density=0.5,
                    narrative_continuity=0.5, brand_voice=0.5,
                    composite_score=0.5, scored_at="",
                ))
            elif edition_param == "ed-001":
                return MagicMock(fetchone=lambda: _make_row(
                    id="s2", edition_id="ed-001",
                    citation_validity=0.9, signal_density=0.9,
                    narrative_continuity=0.9, brand_voice=0.9,
                    composite_score=0.9, scored_at="",
                ))
            return MagicMock(fetchone=lambda: None)
        return side_effect

    def test_regression_detected(self):
        """Score drop > 5% flagged as regression."""
        mock_conn = _mock_conn()
        mock_conn.execute.side_effect = self._make_side_effect()
        with patch(SCORER_CONN, return_value=mock_conn):
            scorer = QualityScorer()
            result = scorer.check_regression("ed-002", "ed-001")
            assert result["regressed"] is True
            assert result["score_delta"] == -0.4
            assert len(result["regressed_metrics"]) == 4

    def _make_improved_side_effect(self):
        def side_effect(sql, *args, **kwargs):
            sql_str = str(sql) if sql else ""
            params = args[0] if args else ()
            edition_param = params[0] if params else ""
            if edition_param == "ed-002":
                return MagicMock(fetchone=lambda: _make_row(
                    id="s1", edition_id="ed-002",
                    citation_validity=0.9, signal_density=0.9,
                    narrative_continuity=0.9, brand_voice=0.9,
                    composite_score=0.9, scored_at="",
                ))
            elif edition_param == "ed-001":
                return MagicMock(fetchone=lambda: _make_row(
                    id="s2", edition_id="ed-001",
                    citation_validity=0.8, signal_density=0.8,
                    narrative_continuity=0.8, brand_voice=0.8,
                    composite_score=0.8, scored_at="",
                ))
            return MagicMock(fetchone=lambda: None)
        return side_effect

    def test_no_regression(self):
        """Stable or improved score → no regression."""
        mock_conn = _mock_conn()
        mock_conn.execute.side_effect = self._make_improved_side_effect()
        with patch(SCORER_CONN, return_value=mock_conn):
            scorer = QualityScorer()
            result = scorer.check_regression("ed-002", "ed-001")
            assert result["regressed"] is False
            assert result["score_delta"] == 0.1


# ── BaselineManager Tests ────────────────────────────────────────


class TestBaselineCreateGet:
    def test_create_baseline(self):
        """Creating a baseline returns a record with ID."""
        mock_conn = _mock_conn()
        with patch(BASELINE_CONN, return_value=mock_conn):
            mgr = BaselineManager()
            result = mgr.create(
                edition_id="ed-001",
                quality_score={"composite_score": 0.85, "citation_validity": 0.9},
                metadata={"label": "first edition"},
            )
            assert result["edition_id"] == "ed-001"
            assert "id" in result
            assert result["baseline_json"]["quality_score"]["composite_score"] == 0.85

    def test_get_baseline(self):
        """Retrieve a baseline by ID returns the stored record."""
        mock_conn = _mock_conn()
        baseline_id = "bl-001"
        mock_conn.execute.return_value.fetchone.return_value = _make_row(
            id=baseline_id, edition_id="ed-001", run_id="",
            baseline_json=json.dumps({
                "edition_id": "ed-001",
                "quality_score": {"composite_score": 0.85},
                "metadata": {},
            }),
            created_at="2026-01-01T00:00:00",
        )
        with patch(BASELINE_CONN, return_value=mock_conn):
            mgr = BaselineManager()
            result = mgr.get(baseline_id)
            assert result is not None
            assert result["id"] == baseline_id
            assert result["baseline_json"]["quality_score"]["composite_score"] == 0.85

    def test_get_latest_no_baselines(self):
        """get_latest returns None when no baselines exist."""
        mock_conn = _mock_conn()
        mock_conn.execute.return_value.fetchone.return_value = None
        with patch(BASELINE_CONN, return_value=mock_conn):
            mgr = BaselineManager()
            result = mgr.get_latest()
            assert result is None


class TestBaselineCompare:
    def test_compare_improvement(self):
        """Compare detects improvement over baseline."""
        def score_side_effect(sql, *args, **kwargs):
            sql_str = str(sql) if sql else ""
            params = args[0] if args else ()
            if "wf_quality_scores" in sql_str and params:
                ed_id = params[0] if params else ""
                if ed_id == "ed-002":
                    return MagicMock(fetchone=lambda: _make_row(
                        id="s1", edition_id="ed-002", run_id="",
                        citation_validity=0.9, signal_density=0.9,
                        narrative_continuity=0.9, brand_voice=0.9,
                        composite_score=0.9, scored_at="",
                    ))
                return MagicMock(fetchone=lambda: _make_row(
                    id="s2", edition_id="ed-001", run_id="",
                    citation_validity=0.7, signal_density=0.7,
                    narrative_continuity=0.7, brand_voice=0.7,
                    composite_score=0.7, scored_at="",
                ))
            elif "wf_quality_baselines" in sql_str:
                return MagicMock(fetchone=lambda: _make_row(
                    id="bl-001", edition_id="ed-001", run_id="",
                    baseline_json=json.dumps({
                        "edition_id": "ed-001",
                        "quality_score": {
                            "citation_validity": 0.7, "signal_density": 0.7,
                            "narrative_continuity": 0.7, "brand_voice": 0.7,
                            "composite_score": 0.7,
                        },
                        "metadata": {},
                    }),
                    created_at="",
                ))
            return MagicMock(fetchone=lambda: None)

        mock_conn = _mock_conn()
        mock_conn.execute.side_effect = score_side_effect
        with patch(SCORER_CONN, return_value=mock_conn), \
             patch(BASELINE_CONN, return_value=mock_conn):
            mgr = BaselineManager()
            result = mgr.compare("ed-002", "bl-001")
            assert result["regressed"] is False
            assert "improved" in result["recommendation"].lower()


# ── Step Type Routing Tests ──────────────────────────────────────


class TestQualityStepType:
    def test_score_edition_quality_step_type_routes(self):
        """score_edition_quality step type returns quality score result."""
        from workflow_executor import WorkflowExecutor
        from executor_context import ExecutorContext

        mock_db = MagicMock()
        mock_tracer = MagicMock()
        mock_watchdog = MagicMock()
        mock_metrics = MagicMock()

        executor = WorkflowExecutor.__new__(WorkflowExecutor)
        executor.db = mock_db
        executor.tracer = mock_tracer
        executor.watchdog = mock_watchdog
        executor.metrics = mock_metrics

        step = {
            "id": "step-1",
            "step_type": "score_edition_quality",
            "config_json": json.dumps({
                "edition_id": "ed-test",
                "citation_report": {"valid": True, "hallucination_count": 0, "total_claims": 1},
                "signal_data": {"items": [{"id": "s1"}], "source_count": 1},
                "narrative_data": {"story_diffs": [], "trajectories": []},
                "brand_data": {"output_text": "test"},
            }),
        }

        mock_exec_ctx = MagicMock(spec=ExecutorContext)
        mock_exec_ctx.step_results = {}

        with patch(SCORER_CONN, return_value=_mock_conn()):
            result = executor._execute_score_edition_quality_step(step, mock_exec_ctx)
            assert "composite_score" in result.get("quality_score", {})

    def test_check_quality_regression_step_type_routes(self):
        """check_quality_regression step type returns regression result."""
        from workflow_executor import WorkflowExecutor
        from executor_context import ExecutorContext

        mock_db = MagicMock()
        mock_tracer = MagicMock()
        mock_watchdog = MagicMock()
        mock_metrics = MagicMock()

        executor = WorkflowExecutor.__new__(WorkflowExecutor)
        executor.db = mock_db
        executor.tracer = mock_tracer
        executor.watchdog = mock_watchdog
        executor.metrics = mock_metrics

        step = {
            "id": "step-2",
            "step_type": "check_quality_regression",
            "config_json": json.dumps({
                "edition_id": "ed-002",
                "baseline_id": "bl-001",
            }),
        }

        mock_exec_ctx = MagicMock(spec=ExecutorContext)

        with patch(SCORER_CONN, return_value=_mock_conn()):
            result = executor._execute_check_quality_regression_step(step, mock_exec_ctx)
            assert "regressed" in result.get("regression", {})
