"""Verify all modules import cleanly."""


def test_core_imports():
    from database import get_connection, init_db
    from agent_workflow import AgentWorkflowDB
    from audit_log import ensure_schema, record_event, query_events
    from job_queue import BackgroundWorker
    from state import WorkflowState
    from yaml_pipeline import sync_agent_to_yaml
    from logging_config import configure_logging, get_logger
    from tracing import TraceStore
    from prompt_versioning import PromptVersionManager
    from evaluator import Evaluator
    from credential_store import CredentialStore
    assert True


def test_server_import():
    from api_server import app
    assert app.title == "ES Agent Management"
