"""Template variable renderer for workflow steps.

Resolves ``{{variable}}`` expressions in template strings, supporting:

- ``{{input.field_name}}`` — resolves from ``input_vars`` dict
- ``{{steps.step_id.field_name}}`` — resolves from ``step_results`` dict
- ``{{input.field_name | default: "fallback"}}`` — provides a default
- ``{{steps.step_id.field_name | join: ", "}}`` — joins list elements

If a variable cannot be resolved and no default is provided, the original
``{{...}}`` expression is left verbatim in the output.
"""

from __future__ import annotations

import json
import re
from typing import Any

_UNSET: Any = object()  # Sentinel for "not found" — distinct from None


# ──────────────────────────────────────────────────────────────────────────
#  Path resolution
# ──────────────────────────────────────────────────────────────────────────


def _resolve_path(source: Any, path: list[str]) -> Any:
    """Traverse *source* (dict or list) using dotted *path* parts.

    Supports both dict key access (``"name"``) and list index access
    (``"0"``, ``"1"``, …).  Returns :data:`_UNSET` if any part of the
    path does not exist.
    """
    current = source
    for part in path:
        if isinstance(current, dict):
            if part in current:
                current = current[part]
            else:
                return _UNSET
        elif isinstance(current, (list, tuple)):
            try:
                idx = int(part)
            except (ValueError, TypeError):
                return _UNSET
            if 0 <= idx < len(current):
                current = current[idx]
            else:
                return _UNSET
        else:
            return _UNSET
    return current


# ──────────────────────────────────────────────────────────────────────────
#  Expression parsing
# ──────────────────────────────────────────────────────────────────────────


def _parse_filters(expr: str) -> tuple[str, list[tuple[str, str | None]]]:
    """Split *expr* into a value expression and a list of ``(name, arg)``
    filter tuples.

    Example::

        >>> _parse_filters("input.recipient | default: fallback@e.s")
        ('input.recipient', [('default', 'fallback@e.s')])

        >>> _parse_filters("steps.step-x.items | join: ', '")
        ('steps.step-x.items', [('join', ", ")])

        >>> _parse_filters("input.topic")
        ('input.topic', [])
    """
    filters: list[tuple[str, str | None]] = []
    value_expr = expr

    parts = expr.split("|")
    value_expr = parts[0].strip()

    for spec in parts[1:]:
        spec = spec.strip()
        if ":" in spec:
            name, arg = spec.split(":", 1)
            arg = arg.strip()
            # Strip surrounding quotes from argument
            if len(arg) >= 2 and arg[0] in ("'", '"') and arg[0] == arg[-1]:
                arg = arg[1:-1]
            filters.append((name.strip(), arg))
        else:
            filters.append((spec, None))

    return value_expr, filters


def _resolve_value_expr(
    value_expr: str,
    input_vars: dict[str, Any],
    step_results: dict[str, dict[str, Any]],
) -> Any:
    """Resolve a value expression (e.g. ``input.topic`` or
    ``steps.step-id.field``) against the provided dicts.

    Returns the resolved value or :data:`_UNSET` if the path does not
    exist.
    """
    parts = value_expr.split(".")
    prefix = parts[0] if parts else ""

    if prefix == "input" and len(parts) >= 2:
        return _resolve_path(input_vars, parts[1:])

    if prefix == "steps" and len(parts) >= 3:
        step_id = parts[1]
        step_data: Any = step_results.get(step_id, {})
        return _resolve_path(step_data, parts[2:])

    # Fall back: look up arbitrary keys in input_vars
    # e.g. {{context.articles}} resolves input_vars["context"]["articles"]
    if len(parts) >= 1:
        return _resolve_path(input_vars, parts)

    return _UNSET


# ──────────────────────────────────────────────────────────────────────────
#  Filter application
# ──────────────────────────────────────────────────────────────────────────


def _apply_filters(
    value: Any,
    filters: list[tuple[str, str | None]],
) -> Any:
    """Apply post-resolution filters to *value*.

    Supported filters:

    * ``default: <arg>`` — if *value* is :data:`_UNSET`, ``None``, or
      an empty string, return *arg* instead.
    * ``join: <sep>`` — if *value* is a list/tuple, join its string
      representations with *sep*.

    Returns the (possibly-modified) value.
    """
    result = value

    for name, arg in filters:
        if name == "default":
            if result is _UNSET or result is None or result == "":
                # If the default arg itself is quoted, strip was done earlier
                result = arg if arg is not None else ""
        elif name == "join":
            if isinstance(result, (list, tuple)):
                sep = arg if arg is not None else ", "
                result = sep.join(str(item) for item in result)

    return result


# ──────────────────────────────────────────────────────────────────────────
#  Type coercion
# ──────────────────────────────────────────────────────────────────────────


def _coerce_to_string(value: Any) -> str:
    """Convert *value* to a string.

    * ``None`` → ``""``
    * ``str`` → unchanged
    * ``list``, ``dict`` → JSON
    * Everything else → ``str(value)``
    """
    if value is None or value is _UNSET:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)


# ──────────────────────────────────────────────────────────────────────────
#  Public API
# ──────────────────────────────────────────────────────────────────────────


def render_template(
    template: str,
    input_vars: dict[str, Any],
    step_results: dict[str, dict[str, Any]],
) -> str:
    """Resolve ``{{variable}}`` expressions in *template*.

    Parameters
    ----------
    template:
        The template string containing ``{{variable}}`` expressions.
    input_vars:
        Workflow input variables.  Accessed via ``{{input.field_name}}``.
    step_results:
        Previous step outputs, keyed by step ID.  Accessed via
        ``{{steps.step_id.field_name}}``.

    Returns
    -------
    str
        The template string with all resolvable variables replaced.
        Unresolvable variables (without a ``default`` filter) are left
        verbatim.

    Examples
    --------
    >>> render_template("Hello {{input.name}}", {"name": "World"}, {})
    'Hello World'

    >>> render_template(
    ...     "Items: {{steps.step-x.items | join: ', '}}",
    ...     {},
    ...     {"step-x": {"items": ["a", "b"]}},
    ... )
    'Items: a, b'

    >>> render_template(
    ...     "{{input.missing | default: fallback}}",
    ...     {},
    ...     {},
    ... )
    'fallback'

    >>> render_template("{{input.nonexistent}}", {}, {})
    '{{input.nonexistent}}'
    """
    if not template:
        return ""

    def _replacer(match: re.Match[str]) -> str:
        full_match = match.group(0)
        expr = match.group(1).strip()

        # Parse the expression
        value_expr, filters = _parse_filters(expr)

        # Resolve the value
        resolved = _resolve_value_expr(value_expr, input_vars, step_results)

        # Apply filters (default, join) — this may replace an _UNSET value
        resolved = _apply_filters(resolved, filters)

        # If still unset after filtering, leave original placeholder
        if resolved is _UNSET:
            return full_match

        # Coerce to string
        return _coerce_to_string(resolved)

    return re.sub(r"\{\{\s*([^}]+?)\s*\}\}", _replacer, template)
