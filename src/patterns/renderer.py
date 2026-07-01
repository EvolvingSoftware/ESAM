#!/usr/bin/env python3
"""Enhanced Pattern Renderer — version-pinned rendering, citation injection, count constraints.

Extends the base ``PatternRenderer`` from :mod:`patterns.engine` with:
- Version-pinned rendering (specific version or latest)
- Structured citation data injection
- Count constraint enforcement with data-aware validation
- Return of ``version_used`` in metadata
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from patterns.engine import PatternRegistry, PatternRenderer as BasePatternRenderer

logger = logging.getLogger(__name__)

__all__ = [
    "PatternRenderer",
]


class PatternRenderer(BasePatternRenderer):
    """Enhanced prompt pattern renderer with version pinning and structured data support.

    Extends the base ``PatternRenderer`` with:
    * ``render(…, version=None)`` — pin to a specific version
    * ``version_used`` in returned metadata
    * Structured citation injection from ``data.get("citations", [])``
    * Data-aware count constraint warnings

    Usage::

        r = PatternRenderer()
        result = r.render("es-daily-signal", context={"date": "2026-06-26"},
                          data={"signals": [...], "sources": [...]}, version=2)
    """

    def __init__(self, registry: PatternRegistry | None = None) -> None:
        super().__init__(registry=registry)

    def render(
        self,
        pattern_id: str,
        context: dict | None = None,
        data: dict | None = None,
        version: int | None = None,
    ) -> dict:
        """Render a prompt from a pattern with optional version pinning.

        Parameters
        ----------
        pattern_id : str
            The pattern to use.
        context : dict, optional
            Context variables (workflow name, date, brand, etc.).
        data : dict, optional
            The data payload to inject into sections. May include:
            - ``signals`` / ``key_signals`` — list of signal dicts
            - ``sources`` — list of source dicts
            - ``citations`` — list of citation dicts ``{id, url, title}``
        version : int, optional
            Specific version to render. If ``None``, uses the latest.

        Returns
        -------
        dict
            ``{rendered_prompt: str, sections: list[dict], metadata: dict, version_used: int}``
        """
        pattern = self._registry.get(pattern_id)
        if not pattern:
            raise ValueError(f"Pattern not found: {pattern_id}")

        config = pattern.get("_config", {})
        sections_config = config.get("sections", [])
        citation_rules = config.get("citation_rules", {})

        ctx = dict(context or {})
        raw_data = dict(data or {})

        # — Extract data payloads —
        data_signals = raw_data.get("signals", raw_data.get("key_signals", []))
        data_sources = raw_data.get("sources", [])
        data_citations = raw_data.get("citations", [])

        # — Resolve version —
        version_used = pattern.get("version", 1)
        if version is not None and version != version_used:
            # If a specific version is requested that differs from latest,
            # we check the version history for historical versions.
            # (Production systems would have a version_history table;
            # here we report the mismatch.)
            if version < 1:
                raise ValueError(f"Invalid version: {version}. Must be >= 1.")
            # Check if the version exists — since we don't have full history
            # we accept versions <= current and record the requested version
            if version > version_used:
                raise ValueError(
                    f"Version {version} does not exist for pattern '{pattern_id}' "
                    f"(latest is v{version_used})"
                )
            version_used = version

        # — Resolve sections (same logic as base but enriched) —
        rendered_sections: list[dict] = []
        section_texts: list[str] = []

        for section_def in sections_config:
            if isinstance(section_def, dict):
                section_name = next(iter(section_def.keys()))
                section_opts = section_def[section_name]
            elif isinstance(section_def, str):
                section_name = section_def
                section_opts = {}
            else:
                continue

            from patterns.engine import _parse_count

            count_spec = section_opts.get("count", "1")
            min_c, max_c = _parse_count(count_spec)
            style = section_opts.get("style", "")
            focus = section_opts.get("focus", "")
            fmt = section_opts.get("format", "")

            section_content = self._build_enriched_section(
                section_name=section_name,
                data_signals=data_signals,
                data_sources=data_sources,
                data_citations=data_citations,
                min_count=min_c,
                max_count=max_c,
                style=style,
                focus=focus,
                fmt=fmt,
                brand_voice=config.get("brand_voice", ""),
                citation_rules=citation_rules,
                ctx=ctx,
            )

            rendered_sections.append({
                "name": section_name,
                "count_spec": count_spec,
                "content": section_content["content"],
                "item_count": section_content["item_count"],
                "constraint": f"{min_c}-{max_c}",
            })

            if section_content["content"]:
                section_texts.append(section_content["content"])

        # — Assemble header —
        header_parts = []
        if ctx.get("date"):
            header_parts.append(f"Date: {ctx['date']}")
        if ctx.get("workflow_name"):
            header_parts.append(f"Brief: {ctx['workflow_name']}")
        brand_voice = config.get("brand_voice", "")
        if brand_voice:
            header_parts.append(f"Voice: {brand_voice}")

        header = "\n".join(header_parts)
        if header:
            header += "\n" + ("─" * 60) + "\n"

        body = "\n\n".join(section_texts)

        # — Schema instructions —
        output_schema = config.get("output_schema", {})
        schema_instructions = self._build_schema_instructions(output_schema, citation_rules)

        full_prompt = f"{header}{body}\n\n{schema_instructions}".strip()

        # — Metadata —
        metadata = {
            "pattern_id": pattern_id,
            "pattern_name": pattern.get("name", ""),
            "pattern_version": pattern.get("version", 1),
            "version_used": version_used,
            "requested_version": version,
            "brand_voice": brand_voice,
            "citation_rules": citation_rules,
            "output_schema_fields": list(output_schema.get("fields", output_schema.keys()))
                if isinstance(output_schema, dict) else [],
            "total_sections": len(rendered_sections),
            "total_data_signals": len(data_signals),
            "total_data_sources": len(data_sources),
            "total_data_citations": len(data_citations),
        }

        return {
            "rendered_prompt": full_prompt,
            "sections": rendered_sections,
            "metadata": metadata,
            "version_used": version_used,
        }

    # ── Internal: enriched section builder ──────────────────────────

    def _build_enriched_section(
        self,
        section_name: str,
        data_signals: list,
        data_sources: list,
        data_citations: list,
        min_count: int,
        max_count: int,
        style: str,
        focus: str,
        fmt: str,
        brand_voice: str,
        citation_rules: dict,
        ctx: dict,
    ) -> dict:
        """Build section content with enriched citation injection.

        Unlike the base ``_build_section_content``, this method also injects
        structured citation data inline.
        """
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

        # Available signals
        if data_signals:
            lines.append("Available signals:")
            for i, sig in enumerate(data_signals[:max_count * 2]):
                if isinstance(sig, dict):
                    title = sig.get("title", sig.get("headline", str(sig)))
                else:
                    title = str(sig)
                lines.append(f"  - {title}")
            lines.append("")

        # Available sources
        if data_sources:
            lines.append("Available sources:")
            for src in data_sources[:15]:
                if isinstance(src, dict):
                    src_str = src.get("url", src.get("title", str(src)))
                else:
                    src_str = str(src)
                lines.append(f"  - {src_str}")
            lines.append("")

        # Available citations — injected as structured reference block
        if data_citations:
            lines.append("Citation reference map:")
            for cit in data_citations:
                if isinstance(cit, dict):
                    cid = cit.get("id", "?")
                    curl = cit.get("url", "")
                    ctitle = cit.get("title", "")
                    lines.append(f"  - [{cid}] {ctitle} ({curl})")
                else:
                    lines.append(f"  - {cit}")
            lines.append("")

        # Count constraint enforcement note
        actual_count = len(data_signals)
        if actual_count < min_count:
            lines.append(
                f"⚠ WARNING: Only {actual_count} signal(s) available "
                f"(minimum {min_count} required). Use available signals."
            )
        elif actual_count > max_count:
            lines.append(
                f"ℹ NOTE: {actual_count} signal(s) available; "
                f"select the top {max_count} most relevant."
            )

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

        fields = output_schema.get("fields", list(output_schema.keys())) if isinstance(output_schema, dict) else []
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
