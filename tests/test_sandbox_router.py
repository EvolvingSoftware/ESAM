"""Tests for SandboxRouter — NemoClaw sandbox execution isolation."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Ensure src is on the path
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from sandbox_router import SandboxRouter


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def router() -> SandboxRouter:
    """A SandboxRouter with a non-existent pool_path (safe for offline tests)."""
    return SandboxRouter(pool_path="/nonexistent/pool.py", ssh_alias="test-oracle")


# ── Test 1: ensure_sandbox offline graceful ──────────────────────────


class TestEnsureSandboxOfflineGraceful:
    """When pool.py is not reachable, ensure_sandbox returns an error dict gracefully."""

    def test_pool_not_found(self, router: SandboxRouter) -> None:
        """pool.py not found returns error status gracefully."""
        result = router.ensure_sandbox()
        assert isinstance(result, dict)
        assert result.get("status") == "error"
        assert "pool.py not found" in result.get("error", "")

    @patch("sandbox_router.subprocess.run")
    def test_subprocess_timeout(self, mock_run: MagicMock, router: SandboxRouter) -> None:
        """Subprocess timeout returns timeout status gracefully."""
        from subprocess import TimeoutExpired
        mock_run.side_effect = TimeoutExpired(cmd="pool.py ensure", timeout=30)

        result = router.ensure_sandbox()
        assert isinstance(result, dict)
        assert result.get("status") == "timeout"

    @patch("sandbox_router.subprocess.run")
    def test_nonzero_exit(self, mock_run: MagicMock, router: SandboxRouter) -> None:
        """Non-zero exit returns error dict with stderr."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Sandbox creation failed: insufficient resources"
        mock_run.return_value = mock_result

        result = router.ensure_sandbox()
        assert isinstance(result, dict)
        assert result.get("status") == "error"
        assert "insufficient resources" in result.get("error", "")

    @patch("sandbox_router.subprocess.run")
    def test_success_json_output(self, mock_run: MagicMock, router: SandboxRouter) -> None:
        """Successful ensure returns parsed JSON status."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"status": "ready", "sandbox": "esam-sandbox", "action": "none"}'
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        result = router.ensure_sandbox()
        assert isinstance(result, dict)
        assert result.get("status") == "ready"
        assert result.get("sandbox") == "esam-sandbox"

    @patch("sandbox_router.subprocess.run")
    def test_success_nonjson_output(self, mock_run: MagicMock, router: SandboxRouter) -> None:
        """Non-JSON stdout is still handled as ready with raw_output."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Sandbox esam-sandbox is Ready"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        result = router.ensure_sandbox()
        assert isinstance(result, dict)
        assert result.get("status") == "ready"
        assert "raw_output" in result


# ── Test 2: execute_in_sandbox builds payload ────────────────────────


