from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Protocol


AI_CONSENT_SCHEMA_VERSION = "ai-consent-v1"
AI_CONSENT_ERROR_SCHEMA_VERSION = "ai-consent-error-v1"
AI_CONSENT_TTL_SECONDS = 5 * 60
MAX_ACTIVE_CONSENTS = 64
MAX_ACTIVE_CONSENTS_PER_SESSION = 16
DISCLOSURE_VERSIONS = {
    "capability_test": "capability-test-disclosure-v1",
    "librarian": "librarian-disclosure-v1",
    "literature_extraction": "literature-extraction-disclosure-v1",
    "personal_suggestion": "personal-suggestion-disclosure-v1",
    "selected_evidence_chat": "selected-evidence-chat-disclosure-v1",
}
AI_CONSENT_SCOPES = frozenset(DISCLOSURE_VERSIONS)

_ERRORS = {
    "ai_consent_invalid": ("AI 知情同意凭证无效。", False),
    "ai_consent_scope_invalid": ("AI 使用场景无法识别。", False),
    "ai_consent_action_invalid": ("AI 外发内容不符合安全限制。", False),
    "ai_consent_expired": ("AI 知情同意已过期，请重新确认。", False),
    "ai_consent_replayed": ("AI 知情同意已使用，请重新确认。", False),
    "ai_consent_state_unavailable": ("AI 知情同意服务暂时不可用。", True),
}


class AIConsentError(RuntimeError):
    def __init__(self, code: str) -> None:
        if code not in _ERRORS:
            raise ValueError("unsupported AI consent error code")
        message, retryable = _ERRORS[code]
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": AI_CONSENT_ERROR_SCHEMA_VERSION,
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.retryable,
        }


class ConsentClock(Protocol):
    def now(self) -> int: ...


class SystemConsentClock:
    def now(self) -> int:
        return int(time.time())


@dataclass(frozen=True)
class _ConsentRecord:
    action_id: str
    session_id: str
    provider_id: str
    provider_revision: int
    credential_generation: int
    scope: str
    disclosure_version: str
    issued_at: int
    expires_at: int
    action_digest: str
    signature: str


@dataclass(frozen=True)
class PreparedConsentBinding:
    action_id: str
    session_id: str
    provider_id: str
    provider_revision: int
    credential_generation: int
    scope: str
    disclosure_version: str
    manifest_digest: str
    prepared_expires_at: int


