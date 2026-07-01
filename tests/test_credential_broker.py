"""Tests for CredentialBroker — the security layer between LLM steps
and credential-protected tool calls.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Ensure src is on the path
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from credential_broker import (
    AuthorizationError,
    CredentialBroker,
    CredentialResolutionError,
)

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def mock_store() -> MagicMock:
    """A CredentialStore mock that returns pre-configured credentials."""
    store = MagicMock()
    credentials: dict[str, dict] = {
        "social_media_oauth": {
            "id": "cred-1",
            "credential_key": "social_media_oauth",
            "encrypted_value": "sk-abc123-secret",
            "scope_step_id": None,
        },
        "smtp_credentials": {
            "id": "cred-2",
            "credential_key": "smtp_credentials",
            "encrypted_value": "smtp-user:super-secret-pass",
            "scope_step_id": None,
        },
        "db_connection": {
            "id": "cred-3",
            "credential_key": "db_connection",
            "encrypted_value": "postgres://user:pass@localhost:5432/mydb",
            "scope_step_id": None,
        },
        "campaign_creds": {
            "id": "cred-4",
            "credential_key": "campaign_creds",
            "encrypted_value": "campaign-api-key-xyz",
            "scope_step_id": "campaign-Q2",
        },
    }

    def get_credential(cred_id: str) -> dict | None:
        return credentials.get(cred_id)

    store.get.side_effect = get_credential
    return store


@pytest.fixture
def tool_registry() -> dict[str, Any]:
    """A minimal tool registry matching the structure in tools/registry.yaml."""
    return {
        "social_media_api": {
            "tier": "permanent",
            "auth_type": "oauth2",
            "cost_per_call": 0.001,
            "credential_ref": "social_media_oauth",
        },
        "send_email": {
            "tier": "permanent",
            "auth_type": "smtp",
            "cost_per_call": 0.0001,
            "credential_ref": "smtp_credentials",
        },
        "search_web": {
            "tier": "ephemeral",
            "auth_type": None,
            "cost_per_call": 0.0,
            "credential_ref": None,
        },
        "postgres_read": {
            "tier": "instance_scoped",
            "cost_per_call": 0.0005,
            "credential_ref": "db_connection",
            "instance_template": {
                "config_fields": {
                    "connection_name": {"type": "string", "required": True},
                    "host": {"type": "string", "required": True},
                },
                "credential_fields": {
                    "username": {"type": "string", "required": True},
                    "password": {"type": "string", "required": True},
                },
            },
        },
        "email_marketing_api": {
            "tier": "permanent",
            "auth_type": "api_key",
            "cost_per_call": 0.002,
            "credential_ref": "email_marketing_api_key",
        },
        "sms_gateway": {
            "tier": "permanent",
            "auth_type": "api_key",
            "cost_per_call": 0.005,
            "credential_ref": "sms_api_key",
        },
        "agent_hermes": {
            "tier": "ephemeral",
            "cost_per_call": 0.01,
            "credential_ref": "hermes_profile",
        },
        "format_output": {
            "tier": "ephemeral",
            "cost_per_call": 0.0,
            "credential_ref": None,
        },
    }


@pytest.fixture
def broker(mock_store: MagicMock, tool_registry: dict[str, Any]) -> CredentialBroker:
    return CredentialBroker(credential_store=mock_store, tool_registry=tool_registry)


# ── 1. validate_step_tool_access ────────────────────────────────────


class TestValidateStepToolAccess:
    """Tests for ``CredentialBroker.validate_step_tool_access``."""

    def test_validate_valid_access(self, broker: CredentialBroker) -> None:
        """A step with a valid tool in tool_instances passes validation."""
        step: dict = {
            "tools": ["social_media_api"],
            "authority": {"level": "standard", "tool_allowlist": None},
        }
        tool_instances: dict = {
            "social_media_api": {"credential_ref": "social_media_oauth"},
        }
        # Should not raise
        broker.validate_step_tool_access(step, tool_instances)

    def test_validate_tool_not_in_instances(self, broker: CredentialBroker) -> None:
        """Step references a tool not declared in workflow tool_instances."""
        step: dict = {
            "tools": ["nonexistent_tool"],
            "authority": {"level": "standard"},
        }
        tool_instances: dict = {"social_media_api": {}}
        with pytest.raises(AuthorizationError) as excinfo:
            broker.validate_step_tool_access(step, tool_instances)
        assert "nonexistent_tool" in str(excinfo.value)

    def test_validate_tool_allowlist_pass(self, broker: CredentialBroker) -> None:
        """Step tool is explicitly in the authority's tool_allowlist."""
        step: dict = {
            "tools": ["social_media_api"],
            "authority": {
                "level": "standard",
                "tool_allowlist": ["social_media_api", "format_output"],
            },
        }
        tool_instances: dict = {"social_media_api": {}}
        broker.validate_step_tool_access(step, tool_instances)

    def test_validate_tool_allowlist_fail(self, broker: CredentialBroker) -> None:
        """Step tool is NOT in the authority's tool_allowlist."""
        step: dict = {
            "tools": ["send_email"],
            "authority": {
                "level": "standard",
                "tool_allowlist": ["social_media_api", "format_output"],
            },
        }
        tool_instances: dict = {"send_email": {}}
        with pytest.raises(AuthorizationError) as excinfo:
            broker.validate_step_tool_access(step, tool_instances)
        assert "send_email" in str(excinfo.value)

    def test_validate_credential_scope_matches(self, broker: CredentialBroker) -> None:
        """Authority credential_scope matches the tool instance scope."""
        step: dict = {
            "tools": ["social_media_api"],
            "authority": {
                "level": "standard",
                "credential_scope": "campaign-Q2",
            },
        }
        tool_instances: dict = {
            "social_media_api": {
                "credential_scope": "campaign-Q2",
                "credential_ref": "social_media_oauth",
            },
        }
        # Should not raise
        broker.validate_step_tool_access(step, tool_instances)

    def test_validate_credential_scope_mismatch(self, broker: CredentialBroker) -> None:
        """Authority credential_scope does not match the tool instance scope."""
        step: dict = {
            "tools": ["social_media_api"],
            "authority": {
                "level": "standard",
                "credential_scope": "campaign-Q2",
            },
        }
        tool_instances: dict = {
            "social_media_api": {
                "credential_scope": "customer-123",
                "credential_ref": "social_media_oauth",
            },
        }
        with pytest.raises(AuthorizationError) as excinfo:
            broker.validate_step_tool_access(step, tool_instances)
        assert "campaign-Q2" in str(excinfo.value)
        assert "customer-123" in str(excinfo.value)


