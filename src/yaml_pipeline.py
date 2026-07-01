"""YAML Write-Through Pipeline.

After every state-changing API operation, this module exports the
affected agent from the database to a YAML file under workflows/.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from workflow_loader import export_agent_to_yaml

logger = logging.getLogger(__name__)


def _git_auto_commit(filepath: str, agent_name: str) -> None:
    """Auto-commit a workflow YAML change.

    Fails silently — if git isn't initialized, no changes, or the
    repo doesn't exist, this just logs and continues.
    Never crashes the API.
    """
    try:
        repo = Path(__file__).parent.parent
        subprocess.run(
            ["git", "add", filepath],
            cwd=repo, capture_output=True, timeout=10,
        )
        subprocess.run(
            ["git", "commit", "-m", f"workflow: auto-update {agent_name}"],
            cwd=repo, capture_output=True, timeout=10,
            env={
                **dict(os.environ),
                "GIT_AUTHOR_NAME": "ES Agent Management",
                "GIT_AUTHOR_EMAIL": "agent@evolving.software",
                "GIT_COMMITTER_NAME": "ES Agent Management",
                "GIT_COMMITTER_EMAIL": "agent@evolving.software",
            },
        )
    except Exception:
        logger.debug("git auto-commit skipped (expected if git not initialised)", exc_info=True)


def sync_agent_to_yaml(agent_id: str) -> str | None:
    """Export an agent from DB to YAML and write to workflows/<name>.yaml.

    Returns the file path written, or None if agent not found.
    Uses workflow_loader.export_agent_to_yaml internally.
    Handles errors gracefully (logs, doesn't crash).
    """
    try:
        # export_agent_to_yaml raises ValueError if agent not found.
        # We pass output_path=None so it builds the path ourselves.
        from agent_workflow import AgentWorkflowDB

        db = AgentWorkflowDB()
        agent = db.get_agent(agent_id)
        if not agent:
            logger.warning("sync_agent_to_yaml: agent %s not found, skipping", agent_id)
            return None

        # Build the output path: workflows/<name-slug>.yaml
        safe_name = agent["name"].lower().replace(" ", "-").replace("/", "-")
        workflows_dir = Path(__file__).parent.parent / "workflows"
        workflows_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(workflows_dir / f"{safe_name}.yaml")

        result = export_agent_to_yaml(agent_id, output_path)
        # Auto-commit the YAML write to git (fail-safe)
        _git_auto_commit(output_path, agent["name"])
        return result
    except ValueError:
        logger.warning("sync_agent_to_yaml: agent %s not found, skipping", agent_id)
        return None
    except Exception:
        logger.exception("sync_agent_to_yaml: error exporting agent %s to YAML", agent_id)
        return None
