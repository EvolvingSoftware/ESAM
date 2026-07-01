"""Test the workflow YAML loader."""
from workflow_loader import import_one_yaml, export_one_yaml, validate_workflow

SAMPLE_YAML = """\
name: Test Agent
description: A test agent
version: 1

steps:
  - id: step-1
    label: Step One
    type: llm_call
    prompt: Test prompt
    position: [100, 100]

connections:
  - from: step-1
    to: step-2
    label: Step 1 → 2
"""


def test_import_valid_yaml():
    """Valid YAML should import correctly."""
    data = import_one_yaml(SAMPLE_YAML)
    assert data["name"] == "Test Agent"
    assert len(data["steps"]) == 1
    assert len(data["connections"]) == 1


def test_export_roundtrip():
    """Export then import should preserve data."""
    original = {
        "name": "Roundtrip Agent",
        "description": "Testing roundtrip",
        "steps": [
            {"id": "s1", "label": "Step 1", "type": "llm_call", "prompt": "Hello"}
        ],
        "connections": [],
    }
    yaml_str = export_one_yaml(original)
    restored = import_one_yaml(yaml_str)
    assert restored["name"] == original["name"]
    assert len(restored["steps"]) == len(original["steps"])


def test_validation_missing_name():
    """Missing name should fail validation."""
    v = validate_workflow(
        {"steps": [{"label": "x", "type": "llm_call", "id": "s1"}]}
    )
    assert not v["valid"]
    assert any("name" in e for e in v["errors"])


def test_validation_duplicate_step_ids():
    """Duplicate step IDs should fail validation."""
    data = {
        "name": "Test",
        "steps": [
            {"id": "s1", "label": "A", "type": "llm_call"},
            {"id": "s1", "label": "B", "type": "llm_call"},
        ],
    }
    v = validate_workflow(data)
    assert not v["valid"]
    assert any("duplicate" in e for e in v["errors"])