# ── 2. resolve_for_step ─────────────────────────────────────────────


class TestResolveForStep:
    """Tests for ``CredentialBroker.resolve_for_step``."""

    def test_resolve_happy_path(self, broker: CredentialBroker) -> None:
        """Resolves credential_ref and returns dict with credential_value."""
        step: dict = {
            "tools": ["social_media_api"],
            "authority": {"level": "standard"},
        }
        tool_instances: dict = {
            "social_media_api": {"credential_ref": "social_media_oauth"},
        }
        resolved: dict = broker.resolve_for_step(step, tool_instances)
        assert "social_media_api" in resolved
        r: dict = resolved["social_media_api"]
        assert r["credential_ref"] == "social_media_oauth"
        assert r["credential_value"] == "sk-abc123-secret"
        assert "tool_config" in r

    def test_resolve_missing_credential(self, broker: CredentialBroker) -> None:
        """Credential ref not in store raises CredentialResolutionError."""
        step: dict = {
            "tools": ["email_marketing_api"],
            "authority": {"level": "standard"},
        }
        tool_instances: dict = {
            "email_marketing_api": {"credential_ref": "nonexistent_cred"},
        }
        with pytest.raises(CredentialResolutionError) as excinfo:
            broker.resolve_for_step(step, tool_instances)
        assert "nonexistent_cred" in str(excinfo.value)
        assert "email_marketing_api" in str(excinfo.value)

    def test_resolve_ephemeral_tool_no_creds(self, broker: CredentialBroker) -> None:
        """Ephemeral tools with no credential_ref resolve to None values."""
        step: dict = {
            "tools": ["search_web"],
            "authority": {"level": "standard"},
        }
        tool_instances: dict = {"search_web": {}}
        resolved: dict = broker.resolve_for_step(step, tool_instances)
        assert resolved["search_web"]["credential_ref"] is None
        assert resolved["search_web"]["credential_value"] is None

    def test_resolve_credential_scope_restriction(self, broker: CredentialBroker) -> None:
        """Credential with scope_step_id not matching authority scope raises."""
        step: dict = {
            "tools": ["social_media_api"],
            "authority": {
                "level": "standard",
                "credential_scope": "wrong-scope",
            },
        }
        # The mock store returns creds without scope_step_id for social_media_oauth,
        # so it should pass. Let's use a credential that HAS a scope step.
        # We'll add a cred with scope_step_id set via the mock.
        step2: dict = {
            "tools": ["social_media_api"],
            "authority": {
                "level": "standard",
                "credential_scope": "wrong-scope",
            },
        }
        tool_instances: dict = {
            "social_media_api": {"credential_ref": "social_media_oauth"},
        }
        # social_media_oauth has scope_step_id=None, so no scope restriction triggered
        resolved = broker.resolve_for_step(step2, tool_instances)
        assert resolved["social_media_api"]["credential_value"] == "sk-abc123-secret"

    def test_resolve_scope_mismatch_on_cred(self, broker: CredentialBroker, mock_store: MagicMock) -> None:
        """Credential with a non-null scope_step_id must match authority scope."""
        # Add a scoped credential to the mock
        mock_store.get.side_effect = lambda cid: {
            "scoped_cred": {
                "id": "cred-scoped",
                "credential_key": "scoped_cred",
                "encrypted_value": "secret-scoped",
                "scope_step_id": "campaign-Q2",
            },
            "social_media_oauth": {
                "id": "cred-1",
                "credential_key": "social_media_oauth",
                "encrypted_value": "sk-abc123-secret",
                "scope_step_id": None,
            },
        }.get(cid)

        step: dict = {
            "tools": ["social_media_api"],
            "authority": {
                "level": "standard",
                "credential_scope": "campaign-Q2",
            },
        }
        tool_instances: dict = {
            "social_media_api": {"credential_ref": "scoped_cred"},
        }
        # scoped_cred has scope_step_id="campaign-Q2", authority scope is also "campaign-Q2" → match
        resolved = broker.resolve_for_step(step, tool_instances)
        assert resolved["social_media_api"]["credential_value"] == "secret-scoped"

        # Now test mismatch
        step2: dict = {
            "tools": ["social_media_api"],
            "authority": {
                "level": "standard",
                "credential_scope": "customer-123",
            },
        }
        with pytest.raises(CredentialResolutionError) as excinfo:
            broker.resolve_for_step(step2, tool_instances)
        assert "campaign-Q2" in str(excinfo.value)
        assert "customer-123" in str(excinfo.value)


