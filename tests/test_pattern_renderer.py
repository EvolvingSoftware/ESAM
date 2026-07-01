#!/usr/bin/env python3
"""Tests for P2-5: Pattern Renderer + Sandbox.

Tests the enhanced ``PatternRenderer``, ``PatternSandbox``, and related
API endpoints and workflow executor step types.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Add src to path
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from patterns.engine import PatternRegistry, OutputSchemaValidator
from patterns.renderer import PatternRenderer
from patterns.sandbox import PatternSandbox


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
# PatternRenderer Tests
# ═══════════════════════════════════════════════════════════════════


class TestPatternRenderer:
    """Tests for the enhanced ``PatternRenderer``."""

    def test_render_basic(self):
        """Render a pattern with context — basic smoke test."""
        reg = PatternRegistry()
        pat = make_test_pattern("basic-render")
        created = reg.register(pat)

        renderer = PatternRenderer(registry=reg)
        result = renderer.render(
            created["id"],
            context={"date": "2026-06-26", "workflow_name": "Daily Brief"},
            data={"signals": [{"title": "AI adoption accelerating"}]},
        )

        assert "rendered_prompt" in result
        assert "sections" in result
        assert "metadata" in result
        assert "version_used" in result

        assert len(result["sections"]) == 2
        assert result["metadata"]["pattern_id"] == created["id"]
        assert result["metadata"]["pattern_version"] == 1
        assert result["version_used"] == 1
        assert "2026-06-26" in result["rendered_prompt"]
        assert "Daily Brief" in result["rendered_prompt"]
        assert "architectural" in result["rendered_prompt"]

    def test_render_with_citations(self):
        """Inject citation data into rendered prompt."""
        reg = PatternRegistry()
        pat = make_test_pattern("citation-inject")
        created = reg.register(pat)

        renderer = PatternRenderer(registry=reg)
        result = renderer.render(
            created["id"],
            context={},
            data={
                "signals": [{"title": "Signal 1"}],
                "citations": [
                    {"id": "S001", "url": "https://example.com/1", "title": "Source 1"},
                    {"id": "S002", "url": "https://example.com/2", "title": "Source 2"},
                ],
            },
        )

        assert "S001" in result["rendered_prompt"]
        assert "S002" in result["rendered_prompt"]
        assert "Citation reference map" in result["rendered_prompt"]
        assert result["metadata"]["total_data_citations"] == 2

    def test_render_count_constraint(self):
        """Enforce 5-7 count constraint in rendered sections."""
        reg = PatternRegistry()
        pat = make_test_pattern("count-enforce", sections=[
            {"signals-section": {"count": "5-7"}},
        ])
        created = reg.register(pat)

        renderer = PatternRenderer(registry=reg)
        result = renderer.render(
            created["id"],
            context={},
            data={
                "signals": [
                    {"title": f"Signal {i}"} for i in range(3)  # Only 3 signals
                ],
            },
        )

        assert len(result["sections"]) == 1
        assert result["sections"][0]["constraint"] == "5-7"
        # Should have a warning about insufficient signals
        assert "WARNING" in result["sections"][0]["content"] or "warn" in result["sections"][0]["content"].lower()

        # Now test with enough signals
        result2 = renderer.render(
            created["id"],
            context={},
            data={
                "signals": [
                    {"title": f"Signal {i}"} for i in range(7)  # Exactly 7
                ],
            },
        )

        assert "WARNING" not in result2["sections"][0]["content"]
        assert "select the top" in result2["sections"][0]["content"].lower() or \
               "Target" in result2["sections"][0]["content"]

    def test_render_version_pinning(self):
        """Render with a specific version."""
        reg = PatternRegistry()
        pat = make_test_pattern("version-pin")
        created = reg.register(pat)

        # Update to version 2
        reg.update(created["id"], {"name": "version-pin-v2"})

        renderer = PatternRenderer(registry=reg)

        # Render latest (version 2)
        result_latest = renderer.render(created["id"], context={}, data={})
        assert result_latest["metadata"]["pattern_version"] == 2
        assert result_latest["version_used"] == 2
        assert result_latest["metadata"]["requested_version"] is None

        # Render specific version 1
        result_v1 = renderer.render(created["id"], context={}, data={}, version=1)
        assert result_v1["version_used"] == 1
        assert result_v1["metadata"]["requested_version"] == 1

        # Version 0 should fail
        with pytest.raises(ValueError, match="Invalid version"):
            renderer.render(created["id"], context={}, data={}, version=0)

        # Version 99 (beyond current) should fail
        with pytest.raises(ValueError, match="does not exist"):
            renderer.render(created["id"], context={}, data={}, version=99)

    def test_render_pattern_not_found(self):
        """Render with nonexistent pattern raises ValueError."""
        renderer = PatternRenderer()
        with pytest.raises(ValueError, match="not found"):
            renderer.render("nonexistent-pattern", {}, {})


# ═══════════════════════════════════════════════════════════════════
# PatternSandbox Tests
# ═══════════════════════════════════════════════════════════════════


class TestPatternSandbox:
    """Tests for the ``PatternSandbox``."""

    def test_sandbox_render_test(self):
        """Test data rendering through the sandbox."""
        reg = PatternRegistry()
        pat = make_test_pattern("sandbox-render")
        created = reg.register(pat)

        sandbox = PatternSandbox(registry=reg)
        result = sandbox.render_test(
            created["id"],
            test_data={"signals": [{"title": "Test signal"}]},
            context={"date": "2026-06-26"},
        )

        assert "rendered_prompt" in result
        assert "schema" in result
        assert "warnings" in result
        assert "expected_structure" in result
        assert "actual_structure" in result
        assert "metadata" in result
        assert "version_used" in result

        assert "architectural" in result["rendered_prompt"]
        assert "Test signal" in result["rendered_prompt"]
        assert len(result["expected_structure"]) >= 3  # At least subject, body, signals

    def test_sandbox_render_test_nonexistent(self):
        """Sandbox render with nonexistent pattern returns error dict."""
        sandbox = PatternSandbox()
        result = sandbox.render_test("nonexistent", test_data={}, context={})

        assert "error" in result
        assert "not found" in result["error"]

    def test_sandbox_validation(self):
        """Validate output through the sandbox."""
        reg = PatternRegistry()
        pat = make_test_pattern("sandbox-validate")
        created = reg.register(pat)

        sandbox = PatternSandbox(registry=reg)

        # Valid output
        valid_output = {
            "subject": "Test Subject",
            "body_markdown": "Body with citation [S001]",
            "key_signals": ["Signal 1"],
            "sources": [{"url": "https://example.com/1", "key": "S001"}],
            "citation_map": {"S001": "https://example.com/1"},
        }
        validation = sandbox.validate_sandbox_output(valid_output, created["id"])
        assert validation["valid"] is True
        assert validation["errors"] == []

        # Invalid output (missing required fields)
        invalid_output = {
            "subject": "Only subject",
            # missing body_markdown, key_signals, etc.
        }
        validation2 = sandbox.validate_sandbox_output(invalid_output, created["id"])
        assert validation2["valid"] is False
        assert len(validation2["errors"]) >= 1

    def test_sandbox_warnings(self):
        """Sandbox should produce warnings for constraint violations."""
        reg = PatternRegistry()
        pat = make_test_pattern("sandbox-warn", sections=[
            {"needs-many": {"count": "5-7"}},
        ])
        created = reg.register(pat)

        sandbox = PatternSandbox(registry=reg)
        result = sandbox.render_test(
            created["id"],
            test_data={"signals": [{"title": "Only one signal"}]},
        )

        # Should have a warning about insufficient signals
        assert len(result["warnings"]) > 0
        warning_text = " ".join(result["warnings"]).lower()
        assert "warn" in warning_text or "only" in warning_text


# ═══════════════════════════════════════════════════════════════════
# Version Publishing Tests
# ═══════════════════════════════════════════════════════════════════


class TestPatternPublishVersion:
    """Tests for version publishing via PatternRegistry."""

    def test_pattern_publish_version(self):
        """Publishing a new version works correctly."""
        reg = PatternRegistry()
        pat = make_test_pattern("publish-test")
        created = reg.register(pat)
        assert created["version"] == 1

        # Publish new version
        updated = reg.update(created["id"], {
            "name": "published-v2",
            "brand_voice": "updated voice",
        })

        assert updated["version"] == 2
        assert updated["name"] == "published-v2"

        # Verify config was updated
        config = updated.get("_config", {})
        assert config.get("brand_voice") == "updated voice"

        # Version 3
        updated2 = reg.update(created["id"], {
            "description": "Third version",
        })
        assert updated2["version"] == 3
        assert updated2["description"] == "Third version"


# ═══════════════════════════════════════════════════════════════════
# Executor Step Type Tests
# ═══════════════════════════════════════════════════════════════════


class TestRendererStepType:
    """Tests for the workflow executor step type routing."""

    def test_renderer_step_type(self):
        """Verify executor routes render_pattern_with_version correctly."""
        from workflow_executor import WorkflowExecutor

        # We test that the step type is recognized (not hitting unknown_step_type)
        # by checking the executor's dispatch logic handles it
        executor = WorkflowExecutor()

        # Test that the executor has the handler methods
        assert hasattr(executor, "_execute_render_pattern_with_version_step")
        assert hasattr(executor, "_execute_sandbox_verify_pattern_step")

        # Direct call to the handler should work
        reg = PatternRegistry()
        pat = make_test_pattern("executor-test")
        created = reg.register(pat)

        step = {
            "step_type": "render_pattern_with_version",
            "config_json": json.dumps({
                "pattern_id": created["id"],
                "context": {"date": "2026-06-26"},
                "data": {"signals": [{"title": "Test"}]},
            }),
        }

        result = executor._execute_render_pattern_with_version_step(step)
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        assert "rendered_prompt" in result
        assert result["version_used"] == 1
        assert "2026-06-26" in result["rendered_prompt"]

    def test_sandbox_step_type(self):
        """Verify executor routes sandbox_verify_pattern correctly."""
        from workflow_executor import WorkflowExecutor

        executor = WorkflowExecutor()
        reg = PatternRegistry()
        pat = make_test_pattern("sandbox-executor")
        created = reg.register(pat)

        step = {
            "step_type": "sandbox_verify_pattern",
            "config_json": json.dumps({
                "pattern_id": created["id"],
                "output": {
                    "subject": "Test",
                    "body_markdown": "Body [S001]",
                    "key_signals": ["Signal 1"],
                    "sources": [{"url": "https://example.com", "key": "S001"}],
                    "citation_map": {"S001": "https://example.com"},
                },
            }),
        }

        result = executor._execute_sandbox_verify_pattern_step(step)
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        assert "valid" in result
        assert result["valid"] is True

        # Test with invalid output
        step_invalid = {
            "step_type": "sandbox_verify_pattern",
            "config_json": json.dumps({
                "pattern_id": created["id"],
                "output": {"subject": "Only subject"},
            }),
        }

        result_invalid = executor._execute_sandbox_verify_pattern_step(step_invalid)
        assert result_invalid["valid"] is False
        assert len(result_invalid["errors"]) > 0

    def test_renderer_step_type_missing_pattern_id(self):
        """Executor handler returns error when pattern_id is missing."""
        from workflow_executor import WorkflowExecutor

        executor = WorkflowExecutor()
        step = {
            "step_type": "render_pattern_with_version",
            "config_json": "{}",
        }

        result = executor._execute_render_pattern_with_version_step(step)
        assert "error" in result
        assert "pattern_id required" in result["error"]
