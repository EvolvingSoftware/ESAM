"""Workflow execution context with credential brokering and Delegation of Authority support."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional


class AuthorizationError(Exception):
    """Raised when a step violates Delegation of Authority constraints."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass
class ExecutorContext:
    """Context passed through all step executions.

    Carries credential brokering, tool registry, DoA state, cost
    tracking, and template rendering state across every step of a
    workflow run.
    """

    workflow_id: str
    run_id: str
    credential_broker: Any  # CredentialBroker — imported lazily to avoid circular deps
    tool_registry: dict[str, Any]
    tool_instances: dict[str, Any]  # {name: {tool_ref, tier, credential_ref, config}}
    credential_store: Any  # CredentialStore
    context: dict[str, Any] = field(default_factory=dict)
    accumulated_cost_cents: float = 0.0
    start_time: float = field(default_factory=time.time)
    input_vars: dict[str, Any] = field(default_factory=dict)
    step_results: dict[str, dict[str, Any]] = field(default_factory=dict)
