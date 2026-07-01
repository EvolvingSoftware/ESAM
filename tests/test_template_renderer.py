"""Comprehensive tests for template_renderer.render_template."""

from __future__ import annotations

import pytest

from src.template_renderer import render_template


# =========================================================================
#  Basic variable resolution
# =========================================================================


def test_simple_input_var():
    """Resolve a basic ``{{input.field}}`` expression."""
    result = render_template("Hello {{input.name}}", {"name": "World"}, {})
    assert result == "Hello World"


def test_step_result_var():
    """Resolve a basic ``{{steps.step-id.field}}`` expression."""
    step_results = {"step-score": {"top_picks": "Item A"}}
    result = render_template(
        "Score: {{steps.step-score.top_picks}}",
        {},
        step_results,
    )
    assert result == "Score: Item A"


def test_no_vars():
    """A plain string without template variables passes through unchanged."""
    result = render_template("Hello, world!", {}, {})
    assert result == "Hello, world!"


def test_empty_template():
    """An empty template string returns an empty string."""
    assert render_template("", {}, {}) == ""


# =========================================================================
#  Default filter
# =========================================================================


def test_default_filter_used():
    """When a variable is missing, the ``default`` filter provides the value."""
    result = render_template(
        "{{input.missing | default: fallback}}",
        {},
        {},
    )
    assert result == "fallback"


def test_default_filter_unused():
    """When a variable IS present, the ``default`` filter is not used."""
    result = render_template(
        "{{input.present | default: fallback}}",
        {"present": "actual_value"},
        {},
    )
    assert result == "actual_value"


def test_default_filter_with_empty_string():
    """An empty-string value triggers the default filter."""
    result = render_template(
        "{{input.empty | default: filled}}",
        {"empty": ""},
        {},
    )
    assert result == "filled"


def test_default_filter_with_none():
    """A ``None`` value triggers the default filter."""
    result = render_template(
        "{{input.null_val | default: backup}}",
        {"null_val": None},
        {},
    )
    assert result == "backup"


def test_default_filter_quoted_arg():
    """The default argument may be quoted (single or double quotes) — quotes
    are stripped before use."""
    result = render_template(
        '{{input.x | default: "quoted-fallback"}}',
        {},
        {},
    )
    assert result == "quoted-fallback"


# =========================================================================
#  Join filter
# =========================================================================


def test_join_filter():
    """Join a list with a separator."""
    step_results = {"step-x": {"items": ["a", "b", "c"]}}
    result = render_template(
        "Items: {{steps.step-x.items | join: ', '}}",
        {},
        step_results,
    )
    assert result == "Items: a, b, c"


def test_join_filter_default_separator():
    """Join without explicit separator uses ``', '`` by default."""
    step_results = {"step-x": {"items": ["x", "y"]}}
    result = render_template(
        "{{steps.step-x.items | join}}",
        {},
        step_results,
    )
    assert result == "x, y"


def test_join_filter_non_list():
    """If the value is not a list, the join filter leaves it as-is."""
    step_results = {"step-x": {"items": "string"}}
    result = render_template(
        "{{steps.step-x.items | join: ', '}}",
        {},
        step_results,
    )
    assert result == "string"


# =========================================================================
#  Missing variable preservation
# =========================================================================


def test_missing_var_preserved():
    """If a variable cannot be resolved and has no default, the original
    ``{{...}}`` expression is left intact."""
    result = render_template("{{input.nonexistent}}", {}, {})
    assert result == "{{input.nonexistent}}"


def test_missing_step_var_preserved():
    """Missing step variable without default is preserved."""
    result = render_template("{{steps.unknown.field}}", {}, {})
    assert result == "{{steps.unknown.field}}"


def test_missing_step_id_preserved():
    """Step ID not found in step_results preserves expression."""
    step_results = {"step-a": {"x": 1}}
    result = render_template(
        "{{steps.step-b.y}}",
        {},
        step_results,
    )
    assert result == "{{steps.step-b.y}}"


# =========================================================================
#  Nested path resolution
# =========================================================================


def test_nested_path():
    """Access nested dict/list paths like ``steps.step-id.items.0.title``."""
    step_results = {
        "step-score": {
            "items": [
                {"title": "First", "score": 9},
                {"title": "Second", "score": 7},
            ],
        },
    }
    result = render_template(
        "Top: {{steps.step-score.items.0.title}}",
        {},
        step_results,
    )
    assert result == "Top: First"


