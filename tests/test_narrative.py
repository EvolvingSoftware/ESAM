"""Tests for Narrative Synthesizer — P1-5."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from narrative.engine import NarrativeEngine
from narrative.arc_detector import ArcDetector
from narrative.decay import SignalDecayer
from narrative.ideas import ArticleIdeaGenerator


# ── NarrativeEngine Tests ─────────────────────────────────────────────


def test_synthesize_basic():
    """Generate narrative from diffs — basic smoke test."""
    engine = NarrativeEngine()
    diffs = [
        {
            "story_id": "s1",
            "title": "AI agents on the rise",
            "diff_type": "continued",
            "signal_strength": 0.75,
            "edition_count": 3,
        },
        {
            "story_id": "s2",
            "title": "New LLM benchmark released",
            "diff_type": "new",
            "signal_strength": 0.5,
            "edition_count": 1,
        },
    ]
    trajectories = {"s1": "rising", "s2": "new"}
    text = engine.synthesize(diffs, trajectories)
    assert text is not None
    assert isinstance(text, str)
    assert len(text) > 50
    # Should contain section heading
    assert text.startswith("# Narrative Synthesis")
    # Should mention both stories
    assert "AI agents on the rise" in text
    assert "New LLM benchmark released" in text


def test_synthesize_rising_trajectory():
    """Narrative should mention 'rising' for rising trajectory."""
    engine = NarrativeEngine()
    diffs = [
        {
            "story_id": "s1",
            "title": "AI safety research funding",
            "diff_type": "continued",
            "signal_strength": 0.85,
            "edition_count": 5,
        },
    ]
    trajectories = {"s1": "rising"}
    text = engine.synthesize(diffs, trajectories)
    assert "rising" in text.lower(), f"Narrative should mention 'rising': {text}"


def test_synthesize_empty_diffs():
    """Empty diffs should return a default message."""
    engine = NarrativeEngine()
    text = engine.synthesize([], {})
    assert "No story signals detected" in text


def test_synthesize_batch():
    """synthesize_batch should return structured narratives + combined."""
    engine = NarrativeEngine()
    diffs = {
        "wf1": [
            {
                "story_id": "s1",
                "title": "AI agents",
                "diff_type": "continued",
                "signal_strength": 0.7,
                "edition_count": 3,
            },
        ],
        "wf2": [
            {
                "story_id": "s2",
                "title": "Robotics update",
                "diff_type": "new",
                "signal_strength": 0.5,
                "edition_count": 1,
            },
        ],
    }
    trajectories = {
        "wf1": {"s1": "rising"},
        "wf2": {"s2": "new"},
    }
    result = engine.synthesize_batch(diffs, trajectories)
    assert "narratives" in result
    assert "combined_narrative" in result
    assert len(result["narratives"]) == 2
    assert result["narratives"][0]["story_id"] == "s1"
    assert result["narratives"][0]["trajectory"] == "rising"
    assert len(result["combined_narrative"]) > 50


# ── ArcDetector Tests ─────────────────────────────────────────────────


def test_arc_detection():
    """Find arcs across editions — stories with 2+ editions."""
    detector = ArcDetector()
    stories = [
        {
            "id": "s1",
            "title": "AI regulation debate",
            "edition_count": 3,
            "signal_strength": 0.8,
            "signal_trajectory": "rising",
            "created_at": "2026-06-01T00:00:00Z",
            "updated_at": "2026-06-26T00:00:00Z",
        },
        {
            "id": "s2",
            "title": "Quantum computing breakthrough",
            "edition_count": 2,
            "signal_strength": 0.6,
            "signal_trajectory": "stable",
            "created_at": "2026-06-10T00:00:00Z",
            "updated_at": "2026-06-25T00:00:00Z",
        },
        {
            "id": "s3",
            "title": "One-off event",
            "edition_count": 1,
            "signal_strength": 0.3,
            "signal_trajectory": "new",
            "created_at": "2026-06-26T00:00:00Z",
            "updated_at": "2026-06-26T00:00:00Z",
        },
    ]
    arcs = detector.detect(stories, min_arc_length=2)
    assert len(arcs) >= 2  # s1 and s2 have 2+ editions
    arc_names = [a["arc_name"] for a in arcs]
    assert "AI regulation debate" in arc_names or any("AI regulation" in n for n in arc_names)
    assert "Quantum computing breakthrough" in arc_names or any("Quantum" in n for n in arc_names)


def test_arc_labeling():
    """Arc labeling should produce human-readable names."""
    detector = ArcDetector()
    stories = [
        {
            "id": "s1",
            "title": "OpenAI launches GPT-5",
            "signal_strength": 0.9,
            "edition_count": 3,
        },
    ]
    name = detector.label_arc(stories)
    assert isinstance(name, str)
    assert len(name) > 0
    assert "OpenAI" in name

    # Multi-story arc name
    stories2 = [
        {"id": "s1", "title": "AI regulation", "signal_strength": 0.8, "edition_count": 3},
        {"id": "s2", "title": "EU AI Act update", "signal_strength": 0.6, "edition_count": 2},
    ]
    name2 = detector.label_arc(stories2)
    assert "related" in name2.lower() or "&" in name2


def test_detect_new_arcs():
    """detect_new_arcs should only return arcs not in prior arcs."""
    detector = ArcDetector()
    stories = [
        {"id": "s1", "title": "New rising story", "signal_strength": 0.7, "edition_count": 3},
        {"id": "s2", "title": "Existing story", "signal_strength": 0.5, "edition_count": 2},
    ]
    prior_arcs = [
        {"arc_name": "Existing story", "stories": ["s2"], "strength": 0.5},
    ]
    new_arcs = detector.detect_new_arcs(stories, prior_arcs)
    assert len(new_arcs) >= 1
    arc_stories = set()
    for a in new_arcs:
        arc_stories.update(a.get("stories", []))
    assert "s2" not in arc_stories  # s2 is in prior arcs


# ── SignalDecayer Tests ───────────────────────────────────────────────


def test_signal_decay():
    """Exponential decay formula should correctly reduce signal strength."""
    decayer = SignalDecayer()

    # Test basic formula: strength * 0.5^(days/half_life)
    # 1 day with half-life 14: 1.0 * 0.5^(1/14) ≈ 0.9516
    result = decayer.decay(1.0, 1, half_life_days=14)
    assert 0.9 < result < 1.0, f"Expected ~0.95, got {result}"

    # 14 days (one half-life): should be ~0.5
    result = decayer.decay(1.0, 14, half_life_days=14)
    assert 0.49 < result < 0.51, f"Expected ~0.5, got {result}"

    # 0 days: should be unchanged
    result = decayer.decay(0.75, 0, half_life_days=14)
    assert result == 0.75

    # 28 days (two half-lives): should be ~0.25
    result = decayer.decay(1.0, 28, half_life_days=14)
    assert 0.24 < result < 0.26, f"Expected ~0.25, got {result}"


def test_stale_stories_detection():
    """Stories that have decayed below threshold should be detected."""
    decayer = SignalDecayer()

    # Create a story that was last seen many days ago
    now = datetime.now(timezone.utc)
    old_date = (now - timedelta(days=30)).isoformat()

    stories = [
        {
            "id": "s1",
            "title": "Old story",
            "signal_strength": 0.5,
            "updated_at": old_date,
        },
        {
            "id": "s2",
            "title": "Recent story",
            "signal_strength": 0.5,
            "updated_at": now.isoformat(),
        },
    ]

    # Apply decay first
    decayed = decayer.apply_decay_batch(stories)

    # Check that old story decayed significantly
    old_decayed = [s for s in decayed if s["id"] == "s1"][0]
    assert old_decayed["decayed_signal_strength"] < 0.3  # 30 days with half-life 14

    # Find stale stories
    stale = decayer.get_stale_stories(decayed, threshold=0.3, min_days=7)
    stale_ids = [s["id"] for s in stale]
    assert "s1" in stale_ids
    assert "s2" not in stale_ids


def test_apply_decay_batch():
    """apply_decay_batch should return stories with decayed signals."""
    decayer = SignalDecayer()
    now = datetime.now(timezone.utc)
    stories = [
        {
            "id": "s1",
            "title": "Story A",
            "signal_strength": 1.0,
            "updated_at": (now - timedelta(days=14)).isoformat(),
        },
        {
            "id": "s2",
            "title": "Story B",
            "signal_strength": 0.5,
            "updated_at": now.isoformat(),
        },
    ]
    result = decayer.apply_decay_batch(stories)
    assert len(result) == 2
    # Story A should be ~0.5 after 14 days
    s1 = [s for s in result if s["id"] == "s1"][0]
    assert 0.45 < s1["decayed_signal_strength"] < 0.55
    # Story B should be unchanged
    s2 = [s for s in result if s["id"] == "s2"][0]
    assert s2["decayed_signal_strength"] == 0.5


# ── ArticleIdeaGenerator Tests ────────────────────────────────────────


def test_article_ideas():
    """Generate article ideas from signals."""
    generator = ArticleIdeaGenerator()
    signals = [
        {
            "title": "AI safety research funding increases",
            "signal_strength": 0.85,
            "trajectory": "rising",
            "source": "techcrunch",
        },
        {
            "title": "New EU AI Act amendments proposed",
            "signal_strength": 0.7,
            "trajectory": "rising",
            "source": "reuters",
        },
        {
            "title": "OpenAI releases GPT-5",
            "signal_strength": 0.6,
            "trajectory": "stable",
            "source": "techcrunch",
        },
    ]
    ideas = generator.generate(signals, max_ideas=3)
    assert len(ideas) >= 2
    assert len(ideas) <= 3
    for idea in ideas:
        assert "title" in idea
        assert "rationale" in idea
        assert "signals_involved" in idea
        assert "target_audience" in idea
        assert len(idea["title"]) > 0


def test_article_ideas_empty_signals():
    """Empty signals should produce no ideas."""
    generator = ArticleIdeaGenerator()
    ideas = generator.generate([], max_ideas=5)
    assert ideas == []


# ── Step Type Routing Test ────────────────────────────────────────────


def test_narrative_step_type():
    """Verify that step type routing in workflow_executor would work
    by checking that the narrative module can be imported and that
    the NarrativeEngine is callable with the expected interface."""
    from narrative.engine import NarrativeEngine

    engine = NarrativeEngine()
    # Must have both synthesize and synthesize_batch methods
    assert hasattr(engine, "synthesize")
    assert hasattr(engine, "synthesize_batch")
    assert callable(engine.synthesize)
    assert callable(engine.synthesize_batch)

    from narrative.arc_detector import ArcDetector

    detector = ArcDetector()
    assert hasattr(detector, "detect")
    assert hasattr(detector, "label_arc")
    assert hasattr(detector, "detect_new_arcs")

    from narrative.decay import SignalDecayer

    decayer = SignalDecayer()
    assert hasattr(decayer, "decay")
    assert hasattr(decayer, "apply_decay_batch")
    assert hasattr(decayer, "get_stale_stories")

    from narrative.ideas import ArticleIdeaGenerator

    generator = ArticleIdeaGenerator()
    assert hasattr(generator, "generate")
