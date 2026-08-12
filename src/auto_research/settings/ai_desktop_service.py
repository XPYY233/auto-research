from __future__ import annotations

import threading
from typing import Any, Mapping, Protocol

from auto_research.ai.provider_registry import (
    CAPABILITY_AGENT,
    CAPABILITY_STRUCTURED_JSON,
    CAPABILITY_TOOL_CALLING,
    TrustedProviderRegistryError,
    trusted_provider_profile,
    trusted_provider_public_catalog,
)

from .ai_runtime_state import (
    AIRuntimeStateService,
    BackendCredentialState,
)


AI_DESKTOP_CATALOG_SCHEMA_VERSION = "ai-desktop-catalog-v1"
AI_CREDENTIAL_STATUS_SCHEMA_VERSION = "ai-credential-status-v1"
AI_CAPABILITY_TEST_CONSENT_VERSION = "ai-capability-test-consent-v1"
_FIXED_CREDENTIAL_REFS = {
    "deepseek": "deepseek.default",
    "openai": "openai.default",
}
_TEST_KEYS = frozenset({"consent_version", "expected_revision"})
_PATCH_KEYS = frozenset({"provider_id", "task_models", "expected_revision"})
_ERRORS = {
    "ai_desktop_request_invalid": ("AI 设置请求内容无效。", False),
    "ai_desktop_provider_untrusted": ("所选 AI 提供商尚未通过安全审计。", False),
    "ai_desktop_credential_invalid": ("AI 密钥格式无效。", False),
    "ai_desktop_credential_unavailable": ("本机 AI 密钥暂时无法读取或保存。", True),
    "ai_desktop_consent_required": ("请先确认可能产生费用的能力测试。", False),
    "ai_desktop_test_busy": ("该 AI 设置正在进行能力测试，请稍后再试。", True),
}
_TEST_GUARD = threading.Lock()
_TESTS_IN_PROGRESS: set[tuple[str, int]] = set()


class AIDesktopServiceError(RuntimeError):
    def __init__(self, code: str) -> None:
        if code not in _ERRORS:
            raise ValueError("unsupported AI desktop service error code")
        message, retryable = _ERRORS[code]
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": "ai-desktop-error-v1",
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.retryable,
        }


class CredentialManager(Protocol):
    """Platform secure store with atomic key mutation plus generation increment."""

    def state_for(self, provider_id: str) -> BackendCredentialState: ...

    def resolve(self, credential_ref: str) -> str | None: ...

    def save(
        self, *, provider_id: str, credential_ref: str, api_key: str
    ) -> BackendCredentialState:
        """Atomically replace the key and strictly increment its generation."""
        ...

    def delete(
        self, *, provider_id: str, credential_ref: str
    ) -> BackendCredentialState:
        """Atomically delete the key and strictly increment its generation."""
        ...


def _provider(provider_id: object) -> str:
    if not isinstance(provider_id, str):
        raise AIDesktopServiceError("ai_desktop_provider_untrusted")
    try:
        return trusted_provider_profile(provider_id).provider_id
    except TrustedProviderRegistryError as exc:
        raise AIDesktopServiceError("ai_desktop_provider_untrusted") from exc


def _credential_ref(provider_id: str) -> str:
    try:
        return _FIXED_CREDENTIAL_REFS[provider_id]
    except KeyError as exc:
        raise AIDesktopServiceError("ai_desktop_provider_untrusted") from exc


