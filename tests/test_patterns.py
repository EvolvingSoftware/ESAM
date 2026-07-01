#!/usr/bin/env python3
"""Tests for the Prompt Pattern Library (src/patterns/)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Add src to path
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from patterns.engine import (
    PatternRegistry,
    PatternRenderer,
    OutputSchemaValidator,
)


# ── Helpers ────────────────────────────────────────────────────────


def make_test_pattern(name: str = "test-pattern", **overrides) -> dict:
    """Build a minimal pattern dict for testing."""
    return {
        "id": f"test-{name}",
        "name": name,
        "description": "Test pattern for unit tests",
        "sections": [
            {"section-a": {"count": "2-4", "style": "bold claim with evidence"}},
            {"section-b": {"count": "1-2", "focus": "key findings"}},
        ],
        "output_schema": {
            "type": "json",
            "fields": {
                "subject": "string",
                "body_markdown": "string",
                "key_signals": "array",
                "sources": "array",
                "citation_map": "dict",
            },
        },
        "citation_rules": {
            "enforce_verified": True,
            "hallucination_guard": True,
        },
        "brand_voice": "architectural, definitive, forward-looking",
        "category": "test",
        "tags": ["test", "unit"],
        **overrides,
    }


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_db():
    """Ensure a clean state by creating a fresh registry that ensures schema."""
    from database import get_connection
    conn = get_connection()
    conn.execute("DELETE FROM wf_prompt_patterns WHERE category = 'test'")
    conn.commit()
    yield
    conn = get_connection()
    conn.execute("DELETE FROM wf_prompt_patterns WHERE category = 'test'")
    conn.commit()


# ═══════════════════════════════════════════════════════════════════
# PatternRegistry Tests
# ═══════════════════════════════════════════════════════════════════


class TestPatternRegistry:
    def test_register_pattern(self):
        reg = PatternRegistry()
        pat = make_test_pattern("register-me")
        result = reg.register(pat)

        assert result["name"] == "register-me"
        assert result["id"] == "test-register-me"
        assert result["version"] == 1
        assert "created_at" in result
        assert "updated_at" in result

    def test_register_pattern_auto_id(self):
        reg = PatternRegistry()
        pat = {"name": "auto-id-pattern", "sections": [], "output_schema": {}}
        result = reg.register(pat)
        assert result["name"] == "auto-id-pattern"
        assert result["id"].startswith("pat-")
        assert result["version"] == 1

    def test_register_pattern_missing_name(self):
        reg = PatternRegistry()
        with pytest.raises(ValueError, match="name"):
            reg.register({"sections": []})

    def test_get_pattern(self):
        reg = PatternRegistry()
        pat = make_test_pattern("get-me")
        created = reg.register(pat)

        fetched = reg.get(created["id"])
        assert fetched is not None
        assert fetched["name"] == "get-me"
        assert fetched["id"] == created["id"]

    def test_get_pattern_not_found(self):
        reg = PatternRegistry()
        result = reg.get("nonexistent-pattern")
        assert result is None

    def test_list_patterns(self):
        reg = PatternRegistry()
        reg.register(make_test_pattern("list-a"))
        reg.register(make_test_pattern("list-b"))

        patterns = reg.list()
        names = [p["name"] for p in patterns]
        assert "list-a" in names
        assert "list-b" in names

    def test_list_patterns_by_category(self):
        reg = PatternRegistry()
        reg.register(make_test_pattern("cat-a", category="alpha"))
        reg.register(make_test_pattern("cat-b", category="beta"))

        alpha_patterns = reg.list(category="alpha")
        names = [p["name"] for p in alpha_patterns]
        assert "cat-a" in names
        assert "cat-b" not in names

    def test_update_pattern(self):
        reg = PatternRegistry()
        pat = make_test_pattern("update-me")
        created = reg.register(pat)
        original_version = created["version"]

        updated = reg.update(created["id"], {"name": "updated-name"})
        assert updated["name"] == "updated-name"
        assert updated["version"] > original_version

    def test_update_pattern_not_found(self):
        reg = PatternRegistry()
        with pytest.raises(ValueError, match="not found"):
            reg.update("nonexistent", {"name": "nope"})

    def test_delete_pattern(self):
        reg = PatternRegistry()
        pat = make_test_pattern("delete-me")
        created = reg.register(pat)

        assert reg.delete(created["id"]) is True
        assert reg.get(created["id"]) is None

    def test_delete_pattern_not_found(self):
        reg = PatternRegistry()
        assert reg.delete("nonexistent") is False

    def test_version_history(self):
        reg = PatternRegistry()
        pat = make_test_pattern("version-test")
        created = reg.register(pat)

        history = reg.get_version_history(created["id"])
        assert len(history) >= 1
        assert history[0]["id"] == created["id"]
        assert history[0]["version"] == 1


# ═══════════════════════════════════════════════════════════════════
# PatternRenderer Tests
# ═══════════════════════════════════════════════════════════════════


class TestPatternRenderer:
    def test_render_pattern(self):
        reg = PatternRegistry()
        pat = make_test_pattern("render-test")
        created = reg.register(pat)

        renderer = PatternRenderer(registry=reg)
        result = renderer.render(created["id"], context={"date": "2026-06-26"}, data={})

        assert "rendered_prompt" in result
        assert "sections" in result
        assert "metadata" in result
        assert len(result["sections"]) == 2
        assert result["metadata"]["pattern_id"] == created["id"]
        assert result["metadata"]["pattern_version"] == 1
        assert "2026-06-26" in result["rendered_prompt"]

    def test_render_pattern_not_found(self):
        renderer = PatternRenderer()
        with pytest.raises(ValueError, match="not found"):
            renderer.render("nonexistent", {}, {})

    def test_render_with_data(self):
        reg = PatternRegistry()
        pat = make_test_pattern("data-render")
        created = reg.register(pat)

        renderer = PatternRenderer(registry=reg)
        result = renderer.render(
            created["id"],
            context={"workflow_name": "Daily Brief"},
            data={
                "signals": [
                    {"title": "AI adoption accelerating", "url": "https://example.com/1"},
                    {"title": "New model release", "url": "https://example.com/2"},
                    {"title": "Regulation update", "url": "https://example.com/3"},
                ],
                "sources": [
                    {"url": "https://example.com/1", "title": "Source 1"},
                    {"url": "https://example.com/2", "title": "Source 2"},
                ],
            },
        )

        assert "Daily Brief" in result["rendered_prompt"]
        assert "AI adoption" in result["rendered_prompt"]
        assert len(result["sections"]) == 2

    def test_render_section_ordering(self):
        reg = PatternRegistry()
        pat = make_test_pattern("ordering-test", sections=[
            {"first-section": {"count": 1}},
            {"second-section": {"count": 1}},
            {"third-section": {"count": 1}},
        ])
        created = reg.register(pat)

        renderer = PatternRenderer(registry=reg)
        result = renderer.render(created["id"], {}, {})

        section_names = [s["name"] for s in result["sections"]]
        assert section_names == ["first-section", "second-section", "third-section"]

    def test_render_count_constraints(self):
        reg = PatternRegistry()
        pat = make_test_pattern("count-test", sections=[
            {"constrained-section": {"count": "5-7"}},
        ])
        created = reg.register(pat)

        renderer = PatternRenderer(registry=reg)
        result = renderer.render(created["id"], {}, {})

        assert len(result["sections"]) == 1
        assert result["sections"][0]["constraint"] == "5-7"
        assert "5-7" in result["sections"][0]["content"]

    def test_render_citation_instructions(self):
        reg = PatternRegistry()
        pat = make_test_pattern("citation-test")
        created = reg.register(pat)

        renderer = PatternRenderer(registry=reg)
        result = renderer.render(created["id"], {}, {})

        assert "[SXXX]" in result["rendered_prompt"]
        assert "verified citation" in result["rendered_prompt"].lower()
        assert "hallucination" in result["rendered_prompt"].lower()


# ═══════════════════════════════════════════════════════════════════
# OutputSchemaValidator Tests
# ═══════════════════════════════════════════════════════════════════


class TestOutputSchemaValidator:
    def test_validate_valid_output(self):
        reg = PatternRegistry()
        pat = make_test_pattern("valid-schema")
        created = reg.register(pat)

        validator = OutputSchemaValidator(registry=reg)
        output = {
            "subject": "Test Subject",
            "body_markdown": "Body text with citation [S001]",
            "key_signals": ["Signal 1", "Signal 2"],
            "sources": [{"url": "https://example.com/1", "key": "S001"}],
            "citation_map": {"S001": "https://example.com/1"},
        }
        result = validator.validate(output, created["id"])

        assert result["valid"] is True
        assert result["errors"] == []

    def test_validate_missing_required_field(self):
        reg = PatternRegistry()
        pat = make_test_pattern("missing-field")
        created = reg.register(pat)

        validator = OutputSchemaValidator(registry=reg)
        output = {
            "subject": "Only subject provided",
            # missing body_markdown, key_signals, sources, citation_map
        }
        result = validator.validate(output, created["id"])

        assert result["valid"] is False
        assert len(result["errors"]) >= 1
        error_fields = [e for e in result["errors"] if "Missing required" in e]
        assert len(error_fields) >= 1

    def test_validate_wrong_type(self):
        reg = PatternRegistry()
        pat = make_test_pattern("wrong-type")
        created = reg.register(pat)

        validator = OutputSchemaValidator(registry=reg)
        output = {
            "subject": "Test",
            "body_markdown": "Body",
            "key_signals": "not an array",  # should be array
            "sources": [],
            "citation_map": {},
        }
        result = validator.validate(output, created["id"])

        assert result["valid"] is False
        type_errors = [e for e in result["errors"] if "expected" in e]
        assert len(type_errors) >= 1

    def test_validate_unverified_citation(self):
        reg = PatternRegistry()
        pat = make_test_pattern("citation-violation")
        created = reg.register(pat)

        validator = OutputSchemaValidator(registry=reg)
        output = {
            "subject": "Test",
            "body_markdown": "Claim with citation [S999] that is not in citation_map",
            "key_signals": ["Signal"],
            "sources": [],
            "citation_map": {"S001": "https://example.com/1"},
        }
        result = validator.validate(output, created["id"])

        assert result["valid"] is False
        citation_errors = [e for e in result["errors"] if "citation_map" in e.lower()]
        assert len(citation_errors) >= 1

    def test_validate_pattern_not_found(self):
        validator = OutputSchemaValidator()
        result = validator.validate({"subject": "Test"}, "nonexistent")
        assert result["valid"] is False
        assert "not found" in result["errors"][0]


# ═══════════════════════════════════════════════════════════════════
# Integration: Seed the es-daily-signal pattern
# ═══════════════════════════════════════════════════════════════════


class TestESDailySignalSeed:
    """Verify the es-daily-signal pattern can be registered and rendered."""

    def test_seed_es_daily_signal(self):
        reg = PatternRegistry()
        pattern = reg.register({
            "id": "es-daily-signal",
            "name": "Evolving Software Daily Signal",
            "description": "Daily intelligence brief with For Evolving Software analytical framework",
            "sections": [
                {"converging-signals": {"count": "5-7", "style": "**bold claim.** evidence (with citation). *For Evolving Software...* Watch for..."}},
                {"frontier-lab-48h": {"count": "3-5", "focus": "product/research launches in past 48 hours"}},
                {"ai-interaction-psychology": {"count": "2-3", "focus": "human-AI interaction, perception, trust"}},
                {"business-architecture": {"count": "2-3", "focus": "enterprise adoption, pricing, regulation"}},
                {"what-seems-taking-off": {"count": "2-3", "focus": "trends gaining momentum"}},
                {"article-ideas": {"count": "3-5", "format": "concrete blog post titles"}},
            ],
            "output_schema": {
                "type": "json",
                "fields": ["subject", "body_markdown", "key_signals", "sources", "citation_map"],
            },
            "citation_rules": {"enforce_verified": True, "hallucination_guard": True},
            "brand_voice": "architectural, definitive, forward-looking",
            "category": "newsletter",
            "tags": ["newsletter", "daily", "evolving-software"],
        })

        assert pattern["id"] == "es-daily-signal"
        assert pattern["name"] == "Evolving Software Daily Signal"
        assert pattern["version"] >= 1

        # Verify sections in config
        config = pattern.get("_config", {})
        assert len(config["sections"]) == 6
        section_names = [list(s.keys())[0] if isinstance(s, dict) else s for s in config["sections"]]
        assert "converging-signals" in section_names
        assert "article-ideas" in section_names

    def test_render_es_daily_signal(self):
        reg = PatternRegistry()
        reg.register({
            "id": "es-daily-signal",
            "name": "Evolving Software Daily Signal",
            "description": "Daily intelligence brief",
            "sections": [
                {"converging-signals": {"count": "5-7"}},
                {"article-ideas": {"count": "3-5"}},
            ],
            "output_schema": {
                "fields": ["subject", "body_markdown", "key_signals", "sources", "citation_map"],
            },
            "citation_rules": {"enforce_verified": True},
            "brand_voice": "architectural, definitive, forward-looking",
        })

        renderer = PatternRenderer(registry=reg)
        result = renderer.render("es-daily-signal", context={"date": "2026-06-26"}, data={})

        assert "rendered_prompt" in result
        assert "Converging Signals" in result["rendered_prompt"]
        assert "Article Ideas" in result["rendered_prompt"]
        assert "architectural" in result["rendered_prompt"]
        assert "[SXXX]" in result["rendered_prompt"]

    def test_validate_es_daily_signal_output(self):
        reg = PatternRegistry()
        reg.register({
            "id": "es-daily-signal",
            "name": "Evolving Software Daily Signal",
            "sections": [{"test": {"count": 1}}],
            "output_schema": {
                "fields": ["subject", "body_markdown", "key_signals", "sources", "citation_map"],
            },
            "citation_rules": {"enforce_verified": True},
        })

        validator = OutputSchemaValidator(registry=reg)
        result = validator.validate(
            {
                "subject": "Daily Brief: AI Signals",
                "body_markdown": "**AI Adoption Accelerating.** Evidence [S001].",
                "key_signals": ["AI Adoption Accelerating", "New Model Release"],
                "sources": [{"url": "https://example.com/1", "key": "S001"}],
                "citation_map": {"S001": "https://example.com/1"},
            },
            "es-daily-signal",
        )

        assert result["valid"] is True, f"Errors: {result['errors']}"
