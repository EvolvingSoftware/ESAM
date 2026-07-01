"""SMTP Engine — send emails via SMTP with connection pooling and retry."""

from __future__ import annotations

import logging
import smtplib
import time
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["SMTPEngine"]

# ── Connection pool ────────────────────────────────────────────────────


class _ConnectionPool:
    """Simple per-host connection pool for SMTP connections.

    Caches open SMTP connections keyed by ``host:port:username`` so that
    repeated sends to the same server reuse the same TCP connection.
    """

    def __init__(self) -> None:
        self._pool: dict[str, smtplib.SMTP] = {}

    def _pool_key(self, config: dict[str, Any]) -> str:
        return f"{config.get('host', 'localhost')}:{config.get('port', 587)}:{config.get('username', '')}"

    def acquire(self, config: dict[str, Any]) -> smtplib.SMTP:
        """Get a cached connection or create a new one."""
        key = self._pool_key(config)
        if key in self._pool:
            try:
                # Quick no-op to verify connection is alive
                self._pool[key].noop()
                return self._pool[key]
            except Exception:
                # Stale connection — remove and reconnect
                self._release(key)
                logger.debug("Stale SMTP connection to %s — reconnecting", key)

        client = self._connect(config)
        self._pool[key] = client
        return client

    def release(self, config: dict[str, Any]) -> None:
        """Explicitly release and close a pooled connection."""
        key = self._pool_key(config)
        self._release(key)

    def _release(self, key: str) -> None:
        client = self._pool.pop(key, None)
        if client is not None:
            try:
                client.quit()
            except Exception:
                client.close()

    def close_all(self) -> None:
        """Close all pooled connections."""
        for key in list(self._pool.keys()):
            self._release(key)

    @staticmethod
    def _connect(config: dict[str, Any]) -> smtplib.SMTP:
        """Create and authenticate a new SMTP connection."""
        host = str(config.get("host", "localhost"))
        port = int(config.get("port", 587))
        timeout = int(config.get("timeout", 30))
        use_tls = bool(config.get("use_tls", True))

        logger.debug("Connecting to SMTP %s:%d (tls=%s)", host, port, use_tls)

        if use_tls and port == 465:
            # Implicit SSL (SMTPS)
            client = smtplib.SMTP_SSL(host=host, port=port, timeout=timeout)
        else:
            client = smtplib.SMTP(host=host, port=port, timeout=timeout)
            client.ehlo()
            if use_tls:
                client.starttls()
                client.ehlo()

        username = str(config.get("username", ""))
        password = str(config.get("password", ""))
        if username and password:
            client.login(username, password)

        return client


# ── Global connection pool ─────────────────────────────────────────────

_CONNECTION_POOL = _ConnectionPool()


# ── SMTPEngine ─────────────────────────────────────────────────────────


class SMTPEngine:
    """Send emails via SMTP with connection pooling and automatic retry.

    Usage::

        engine = SMTPEngine()
        result = engine.send(
            to="recipient@example.com",
            subject="Hello",
            html_body="<h1>Hello</h1>",
            text_body="Hello",
            config={
                "host": "smtp.example.com",
                "port": 587,
                "username": "user@example.com",
                "password": "sekret",
                "use_tls": True,
            },
        )
    """

    def __init__(self, max_retries: int = 3) -> None:
        self._max_retries = max_retries

    # ── Public API ─────────────────────────────────────────────────────

    def send(
        self,
        to: str,
        subject: str,
        html_body: str,
        text_body: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send an email via SMTP.

        Args:
            to: Recipient email address.
            subject: Email subject line.
            html_body: HTML body content.
            text_body: Optional plain text fallback body.
            config: SMTP connection configuration with keys:
                ``host``, ``port``, ``username``, ``password``,
                ``use_tls``, ``timeout``.

        Returns:
            A result dict with keys:
            - ``success`` (bool): Whether the send succeeded.
            - ``message_id`` (str): A unique message identifier.
            - ``provider`` (str): ``"smtp"`` or provider name.
            - ``status`` (str): ``"sent"`` or ``"failed"``.
            - ``error`` (str): Error message if failed.
        """
        cfg = dict(config or {})
        message_id = self._generate_message_id()
        provider = cfg.get("provider", "smtp")

        try:
            self._send_with_retry(to, subject, html_body, text_body, cfg)
            logger.info(
                "Email sent to %s subject=%s message_id=%s provider=%s",
                to, subject, message_id, provider,
            )
            return {
                "success": True,
                "message_id": message_id,
                "provider": provider,
                "status": "sent",
                "error": "",
            }
        except Exception as exc:
            error_msg = str(exc)
            logger.error(
                "Failed to send email to %s subject=%s: %s",
                to, subject, error_msg,
            )
            return {
                "success": False,
                "message_id": message_id,
                "provider": provider,
                "status": "failed",
                "error": error_msg,
            }

    # ── Internal methods ───────────────────────────────────────────────

    def _send_with_retry(
        self,
        to: str,
        subject: str,
        html_body: str,
        text_body: str | None,
        config: dict[str, Any],
    ) -> None:
        """Attempt to send with exponential backoff on transient failures."""
        last_exc: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                self._send_once(to, subject, html_body, text_body, config)
                return  # Success
            except smtplib.SMTPAuthenticationError:
                # Authentication errors are non-transient — don't retry
                raise
            except smtplib.SMTPRecipientsRefused:
                # Recipient refused — non-transient
                raise
            except (
                smtplib.SMTPServerDisconnected,
                smtplib.SMTPConnectError,
                smtplib.SMTPHeloError,
                TimeoutError,
                OSError,
            ) as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    delay = 2 ** attempt  # exponential backoff: 2, 4, 8
                    logger.warning(
                        "SMTP attempt %d/%d failed for %s (retry in %ds): %s",
                        attempt, self._max_retries, to, delay, exc,
                    )
                    time.sleep(delay)
                    # Close stale connection so we reconnect on next attempt
                    _CONNECTION_POOL.release(config)
                else:
                    logger.error(
                        "SMTP attempt %d/%d failed for %s (no retries left): %s",
                        attempt, self._max_retries, to, exc,
                    )

        # All retries exhausted
        raise last_exc or RuntimeError(f"SMTP send failed after {self._max_retries} attempts")

    def _send_once(
        self,
        to: str,
        subject: str,
        html_body: str,
        text_body: str | None,
        config: dict[str, Any],
    ) -> None:
        """Build and send a single email message."""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = str(config.get("from_addr", config.get("username", "noreply@evolvingsoftware.com.au")))
        msg["To"] = to

        # Attach plain text fallback first (email clients prefer last part)
        text = text_body or html_body
        if text:
            msg.attach(MIMEText(text, "plain"))

        # Attach HTML version
        msg.attach(MIMEText(html_body, "html"))

        # Get or create connection from pool
        client = _CONNECTION_POOL.acquire(config)
        client.sendmail(msg["From"], [to], msg.as_string())

    @staticmethod
    def _generate_message_id() -> str:
        """Generate a unique message identifier."""
        return f"msg_{uuid.uuid4().hex[:16]}"
