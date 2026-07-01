#!/usr/bin/env python3
"""Evaluation routes — /api/workflow/eval/* routes."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from database import get_connection
from evaluator import Evaluator
from job_queue import get_worker

logger = logging.getLogger(__name__)


def register(app: FastAPI):
    """Register all /api/workflow/eval/* routes."""
    _evaluator = Evaluator()

    @app.post("/api/workflow/eval/datasets")
    def create_eval_dataset(data: dict):
        """Create an evaluation dataset."""
        name = data.get("name", "")
        desc = data.get("description", "")
        if not name:
            raise HTTPException(400, "name is required")
        return _evaluator.create_dataset(name, desc)

    @app.get("/api/workflow/eval/datasets")
    def list_eval_datasets():
        """List all evaluation datasets."""
        return {"datasets": _evaluator.list_datasets()}

    @app.get("/api/workflow/eval/datasets/{dataset_id}")
    def get_eval_dataset(dataset_id: str):
        """Get a dataset with its items."""
        ds = _evaluator.get_dataset(dataset_id)
        if not ds:
            raise HTTPException(404, "Dataset not found")
        items = _evaluator.list_items(dataset_id)
        return {"dataset": ds, "items": items}

    @app.delete("/api/workflow/eval/datasets/{dataset_id}")
    def delete_eval_dataset(dataset_id: str):
        """Delete a dataset."""
        ok = _evaluator.delete_dataset(dataset_id)
        if not ok:
            raise HTTPException(404, "Dataset not found")
        return {"deleted": True}

    @app.post("/api/workflow/eval/datasets/{dataset_id}/items")
    def add_eval_item(dataset_id: str, data: dict):
        """Add an item to a dataset."""
        input_text = data.get("input_text", "")
        if not input_text:
            raise HTTPException(400, "input_text is required")
        return _evaluator.add_item(
            dataset_id=dataset_id,
            input_text=input_text,
            expected_output=data.get("expected_output", ""),
            metadata_json=json.dumps(data.get("metadata", {})),
        )

    @app.post("/api/workflow/eval/datasets/{dataset_id}/import")
    def bulk_import_eval_items(dataset_id: str, data: dict):
        """Bulk import items into a dataset."""
        items = data.get("items", [])
        if not items:
            raise HTTPException(400, "items array is required")
        result = _evaluator.import_items(dataset_id, items)
        return {"imported": len(result), "items": result}

    @app.post("/api/workflow/eval/run")
    def run_evaluation(data: dict):
        """Run an evaluation against an agent in the background.

        Submits to the job queue and returns immediately with 202 Accepted.
        """
        dataset_id = data.get("dataset_id", "")
        agent_id = data.get("agent_id", "")
        notes = data.get("notes", "")
        idempotency_key = data.get("idempotency_key", "")
        if not dataset_id or not agent_id:
            raise HTTPException(400, "dataset_id and agent_id are required")

        worker = get_worker(max_workers=2)
        job = worker.submit(
            job_type="eval_run",
            agent_id=agent_id,
            input_json=json.dumps({
                "dataset_id": dataset_id,
                "notes": notes,
            }),
            idempotency_key=idempotency_key,
            timeout_s=data.get("timeout_s", 600),
        )

        from starlette.responses import JSONResponse
        return JSONResponse(
            status_code=202,
            content={
                "job_id": job["id"],
                "agent_id": agent_id,
                "dataset_id": dataset_id,
                "status": job["status"],
                "created_at": job["created_at"],
            },
        )

    @app.post("/api/workflow/eval/run/{eval_run_id}/llm-judge")
    def run_llm_judge(eval_run_id: str):
        """Run LLM-as-judge on an evaluation run."""
        return _evaluator.run_llm_judge(eval_run_id)

    @app.get("/api/workflow/eval/runs")
    def list_eval_runs():
        """List all evaluation runs."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM eval_runs ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        return {"runs": [dict(r) for r in rows]}

    @app.get("/api/workflow/eval/runs/{eval_run_id}")
    def get_eval_run(eval_run_id: str):
        """Get full evaluation run with results."""
        conn = get_connection()
        run = conn.execute("SELECT * FROM eval_runs WHERE id = ?", (eval_run_id,)).fetchone()
        if not run:
            raise HTTPException(404, "Eval run not found")
        results = conn.execute(
            "SELECT er.*, dei.input_text, dei.expected_output FROM eval_results er "
            "JOIN eval_items dei ON er.dataset_item_id = dei.id "
            "WHERE er.eval_run_id = ? ORDER BY er.created_at", (eval_run_id,)
        ).fetchall()
        return {"run": dict(run), "results": [dict(r) for r in results]}

    @app.get("/api/workflow/eval/compare/{run_a}/{run_b}")
    def compare_eval_runs(run_a: str, run_b: str):
        """Compare two evaluation runs."""
        return _evaluator.compare_runs(run_a, run_b)
