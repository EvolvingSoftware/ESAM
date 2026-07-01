#!/usr/bin/env python3
"""Pattern Sandbox — interactive pattern rendering and output validation.

Provides:
- ``PatternSandbox.render_test()`` — Renders a template with test data,
  returning the rendered prompt, expected schema, and warnings.
- ``PatternSandbox.validate_sandbox_output()`` — Validates output dict
  against the pattern's schema using ``OutputSchemaValidator``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from patterns.engine import PatternRegistry, OutputSchemaValidator
from patterns.renderer import PatternRenderer

logger = logging.getLogger(__name__)

__all__ = [
    "PatternSandbox",
]


class PatternSandbox:
    """Interactive sandbox for testing prompt pattern rendering and validation.

    Usage::

        sb = PatternSandbox()
        result = sb.render_test("es-daily-signal", {"signals": [...]}, {"date": "..."})
        # result = {rendered_prompt, sections, schema, warnings, expected_structure, actual_structure}

        validation = sb.validate_sandbox_output({"subject": "..."}, "es-daily-signal")
        # validation = {valid, errors, schema, output_fields}
    """

    def __init__(
        self,
        registry: PatternRegistry | None = None,
        renderer: PatternRenderer | None = None,
        validator: OutputSchemaValidator | None = None,
    ) -> None:
        self._registry = registry or PatternRegistry()
        self._renderer = renderer or PatternRenderer(registry=self._registry)
        self._validator = validator or OutputSchemaValidator(registry=self._registry)

    # ── Public API ─────────────────────────────────────────────────

    def render_test(
        self,
        pattern_id: str,
        test_data: dict | None = None,
        context: dict | None = None,
    ) -> dict:
        """Render a pattern with test data, returning schema and warnings.

        Parameters
        ----------
        pattern_id : str
            The pattern to test.
        test_data : dict, optional
            Test data payload (signals, sources, citations, etc.).
        context : dict, optional
            Context variables (date, workflow name, etc.).

        Returns
        -------
        dict
            ``{
                rendered_prompt: str,
                sections: list[dict],
                schema: dict,
                warnings: list[str],
                expected_structure: dict,
                actual_structure: dict,
                metadata: dict,
                version_used: int,
            }``
        """
        pattern = self._registry.get(pattern_id)
        if not pattern:
            return {
                "error": f"Pattern not found: {pattern_id}",
                "rendered_prompt": "",
                "sections": [],
                "schema": {},
                "warnings": [f"Pattern '{pattern_id}' not found in registry."],
                "expected_structure": {},
                "actual_structure": {},
                "metadata": {},
                "version_used": 0,
            }

        config = pattern.get("_config", {})
        output_schema = config.get("output_schema", {})
        citation_rules = config.get("citation_rules", {})

        # Render using the enhanced renderer
        try:
            render_result = self._renderer.render(
                pattern_id,
                context=context or {},
                data=test_data or {},
            )
        except ValueError as exc:
            return {
                "error": str(exc),
                "rendered_prompt": "",
                "sections": [],
                "schema": output_schema,
                "warnings": [str(exc)],
                "expected_structure": {},
                "actual_structure": {},
                "metadata": {},
                "version_used": 0,
            }

        # Collect warnings from rendering
        warnings: list[str] = []
        for sec in render_result.get("sections", []):
            content = sec.get("content", "")
            if "WARNING" in content or "NOTE" in content:
                # Extract inline warnings
                for line in content.split("\n"):
                    stripped = line.strip()
                    if stripped.startswith("⚠") or stripped.startswith("ℹ") or "WARNING" in stripped:
                        warnings.append(stripped)

        # Build expected structure from output schema
        expected_structure = self._build_expected_structure(output_schema)

        # Build actual structure from test data keys
        actual_structure = self._build_actual_structure(test_data or {})

        # Schema expectations
        schema_info = {
            "fields": self._get_schema_fields(output_schema),
            "citation_rules": citation_rules,
            "brand_voice": config.get("brand_voice", ""),
            "section_count": len(config.get("sections", [])),
        }

        return {
            "rendered_prompt": render_result.get("rendered_prompt", ""),
            "sections": render_result.get("sections", []),
            "schema": schema_info,
            "warnings": warnings,
            "expected_structure": expected_structure,
            "actual_structure": actual_structure,
            "metadata": render_result.get("metadata", {}),
            "version_used": render_result.get("version_used", 0),
        }

    def validate_sandbox_output(self, output: dict, pattern_id: str) -> dict:
        """Validate an output dict against a pattern's schema.

        Uses ``OutputSchemaValidator`` internally and enriches the result
        with schema metadata.

        Parameters
        ----------
        output : dict
            The output to validate (e.g. the LLM's JSON response).
        pattern_id : str
            The pattern whose schema to check against.

        Returns
        -------
        dict
            ``{valid: bool, errors: list[str], schema: dict, output_fields: list[str]}``
        """
        pattern = self._registry.get(pattern_id)
        config = pattern.get("_config", {}) if pattern else {}
        output_schema = config.get("output_schema", {})

        validation = self._validator.validate(output, pattern_id)

        return {
            "valid": validation.get("valid", False),
            "errors": validation.get("errors", []),
            "schema": {
                "fields": self._get_schema_fields(output_schema),
                "citation_rules": config.get("citation_rules", {}),
            },
            "output_fields": list(output.keys()),
        }

    # ── Internal helpers ───────────────────────────────────────────

    @staticmethod
    def _get_schema_fields(output_schema: dict) -> list:
        """Extract the field list from an output_schema dict."""
        fields = output_schema.get("fields", list(output_schema.keys())) if isinstance(output_schema, dict) else []
        if isinstance(fields, dict):
            fields = list(fields.keys())
        # Filter out metadata keys
        metadata_keys = {"type", "fields"}
        return [f for f in fields if f not in metadata_keys]

    @staticmethod
    def _build_expected_structure(output_schema: dict) -> dict:
        """Build an expected structure dict from the output schema."""
        expected = {}
        fields = output_schema.get("fields", list(output_schema.keys())) if isinstance(output_schema, dict) else []
        if isinstance(fields, dict):
            for key, typ in fields.items():
                if key not in ("type", "fields"):
                    expected[key] = {"type": typ, "required": True}
        elif isinstance(fields, list):
            for field in fields:
                if field not in ("type", "fields"):
                    expected[field] = {"type": "any", "required": True}
        return expected

    @staticmethod
    def _build_actual_structure(test_data: dict) -> dict:
        """Build an actual structure dict from test data keys."""
        actual = {}
        for key, value in test_data.items():
            actual[key] = {
                "type": type(value).__name__,
                "sample": str(value)[:80] if not isinstance(value, (list, dict)) else _sample_structure(value),
            }
        return actual


def _sample_structure(value: Any) -> str:
    """Create a brief sample from a list or dict value."""
    if isinstance(value, list):
        if not value:
            return "[]"
        items = value[:3]
        return f"[{', '.join(str(i)[:40] for i in items)}{'...' if len(value) > 3 else ''}]"
    if isinstance(value, dict):
        keys = list(value.keys())[:5]
        return "{" + ", ".join(f"{k}: ..." for k in keys) + ("..." if len(value) > 5 else "") + "}"
    return str(value)[:80]
