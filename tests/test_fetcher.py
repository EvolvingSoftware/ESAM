"""Tests for the HTTP Fetcher engine components."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Ensure src is on the path
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from fetcher.engine import FetcherEngine
from fetcher.rate_limiter import RateLimiter
from fetcher.cache import ResponseCache
from fetcher.auth import AuthResolver


# ── Rate Limiter Tests ───────────────────────────────────────────────


class TestRateLimiter:
    def test_basic_allowed(self) -> None:
        rl = RateLimiter(default_rpm=60, default_burst=10)
        assert rl.check("example.com") is True

    def test_rate_limit_exceeded(self) -> None:
        rl = RateLimiter(default_rpm=2, default_burst=0)
        # First two should be allowed
        assert rl.check("example.com") is True
        rl.record("example.com")
        assert rl.check("example.com") is True
        rl.record("example.com")
        # Third should be denied (rpm=2, burst=0)
        assert rl.check("example.com") is False

    def test_burst_allows_extra(self) -> None:
        rl = RateLimiter(default_rpm=2, default_burst=1)
        # First two (within RPM)
        assert rl.check("example.com") is True
        rl.record("example.com")
        assert rl.check("example.com") is True
        rl.record("example.com")
        # Third (within burst)
        assert rl.check("example.com") is True
        rl.record("example.com")
        # Fourth should be denied (rpm=2, burst=1, so 3 max)
        assert rl.check("example.com") is False

    def test_per_domain_tracking(self) -> None:
        rl = RateLimiter(default_rpm=1, default_burst=0)
        assert rl.check("a.com") is True
        rl.record("a.com")
        assert rl.check("a.com") is False  # a.com exceeded
        assert rl.check("b.com") is True   # b.com still allowed

    def test_get_stats(self) -> None:
        rl = RateLimiter(default_rpm=10, default_burst=2)
        rl.record("test.dev")
        stats = rl.get_stats()
        assert "test.dev" in stats
        assert stats["test.dev"]["current_count"] == 1
        assert stats["test.dev"]["rpm"] == 10
        assert stats["test.dev"]["burst"] == 2


# ── Cache Tests ──────────────────────────────────────────────────────


class TestResponseCache:
    def test_set_get(self) -> None:
        cache = ResponseCache(max_size=10)
        cache.set("https://example.com", {"body_text": "hello"}, ttl_seconds=60)
        result = cache.get("https://example.com")
        assert result is not None
        assert result["body_text"] == "hello"

    def test_missing_key(self) -> None:
        cache = ResponseCache()
        assert cache.get("https://missing.com") is None

    def test_expiry(self) -> None:
        cache = ResponseCache(max_size=10)
        cache.set("https://example.com", {"body_text": "expire"}, ttl_seconds=0)
        time.sleep(0.01)
        assert cache.get("https://example.com") is None

    def test_lru_eviction(self) -> None:
        cache = ResponseCache(max_size=3)
        cache.set("a", {"body": "1"})
        cache.set("b", {"body": "2"})
        cache.set("c", {"body": "3"})
        cache.set("d", {"body": "4"})  # should evict "a"
        assert cache.get("a") is None
        assert cache.get("d") is not None

    def test_invalidate(self) -> None:
        cache = ResponseCache(max_size=10)
        cache.set("https://example.com", {"body_text": "data"})
        assert cache.get("https://example.com") is not None
        cache.invalidate("https://example.com")
        assert cache.get("https://example.com") is None

    def test_get_moves_to_end(self) -> None:
        """Accessing an entry should make it recently used (no eviction)."""
        cache = ResponseCache(max_size=2)
        cache.set("a", {"body": "1"})
        cache.set("b", {"body": "2"})
        # Access 'a' — makes it recently used
        assert cache.get("a") is not None
        # Add 'c' — should evict 'b' (oldest), not 'a'
        cache.set("c", {"body": "3"})
        assert cache.get("b") is None
        assert cache.get("a") is not None

    def test_size_property(self) -> None:
        cache = ResponseCache(max_size=10)
        assert cache.size == 0
        cache.set("a", {"body": "1"})
        assert cache.size == 1


# ── Auth Resolver Tests ──────────────────────────────────────────────


class TestAuthResolver:
    def test_no_auth_ref(self) -> None:
        resolver = AuthResolver()
        result = resolver.resolve(None, {"some": "context"})
        assert result == {"headers": {}, "params": {}}

    def test_empty_auth_ref(self) -> None:
        resolver = AuthResolver()
        result = resolver.resolve("", {})
        assert result == {"headers": {}, "params": {}}

    def test_missing_credential(self) -> None:
        resolver = AuthResolver()
        result = resolver.resolve("my_key", {"other": "data"})
        assert result == {"headers": {}, "params": {}}

    def test_api_key_header(self) -> None:
        resolver = AuthResolver()
        context = {
            "my_key": {
                "type": "api_key",
                "value": "abc123",
                "placement": "header",
                "key_name": "X-API-Key",
            }
        }
        result = resolver.resolve("my_key", context)
        assert result["headers"].get("X-API-Key") == "abc123"
        assert result["params"] == {}

    def test_api_key_default_header(self) -> None:
        resolver = AuthResolver(default_api_key_header="X-Custom-Key")
        context = {
            "my_key": {
                "type": "api_key",
                "value": "secret",
            }
        }
        result = resolver.resolve("my_key", context)
        assert result["headers"].get("X-Custom-Key") == "secret"

    def test_api_key_query_param(self) -> None:
        resolver = AuthResolver()
        context = {
            "my_key": {
                "type": "api_key",
                "value": "abc123",
                "placement": "query",
                "key_name": "api_key",
            }
        }
        result = resolver.resolve("my_key", context)
        assert result["headers"] == {}
        assert result["params"].get("api_key") == "abc123"

    def test_bearer_token(self) -> None:
        resolver = AuthResolver()
        context = {
            "my_token": {
                "type": "bearer_token",
                "value": "tok-xyz-789",
            }
        }
        result = resolver.resolve("my_token", context)
        assert result["headers"].get("Authorization") == "Bearer tok-xyz-789"

    def test_basic_auth(self) -> None:
        resolver = AuthResolver()
        context = {
            "my_creds": {
                "type": "basic_auth",
                "value": "user:pass",
            }
        }
        result = resolver.resolve("my_creds", context)
        auth_val = result["headers"].get("Authorization", "")
        assert auth_val.startswith("Basic ")
        # Decode and verify
        import base64
        decoded = base64.b64decode(auth_val.replace("Basic ", "")).decode("utf-8")
        assert decoded == "user:pass"


# ── FetcherEngine Tests ──────────────────────────────────────────────


class TestFetcherEngine:
    def test_engine_basic_fetch(self) -> None:
        """Mock urllib and verify the engine returns the expected structure."""
        engine = FetcherEngine()

        # Mock urllib.request.urlopen
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.read.return_value = b"<html>OK</html>"
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = engine.fetch("https://example.com")

        assert result["status_code"] == 200
        assert result["body_text"] == "<html>OK</html>"
        assert "Content-Type" in result["headers"]
        assert result["error"] is None
        assert isinstance(result["elapsed_ms"], int)

    def test_retry_on_429(self) -> None:
        """Verify retry on 429 with exponential backoff."""
        engine = FetcherEngine()
        # Simulate 429 twice, then 200
        mock_429 = MagicMock()
        mock_429.status = 429
        mock_429.headers = {"Retry-After": "1"}
        mock_429.read.return_value = b"rate limited"
        mock_429.__enter__.return_value = mock_429
        mock_429.__exit__.return_value = None
        # Simulate 429 raising HTTPError
        http_error_429 = __import__("urllib.error").error.HTTPError(
            "https://example.com", 429, "Too Many Requests", {}, None
        )

        mock_200 = MagicMock()
        mock_200.status = 200
        mock_200.headers = {"Content-Type": "text/plain"}
        mock_200.read.return_value = b"success"
        mock_200.__enter__.return_value = mock_200
        mock_200.__exit__.return_value = None

        # mock urlopen to raise HTTPError 429 twice, then return 200
        call_count = [0]

        def side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            call_count[0] += 1
            if call_count[0] <= 2:
                raise http_error_429
            return mock_200

        with patch("urllib.request.urlopen", side_effect=side_effect):
            result = engine.fetch("https://example.com", retry_count=3)

        assert result["status_code"] == 200
        assert result["body_text"] == "success"
        assert result["error"] is None
        # Should have been called 3 times (2 retries + 1 success)
        assert call_count[0] == 3

    def test_retry_on_503(self) -> None:
        """Verify retry on 503."""
        engine = FetcherEngine()
        http_error_503 = __import__("urllib.error").error.HTTPError(
            "https://example.com", 503, "Service Unavailable", {}, None
        )
        mock_200 = MagicMock()
        mock_200.status = 200
        mock_200.headers = {}
        mock_200.read.return_value = b"ok"
        mock_200.__enter__.return_value = mock_200
        mock_200.__exit__.return_value = None

        call_count = [0]

        def side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            call_count[0] += 1
            if call_count[0] <= 2:
                raise http_error_503
            return mock_200

        with patch("urllib.request.urlopen", side_effect=side_effect):
            result = engine.fetch("https://example.com", retry_count=3)

        assert result["status_code"] == 200
        assert result["error"] is None

    def test_engine_accept_encoding_header(self) -> None:
        """Verify Accept-Encoding: gzip is set by default."""
        engine = FetcherEngine()

        captured_headers: dict[str, str] = {}

        def capture_request(*args: Any, **kwargs: Any) -> MagicMock:
            req = args[0]
            captured_headers.update(dict(req.headers))
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.headers = {}
            mock_resp.read.return_value = b"ok"
            mock_resp.__enter__.return_value = mock_resp
            mock_resp.__exit__.return_value = None
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=capture_request):
            engine.fetch("https://example.com")

        lower_headers = {k.lower(): v for k, v in captured_headers.items()}
        assert lower_headers.get("accept-encoding") == "gzip"

    def test_caching_works(self) -> None:
        """Verify that cached responses are returned on subsequent fetches."""
        engine = FetcherEngine()

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {}
        mock_resp.read.return_value = b"cached content"
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        call_count = [0]

        def side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            call_count[0] += 1
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=side_effect):
            # First fetch — hits network
            r1 = engine.fetch("https://cached.example.com")
            assert r1["body_text"] == "cached content"
            assert call_count[0] == 1

            # Second fetch — should come from cache
            r2 = engine.fetch("https://cached.example.com")
            assert r2["body_text"] == "cached content"
            assert r2["cached_from"] == "memory"
            # Should NOT have called urlopen again
            assert call_count[0] == 1

    def test_error_on_connection_error(self) -> None:
        """Verify ConnectionError is caught and returned as error."""
        engine = FetcherEngine()

        def raise_conn_err(*args: Any, **kwargs: Any) -> None:
            raise ConnectionError("Connection refused")

        with patch("urllib.request.urlopen", side_effect=raise_conn_err):
            result = engine.fetch("https://down.example.com", retry_count=0)

        assert result["error"] is not None
        assert "ConnectionError" in result["error"]
        assert result["status_code"] == 0

    def test_follow_redirects(self) -> None:
        """Verify that redirects are followed up to 5."""
        engine = FetcherEngine()

        mock_redirect = MagicMock()
        mock_redirect.status = 301
        mock_redirect.headers = {"Location": "https://final.example.com"}
        mock_redirect.read.return_value = b""
        mock_redirect.__enter__.return_value = mock_redirect
        mock_redirect.__exit__.return_value = None

        mock_final = MagicMock()
        mock_final.status = 200
        mock_final.headers = {}
        mock_final.read.return_value = b"final content"
        mock_final.__enter__.return_value = mock_final
        mock_final.__exit__.return_value = None

        call_log: list[str] = []

        def side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            req = args[0]
            call_log.append(req.full_url or req.get_full_url())
            if "redirect" in (req.full_url or req.get_full_url()):
                return mock_redirect
            return mock_final

        with patch("urllib.request.urlopen", side_effect=side_effect):
            result = engine.fetch("https://redirect.example.com")

        assert result["status_code"] == 200
        assert result["body_text"] == "final content"
        assert len(call_log) >= 1  # at least the redirect was followed