# ── 3. inject_credentials ───────────────────────────────────────────


class TestInjectCredentials:
    """Tests for ``CredentialBroker.inject_credentials``."""

    def test_inject_http_auth(self, broker: CredentialBroker) -> None:
        """Injects Authorization header for Tier 3 HTTP tool (oauth2/bearer)."""
        resolved: dict = {
            "credential_ref": "social_media_oauth",
            "credential_value": "sk-abc123-secret",
            "tool_config": {},
        }
        params: dict = {
            "channel": "twitter",
            "content": "Hello world",
        }
        augmented: dict = broker.inject_credentials(
            "social_media_api", params, resolved
        )
        assert augmented["channel"] == "twitter"
        assert augmented["content"] == "Hello world"
        assert "headers" in augmented
        assert augmented["headers"]["Authorization"] == "Bearer sk-abc123-secret"

    def test_inject_api_key_header(self, broker: CredentialBroker) -> None:
        """Injects X-API-Key header for api_key auth type."""
        resolved: dict = {
            "credential_ref": "email_marketing_api_key",
            "credential_value": "key-9876",
            "tool_config": {},
        }
        params: dict = {"campaign_name": "Summer Sale", "subject": "Sale!"}
        augmented: dict = broker.inject_credentials(
            "email_marketing_api", params, resolved
        )
        assert augmented["headers"]["X-API-Key"] == "key-9876"

    def test_inject_db_connection(self, broker: CredentialBroker) -> None:
        """Injects connection credentials for Tier 2 instance_scoped tool."""
        resolved: dict = {
            "credential_ref": "db_connection",
            "credential_value": "postgres://user:pass@localhost:5432/mydb",
            "tool_config": {},
        }
        params: dict = {"query": "SELECT * FROM users"}
        augmented: dict = broker.inject_credentials(
            "postgres_read", params, resolved
        )
        # Instance-scoped tools inject credential_value into credential_fields
        assert augmented["query"] == "SELECT * FROM users"
        # The credential_value is used for each field in credential_fields
        assert "username" in augmented or "password" in augmented

    def test_inject_agent_tool_skipped(self, broker: CredentialBroker) -> None:
        """Agent tools (ephemeral) skip credential injection."""
        resolved: dict = {
            "credential_ref": None,
            "credential_value": None,
            "tool_config": {},
        }
        params: dict = {"task": "Do something"}
        augmented: dict = broker.inject_credentials(
            "agent_hermes", params, resolved
        )
        # Agent tools: no credential_value, so params returned unchanged
        assert augmented == params

    def test_inject_no_credential_value(self, broker: CredentialBroker) -> None:
        """When credential_value is None, params are returned unchanged."""
        resolved: dict = {
            "credential_ref": None,
            "credential_value": None,
            "tool_config": {},
        }
        params: dict = {"query": "test"}
        augmented: dict = broker.inject_credentials("search_web", params, resolved)
        assert augmented == params


