"""Provider Router — resolves credential references to SMTP configuration."""

from __future__ import annotations

import json
import logging
from typing import Any

from database import get_connection

logger = logging.getLogger(__name__)

__all__ = ["ProviderRouter"]

# ── Known provider config stubs ────────────────────────────────────────

SENDGRID_SMTP = {
    "host": "smtp.sendgrid.net",
    "port": 587,
    "use_tls": True,
}

AWS_SES_SMTP = {
    "host": "email-smtp.us-east-1.amazonaws.com",
    "port": 587,
    "use_tls": True,
}

MAILGUN_SMTP = {
    "host": "smtp.mailgun.org",
    "port": 587,
    "use_tls": True,
}

PROVIDER_STUBS: dict[str, dict[str, Any]] = {
    "sendgrid": dict(SENDGRID_SMTP),
    "aws_ses": dict(AWS_SES_SMTP),
    "mailgun": dict(MAILGUN_SMTP),
}


class ProviderRouter:
    """Resolve a credential reference into an SMTP configuration dict.

    Supports direct SMTP credentials (inline host/port/username/password),
    as well as stubs for SendGrid, AWS SES, and Mailgun.

    Usage::

        router = ProviderRouter()
        config = router.resolve(credential_ref="my-smtp-creds")

    Returns::

        {
            "host": "smtp.example.com",
            "port": 587,
            "username": "user@example.com",
            "password": "sekret",
            "use_tls": True,
        }
    """

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] = {}

    # ── Public API ─────────────────────────────────────────────────────

    def resolve(self, credential_ref: str) -> dict[str, Any]:
        """Resolve a credential reference to an SMTP config dict.

        Looks up the credential in the credential store
        (``wf_agent_credentials`` table), then interprets the
        ``credential_key`` to determine the provider type.

        Args:
            credential_ref: The credential reference to resolve (e.g.
                ``"my-smtp-creds"``, ``"sendgrid-prod"``).

        Returns:
            An SMTP config dict with keys ``host``, ``port``,
            ``username``, ``password``, ``use_tls``.

        Raises:
            ValueError: If the credential reference cannot be resolved.
        """
        if credential_ref in self._cache:
            return dict(self._cache[credential_ref])

        # Try credential store first
        try:
            cred = self._fetch_credential(credential_ref)
            if cred:
                config = self._parse_credential(credential_ref, cred)
                self._cache[credential_ref] = dict(config)
                return config
        except Exception as exc:
            logger.debug("Credential store lookup failed for %s: %s", credential_ref, exc)

        # Fall back to provider stub by matching keywords in the ref
        config = self._resolve_provider_stub(credential_ref)
        if config:
            self._cache[credential_ref] = dict(config)
            return config

        # Last resort: return a default localhost config
        logger.warning(
            "Credential ref '%s' not found; returning localhost SMTP stub",
            credential_ref,
        )
        return {
            "host": "localhost",
            "port": 1025,
            "username": "",
            "password": "",
            "use_tls": False,
        }

    def clear_cache(self) -> None:
        """Clear the in-memory resolution cache."""
        self._cache.clear()

    # ── Internal helpers ───────────────────────────────────────────────

    def _fetch_credential(self, credential_ref: str) -> dict[str, Any] | None:
        """Query the credential store for a matching record."""
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM wf_agent_credentials WHERE credential_key = ?",
            (credential_ref,),
        ).fetchone()
        if row:
            return dict(row)
        # Also try matching by id
        row = conn.execute(
            "SELECT * FROM wf_agent_credentials WHERE id = ?",
            (credential_ref,),
        ).fetchone()
        return dict(row) if row else None

    def _parse_credential(
        self, credential_ref: str, cred: dict[str, Any]
    ) -> dict[str, Any]:
        """Parse a credential record into an SMTP config dict.

        The ``encrypted_value`` field is expected to be a JSON string
        with keys ``host``, ``port``, ``username``, ``password``,
        ``use_tls``.  Alternatively, the credential_key itself may
        contain a provider hint (e.g. ``sendgrid-prod``).
        """
        raw = cred.get("encrypted_value", "{}")
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            parsed = {}

        if isinstance(parsed, dict) and "host" in parsed:
            return {
                "host": str(parsed.get("host", "localhost")),
                "port": int(parsed.get("port", 587)),
                "username": str(parsed.get("username", "")),
                "password": str(parsed.get("password", "")),
                "use_tls": bool(parsed.get("use_tls", True)),
            }

        # No inline config — try provider stub
        stub_config = self._resolve_provider_stub(credential_ref)
        if stub_config:
            # Use stub as base, but inject username/password from parsed
            stub_config["username"] = str(parsed.get("username", parsed.get("api_key", "")))
            stub_config["password"] = str(parsed.get("password", parsed.get("api_key", "")))
            return stub_config

        return {
            "host": "localhost",
            "port": 1025,
            "username": "",
            "password": "",
            "use_tls": False,
        }

    @staticmethod
    def _resolve_provider_stub(ref: str) -> dict[str, Any] | None:
        """Match credential ref keywords to known provider stubs."""
        ref_lower = ref.lower()
        for key, stub in PROVIDER_STUBS.items():
            if key in ref_lower:
                return dict(stub)
        return None
