"""Metrics collection — in-memory with rolling window and Prometheus export."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Any


class MetricsCollector:
    """Singleton metrics collector with a rolling window (last 1000 events per metric).

    Usage::

        m = MetricsCollector()
        m.record_step_duration("step-fetch", 1500, success=True)
        p50 = m.get_step_latency_p50("step-fetch")
    """

    _instance: MetricsCollector | None = None
    _lock = threading.Lock()

    # --- Singleton plumbing ---

    def __new__(cls) -> MetricsCollector:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    obj = super().__new__(cls)
                    obj._initialized = False
                    cls._instance = obj
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._lock = threading.RLock()
        # Rolling windows: keyed by step_type -> deque of (timestamp, duration_ms, success)
        self._step_durations: dict[str, deque[tuple[float, float, bool]]] = defaultdict(
            lambda: deque(maxlen=1000)
        )
        # Token usage: keyed by model -> deque of (timestamp, input_tokens, output_tokens)
        self._token_counts: dict[str, deque[tuple[float, int, int]]] = defaultdict(
            lambda: deque(maxlen=1000)
        )
        # Error counts per step_type
        self._error_counts: dict[str, int] = defaultdict(int)
        self._total_counts: dict[str, int] = defaultdict(int)
        # Queue depth approximation
        self._queue_depth = 0
        self._initialized = True

    # --- Record methods ---

    def record_step_duration(self, step_id: str, duration_ms: float, success: bool) -> None:
        """Record a step execution duration.

        Args:
            step_id: The step identifier (used as step_type key).
            duration_ms: Duration in milliseconds.
            success: Whether the step completed successfully.
        """
        with self._lock:
            self._step_durations[step_id].append((time.time(), duration_ms, success))
            self._total_counts[step_id] += 1
            if not success:
                self._error_counts[step_id] += 1

    def record_token_count(self, model: str, input_tokens: int, output_tokens: int) -> None:
        """Record token usage for an LLM call.

        Args:
            model: The model identifier (e.g. "gpt-4", "deepseek-v4-flash").
            input_tokens: Number of input (prompt) tokens.
            output_tokens: Number of output (completion) tokens.
        """
        with self._lock:
            self._token_counts[model].append((time.time(), input_tokens, output_tokens))

    # --- Query methods ---

    def get_step_latency_p50(self, step_type: str) -> float:
        """P50 (median) latency in ms for the given step type.

        Returns 0.0 if no data.
        """
        with self._lock:
            durations = [d for _, d, _ in self._step_durations.get(step_type, [])]
        if not durations:
            return 0.0
        sorted_d = sorted(durations)
        n = len(sorted_d)
        if n % 2 == 1:
            return float(sorted_d[n // 2])
        return (sorted_d[n // 2 - 1] + sorted_d[n // 2]) / 2.0

    def get_step_latency_p95(self, step_type: str) -> float:
        """P95 latency in ms for the given step type.

        Returns 0.0 if no data.
        """
        with self._lock:
            durations = [d for _, d, _ in self._step_durations.get(step_type, [])]
        if not durations:
            return 0.0
        sorted_d = sorted(durations)
        n = len(sorted_d)
        idx = min(int(n * 0.95), n - 1)
        return float(sorted_d[idx])

    def get_step_error_rate(self, step_type: str) -> float:
        """Error rate (0.0–1.0) for the given step type."""
        with self._lock:
            total = self._total_counts.get(step_type, 0)
            errors = self._error_counts.get(step_type, 0)
        if total == 0:
            return 0.0
        return round(errors / total, 4)

    def get_queue_depth(self) -> int:
        """Approximate number of pending items in the queue."""
        with self._lock:
            return self._queue_depth

    def set_queue_depth(self, depth: int) -> None:
        """Set the queue depth (called by the job queue)."""
        with self._lock:
            self._queue_depth = depth

    # --- Prometheus export ---

    def export_prometheus(self) -> str:
        """Export all metrics in Prometheus text format."""
        lines: list[str] = []
        lines.append("# HELP esam_step_duration_ms Step execution duration in milliseconds")
        lines.append("# TYPE esam_step_duration_ms gauge")
        with self._lock:
            for step_type, events in self._step_durations.items():
                if events:
                    # Latest value for each step type
                    latest = events[-1]
                    lines.append(
                        f'esam_step_duration_ms{{step_type="{step_type}"}} {latest[1]}'
                    )

            lines.append("# HELP esam_step_error_rate Step error rate (0-1)")
            lines.append("# TYPE esam_step_error_rate gauge")
            for step_type in self._total_counts:
                total = self._total_counts.get(step_type, 0)
                errors = self._error_counts.get(step_type, 0)
                rate = round(errors / total, 4) if total > 0 else 0.0
                lines.append(
                    f'esam_step_error_rate{{step_type="{step_type}"}} {rate}'
                )

            lines.append("# HELP esam_step_latency_p50 Step latency P50 in ms")
            lines.append("# TYPE esam_step_latency_p50 gauge")
            for step_type in list(self._step_durations.keys()):
                p50 = self.get_step_latency_p50(step_type)
                lines.append(f'esam_step_latency_p50{{step_type="{step_type}"}} {p50}')

            lines.append("# HELP esam_step_latency_p95 Step latency P95 in ms")
            lines.append("# TYPE esam_step_latency_p95 gauge")
            for step_type in list(self._step_durations.keys()):
                p95 = self.get_step_latency_p95(step_type)
                lines.append(f'esam_step_latency_p95{{step_type="{step_type}"}} {p95}')

            lines.append("# HELP esam_token_count_total Total tokens used per model")
            lines.append("# TYPE esam_token_count_total counter")
            for model, events in self._token_counts.items():
                total_in = sum(e[1] for e in events)
                total_out = sum(e[2] for e in events)
                lines.append(
                    f'esam_token_count_total{{model="{model}",type="input"}} {total_in}'
                )
                lines.append(
                    f'esam_token_count_total{{model="{model}",type="output"}} {total_out}'
                )

            lines.append("# HELP esam_queue_depth Current queue depth")
            lines.append("# TYPE esam_queue_depth gauge")
            lines.append(f"esam_queue_depth {self._queue_depth}")

        return "\n".join(lines) + "\n"
