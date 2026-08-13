from __future__ import annotations

import base64
import hashlib
import hmac
import threading
from contextlib import contextmanager
from pathlib import Path

from credential_manager import AI_ATTESTATION_KEY_TARGET, CredentialBackend, CredentialKeyProvider
from settings_store import WindowsAtomicDesktopSettingsStore


AI_RUNTIME_STATE_FILENAME = "ai-runtime-state-v1.json"


class WindowsAtomicAIRuntimeStateStore:
    """CAS store for non-secret provider/model selection state."""

    def __init__(self, state_directory: Path | str) -> None:
        self._store = WindowsAtomicDesktopSettingsStore(
            state_directory,
            filename=AI_RUNTIME_STATE_FILENAME,
        )

    @property
    def path(self) -> Path:
        return self._store.path

    def read(self):
        return self._store.read()

    def compare_and_swap(self, *, expected_revision: int, value):
        return self._store.compare_and_swap(
            expected_revision=expected_revision,
            value=value,
        )


class WindowsVerificationAttestationSigner:
    """Keep the HMAC key in Credential Manager, never in renderer-visible state."""

    def __init__(self, backend: CredentialBackend) -> None:
        self._keys = CredentialKeyProvider(
            backend,
            target=AI_ATTESTATION_KEY_TARGET,
        )

    def issue(self, claims: bytes) -> str:
        if not isinstance(claims, bytes) or not claims:
            raise ValueError("AI attestation claims are invalid")
        digest = hmac.new(self._keys.get_or_create_key(), claims, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def verify(self, token: str, claims: bytes) -> bool:
        if not isinstance(token, str) or not token or not isinstance(claims, bytes):
            return False
        try:
            supplied = token.encode("ascii")
        except UnicodeEncodeError:
            return False
        return hmac.compare_digest(supplied, self.issue(claims).encode("ascii"))


class WindowsAIExecutionLeaseAuthority:
    def __init__(self, execution_lock: threading.RLock) -> None:
        self._lock = execution_lock

    @contextmanager
    def acquire(self, _action):
        with self._lock:
            yield


__all__ = [
    "AI_RUNTIME_STATE_FILENAME",
    "WindowsAIExecutionLeaseAuthority",
    "WindowsAtomicAIRuntimeStateStore",
    "WindowsVerificationAttestationSigner",
]
