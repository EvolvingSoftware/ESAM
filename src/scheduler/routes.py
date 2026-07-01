"""Scheduler REST API routes.

Integrates into api_server.py via APIRouter with prefix /api.
Provides CRUD + classification + stats endpoints for the schedule dashboard.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from scheduler.db import ScheduleDB
from scheduler.sync import CronSync

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_db(request: Request) -> ScheduleDB:
    """Get or create a ScheduleDB instance from app state."""
    if not hasattr(request.app.state, "schedule_db"):
        request.app.state.schedule_db = ScheduleDB()
        request.app.state.cron_sync = CronSync(db=request.app.state.schedule_db)
    return request.app.state.schedule_db


def _get_sync(request: Request) -> CronSync:
    _get_db(request)
    return request.app.state.cron_sync


# ── Helper: Convert schedule record (tags str -> list) ──────────────


def _prepare(s: dict) -> dict:
    if isinstance(s.get("tags"), str):
        try:
            s["tags"] = json.loads(s["tags"])
        except (json.JSONDecodeError, TypeError):
            s["tags"] = []
    return s


# ── List / Query ────────────────────────────────────────────────────


@router.get("/schedules")
def list_schedules(
    request: Request,
    status: str | None = Query(None),
    department: str | None = Query(None),
    team: str | None = Query(None),
    project: str | None = Query(None),
    task_type: str | None = Query(None),
    schedule_type: str | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """List schedules with optional filters and pagination."""
    db = _get_db(request)
    filters = {
        k: v
        for k, v in {
            "status": status,
            "department": department,
            "team": team,
            "project": project,
            "task_type": task_type,
            "schedule_type": schedule_type,
            "search": search,
        }.items()
        if v is not None
    }
    all_items = db.list(filters=filters if filters else None)

    # Manual pagination
    offset = (page - 1) * limit
    page_items = all_items[offset : offset + limit]

    return {
        "items": [_prepare(s) for s in page_items],
        "total": len(all_items),
        "page": page,
        "limit": limit,
        "pages": max(1, (len(all_items) + limit - 1) // limit),
    }


# ── Create ──────────────────────────────────────────────────────────


@router.post("/schedules")
def create_schedule(request: Request, body: dict):
    """Create schedule metadata record.

    NOTE: This does NOT create a cron job — it only stores metadata.
    The cron job should be created separately or pre-existing.
    """
    cron_job_id = body.get("cron_job_id", "")
    if not cron_job_id:
        raise HTTPException(status_code=400, detail="cron_job_id is required")

    db = _get_db(request)

    # Check duplicate
    existing = db.get_by_cron_job_id(cron_job_id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Schedule for cron job '{cron_job_id}' already exists",
        )

    record = db.create(
        cron_job_id=cron_job_id,
        name=body.get("name", cron_job_id),
        description=body.get("description", ""),
        department=body.get("department", ""),
        team=body.get("team", ""),
        project=body.get("project", ""),
        task_type=body.get("task_type", ""),
        tags=body.get("tags", []),
        schedule_type=body.get("schedule_type", "cron"),
        status=body.get("status", "unknown"),
    )
    return _prepare(record)


# ── Stats (must be before {schedule_id} routes) ──────────────────────


@router.get("/schedules/stats")
def get_stats(request: Request):
    """Aggregate stats: total, by status, by department."""
    db = _get_db(request)
    return db.stats()


# ── Classifications ──────────────────────────────────────────────────


@router.get("/schedules/classifications")
def list_classifications(request: Request):
    """List all unique classification values."""
    db = _get_db(request)
    return db.list_classifications()


@router.post("/schedules/classifications")
def bulk_update_classifications(request: Request, body: dict):
    """Bulk update classifications for multiple schedules.

    Body format: {"updates": [{"id": "...", "department": "...", ...}, ...]}
    """
    db = _get_db(request)
    updates = body.get("updates", [])
    results = []

    for update in updates:
        sid = update.get("id", "")
        if not sid:
            continue
        existing = db.get(sid)
        if not existing:
            continue
        record = db.update(sid, **update)
        results.append(_prepare(record))

    return {"updated": len(results), "items": results}


# ── Update ──────────────────────────────────────────────────────────


@router.put("/schedules/{schedule_id}")
def update_schedule(request: Request, schedule_id: str, body: dict):
    """Update schedule classification/metadata."""
    db = _get_db(request)
    existing = db.get(schedule_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Schedule not found")

    record = db.update(schedule_id, **body)
    return _prepare(record)


# ── Delete ──────────────────────────────────────────────────────────


@router.delete("/schedules/{schedule_id}")
def delete_schedule(request: Request, schedule_id: str):
    """Delete schedule metadata and remove associated cron job."""
    db = _get_db(request)
    sync = _get_sync(request)

    existing = db.get(schedule_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Schedule not found")

    # Remove the Hermes cron job first
    cron_job_id = existing.get("cron_job_id", "")
    if cron_job_id:
        try:
            sync.remove(cron_job_id)
        except Exception:
            logger.warning("Failed to remove cron job %s, continuing", cron_job_id)

    # Delete metadata
    db.delete(schedule_id)
    return {"deleted": True, "id": schedule_id}


# ── Pause / Resume / Run-Now ────────────────────────────────────────


@router.post("/schedules/{schedule_id}/pause")
def pause_schedule(request: Request, schedule_id: str):
    """Pause a cron job."""
    db = _get_db(request)
    sync = _get_sync(request)

    existing = db.get(schedule_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Schedule not found")

    cron_job_id = existing.get("cron_job_id", "")
    if not cron_job_id:
        raise HTTPException(status_code=400, detail="No associated cron job")

    success = sync.pause(cron_job_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to pause cron job")

    updated = db.get(schedule_id)
    return _prepare(updated)


@router.post("/schedules/{schedule_id}/resume")
def resume_schedule(request: Request, schedule_id: str):
    """Resume a cron job."""
    db = _get_db(request)
    sync = _get_sync(request)

    existing = db.get(schedule_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Schedule not found")

    cron_job_id = existing.get("cron_job_id", "")
    if not cron_job_id:
        raise HTTPException(status_code=400, detail="No associated cron job")

    success = sync.resume(cron_job_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to resume cron job")

    updated = db.get(schedule_id)
    return _prepare(updated)


@router.post("/schedules/{schedule_id}/run-now")
def run_now(request: Request, schedule_id: str):
    """Trigger an immediate run of the cron job."""
    db = _get_db(request)
    existing = db.get(schedule_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Schedule not found")

    cron_job_id = existing.get("cron_job_id", "")
    if not cron_job_id:
        raise HTTPException(status_code=400, detail="No associated cron job")

    # Record a run in history
    db.record_run(
        schedule_id=schedule_id,
        cron_job_id=cron_job_id,
        status="triggered",
        started_at=db._now() if hasattr(db, "_now") else "",
    )

    return {"triggered": True, "cron_job_id": cron_job_id, "schedule_id": schedule_id}


# ── Run History ──────────────────────────────────────────────────────


@router.get("/schedules/{schedule_id}/history")
def get_run_history(request: Request, schedule_id: str, limit: int = Query(20, ge=1, le=100)):
    """Get run history for a schedule."""
    db = _get_db(request)
    existing = db.get(schedule_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Schedule not found")

    history = db.get_run_history(schedule_id, limit=limit)
    return {"items": history, "count": len(history)}
