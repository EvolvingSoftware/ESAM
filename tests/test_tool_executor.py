"""Tests for the HTTP Tool Execution Engine (tool_executor.py).

Uses unittest.mock to simulate HTTP responses and verify that the
tool executor builds correct requests from tool_instances/tool_registry.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import sys
from pathlib import Path

# Add src to path
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from tool_executor import (
    _execute_camoufox,
    _execute_searxng,
    _execute_send_email,
    _execute_web_extract,
    _execute_web_search,
    _http_request,
    execute_tool_call,
)


class TestHttpRequest(unittest.TestCase):
    """Test the low-level HTTP request helper."""

    @patch("tool_executor.urllib.request.urlopen")
    def test_get_request(self, mock_urlopen):
        """GET request passes query params and returns parsed JSON."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({"results": ["a", "b"]}).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        import asyncio
        result = asyncio.run(
            _http_request("GET", "http://example.com/search", params={"q": "test"})
        )

        self.assertEqual(result["status"], 200)
        self.assertEqual(result["content"]["results"], ["a", "b"])

        # Verify the URL was built correctly
        call_args = mock_urlopen.call_args[0][0]
        self.assertIn("q=test", call_args.full_url)

    @patch("tool_executor.urllib.request.urlopen")
    def test_post_request(self, mock_urlopen):
        """POST request sends JSON body and returns parsed response."""
        mock_response = MagicMock()
        mock_response.status = 201
        mock_response.read.return_value = json.dumps({"id": "abc"}).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        import asyncio
        result = asyncio.run(
            _http_request("POST", "http://example.com/api", data={"key": "val"})
        )

        self.assertEqual(result["status"], 201)
        self.assertEqual(result["content"]["id"], "abc")

        # Verify JSON body
        call = mock_urlopen.call_args[0][0]
        sent_data = json.loads(call.data)
        self.assertEqual(sent_data["key"], "val")

    @patch("tool_executor.urllib.request.urlopen")
    def test_http_error(self, mock_urlopen):
        """HTTP errors return error dict with status code."""
        import urllib.error
        error_response = MagicMock()
        error_response.read.return_value = b'{"error": "not found"}'
        error_response.code = 404
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "http://example.com/404", 404, "Not Found", {}, error_response
        )

        import asyncio
        result = asyncio.run(
            _http_request("GET", "http://example.com/404")
        )

        self.assertIn("error", result)
        self.assertEqual(result["status"], 404)

    @patch("tool_executor.urllib.request.urlopen")
    def test_connection_error(self, mock_urlopen):
        """Connection failures return error dict with status 0."""
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        import asyncio
        result = asyncio.run(
            _http_request("GET", "http://127.0.0.1:1/nope")
        )

        self.assertIn("error", result)
        self.assertEqual(result["status"], 0)
        self.assertIn("Connection failed", result["error"])

    @patch("tool_executor.urllib.request.urlopen")
    def test_passthrough_headers(self, mock_urlopen):
        """Custom headers are passed to the HTTP request."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b"{}"
        mock_urlopen.return_value.__enter__.return_value = mock_response

        import asyncio
        asyncio.run(
            _http_request("GET", "http://example.com", headers={"X-API-Key": "sekret"})
        )

        call = mock_urlopen.call_args[0][0]
        # urllib.request.Request stores headers in a plain dict with lowered keys
        self.assertEqual(call.headers.get("X-api-key"), "sekret")
        self.assertEqual(call.headers.get("Accept"), "application/json")


class TestSearXNG(unittest.TestCase):
    """Test the SearXNG search tool executor."""

    @patch("tool_executor._http_request")
    def test_basic_search(self, mock_http):
        """SearXNG GET /search with query parameter."""
        mock_http.return_value = {
            "content": {
                "results": [
                    {"title": "Result 1", "url": "http://ex.com/1"},
                    {"title": "Result 2", "url": "http://ex.com/2"},
                ],
                "answers": [],
            },
            "status": 200,
        }

        import asyncio
        result = asyncio.run(
            _execute_searxng(
                "http://127.0.0.1:8888",
                {"query": "AI safety", "categories": "news"},
                None,
            )
        )

        self.assertEqual(result["total"], 2)
        self.assertEqual(len(result["results"]), 2)
        self.assertEqual(result["results"][0]["title"], "Result 1")

        # Verify the HTTP request was built correctly
        mock_http.assert_called_once_with(
            method="GET",
            url="http://127.0.0.1:8888/search",
            params={"q": "AI safety", "format": "json", "categories": "news"},
            headers=None,
            timeout=30,
        )

    @patch("tool_executor._http_request")
    def test_missing_query(self, mock_http):
        """Missing query returns error without making HTTP call."""
        import asyncio
        result = asyncio.run(
            _execute_searxng("http://127.0.0.1:8888", {}, None)
        )
        self.assertIn("error", result)
        mock_http.assert_not_called()


class TestCamoufox(unittest.TestCase):
    """Test the Camoufox browser render tool executor."""

    @patch("tool_executor._http_request")
    def test_render_with_actions(self, mock_http):
        """Camoufox POST /render with url and actions."""
        mock_http.return_value = {
            "content": {"content": "<html>rendered</html>"},
            "status": 200,
        }

        import asyncio
        result = asyncio.run(
            _execute_camoufox(
                "http://127.0.0.1:3211",
                {"url": "http://example.com", "actions": [{"type": "click", "selector": "#btn"}]},
                None,
            )
        )

        self.assertIn("content", result)
        self.assertEqual(result["content"], "<html>rendered</html>")

        mock_http.assert_called_once_with(
            method="POST",
            url="http://127.0.0.1:3211/render",
            data={"url": "http://example.com", "actions": [{"type": "click", "selector": "#btn"}]},
            headers=None,
            timeout=30,
        )

    @patch("tool_executor._http_request")
    def test_missing_url(self, mock_http):
        """Missing url returns error."""
        import asyncio
        result = asyncio.run(
            _execute_camoufox("http://127.0.0.1:3211", {}, None)
        )
        self.assertIn("error", result)
        mock_http.assert_not_called()


class TestWebSearch(unittest.TestCase):
    """Test the aggregated web search tool executor."""

    @patch("tool_executor._http_request")
    def test_search(self, mock_http):
        """Web search GET /search with query."""
        mock_http.return_value = {
            "content": {"results": [{"title": "R1", "url": "http://x.com/1"}]},
            "status": 200,
        }

        import asyncio
        result = asyncio.run(
            _execute_web_search("http://127.0.0.1:4000", {"query": "test"}, None)
        )

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["title"], "R1")

        mock_http.assert_called_once_with(
            method="GET",
            url="http://127.0.0.1:4000/search",
            params={"q": "test"},
            headers=None,
            timeout=15,
        )


class TestWebExtract(unittest.TestCase):
    """Test the web content extractor tool executor."""

    @patch("tool_executor._http_request")
    def test_extract(self, mock_http):
        """Web extract POST /extract with url."""
        mock_http.return_value = {
            "content": {"content": "# Title\n\nBody text"},
            "status": 200,
        }

        import asyncio
        result = asyncio.run(
            _execute_web_extract(
                "http://127.0.0.1:4000",
                {"url": "http://example.com/article", "extract_format": "markdown"},
                None,
            )
        )

        self.assertIn("content", result)
        self.assertEqual(result["content"], "# Title\n\nBody text")

        mock_http.assert_called_once_with(
            method="POST",
            url="http://127.0.0.1:4000/extract",
            data={"url": "http://example.com/article", "format": "markdown"},
            headers=None,
            timeout=30,
        )


class TestSendEmail(unittest.TestCase):
    """Test the send_email tool executor."""

    @patch("tool_executor._http_request")
    def test_send_via_http(self, mock_http):
        """Email sent via HTTP POST to configured endpoint."""
        mock_http.return_value = {"content": {"sent": True}, "status": 200}

        import asyncio
        result = asyncio.run(
            _execute_send_email(
                "http://127.0.0.1:8025/api/send",
                {"to": "user@example.com", "subject": "Test", "body": "Hello"},
                {"Authorization": "Bearer tok"},
                None,
            )
        )

        self.assertTrue(result["sent"])
        mock_http.assert_called_once_with(
            method="POST",
            url="http://127.0.0.1:8025/api/send",
            data={"to": "user@example.com", "subject": "Test", "body": "Hello"},
            headers={"Authorization": "Bearer tok"},
            timeout=30,
        )

    def test_simulated_fallback(self):
        """Without endpoint, returns simulated success."""
        import asyncio
        result = asyncio.run(
            _execute_send_email(
                None,
                {"to": "user@example.com", "subject": "Sim", "body": "Body"},
                None,
                None,
            )
        )
        self.assertTrue(result["sent"])
        self.assertIn("simulated", result.get("note", ""))


class TestExecuteToolCall(unittest.TestCase):
    """Test the main execute_tool_call dispatcher."""

    def setUp(self):
        self.tool_instances = {
            "searxng_search": {"tool_ref": "searxng", "tier": "ephemeral"},
            "email_gateway": {"tool_ref": "send_email", "tier": "permanent",
                              "credential_ref": "newsletter-email-creds"},
            "custom_search": {"tool_ref": "web_search", "tier": "ephemeral"},
        }
        self.tool_registry = {
            "searxng": {"endpoint": "http://127.0.0.1:8888", "type": "http",
                        "tier": "ephemeral", "timeout": 30000},
            "send_email": {"type": "api", "tier": "permanent", "auth_type": "smtp"},
            "web_search": {"endpoint": "http://127.0.0.1:4000/search", "type": "http",
                           "tier": "ephemeral", "timeout": 15000},
            "web_extract": {"endpoint": "http://127.0.0.1:4000/extract", "type": "http",
                            "tier": "ephemeral", "timeout": 30000},
            "camoufox": {"endpoint": "http://127.0.0.1:3211", "type": "http",
                         "tier": "ephemeral", "timeout": 30000},
        }

    @patch("tool_executor._execute_searxng")
    def test_searxng_instance(self, mock_searxng):
        """Dispatches to _execute_searxng via instance name."""
        mock_searxng.return_value = {"results": [{"title": "R1"}]}

        import asyncio
        result = asyncio.run(
            execute_tool_call(
                "searxng_search",
                {"query": "AI"},
                self.tool_instances,
                self.tool_registry,
            )
        )

        mock_searxng.assert_called_once_with(
            "http://127.0.0.1:8888", {"query": "AI"}, None
        )
        self.assertEqual(result["results"][0]["title"], "R1")

    @patch("tool_executor._execute_send_email")
    def test_send_email_instance(self, mock_email):
        """Dispatches to _execute_send_email via instance name."""
        mock_email.return_value = {"sent": True, "to": "a@b.com"}

        import asyncio
        result = asyncio.run(
            execute_tool_call(
                "email_gateway",
                {"to": "a@b.com", "subject": "Hi", "body": "Body",
                 "headers": {"Authorization": "Bearer tok"}},
                self.tool_instances,
                self.tool_registry,
                credentials={"credential_value": "smtp_pass"},
            )
        )

        mock_email.assert_called_once()
        args = mock_email.call_args[0]
        self.assertEqual(args[0], None)  # no endpoint in registry for send_email
        self.assertEqual(args[1]["to"], "a@b.com")
        self.assertEqual(args[2]["Authorization"], "Bearer tok")

    @patch("tool_executor._execute_web_search")
    def test_web_search_instance(self, mock_search):
        """Dispatches to _execute_web_search."""
        mock_search.return_value = {"results": [{"title": "R1"}]}

        import asyncio
        result = asyncio.run(
            execute_tool_call(
                "custom_search",
                {"query": "test"},
                self.tool_instances,
                self.tool_registry,
            )
        )

        mock_search.assert_called_once()
        self.assertEqual(result["results"][0]["title"], "R1")

    def test_unknown_instance(self):
        """Unknown instance returns error."""
        import asyncio
        result = asyncio.run(
            execute_tool_call(
                "nonexistent_tool",
                {},
                self.tool_instances,
                self.tool_registry,
            )
        )
        self.assertIn("error", result)
        self.assertIn("tool_instance_not_found", result["error"])

    def test_unknown_ref(self):
        """Instance with unknown tool_ref returns error."""
        instances = {"bad": {"tool_ref": "nonexistent_ref"}}

        import asyncio
        result = asyncio.run(
            execute_tool_call("bad", {}, instances, self.tool_registry)
        )
        self.assertIn("error", result)
        self.assertIn("tool_ref_not_found_in_registry", result["error"])


if __name__ == "__main__":
    unittest.main()