class AIDesktopService:
    """Platform-neutral renderer orchestration for AI settings and verification."""

    REQUIRED_AGENT_CAPABILITIES = (
        CAPABILITY_AGENT,
        CAPABILITY_STRUCTURED_JSON,
        CAPABILITY_TOOL_CALLING,
    )

    def __init__(
        self,
        *,
        runtime_state: AIRuntimeStateService,
        credential_manager: CredentialManager,
    ) -> None:
        self._runtime_state = runtime_state
        self._credentials = credential_manager

    def catalog(self) -> dict[str, object]:
        current = self.get()
        return {
            "schema_version": AI_DESKTOP_CATALOG_SCHEMA_VERSION,
            "providers": list(
                trusted_provider_public_catalog(self.REQUIRED_AGENT_CAPABILITIES)
            ),
            "current": current,
            "capability_test": self._test_plan(current),
        }

    def get(self) -> dict[str, object]:
        return self._runtime_state.get().public_dict()

    def patch(self, payload: Mapping[str, Any]) -> dict[str, object]:
        if not isinstance(payload, Mapping) or set(payload) != _PATCH_KEYS:
            raise AIDesktopServiceError("ai_desktop_request_invalid")
        expected_revision = payload.get("expected_revision")
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise AIDesktopServiceError("ai_desktop_request_invalid")
        return self._runtime_state.patch_selection(
            {
                "provider_id": payload.get("provider_id"),
                "task_models": payload.get("task_models"),
            },
            expected_revision=expected_revision,
        ).public_dict()

    def credential_status(self, provider_id: str) -> dict[str, object]:
        provider = _provider(provider_id)
        state = self._credential_state(provider)
        return {
            "schema_version": AI_CREDENTIAL_STATUS_SCHEMA_VERSION,
            "provider_id": provider,
            "configured": state.configured,
        }

    def credential_save(self, provider_id: str, api_key: object) -> dict[str, object]:
        provider = _provider(provider_id)
        if (
            not isinstance(api_key, str)
            or not 8 <= len(api_key) <= 4096
            or api_key != api_key.strip()
            or any(character in api_key for character in ("\x00", "\r", "\n"))
        ):
            raise AIDesktopServiceError("ai_desktop_credential_invalid")
        before = self._credential_state(provider)
        try:
            after = self._credentials.save(
                provider_id=provider,
                credential_ref=_credential_ref(provider),
                api_key=api_key,
            )
        except Exception as exc:
            raise AIDesktopServiceError("ai_desktop_credential_unavailable") from exc
        self._validate_credential_change(provider, before, after, configured=True)
        return self.credential_status(provider)

    def credential_delete(self, provider_id: str) -> dict[str, object]:
        provider = _provider(provider_id)
        before = self._credential_state(provider)
        try:
            after = self._credentials.delete(
                provider_id=provider,
                credential_ref=_credential_ref(provider),
            )
        except Exception as exc:
            raise AIDesktopServiceError("ai_desktop_credential_unavailable") from exc
        self._validate_credential_change(provider, before, after, configured=False)
        return self.credential_status(provider)

    def test(self, provider_id: str, payload: Mapping[str, Any]) -> dict[str, object]:
        provider = _provider(provider_id)
        if not isinstance(payload, Mapping) or set(payload) != _TEST_KEYS:
            raise AIDesktopServiceError("ai_desktop_request_invalid")
        if payload.get("consent_version") != AI_CAPABILITY_TEST_CONSENT_VERSION:
            raise AIDesktopServiceError("ai_desktop_consent_required")
        expected_revision = payload.get("expected_revision")
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise AIDesktopServiceError("ai_desktop_request_invalid")
        key = (provider, expected_revision)
        with _TEST_GUARD:
            if key in _TESTS_IN_PROGRESS:
                raise AIDesktopServiceError("ai_desktop_test_busy")
            _TESTS_IN_PROGRESS.add(key)
        try:
            return self._runtime_state.record_verification(
                expected_provider_id=provider,
                expected_revision=expected_revision,
            ).public_dict()
        finally:
            with _TEST_GUARD:
                _TESTS_IN_PROGRESS.discard(key)

    def _test_plan(self, current: Mapping[str, Any]) -> dict[str, object]:
        models = current.get("task_models")
        if not isinstance(models, Mapping):
            raise AIDesktopServiceError("ai_desktop_request_invalid")
        unique_model_count = len(set(models.values()))
        return {
            "consent_version": AI_CAPABILITY_TEST_CONSENT_VERSION,
            "provider_id": current.get("provider_id"),
            "expected_revision": current.get("revision"),
            "unique_model_count": unique_model_count,
            "maximum_model_calls": 2 * unique_model_count,
        }

    def _credential_state(self, provider_id: str) -> BackendCredentialState:
        try:
            state = self._credentials.state_for(provider_id)
        except Exception as exc:
            raise AIDesktopServiceError("ai_desktop_credential_unavailable") from exc
        if (
            not isinstance(state, BackendCredentialState)
            or state.credential_ref != _credential_ref(provider_id)
        ):
            raise AIDesktopServiceError("ai_desktop_credential_unavailable")
        return state

    def _validate_credential_change(
        self,
        provider_id: str,
        before: BackendCredentialState,
        after: BackendCredentialState,
        *,
        configured: bool,
    ) -> None:
        if (
            not isinstance(after, BackendCredentialState)
            or after.credential_ref != _credential_ref(provider_id)
            or after.configured is not configured
            or after.generation <= before.generation
        ):
            raise AIDesktopServiceError("ai_desktop_credential_unavailable")


__all__ = [
    "AI_CAPABILITY_TEST_CONSENT_VERSION",
    "AIDesktopService",
    "AIDesktopServiceError",
    "CredentialManager",
]
