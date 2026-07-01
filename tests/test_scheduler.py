"""Tests for the Scheduler module (ScheduleDB + CronSync + Routes).

Covers CRUD operations on schedule metadata, filtering, classification
aggregation, sync with Hermes cron, and API route behavior.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from scheduler.db import ScheduleDB


def _build_db(db_path: str | None = None) -> ScheduleDB:
    """Create a ScheduleDB backed by a temp file."""
    if db_path is None:
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
    d = ScheduleDB(db_path=db_path)
    # Ensure clean schema
    d.ensure_schema()
    return d


class TestScheduleDB(unittest.TestCase):
    """Tests for ScheduleDB metadata CRUD operations."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = _build_db(self.db_path)

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except Exception:
            pass

    def test_db_create(self):
        """Create schedule metadata record and verify fields."""
        r = self.db.create(
            cron_job_id="cron-test-1",
            name="Daily newsletter",
            description="Morning intelligence briefing",
            department="Intelligence",
            team="Newsletter",
            project="ESAM Morning Brief",
            task_type="digest",
            tags=["morning", "intel", "automated"],
        )
        self.assertIn("id", r)
        self.assertEqual(r["cron_job_id"], "cron-test-1")
        self.assertEqual(r["name"], "Daily newsletter")
        self.assertEqual(r["department"], "Intelligence")
        self.assertEqual(r["team"], "Newsletter")
        self.assertEqual(r["project"], "ESAM Morning Brief")
        self.assertEqual(r["task_type"], "digest")
        self.assertEqual(r["tags"], ["morning", "intel", "automated"])
        self.assertIn("created_at", r)
        self.assertIn("updated_at", r)

    def test_db_get(self):
        """Retrieve schedule by id."""
        r = self.db.create("cron-get-1", "Get test")
        fetched = self.db.get(r["id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["name"], "Get test")
        self.assertEqual(fetched["id"], r["id"])

    def test_db_get_nonexistent(self):
        """Getting a non-existent id returns None."""
        fetched = self.db.get("nonexistent-id")
        self.assertIsNone(fetched)

    def test_db_get_by_cron_job_id(self):
        """Retrieve by cron_job_id."""
        self.db.create("cron-by-cron-1", "By cron ID")
        fetched = self.db.get_by_cron_job_id("cron-by-cron-1")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["name"], "By cron ID")

    def test_db_list_filters(self):
        """Filter schedules by department."""
        self.db.create("cron-f1", "Alpha", department="Intelligence", team="A")
        self.db.create("cron-f2", "Beta", department="Operations", team="B")
        self.db.create("cron-f3", "Gamma", department="Intelligence", team="C")

        results = self.db.list({"department": "Intelligence"})
        self.assertEqual(len(results), 2)
        names = {r["name"] for r in results}
        self.assertIn("Alpha", names)
        self.assertIn("Gamma", names)

    def test_db_list_multiple_filters(self):
        """Filter by team AND project."""
        self.db.create("cron-mf1", "Item1", department="Intel", team="Alpha", project="ProjX")
        self.db.create("cron-mf2", "Item2", department="Intel", team="Beta", project="ProjX")
        self.db.create("cron-mf3", "Item3", department="Intel", team="Alpha", project="ProjY")

        results = self.db.list({"team": "Alpha", "project": "ProjX"})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Item1")

    def test_db_list_search(self):
        """Free-text search across name and description."""
        self.db.create("cron-s1", "Morning Report", description="Daily briefing")
        self.db.create("cron-s2", "Evening Digest", description="End of day summary")

        results = self.db.list({"search": "Morning"})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Morning Report")

        results = self.db.list({"search": "summary"})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Evening Digest")

    def test_db_list_classifications(self):
        """Return unique classification values."""
        self.db.create("cron-c1", "A", department="Intel", team="Alpha", project="P1", task_type="digest")
        self.db.create("cron-c2", "B", department="Intel", team="Beta", project="P2", task_type="alert")
        self.db.create("cron-c3", "C", department="Ops", team="Alpha", project="P1", task_type="digest")

        classifications = self.db.list_classifications()
        self.assertIn("Intel", classifications["departments"])
        self.assertIn("Ops", classifications["departments"])
        self.assertIn("Alpha", classifications["teams"])
        self.assertIn("Beta", classifications["teams"])
        self.assertIn("P1", classifications["projects"])
        self.assertIn("P2", classifications["projects"])
        self.assertIn("digest", classifications["task_types"])
        self.assertIn("alert", classifications["task_types"])

    def test_db_update(self):
        """Update metadata fields."""
        r = self.db.create("cron-upd-1", "Original", department="Intel")
        updated = self.db.update(r["id"], name="Updated Name", team="Bravo")
        self.assertEqual(updated["name"], "Updated Name")
        self.assertEqual(updated["team"], "Bravo")
        # Original fields unchanged
        self.assertEqual(updated["department"], "Intel")

    def test_db_delete(self):
        """Delete schedule metadata."""
        r = self.db.create("cron-del-1", "To Delete")
        deleted = self.db.delete(r["id"])
        self.assertTrue(deleted)
        self.assertIsNone(self.db.get(r["id"]))

    def test_db_delete_nonexistent(self):
        """Deleting non-existent returns False."""
        self.assertFalse(self.db.delete("nonexistent-id"))

    def test_db_duplicate_cron_job_id(self):
        """Creating with duplicate cron_job_id should fail."""
        self.db.create("cron-dup-1", "First")
        with self.assertRaises(Exception):
            self.db.create("cron-dup-1", "Second")

    def test_run_history(self):
        """Record and retrieve run history."""
        r = self.db.create("cron-hist-1", "History Test")
        self.db.record_run(
            schedule_id=r["id"],
            cron_job_id="cron-hist-1",
            status="success",
            started_at="2026-01-01T00:00:00Z",
            finished_at="2026-01-01T00:05:00Z",
            duration_sec=300.0,
            output_summary="Completed successfully",
        )
        history = self.db.get_run_history(r["id"])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["status"], "success")
        self.assertEqual(history[0]["output_summary"], "Completed successfully")

    def test_stats(self):
        """Aggregate stats."""
        self.db.create("cron-st1", "S1", department="Intel", status="running")
        self.db.create("cron-st2", "S2", department="Intel", status="paused")
        self.db.create("cron-st3", "S3", department="Ops", status="running")

        stats = self.db.stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["by_status"].get("running"), 2)
        self.assertEqual(stats["by_status"].get("paused"), 1)
        self.assertEqual(stats["by_department"].get("Intel"), 2)
        self.assertEqual(stats["by_department"].get("Ops"), 1)