def test_deeply_nested_input():
    """Access deeply nested input fields."""
    result = render_template(
        "{{input.user.profile.name}}",
        {"user": {"profile": {"name": "Alice"}}},
        {},
    )
    assert result == "Alice"


def test_nested_path_missing_midway():
    """If a middle part of a nested path is missing, expression is preserved."""
    result = render_template(
        "{{input.user.missing.field}}",
        {"user": {"profile": {"name": "Alice"}}},
        {},
    )
    assert result == "{{input.user.missing.field}}"


# =========================================================================
#  Multiple variables
# =========================================================================


def test_multiple_vars():
    """Multiple ``{{...}}`` expressions in one template are all resolved."""
    step_results = {"step-score": {"top_picks": "AI News"}}
    result = render_template(
        "{{input.topic}} brief for {{input.date}}: {{steps.step-score.top_picks}}",
        {"topic": "Tech", "date": "2025-01-15"},
        step_results,
    )
    assert result == "Tech brief for 2025-01-15: AI News"


def test_multiple_vars_same_source():
    """Multiple references to the same field work correctly."""
    result = render_template(
        "{{input.x}} + {{input.x}} = {{input.y}}",
        {"x": "hello", "y": "world"},
        {},
    )
    assert result == "hello + hello = world"


# =========================================================================
#  Type coercion
# =========================================================================


def test_number_coercion():
    """Integer values are coerced to strings."""
    result = render_template("Count: {{input.count}}", {"count": 5}, {})
    assert result == "Count: 5"


def test_float_coercion():
    """Float values are coerced to strings."""
    result = render_template("Price: {{input.price}}", {"price": 12.5}, {})
    assert result == "Price: 12.5"


def test_boolean_coercion():
    """Boolean values are coerced to strings."""
    result = render_template("Flag: {{input.flag}}", {"flag": True}, {})
    assert result == "Flag: True"


def test_list_coercion():
    """List values (non-joined) are JSON-serialised."""
    result = render_template("Data: {{input.items}}", {"items": [1, 2, 3]}, {})
    assert result == "Data: [1, 2, 3]"


def test_dict_coercion():
    """Dict values are JSON-serialised."""
    result = render_template("Map: {{input.map}}", {"map": {"a": 1}}, {})
    assert result == 'Map: {"a": 1}'


# =========================================================================
#  None value handling
# =========================================================================


def test_none_value():
    """A ``None`` value returns an empty string (not the text ``'None'``)."""
    result = render_template("{{input.null_val}}", {"null_val": None}, {})
    assert result == ""


def test_none_in_step_result():
    """``None`` in a step result field returns an empty string."""
    step_results = {"step-a": {"result": None}}
    result = render_template(
        "Output: {{steps.step-a.result}}",
        {},
        step_results,
    )
    assert result == "Output: "


# =========================================================================
#  Multiline content
# =========================================================================


def test_multiline_content_preserved():
    """Newlines in resolved values are preserved in the output."""
    step_results = {
        "step-synth": {
            "body": "Line 1\nLine 2\nLine 3",
        },
    }
    result = render_template(
        "Body:\n{{steps.step-synth.body}}",
        {},
        step_results,
    )
    assert result == "Body:\nLine 1\nLine 2\nLine 3"


def test_multiline_with_newlines_around():
    """Newlines before and after the variable are preserved."""
    result = render_template(
        "Before\n{{input.content}}\nAfter",
        {"content": "middle"},
        {},
    )
    assert result == "Before\nmiddle\nAfter"


# =========================================================================
#  Edge cases — whitespace, mixed patterns
# =========================================================================


def test_whitespace_inside_braces():
    """Whitespace around the variable name inside ``{{ }}`` is ignored."""
    result = render_template(
        "Hello {{  input.name  }}",
        {"name": "World"},
        {},
    )
    assert result == "Hello World"


def test_multiple_filters():
    """A value with both default and join filters (default used when
    value is missing)."""
    step_results = {"step-x": {}}
    result = render_template(
        "{{steps.step-x.items | default: [] | join: ', '}}",
        {},
        step_results,
    )
    # The default of "[]" is a string, so join on a string is a no-op
    assert result == "[]"


