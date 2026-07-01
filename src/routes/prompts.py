#!/usr/bin/env python3
"""Prompt versioning routes — /api/workflow/prompts/* routes."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, HTTPException

from prompt_versioning import PromptVersionManager

logger = logging.getLogger(__name__)


def register(app: FastAPI):
    """Register all /api/workflow/prompts/* routes."""
    _prompt_manager = PromptVersionManager()

    @app.get("/api/workflow/prompts/versions/{step_id}")
    def list_prompt_versions(step_id: str):
        """List all prompt versions for a step."""
        versions = _prompt_manager.list_versions(step_id)
        return {"versions": versions, "count": len(versions)}

    @app.get("/api/workflow/prompts/version/{version_id}")
    def get_prompt_version(version_id: str):
        """Get a specific prompt version."""
        v = _prompt_manager.get_version(version_id)
        if not v:
            raise HTTPException(404, "Version not found")
        return v

    @app.post("/api/workflow/prompts/diff")
    def diff_prompt_versions(data: dict):
        """Diff two prompt versions."""
        id_a = data.get("version_id_a", "")
        id_b = data.get("version_id_b", "")
        if not id_a or not id_b:
            raise HTTPException(400, "version_id_a and version_id_b are required")
        return _prompt_manager.diff_versions(id_a, id_b)

    @app.post("/api/workflow/prompts/rollback/{step_id}")
    def rollback_prompt(step_id: str, data: dict):
        """Rollback a step's prompt to a previous version."""
        target = data.get("target_version", 0)
        if not target:
            raise HTTPException(400, "target_version is required")
        try:
            return _prompt_manager.rollback(step_id, target)
        except ValueError as e:
            raise HTTPException(404, str(e))

    @app.post("/api/workflow/prompts/record")
    def record_prompt_version(data: dict):
        """Manually record a prompt version."""
        step_id = data.get("step_id", "")
        if not step_id:
            raise HTTPException(400, "step_id is required")
        return _prompt_manager.record_version(
            step_id=step_id,
            prompt_template=data.get("prompt_template", ""),
            rendered_prompt=data.get("rendered_prompt", ""),
            run_id=data.get("run_id", ""),
            context_data=json.dumps(data.get("context", {})),
            output_data=json.dumps(data.get("output", {})),
            tokens_input=data.get("tokens_input", 0),
            tokens_output=data.get("tokens_output", 0),
            model_used=data.get("model_used", ""),
            notes=data.get("notes", ""),
        )
