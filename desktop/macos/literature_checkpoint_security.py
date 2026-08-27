from __future__ import annotations

import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from secure_history import (
    APP_IDENTIFIER,
    HistoryKeyProvider,
    LocalFileHistoryKeyProvider,
    MacKeychainKeyProvider,
)


CHECKPOINT_SECURITY_SCHEMA_VERSION = "literature-checkpoint-security-error-v1"
CHECKPOINT_AAD_DOMAIN = (
    f"{APP_IDENTIFIER}:literature-task-checkpoint-sealer:v1".encode("ascii")
)
CHECKPOINT_KEYCHAIN_SERVICE = f"{APP_IDENTIFIER}.literature-task-checkpoint.v1"
CHECKPOINT_KEYCHAIN_ACCOUNT = "literature-task-checkpoint-encryption-key-v1"
CHECKPOINT_LOCAL_KEY_FILENAME = "literature-task-checkpoint-v1.key"

_ENVELOPE_MAGIC = b"ARLCP\x01"
_NONCE_BYTES = 12
_MAX_AAD_BYTES = 2_048
# The same authenticated platform authority seals both the compact mutable
# checkpoint envelope (currently capped below 48 MiB by the core store) and
# the separately persisted immutable PDF snapshot (capped at 128 MiB).  Keep
# the cryptographic adapter's ceiling large enough for either reviewed caller;
# each domain store still enforces its own tighter limit before invoking it.
_MAX_PLAINTEXT_BYTES = 129 * 1024 * 1024
_MAX_CIPHERTEXT_BYTES = _MAX_PLAINTEXT_BYTES + 64


class LiteratureCheckpointSecurityError(RuntimeError):
    _MESSAGES = {
        "literature_checkpoint_security_invalid": "提取任务安全数据无效。",
        "literature_checkpoint_key_unavailable": "提取任务加密密钥不可用。",
        "literature_checkpoint_seal_failed": "无法安全保存提取任务。",
        "literature_checkpoint_open_failed": "无法安全读取提取任务。",
    }

    def __init__(self, code: str) -> None:
        if code not in self._MESSAGES:
            code = "literature_checkpoint_security_invalid"
        self.code = code
        self.safe_message = self._MESSAGES[code]
        super().__init__(self.safe_message)

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": CHECKPOINT_SECURITY_SCHEMA_VERSION,
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.code == "literature_checkpoint_key_unavailable",
        }


class AuthenticatedCheckpointSealer:
    """AES-256-GCM adapter over an injected platform key authority."""

    def __init__(self, key_provider: HistoryKeyProvider) -> None:
        self._key_provider = key_provider

    def seal(self, plaintext: bytes, *, associated_data: bytes) -> bytes:
        _validate_bytes(
            plaintext,
            maximum=_MAX_PLAINTEXT_BYTES,
            code="literature_checkpoint_security_invalid",
        )
        aad = _domain_aad(associated_data)
        key = self._key()
        try:
            nonce = os.urandom(_NONCE_BYTES)
            ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
        except Exception:
            raise LiteratureCheckpointSecurityError(
                "literature_checkpoint_seal_failed"
            ) from None
        return _ENVELOPE_MAGIC + nonce + ciphertext

    def open(self, ciphertext: bytes, *, associated_data: bytes) -> bytes:
        _validate_bytes(
            ciphertext,
            maximum=_MAX_CIPHERTEXT_BYTES,
            code="literature_checkpoint_security_invalid",
        )
        minimum = len(_ENVELOPE_MAGIC) + _NONCE_BYTES + 16
        if len(ciphertext) < minimum or not ciphertext.startswith(_ENVELOPE_MAGIC):
            raise LiteratureCheckpointSecurityError("literature_checkpoint_open_failed")
        aad = _domain_aad(associated_data)
        key = self._key()
        offset = len(_ENVELOPE_MAGIC)
        nonce = ciphertext[offset : offset + _NONCE_BYTES]
        encrypted = ciphertext[offset + _NONCE_BYTES :]
        try:
            return AESGCM(key).decrypt(nonce, encrypted, aad)
        except (InvalidTag, TypeError, ValueError):
            raise LiteratureCheckpointSecurityError(
                "literature_checkpoint_open_failed"
            ) from None
        except Exception:
            raise LiteratureCheckpointSecurityError(
                "literature_checkpoint_open_failed"
            ) from None

    def _key(self) -> bytes:
        try:
            key = self._key_provider.get_or_create_key()
        except Exception:
            raise LiteratureCheckpointSecurityError(
                "literature_checkpoint_key_unavailable"
            ) from None
        if not isinstance(key, bytes) or len(key) != 32:
            raise LiteratureCheckpointSecurityError(
                "literature_checkpoint_key_unavailable"
            )
        return key


def default_authenticated_checkpoint_sealer(
    *,
    stable_signed: bool = False,
    key_provider: HistoryKeyProvider | None = None,
    private_directory: Path | None = None,
) -> AuthenticatedCheckpointSealer:
    """Create the Mac platform adapter without wiring it into composition.

    Ad-hoc and preview builds use a dedicated 0600 local key through the
    existing LocalFileHistoryKeyProvider. Formally signed builds may select the
    independent Keychain service/account. AI provider credentials, Librarian
    history, and research-memory keys are separate authorities.
    """

    if key_provider is None:
        if stable_signed:
            key_provider = MacKeychainKeyProvider(
                service=CHECKPOINT_KEYCHAIN_SERVICE,
                account=CHECKPOINT_KEYCHAIN_ACCOUNT,
            )
        else:
            root = private_directory or (
                Path.home()
                / "Library"
                / "Application Support"
                / "Auto Research"
                / "Private Data"
            )
            key_provider = LocalFileHistoryKeyProvider(
                Path(root) / CHECKPOINT_LOCAL_KEY_FILENAME
            )
    return AuthenticatedCheckpointSealer(key_provider)


def _domain_aad(associated_data: bytes) -> bytes:
    _validate_bytes(
        associated_data,
        maximum=_MAX_AAD_BYTES,
        code="literature_checkpoint_security_invalid",
        allow_empty=False,
    )
    return CHECKPOINT_AAD_DOMAIN + b"\x00" + associated_data


def _validate_bytes(
    value: object,
    *,
    maximum: int,
    code: str,
    allow_empty: bool = True,
) -> None:
    if (
        not isinstance(value, bytes)
        or len(value) > maximum
        or (not allow_empty and not value)
    ):
        raise LiteratureCheckpointSecurityError(code)


__all__ = [
    "AuthenticatedCheckpointSealer",
    "CHECKPOINT_AAD_DOMAIN",
    "CHECKPOINT_KEYCHAIN_ACCOUNT",
    "CHECKPOINT_KEYCHAIN_SERVICE",
    "LiteratureCheckpointSecurityError",
    "default_authenticated_checkpoint_sealer",
]