class TestExecuteInSandboxBuildsPayload:
    """Verify the temp payload JSON is correct (Step 2 of execute_in_sandbox)."""

    @patch("sandbox_router.subprocess.run")
    def test_payload_structure(self, mock_run: MagicMock, router: SandboxRouter) -> None:
        """Payload JSON contains all required fields."""
        # Make scp succeed so we get to the payload write
        def mock_run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            result = MagicMock()
            result.returncode = 0
            result.stdout = '{"status": "ok", "output": "done"}'
            result.stderr = ""
            return result

        mock_run.side_effect = mock_run_side_effect

        prompt = "What is the customer's account balance?"
        tool_config = {
            "tools": ["customer_db_query"],
            "model": "gemma",
            "prompt": prompt,
        }
        credentials = {
            "customer_db_query": {
                "credential_ref": "db-cred-123",
                "credential_value": "postgres://user:pass@db.internal:5432/customers",
                "tool_config": {},
            }
        }
        step_id = "step-42"
        credential_scope = "customer-123"

        # Capture the payload written to disk
        with patch("builtins.open") as mock_open:
            mock_file = MagicMock()
            mock_open.return_value.__enter__.return_value = mock_file

            router.execute_in_sandbox(
                prompt=prompt,
                tool_config=tool_config,
                credentials=credentials,
                step_id=step_id,
                credential_scope=credential_scope,
            )

        # Verify open was called with the right path
        expected_path = f"/tmp/esam-payload-{step_id}.json"
        mock_open.assert_called_once_with(expected_path, "w")

        # Verify the written JSON has all required keys
        write_calls = mock_file.write.call_args_list
        written_content = "".join(call[0][0] for call in write_calls)
        payload = json.loads(written_content)

        assert payload["step_id"] == step_id
        assert payload["prompt"] == prompt
        assert payload["tool_config"] == tool_config
        assert payload["credentials"] == credentials
        assert payload["credential_scope"] == credential_scope

    @patch("sandbox_router.subprocess.run")
    def test_payload_contains_credential_scope(
        self, mock_run: MagicMock, router: SandboxRouter
    ) -> None:
        """credential_scope MUST be forwarded in the payload."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"output": "done"}'
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        with patch("builtins.open") as mock_open:
            mock_file = MagicMock()
            mock_open.return_value.__enter__.return_value = mock_file

            router.execute_in_sandbox(
                prompt="test",
                tool_config={"tools": []},
                credentials={},
                step_id="test-step",
                credential_scope="campaign-Q2",
            )

        write_calls = mock_file.write.call_args_list
        written_content = "".join(call[0][0] for call in write_calls)
        payload = json.loads(written_content)

        assert payload.get("credential_scope") == "campaign-Q2"

    @patch("sandbox_router.subprocess.run")
    def test_payload_no_scope_defaults_none(
        self, mock_run: MagicMock, router: SandboxRouter
    ) -> None:
        """When credential_scope is None, payload still sends None."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"output": "done"}'
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        with patch("builtins.open") as mock_open:
            mock_file = MagicMock()
            mock_open.return_value.__enter__.return_value = mock_file

            router.execute_in_sandbox(
                prompt="test",
                tool_config={"tools": []},
                credentials={},
                step_id="test-step",
                credential_scope=None,
            )

        write_calls = mock_file.write.call_args_list
        written_content = "".join(call[0][0] for call in write_calls)
        payload = json.loads(written_content)

        assert payload.get("credential_scope") is None

    def test_cleanup_on_scp_failure(self, router: SandboxRouter) -> None:
        """Local payload file is cleaned up when SCP fails."""
        with (
            patch("sandbox_router.subprocess.run") as mock_run,
            patch("os.remove") as mock_remove,
        ):
            # scp fails — second call to subprocess.run (for SCP) returns non-zero
            def side_effect(args: list[str], **kwargs: Any) -> MagicMock:
                result = MagicMock()
                if "scp" in args:
                    result.returncode = 1
                    result.stderr = "Connection refused"
                else:
                    result.returncode = 0
                    result.stdout = "{}"
                    result.stderr = ""
                return result

            mock_run.side_effect = side_effect

            with patch("builtins.open") as mock_open:
                mock_file = MagicMock()
                mock_open.return_value.__enter__.return_value = mock_file

                result = router.execute_in_sandbox(
                    prompt="test",
                    tool_config={"tools": []},
                    credentials={},
                    step_id="scp-fail-step",
                )

            assert "error" in result
            assert mock_remove.called


# ── Test 3: sandbox_skipped_when_disabled ────────────────────────────


