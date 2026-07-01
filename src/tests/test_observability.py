"""Tests for the Observability Framework — metrics, watchdog, health checks."""

import threading
import time

from observability.metrics import MetricsCollector
from observability.watchdog import StepWatchdog
from observability.health import HealthChecker


# ── Metrics Tests ────────────────────────────────────────────────────


def test_metrics_record_and_query():
    """Record step durations and verify they are stored."""
    m = MetricsCollector()
    # Reset singleton state for clean test
    m._step_durations.clear()
    m._total_counts.clear()
    m._error_counts.clear()
    m._token_counts.clear()

    m.record_step_duration("step-fetch", 100.0, success=True)
    m.record_step_duration("step-fetch", 200.0, success=True)
    m.record_step_duration("step-fetch", 50.0, success=False)

    assert len(m._step_durations["step-fetch"]) == 3
    assert m._total_counts["step-fetch"] == 3
    assert m._error_counts["step-fetch"] == 1
    assert abs(m.get_step_error_rate("step-fetch") - (1 / 3)) < 0.001


def test_metrics_p50_p95():
    """Verify P50 and P95 latency calculations."""
    m = MetricsCollector()
    m._step_durations.clear()
    m._total_counts.clear()
    m._error_counts.clear()
    m._token_counts.clear()

    durations = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    for d in durations:
        m.record_step_duration("step-analyze", float(d), success=True)

    # Sorted: [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    # P50 for 10 items: average of 5th (50) and 6th (60) = 55
    # P95: int(10 * 0.95) = 9 -> sorted_d[9] = 100
    p50 = m.get_step_latency_p50("step-analyze")
    p95 = m.get_step_latency_p95("step-analyze")

    assert p50 == 55.0, f"Expected 55.0, got {p50}"
    assert p95 == 100.0, f"Expected 100.0, got {p95}"


def test_metrics_prometheus_format():
    """Verify Prometheus export format."""
    m = MetricsCollector()
    m._step_durations.clear()
    m._total_counts.clear()
    m._error_counts.clear()
    m._token_counts.clear()

    m.record_step_duration("step-fetch", 150.0, success=True)
    m.record_step_duration("step-fetch", 250.0, success=False)
    m.record_token_count("gpt-4", 100, 50)

    output = m.export_prometheus()

    assert "esam_step_duration_ms" in output
    assert "esam_step_error_rate" in output
    assert "esam_step_latency_p50" in output
    assert "esam_step_latency_p95" in output
    assert "esam_token_count_total" in output
    assert "esam_queue_depth" in output
    assert 'step_type="step-fetch"' in output
    assert 'model="gpt-4"' in output
    assert 'type="input"' in output
    assert 'type="output"' in output


def test_metrics_singleton():
    """Verify MetricsCollector is a singleton."""
    m1 = MetricsCollector()
    m2 = MetricsCollector()
    assert m1 is m2


# ── Watchdog Tests ──────────────────────────────────────────────────


def test_watchdog_track_and_check():
    """Track a step and verify it's not hung."""
    w = StepWatchdog()
    w._watched.clear()

    w.start_watchdog(timeout_seconds=300, poll_interval=60)

    w.watch_step("step-1", timeout=300)

    # Step just started, shouldn't be hung
    hung = w.check_hung_steps()
    assert "step-1" not in hung

    watched = w.get_all_watched()
    assert "step-1" in watched

    w.cancel_watch("step-1")
    hung = w.check_hung_steps()
    assert "step-1" not in hung

    w.stop_watchdog()


def test_watchdog_hung_detection():
    """Verify that steps exceeding their timeout are detected as hung."""
    w = StepWatchdog()
    w._watched.clear()

    w.start_watchdog(timeout_seconds=300, poll_interval=60)

    # Set an already-expired step by manipulating internal state
    import time as _time
    w._watched["step-hung"] = {
        "step_id": "step-hung",
        "started_at": _time.time() - 600,  # Started 10 minutes ago
        "timeout": 5,  # 5 second timeout
    }

    hung = w.check_hung_steps()
    assert "step-hung" in hung

    report = w.get_hung_step_report()
    assert len(report) >= 1
    assert report[0]["step_id"] == "step-hung"
    assert report[0]["elapsed_seconds"] > 5

    w.cancel_watch("step-hung")
    hung = w.check_hung_steps()
    assert "step-hung" not in hung

    w.stop_watchdog()


# ── Health Tests ─────────────────────────────────────────────────────


def test_health_liveness():
    """Liveness check returns ok with uptime and version."""
    hc = HealthChecker(version="0.2.0")
    result = hc.check_liveness()
    assert result["status"] == "ok"
    assert result["version"] == "0.2.0"
    assert isinstance(result["uptime"], float)
    assert result["uptime"] >= 0


def test_health_readiness_fail():
    """Readiness check may return degraded when DB is unavailable."""
    hc = HealthChecker()
    result = hc.check_readiness()
    # At minimum it should have the expected structure
    assert "status" in result
    assert "db_connected" in result
    assert "fetcher_ok" in result
    assert "parser_ok" in result
    assert result["status"] in ("ok", "degraded")


def test_health_diagnostics():
    """Diagnostics runs all checks and returns results."""
    hc = HealthChecker()
    results = hc.run_diagnostics()

    assert isinstance(results, list)
    assert len(results) >= 4  # liveness, readiness, metrics, watchdog

    check_names = [r["check"] for r in results]
    assert "liveness" in check_names
    assert "readiness" in check_names
    assert "metrics" in check_names
    assert "watchdog" in check_names

    for r in results:
        assert "check" in r
        assert "status" in r
        assert "details" in r
        assert r["status"] in ("ok", "degraded")


def test_health_step_health():
    """Step health reports on a specific step's metrics."""
    m = MetricsCollector()
    m._step_durations.clear()
    m._total_counts.clear()
    m._error_counts.clear()
    m._token_counts.clear()

    hc = HealthChecker(metrics=m)

    # No data yet
    result = hc.check_step_health("nonexistent")
    assert result["step_id"] == "nonexistent"
    assert result["success_rate"] == 1.0
    assert result["avg_latency_ms"] == 0.0
    assert result["total_executions"] == 0

    # Record some data
    m.record_step_duration("step-test", 100.0, success=True)
    m.record_step_duration("step-test", 200.0, success=False)

    result = hc.check_step_health("step-test")
    assert result["total_executions"] == 2
    assert result["total_errors"] == 1
    assert result["avg_latency_ms"] == 150.0
    assert result["success_rate"] == 0.5
