#!/usr/bin/env python3
"""Job queue routes — /api/jobs/* routes (job queue status)."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, HTTPException

from job_queue import get_worker

logger = logging.getLogger(__name__)


def register(app: FastAPI):
    """Register all /api/jobs/* routes."""

    @app.get("/api/jobs/{job_id}")
    def get_job_status(job_id: str):
        """Get the status, progress, and result of a background job."""
        worker = get_worker()
        job = worker.get_job(job_id)
        if not job:
            raise HTTPException(404, f"Job {job_id} not found")
        return job

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        """Cancel a running or queued background job."""
        worker = get_worker()
        ok = worker.cancel_job(job_id)
        if not ok:
            raise HTTPException(
                404, f"Job {job_id} not found or already finished"
            )
        return {"status": "cancelled", "job_id": job_id}

    @app.get("/api/jobs")
    def list_jobs(status: str | None = None):
        """List background jobs, optionally filtered by status."""
        worker = get_worker()
        jobs = worker.list_jobs(status=status)
        return {"jobs": jobs, "total": len(jobs)}