class TestCronSync(unittest.TestCase):
    """Tests for CronSync Hermes cron bridge."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = _build_db(self.db_path)

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except Exception:
            pass

    @patch("scheduler.sync._run_hermes")
    def test_sync_all(self, mock_run):
        """Sync Hermes cron jobs with local metadata."""
        # Mock hermes cronjob list output
        mock_run.return_value = (
            json.dumps([
                {"id": "cron-sync-1", "name": "SyncJob1", "status": "running"},
                {"id": "cron-sync-2", "name": "SyncJob2", "status": "paused"},
            ]),
            "",
            0,
        )

        from scheduler.sync import CronSync
        sync = CronSync(db=self.db)
        merged = sync.sync_all()

        # Should have created metadata for both jobs
        self.assertEqual(len(merged), 2)
        ids = {m["cron_job_id"] for m in merged}
        self.assertIn("cron-sync-1", ids)
        self.assertIn("cron-sync-2", ids)

        # Verify metadata was persisted
        r1 = self.db.get_by_cron_job_id("cron-sync-1")
        self.assertIsNotNone(r1)
        self.assertEqual(r1["name"], "SyncJob1")
        self.assertEqual(r1["status"], "running")

    @patch("scheduler.sync._run_hermes")
    def test_sync_all_with_existing(self, mock_run):
        """Sync should update existing local records."""
        self.db.create("cron-existing-1", "Existing Job", department="Intel")

        mock_run.return_value = (
            json.dumps([
                {"id": "cron-existing-1", "name": "Existing Job", "status": "running"},
            ]),
            "",
            0,
        )

        from scheduler.sync import CronSync
        sync = CronSync(db=self.db)
        merged = sync.sync_all()

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["department"], "Intel")
        self.assertEqual(merged[0]["status"], "running")

    @patch("scheduler.sync._run_hermes")
    def test_pause_resume(self, mock_run):
        """Pause and resume should update local status."""
        self.db.create("cron-pr-1", "PauseResume Test")

        from scheduler.sync import CronSync
        sync = CronSync(db=self.db)

        # Mock pause
        mock_run.return_value = ("", "", 0)
        result = sync.pause("cron-pr-1")
        self.assertTrue(result)
        record = self.db.get_by_cron_job_id("cron-pr-1")
        self.assertEqual(record["status"], "paused")

        # Mock resume
        result = sync.resume("cron-pr-1")
        self.assertTrue(result)
        record = self.db.get_by_cron_job_id("cron-pr-1")
        self.assertEqual(record["status"], "running")

    @patch("scheduler.sync._run_hermes")
    def test_remove(self, mock_run):
        """Remove should delete metadata + call cron remove."""
        self.db.create("cron-rm-1", "Remove Test")

        mock_run.return_value = ("", "", 0)

        from scheduler.sync import CronSync
        sync = CronSync(db=self.db)
        result = sync.remove("cron-rm-1")
        self.assertTrue(result)
        self.assertIsNone(self.db.get_by_cron_job_id("cron-rm-1"))


class TestClassificationFilter(unittest.TestCase):
    """Test combined classification filtering."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = _build_db(self.db_path)

        # Seed data
        self.db.create("c1", "Team A Digest", department="Intel", team="Alpha", project="ProjectX", task_type="digest")
        self.db.create("c2", "Team A Alert", department="Intel", team="Alpha", project="ProjectY", task_type="alert")
        self.db.create("c3", "Team B Digest", department="Intel", team="Bravo", project="ProjectX", task_type="digest")
        self.db.create("c4", "Ops Monitor", department="Operations", team="Charlie", project="ProjectZ", task_type="monitoring")

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except Exception:
            pass

    def test_filter_by_team_and_project(self):
        """Filter by team + project returns exact match."""
        results = self.db.list({"team": "Alpha", "project": "ProjectX"})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Team A Digest")

    def test_filter_by_department_and_task_type(self):
        """Filter by department + task type."""
        results = self.db.list({"department": "Intel", "task_type": "digest"})
        self.assertEqual(len(results), 2)
        names = {r["name"] for r in results}
        self.assertIn("Team A Digest", names)
        self.assertIn("Team B Digest", names)

    def test_filter_by_team_and_task_type(self):
        """Filter by team + task type."""
        results = self.db.list({"team": "Alpha", "task_type": "alert"})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Team A Alert")

    def test_filter_no_match(self):
        """Filter with no matches returns empty list."""
        results = self.db.list({"team": "Nonexistent"})
        self.assertEqual(len(results), 0)


class TestRouteList(unittest.TestCase):
    """Test route-level behavior by exercising the DB layer as the routes would."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = _build_db(self.db_path)

        self.db.create("route-test-1", "RouteJob Alpha", department="Intel", status="running")
        self.db.create("route-test-2", "RouteJob Beta", department="Intel", status="paused")
        self.db.create("route-test-3", "RouteJob Gamma", department="Ops", status="running")

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except Exception:
            pass

    def test_route_list_all(self):
        """List all schedules (no filters)."""
        results = self.db.list()
        self.assertEqual(len(results), 3)

    def test_route_list_filtered_by_status(self):
        """List schedules filtered by status."""
        results = self.db.list({"status": "running"})
        self.assertEqual(len(results), 2)

    def test_route_list_filtered_by_department(self):
        """List schedules filtered by department."""
        results = self.db.list({"department": "Ops"})
        self.assertEqual(len(results), 1)

    def test_route_list_pagination_equivalent(self):
        """Verify pagination via slicing matches total count."""
        all_results = self.db.list()
        page = all_results[0:2]
        self.assertEqual(len(page), 2)
        self.assertEqual(len(all_results), 3)


if __name__ == "__main__":
    unittest.main()