class TestSandboxSkippedWhenDisabled:
    """Steps without sandbox: true should use the local path."""

    def _make_step(self, sandbox: bool = False, **overrides: Any) -> dict[str, Any]:
        step = {
            "id": "test-step",
            "step_type": "tool_call",
            "sandbox": sandbox,
            "tools": ["web_search"],
            "tools_json": '["web_search"]',
            "authority": {
                "tool_allowlist": ["web_search"],
                "credential_scope": "test-scope",
                "cost_limit_cents": 10,
            },
        }
        step.update(overrides)
        return step

    def _make_exec_ctx(self) -> Any:
        """Create a minimal ExecutorContext-like mock with numeric cost returns."""
        ctx = MagicMock()
        ctx.workflow_id = "test-workflow"
        ctx.run_id = "test-run"
        ctx.context = {"query": "hello"}
        ctx.accumulated_cost_cents = 0.0
        ctx.start_time = 0.0
        ctx.tool_instances = {
            "web_search": {
                "tool_ref": "web_search",
                "tier": "ephemeral",
                "credential_ref": None,
                "config": {},
            }
        }
        ctx.tool_registry = {}
        # Ensure estimate_cost returns a float, not a MagicMock
        ctx.credential_broker.estimate_cost.return_value = 0.0
        ctx.credential_broker.inject_credentials.side_effect = lambda name, params, resolved: params
        ctx.credential_broker.redact_credentials.side_effect = lambda name, result: result
        return ctx

    @patch("sandbox_router.SandboxRouter.execute_in_sandbox")
    def test_disabled_by_default(self, mock_sandbox: MagicMock) -> None:
        """A step without sandbox: true should NOT call router."""
        step = self._make_step(sandbox=False)
        exec_ctx = self._make_exec_ctx()

        # We need to test the logic inside _execute_tool_step, so we
        # test the sandbox check condition directly
        from workflow_executor import WorkflowExecutor

        executor = WorkflowExecutor()
        # Patch the tool_registry to have web_search
        executor._tool_registry["web_search"] = MagicMock(return_value={"result": "ok"})

        result = executor._execute_tool_step(step, exec_ctx)

        mock_sandbox.assert_not_called()
        assert "output" in result
        assert "web_search" in result["output"]

    @patch.dict(os.environ, {}, clear=True)
    def test_env_var_not_set(self) -> None:
        """ESAM_SANDBOX_ALL not set defaults to disabled."""
        from workflow_executor import WorkflowExecutor

        step = self._make_step(sandbox=False)
        exec_ctx = self._make_exec_ctx()

        executor = WorkflowExecutor()
        executor._tool_registry["web_search"] = MagicMock(return_value={"result": "ok"})

        with (
            patch("sandbox_router.SandboxRouter.execute_in_sandbox") as mock_sandbox,
        ):
            result = executor._execute_tool_step(step, exec_ctx)

        mock_sandbox.assert_not_called()
        assert result["output"]["web_search"]["result"] == "ok"


# ── Test 4: sandbox_enabled_with_env ─────────────────────────────────


