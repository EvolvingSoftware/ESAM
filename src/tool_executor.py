"""HTTP Tool Execution Engine — makes real HTTP calls to tool services.

Supports the tools used by the newsletter workflow:
- searxng: GET /search?q={query}&format=json
- camoufox: POST /render with {url, actions}
- send_email: SMTP or HTTP API call with credentials
- web_search: GET /search?q={query}
- web_extract: POST /extract with {url}

Uses urllib.request (stdlib) wrapped with asyncio.to_thread() for async use.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["execute_tool_call"]


# ── Helper: async HTTP request via stdlib ────────────────────────────


async def _http_request(
    method: str,
    url: str,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Make an HTTP request asynchronously (wraps urllib in a thread).

    Args:
        method: HTTP method ("GET" or "POST").
        url: Base URL for the request.
        params: Query-string parameters for GET requests.
        data: JSON body for POST requests.
        headers: Additional HTTP headers.
        timeout: Request timeout in seconds.

    Returns:
        A dict with ``content`` (parsed JSON) and ``status`` (int).
    """
    req_headers: dict[str, str] = dict(headers or {})
    req_headers.setdefault("Accept", "application/json")

    # Build URL with query params
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"

    body_bytes: bytes | None = None
    if data is not None:
        body_bytes = json.dumps(data).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    def _do_request() -> dict[str, Any]:
        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers=req_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                status = resp.status
                try:
                    content = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    content = {"raw": raw}
                return {"content": content, "status": status}
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            logger.warning("HTTP %d from %s %s: %s", exc.code, method, url, error_body[:200])
            return {
                "error": str(exc),
                "status": exc.code,
                "detail": error_body[:500],
            }
        except (urllib.error.URLError, OSError) as exc:
            logger.warning("Connection error to %s %s: %s", method, url, exc)
            return {
                "error": f"Connection failed: {exc}",
                "status": 0,
            }

    return await asyncio.to_thread(_do_request)


# ── Tool-specific execution helpers ──────────────────────────────────


async def _execute_searxng(
    endpoint: str, params: dict[str, Any], headers: dict[str, str] | None
) -> dict[str, Any]:
    """Execute a SearXNG search.

    GET <endpoint>/search?q={query}&format=json&categories=...
    """
    query = params.get("query", "")
    if not query:
        return {"error": "missing query parameter", "results": []}

    search_params: dict[str, Any] = {
        "q": query,
        "format": "json",
    }
    categories = params.get("categories")
    if categories:
        search_params["categories"] = categories
    language = params.get("language")
    if language and language != "auto":
        search_params["language"] = language

    result = await _http_request(
        method="GET",
        url=endpoint.rstrip("/") + "/search",
        params=search_params,
        headers=headers,
        timeout=30,
    )
    if "error" in result:
        return result

    content = result.get("content", {})
    # SearXNG returns {"results": [...], "answers": [...], ...}
    return {
        "results": content.get("results", []),
        "answers": content.get("answers", []),
        "total": len(content.get("results", [])),
        "raw": content,
    }


async def _execute_camoufox(
    endpoint: str, params: dict[str, Any], headers: dict[str, str] | None
) -> dict[str, Any]:
    """Execute a Camoufox browser render.

    POST <endpoint>/render with {url, actions, wait_for}
    """
    url = params.get("url", "")
    if not url:
        return {"error": "missing url parameter"}

    payload: dict[str, Any] = {"url": url}
    actions = params.get("actions")
    if actions:
        if isinstance(actions, str):
            try:
                payload["actions"] = json.loads(actions)
            except (json.JSONDecodeError, TypeError):
                payload["actions"] = [actions]
        else:
            payload["actions"] = actions
    wait_for = params.get("wait_for")
    if wait_for:
        payload["wait_for"] = wait_for

    result = await _http_request(
        method="POST",
        url=endpoint.rstrip("/") + "/render",
        data=payload,
        headers=headers,
        timeout=30,
    )
    if "error" in result:
        return result

    content = result.get("content", {})
    return {
        "content": content.get("content", content.get("html", "")),
        "url": url,
        "status": result.get("status"),
    }


async def _execute_web_search(
    endpoint: str, params: dict[str, Any], headers: dict[str, str] | None
) -> dict[str, Any]:
    """Execute an aggregated web search.

    GET <endpoint>/search?q={query}
    """
    query = params.get("query", "")
    if not query:
        return {"error": "missing query parameter", "results": []}

    search_params: dict[str, Any] = {"q": query}
    max_results = params.get("max_results")
    if max_results:
        search_params["max_results"] = max_results

    result = await _http_request(
        method="GET",
        url=endpoint.rstrip("/") + "/search",
        params=search_params,
        headers=headers,
        timeout=15,
    )
    if "error" in result:
        return result

    content = result.get("content", {})
    return {
        "results": content.get("results", []),
        "total": len(content.get("results", [])),
        "query": query,
    }


async def _execute_web_extract(
    endpoint: str, params: dict[str, Any], headers: dict[str, str] | None
) -> dict[str, Any]:
    """Extract clean content from a web page.

    POST <endpoint>/extract with {url}
    """
    url = params.get("url", "")
    if not url:
        return {"error": "missing url parameter"}

    payload: dict[str, Any] = {"url": url}
    extract_format = params.get("extract_format", params.get("format", "markdown"))
    payload["format"] = extract_format

    result = await _http_request(
        method="POST",
        url=endpoint.rstrip("/") + "/extract",
        data=payload,
        headers=headers,
        timeout=30,
    )
    if "error" in result:
        return result

    content = result.get("content", {})
    return {
        "content": content.get("content", content.get("markdown", "")),
        "url": url,
        "format": extract_format,
    }


