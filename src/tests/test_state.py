"""Test the structured state management module."""
from state import WorkflowState


def test_legacy_variable_resolution():
    """{field_name} resolves from input."""
    state = WorkflowState({"name": "Alice", "amount": "500"})
    result = state.resolve("Dear {name}, you owe ${amount}")
    assert result == "Dear Alice, you owe $500"


def test_structured_step_reference():
    """{{steps.STEP_ID.field}} resolves from step output."""
    state = WorkflowState({"test": "data"})
    state.set_step_output(
        "step-assess",
        '{"risk_score": 85, "level": "high"}',
        {"risk_score": 85, "level": "high"},
    )
    result = state.resolve(
        "Risk: {{steps.step-assess.risk_score}}, Level: {{steps.step-assess.level}}"
    )
    assert result == "Risk: 85, Level: high"


def test_step_output_text():
    """{{steps.STEP_ID.output_text}} resolves to raw text."""
    state = WorkflowState({})
    state.set_step_output("step-1", "Hello world")
    result = state.resolve("Output: {{steps.step-1.output_text}}")
    assert result == "Output: Hello world"


def test_input_reference():
    """{{input.field}} resolves from input."""
    state = WorkflowState({"customer": "Acme Corp", "amount": "$5000"})
    result = state.resolve(
        "Customer: {{input.customer}}, Amount: {{input.amount}}"
    )
    assert result == "Customer: Acme Corp, Amount: $5000"


def test_unknown_step_reference_preserved():
    """Unknown step references should leave the placeholder unchanged."""
    state = WorkflowState({"test": "data"})
    result = state.resolve("Unknown: {{steps.nonexistent.field}}")
    assert "{{steps.nonexistent.field}}" in result


def test_mixed_templates():
    """Mix legacy and structured templates."""
    state = WorkflowState({"customer": "Bob"})
    state.set_step_output("analyze", "High risk", {"risk": "High"})
    result = state.resolve("{customer}: {{steps.analyze.risk}}")
    assert result == "Bob: High"


def test_set_step_error():
    """Error steps should be tracked."""
    state = WorkflowState({})
    state.set_step_error("step-1", "LLM timeout")
    assert len(state.errors) == 1
    assert state.errors[0]["error"] == "LLM timeout"
    assert state.steps["step-1"]["status"] == "error"


def test_to_dict_roundtrip():
    """to_dict and from_dict should be inverse operations."""
    state = WorkflowState({"key": "value"})
    state.set_step_output("s1", "output text", {"score": 95})
    d = state.to_dict()
    restored = WorkflowState.from_dict(d)
    assert restored.input == {"key": "value"}
    assert restored.steps["s1"]["output_text"] == "output text"
