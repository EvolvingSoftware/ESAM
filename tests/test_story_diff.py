"""Tests for the Story Diff Engine (P1-4)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from stories.headline_compare import HeadlineComparer
from stories.body_diff import BodyDiffer
from stories.trajectory import TrajectoryComputer
from stories.diff_engine import DiffEngine


# ---------------------------------------------------------------------------
# HeadlineComparer tests
# ---------------------------------------------------------------------------

class TestHeadlineComparer:
    """Test headline similarity and match finding."""

    def test_headline_similarity_same_story(self):
        """Same story with different wording should score > 0.3."""
        h = HeadlineComparer()
        sim = h.similarity(
            "AI agents reshape enterprise",
            "How AI agents are transforming business",
        )
        assert sim > 0.3, f"Expected > 0.3, got {sim}"

    def test_headline_similarity_exact(self):
        """Exact same title should score 1.0."""
        h = HeadlineComparer()
        sim = h.similarity("Breaking News: Market Surge", "Breaking News: Market Surge")
        assert sim == 1.0

    def test_headline_similarity_empty(self):
        """Empty titles should score 0.0."""
        h = HeadlineComparer()
        assert h.similarity("", "test") == 0.0
        assert h.similarity("test", "") == 0.0
        assert h.similarity("", "") == 0.0

    def test_headline_no_match(self):
        """Completely different topics should score 0.0 or very low."""
        h = HeadlineComparer()
        sim = h.similarity(
            "AI agents reshape enterprise",
            "Weather forecast for weekend",
        )
        assert sim < 0.3, f"Expected < 0.3, got {sim}"

    def test_headline_find_match_found(self):
        """find_match should return best match above threshold."""
        h = HeadlineComparer()
        existing = [
            "AI agents reshape enterprise",
            "Weather forecast for this weekend",
            "Sports: championship results",
        ]
        title, score = h.find_match("AI agents transforming enterprise", existing)
        assert title is not None
        assert score > 0.3
        assert "AI" in title

    def test_headline_find_match_not_found(self):
        """find_match should return (None, 0.0) for no match."""
        h = HeadlineComparer()
        existing = ["Weather forecast", "Sports updates"]
        title, score = h.find_match("Quantum computing breakthroughs", existing)
        assert title is None
        assert score == 0.0


# ---------------------------------------------------------------------------
# BodyDiffer tests
# ---------------------------------------------------------------------------

class TestBodyDiffer:
    """Test body text diffing at sentence level."""

    def test_body_diff_unchanged(self):
        """Identical bodies should be 'unchanged' with score 1.0."""
        b = BodyDiffer()
        text = "The market rallied today. AI stocks led gains. Tech sector up 2%."
        result = b.diff(text, text)
        assert result["diff_type"] == "unchanged"
        assert result["similarity_score"] == 1.0
        assert len(result["changes"]) == 0

    def test_body_diff_minor_changes(self):
        """Minor changes should produce 'minor_changes' with score >= 0.5."""
        b = BodyDiffer()
        a = "The market rallied today. AI stocks led gains. Tech sector up 2%."
        b_text = "The market rallied today. AI stocks led major gains. Tech sector up 3%."
        result = b.diff(a, b_text)
        assert result["diff_type"] in ("minor_changes", "unchanged")
        assert result["similarity_score"] >= 0.5

    def test_body_diff_new(self):
        """New body from empty should be 'new'."""
        b = BodyDiffer()
        result = b.diff("", "Brand new content here. With multiple sentences.")
        assert result["diff_type"] == "new"
        assert result["similarity_score"] == 0.0

    def test_body_diff_sentence_level_changes(self):
        """Changes should be detected at sentence level."""
        b = BodyDiffer()
        a = "First sentence. Second sentence. Third sentence."
        b_text = "First sentence. NEW SENTENCE HERE. Third sentence."
        result = b.diff(a, b_text)
        # Should detect at least one change (added or removed)
        change_types = {c["type"] for c in result["changes"]}
        assert "added" in change_types or "removed" in change_types

    def test_body_summarize_diffs(self):
        """summarize_diffs should return human-readable bullet points."""
        b = BodyDiffer()
        diffs = [
            b.diff("Old content with several sentences. Still relevant.", "New content here. Different sentences entirely."),
        ]
        points = b.summarize_diffs(diffs, max_points=2)
        assert len(points) <= 2
        assert all(isinstance(p, str) for p in points)
        assert any("Similarity" in p or "changes" in p or "New" in p for p in points)


# ---------------------------------------------------------------------------
# TrajectoryComputer tests
# ---------------------------------------------------------------------------

class TestTrajectoryComputer:
    """Test story trajectory computation."""

    def test_trajectory_new(self):
        """First edition should be 'new'."""
        tc = TrajectoryComputer()
        story = {
            "id": "story-1",
            "edition_count": 1,
            "signal_strength": 0.5,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        result = tc.compute(story, [])
        assert result["trajectory"] == "new"
        assert result["edition_count"] == 1

    def test_trajectory_rising(self):
        """Rising signal with multiple editions should be 'rising'."""
        tc = TrajectoryComputer()
        story = {
            "id": "story-1",
            "edition_count": 4,
            "signal_strength": 0.8,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        prior = [
            {"signal_strength": 0.3, "seen_in_current": True},
            {"signal_strength": 0.4, "seen_in_current": True},
            {"signal_strength": 0.5, "seen_in_current": True},
        ]
        result = tc.compute(story, prior)
        assert result["trajectory"] == "rising"
        assert result["signal_delta"] > 0.05

    def test_trajectory_fading(self):
        """Fading signal should be 'fading'."""
        tc = TrajectoryComputer()
        story = {
            "id": "story-1",
            "edition_count": 4,
            "signal_strength": 0.2,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        prior = [
            {"signal_strength": 0.8, "seen_in_current": True},
            {"signal_strength": 0.7, "seen_in_current": True},
            {"signal_strength": 0.6, "seen_in_current": True},
        ]
        result = tc.compute(story, prior)
        assert result["trajectory"] == "fading"
        assert result["signal_delta"] < -0.05

    def test_trajectory_stable(self):
        """Consistent signal with 3+ editions should be 'stable'."""
        tc = TrajectoryComputer()
        story = {
            "id": "story-1",
            "edition_count": 4,
            "signal_strength": 0.55,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        prior = [
            {"signal_strength": 0.5, "seen_in_current": True},
            {"signal_strength": 0.55, "seen_in_current": True},
            {"signal_strength": 0.53, "seen_in_current": True},
        ]
        result = tc.compute(story, prior)
        assert result["trajectory"] == "stable"

    def test_trajectory_resolved(self):
        """Absent from 3+ consecutive editions should be 'resolved'."""
        tc = TrajectoryComputer()
        story = {
            "id": "story-1",
            "edition_count": 5,
            "signal_strength": 0.4,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        prior = [
            {"signal_strength": 0.5, "seen_in_current": True},
            {"signal_strength": 0.5, "seen_in_current": True},
            {"signal_strength": 0.5, "seen_in_current": False},
            {"signal_strength": 0.5, "seen_in_current": False},
            {"signal_strength": 0.5, "seen_in_current": False},
        ]
        result = tc.compute(story, prior)
        assert result["trajectory"] == "resolved"

    def test_trajectory_batch(self):
        """Batch computation should return trajectories for all stories."""
        tc = TrajectoryComputer()
        stories = [
            {"id": "s1", "edition_count": 1, "signal_strength": 0.5,
             "created_at": datetime.now(timezone.utc).isoformat()},
            {"id": "s2", "edition_count": 4, "signal_strength": 0.8,
             "created_at": datetime.now(timezone.utc).isoformat()},
        ]
        editions = [
            {"story_id": "s2", "signal_strength": 0.3, "seen_in_current": True},
            {"story_id": "s2", "signal_strength": 0.4, "seen_in_current": True},
            {"story_id": "s2", "signal_strength": 0.5, "seen_in_current": True},
        ]
        results = tc.compute_batch(stories, editions)
        assert len(results) == 2
        assert results[0]["story_id"] == "s1"
        assert results[1]["story_id"] == "s2"
        assert results[0]["trajectory"] == "new"
        assert results[1]["trajectory"] in ("rising", "stable")


# ---------------------------------------------------------------------------
# DiffEngine tests
# ---------------------------------------------------------------------------

class TestDiffEngine:
    """Test the unified DiffEngine."""

    def test_diff_stories_new(self):
        """Current items not matching prior stories should be 'new'."""
        engine = DiffEngine()
        current = [{"title": "Brand new topic nobody has seen"}]
        prior = [{"id": "s1", "title": "Existing story about AI", "signal_strength": 0.5}]
        diffs = engine.diff_stories(current, prior, "wf-1")
        new_diffs = [d for d in diffs if d["diff_type"] == "new"]
        assert len(new_diffs) == 1
        assert new_diffs[0]["title"] == "Brand new topic nobody has seen"

    def test_diff_stories_continued(self):
        """Same story appearing in both editions should be 'continued'."""
        engine = DiffEngine()
        current = [{"title": "AI agents reshape enterprise", "body": "Same content"}]
        prior = [
            {"id": "s1", "title": "AI agents reshape enterprise",
             "last_headline": "AI agents reshape enterprise",
             "last_body_snippet": "Same content", "signal_strength": 0.6}
        ]
        diffs = engine.diff_stories(current, prior, "wf-1")
        continued = [d for d in diffs if d["diff_type"] == "continued"]
        assert len(continued) == 1

    def test_diff_stories_updated(self):
        """Story with changed headline should be 'updated'."""
        engine = DiffEngine()
        current = [{"title": "AI agents completely transform enterprise", "body": "New body content here"}]
        prior = [
            {"id": "s1", "title": "AI agents reshape enterprise",
             "last_headline": "AI agents reshape enterprise",
             "last_body_snippet": "Old body content from before", "signal_strength": 0.5}
        ]
        diffs = engine.diff_stories(current, prior, "wf-1")
        updated = [d for d in diffs if d["diff_type"] == "updated"]
        assert len(updated) == 1

    def test_significance_filter(self):
        """get_significant_diffs should filter by threshold."""
        engine = DiffEngine()
        diffs = [
            {"significance": "high", "title": "A"},
            {"significance": "medium", "title": "B"},
            {"significance": "low", "title": "C"},
        ]
        filtered = engine.get_significant_diffs(diffs, threshold="medium")
        assert len(filtered) == 2
        assert all(d["significance"] in ("high", "medium") for d in filtered)

    def test_diff_narrative_generation(self):
        """generate_diff_narrative should produce readable summary."""
        engine = DiffEngine()
        diffs = [
            {"story_id": None, "title": "New Story", "signal_strength": 0.1,
             "diff_type": "new", "headline_diff": {}, "body_diff": {},
             "sources_diff": "new", "significance": "low"},
            {"story_id": "s1", "title": "Updated Story", "signal_strength": 0.6,
             "diff_type": "updated", "headline_diff": {"similarity": 0.4},
             "body_diff": {"diff_type": "significant_changes", "similarity_score": 0.3},
             "sources_diff": "unchanged", "significance": "high"},
        ]
        narrative = engine.generate_diff_narrative(diffs)
        assert isinstance(narrative, str)
        assert "New Stories" in narrative
        assert "Updated Stories" in narrative
        assert "New Story" in narrative
        assert "Updated Story" in narrative


# ---------------------------------------------------------------------------
# Workflow executor step type test
# ---------------------------------------------------------------------------

class TestWorkflowExecutorStepTypes:
    """Test that workflow_executor routes story diff step types correctly."""

    def test_story_diff_step_type_registered(self):
        """diff_stories, compute_trajectories, generate_diff_narrative should be in executor dispatch."""
        from workflow_executor import WorkflowExecutor
        executor = WorkflowExecutor()
        # Verify the executor has the handler methods
        assert hasattr(executor, "_execute_diff_stories_step")
        assert hasattr(executor, "_execute_compute_trajectories_step")
        assert hasattr(executor, "_execute_generate_diff_narrative_step")
        # Verify they are callable
        assert callable(executor._execute_diff_stories_step)
        assert callable(executor._execute_compute_trajectories_step)
        assert callable(executor._execute_generate_diff_narrative_step)
