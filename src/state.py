"""Structured state management for workflow execution.

Steps can now reference any previous step's output by field name:
  Old: "Based on: {previous_output}, draft a letter"
  New: "Based on assessment: {{steps.step-assess.risk_score}}, draft level {{steps.step-assess.escalation_level}} letter"
"""

from __future__ import annotations

import json
import re
from typing import Any


class WorkflowState:
    """Structured state that flows through workflow steps."""

    def __init__(self, input_data: dict | None = None):
        self.input = input_data or {}
        self.steps: dict[str, dict] = {}  # {step_id: {output_text, structured: {...}, status: ...}}
        self.metadata: dict = {}
        self.errors: list[dict] = []

    def set_step_output(self, step_id: str, output_text: str, structured: dict | None = None):
        """Record a step's output. structured is the parsed JSON/structured version."""
        self.steps[step_id] = {
            "output_text": output_text,
            "structured": structured or {},
            "status": "completed",
        }

    def set_step_error(self, step_id: str, error: str):
        self.steps[step_id] = {"output_text": "", "structured": {}, "status": "error"}
        self.errors.append({"step_id": step_id, "error": error})

    def resolve(self, template: str) -> str:
        """Resolve a prompt template against the current state.

        Supports:
        - {field_name} — legacy, resolves from input or last step output
        - {{steps.STEP_ID.field}} — structured reference to any step's parsed output
        - {{steps.STEP_ID.output_text}} — raw text output of a step
        - {{input.field}} — input context fields
        """

        def _resolve_var(match):
            expr = match.group(1).strip()
            parts = expr.split(".")
            if parts[0] == "steps" and len(parts) >= 3:
                step_id = parts[1]
                field = ".".join(parts[2:])
                if step_id in self.steps:
                    if field == "output_text":
                        return self.steps[step_id].get("output_text", "")
                    step_data = self.steps[step_id].get("structured", {})
                    # Handle nested field access: steps.ASSESS.risk.score
                    val: Any = step_data
                    for f in parts[2:]:
                        if isinstance(val, dict):
                            val = val.get(f, match.group(0))
                        else:
                            return match.group(0)
                    return str(val) if not isinstance(val, (dict, list)) else json.dumps(val)
                return match.group(0)
            elif parts[0] == "input" and len(parts) >= 2:
                return str(self.input.get(parts[1], match.group(0)))
            else:
                # Legacy: try input, then last step output
                if expr in self.input:
                    return str(self.input[expr])
                if self.steps:
                    last_key = list(self.steps.keys())[-1]
                    return self.steps[last_key].get("output_text", match.group(0))
                return match.group(0)

        # Resolve {{ ... }} (double-brace) first
        result = re.sub(r"\{\{\s*([^}]+)\s*\}\}", _resolve_var, template)
        # Then resolve {field_name} (single-brace) — only word characters
        result = re.sub(r"\{(\w+)\}", _resolve_var, result)
        return result

    def to_dict(self) -> dict:
        return {"input": self.input, "steps": self.steps, "metadata": self.metadata, "errors": self.errors}

    @classmethod
    def from_dict(cls, data: dict) -> WorkflowState:
        state = cls(data.get("input", {}))
        state.steps = data.get("steps", {})
        state.metadata = data.get("metadata", {})
        state.errors = data.get("errors", [])
        return state

def _parse_structured_output(output_text: str) -> dict:
    """Try to parse LLM output as JSON. If the output is wrapped in
    markdown code blocks (```json ... ```), strip them first. If
    parsing fails, return {output_text: the_text}.

    Handles common LLM JSON quirks: trailing commas, invalid escapes
    like \', truncated JSON (missing closing brace/quote), escaped newlines
    that break the JSON parser.
    """
    text = output_text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :].strip()
        if text.endswith("```"):
            text = text[:-3].strip()

    # Strategy 1: strict parse
    try:
        if text.startswith("{"):
            return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: sanitize then retry
    # Strip invalid JSON escapes like \' but keep valid ones
    try:
        if text.startswith("{"):
            sanitized = _sanitize_json(text)
            return json.loads(sanitized)
    except json.JSONDecodeError:
        pass

    # Strategy 3: regex extraction of key-value pairs
    # Handles truncated JSON, mixed content, nested quotes
    result = {}
    try:
        if text.startswith("{"):
            # Simple regex: extract "key": "value" pairs
            pairs = re.findall(
                r'"([^"]+)"\s*:\s*"((?:[^"\\]|\\.)*)"',
                text,
            )
            for key, value in pairs:
                result[key] = value
            # Extract "key": "value (unterminated — truncated before closing quote)
            # This handles truncated JSON where the value string is cut off
            unterminated = re.findall(
                r'"([^"]+)"\s*:\s*"((?:[^"\\]|\\.)*)(?:"|$)',
                text,
            )
            for key, value in unterminated:
                if key not in result:
                    result[key] = value
            # Also extract "key": [array] pairs (one level only)
            array_pairs = re.findall(
                r'"([^"]+)"\s*:\s*\[([^\]]+)\]',
                text,
            )
            for key, value in array_pairs:
                try:
                    result[key] = json.loads("[" + value + "]")
                except json.JSONDecodeError:
                    result[key] = [v.strip().strip("\"'") for v in value.split(",")]
            # Extract "key": number (integer/float)
            num_pairs = re.findall(
                r'"([^"]+)"\s*:\s*(\d+\.?\d*)',
                text,
            )
            for key, value in num_pairs:
                try:
                    result[key] = int(value) if "." not in value else float(value)
                except ValueError:
                    pass
            # Extract "key": true/false
            bool_pairs = re.findall(
                r'"([^"]+)"\s*:\s*(true|false)',
                text,
                re.IGNORECASE,
            )
            for key, value in bool_pairs:
                result[key] = value.lower() == "true"
        if result:
            return result
    except Exception:
        pass

    return {"output_text": output_text}


def _sanitize_json(text: str) -> str:
    """Sanitize common LLM JSON issues: remove invalid escapes,
    handle truncated content."""
    # Strip invalid escapes: \' \_ \` etc. but keep \", \\, \/, \b, \f, \n, \r, \t, \uXXXX
    sanitized = re.sub(r'\\([^\"/\\bfnrtu])', r'\1', text)
    # Handle truncated JSON by completing it
    stripped = sanitized.rstrip()
    if not stripped.endswith("}"):
        # Count quotes — if odd, add closing quote
        if stripped.count('"') % 2 != 0:
            stripped += '"'
        if not stripped.endswith("}"):
            stripped += "\n}"
    return stripped
