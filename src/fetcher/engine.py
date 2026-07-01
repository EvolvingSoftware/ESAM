"""HTTP Fetch Engine — reusable platform primitive for fetching web content."""

from __future__ import annotations
import gzip
import io
import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

from fetcher.cache import ResponseCache
from fetcher.rate_limiter import RateLimiter
from fetcher.auth import AuthResolver

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 5
DEFAULT_TIMEOUT = 30
DEFAULT_RETRY_COUNT = 3


def _extract_domain(url: str) -> str:
    """Extract the domain part from a URL."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.netloc or parsed.hostname or "unknown"


class FetcherEngine:
    """Reusable HTTP fetch engine with caching, rate limiting, auth, and retry.

    Args:
        cache: Optional :class:`ResponseCache` instance.  Creates a default
            one if not provided.
        rate_limiter: Optional :class:`RateLimiter` instance.  Creates a
            default one if not provided.
        auth_resolver: Optional :class:`AuthResolver` instance.  Creates a
            default one if not provided.
    """

    def __init__(
        self,
        cache: ResponseCache | None = None,
        rate_limiter: RateLimiter | None = None,
        auth_resolver: AuthResolver | None = None,
        proxy_url: str | None = None,
    ) -> None:
        self.cache = cache or ResponseCache()
        self.rate_limiter = rate_limiter or RateLimiter()
        self.auth_resolver = auth_resolver or AuthResolver()
        self.proxy_url = proxy_url

    # ── Public API ────────────────────────────────────────────────────

    def fetch(
        self,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        auth_ref: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        retry_count: int = DEFAULT_RETRY_COUNT,
    ) -> dict[str, Any]:
        """Fetch a URL with caching, rate limiting, auth, and retry.

        Args:
            url: The URL to fetch.
            method: HTTP method (default ``GET``).
            headers: Optional extra HTTP headers.
            auth_ref: Optional credential reference to resolve via
                :class:`AuthResolver`.
            timeout: Request timeout in seconds (default 30).
            retry_count: Max retries on 429/503 (default 3).

        Returns:
            ``{
                "status_code": int,
                "headers": dict,
                "body_text": str,
                "elapsed_ms": int,
                "cached_from": str | None,
                "error": str | None,
            }``
        """
        # 1. Check cache (GET only, skip if auth is involved)
        if method.upper() == "GET" and not auth_ref:
            cached = self.cache.get(url)
            if cached is not None:
                return {
                    "status_code": cached.get("status_code", 200),
                    "headers": cached.get("headers", {}),
                    "body_text": cached.get("body_text", ""),
                    "elapsed_ms": 0,
                    "cached_from": cached.get("_cache_key", "memory"),
                    "error": None,
                }

        # 2. Rate limiting
        domain = _extract_domain(url)
        if not self.rate_limiter.check(domain):
            return {
                "status_code": 429,
                "headers": {"X-RateLimit-Reset": "1"},
                "body_text": "",
                "elapsed_ms": 0,
                "cached_from": None,
                "error": "rate_limited",
            }

        # 3. Resolve auth
        auth_result = self.auth_resolver.resolve(auth_ref)
        request_headers = dict(headers or {})
        request_headers.update(auth_result.get("headers", {}))
        request_params = auth_result.get("params", {})

        # If auth resolved with query params, append them to URL
        if request_params:
            from urllib.parse import urlencode, urlparse, urlunparse, parse_qs
            parsed = urlparse(url)
            existing_params = parse_qs(parsed.query, keep_blank_values=True)
            # Flatten existing values (parse_qs returns lists)
            flat_params = {k: v[0] if len(v) == 1 else v for k, v in existing_params.items()}
            flat_params.update(request_params)
            new_query = urlencode(flat_params, doseq=True)
            url = urlunparse(parsed._replace(query=new_query))

        # 4. Build request
        if "User-Agent" not in request_headers:
            request_headers["User-Agent"] = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        if "Accept" not in request_headers:
            request_headers["Accept"] = "application/rss+xml,application/xml,application/atom+xml,text/xml;q=0.9,*/*;q=0.8"
        if "Accept-Encoding" not in request_headers:
            request_headers["Accept-Encoding"] = "gzip"

        result: dict[str, Any] = {}
        last_error: str | None = None
        start_time = time.time()

        # 5. Attempt fetch with retry
        for attempt in range(1 + retry_count):
            try:
                result = self._do_request(
                    url=url,
                    method=method,
                    headers=request_headers,
                    timeout=timeout,
                    attempt=attempt,
                )
                last_error = None
                break
            except urllib.error.HTTPError as e:
                status = e.code
                if status in (429, 503) and attempt < retry_count:
                    # Exponential backoff
                    sleep_sec = 2 ** attempt
                    logger.info(
                        "HTTP %d on %s, retrying in %ds (attempt %d/%d)",
                        status, url, sleep_sec, attempt + 1, retry_count,
                    )
                    time.sleep(sleep_sec)
                    continue
                # Non-retryable or exhausted retries
                body = e.read().decode("utf-8", errors="replace") if e.fp else ""
                result = {
                    "status_code": status,
                    "headers": dict(e.headers) if e.headers else {},
                    "body_text": body,
                    "elapsed_ms": int((time.time() - start_time) * 1000),
                    "cached_from": None,
                    "error": f"HTTPError: {e.reason}",
                }
                last_error = result["error"]
                break
            except urllib.error.URLError as e:
                last_error = f"URLError: {e.reason}"
                if attempt < retry_count:
                    sleep_sec = 2 ** attempt
                    logger.info(
                        "URLError %s on %s, retrying in %ds (attempt %d/%d)",
                        e.reason, url, sleep_sec, attempt + 1, retry_count,
                    )
                    time.sleep(sleep_sec)
                    continue
                result = {
                    "status_code": 0,
                    "headers": {},
                    "body_text": "",
                    "elapsed_ms": int((time.time() - start_time) * 1000),
                    "cached_from": None,
                    "error": last_error,
                }
                break
            except ConnectionError as e:
                last_error = f"ConnectionError: {e}"
                if attempt < retry_count:
                    sleep_sec = 2 ** attempt
                    logger.info(
                        "ConnectionError on %s, retrying in %ds (attempt %d/%d)",
                        url, sleep_sec, attempt + 1, retry_count,
                    )
                    time.sleep(sleep_sec)
                    continue
                result = {
                    "status_code": 0,
                    "headers": {},
                    "body_text": "",
                    "elapsed_ms": int((time.time() - start_time) * 1000),
                    "cached_from": None,
                    "error": last_error,
                }
                break
            except TimeoutError as e:
                last_error = f"TimeoutError: {e}"
                if attempt < retry_count:
                    sleep_sec = 2 ** attempt
                    logger.info(
                        "Timeout on %s, retrying in %ds (attempt %d/%d)",
                        url, sleep_sec, attempt + 1, retry_count,
                    )
                    time.sleep(sleep_sec)
                    continue
                result = {
                    "status_code": 0,
                    "headers": {},
                    "body_text": "",
                    "elapsed_ms": int((time.time() - start_time) * 1000),
                    "cached_from": None,
                    "error": last_error,
                }
                break

        # Record request for rate limiting
        if result.get("status_code", 0) != 429:  # Don't record rate-limited requests
            self.rate_limiter.record(domain)

        # Populate elapsed_ms if not already set
        if "elapsed_ms" not in result or result["elapsed_ms"] == 0:
            result["elapsed_ms"] = int((time.time() - start_time) * 1000)

        # Cache successful GET responses
        if (
            method.upper() == "GET"
            and result.get("status_code", 0) in (200, 301, 302, 304)
            and not result.get("error")
            and not auth_ref
        ):
            # Prepare cache entry
            cache_entry = {
                "status_code": result["status_code"],
                "headers": result.get("headers", {}),
                "body_text": result.get("body_text", ""),
                "_cache_key": "memory",
            }
            # Respect ETag/Last-Modified from response for cache freshness
            resp_headers = result.get("headers", {})
            ttl = 300  # default 5 min
            if resp_headers.get("Cache-Control"):
                import re
                cc = resp_headers["Cache-Control"]
                match = re.search(r"max-age=(\d+)", cc)
                if match:
                    ttl = min(int(match.group(1)), 3600)  # cap at 1 hour
            self.cache.set(url, cache_entry, ttl_seconds=ttl)

        # Ensure all required keys are present
        result.setdefault("status_code", 0)
        result.setdefault("headers", {})
        result.setdefault("body_text", "")
        result.setdefault("cached_from", None)
        result.setdefault("error", last_error)

        return result

    # ── Internal ──────────────────────────────────────────────────────

    def _do_request(
        self,
        url: str,
        method: str,
        headers: dict[str, str],
        timeout: int,
        attempt: int,
    ) -> dict[str, Any]:
        """Perform a single HTTP request with redirect following.

        Follows up to ``MAX_REDIRECTS`` (5) redirects.

        If ``self.proxy_url`` is set, routes the request through a Camoufox
        proxy API (POST ``{proxy_url}/render``) instead of making a direct
        HTTP request.
        """
        # ── Proxy mode (Camoufox) ──────────────────────────────────
        if self.proxy_url:
            proxy_data = json.dumps({
                "url": url,
                "wait_ms": 5000,
                "human_like": True,
                "text_limit": 12000,
            }).encode("utf-8")

            proxy_headers = {
                "Content-Type": "application/json",
            }

            proxy_req = urllib.request.Request(
                f"{self.proxy_url}/render",
                data=proxy_data,
                headers=proxy_headers,
                method="POST",
            )

            with urllib.request.urlopen(proxy_req, timeout=timeout) as resp:
                resp_body = resp.read().decode("utf-8", errors="replace")
                proxy_result = json.loads(resp_body)

                if proxy_result.get("ok"):
                    return {
                        "status_code": proxy_result.get("status", 200),
                        "headers": {},
                        "body_text": proxy_result.get("text", ""),
                        "elapsed_ms": 0,
                        "cached_from": None,
                        "error": None,
                    }
                else:
                    return {
                        "status_code": 500,
                        "headers": {},
                        "body_text": "",
                        "elapsed_ms": 0,
                        "cached_from": None,
                        "error": "camoufox_error: ok=false",
                    }

        # ── Direct HTTP mode ──────────────────────────────────────
        current_url = url
        redirect_count = 0

        while redirect_count <= MAX_REDIRECTS:
            req = urllib.request.Request(
                current_url,
                data=None,
                headers=headers,
                method=method,
            )

            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_headers = dict(resp.headers) if resp.headers else {}
                body = resp.read()
                # Decompress gzip if needed
                content_encoding = resp_headers.get("Content-Encoding", "").lower()
                if "gzip" in content_encoding or body[:2] == b'\x1f\x8b':
                    try:
                        body = gzip.decompress(body)
                    except Exception:
                        pass  # Not actually gzipped despite header
                body = body.decode("utf-8", errors="replace")
                status = resp.status

                # Follow redirect (301, 302, 303, 307, 308)
                if status in (301, 302, 303, 307, 308):
                    redirect_url = resp_headers.get("Location")
                    if redirect_url:
                        # Handle relative redirects
                        from urllib.parse import urljoin
                        current_url = urljoin(current_url, redirect_url)
                        redirect_count += 1
                        # 303 -> always GET, others keep original method
                        if status == 303:
                            method = "GET"
                        continue

                return {
                    "status_code": status,
                    "headers": resp_headers,
                    "body_text": body,
                    "elapsed_ms": 0,  # caller fills this
                    "cached_from": None,
                    "error": None,
                }

        # Too many redirects
        return {
            "status_code": 0,
            "headers": {},
            "body_text": "",
            "elapsed_ms": 0,
            "cached_from": None,
            "error": "too_many_redirects",
        }
