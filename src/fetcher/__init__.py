"""HTTP Fetch Engine — reusable platform primitive for fetching web content."""

from __future__ import annotations

from fetcher.engine import FetcherEngine
from fetcher.rate_limiter import RateLimiter
from fetcher.cache import ResponseCache
from fetcher.auth import AuthResolver

__all__ = [
    "FetcherEngine",
    "RateLimiter",
    "ResponseCache",
    "AuthResolver",
]
