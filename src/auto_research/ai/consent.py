from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence


AI_CONSENT_SCHEMA_VERSION = "ai-consent-v1"
AI_CONSENT_ERROR_SCHEMA_VERSION = "ai-consent-error-v1"
AI_CONSENT_TTL_SECONDS = 5 * 60
MAX_ACTION_BYTES = 64 * 1024
MAX_ACTION_DEPTH = 12
MAX_ACTION_NODES = 2_000

DISCLOSURE_VERSIONS = {
    "librarian": "librarian-disclosure-v1",
    "literature_extraction": "literature-extraction-disclosure-v1",
    "personal_suggestion": "personal-suggestion-disclosure-v1",
    "selected_evidence_chat": "selected-evidence-chat-disclosure-v1",
}
AI_CONSENT_SCOPES = frozenset(DISCLOSURE_VERSIONS)

_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_SENSITIVE_KEY_RE = re.compile(
    r"(?:^|_)(?:api_?key|secret|password|credential|token|nonce|path)(?:$|_)",
    re.IGNORECASE,
)
_LOCAL_VALUE_RE = re.compile(
    r"(?:^|[\s='\"])(?:~[/\\]|/(?:Users|private|tmp|var|etc|usr|root|srv|mnt|media|opt|Applications|Library|System)(?:[/\\]|$)|/[^/\s]+[/\\][^\s]*|[A-Za-z]:[\\/]|\\\\|file:|sqlite:)",
    re.IGNORECASE,
)
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


class RuntimeProviderAuthority(Protocol):
    def get(self) -> Any: ...


class ConsentClock(Protocol):
    def now(self) -> int: ...


class SystemConsentClock:
    def now(self) -> int:
        return int(time.time())


@dataclass(frozen=True)
class _ConsentRecord:
    session_id: str
    provider_id: str
    provider_revision: int
    scope: str
    disclosure_version: str
    issued_at: int
    expires_at: int
    action_digest: str
    signature: str


def _canonical_action(value: Any, *, depth: int, counter: list[int]) -> Any:
    counter[0] += 1
    if counter[0] > MAX_ACTION_NODES or depth > MAX_ACTION_DEPTH:
        raise AIConsentError("ai_consent_action_invalid")
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and _LOCAL_VALUE_RE.search(value):
            raise AIConsentError("ai_consent_action_invalid")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AIConsentError("ai_consent_action_invalid")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or _SENSITIVE_KEY_RE.search(key):
                raise AIConsentError("ai_consent_action_invalid")
            normalized[key] = _canonical_action(
                item, depth=depth + 1, counter=counter
            )
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            _canonical_action(item, depth=depth + 1, counter=counter)
            for item in value
        ]
    raise AIConsentError("ai_consent_action_invalid")


def canonical_action_digest(action: Any) -> str:
    """Hash one bounded, path-free DTO exactly as it will be sent externally."""

    normalized = _canonical_action(action, depth=0, counter=[0])
    try:
        payload = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AIConsentError("ai_consent_action_invalid") from exc
    if len(payload) > MAX_ACTION_BYTES:
        raise AIConsentError("ai_consent_action_invalid")
    return hashlib.sha256(payload).hexdigest()


def _claims_bytes(nonce: str, record: _ConsentRecord) -> bytes:
    return json.dumps(
        {
            "schema_version": AI_CONSENT_SCHEMA_VERSION,
            "nonce": nonce,
            "session_id": record.session_id,
            "provider_id": record.provider_id,
            "provider_revision": record.provider_revision,
            "scope": record.scope,
            "disclosure_version": record.disclosure_version,
            "issued_at": record.issued_at,
            "expires_at": record.expires_at,
            "action_digest": record.action_digest,
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
        runtime_state: RuntimeProviderAuthority,
        clock: ConsentClock | None = None,
        secret_key: bytes | None = None,
    ) -> None:
        key = secret_key if secret_key is not None else secrets.token_bytes(32)
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("AI consent key must contain at least 32 bytes")
        self._runtime_state = runtime_state
        self._clock = clock or SystemConsentClock()
        self._key = key
        self._lock = threading.Lock()
        self._records: dict[str, _ConsentRecord] = {}
        self._consumed: dict[str, int] = {}

    def issue(
        self,
        *,
        session_id: str,
        scope: str,
        action: Any,
    ) -> dict[str, object]:
        session = self._session(session_id)
        disclosure_version = self._disclosure(scope)
        provider_id, provider_revision = self._provider_state()
        action_digest = canonical_action_digest(action)
        issued_at = self._now()
        expires_at = issued_at + AI_CONSENT_TTL_SECONDS
        nonce = secrets.token_urlsafe(32)
        unsigned = _ConsentRecord(
            session,
            provider_id,
            provider_revision,
            scope,
            disclosure_version,
            issued_at,
            expires_at,
            action_digest,
            "",
        )
        signature = hmac.new(
            self._key, _claims_bytes(nonce, unsigned), hashlib.sha256
        ).hexdigest()
        record = _ConsentRecord(**{**unsigned.__dict__, "signature": signature})
        with self._lock:
            self._cleanup_locked(issued_at)
            self._records[nonce] = record
        return {
            "schema_version": AI_CONSENT_SCHEMA_VERSION,
            "scope": scope,
            "provider_id": provider_id,
            "disclosure_version": disclosure_version,
            "nonce": nonce,
            "expires_at": expires_at,
        }

    def consume(
        self,
        *,
        nonce: str,
        session_id: str,
        scope: str,
        action: Any,
    ) -> None:
        if not isinstance(nonce, str) or not nonce:
            raise AIConsentError("ai_consent_invalid")
        session = self._session(session_id)
        disclosure_version = self._disclosure(scope)
        action_digest = canonical_action_digest(action)
        provider_id, provider_revision = self._provider_state()
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
                session,
                provider_id,
                provider_revision,
                scope,
                disclosure_version,
                record.issued_at,
                record.expires_at,
                action_digest,
                record.signature,
            )
            expected_signature = hmac.new(
                self._key, _claims_bytes(nonce, candidate), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(record.signature, expected_signature):
                raise AIConsentError("ai_consent_invalid")
            self._records.pop(nonce, None)
            self._consumed[nonce] = record.expires_at

    def _provider_state(self) -> tuple[str, int]:
        try:
            state = self._runtime_state.get()
            provider_id = state.provider_id
            revision = state.revision
        except Exception as exc:
            raise AIConsentError("ai_consent_state_unavailable") from exc
        if (
            not isinstance(provider_id, str)
            or not provider_id
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 0
        ):
            raise AIConsentError("ai_consent_state_unavailable")
        return provider_id, revision

    def _now(self) -> int:
        try:
            value = self._clock.now()
        except Exception as exc:
            raise AIConsentError("ai_consent_state_unavailable") from exc
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AIConsentError("ai_consent_state_unavailable")
        return value

    @staticmethod
    def _session(session_id: object) -> str:
        if not isinstance(session_id, str) or _SESSION_RE.fullmatch(session_id) is None:
            raise AIConsentError("ai_consent_invalid")
        return session_id

    @staticmethod
    def _disclosure(scope: object) -> str:
        if not isinstance(scope, str) or scope not in DISCLOSURE_VERSIONS:
            raise AIConsentError("ai_consent_scope_invalid")
        return DISCLOSURE_VERSIONS[scope]

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
    "DISCLOSURE_VERSIONS",
    "AIConsentError",
    "AIConsentService",
    "ConsentClock",
    "RuntimeProviderAuthority",
    "SystemConsentClock",
    "canonical_action_digest",
]