class TestSandboxEnabledWithEnv:
    """ESAM_SANDBOX_ALL=1 enables sandbox even without step flag."""

    def _make_step(self, **overrides: Any) -> dict[str, Any]:
        step = {
            "id": "env-step",
            "step_type": "tool_call",
            "sandbox": False,
            "tools": ["web_search"],
            "tools_json": '["web_search"]',
            "prompt_template": "Search for {{query}}",
            "authority": {
                "tool_allowlist": ["web_search"],
                "credential_scope": "env-scope",
                "cost_limit_cents": 10,
            },
        }
        step.update(overrides)
        return step

    def _make_exec_ctx(self) -> Any:
        ctx = MagicMock()
        ctx.workflow_id = "test-workflow"
        ctx.run_id = "test-run"
        ctx.context = {"query": "sandbox test"}
        ctx.accumulated_cost_cents = 0.0
        ctx.start_time = 0.0
        ctx.tool_instances = {
            "web_search": {
                "tool_ref": "web_search",
                "tier": "ephemeral",
                "credential_ref": None,
                "config": {},
            }
        }
        ctx.tool_registry = {}
        # Ensure numeric returns from broker mock
        ctx.credential_broker.estimate_cost.return_value = 0.0
        ctx.credential_broker.inject_credentials.side_effect = lambda name, params, resolved: params
        ctx.credential_broker.redact_credentials.side_effect = lambda name, result: result
        return ctx

    @patch.dict(os.environ, {"ESAM_SANDBOX_ALL": "1"})
    def test_env_force_enables_sandbox(self) -> None:
        """ESAM_SANDBOX_ALL=1 forces sandbox even when step.sandbox is False."""
        from workflow_executor import WorkflowExecutor

        step = self._make_step(sandbox=False)
        exec_ctx = self._make_exec_ctx()

        executor = WorkflowExecutor()

        with patch("sandbox_router.SandboxRouter") as MockRouter:
            mock_instance = MagicMock()
            MockRouter.return_value = mock_instance
            mock_instance.ensure_sandbox.return_value = {"status": "ready"}
            mock_instance.execute_in_sandbox.return_value = {
                "output": {"sandbox_result": "executed"}
            }

            result = executor._execute_tool_step(step, exec_ctx)

        mock_instance.ensure_sandbox.assert_called_once()
        mock_instance.execute_in_sandbox.assert_called_once()
        # Credential scope from authority should be passed
        _, kwargs = mock_instance.execute_in_sandbox.call_args
        assert kwargs.get("credential_scope") == "env-scope"

    @patch.dict(os.environ, {"ESAM_SANDBOX_ALL": "0"})
    def test_env_zero_does_not_force(self) -> None:
        """ESAM_SANDBOX_ALL=0 without step flag stays local."""
        from workflow_executor import WorkflowExecutor

        step = self._make_step(sandbox=False)
        exec_ctx = self._make_exec_ctx()

        executor = WorkflowExecutor()
        executor._tool_registry["web_search"] = MagicMock(return_value={"result": "ok"})

        with patch("sandbox_router.SandboxRouter") as MockRouter:
            result = executor._execute_tool_step(step, exec_ctx)

        MockRouter.assert_not_called()
        assert result["output"]["web_search"]["result"] == "ok"


# ── Test 5: scp_commands_format ──────────────────────────────────────


