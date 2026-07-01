"""CronSync — Synchronize Hermes cron jobs with local schedule metadata.

Bridges the Hermes CLI cron system with the SQLite-backed ScheduleDB.
Provides pause/resume/remove commands that delegate to the Hermes CLI.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from scheduler.db import ScheduleDB

logger = logging.getLogger(__name__)

HERMES_BIN = None


def _find_hermes() -> str:
    """Locate the hermes CLI binary."""
    global HERMES_BIN
    if HERMES_BIN:
        return HERMES_BIN

    # Check common locations
    candidates = [
        "hermes",
        str(Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "hermes"),
    ]
    for cmd in candidates:
        try:
            subprocess.run(
                [cmd, "--version"],
                capture_output=True,
                timeout=5,
                text=True,
            )
            HERMES_BIN = cmd
            return cmd
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    # Fallback: just use 'hermes' and let it fail at runtime
    HERMES_BIN = "hermes"
    return HERMES_BIN


def _run_hermes(args: list[str]) -> tuple[str, str, int]:
    """Run a hermes CLI command and return (stdout, stderr, returncode)."""
    cmd = [_find_hermes()] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except FileNotFoundError:
        return "", f"hermes CLI not found: {cmd[0]}", -1
    except subprocess.TimeoutExpired:
        return "", "hermes command timed out", -1


class CronSync:
    """Synchronizes Hermes cron jobs with the local ScheduleDB metadata store."""

    def __init__(self, db: ScheduleDB | None = None):
        self.db = db or ScheduleDB()

    def sync_all(self) -> list[dict]:
        """Sync Hermes cron jobs with local metadata.

        Calls `hermes cronjob list` and creates default metadata records
        for any jobs that don't have one yet.

        Returns:
            Merged list of all jobs with metadata (flat dicts).
        """
        stdout, stderr, rc = _run_hermes(["cronjob", "list"])

        if rc != 0 or not stdout:
            logger.warning("Failed to list Hermes cron jobs: %s", stderr)
            # Return all local metadata
            return self.db.list()

        # Parse JSON output — it may be a single array or one JSON object per line
        try:
            cron_jobs = json.loads(stdout)
            if isinstance(cron_jobs, dict):
                cron_jobs = [cron_jobs]
        except json.JSONDecodeError:
            # Try parsing line by line
            cron_jobs = []
            for line in stdout.splitlines():
                line = line.strip()
                if line:
                    try:
                        item = json.loads(line)
                        cron_jobs.append(item)
                    except json.JSONDecodeError:
                        continue

        merged = []
        for job in cron_jobs:
            cron_id = job.get("id", "") or job.get("job_id", "") or job.get("name", "")
            job_name = job.get("name", "") or job.get("description", "") or cron_id
            status = job.get("status", "unknown")

            if not cron_id:
                continue

            existing = self.db.get_by_cron_job_id(cron_id)
            if existing:
                # Update status from Hermes
                self.db.update(existing["id"], status=status)
                merged.append(self.db.get(existing["id"]))
            else:
                # Create default metadata record
                record = self.db.create(
                    cron_job_id=cron_id,
                    name=job_name,
                    status=status,
                    schedule_type="cron",
                )
                merged.append(record)

        # Also include local-only records (metadata without a cron job)
        local_only = self.db.list()
        local_ids = {m.get("cron_job_id") for m in merged}
        for rec in local_only:
            if rec.get("cron_job_id") not in local_ids:
                merged.append(rec)

        return merged

    def get_cron_status(self, cron_job_id: str) -> str:
        """Get job status from Hermes cron."""
        stdout, stderr, rc = _run_hermes(["cronjob", "list"])
        if rc != 0 or not stdout:
            return "unknown"

        try:
            jobs = json.loads(stdout)
            if isinstance(jobs, dict):
                jobs = [jobs]
        except json.JSONDecodeError:
            return "unknown"

        for job in jobs:
            jid = job.get("id", "") or job.get("job_id", "") or job.get("name", "")
            if jid == cron_job_id:
                return job.get("status", "unknown")
        return "unknown"

    def pause(self, cron_job_id: str) -> bool:
        """Pause a Hermes cron job. Returns True on success."""
        stdout, stderr, rc = _run_hermes(["cronjob", "pause", cron_job_id])
        success = rc == 0
        if success:
            # Update local status
            record = self.db.get_by_cron_job_id(cron_job_id)
            if record:
                self.db.update(record["id"], status="paused")
        else:
            logger.warning("Failed to pause cron job %s: %s", cron_job_id, stderr)
        return success

    def resume(self, cron_job_id: str) -> bool:
        """Resume a Hermes cron job. Returns True on success."""
        stdout, stderr, rc = _run_hermes(["cronjob", "resume", cron_job_id])
        success = rc == 0
        if success:
            record = self.db.get_by_cron_job_id(cron_job_id)
            if record:
                self.db.update(record["id"], status="running")
        else:
            logger.warning("Failed to resume cron job %s: %s", cron_job_id, stderr)
        return success

    def remove(self, cron_job_id: str) -> bool:
        """Remove a Hermes cron job. Returns True on success."""
        stdout, stderr, rc = _run_hermes(["cronjob", "remove", cron_job_id])
        success = rc == 0
        if success:
            record = self.db.get_by_cron_job_id(cron_job_id)
            if record:
                self.db.delete(record["id"])
        else:
            logger.warning("Failed to remove cron job %s: %s", cron_job_id, stderr)
        return success
