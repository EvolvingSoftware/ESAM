"""Credential Broker — security layer between LLM steps and credential-protected tool calls.

The LLM NEVER sees credentials. The broker:
1. Validates Delegation of Authority (DoA) constraints
2. Resolves credentials from the store based on step's tool bindings
3. Injects credentials into tool calls without the LLM seeing them
4. Redacts credentials from execution traces
"""

from __future__ import annotations

import logging
from typing import Any

from credential_store import CredentialStore

logger = logging.getLogger(__name__)

__all__ = [
    "CredentialBroker",
    "CredentialBrokerError",
    "AuthorizationError",
    "CredentialResolutionError",
]


# ── Exception hierarchy ──────────────────────────────────────────────


class CredentialBrokerError(Exception):
    """Base exception for all credential broker errors."""


class AuthorizationError(CredentialBrokerError):
    """Raised when a step violates Delegation of Authority constraints."""


class CredentialResolutionError(CredentialBrokerError):
    """Raised when credentials cannot be resolved for a step."""


# ── HTTP auth-header helpers ─────────────────────────────────────────


_AUTH_HEADER_MAP: dict[str, str] = {
    "api_key": "X-API-Key",
    "bearer_token": "Authorization",
    "oauth2": "Authorization",
    "smtp": "Authorization",
}

_SENSITIVE_KEYS = frozenset({
    "authorization",
    "x-api-key",
    "api_key",
    "api-key",
    "password",
    "passwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "client_secret",
    "private_key",
})

# ── CredentialBroker ─────────────────────────────────────────────────


