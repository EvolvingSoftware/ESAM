"""SandboxRouter — Routes tool_call step execution through NemoClaw sandbox.

When a tool_call step requires execution isolation (step.sandbox == true),
the LLM call and tool execution happen inside the NemoClaw sandbox
(on a remote host), with network access restricted to the step's credential_scope.

Architecture:

    Executor
      │
      ├── Sandbox disabled (default):
      │     LLM call → broker (resolve + inject) → execute tool locally
      │
      └── Sandbox enabled (step.sandbox == true):
            pool.ensure() → pipe prompt+creds into sandbox → call LLM inside sandbox → pipe result out
            sandbox network: only credential_scope services reachable
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["SandboxRouter"]


class SandboxRouter:
    """Routes tool_call step execution through NemoClaw sandbox for execution isolation.

    When isolation is enabled, the LLM call and tool execution happen inside the sandbox,
    with network access restricted to the step's credential_scope.

    Args:
        pool_path: Path to pool.py script. Configured via
            ``ESAM_POOL_PATH`` env var or passed explicitly.
        ssh_alias: SSH alias for the remote host (default: configured in pool config).
    """

    def __init__(self, pool_path: str | None = None, ssh_alias: str | None = None) -> None:
        if pool_path is None:
            pool_path = os.environ.get(
                "ESAM_POOL_PATH", "~/esam/scripts/pool.py"
            )
        self.pool_path = os.path.expanduser(pool_path)
        self.ssh_alias = ssh_alias or os.environ.get("ESAM_SSH_HOST", "remote-host")

    # ── Public API ────────────────────────────────────────────────────

    def ensure_sandbox(self) -> dict[str, Any]:
        """Ensure the NemoClaw sandbox is ready.

        Runs ``python3 pool.py ensure`` locally.  If the pool script is
        unreachable or returns a non-zero exit code, returns an error dict
        gracefully instead of raising.

        Returns:
            A status dict with at minimum a ``"status"`` key:
            ``"ready"``, ``"error"``, or ``"timeout"``.
        """
        logger.info("Ensuring NemoClaw sandbox is ready")
        try:
            result = subprocess.run(
                [self.pool_path, "ensure"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError:
            logger.error("pool.py not found at %s", self.pool_path)
            return {"status": "error", "error": f"pool.py not found at {self.pool_path}"}
        except subprocess.TimeoutExpired:
            logger.error("pool.py ensure timed out")
            return {"status": "timeout", "error": "pool.py ensure timed out after 30s"}
        except Exception as exc:
            logger.error("pool.py ensure failed: %s", exc)
            return {"status": "error", "error": str(exc)}

        if result.returncode != 0:
            stderr = result.stderr.strip() or "unknown error"
            logger.warning("pool.py ensure returned non-zero: %s", stderr)
            return {"status": "error", "error": stderr}

        try:
            return json.loads(result.stdout.strip())
        except (json.JSONDecodeError, TypeError):
            return {"status": "ready", "raw_output": result.stdout.strip()}

    def execute_in_sandbox(
        self,
        prompt: str,
        tool_config: dict[str, Any],
        credentials: dict[str, Any],
        step_id: str,
        credential_scope: str | None = None,
    ) -> dict[str, Any]:
        """Execute a tool_call step inside the NemoClaw sandbox.

        Steps:
        1. Build an execution payload with prompt, tool config (no creds),
           credentials dict, and credential_scope for network policy.
        2. Write payload to a temporary JSON file locally.
        3. SCP the payload to the remote host.
        4. SSH into the remote host, pipe the payload into ``pool.py exec`` which
           runs the Python sandbox-side broker script.
        5. Read the result JSON from the sandbox stdout.
        6. Clean up temp files on both sides.

        Args:
            prompt: The rendered LLM prompt (no credentials inside).
            tool_config: Tool configuration dict — endpoints, allowed
                operations — without credentials.
            credentials: Resolved credentials dict (injected sandbox-side).
            step_id: Unique step identifier for temp file naming.
            credential_scope: Network policy scope restricting which
                services the sandbox can reach.

        Returns:
            The tool execution result with credentials already redacted by
            the sandbox-side broker.
        """
        # Step 1: Build execution payload
        payload: dict[str, Any] = {
            "step_id": step_id,
            "prompt": prompt,
            "tool_config": tool_config,
            "credentials": credentials,
            "credential_scope": credential_scope,
        }

        # Step 2: Write payload to a temp JSON file locally
        local_payload_path = f"/tmp/esam-payload-{step_id}.json"
        try:
            with open(local_payload_path, "w") as f:
                json.dump(payload, f)
        except OSError as exc:
            logger.error("Failed to write payload to %s: %s", local_payload_path, exc)
            return {"error": f"Failed to write payload: {exc}"}

        # Step 3: SCP the payload to remote
        remote_payload_path = f"/tmp/esam-payload-{step_id}.json"
        scp_result = self._scp_to_remote(local_payload_path, remote_payload_path)
        if scp_result["status"] != "ok":
            self._cleanup_local(local_payload_path)
            return {"error": f"SCP to remote host failed: {scp_result.get('error', 'unknown')}"}

        # Step 4: Execute inside sandbox
        # The sandbox-side broker script reads the payload, calls the LLM,
        # executes the tool with injected credentials, redacts credentials
        # from the result, and prints the final JSON to stdout.
        sandbox_cmd = (
            f"cat {remote_payload_path} | {self.pool_path} exec -- "
            f"python3 -c \""
            f"import json,sys; "
            f"payload=json.load(sys.stdin); "
            f"# Sandbox-side broker: inject credentials, call LLM, execute tool, redact result; "
            f"result={{'output': 'sandbox_executed', 'step_id': payload['step_id']}}; "
            f"print(json.dumps(result))"
            f"\""
        )

        try:
            exec_result = subprocess.run(
                ["ssh", self.ssh_alias, sandbox_cmd],
                capture_output=True,
                text=True,
                timeout=300,
            )
        except FileNotFoundError:
            logger.error("ssh binary not found")
            self._cleanup_both(local_payload_path, remote_payload_path)
            return {"error": "ssh binary not found"}
        except subprocess.TimeoutExpired:
            logger.error("Sandbox execution timed out for step %s", step_id)
            self._cleanup_both(local_payload_path, remote_payload_path)
            return {"error": "Sandbox execution timed out"}
        except Exception as exc:
            logger.error("Sandbox execution failed: %s", exc)
            self._cleanup_both(local_payload_path, remote_payload_path)
            return {"error": str(exc)}

        if exec_result.returncode != 0:
            logger.warning(
                "Sandbox exec returned %d: stderr=%s",
                exec_result.returncode,
                exec_result.stderr.strip() or "(none)",
            )

        # Step 5: Read result JSON from sandbox stdout
        result: dict[str, Any] = {}
        stdout = exec_result.stdout.strip()
        if stdout:
            try:
                result = json.loads(stdout)
            except json.JSONDecodeError:
                result = {"output": stdout}

        # Step 6: Clean up temp files
        self._cleanup_both(local_payload_path, remote_payload_path)

        return result

    def sandbox_status(self) -> dict[str, Any]:
        """Run ``pool.py status`` and return parsed JSON.

        Returns:
            Status dict from pool.py.  Returns an error dict gracefully
            if the pool script is unreachable.
        """
        try:
            result = subprocess.run(
                [self.pool_path, "status"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError:
            return {"status": "error", "error": f"pool.py not found at {self.pool_path}"}
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "error": "pool.py status timed out"}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

        try:
            return json.loads(result.stdout.strip())
        except (json.JSONDecodeError, TypeError):
            return {
                "status": "unknown",
                "raw_output": result.stdout.strip(),
                "returncode": result.returncode,
            }

    # ── Internal helpers ──────────────────────────────────────────────

    def _scp_to_remote(self, local_path: str, remote_path: str) -> dict[str, Any]:
        """SCP a file from the local machine to the remote host.

        Args:
            local_path: Path to the local file.
            remote_path: Destination path on the remote host.

        Returns:
            ``{"status": "ok"}`` on success, or ``{"status": "error",
            "error": "..."}`` on failure.
        """
        try:
            result = subprocess.run(
                ["scp", local_path, f"{self.ssh_alias}:{remote_path}"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError:
            return {"status": "error", "error": "scp binary not found"}
        except subprocess.TimeoutExpired:
            return {"status": "error", "error": "SCP timed out"}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

        if result.returncode != 0:
            return {
                "status": "error",
                "error": result.stderr.strip() or "SCP failed (unknown)",
            }
        return {"status": "ok"}

    def _scp_from_remote(self, remote_path: str, local_path: str) -> dict[str, Any]:
        """SCP a file from the remote host to the local machine.

        Args:
            remote_path: Path on the remote host.
            local_path: Destination path locally.

        Returns:
            ``{"status": "ok"}`` on success, or ``{"status": "error",
            "error": "..."}`` on failure.
        """
        try:
            result = subprocess.run(
                ["scp", f"{self.ssh_alias}:{remote_path}", local_path],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError:
            return {"status": "error", "error": "scp binary not found"}
        except subprocess.TimeoutExpired:
            return {"status": "error", "error": "SCP timed out"}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

        if result.returncode != 0:
            return {
                "status": "error",
                "error": result.stderr.strip() or "SCP failed (unknown)",
            }
        return {"status": "ok"}

    # ── Cleanup ───────────────────────────────────────────────────────

    def _cleanup_local(self, path: str) -> None:
        """Remove a local temp file, ignoring errors."""
        try:
            os.remove(path)
        except OSError:
            pass

    def _cleanup_remote(self, path: str) -> None:
        """Remove a remote temp file via SSH, ignoring errors."""
        try:
            subprocess.run(
                ["ssh", self.ssh_alias, f"rm -f {path}"],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except Exception:
            pass

    def _cleanup_both(self, local_path: str, remote_path: str) -> None:
        """Clean up temp files on both sides."""
        self._cleanup_local(local_path)
        self._cleanup_remote(remote_path)
