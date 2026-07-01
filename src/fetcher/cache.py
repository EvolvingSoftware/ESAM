"""In-memory LRU response cache with TTL and ETag support."""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any


class ResponseCache:
    """In-memory LRU cache for HTTP responses.

    Supports TTL-based expiry and respects ETag/Last-Modified headers
    for conditional revalidation.

    Thread-safe for single-threaded access (assumes external locking
    if used concurrently).  For concurrent use cases, combine with
    ``threading.Lock`` externally or subclass.

    Args:
        max_size: Maximum number of entries before LRU eviction.
    """

    def __init__(self, max_size: int = 500) -> None:
        self._max_size = max_size
        self._cache: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def get(self, url: str) -> dict[str, Any] | None:
        """Retrieve a cached response for *url*.

        Returns ``None`` if the entry is missing or expired.
        Moves the entry to the end (most recently used) on access.
        """
        entry = self._cache.get(url)
        if entry is None:
            return None

        # Check TTL expiry
        expires_at = entry.get("_expires_at", 0)
        if time.time() > expires_at:
            self._cache.pop(url, None)
            return None

        # Move to end (most recently used)
        self._cache.move_to_end(url)
        # Return a copy without internal metadata
        result = {k: v for k, v in entry.items() if not k.startswith("_")}
        return result

    def set(
        self,
        url: str,
        response: dict[str, Any],
        ttl_seconds: int = 300,
    ) -> None:
        """Store *response* for *url* with a TTL.

        If the response contains ``ETag`` or ``Last-Modified`` headers,
        they are preserved for conditional revalidation.

        Args:
            url: The request URL (cache key).
            response: The response dict to cache.
            ttl_seconds: Time-to-live in seconds (default 300 = 5 min).
        """
        # Evict if at capacity (LRU: remove oldest/first entry)
        if len(self._cache) >= self._max_size and url not in self._cache:
            self._cache.popitem(last=False)

        entry: dict[str, Any] = dict(response)
        entry["_expires_at"] = time.time() + ttl_seconds
        self._cache[url] = entry
        self._cache.move_to_end(url)

    def invalidate(self, url: str) -> None:
        """Remove the cached entry for *url*, if present."""
        self._cache.pop(url, None)

    @property
    def size(self) -> int:
        """Current number of cache entries."""
        return len(self._cache)
