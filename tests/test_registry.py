"""Tests for edition registry (P2-4)."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from registry.engine import EditionRegistry
from registry.comparer import EditionComparer
from registry.stats import EditionStats


def _mock_conn():
    """Create an in-memory SQLite database with the wf_editions table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS wf_editions (
            id TEXT PRIMARY KEY,
            workflow_id TEXT, run_id TEXT, edition_number INTEGER UNIQUE,
            date TEXT, subject TEXT,
            signal_ids TEXT DEFAULT '[]',
            citation_ids TEXT DEFAULT '[]',
            narrative_json TEXT DEFAULT '{}',
            quality_score REAL,
            source_count INTEGER DEFAULT 0,
            item_count INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            duration_seconds REAL DEFAULT 0.0,
            created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_wf_editions_number ON wf_editions(edition_number);
        CREATE INDEX IF NOT EXISTS idx_wf_editions_run ON wf_editions(run_id);

        CREATE TABLE IF NOT EXISTS wf_stories (
            id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            title TEXT NOT NULL,
            title_hash TEXT NOT NULL,
            first_seen_run_id TEXT,
            last_seen_run_id TEXT,
            edition_count INTEGER DEFAULT 0,
            signal_strength REAL DEFAULT 0.0,
            change_log_json TEXT DEFAULT '[]',
            last_headline TEXT DEFAULT '',
            last_body_snippet TEXT DEFAULT '',
            sources_json TEXT DEFAULT '[]',
            tags TEXT DEFAULT '',
            narrative_summary TEXT DEFAULT '',
            signal_trajectory TEXT DEFAULT 'new',
            related_story_ids TEXT DEFAULT '[]',
            created_at TEXT,
            updated_at TEXT
        );
    """)
    return conn


class TestEditionRegistry(unittest.TestCase):
    """Tests for EditionRegistry CRUD operations."""

    def setUp(self):
        self.conn = _mock_conn()
        self.reg = EditionRegistry(db_conn=self.conn)

    def test_create_edition(self):
        """Create an edition and verify all fields are set."""
        e = self.reg.create("wf-test", "run-1", "Test Edition", 5, 20, 10000, 45.5)
        self.assertIn("id", e)
        self.assertEqual(e["workflow_id"], "wf-test")
        self.assertEqual(e["run_id"], "run-1")
        self.assertEqual(e["subject"], "Test Edition")
        self.assertEqual(e["source_count"], 5)
        self.assertEqual(e["item_count"], 20)
        self.assertEqual(e["total_tokens"], 10000)
        self.assertEqual(e["duration_seconds"], 45.5)
        self.assertEqual(e["edition_number"], 1)
        self.assertIsNotNone(e["created_at"])

    def test_get_edition(self):
        """Retrieve an edition by ID."""
        e = self.reg.create("wf-test", "run-1", "Test Edition", 5, 20, 10000, 45.5)
        got = self.reg.get(e["id"])
        self.assertIsNotNone(got)
        self.assertEqual(got["id"], e["id"])
        self.assertEqual(got["subject"], "Test Edition")

    def test_get_edition_not_found(self):
        """Getting a non-existent edition returns None."""
        got = self.reg.get("non-existent-id")
        self.assertIsNone(got)

    def test_edition_numbering(self):
        """Edition numbers auto-increment."""
        e1 = self.reg.create("wf-test", "run-1", "First", 1, 10, 5000, 30.0)
        self.assertEqual(e1["edition_number"], 1)
        e2 = self.reg.create("wf-test", "run-2", "Second", 2, 20, 10000, 45.0)
        self.assertEqual(e2["edition_number"], 2)
        e3 = self.reg.create("wf-test", "run-3", "Third", 3, 30, 15000, 60.0)
        self.assertEqual(e3["edition_number"], 3)

    def test_get_by_number(self):
        """Retrieve an edition by edition_number."""
        e = self.reg.create("wf-test", "run-1", "By Number", 5, 20, 10000, 45.5)
        got = self.reg.get_by_number(e["edition_number"])
        self.assertIsNotNone(got)
        self.assertEqual(got["id"], e["id"])

    def test_list_editions(self):
        """List returns editions ordered by edition_number descending."""
        self.reg.create("wf-test", "run-1", "First", 1, 10, 5000, 30.0)
        self.reg.create("wf-test", "run-2", "Second", 2, 20, 10000, 45.0)
        self.reg.create("wf-test", "run-3", "Third", 3, 30, 15000, 60.0)
        editions = self.reg.list(limit=10, offset=0)
        self.assertEqual(len(editions), 3)
        # Should be ordered by edition_number DESC
        numbers = [e["edition_number"] for e in editions]
        self.assertEqual(numbers, [3, 2, 1])

    def test_list_limit_offset(self):
        """List respects limit and offset."""
        for i in range(5):
            self.reg.create("wf-test", f"run-{i}", f"Edition {i}", 1, 10, 5000, 30.0)
        editions = self.reg.list(limit=2, offset=1)
        self.assertEqual(len(editions), 2)
        # offset=1 means skip the first (edition 5), so we get 4, 3
        self.assertEqual(editions[0]["edition_number"], 4)
        self.assertEqual(editions[1]["edition_number"], 3)

    def test_get_latest(self):
        """get_latest returns the most recent edition."""
        self.reg.create("wf-test", "run-1", "First", 1, 10, 5000, 30.0)
        self.reg.create("wf-test", "run-2", "Second", 2, 20, 10000, 45.0)
        latest = self.reg.get_latest()
        self.assertIsNotNone(latest)
        self.assertEqual(latest["edition_number"], 2)

    def test_get_latest_none(self):
        """get_latest returns None when no editions exist."""
        latest = self.reg.get_latest()
        self.assertIsNone(latest)

    def test_get_by_run(self):
        """Retrieve an edition by run_id."""
        e = self.reg.create("wf-test", "run-specific", "By Run", 5, 20, 10000, 45.5)
        got = self.reg.get_by_run("run-specific")
        self.assertIsNotNone(got)
        self.assertEqual(got["id"], e["id"])

    def test_get_by_run_not_found(self):
        """get_by_run returns None when no edition matches."""
        got = self.reg.get_by_run("non-existent-run")
        self.assertIsNone(got)


class TestEditionComparer(unittest.TestCase):
    """Tests for EditionComparer."""

    def setUp(self):
        self.conn = _mock_conn()
        self.reg = EditionRegistry(db_conn=self.conn)
        self.comparer = EditionComparer(db_conn=self.conn)

    def test_compare_editions(self):
        """Compare two editions and check diff fields."""
        a = self.reg.create("wf-test", "run-1", "First Edition", 5, 20, 10000, 45.0)
        b = self.reg.create("wf-test", "run-2", "Second Edition", 8, 35, 18000, 60.0)

        result = self.comparer.compare(a["id"], b["id"])

        self.assertIn("edition_a", result)
        self.assertIn("edition_b", result)
        self.assertEqual(result["subject_diff"], "'First Edition' → 'Second Edition'")
        self.assertEqual(result["source_count_diff"], 3)
        self.assertEqual(result["item_count_diff"], 15)
        self.assertEqual(result["token_diff"], 8000)
        self.assertEqual(result["duration_diff"], 15.0)

    def test_compare_identical_editions(self):
        """Compare two editions with identical subject."""
        a = self.reg.create("wf-test", "run-1", "Same Subject", 5, 20, 10000, 45.0)
        b = self.reg.create("wf-test", "run-2", "Same Subject", 5, 20, 10000, 45.0)

        result = self.comparer.compare(a["id"], b["id"])

        self.assertEqual(result["subject_diff"], "unchanged")
        self.assertEqual(result["source_count_diff"], 0)

    def test_compare_latest(self):
        """compare_latest compares the last 2 editions."""
        self.reg.create("wf-test", "run-1", "First", 5, 20, 10000, 45.0)
        self.reg.create("wf-test", "run-2", "Second", 8, 35, 18000, 60.0)

        result = self.comparer.compare_latest()

        self.assertNotIn("error", result)
        self.assertEqual(result["edition_b"]["edition_number"], 2)

    def test_compare_latest_insufficient(self):
        """compare_latest returns error when fewer than 2 editions exist."""
        self.reg.create("wf-test", "run-1", "Only One", 5, 20, 10000, 45.0)

        result = self.comparer.compare_latest()

        self.assertIn("error", result)

    def test_compare_not_found(self):
        """compare returns error when an edition is missing."""
        result = self.comparer.compare("nonexistent-a", "nonexistent-b")
        self.assertIn("error", result)


class TestEditionStats(unittest.TestCase):
    """Tests for EditionStats."""

    def setUp(self):
        self.conn = _mock_conn()
        self.reg = EditionRegistry(db_conn=self.conn)
        self.stats = EditionStats(db_conn=self.conn)

    def test_compute_stats(self):
        """Compute statistics for a single edition."""
        e = self.reg.create("wf-test", "run-1", "Test Edition", 5, 20, 10000, 45.5)
        result = self.stats.compute(e["id"])

        self.assertEqual(result["edition_number"], 1)
        self.assertEqual(result["source_count"], 5)
        self.assertEqual(result["item_count"], 20)
        self.assertEqual(result["total_tokens"], 10000)
        self.assertEqual(result["duration_seconds"], 45.5)
        self.assertEqual(result["avg_tokens_per_source"], 2000.0)
        self.assertEqual(result["signal_count"], 0)
        self.assertEqual(result["top_signals"], [])

    def test_compute_stats_not_found(self):
        """compute returns error when edition not found."""
        result = self.stats.compute("nonexistent")
        self.assertIn("error", result)

    def test_compute_trend(self):
        """Compute trend across multiple editions."""
        self.reg.create("wf-test", "run-1", "First", 5, 20, 10000, 45.0)
        self.reg.create("wf-test", "run-2", "Second", 8, 35, 18000, 60.0)
        self.reg.create("wf-test", "run-3", "Third", 3, 15, 5000, 30.0)

        result = self.stats.compute_trend()

        self.assertEqual(result["total_editions"], 3)
        # Avg sources: (5+8+3)/3 = 5.33
        self.assertAlmostEqual(result["avg_sources_per_edition"], 5.33, places=1)
        # Avg items: (20+35+15)/3 = 23.33
        self.assertAlmostEqual(result["avg_items_per_edition"], 23.33, places=1)
        # Total tokens: 10000+18000+5000 = 33000
        self.assertEqual(result["total_tokens_across_all"], 33000)
        # Avg duration: (45+60+30)/3 = 45.0
        self.assertAlmostEqual(result["avg_duration"], 45.0, places=1)

    def test_compute_trend_empty(self):
        """Trend with no editions returns zeros."""
        result = self.stats.compute_trend()

        self.assertEqual(result["total_editions"], 0)
        self.assertEqual(result["avg_sources_per_edition"], 0.0)
        self.assertEqual(result["total_tokens_across_all"], 0)

    def test_compute_with_signals(self):
        """Stats should handle signal_ids from edition."""
        e = self.reg.create("wf-test", "run-1", "With Signals", 5, 20, 10000, 45.5)
        # Set signal_ids directly in DB
        self.conn.execute(
            "UPDATE wf_editions SET signal_ids = ? WHERE id = ?",
            (json.dumps(["story-1", "story-2"]), e["id"]),
        )
        self.conn.commit()

        result = self.stats.compute(e["id"])
        self.assertEqual(result["signal_count"], 2)


class TestRegistryStepTypes(unittest.TestCase):
    """Tests that workflow executor step types route correctly."""

    def test_registry_step_type_enumeration(self):
        """Verify step type names exist in the executor dispatch logic.

        We check by importing the executor and verifying the three
        step type methods exist on the class.
        """
        from workflow_executor import WorkflowExecutor

        methods = [
            "_execute_register_edition_step",
            "_execute_compare_editions_step",
            "_execute_compute_edition_stats_step",
        ]
        for m in methods:
            self.assertTrue(
                hasattr(WorkflowExecutor, m),
                f"WorkflowExecutor missing method: {m}",
            )


if __name__ == "__main__":
    unittest.main()
