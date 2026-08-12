from __future__ import annotations

import base64
import hashlib
import hmac
import os
import stat
from pathlib import Path
from typing import Any, Mapping

from desktop_settings_store import MacAtomicDesktopSettingsStore


DEFAULT_STATE_DIRECTORY = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Auto Research"
    / "State"
)
DEFAULT_AI_RUNTIME_STATE_PATH = DEFAULT_STATE_DIRECTORY / "ai-runtime-state-v1.json"
DEFAULT_AI_ATTESTATION_KEY_PATH = DEFAULT_STATE_DIRECTORY / "ai-attestation-v1.key"


class MacAtomicAIRuntimeStateStore:
    """Use the audited device-local CAS store for non-secret AI selection state."""

    def __init__(self, path: Path | str = DEFAULT_AI_RUNTIME_STATE_PATH) -> None:
        self._store = MacAtomicDesktopSettingsStore(path)

    @property
    def path(self) -> Path:
        return self._store.path

    def read(self) -> Mapping[str, Any] | None:
        return self._store.read()

    def compare_and_swap(
        self,
        *,
        expected_revision: int,
        value: Mapping[str, Any],
    ) -> bool:
        return self._store.compare_and_swap(
            expected_revision=expected_revision,
            value=value,
        )


class MacVerificationAttestationSigner:
    """Sign backend-only provider verification claims with one local HMAC key."""

    def __init__(self, path: Path | str = DEFAULT_AI_ATTESTATION_KEY_PATH) -> None:
        self.path = Path(path).expanduser()

    def _directory(self) -> Path:
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True)
        metadata = directory.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise OSError("AI attestation directory is unsafe")
        os.chmod(directory, 0o700)
        return directory

    def _read_key(self) -> bytes:
        self._directory()
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.path, flags)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size != 32:
                raise OSError("AI attestation key is invalid")
            key = os.read(descriptor, 33)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if (
            len(key) != 32
            or (before.st_dev, before.st_ino, before.st_size)
            != (after.st_dev, after.st_ino, after.st_size)
        ):
            raise OSError("AI attestation key changed while reading")
        return key

    def _key(self) -> bytes:
        try:
            return self._read_key()
        except FileNotFoundError:
            key = os.urandom(32)
            self._directory()
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                descriptor = os.open(self.path, flags, 0o600)
            except FileExistsError:
                return self._read_key()
            try:
                written = os.write(descriptor, key)
                if written != len(key):
                    raise OSError("AI attestation key write was incomplete")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.chmod(self.path, 0o600)
            return key

    def issue(self, claims: bytes) -> str:
        if not isinstance(claims, bytes) or not claims:
            raise ValueError("AI attestation claims are invalid")
        digest = hmac.new(self._key(), claims, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def verify(self, token: str, claims: bytes) -> bool:
        if not isinstance(token, str) or not token or not isinstance(claims, bytes):
            return False
        try:
            supplied = token.encode("ascii")
        except UnicodeEncodeError:
            return False
        expected = self.issue(claims).encode("ascii")
        return hmac.compare_digest(supplied, expected)


__all__ = [
    "DEFAULT_AI_ATTESTATION_KEY_PATH",
    "DEFAULT_AI_RUNTIME_STATE_PATH",
    "MacAtomicAIRuntimeStateStore",
    "MacVerificationAttestationSigner",
]
