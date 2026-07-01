"""Step Watchdog — Thread-based hung step detection.

Monitors running steps and reports any that exceed their timeout.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


class StepWatchdog:
    """Tracks running steps and detects hung ones via a background thread.

    Usage::

        w = StepWatchdog()
        w.start_watchdog(timeout_seconds=300, poll_interval=5)
        w.watch_step("step-42", timeout=120)
        # ... step runs ...
        w.cancel_watch("step-42")

    If a step runs longer than its timeout, the watchdog logs a warning
    and the step is returned by ``check_hung_steps()``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._watched: dict[str, dict[str, Any]] = {}  # step_id -> info
        self._watchdog_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running = False

    # --- Lifecycle ---

    def start_watchdog(self, timeout_seconds: int = 300, poll_interval: int = 5) -> None:
        """Start the watchdog background thread.

        Args:
            timeout_seconds: Default timeout for steps that don't specify one.
            poll_interval: Seconds between hung-step checks.
        """
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._default_timeout = timeout_seconds
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            args=(poll_interval,),
            daemon=True,
            name="step-watchdog",
        )
        self._watchdog_thread.start()
        logger.info(
            "StepWatchdog started (default_timeout=%ds, poll_interval=%ds)",
            timeout_seconds,
            poll_interval,
        )

    def stop_watchdog(self) -> None:
        """Stop the watchdog thread."""
        self._running = False
        self._stop_event.set()
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=3)
        logger.info("StepWatchdog stopped")

    # --- Step tracking ---

    def watch_step(self, step_id: str, timeout: int | None = None) -> None:
        """Begin tracking a running step.

        Args:
            step_id: Unique identifier for the step.
            timeout: Max allowed seconds before the step is considered hung.
                     Falls back to the default timeout if not provided.
        """
        with self._lock:
            self._watched[step_id] = {
                "step_id": step_id,
                "started_at": time.time(),
                "timeout": timeout or self._default_timeout,
            }

    def cancel_watch(self, step_id: str) -> None:
        """Mark a step as completed normally.

        Args:
            step_id: The step to remove from watch.
        """
        with self._lock:
            self._watched.pop(step_id, None)

    # --- Queries ---

    def check_hung_steps(self) -> list[str]:
        """Return step_ids of steps that have exceeded their timeout."""
        now = time.time()
        hung: list[str] = []
        with self._lock:
            for step_id, info in list(self._watched.items()):
                elapsed = now - info["started_at"]
                if elapsed > info["timeout"]:
                    hung.append(step_id)
        return hung

    def get_hung_step_report(self) -> list[dict[str, Any]]:
        """Return detailed info about all hung steps.

        Returns a list of dicts with keys: step_id, elapsed, timeout, started_at.
        """
        now = time.time()
        report: list[dict[str, Any]] = []
        with self._lock:
            for step_id, info in list(self._watched.items()):
                elapsed = now - info["started_at"]
                if elapsed > info["timeout"]:
                    report.append({
                        "step_id": step_id,
                        "elapsed_seconds": round(elapsed, 2),
                        "timeout_seconds": info["timeout"],
                        "started_at": info["started_at"],
                    })
        return report

    def get_all_watched(self) -> dict[str, dict[str, Any]]:
        """Return all watched steps (for diagnostics)."""
        with self._lock:
            return dict(self._watched)

    # --- Internal ---

    def _watchdog_loop(self, poll_interval: int) -> None:
        """Background loop that periodically checks for hung steps."""
        while not self._stop_event.is_set():
            try:
                hung = self.check_hung_steps()
                if hung:
                    logger.warning(
                        "StepWatchdog detected %d hung step(s): %s",
                        len(hung),
                        ", ".join(hung),
                    )
                    for step_id in hung:
                        info = self._watched.get(step_id)
                        if info:
                            elapsed = time.time() - info["started_at"]
                            logger.warning(
                                "Hung step %s — elapsed=%.1fs timeout=%ds",
                                step_id,
                                elapsed,
                                info["timeout"],
                            )
            except Exception:
                logger.exception("StepWatchdog check error")
            self._stop_event.wait(poll_interval)