def test_template_with_only_variable():
    """Template that is entirely a single variable."""
    result = render_template("{{input.name}}", {"name": "Alice"}, {})
    assert result == "Alice"


def test_template_with_partial_variable():
    """Variable that appears mid-text."""
    result = render_template(
        "Dear {{input.name}},\n\nThank you.",
        {"name": "Bob"},
        {},
    )
    assert result == "Dear Bob,\n\nThank you."


def test_mixed_existing_and_missing():
    """Existing and missing variables in the same template."""
    result = render_template(
        "{{input.a}} and {{input.b}}",
        {"a": "exists"},
        {},
    )
    assert result == "exists and {{input.b}}"


def test_default_and_join_chain():
    """Chain default then join: if the value is a list, join; if missing,
    use default."""
    step_results = {"step-x": {"tags": ["a", "b"]}}
    result = render_template(
        "{{steps.step-x.tags | default: none | join: '+'}}",
        {},
        step_results,
    )
    assert result == "a+b"

    step_results_missing = {"step-x": {}}
    result2 = render_template(
        "{{steps.step-x.tags | default: none | join: '+'}}",
        {},
        step_results_missing,
    )
    assert result2 == "none"


# =========================================================================
#  Integration-style — resembles newsletter workflow
# =========================================================================


def test_newsletter_style_prompt():
    """A prompt resembling the newsletter workflow with input, step
    references, join filter, and default."""
    input_vars = {
        "topic": "AI Regulation",
        "date": "2025-06-24",
        "recipient": "admin@example.com",
    }
    step_results = {
        "step-collect": {
            "search_queries": ["AI policy 2025", "regulation EU"],
            "communities": ["r/artificial", "HN"],
        },
        "step-score": {
            "top_picks": ["AI Act impact", "EU guidelines"],
        },
        "step-synthesize": {
            "subject": "AI Regulation Brief",
            "body_markdown": "Here is the brief.\n\nIt covers key topics.",
            "key_signals": ["Signal A", "Signal B"],
            "decision_grade_items": 5,
        },
    }

    template = (
        "Write the \"{{input.topic}}\" intelligence brief for {{input.date}}.\n\n"
        "Top picks: {{steps.step-score.top_picks | join: ', '}}\n\n"
        "To: {{input.recipient | default: 'sebastian@evolving.software'}}\n\n"
        "{{steps.step-synthesize.body_markdown}}\n"
        "Key Signals: {{steps.step-synthesize.key_signals | join: ', '}}"
    )

    expected = (
        'Write the "AI Regulation" intelligence brief for 2025-06-24.\n\n'
        "Top picks: AI Act impact, EU guidelines\n\n"
        "To: admin@example.com\n\n"
        "Here is the brief.\n\nIt covers key topics.\n"
        "Key Signals: Signal A, Signal B"
    )

    result = render_template(template, input_vars, step_results)
    assert result == expected


def test_escalation_subject_style():
    """Escalation subject line template resembling newsletter review."""
    input_vars = {"topic": "AI Regulation", "date": "2025-06-24"}
    template = "[Newsletter] Review — {{input.topic}} for {{input.date}}"
    expected = "[Newsletter] Review — AI Regulation for 2025-06-24"
    assert render_template(template, input_vars, {}) == expected


def test_escalation_body_style():
    """Escalation body template with mixed references."""
    input_vars = {"topic": "Security", "date": "2025-06-25"}
    step_results = {
        "step-synthesize": {
            "subject": "Security Brief",
            "body_markdown": "Body content here.",
            "key_signals": ["Threat A"],
        },
    }
    template = (
        "Intelligence Brief Review\n"
        "Topic: {{input.topic}}\n"
        "Date: {{input.date}}\n\n"
        "Subject: {{steps.step-synthesize.subject}}\n"
        "Body:\n{{steps.step-synthesize.body_markdown}}\n\n"
        "Key Signals: {{steps.step-synthesize.key_signals | join: ', '}}"
    )
    expected = (
        "Intelligence Brief Review\n"
        "Topic: Security\n"
        "Date: 2025-06-25\n\n"
        "Subject: Security Brief\n"
        "Body:\nBody content here.\n\n"
        "Key Signals: Threat A"
    )
    assert render_template(template, input_vars, step_results) == expected