class TestScpCommandsFormat:
    """Verify SCP command strings are correctly built."""

    def test_scp_to_oracle_format(self, router: SandboxRouter) -> None:
        """_scp_to_oracle builds correct SCP command."""
        with patch("sandbox_router.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stderr = ""
            mock_run.return_value = mock_result

            router._scp_to_oracle("/tmp/local.json", "/tmp/remote.json")

        args = mock_run.call_args[0][0]
        assert args[0] == "scp"
        assert args[1] == "/tmp/local.json"
        assert args[2] == "test-oracle:/tmp/remote.json"

    def test_scp_from_oracle_format(self, router: SandboxRouter) -> None:
        """_scp_from_oracle builds correct SCP command."""
        with patch("sandbox_router.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stderr = ""
            mock_run.return_value = mock_result

            router._scp_from_oracle("/tmp/remote.json", "/tmp/local.json")

        args = mock_run.call_args[0][0]
        assert args[0] == "scp"
        assert args[1] == "test-oracle:/tmp/remote.json"
        assert args[2] == "/tmp/local.json"

    def test_scp_to_oracle_failure(self, router: SandboxRouter) -> None:
        """SCP failure returns error dict."""
        with patch("sandbox_router.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stderr = "Connection timed out"
            mock_run.return_value = mock_result

            result = router._scp_to_oracle("/tmp/a.json", "/tmp/b.json")

        assert result["status"] == "error"
        assert "Connection timed out" in result["error"]

    def test_scp_to_oracle_timeout(self, router: SandboxRouter) -> None:
        """SCP timeout returns error dict gracefully."""
        with patch("sandbox_router.subprocess.run") as mock_run:
            from subprocess import TimeoutExpired
            mock_run.side_effect = TimeoutExpired(cmd="scp", timeout=60)

            result = router._scp_to_oracle("/tmp/a.json", "/tmp/b.json")

        assert result["status"] == "error"
        assert "timed out" in result["error"].lower()


# ── Test 6: sandbox_status ───────────────────────────────────────────


class TestSandboxStatus:
    """Sandbox status endpoint behavior."""

    def test_status_pool_not_found(self, router: SandboxRouter) -> None:
        """Status returns error gracefully when pool.py is missing."""
        result = router.sandbox_status()
        assert isinstance(result, dict)
        assert result.get("status") == "error"
        assert "pool.py not found" in result.get("error", "")

    @patch("sandbox_router.subprocess.run")
    def test_status_success(self, mock_run: MagicMock, router: SandboxRouter) -> None:
        """Status returns parsed JSON from pool.py."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            '{"sandbox": {"name": "esam-sandbox"}, "phase": "Ready", "exists": true}'
        )
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        result = router.sandbox_status()
        assert result.get("sandbox", {}).get("name") == "esam-sandbox"
        assert result.get("phase") == "Ready"

    @patch("sandbox_router.subprocess.run")
    def test_status_nonjson(self, mock_run: MagicMock, router: SandboxRouter) -> None:
        """Non-JSON status output is handled gracefully."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Sandbox: Running\nPhase: Ready\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        result = router.sandbox_status()
        assert result.get("status") == "unknown"
        assert "raw_output" in result


# ── Test 7: credential_scope propagation ─────────────────────────────


class TestCredentialScopePropagation:
    """credential_scope MUST be forwarded to sandbox for network policy."""

    @patch("sandbox_router.subprocess.run")
    def test_scope_forwarded_in_execute(
        self, mock_run: MagicMock, router: SandboxRouter
    ) -> None:
        """credential_scope is passed through to execute_in_sandbox."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"output": "done"}'
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        with patch("builtins.open") as mock_open:
            mock_file = MagicMock()
            mock_open.return_value.__enter__.return_value = mock_file

            router.execute_in_sandbox(
                prompt="test",
                tool_config={"tools": []},
                credentials={},
                step_id="scope-test",
                credential_scope="restricted-tenant",
            )

        write_calls = mock_file.write.call_args_list
        written_content = "".join(call[0][0] for call in write_calls)
        payload = json.loads(written_content)

        assert payload["credential_scope"] == "restricted-tenant"


# ── Test 8: error handling ───────────────────────────────────────────


class TestErrorHandling:
    """SandboxRouter must NOT crash when Oracle is unreachable."""

    def test_every_public_method_returns_dict(self) -> None:
        """All public methods return a dict (not raise)."""
        router = SandboxRouter(pool_path="/does/not/exist/pool.py")

        # These should not raise
        ensure_result = router.ensure_sandbox()
        status_result = router.sandbox_status()

        assert isinstance(ensure_result, dict)
        assert isinstance(status_result, dict)
        # execute_in_sandbox handles FileNotFoundError for ssh
        result = router.execute_in_sandbox(
            prompt="test", tool_config={}, credentials={}, step_id="error-step"
        )
        assert isinstance(result, dict)

    @patch("sandbox_router.subprocess.run")
    def test_ssh_binary_not_found(self, mock_run: MagicMock, router: SandboxRouter) -> None:
        """Missing ssh binary returns error gracefully."""
        import subprocess

        # Simulate FileNotFoundError on scp call
        real_run = subprocess.run

        def side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            cmd = args[0] if args else kwargs.get("args", [])
            if isinstance(cmd, list) and cmd[0] in ("scp",):
                raise FileNotFoundError("scp: no such file or directory")
            if isinstance(cmd, list) and cmd[0] == "ssh":
                raise FileNotFoundError("ssh: no such file or directory")
            return real_run(cmd, capture_output=True, text=True, timeout=30)

        mock_run.side_effect = side_effect

        result = router.execute_in_sandbox(
            prompt="test", tool_config={}, credentials={}, step_id="ssh-fail"
        )
        assert isinstance(result, dict)
        assert "error" in result
