"""Simple JSONPath parser — supports basic path expressions."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class JSONPathParser:
    """A minimal JSONPath implementation supporting common patterns.

    Supported expressions:

    * ``$`` — root object
    * ``$.field`` — dot-notation field access
    * ``$.a.b.c`` — nested dot-notation
    * ``$.arr[0]`` — array index access
    * ``$.arr[*]`` — array wildcard (all elements)
    * ``$[*]`` — root array wildcard
    * ``$.obj[*].field`` — wildcard then field access
    """

    def parse(
        self,
        json_data: Any,
        path_expression: str,
    ) -> list[dict[str, Any]]:
        """Evaluate *path_expression* against *json_data*.

        Args:
            json_data: The parsed JSON data (dict or list).
            path_expression: A JSONPath expression like ``$.data.items[*]``.

        Returns:
            A list of matched items (typically dicts).
        """
        if not path_expression:
            return []

        # Strip leading $ if present
        expr = path_expression.strip()
        if expr.startswith("$."):
            expr = expr[2:]
        elif expr.startswith("$"):
            expr = expr[1:]

        if not expr:
            # Just "$" — return the root as a single-item list (if dict)
            if isinstance(json_data, dict):
                return [json_data]
            if isinstance(json_data, list):
                return json_data
            return [json_data]

        # Tokenize: split on '.' but keep bracketed segments together
        tokens = self._tokenize(expr)
        results = self._evaluate([json_data], tokens)
        # Don't filter to dicts only — return all matched results
        return results

    # ── Internal ──────────────────────────────────────────────────────

    @staticmethod
    def _tokenize(expr: str) -> list[str]:
        """Split a dotted path into tokens, preserving bracketed segments."""
        tokens: list[str] = []
        current: list[str] = []
        bracket_depth = 0

        for ch in expr:
            if ch == "[":
                if not bracket_depth and current:
                    # Collect a bracket group
                    tokens.append("".join(current))
                    current = []
                bracket_depth += 1
                current.append(ch)
            elif ch == "]":
                current.append(ch)
                bracket_depth -= 1
                if bracket_depth == 0:
                    tokens.append("".join(current))
                    current = []
            elif ch == "." and bracket_depth == 0:
                if current:
                    tokens.append("".join(current))
                    current = []
            else:
                current.append(ch)

        if current:
            tokens.append("".join(current))

        return [t for t in tokens if t]

    @staticmethod
    def _evaluate(
        current: list[Any],
        tokens: list[str],
    ) -> list[Any]:
        """Recursively evaluate tokens against current result set."""
        if not tokens:
            return current

        token = tokens[0]
        remaining = tokens[1:]

        results: list[Any] = []

        if token == "*":
            # Wildcard — flatten all levels
            for item in current:
                if isinstance(item, dict):
                    results.extend(item.values())
                elif isinstance(item, list):
                    results.extend(item)
                else:
                    results.append(item)

        elif token.startswith("[") and token.endswith("]"):
            inner = token[1:-1]
            if inner == "*":
                # Array wildcard [*]
                for item in current:
                    if isinstance(item, (list, tuple)):
                        results.extend(item)
                    elif isinstance(item, dict):
                        results.extend(item.values())
                    else:
                        results.append(item)
            else:
                # Array index [n]
                try:
                    index = int(inner)
                except ValueError:
                    # Could be a string key like ["key"]
                    inner_clean = inner.strip().strip("\"'")
                    for item in current:
                        if isinstance(item, dict) and inner_clean in item:
                            results.append(item[inner_clean])
                        elif isinstance(item, (list, tuple)):
                            try:
                                idx = int(inner_clean)
                                if 0 <= idx < len(item):
                                    results.append(item[idx])
                            except ValueError:
                                pass
                    return JSONPathParser._evaluate(results, remaining)

                for item in current:
                    if isinstance(item, (list, tuple)):
                        if 0 <= index < len(item):
                            results.append(item[index])
                    elif isinstance(item, dict):
                        # Try numeric key as string
                        try:
                            results.append(item[str(index)])
                        except (KeyError, TypeError):
                            pass

        else:
            # Dot-notation field access
            for item in current:
                if isinstance(item, dict) and token in item:
                    results.append(item[token])
                elif isinstance(item, (list, tuple)):
                    for sub in item:
                        if isinstance(sub, dict) and token in sub:
                            results.append(sub[token])

        return JSONPathParser._evaluate(results, remaining)
