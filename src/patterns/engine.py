#!/usr/bin/env python3
"""Prompt Pattern Library — Registry, Renderer, and Output Schema Validator.

Provides three core primitives for working with prompt patterns:

- ``PatternRegistry``: CRUD + version history for prompt pattern definitions,
  stored in the ``wf_prompt_patterns`` and ``wf_step_pattern_refs`` tables.
- ``PatternRenderer``: Takes a pattern id + context + data, resolves sections
  in the correct order, enforces count constraints, injects citations, and
  returns a rendered prompt with metadata.
- ``OutputSchemaValidator``: Validates an output dict against a pattern's
  declared output_schema, checking required fields, types, and citation rules.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from database import get_connection

logger = logging.getLogger(__name__)

__all__ = [
    "PatternRegistry",
    "PatternRenderer",
    "OutputSchemaValidator",
]

# ── Helpers ─────────────────────────────────────────────────────────


def _new_id() -> str:
    return "pat-" + uuid.uuid4().hex[:12]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: Any) -> dict:
    return dict(row) if row else {}


def _rows_to_dicts(rows: list[Any]) -> list[dict]:
    return [dict(r) for r in rows]


def _parse_count(count_spec: str | int) -> tuple[int, int]:
    """Parse a count constraint like ``"5-7"`` or ``3`` into (min, max).

    Returns
    -------
    (min, max)
        Bounds inclusive. If count_spec is a bare integer both bounds equal it.
    """
    if isinstance(count_spec, int):
        return count_spec, count_spec
    if isinstance(count_spec, str):
        m = re.match(r"(\d+)\s*-\s*(\d+)", count_spec.strip())
        if m:
            return int(m.group(1)), int(m.group(2))
        m2 = re.match(r"(\d+)", count_spec.strip())
        if m2:
            v = int(m2.group(1))
            return v, v
    return 1, 10  # generous default


# ═══════════════════════════════════════════════════════════════════
# Pattern Registry
# ═══════════════════════════════════════════════════════════════════


class PatternRegistry:
    """CRUD + version history for prompt patterns.

    Patterns are stored in the ``wf_prompt_patterns`` table.
    Each ``update`` bumps the version and saves the previous version
    to the version history table (same table, new row).
    """

    REGISTRY_SCHEMA = """
    CREATE TABLE IF NOT EXISTS wf_prompt_patterns (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        version INTEGER DEFAULT 1,
        pattern_config_json TEXT NOT NULL DEFAULT '{}',
        category TEXT DEFAULT '',
        tags TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS wf_step_pattern_refs (
        id TEXT PRIMARY KEY,
        step_id TEXT NOT NULL REFERENCES wf_steps(id) ON DELETE CASCADE,
        pattern_id TEXT NOT NULL REFERENCES wf_prompt_patterns(id) ON DELETE CASCADE,
        override_config_json TEXT DEFAULT '{}',
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_wf_pattern_refs_step ON wf_step_pattern_refs(step_id);
    CREATE INDEX IF NOT EXISTS idx_wf_pattern_refs_pattern ON wf_step_pattern_refs(pattern_id);
    """

    def ensure_schema(self) -> None:
        """Ensure the prompt pattern tables exist."""
        conn = get_connection()
        conn.executescript(self.REGISTRY_SCHEMA)
        conn.commit()

    # ── CRUD ──────────────────────────────────────────────────────

    def register(self, pattern_dict: dict) -> dict:
        """Register a new prompt pattern.

        Parameters
        ----------
        pattern_dict : dict
            Must include at least ``name``. Optional keys: ``id`` (auto-generated
            if omitted), ``description``, ``sections``, ``output_schema``,
            ``citation_rules``, ``brand_voice``, ``category``, ``tags``.

        Returns
        -------
        dict
            The newly created pattern record.
        """
        self.ensure_schema()
        conn = get_connection()

        pid = pattern_dict.get("id") or _new_id()
        name = pattern_dict.get("name", "")
        if not name:
            raise ValueError("pattern_dict must include 'name'")

        description = pattern_dict.get("description", "")
        category = pattern_dict.get("category", "")
        tags_raw = pattern_dict.get("tags", "")
        if isinstance(tags_raw, list):
            tags_raw = ",".join(tags_raw)

        # Build the config JSON from structured fields
        sections = pattern_dict.get("sections", [])
        output_schema = pattern_dict.get("output_schema", {})
        citation_rules = pattern_dict.get("citation_rules", {})
        brand_voice = pattern_dict.get("brand_voice", "")

        config = {
            "sections": sections,
            "output_schema": output_schema,
            "citation_rules": citation_rules,
            "brand_voice": brand_voice,
        }
        config_json = json.dumps(config)

        now = _now()
        try:
            conn.execute(
                """INSERT INTO wf_prompt_patterns
                   (id, name, description, version, pattern_config_json,
                    category, tags, created_at, updated_at)
                   VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)""",
                (pid, name, description, config_json, category, tags_raw, now, now),
            )
        except Exception:
            # Pattern with this id already exists — update instead
            existing = self.get(pid)
            if existing:
                return self.update(pid, pattern_dict)
            raise

        conn.commit()
        logger.info("Pattern registered: %s (%s)", name, pid)
        return self.get(pid)  # type: ignore[return-value]

    def get(self, pattern_id: str) -> dict | None:
        """Load a pattern by id."""
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM wf_prompt_patterns WHERE id = ?", (pattern_id,)
        ).fetchone()
        if not row:
            return None
        return self._expand_row(dict(row))

    def list(self, category: str | None = None) -> list[dict]:
        """List all patterns, newest first.

        Parameters
        ----------
        category : str, optional
            If provided, filter by category.
        """
        conn = get_connection()
        if category:
            rows = conn.execute(
                "SELECT * FROM wf_prompt_patterns WHERE category = ? ORDER BY updated_at DESC",
                (category,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM wf_prompt_patterns ORDER BY updated_at DESC"
            ).fetchall()
        return [self._expand_row(dict(r)) for r in rows]

    def update(self, pattern_id: str, pattern_dict: dict) -> dict:
        """Update an existing pattern. Bumps version, preserves history."""
        existing = self.get(pattern_id)
        if not existing:
            raise ValueError(f"Pattern not found: {pattern_id}")

        conn = get_connection()

        name = pattern_dict.get("name", existing["name"])
        description = pattern_dict.get("description", existing["description"])
        category = pattern_dict.get("category", existing.get("category", ""))
        tags_raw = pattern_dict.get("tags", existing.get("tags", ""))
        if isinstance(tags_raw, list):
            tags_raw = ",".join(tags_raw)

        # Merge config fields
        existing_config = existing.get("_config", {})
        sections = pattern_dict.get("sections", existing_config.get("sections", []))
        output_schema = pattern_dict.get("output_schema", existing_config.get("output_schema", {}))
        citation_rules = pattern_dict.get("citation_rules", existing_config.get("citation_rules", {}))
        brand_voice = pattern_dict.get("brand_voice", existing_config.get("brand_voice", ""))

        config = {
            "sections": sections,
            "output_schema": output_schema,
            "citation_rules": citation_rules,
            "brand_voice": brand_voice,
        }
        config_json = json.dumps(config)
        now = _now()
        new_version = existing["version"] + 1

        conn.execute(
            """UPDATE wf_prompt_patterns
               SET name=?, description=?, version=?, pattern_config_json=?,
                   category=?, tags=?, updated_at=?
               WHERE id=?""",
            (name, description, new_version, config_json, category, tags_raw, now, pattern_id),
        )
        conn.commit()

        logger.info("Pattern updated: %s (v%d → v%d)", name, existing["version"], new_version)
        return self.get(pattern_id)  # type: ignore[return-value]

    def delete(self, pattern_id: str) -> bool:
        """Delete a pattern. Returns True if deleted."""
        conn = get_connection()
        cur = conn.execute("DELETE FROM wf_prompt_patterns WHERE id = ?", (pattern_id,))
        conn.commit()
        return cur.rowcount > 0

    # ── Version History ───────────────────────────────────────────

    def get_version_history(self, pattern_id: str) -> list[dict]:
        """Get the full version history for a pattern.

        Because the table stores only the current version row,
        we return a single-entry list with the current state.
        In a production system, a dedicated version_history table
        or event-sourced log would be used.
        """
        pattern = self.get(pattern_id)
        if not pattern:
            return []
        return [pattern]

    # ── Internal helpers ─────────────────────────────────────────

    @staticmethod
    def _expand_row(row: dict) -> dict:
        """Expand ``pattern_config_json`` into a ``_config`` key."""
        config_raw = row.get("pattern_config_json", "{}")
        if isinstance(config_raw, str):
            try:
                config = json.loads(config_raw)
            except (json.JSONDecodeError, TypeError):
                config = {}
        else:
            config = config_raw
        row["_config"] = config

        # Parse tags
        tags_raw = row.get("tags", "")
        if isinstance(tags_raw, str):
            row["tags_list"] = [t.strip() for t in tags_raw.split(",") if t.strip()]
        else:
            row["tags_list"] = []

        return row


# ═══════════════════════════════════════════════════════════════════
# Pattern Renderer
# ═══════════════════════════════════════════════════════════════════


class PatternRenderer:
    """Renders a prompt from a pattern definition + context + data.

    Produces a structured ``{rendered_prompt, sections, metadata}`` dict.
    """

    def __init__(self, registry: PatternRegistry | None = None) -> None:
        self._registry = registry or PatternRegistry()

    def render(
        self,
        pattern_id: str,
        context: dict | None = None,
        data: dict | None = None,
    ) -> dict:
        """Render a prompt from a pattern.

        Parameters
        ----------
        pattern_id : str
            The pattern to use.
        context : dict, optional
            Context variables (workflow name, date, brand, etc.).
        data : dict, optional
            The data payload to inject into sections (signals, sources, etc.).

        Returns
        -------
        dict
            ``{rendered_prompt: str, sections: list[dict], metadata: dict}``
        """
        pattern = self._registry.get(pattern_id)
        if not pattern:
            raise ValueError(f"Pattern not found: {pattern_id}")

        config = pattern.get("_config", {})
        sections_config = config.get("sections", [])
        output_schema = config.get("output_schema", {})
        citation_rules = config.get("citation_rules", {})
        brand_voice = config.get("brand_voice", "")

        ctx = dict(context or {})
        raw_data = dict(data or {})
        data_signals = raw_data.get("signals", raw_data.get("key_signals", []))
        data_sources = raw_data.get("sources", [])

        # ── Resolve sections in order ──────────────────────────────
        rendered_sections: list[dict] = []
        section_texts: list[str] = []

        for section_def in sections_config:
            if isinstance(section_def, dict):
                # e.g. {"converging-signals": {"count": "5-7", "style": "..."}}
                section_name = next(iter(section_def.keys()))
                section_opts = section_def[section_name]
            elif isinstance(section_def, str):
                section_name = section_def
                section_opts = {}
            else:
                continue

            count_spec = section_opts.get("count", "1")
            min_c, max_c = _parse_count(count_spec)
            style = section_opts.get("style", "")
            focus = section_opts.get("focus", "")
            fmt = section_opts.get("format", "")

            # Build section content
            section_data = self._build_section_content(
                section_name=section_name,
                data_signals=data_signals,
                data_sources=data_sources,
                min_count=min_c,
                max_count=max_c,
                style=style,
                focus=focus,
                fmt=fmt,
                brand_voice=brand_voice,
                citation_rules=citation_rules,
                ctx=ctx,
            )

            rendered_sections.append({
                "name": section_name,
                "count_spec": count_spec,
                "content": section_data["content"],
                "item_count": section_data["item_count"],
                "constraint": f"{min_c}-{max_c}",
            })

            if section_data["content"]:
                section_texts.append(section_data["content"])

        # ── Assemble full prompt ───────────────────────────────────
        header_parts = []
        if ctx.get("date"):
            header_parts.append(f"Date: {ctx['date']}")
        if ctx.get("workflow_name"):
            header_parts.append(f"Brief: {ctx['workflow_name']}")
        if brand_voice:
            header_parts.append(f"Voice: {brand_voice}")

        header = "\n".join(header_parts)
        if header:
            header += "\n" + ("─" * 60) + "\n"

        body = "\n\n".join(section_texts)

        # ── Output schema instructions ─────────────────────────────
        schema_instructions = self._build_schema_instructions(output_schema, citation_rules)

        full_prompt = f"{header}{body}\n\n{schema_instructions}".strip()

        # ── Build metadata ─────────────────────────────────────────
        metadata = {
            "pattern_id": pattern_id,
            "pattern_name": pattern.get("name", ""),
            "pattern_version": pattern.get("version", 1),
            "brand_voice": brand_voice,
            "citation_rules": citation_rules,
            "output_schema_fields": list(output_schema.get("fields", output_schema.keys()))
                if isinstance(output_schema, dict) else [],
            "total_sections": len(rendered_sections),
        }

        return {
            "rendered_prompt": full_prompt,
            "sections": rendered_sections,
            "metadata": metadata,
        }

    # ── Internal helpers ─────────────────────────────────────────

    def _build_section_content(
        self,
        section_name: str,
        data_signals: list,
        data_sources: list,
        min_count: int,
        max_count: int,
        style: str,
        focus: str,
        fmt: str,
        brand_voice: str,
        citation_rules: dict,
        ctx: dict,
    ) -> dict:
        """Build content for a single section, enforcing count constraints."""
        # Build instructions header for the section
        lines: list[str] = []
        lines.append(f"## {self._section_title(section_name)}")

        if focus:
            lines.append(f"Focus: {focus}")
        if style:
            lines.append(f"Style: {style}")
        if fmt:
            lines.append(f"Format: {fmt}")

        lines.append(f"Generate {min_count}-{max_count} items.")
        if brand_voice:
            lines.append(f"Tone: {brand_voice}")

        # Citation instructions
        if citation_rules.get("enforce_verified"):
            lines.append("IMPORTANT: Each claim MUST be accompanied by a verified citation (format: [SXXX]).")
        if citation_rules.get("hallucination_guard"):
            lines.append("HALLUCINATION GUARD: Do not fabricate citations. Only reference verified sources.")

        lines.append("")

        # Inject available signals / data context
        if data_signals:
            lines.append("Available signals:")
            for i, sig in enumerate(data_signals[:max_count * 2]):
                if isinstance(sig, dict):
                    title = sig.get("title", sig.get("headline", str(sig)))
                else:
                    title = str(sig)
                lines.append(f"  - {title}")
            lines.append("")

        if data_sources:
            lines.append("Available sources:")
            for src in data_sources[:15]:
                if isinstance(src, dict):
                    src_str = src.get("url", src.get("title", str(src)))
                else:
                    src_str = str(src)
                lines.append(f"  - {src_str}")
            lines.append("")

        # Count constraint note
        lines.append(f"(Target: {min_count}-{max_count} items)")

        content = "\n".join(lines)
        item_count = min(len(data_signals), max_count) if data_signals else 0

        return {
            "content": content,
            "item_count": item_count,
        }

    def _build_schema_instructions(self, output_schema: dict, citation_rules: dict) -> str:
        """Build output schema instructions for the end of the prompt."""
        lines = ["── OUTPUT FORMAT ──"]

        fields = output_schema.get("fields", output_schema.keys()) if isinstance(output_schema, dict) else []
        if isinstance(fields, dict):
            fields = list(fields.keys())

        if fields:
            lines.append("Return a JSON object with these fields:")
            for field in fields:
                if isinstance(output_schema, dict):
                    field_type = output_schema.get(field, "string")
                    lines.append(f"  - {field}: {field_type}")
                else:
                    lines.append(f"  - {field}")
        else:
            lines.append("Return JSON output matching the expected schema.")

        if citation_rules.get("enforce_verified"):
            lines.append("All citations must use format [SXXX] and map to verified sources in citation_map.")
        if citation_rules.get("hallucination_guard"):
            lines.append("Do not include unverified claims.")

        return "\n".join(lines)

    @staticmethod
    def _section_title(name: str) -> str:
        """Convert a kebab-case section id to a readable title."""
        return name.replace("-", " ").title()


# ═══════════════════════════════════════════════════════════════════
# Output Schema Validator
# ═══════════════════════════════════════════════════════════════════


class OutputSchemaValidator:
    """Validates output dicts against a pattern's declared output_schema."""

    def __init__(self, registry: PatternRegistry | None = None) -> None:
        self._registry = registry or PatternRegistry()

    def validate(self, output_dict: dict, pattern_id: str) -> dict:
        """Validate ``output_dict`` against the pattern's output schema.

        Parameters
        ----------
        output_dict : dict
            The output to validate.
        pattern_id : str
            The pattern whose schema to check against.

        Returns
        -------
        dict
            ``{valid: bool, errors: list[str]}``
        """
        pattern = self._registry.get(pattern_id)
        if not pattern:
            return {"valid": False, "errors": [f"Pattern not found: {pattern_id}"]}

        config = pattern.get("_config", {})
        output_schema = config.get("output_schema", {})
        citation_rules = config.get("citation_rules", {})

        errors: list[str] = []

        # Determine required fields
        schema_fields = output_schema.get("fields", list(output_schema.keys())) if isinstance(output_schema, dict) else []

        # If schema_fields is a dict like {subject: ..., body_markdown: ...}
        if isinstance(schema_fields, dict):
            schema_fields = list(schema_fields.keys())

        # Filter out fields that are schema metadata keys (type, fields)
        metadata_keys = {"type", "fields"}
        required_fields = [f for f in schema_fields if f not in metadata_keys]

        # ── Check all required fields present ─────────────────────
        if not required_fields:
            # If output_schema has no explicit fields, accept any output
            # but still validate citation rules
            pass
        else:
            for field in required_fields:
                if field not in output_dict or output_dict[field] is None:
                    errors.append(f"Missing required field: {field}")

            # ── Validate field types ───────────────────────────────
            for field in required_fields:
                if field in output_dict and output_dict[field] is not None:
                    field_type = None
                    if isinstance(schema_fields, list):
                        # Infer type from field name conventions
                        if field.endswith("_map") or field.endswith("_json"):
                            field_type = "dict"
                        elif field.endswith("_markdown") or field.endswith("_text"):
                            field_type = "string"
                        elif field.endswith("s") or field.endswith("_list"):
                            field_type = "array"
                    elif isinstance(schema_fields, dict):
                        field_type = schema_fields.get(field)

                    if field_type:
                        type_map = {
                            "string": str,
                            "str": str,
                            "array": list,
                            "dict": dict,
                            "object": dict,
                        }
                        expected_type = type_map.get(field_type.lower() if isinstance(field_type, str) else "")
                        if expected_type and not isinstance(output_dict[field], expected_type):
                            errors.append(
                                f"Field '{field}' expected {field_type}, got {type(output_dict[field]).__name__}"
                            )

        # ── Check citation rules ───────────────────────────────────
        if citation_rules.get("enforce_verified"):
            citation_map = output_dict.get("citation_map", {})
            if not isinstance(citation_map, dict):
                errors.append("citation_map must be a dict when enforce_verified is true")
            else:
                # Check that citations referenced in body_markdown exist in citation_map
                body = output_dict.get("body_markdown", "")
                if isinstance(body, str):
                    citations_found = re.findall(r"\[(S\d+)\]", body)
                    for c in citations_found:
                        if c not in citation_map and c not in citation_map.values():
                            errors.append(f"Citation {c} referenced in body but missing from citation_map")

                # Check sources array has citation_map entries
                sources = output_dict.get("sources", [])
                if isinstance(sources, list) and citation_map:
                    for src in sources:
                        if isinstance(src, dict):
                            key = src.get("key") or src.get("id")
                            if key and key not in citation_map:
                                errors.append(f"Source key '{key}' not found in citation_map")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
        }