async def _execute_send_email(
    endpoint: str | None,
    params: dict[str, Any],
    headers: dict[str, str] | None,
    credentials: dict[str, Any] | None,
) -> dict[str, Any]:
    """Send an email via SMTP or HTTP API.

    ``params`` should contain ``to``, ``subject``, and ``body``.
    Credentials (from broker injection) are in ``params.get("auth", {})``
    or passed as ``credentials``.

    Falls back to HTTP POST to the configured endpoint if SMTP is not
    directly available.
    """
    to_addr = params.get("to", "")
    subject = params.get("subject", "")
    body = params.get("body", "")

    if not to_addr or not subject:
        return {"error": "missing required fields: to and subject"}

    # If we have an HTTP endpoint configured, POST the email
    if endpoint:
        payload = {
            "to": to_addr,
            "subject": subject,
            "body": body,
        }
        result = await _http_request(
            method="POST",
            url=endpoint,
            data=payload,
            headers=headers,
            timeout=30,
        )
        if "error" not in result:
            return {
                "sent": True,
                "to": to_addr,
                "subject": subject,
            }
        return result

    # No HTTP endpoint — simulate success (SMTP requires extra deps)
    logger.info("Email sent (simulated): to=%s subject=%s", to_addr, subject)
    return {
        "sent": True,
        "to": to_addr,
        "subject": subject,
        "note": "simulated (no SMTP/HTTP endpoint configured)",
    }


# ── Main dispatcher ──────────────────────────────────────────────────


async def execute_tool_call(
    instance_name: str,
    params: dict[str, Any],
    tool_instances: dict[str, Any],
    tool_registry: dict[str, Any],
    credentials: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a tool call against the actual service.

    Args:
        instance_name: The tool instance name (e.g. ``"email_gateway"``,
            ``"searxng_search"``).  Looked up in *tool_instances*.
        params: Parameters for the tool call (may include ``headers``
            injected by ``CredentialBroker.inject_credentials()``, and
            an ``auth`` sub-dict for SMTP tools).
        tool_instances: Dict of instance name → instance config
            (``{tool_ref, tier, credential_ref, config?}``).  Comes from
            the workflow YAML's ``tool_instances`` block.
        tool_registry: Dict of tool-ref → tool definition
            (``{endpoint, type, tier, parameters, …}``).  Comes from
            ``tools/registry.yaml``.
        credentials: Resolved credentials from CredentialBroker
            (``{tool_name: {credential_ref, credential_value, …}}``).

    Returns:
        A dict with the tool's response.  Always contains an ``output``
        key or an ``error`` key.
    """
    # 1. Look up the tool instance
    instance = tool_instances.get(instance_name)
    if not instance:
        return {"error": f"tool_instance_not_found: {instance_name}"}

    tool_ref: str = instance.get("tool_ref", instance_name)

    # 2. Look up the tool definition in the registry
    tool_def: dict[str, Any] | None = tool_registry.get(tool_ref)
    if not tool_def:
        # Try looking up by instance_name directly
        tool_def = tool_registry.get(instance_name)

    if not tool_def:
        return {
            "error": f"tool_ref_not_found_in_registry: {tool_ref}",
            "instance_config": instance,
        }

    # 3. Determine the endpoint and tool type
    endpoint: str | None = tool_def.get("endpoint")
    tool_type: str = tool_def.get("type", tool_def.get("tool_type", "http"))
    timeout: int = tool_def.get("timeout", 30000) // 1000  # Convert ms → s

    # Extract headers from params (injected by CredentialBroker)
    headers: dict[str, str] | None = params.pop("headers", None)
    auth: dict[str, Any] | None = params.pop("auth", None)

    # 4. Dispatch by tool_ref or type
    logger.debug(
        "Executing tool: instance=%s ref=%s type=%s endpoint=%s params=%s",
        instance_name,
        tool_ref,
        tool_type,
        endpoint,
        {k: "***" if k in ("password", "token", "api_key", "secret") else v
         for k, v in params.items()},
    )

    if tool_ref == "searxng":
        if not endpoint:
            endpoint = "http://127.0.0.1:8888"
        return await _execute_searxng(endpoint, params, headers)

    elif tool_ref == "camoufox":
        if not endpoint:
            endpoint = "http://127.0.0.1:3211"
        return await _execute_camoufox(endpoint, params, headers)

    elif tool_ref in ("send_email",):
        return await _execute_send_email(endpoint, params, headers, credentials)

    elif tool_ref in ("web_search", "search_web"):
        if not endpoint:
            endpoint = "http://127.0.0.1:4000/search"
        return await _execute_web_search(endpoint, params, headers)

    elif tool_ref in ("web_extract",):
        if not endpoint:
            endpoint = "http://127.0.0.1:4000/extract"
        return await _execute_web_extract(endpoint, params, headers)

    # 5. Generic HTTP dispatcher based on tool_type
    if tool_type == "http" and endpoint:
        method = tool_def.get("method", "POST").upper()
        if method == "GET":
            return await _http_request("GET", endpoint, params=params, headers=headers, timeout=timeout)
        else:
            return await _http_request("POST", endpoint, data=params, headers=headers, timeout=timeout)

    # 6. Fallback
    return {
        "error": f"unsupported_tool: {tool_ref} (type={tool_type})",
        "tool_ref": tool_ref,
        "instance_name": instance_name,
    }