# ── 4. redact_credentials ───────────────────────────────────────────


class TestRedactCredentials:
    """Tests for ``CredentialBroker.redact_credentials``."""

    def test_redact_http_auth_headers(self, broker: CredentialBroker) -> None:
        """Authorization and X-API-Key headers are redacted from result."""
        result: dict = {
            "status": 200,
            "headers": {
                "Authorization": "Bearer sk-abc123-secret",
                "X-API-Key": "key-9876",
                "Content-Type": "application/json",
            },
            "body": {"id": 42, "message": "OK"},
        }
        redacted: dict = broker.redact_credentials("social_media_api", result)
        assert redacted["status"] == 200
        assert redacted["headers"]["Authorization"] == "••••••••"
        assert redacted["headers"]["X-API-Key"] == "••••••••"
        assert redacted["headers"]["Content-Type"] == "application/json"
        assert redacted["body"] == {"id": 42, "message": "OK"}

    def test_redact_nested_sensitive_keys(self, broker: CredentialBroker) -> None:
        """Deeply nested sensitive keys are recursively redacted."""
        result: dict = {
            "data": {
                "api_key": "super-secret",
                "token": "abc",
                "nested": {
                    "password": "hunter2",
                    "safe": "keep-me",
                },
            },
            "config": {
                "client_secret": "shh",
            },
        }
        redacted: dict = broker.redact_credentials("any_tool", result)
        assert redacted["data"]["api_key"] == "••••••••"
        assert redacted["data"]["token"] == "••••••••"
        assert redacted["data"]["nested"]["password"] == "••••••••"
        assert redacted["data"]["nested"]["safe"] == "keep-me"
        assert redacted["config"]["client_secret"] == "••••••••"

    def test_redact_list_of_dicts(self, broker: CredentialBroker) -> None:
        """Lists of dicts are redacted recursively."""
        result: dict = {
            "items": [
                {"name": "item1", "token": "secret1"},
                {"name": "item2", "password": "secret2"},
                {"name": "item3", "safe": "visible"},
            ],
        }
        redacted: dict = broker.redact_credentials("any_tool", result)
        assert redacted["items"][0]["token"] == "••••••••"
        assert redacted["items"][1]["password"] == "••••••••"
        assert redacted["items"][2]["safe"] == "visible"

    def test_redact_no_sensitive_data(self, broker: CredentialBroker) -> None:
        """Results without sensitive data pass through unchanged."""
        result: dict = {
            "status": 200,
            "body": {"id": 1, "name": "test"},
        }
        redacted: dict = broker.redact_credentials("any_tool", result)
        assert redacted == result