class CredentialBroker:
    """Mediates between LLM steps and credential-protected tool calls.

    The broker ensures credentials are never exposed to the LLM by:
    1. Validating DoA constraints (tool_allowlist, credential_scope, cost limit)
    2. Resolving credentials from the store based on step's tool bindings
    3. Injecting credentials into tool calls without the LLM seeing them
    4. Redacting credentials from execution traces

    Args:
        credential_store: Instance with ``get_credential(id) -> dict``.
        tool_registry: Dict of tool definitions keyed by tool name
            (e.g. loaded from ``tools/registry.yaml``).
    """

    def __init__(
        self,
        credential_store: CredentialStore,
        tool_registry: dict[str, Any],
    ) -> None:
        self._store = credential_store
        self._tool_registry = tool_registry

    # ── DoA Validation ───────────────────────────────────────────────

    def validate_step_tool_access(
        self, step: dict, tool_instances: dict[str, Any]
    ) -> None:
        """Validate that a step is allowed to access its configured tools.

        Checks performed:
        - Every tool referenced in ``step.tools`` exists in ``tool_instances``
        - If ``step.authority.tool_allowlist`` is set, each tool must be in it
        - If ``step.authority.credential_scope`` is set, verify it is compatible

        Args:
            step: The workflow step dict (may contain ``tools``, ``authority``).
            tool_instances: Dict of tool-instance name → instance config
                declared in the workflow.

        Raises:
            AuthorizationError: On any validation violation.
        """
        tools: list[str] = _step_tools(step)
        authority: dict[str, Any] = _step_authority(step)

        for tool_name in tools:
            # 1. Tool must exist in the workflow's declared tool_instances
            if tool_name not in tool_instances:
                raise AuthorizationError(
                    f"Step references tool '{tool_name}' which is not "
                    f"declared in the workflow's tool_instances"
                )

            # 2. tool_allowlist check
            allowlist: list[str] | None = authority.get("tool_allowlist")
            if allowlist is not None and tool_name not in allowlist:
                raise AuthorizationError(
                    f"Tool '{tool_name}' is not in the authority's "
                    f"tool_allowlist {allowlist}"
                )

            # 3. credential_scope check — ensure the tool instance's
            #    credential_ref is compatible with the authority scope.
            cred_scope: str | None = authority.get("credential_scope")
            if cred_scope is not None:
                instance: dict[str, Any] = tool_instances[tool_name]
                # If the instance has a scope restriction and it doesn't
                # match the authority scope, reject.
                inst_scope: str | None = instance.get("credential_scope")
                if inst_scope is not None and inst_scope != cred_scope:
                    raise AuthorizationError(
                        f"Tool instance '{tool_name}' has credential_scope "
                        f"'{inst_scope}' but authority requires "
                        f"'{cred_scope}'"
                    )

    # ── Credential Resolution ────────────────────────────────────────

    def resolve_for_step(
        self, step: dict, workflow_tool_instances: dict[str, Any]
    ) -> dict[str, Any]:
        """Resolve credentials for all tool calls in this step.

        For each tool in the step, look up the tool definition in the
        registry, find the corresponding instance in
        ``workflow_tool_instances``, resolve the ``credential_ref`` via
        the credential store, and return a mapping.

        Args:
            step: The workflow step dict.
            workflow_tool_instances: Tool instances declared in the
                workflow YAML (name → config).

        Returns:
            ``{tool_name: {"credential_ref": ..., "credential_value": ...,
            "tool_config": ...}}``

        Raises:
            CredentialResolutionError: If a credential cannot be resolved.
        """
        tools: list[str] = _step_tools(step)
        authority: dict[str, Any] = _step_authority(step)
        resolved: dict[str, Any] = {}

        for tool_name in tools:
            # Look up the tool definition in the registry
            tool_def: dict[str, Any] | None = self._tool_registry.get(tool_name)
            if tool_def is None:
                # Not all tools need to be in the registry (ephemeral tools
                # may be defined elsewhere), but if there's no credential_ref
                # in the instance either, skip.
                instance: dict[str, Any] | None = workflow_tool_instances.get(tool_name)
                if instance and instance.get("credential_ref"):
                    raise CredentialResolutionError(
                        f"Tool '{tool_name}' has a credential_ref but no "
                        f"registry definition found"
                    )
                resolved[tool_name] = {
                    "credential_ref": None,
                    "credential_value": None,
                    "tool_config": instance or {},
                }
                continue

            # Determine credential_ref: instance overrides registry default
            instance = workflow_tool_instances.get(tool_name, {})
            credential_ref: str | None = (
                instance.get("credential_ref")
                or tool_def.get("credential_ref")
            )
            if credential_ref is None:
                resolved[tool_name] = {
                    "credential_ref": None,
                    "credential_value": None,
                    "tool_config": instance,
                }
                continue

            # Resolve via credential store
            cred_record: dict | None = self._store.get(credential_ref)
            if cred_record is None:
                raise CredentialResolutionError(
                    f"Credential ref '{credential_ref}' for tool "
                    f"'{tool_name}' not found in credential store"
                )

            # Apply credential_scope restriction
            cred_value: str = cred_record.get("encrypted_value", "")
            cred_scope: str | None = authority.get("credential_scope")
            if cred_scope is not None:
                # If the credential store record has a scope_step_id,
                # validate it's compatible (scoping prevents cross-tenant
                # credential use)
                scope_step_id: str | None = cred_record.get("scope_step_id")
                if scope_step_id is not None and scope_step_id != cred_scope:
                    raise CredentialResolutionError(
                        f"Credential '{credential_ref}' is scoped to "
                        f"step '{scope_step_id}' but authority scope is "
                        f"'{cred_scope}'"
                    )

            resolved[tool_name] = {
                "credential_ref": credential_ref,
                "credential_value": cred_value,
                "tool_config": instance,
            }

        return resolved

    # ── Credential Injection ─────────────────────────────────────────

    def inject_credentials(
        self, tool_name: str, params: dict[str, Any], resolved: dict[str, Any]
    ) -> dict[str, Any]:
        """Inject credentials into a tool call's parameters.

        For HTTP/API tools (Tier 3 permanent): add ``Authorization``
        header or API key parameter.
        For database tools (Tier 2 instance_scoped): inject connection
        credentials.
        For agent tools: skip (auth is session-level).

        Args:
            tool_name: Name of the tool being called.
            params: The parameters dict the LLM produced for the call.
            resolved: The resolved credentials dict for this tool
                (from ``resolve_for_step``).

        Returns:
            Augmented params dict with credential values injected.
        """
        tool_def: dict[str, Any] | None = self._tool_registry.get(tool_name)
        if tool_def is None:
            return dict(params)  # Unknown tool, return params unchanged

        tier: str = tool_def.get("tier", "ephemeral")
        credential_value: str | None = resolved.get("credential_value")
        if credential_value is None:
            return dict(params)

        result: dict[str, Any] = dict(params)

        if tier == "permanent":
            auth_type: str = tool_def.get("auth_type", "api_key")  # type: ignore[union-attr]  # narrowed above
            if auth_type in ("bearer_token", "oauth2"):
                if "headers" not in result:
                    result["headers"] = {}
                if auth_type == "bearer_token":
                    result["headers"]["Authorization"] = (
                        f"Bearer {credential_value}"
                    )
                else:  # oauth2
                    result["headers"]["Authorization"] = (
                        f"Bearer {credential_value}"
                    )
            elif auth_type == "api_key":
                if "headers" not in result:
                    result["headers"] = {}
                result["headers"]["X-API-Key"] = credential_value
            elif auth_type == "smtp":
                if "auth" not in result:
                    result["auth"] = {}
                result["auth"]["password"] = credential_value
            else:
                # Generic: inject as params.credential
                result["credential"] = credential_value

        elif tier == "instance_scoped":
            # For database / instance-scoped tools, inject credential
            # fields into the connection config.
            instance_template: dict | None = tool_def.get("instance_template")
            if instance_template:
                cred_fields: dict = instance_template.get("credential_fields", {})
                for field_name in cred_fields:
                    if field_name not in result:
                        result[field_name] = credential_value

        # Tier 1 (ephemeral/agent): skip — no credential injection needed

        return result

    # ── Credential Redaction ─────────────────────────────────────────

    def redact_credentials(self, tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
        """Remove credential values from execution traces and logs.

        - Strips ``Authorization``, ``X-API-Key`` headers from responses
        - Redacts known sensitive keys from result metadata
        - Returns a clean copy safe for storage in the trace DB

        Args:
            tool_name: Name of the tool that produced this result.
            result: The raw result dict from the tool execution.

        Returns:
            A sanitized copy of the result with credentials redacted.
        """
        redacted: dict[str, Any] = {}
        for key, value in result.items():
            key_lower: str = key.lower()
            if key_lower in _SENSITIVE_KEYS:
                redacted[key] = "••••••••"
            elif isinstance(value, dict):
                redacted[key] = self._redact_dict(value)
            elif isinstance(value, list):
                redacted[key] = [
                    self._redact_dict(item) if isinstance(item, dict)
                    else item
                    for item in value
                ]
            else:
                redacted[key] = value
        return redacted

    @staticmethod
    def _redact_dict(d: dict[str, Any]) -> dict[str, Any]:
        """Recursively redact sensitive keys from a dict."""
        result: dict[str, Any] = {}
        for key, value in d.items():
            key_lower: str = key.lower()
            if key_lower in _SENSITIVE_KEYS:
                result[key] = "••••••••"
            elif isinstance(value, dict):
                result[key] = CredentialBroker._redact_dict(value)
            elif isinstance(value, list):
                result[key] = [
                    CredentialBroker._redact_dict(item)
                    if isinstance(item, dict)
                    else item
                    for item in value
                ]
            else:
                result[key] = value
        return result

    # ── Cost Estimation & Limits ─────────────────────────────────────

    def estimate_cost(self, step: dict, resolved: dict[str, Any]) -> float:
        """Estimate the cost of executing this step's tool calls.

        Uses the tool registry ``cost_per_call`` multiplied by the number
        of configured tool instances.

        Args:
            step: The workflow step dict.
            resolved: The resolved credentials dict from
                ``resolve_for_step``.

        Returns:
            Estimated cost in USD cents.
        """
        total_cents: float = 0.0
        for tool_name in resolved:
            tool_def: dict[str, Any] | None = self._tool_registry.get(tool_name)
            if tool_def is None:
                continue
            cost_per_call: float = tool_def.get("cost_per_call", 0.0)
            total_cents += cost_per_call  # cost_per_call is already in cents

        return total_cents

    def check_cost_limit(
        self,
        step: dict,
        estimated_cost_cents: float,
        accumulated_cost_cents: float,
    ) -> bool:
        """Check if executing this step would exceed the authority's cost limit.

        Args:
            step: The workflow step dict.
            estimated_cost_cents: The estimated cost for this step
                (from ``estimate_cost``).
            accumulated_cost_cents: The accumulated cost so far in the run.

        Returns:
            ``True`` if the step is within the cost limit.

        Raises:
            AuthorizationError: If the step would exceed the limit and
                ``hard_gate`` is ``True``.
        """
        authority: dict[str, Any] = _step_authority(step)
        limit_cents: float = float(authority.get("cost_limit_cents", 10))
        hard_gate: bool = authority.get("hard_gate", True)

        total_cost: float = accumulated_cost_cents + estimated_cost_cents

        if total_cost <= limit_cents:
            return True

        if hard_gate:
            raise AuthorizationError(
                f"Estimated cost ${total_cost:.4f} would exceed "
                f"hard gate limit of ${limit_cents:.4f} "
                f"(accumulated={accumulated_cost_cents}, "
                f"step_estimated={estimated_cost_cents})"
            )

        logger.warning(
            "Soft gate exceeded: estimated cost $%.4f > limit $%.4f "
            "(accumulated=%s, step_estimated=%s)",
            total_cost,
            limit_cents,
            accumulated_cost_cents,
            estimated_cost_cents,
        )
        return False


# ── Internal helpers ─────────────────────────────────────────────────


def _step_tools(step: dict) -> list[str]:
    """Extract the list of tool names from a step dict."""
    raw = step.get("tools", [])
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        import json
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return [raw]
    return []


def _step_authority(step: dict) -> dict[str, Any]:
    """Extract the authority block from a step dict.

    Supports both a pre-parsed ``dict`` in ``step["authority"]`` and
    a JSON string in ``step.get("authority_json", "{}")`` (matching the
    DB schema in ``agent_workflow.py``).
    """
    authority = step.get("authority")
    if isinstance(authority, dict):
        return authority
    raw = step.get("authority_json", "{}")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        import json
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}
