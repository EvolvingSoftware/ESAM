"""Per-domain rate limiter with sliding window."""

from __future__ import annotations

import time
import threading
from collections import defaultdict
from typing import Any


class RateLimiter:
    """Per-domain rate limiter using a sliding window.

    Tracks request timestamps per domain and enforces a maximum
    requests-per-minute (RPM) with optional burst allowance.

    Thread-safe via ``threading.Lock``.

    Args:
        default_rpm: Maximum requests per minute per domain.
        default_burst: Maximum allowed burst over the RPM limit.
    """

    def __init__(self, default_rpm: int = 60, default_burst: int = 10) -> None:
        self._default_rpm = default_rpm
        self._default_burst = default_burst
        self._lock = threading.Lock()
        self._windows: dict[str, list[float]] = defaultdict(list)
        self._domain_rpm: dict[str, int] = {}

    def _get_rpm(self, domain: str) -> int:
        return self._domain_rpm.get(domain, self._default_rpm)

    def check(self, domain: str) -> bool:
        """Check if a request to *domain* is currently allowed.

        Returns:
            ``True`` if the request is within the rate limit.
        """
        now = time.time()
        window_sec = 60.0
        rpm = self._get_rpm(domain)

        with self._lock:
            timestamps = self._windows[domain]
            # Prune entries older than the window
            cutoff = now - window_sec
            self._windows[domain] = [t for t in timestamps if t > cutoff]
            current_count = len(self._windows[domain])

            # Allow up to RPM + burst
            if current_count < rpm + self._default_burst:
                return True
            return False

    def record(self, domain: str) -> None:
        """Record a request timestamp for *domain*.

        Must be called *after* a successful ``check()`` returns ``True``.
        """
        now = time.time()
        with self._lock:
            self._windows[domain].append(now)

    def get_stats(self) -> dict[str, Any]:
        """Return current rate-limiter statistics keyed by domain.

        Returns:
            ``{domain: {"current_count": int, "rpm": int, "burst": int}}``
        """
        now = time.time()
        window_sec = 60.0
        stats: dict[str, Any] = {}
        with self._lock:
            for domain, timestamps in self._windows.items():
                cutoff = now - window_sec
                active = [t for t in timestamps if t > cutoff]
                stats[domain] = {
                    "current_count": len(active),
                    "rpm": self._get_rpm(domain),
                    "burst": self._default_burst,
                }
        return stats