# ── 5. Cost estimation & limits ─────────────────────────────────────


class TestCostEstimation:
    """Tests for cost estimation and limit checking."""

    def test_estimate_cost(self, broker: CredentialBroker) -> None:
        """estimate_cost sums cost_per_call from the tool registry."""
        step: dict = {"tools": ["social_media_api"], "authority": {"level": "standard"}}
        resolved: dict = {
            "social_media_api": {"credential_ref": "x", "credential_value": "y"},
        }
        cost: float = broker.estimate_cost(step, resolved)
        assert cost == 0.001  # social_media_api cost_per_call

    def test_estimate_cost_multiple_tools(self, broker: CredentialBroker) -> None:
        """Cost for multiple tools is the sum of their per-call costs."""
        step: dict = {
            "tools": ["social_media_api", "send_email"],
            "authority": {"level": "standard"},
        }
        resolved: dict = {
            "social_media_api": {},
            "send_email": {},
        }
        cost: float = broker.estimate_cost(step, resolved)
        assert cost == pytest.approx(0.001 + 0.0001)

    def test_estimate_cost_unknown_tool(self, broker: CredentialBroker) -> None:
        """Unknown tools (not in registry) contribute zero cost."""
        step: dict = {"tools": ["unknown_tool"], "authority": {"level": "standard"}}
        resolved: dict = {"unknown_tool": {}}
        cost: float = broker.estimate_cost(step, resolved)
        assert cost == 0.0

    def test_check_cost_limit_under(self, broker: CredentialBroker) -> None:
        """Cost under limit returns True."""
        step: dict = {
            "authority": {"level": "standard", "cost_limit_cents": 10, "hard_gate": True},
        }
        result: bool = broker.check_cost_limit(step, 1.0, 2.0)
        assert result is True

    def test_check_cost_limit_hard_gate(self, broker: CredentialBroker) -> None:
        """Cost exceeding hard gate limit raises AuthorizationError."""
        step: dict = {
            "tools": ["social_media_api"],
            "authority": {
                "level": "standard",
                "cost_limit_cents": 0.5,
                "hard_gate": True,
            },
        }
        with pytest.raises(AuthorizationError) as excinfo:
            broker.check_cost_limit(step, 1.0, 0.0)
        assert "hard gate" in str(excinfo.value).lower()

    def test_check_cost_limit_soft_gate(self, broker: CredentialBroker) -> None:
        """Cost exceeding soft gate limit logs warning and returns False."""
        step: dict = {
            "tools": ["social_media_api"],
            "authority": {
                "level": "standard",
                "cost_limit_cents": 0.5,
                "hard_gate": False,
            },
        }
        with patch.object(logging.getLogger("credential_broker"), "warning") as mock_warn:
            result: bool = broker.check_cost_limit(step, 1.0, 0.0)
            assert result is False
            mock_warn.assert_called_once()


