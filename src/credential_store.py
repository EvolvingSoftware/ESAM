"""AES-256-GCM encrypted credential storage for per-agent credentials.

Provides a CredentialStore class that wraps Fernet symmetric encryption
to securely store and retrieve per-agent credentials via the
AgentWorkflowDB data model.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken

from agent_workflow import AgentWorkflowDB

__all__ = ["CredentialStore"]

_MASTER_KEY_DIR = Path.home() / ".hermes" / "esam"
_MASTER_KEY_FILE = _MASTER_KEY_DIR / "master.key"
_ENV_VAR = "ESAM_MASTER_KEY"


def _load_or_create_master_key() -> bytes:
    """Load the master key from the environment, or generate one for dev.

    Priority:
        1. ``ESAM_MASTER_KEY`` environment variable.
        2. ``~/.hermes/esam/master.key`` file on disk.
        3. Generate a new key, store it, and issue a warning.

    Returns:
        The master key as raw bytes (suitable for ``cryptography.fernet.Fernet``).
    """
    # 1. Environment variable
    env_key = os.environ.get(_ENV_VAR)
    if env_key:
        try:
            return env_key.encode("utf-8")
        except Exception:
            pass

    # 2. File on disk
    if _MASTER_KEY_FILE.exists():
        return _MASTER_KEY_FILE.read_bytes()

    # 3. Generate --- dev-only fallback
    key = Fernet.generate_key()
    _MASTER_KEY_DIR.mkdir(parents=True, exist_ok=True)
    _MASTER_KEY_FILE.write_bytes(key)
    _MASTER_KEY_FILE.chmod(0o600)  # owner-read/write only

    warnings.warn(
        f"ESAM_MASTER_KEY not set in environment. "
        f"Generated dev master key at {_MASTER_KEY_FILE}",
        RuntimeWarning,
        stacklevel=2,
    )
    return key


class CredentialStore:
    """Encrypted credential store for a specific agent.

    Each instance is scoped to a single ``agent_id``.  All encryption uses
    Fernet (AES-256-GCM via the ``cryptography`` library).
    """

    def __init__(self, agent_id: str) -> None:
        """Initialise the store and load the master key.

        Args:
            agent_id: The agent whose credentials this instance manages.
        """
        self._agent_id: str = agent_id
        self._master_key: bytes = _load_or_create_master_key()
        self._fernet: Fernet = Fernet(self._master_key)

    # ------------------------------------------------------------------
    # Low-level encryption helpers
    # ------------------------------------------------------------------

    def encrypt_value(self, plaintext: str) -> str:
        """Encrypt a plaintext string and return a base64-encoded blob.

        Args:
            plaintext: The value to encrypt.

        Returns:
            Base64-encoded encrypted blob (UTF-8 string).
        """
        token: bytes = self._fernet.encrypt(plaintext.encode("utf-8"))
        return token.decode("utf-8")

    def decrypt_value(self, ciphertext: str) -> Optional[str]:
        """Decrypt a base64-encoded ciphertext blob.

        Args:
            ciphertext: The base64-encoded encrypted blob.

        Returns:
            The decrypted plaintext, or ``None`` if decryption fails.
        """
        try:
            plaintext: bytes = self._fernet.decrypt(ciphertext.encode("utf-8"))
            return plaintext.decode("utf-8")
        except (InvalidToken, Exception):
            return None

    # ------------------------------------------------------------------
    # Credential lifecycle (delegates to AgentWorkflowDB)
    # ------------------------------------------------------------------

    def create(
        self,
        credential_key: str,
        plaintext_value: str,
        scope_step_id: Optional[str] = None,
    ) -> dict:
        """Create a new encrypted credential for this agent.

        The value is encrypted *before* being passed to the database layer.

        Args:
            credential_key: Human-readable key (e.g. ``"openai_api_key"``).
            plaintext_value: The secret value to encrypt and store.
            scope_step_id: Optional workflow-step ID this credential is
                scoped to.

        Returns:
            The credential record as returned by
            :meth:`AgentWorkflowDB.create_credential`.
        """
        encrypted: str = self.encrypt_value(plaintext_value)
        return AgentWorkflowDB().create_credential(
            agent_id=self._agent_id,
            credential_key=credential_key,
            encrypted_value=encrypted,
            scope_step_id=scope_step_id,
        )

    def list(self) -> list[dict]:
        """List all credentials for this agent.

        Encrypted values are masked as ``"••••••••"``.

        Returns:
            A list of credential dictionaries with masked values.
        """
        return AgentWorkflowDB().list_credentials(agent_id=self._agent_id)

    def get(self, cred_id: str) -> Optional[dict]:
        """Retrieve a single credential **with its decrypted value**.

        .. warning::

            This is intended for *internal* use.  The decrypted secret is
            returned in plaintext.  Handle with care.

        Args:
            cred_id: The credential record ID.

        Returns:
            The credential dictionary with the ``encrypted_value`` field
            replaced by the decrypted plaintext, or ``None`` if the
            credential does not exist or decryption fails.
        """
        record: Optional[dict] = AgentWorkflowDB().get_credential(cred_id)
        if record is None:
            return None

        plaintext: Optional[str] = self.decrypt_value(record["encrypted_value"])
        if plaintext is None:
            return None

        record["encrypted_value"] = plaintext
        return record

    def delete(self, cred_id: str) -> bool:
        """Delete a credential record.

        Args:
            cred_id: The credential record ID to remove.

        Returns:
            ``True`` if a record was actually deleted, ``False`` otherwise.
        """
        return AgentWorkflowDB().delete_credential(cred_id)

    def test(self, cred_id: str) -> bool:
        """Verify that a stored credential can be decrypted successfully.

        Args:
            cred_id: The credential record ID to test.

        Returns:
            ``True`` if the credential exists and its encrypted value can
            be decrypted with the current master key.
        """
        record: Optional[dict] = AgentWorkflowDB().get_credential(cred_id)
        if record is None:
            return False
        return self.decrypt_value(record["encrypted_value"]) is not None