def _claims_bytes(nonce: str, record: _ConsentRecord) -> bytes:
    return json.dumps(
        {
            "schema_version": AI_CONSENT_SCHEMA_VERSION,
            "action_id": record.action_id,
            "nonce": nonce,
            "session_id": record.session_id,
            "provider_id": record.provider_id,
            "provider_revision": record.provider_revision,
            "credential_generation": record.credential_generation,
            "scope": record.scope,
            "disclosure_version": record.disclosure_version,
            "issued_at": record.issued_at,
            "expires_at": record.expires_at,
            "manifest_digest": record.action_digest,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class AIConsentService:
    """Process-local, one-time consent authority for all outbound AI scopes."""

    def __init__(
        self,
        *,
        clock: ConsentClock | None = None,
        secret_key: bytes | None = None,
    ) -> None:
        key = secret_key if secret_key is not None else secrets.token_bytes(32)
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("AI consent key must contain at least 32 bytes")
        self._clock = clock or SystemConsentClock()
        self._key = key
        self._lock = threading.Lock()
        self._records: dict[str, _ConsentRecord] = {}
        self._consumed: dict[str, int] = {}

    def issue(
        self,
        *,
        binding: PreparedConsentBinding,
    ) -> dict[str, object]:
        self._validate_binding(binding)
        issued_at = self._now()
        expires_at = min(
            issued_at + AI_CONSENT_TTL_SECONDS,
            binding.prepared_expires_at,
        )
        if expires_at <= issued_at:
            raise AIConsentError("ai_consent_expired")
        nonce = secrets.token_urlsafe(32)
        unsigned = _ConsentRecord(
            binding.action_id,
            binding.session_id,
            binding.provider_id,
            binding.provider_revision,
            binding.credential_generation,
            binding.scope,
            binding.disclosure_version,
            issued_at,
            expires_at,
            binding.manifest_digest,
            "",
        )
        signature = hmac.new(
            self._key, _claims_bytes(nonce, unsigned), hashlib.sha256
        ).hexdigest()
        record = _ConsentRecord(**{**unsigned.__dict__, "signature": signature})
        with self._lock:
            self._cleanup_locked(issued_at)
            if any(
                record.action_id == binding.action_id
                for record in self._records.values()
            ):
                raise AIConsentError("ai_consent_replayed")
            session_count = sum(
                record.session_id == binding.session_id
                for record in self._records.values()
            )
            if (
                len(self._records) >= MAX_ACTIVE_CONSENTS
                or session_count >= MAX_ACTIVE_CONSENTS_PER_SESSION
            ):
                raise AIConsentError("ai_consent_state_unavailable")
            self._records[nonce] = record
        return {
            "schema_version": AI_CONSENT_SCHEMA_VERSION,
            "scope": binding.scope,
            "provider_id": binding.provider_id,
            "disclosure_version": binding.disclosure_version,
            "nonce": nonce,
            "expires_at": expires_at,
        }

    def consume(
        self,
        *,
        nonce: str,
        binding: PreparedConsentBinding,
    ) -> None:
        if not isinstance(nonce, str) or not nonce:
            raise AIConsentError("ai_consent_invalid")
        self._validate_binding(binding)
        now = self._now()
        with self._lock:
            replay_expiry = self._consumed.get(nonce)
            if replay_expiry is not None:
                if now < replay_expiry:
                    raise AIConsentError("ai_consent_replayed")
                self._consumed.pop(nonce, None)
            record = self._records.get(nonce)
            if record is None:
                raise AIConsentError("ai_consent_invalid")
            if now >= record.expires_at or now < record.issued_at:
                self._records.pop(nonce, None)
                raise AIConsentError("ai_consent_expired")
            candidate = _ConsentRecord(
                binding.action_id,
                binding.session_id,
                binding.provider_id,
                binding.provider_revision,
                binding.credential_generation,
                binding.scope,
                binding.disclosure_version,
                record.issued_at,
                record.expires_at,
                binding.manifest_digest,
                record.signature,
            )
            expected_signature = hmac.new(
                self._key, _claims_bytes(nonce, candidate), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(record.signature, expected_signature):
                raise AIConsentError("ai_consent_invalid")
            self._records.pop(nonce, None)
            self._consumed[nonce] = record.expires_at

    def _now(self) -> int:
        try:
            value = self._clock.now()
        except Exception as exc:
            raise AIConsentError("ai_consent_state_unavailable") from exc
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AIConsentError("ai_consent_state_unavailable")
        return value

    @staticmethod
    def _validate_binding(binding: object) -> None:
        # The binding is process-internal and every field is validated below.
        # Do not use exact class identity as a security boundary: frozen macOS
        # applications may import an otherwise identical dataclass through a
        # PyInstaller package alias, making ``isinstance`` fail even though the
        # signed claims are unchanged.  Structural validation remains strict
        # and mappings or other untyped external values still fail closed.
        required = (
            "action_id",
            "session_id",
            "provider_id",
            "provider_revision",
            "credential_generation",
            "scope",
            "disclosure_version",
            "manifest_digest",
            "prepared_expires_at",
        )
        if isinstance(binding, dict) or any(
            not hasattr(binding, field) for field in required
        ):
            raise AIConsentError("ai_consent_invalid")
        if (
            not isinstance(binding.action_id, str)
            or not binding.action_id
            or not isinstance(binding.session_id, str)
            or not binding.session_id
            or not isinstance(binding.provider_id, str)
            or not binding.provider_id
            or not isinstance(binding.scope, str)
            or binding.scope not in DISCLOSURE_VERSIONS
            or not isinstance(binding.disclosure_version, str)
            or binding.disclosure_version != DISCLOSURE_VERSIONS[binding.scope]
            or isinstance(binding.provider_revision, bool)
            or not isinstance(binding.provider_revision, int)
            or binding.provider_revision < 0
            or isinstance(binding.credential_generation, bool)
            or not isinstance(binding.credential_generation, int)
            or binding.credential_generation < 0
            or not isinstance(binding.manifest_digest, str)
            or len(binding.manifest_digest) != 64
            or any(character not in "0123456789abcdef" for character in binding.manifest_digest)
            or isinstance(binding.prepared_expires_at, bool)
            or not isinstance(binding.prepared_expires_at, int)
            or binding.prepared_expires_at < 0
        ):
            raise AIConsentError("ai_consent_invalid")

    def _cleanup_locked(self, now: int) -> None:
        for nonce, record in tuple(self._records.items()):
            if now >= record.expires_at:
                self._records.pop(nonce, None)
        for nonce, expires_at in tuple(self._consumed.items()):
            if now >= expires_at:
                self._consumed.pop(nonce, None)


__all__ = [
    "AI_CONSENT_SCHEMA_VERSION",
    "AI_CONSENT_SCOPES",
    "AI_CONSENT_TTL_SECONDS",
    "MAX_ACTIVE_CONSENTS",
    "MAX_ACTIVE_CONSENTS_PER_SESSION",
    "DISCLOSURE_VERSIONS",
    "AIConsentError",
    "AIConsentService",
    "ConsentClock",
    "PreparedConsentBinding",
    "SystemConsentClock",
]
