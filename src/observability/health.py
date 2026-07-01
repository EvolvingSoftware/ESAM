"""Health checking — liveness, readiness, diagnostics."""

from __future__ import annotations

import time
from typing import Any

from observability.metrics import MetricsCollector
from observability.watchdog import StepWatchdog


class HealthChecker:
    """Health checker with liveness, readiness, and step-level diagnostics.

    Usage::

        hc = HealthChecker()
        hc.check_liveness()  # {"status": "ok", "uptime": ..., "version": "0.2.0"}
        hc.check_readiness()  # {"status": "ok", "db_connected": True, ...}
    """

    def __init__(
        self,
        version: str = "0.2.0",
        metrics: MetricsCollector | None = None,
        watchdog: StepWatchdog | None = None,
    ) -> None:
        self._version = version
        self._start_time = time.time()
        self._metrics = metrics or MetricsCollector()
        self._watchdog = watchdog or StepWatchdog()

    # --- Liveness ---

    def check_liveness(self) -> dict[str, Any]:
        """Lightweight liveness check.

        Returns:
            dict with keys: status, uptime (seconds), version.
        """
        return {
            "status": "ok",
            "uptime": round(time.time() - self._start_time, 2),
            "version": self._version,
        }

    # --- Readiness ---

    def check_readiness(self) -> dict[str, Any]:
        """Readiness check that verifies external dependencies.

        Checks:
        - Database connectivity (db_connected)
        - Fetcher availability (fetcher_ok)
        - Parser availability (parser_ok)

        Returns:
            dict with keys: status, db_connected, fetcher_ok, parser_ok.
        """
        checks: dict[str, bool] = {
            "db_connected": self._check_db(),
            "fetcher_ok": self._check_fetcher(),
            "parser_ok": self._check_parser(),
        }

        all_ok = all(checks.values())
        some_ok = any(checks.values())

        return {
            "status": "ok" if all_ok else "degraded",
            **checks,
        }

    # --- Step health ---

    def check_step_health(self, step_id: str) -> dict[str, Any]:
        """Get health report for a specific step.

        Returns:
            dict with keys: success_rate, avg_latency (ms), last_error.
        """
        metrics = self._metrics
        latency_durations = []
        errors = 0
        total = 0

        # Access internal data from MetricsCollector
        with metrics._lock:
            events = list(metrics._step_durations.get(step_id, []))
            total = metrics._total_counts.get(step_id, 0)
            errors = metrics._error_counts.get(step_id, 0)

        avg_latency = 0.0
        if events:
            avg_latency = round(sum(e[1] for e in events) / len(events), 2)

        success_rate = round(1.0 - (errors / total if total > 0 else 0.0), 4)
        last_error = ""
        if errors > 0 and events:
            for ev in reversed(events):
                if not ev[2]:  # success=False
                    last_error = f"Last failure at timestamp {ev[0]}"
                    break

        return {
            "step_id": step_id,
            "success_rate": success_rate,
            "avg_latency_ms": avg_latency,
            "last_error": last_error,
            "total_executions": total,
            "total_errors": errors,
        }

    # --- Diagnostics ---

    def run_diagnostics(self) -> list[dict[str, Any]]:
        """Run all health checks and return results.

        Returns:
            list of check result dicts, each with keys: check, status, details.
        """
        results: list[dict[str, Any]] = []

        # Liveness
        liveness = self.check_liveness()
        results.append({
            "check": "liveness",
            "status": liveness["status"],
            "details": {"uptime": liveness["uptime"], "version": liveness["version"]},
        })

        # Readiness
        readiness = self.check_readiness()
        results.append({
            "check": "readiness",
            "status": readiness["status"],
            "details": {
                "db_connected": readiness.get("db_connected", False),
                "fetcher_ok": readiness.get("fetcher_ok", False),
                "parser_ok": readiness.get("parser_ok", False),
            },
        })

        # Metrics summary
        with self._metrics._lock:
            step_types = list(self._metrics._step_durations.keys())
            models = list(self._metrics._token_counts.keys())
        results.append({
            "check": "metrics",
            "status": "ok" if step_types or models else "degraded",
            "details": {
                "tracked_step_types": len(step_types),
                "tracked_models": len(models),
                "queue_depth": self._metrics.get_queue_depth(),
            },
        })

        # Watchdog — hung steps
        hung = self._watchdog.get_hung_step_report()
        results.append({
            "check": "watchdog",
            "status": "degraded" if hung else "ok",
            "details": {
                "hung_step_count": len(hung),
                "hung_steps": hung,
            },
        })

        return results

    # --- Internal checks ---

    @staticmethod
    def _check_db() -> bool:
        """Check database connectivity."""
        try:
            from database import get_connection
            conn = get_connection()
            conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    @staticmethod
    def _check_fetcher() -> bool:
        """Check if the HTTP fetcher is available."""
        try:
            from fetcher import Fetcher
            f = Fetcher()
            # Just check instantiation, don't actually fetch
            return True
        except ImportError:
            return False
        except Exception:
            return False

    @staticmethod
    def _check_parser() -> bool:
        """Check if the parser engine is available."""
        try:
            from parser.engine import ParserEngine
            eng = ParserEngine()
            return True
        except ImportError:
            return False
        except Exception:
            return False
