"""Tests for the SMTP Delivery Service."""

from __future__ import annotations

import smtplib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure src is on the path
SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

import pytest

from delivery.smtp_engine import SMTPEngine
from delivery.provider_router import ProviderRouter
from delivery.tracker import DeliveryTracker


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _mock_db(monkeypatch):
    """Mock database.get_connection so tests don't need real SQLite."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.execute.return_value = mock_cursor
    mock_conn.fetchone.return_value = None
    mock_cursor.fetchone.return_value = None
    mock_cursor.fetchall.return_value = []
    monkeypatch.setattr("delivery.tracker.get_connection", lambda: mock_conn)
    monkeypatch.setattr("delivery.provider_router.get_connection", lambda: mock_conn)
    return mock_conn


@pytest.fixture
def smtp_config() -> dict:
    return {
        "host": "smtp.example.com",
        "port": 587,
        "username": "user@example.com",
        "password": "sekret",
        "use_tls": True,
    }


# ── SMTP Engine Tests ──────────────────────────────────────────────────


def test_smtp_send(smtp_config):
    """Verify SMTPEngine.send calls smtplib with correct args."""
    with patch("delivery.smtp_engine._CONNECTION_POOL") as mock_pool:
        mock_client = MagicMock()
        mock_pool.acquire.return_value = mock_client

        engine = SMTPEngine()
        result = engine.send(
            to="recipient@example.com",
            subject="Test Subject",
            html_body="<h1>Hello</h1>",
            text_body="Hello plain",
            config=smtp_config,
        )

        assert result["success"] is True
        assert result["message_id"].startswith("msg_")
        assert result["provider"] == "smtp"
        assert result["status"] == "sent"
        assert result["error"] == ""

        # Verify the MIME message was constructed correctly
        mock_client.sendmail.assert_called_once()
        args = mock_client.sendmail.call_args
        assert args[0][0] == "user@example.com"  # From
        assert args[0][1] == ["recipient@example.com"]  # To

        # Verify connection was acquired with correct config
        mock_pool.acquire.assert_called_once_with(smtp_config)


def test_smtp_send_without_text_body(smtp_config):
    """Verify send works without an explicit text body."""
    with patch("delivery.smtp_engine._CONNECTION_POOL") as mock_pool:
        mock_client = MagicMock()
        mock_pool.acquire.return_value = mock_client

        engine = SMTPEngine()
        result = engine.send(
            to="recipient@example.com",
            subject="HTML only",
            html_body="<h1>Hello</h1>",
            config=smtp_config,
        )

        assert result["success"] is True
        mock_client.sendmail.assert_called_once()


def test_smtp_send_failure_retry(smtp_config):
    """Verify retry on transient SMTP failures with backoff."""
    with patch("delivery.smtp_engine._CONNECTION_POOL") as mock_pool:
        mock_client = MagicMock()
        # Fail the first two attempts, succeed on third
        mock_client.sendmail.side_effect = [
            smtplib.SMTPServerDisconnected("Connection lost"),
            smtplib.SMTPConnectError(421, b"Service unavailable"),
            None,  # Success
        ]
        mock_pool.acquire.return_value = mock_client

        engine = SMTPEngine(max_retries=3)
        result = engine.send(
            to="recipient@example.com",
            subject="Retry test",
            html_body="<p>Retry</p>",
            config=smtp_config,
        )

        assert result["success"] is True
        assert result["status"] == "sent"
        # Should have called sendmail 3 times
        assert mock_client.sendmail.call_count == 3
        # Connection pool should have been released after each failure
        assert mock_pool.release.call_count == 2


def test_smtp_send_retry_exhausted(smtp_config):
    """Verify failure after exhausting all retries."""
    with patch("delivery.smtp_engine._CONNECTION_POOL") as mock_pool:
        mock_client = MagicMock()
        mock_client.sendmail.side_effect = smtplib.SMTPServerDisconnected("Connection lost")
        mock_pool.acquire.return_value = mock_client

        engine = SMTPEngine(max_retries=3)
        result = engine.send(
            to="recipient@example.com",
            subject="Fail test",
            html_body="<p>Fail</p>",
            config=smtp_config,
        )

        assert result["success"] is False
        assert result["status"] == "failed"
        assert "Connection lost" in result["error"]
        # Should have tried 3 times
        assert mock_client.sendmail.call_count == 3


def test_smtp_send_auth_error_no_retry(smtp_config):
    """Verify authentication errors are NOT retried."""
    with patch("delivery.smtp_engine._CONNECTION_POOL") as mock_pool:
        mock_client = MagicMock()
        mock_client.sendmail.side_effect = smtplib.SMTPAuthenticationError(
            535, b"Authentication failed"
        )
        mock_pool.acquire.return_value = mock_client

        engine = SMTPEngine(max_retries=3)
        result = engine.send(
            to="recipient@example.com",
            subject="Auth fail",
            html_body="<p>Auth</p>",
            config=smtp_config,
        )

        assert result["success"] is False
        # Should NOT have retried after auth failure
        assert mock_client.sendmail.call_count == 1


# ── Provider Router Tests ──────────────────────────────────────────────


def test_provider_router_smtp():
    """Verify ProviderRouter resolves inline SMTP credentials."""
    router = ProviderRouter()

    # When credential store returns nothing, should fall back to stub or default
    config = router.resolve("unknown-ref")
    assert isinstance(config, dict)
    assert "host" in config
    assert "port" in config


def test_provider_router_sendgrid_stub():
    """Verify SendGrid stub is resolved by keyword match."""
    router = ProviderRouter()
    config = router.resolve("sendgrid-prod")
    assert config["host"] == "smtp.sendgrid.net"
    assert config["port"] == 587
    assert config["use_tls"] is True


def test_provider_router_mailgun_stub():
    """Verify Mailgun stub is resolved by keyword match."""
    router = ProviderRouter()
    config = router.resolve("mailgun-sandbox")
    assert config["host"] == "smtp.mailgun.org"
    assert config["port"] == 587


def test_provider_router_cache():
    """Verify cached results are reused."""
    router = ProviderRouter()
    config1 = router.resolve("sendgrid-prod")
    config2 = router.resolve("sendgrid-prod")
    assert config1 == config2


def test_provider_router_clear_cache():
    """Verify cache can be cleared."""
    router = ProviderRouter()
    _ = router.resolve("sendgrid-prod")
    router.clear_cache()
    # Should not be cached anymore
    assert router._cache == {}


# ── Delivery Tracker Tests ─────────────────────────────────────────────


def test_delivery_tracker_record(_mock_db):
    """Verify DeliveryTracker.record creates a log entry."""
    result = DeliveryTracker.record(
        run_id="run-001",
        to="user@example.com",
        subject="Test",
        provider="smtp",
        status="sent",
        message_id="msg_abc",
        error="",
    )

    assert isinstance(result, dict)
    _mock_db.execute.assert_any_call(
        "SELECT * FROM wf_delivery_log WHERE id = ?",
        unittest.mock.ANY,
    )


def test_delivery_tracker_get_log(_mock_db):
    """Verify DeliveryTracker.get_log fetches log entries."""
    _mock_db.execute.return_value.fetchall.return_value = [
        MagicMock(**{
            "id": "log-1", "run_id": "run-001",
            "to_addr": "user@example.com", "subject": "Test",
            "provider": "smtp", "status": "sent",
            "message_id": "msg_abc", "error": "",
            "sent_at": "2026-01-01T00:00:00", "created_at": "2026-01-01T00:00:00",
            "__getitem__.side_effect": lambda k: {
                "id": "log-1", "run_id": "run-001",
                "to_addr": "user@example.com", "subject": "Test",
                "provider": "smtp", "status": "sent",
                "message_id": "msg_abc", "error": "",
                "sent_at": "2026-01-01T00:00:00", "created_at": "2026-01-01T00:00:00",
            }[k],
        }),
    ]

    logs = DeliveryTracker.get_log("run-001")
    assert len(logs) > 0
    for entry in logs:
        assert isinstance(entry, dict)


def test_delivery_tracker_status(_mock_db):
    """Verify DeliveryTracker.get_status returns correct status."""
    _mock_db.execute.return_value.fetchone.return_value = {"status": "sent"}

    status = DeliveryTracker.get_status("msg_abc")
    assert status == "sent"


def test_delivery_tracker_status_unknown(_mock_db):
    """Verify get_status returns 'unknown' for missing message_id."""
    _mock_db.execute.return_value.fetchone.return_value = None

    status = DeliveryTracker.get_status("nonexistent")
    assert status == "unknown"


# Need unittest.mock for the tracker test
import unittest.mock