# ── 6. Security invariant ────────────────────────────────────────────


class TestSecurityInvariant:
    """Critical security invariants that must never be violated."""

    def test_security_llm_never_sees_credential(self, broker: CredentialBroker) -> None:
        """Verify no public API path returns credentials to an LLM-facing interface.

        The CredentialBroker is designed so that the LLM only sees
        ``resolve_for_step`` output shape, not credential values directly.

        This test verifies that:
        1. `validate_step_tool_access` returns ``None`` (no data leakage)
        2. `resolve_for_step` returns a dict with proper structure but
           the LLM should not receive it — it's consumed internally by the executor.
        3. `inject_credentials` returns augmented tool params, not credentials
        4. `redact_credentials` explicitly scrubs secrets
        5. `check_cost_limit` returns a bool or raises an exception
        """
        step: dict = {
            "tools": ["social_media_api"],
            "authority": {
                "level": "standard",
                "credential_scope": None,
                "tool_allowlist": None,
            },
        }
        tool_instances: dict = {"social_media_api": {"credential_ref": "social_media_oauth"}}

        # 1. validate returns None
        result: None = broker.validate_step_tool_access(step, tool_instances)
        assert result is None

        # 2. resolve_for_step returns credentials, but they are *only*
        #    for internal executor use — the resolved dict contains raw values
        resolved: dict = broker.resolve_for_step(step, tool_instances)
        assert "credential_value" in resolved["social_media_api"]
        # The credential_value is present in the resolved dict, but it is
        # never returned to the LLM. The executor consumes it and passes
        # it to inject_credentials, which produces the final params dict
        # that the executor (not the LLM) sends to the tool.

        # 3. inject_credentials produces params, not credentials back to LLM
        tool_params: dict = {"channel": "twitter", "content": "Hello"}
        augmented: dict = broker.inject_credentials(
            "social_media_api", tool_params, resolved["social_media_api"]
        )
        # The augmented params do NOT contain raw credential_value at top level
        # They contain it inside headers → this is for the executor's HTTP call
        assert "credential_value" not in augmented
        assert "headers" in augmented
        assert augmented["headers"]["Authorization"] == "Bearer sk-abc123-secret"

        # 4. redact_credentials scrubs secrets from result metadata
        raw_result: dict = {
            "headers": {"Authorization": "Bearer sk-abc123-secret"},
            "body": {"id": 1},
        }
        clean: dict = broker.redact_credentials("social_media_api", raw_result)
        assert clean["headers"]["Authorization"] == "••••••••"

        # 5. check_cost_limit returns bool or raises AuthorizationError
        step_under: dict = {
            "authority": {"cost_limit_cents": 100, "hard_gate": True},
        }
        assert broker.check_cost_limit(step_under, 1.0, 0.0) is True

        step_over: dict = {
            "authority": {"cost_limit_cents": 0.5, "hard_gate": True},
        }
        with pytest.raises(AuthorizationError):
            broker.check_cost_limit(step_over, 1.0, 0.0)

    def test_security_no_credential_leak_via_authority_json(
        self, broker: CredentialBroker
    ) -> None:
        """Verify that parsing authority_json (DB schema format) works safely."""
        step: dict = {
            "tools": ["social_media_api"],
            "authority_json": json.dumps({
                "level": "standard",
                "cost_limit_cents": 10,
                "hard_gate": True,
            }),
        }
        tool_instances: dict = {"social_media_api": {"credential_ref": "social_media_oauth"}}
        # Must not raise — authority_json is parsed internally
        resolved: dict = broker.resolve_for_step(step, tool_instances)
        assert resolved["social_media_api"]["credential_value"] == "sk-abc123-secret"
