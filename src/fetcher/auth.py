"""Auth resolver — resolves credential references into HTTP auth headers/params."""

from __future__ import annotations

import base64
from typing import Any


class AuthResolver:
    """Resolves authentication references into injectable HTTP headers and params.

    Supports three auth modes:

    - ``api_key`` — placed in a header (default ``X-API-Key``) or query param
    - ``bearer_token`` — placed in ``Authorization: Bearer <token>``
    - ``basic_auth`` — placed in ``Authorization: Basic <b64>``

    Args:
        default_api_key_header: Default header name for API key auth.
    """

    def __init__(self, default_api_key_header: str = "X-API-Key") -> None:
        self._default_api_key_header = default_api_key_header

    def resolve(
        self,
        auth_ref: str | None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve an *auth_ref* into injectable headers and params.

        Args:
            auth_ref: Credential reference string.  If ``None`` or empty,
                returns an empty result.
            context: Optional dict containing credential values. The
                resolver looks up ``auth_ref`` in *context* to find the
                credential value and auth type.

                Expected context shape:
                ``{<auth_ref>: {"type": str, "value": str, ...}}``

        Returns:
            ``{"headers": {...}, "params": {...}}`` — values to inject
            into the HTTP request.  Returns empty dicts if *auth_ref*
            is falsy or the credential is not found.
        """
        if not auth_ref:
            return {"headers": {}, "params": {}}

        ctx = context or {}
        cred = ctx.get(auth_ref)
        if cred is None:
            return {"headers": {}, "params": {}}

        auth_type = cred.get("type", "api_key")
        value = cred.get("value", "")

        headers: dict[str, str] = {}
        params: dict[str, str] = {}

        if auth_type == "api_key":
            # Determine placement: header (default) or query param
            placement = cred.get("placement", "header")
            key_name = cred.get("key_name", self._default_api_key_header)
            if placement == "query":
                params[key_name] = value
            else:
                headers[key_name] = value

        elif auth_type == "bearer_token":
            headers["Authorization"] = f"Bearer {value}"

        elif auth_type == "basic_auth":
            # Expect value to be "username:password"
            encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {encoded}"

        return {"headers": headers, "params": params}
