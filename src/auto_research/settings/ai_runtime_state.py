from __future__ import annotations

import json
import re
import time
import threading
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from auto_research.ai.provider_registry import (
    CAPABILITY_STRUCTURED_JSON,
    CAPABILITY_TOOL_CALLING,
    PROVIDER_REGISTRY_VERSION,
    RUNTIME_ACTIVATION_CONNECTION_REQUIRED,
    RUNTIME_ACTIVATION_LEGACY,
    TASK_IDS,
    trusted_provider_profile,
    validated_task_models,
)
from auto_research.ai.openai_compatible import AIProviderError


AI_RUNTIME_STATE_SCHEMA_VERSION = "ai-runtime-state-v1"
AI_RUNTIME_PUBLIC_SCHEMA_VERSION = "ai-runtime-public-state-v1"
AI_RUNTIME_ERROR_SCHEMA_VERSION = "ai-runtime-state-error-v1"
AI_VERIFICATION_ATTESTATION_VERSION = "ai-verification-attestation-v1"
AI_VERIFICATION_TTL_SECONDS = 15 * 60
AI_BUSINESS_VERIFICATION_TTL_SECONDS = 15 * 60
MAX_BUSINESS_VERIFICATION_RECORDS = 64
AI_BUSINESS_SCOPES = frozenset(
    {"librarian", "selected_evidence_chat", "literature_extraction", "personal_suggestion"}
)
AI_BUSINESS_TASKS = MappingProxyType({
    "librarian": ("librarian_planning", "librarian_synthesis"),
    "selected_evidence_chat": ("extraction",),
    "literature_extraction": ("analysis", "extraction"),
    "personal_suggestion": ("analysis",),
})

_PATCH_KEYS = frozenset({"provider_id", "task_models"})
_STORED_KEYS = frozenset(
    {"schema_version", "revision", "provider_id", "task_models", "attestation"}
)
_ATTESTATION_KEYS = frozenset(
    {
        "schema_version",
        "registry_version",
        "provider_id",
        "task_models",
        "credential_generation",
        "selection_revision",
        "issued_at",
        "expires_at",
        "token",
    }
)
_LEGACY_ATTESTATION_KEYS = _ATTESTATION_KEYS - {"issued_at", "expires_at"}
_ERROR_CODES = frozenset(
    {
        "ai_runtime_state_invalid",
        "ai_runtime_revision_conflict",
        "ai_runtime_store_unavailable",
        "ai_runtime_verification_failed",
        "ai_runtime_verification_required",
    }
)


class AIRuntimeStateError(RuntimeError):
    def __init__(
        self,
        code: str,
        safe_message: str,
        *,
        retryable: bool,
        cause_code: str = "",
        stage: str = "",
        next_action: str = "",
    ) -> None:
        if code not in _ERROR_CODES:
            raise ValueError("unsupported AI runtime state error code")
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable
        self.cause_code = (
            cause_code if re.fullmatch(r"[a-z][a-z0-9_]{2,95}", cause_code) else ""
        )
        self.stage = stage if re.fullmatch(r"[a-z][a-z0-9_]{2,95}", stage) else ""
        self.next_action = (
            next_action
            if re.fullmatch(r"[a-z][a-z0-9_]{2,95}", next_action)
            else ""
        )

    def public_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": AI_RUNTIME_ERROR_SCHEMA_VERSION,
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.retryable,
        }
        if self.cause_code:
            result["cause_code"] = self.cause_code
        if self.stage:
            result["stage"] = self.stage
        if self.next_action:
            result["next_action"] = self.next_action
        return result


def _error(
    code: str,
    *,
    safe_message: str | None = None,
    cause_code: str = "",
    stage: str = "",
    next_action: str = "",
) -> AIRuntimeStateError:
    messages = {
        "ai_runtime_state_invalid": ("AI 运行设置内容无效。", False),
        "ai_runtime_revision_conflict": (
            "AI 设置已在其他操作中更新，请重新加载后再试。",
            True,
        ),
        "ai_runtime_store_unavailable": ("AI 运行设置暂时无法读取或保存。", True),
        "ai_runtime_verification_failed": ("AI 提供商能力验证未通过。", True),
        "ai_runtime_verification_required": ("AI 提供商尚未完成能力验证。", False),
    }
    message, retryable = messages[code]
    return AIRuntimeStateError(
        code,
        safe_message or message,
        retryable=retryable,
        cause_code=cause_code,
        stage=stage,
        next_action=next_action,
    )


@dataclass(frozen=True)
class BackendCredentialState:
    credential_ref: str
    generation: int
    configured: bool

    def __post_init__(self) -> None:
        if not isinstance(self.credential_ref, str) or not self.credential_ref:
            raise _error("ai_runtime_store_unavailable")
        if (
            isinstance(self.generation, bool)
            or not isinstance(self.generation, int)
            or self.generation < 0
            or not isinstance(self.configured, bool)
        ):
            raise _error("ai_runtime_store_unavailable")


@dataclass(frozen=True)
class ModelCapabilityResult:
    provider_id: str
    model: str
    structured_json: bool
    tool_calling: bool


class AtomicAIRuntimeStateStore(Protocol):
    def read(self) -> Mapping[str, Any] | None: ...

    def compare_and_swap(
        self, *, expected_revision: int, value: Mapping[str, Any]
    ) -> bool: ...


class CredentialStateProvider(Protocol):
    def state_for(self, provider_id: str) -> BackendCredentialState: ...


class ProviderCapabilityVerifier(Protocol):
    def verify_connection(
        self, *, provider_id: str, model: str, credential_ref: str
    ) -> ModelCapabilityResult: ...

    def verify_model(
        self,
        *,
        provider_id: str,
        model: str,
        credential_ref: str,
        required_capabilities: tuple[str, ...],
    ) -> ModelCapabilityResult: ...


class VerificationAttestationSigner(Protocol):
    def issue(self, claims: bytes) -> str: ...

    def verify(self, token: str, claims: bytes) -> bool: ...


class Clock(Protocol):
    def now(self) -> int: ...


class SystemClock:
    def now(self) -> int:
        return int(time.time())


@dataclass(frozen=True)
class AIRuntimeSelection:
    provider_id: str
    task_models: Mapping[str, str]
    revision: int
    attestation: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise _error("ai_runtime_store_unavailable")
        try:
            models = validated_task_models(self.provider_id, self.task_models)
        except Exception as exc:
            raise _error("ai_runtime_store_unavailable") from exc
        object.__setattr__(self, "provider_id", trusted_provider_profile(self.provider_id).provider_id)
        object.__setattr__(self, "task_models", models)
        if self.attestation is not None:
            if (
                not isinstance(self.attestation, Mapping)
                or set(self.attestation)
                not in {_ATTESTATION_KEYS, _LEGACY_ATTESTATION_KEYS}
            ):
                raise _error("ai_runtime_store_unavailable")
            object.__setattr__(self, "attestation", MappingProxyType(dict(self.attestation)))

    def stored_dict(self) -> dict[str, object]:
        return {
            "schema_version": AI_RUNTIME_STATE_SCHEMA_VERSION,
            "revision": self.revision,
            "provider_id": self.provider_id,
            "task_models": dict(self.task_models),
            "attestation": dict(self.attestation) if self.attestation else None,
        }


@dataclass(frozen=True)
class AIRuntimePublicState:
    provider_id: str
    task_models: Mapping[str, str]
    configured: bool
    verified: bool
    availability: str
    revision: int
    verified_until: int | None = None

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": AI_RUNTIME_PUBLIC_SCHEMA_VERSION,
            "revision": self.revision,
            "provider_id": self.provider_id,
            "task_models": dict(self.task_models),
            "configured": self.configured,
            "verified": self.verified,
            "availability": self.availability,
            "verified_until": self.verified_until,
        }


@dataclass(frozen=True)
class ResolvedAIRuntime:
    """Backend-only selection; never project this object to the renderer."""

    provider_id: str
    task_models: Mapping[str, str]
    credential_ref: str
    credential_generation: int
    selection_revision: int
    activation: str


@dataclass(frozen=True)
class RuntimeActionBinding:
    """Backend-only identity used to bind prepared outbound AI actions."""

    provider_id: str
    task_models: Mapping[str, str]
    credential_ref: str
    credential_generation: int
    selection_revision: int
    activation: str


def _claims(
    selection: AIRuntimeSelection,
    credential_generation: int,
    *,
    issued_at: int,
    expires_at: int,
) -> bytes:
    value = {
        "schema_version": AI_VERIFICATION_ATTESTATION_VERSION,
        "registry_version": PROVIDER_REGISTRY_VERSION,
        "provider_id": selection.provider_id,
        "task_models": dict(selection.task_models),
        "credential_generation": credential_generation,
        "selection_revision": selection.revision,
        "issued_at": issued_at,
        "expires_at": expires_at,
    }
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


class AIRuntimeStateService:
    REQUIRED_CAPABILITIES = (CAPABILITY_STRUCTURED_JSON, CAPABILITY_TOOL_CALLING)

    def __init__(
        self,
        *,
        store: AtomicAIRuntimeStateStore,
        credential_states: CredentialStateProvider,
        verifier: ProviderCapabilityVerifier,
        signer: VerificationAttestationSigner,
        clock: Clock | None = None,
    ) -> None:
        self._store = store
        self._credential_states = credential_states
        self._verifier = verifier
        self._signer = signer
        self._clock = clock or SystemClock()
        self._business_lock = threading.RLock()
        self._business_verified: dict[
            tuple[str, int, int, str, tuple[tuple[str, str], ...]], int
        ] = {}
        self._models_verified: dict[
            tuple[str, int, int, str, tuple[str, ...]], int
        ] = {}

    def _now(self) -> int:
        try:
            value = self._clock.now()
        except Exception as exc:
            raise _error("ai_runtime_store_unavailable") from exc
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise _error("ai_runtime_store_unavailable")
        return value

    def _read(self) -> AIRuntimeSelection:
        try:
            raw = self._store.read()
        except Exception as exc:
            raise _error("ai_runtime_store_unavailable") from exc
        if raw is None:
            profile = trusted_provider_profile("deepseek")
            return AIRuntimeSelection("deepseek", profile.default_task_models, 0)
        if (
            not isinstance(raw, Mapping)
            or set(raw) != _STORED_KEYS
            or raw.get("schema_version") != AI_RUNTIME_STATE_SCHEMA_VERSION
        ):
            raise _error("ai_runtime_store_unavailable")
        return AIRuntimeSelection(
            provider_id=raw.get("provider_id"),
            task_models=raw.get("task_models"),
            revision=raw.get("revision"),
            attestation=raw.get("attestation"),
        )

    def _credential(self, provider_id: str) -> BackendCredentialState:
        try:
            return self._credential_states.state_for(provider_id)
        except AIRuntimeStateError:
            raise
        except Exception as exc:
            raise _error("ai_runtime_store_unavailable") from exc

    def _attestation_valid(
        self,
        selection: AIRuntimeSelection,
        credential: BackendCredentialState,
    ) -> bool:
        value = selection.attestation
        if value is None:
            return False
        expected = {
            "schema_version": AI_VERIFICATION_ATTESTATION_VERSION,
            "registry_version": PROVIDER_REGISTRY_VERSION,
            "provider_id": selection.provider_id,
            "task_models": dict(selection.task_models),
            "credential_generation": credential.generation,
            "selection_revision": selection.revision,
        }
        if any(value.get(key) != item for key, item in expected.items()):
            return False
        issued_at = value.get("issued_at")
        expires_at = value.get("expires_at")
        if (
            isinstance(issued_at, bool)
            or not isinstance(issued_at, int)
            or isinstance(expires_at, bool)
            or not isinstance(expires_at, int)
            or issued_at < 0
            or expires_at - issued_at != AI_VERIFICATION_TTL_SECONDS
        ):
            return False
        now = self._now()
        if now < issued_at or now >= expires_at:
            return False
        token = value.get("token")
        if not isinstance(token, str) or not token:
            return False
        try:
            return self._signer.verify(
                token,
                _claims(
                    selection,
                    credential.generation,
                    issued_at=issued_at,
                    expires_at=expires_at,
                ),
            ) is True
        except Exception:
            return False

    def get(self) -> AIRuntimePublicState:
        selection = self._read()
        credential = self._credential(selection.provider_id)
        profile = trusted_provider_profile(selection.provider_id)
        if profile.runtime_activation == RUNTIME_ACTIVATION_LEGACY:
            verified = False
            availability = "legacy_compatible" if credential.configured else "credential_required"
        else:
            verified = credential.configured and self._attestation_valid(selection, credential)
            availability = "available" if verified else (
                "verification_required" if credential.configured else "credential_required"
            )
        return AIRuntimePublicState(
            provider_id=selection.provider_id,
            task_models=selection.task_models,
            configured=credential.configured,
            verified=verified,
            availability=availability,
            revision=selection.revision,
            verified_until=(
                int(selection.attestation["expires_at"])
                if verified and selection.attestation is not None
                else None
            ),
        )

    def patch_selection(
        self,
        payload: Mapping[str, Any],
        *,
        expected_revision: int,
    ) -> AIRuntimePublicState:
        if (
            not isinstance(payload, Mapping)
            or set(payload) != _PATCH_KEYS
            or isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise _error("ai_runtime_state_invalid")
        current = self._read()
        if current.revision != expected_revision:
            raise _error("ai_runtime_revision_conflict")
        try:
            models = validated_task_models(payload.get("provider_id"), payload.get("task_models"))
            provider_id = trusted_provider_profile(payload.get("provider_id")).provider_id
        except Exception as exc:
            raise _error("ai_runtime_state_invalid") from exc
        updated = AIRuntimeSelection(provider_id, models, current.revision + 1)
        try:
            swapped = self._store.compare_and_swap(
                expected_revision=current.revision,
                value=updated.stored_dict(),
            )
        except Exception as exc:
            raise _error("ai_runtime_store_unavailable") from exc
        if swapped is not True:
            raise _error("ai_runtime_revision_conflict")
        return self.get()

    def record_verification(
        self,
        *,
        expected_provider_id: str | None = None,
        expected_revision: int | None = None,
    ) -> AIRuntimePublicState:
        selection = self._read()
        if (
            expected_provider_id is not None
            and selection.provider_id != expected_provider_id
        ) or (
            expected_revision is not None
            and selection.revision != expected_revision
        ):
            raise _error("ai_runtime_revision_conflict")
        credential = self._credential(selection.provider_id)
        if not credential.configured:
            raise _error("ai_runtime_verification_failed")
        try:
            model = selection.task_models["analysis"]
            result = self._verifier.verify_connection(
                provider_id=selection.provider_id,
                model=model,
                credential_ref=credential.credential_ref,
            )
            if (
                result.provider_id != selection.provider_id
                or result.model != model
                or result.structured_json is not True
            ):
                raise _error("ai_runtime_verification_failed")
            attested_selection = AIRuntimeSelection(
                selection.provider_id,
                selection.task_models,
                selection.revision + 1,
            )
            issued_at = self._now()
            expires_at = issued_at + AI_VERIFICATION_TTL_SECONDS
            claims = _claims(
                attested_selection,
                credential.generation,
                issued_at=issued_at,
                expires_at=expires_at,
            )
            token = self._signer.issue(claims)
            if not isinstance(token, str) or not token:
                raise _error("ai_runtime_verification_failed")
        except AIRuntimeStateError:
            raise
        except AIProviderError as exc:
            next_action = {
                "ai_provider_not_configured": "save_credential",
                "ai_provider_unavailable": "retry_connection",
                "ai_provider_capability_missing": "select_supported_model",
            }.get(exc.code, "check_provider_configuration")
            raise _error(
                "ai_runtime_verification_failed",
                safe_message=exc.safe_message,
                cause_code=exc.code,
                stage="connection_verification",
                next_action=next_action,
            ) from exc
        except Exception as exc:
            raise _error("ai_runtime_verification_failed") from exc
        attestation = json.loads(claims.decode("utf-8"))
        attestation["token"] = token
        updated = AIRuntimeSelection(
            attested_selection.provider_id,
            attested_selection.task_models,
            attested_selection.revision,
            attestation,
        )
        try:
            swapped = self._store.compare_and_swap(
                expected_revision=selection.revision,
                value=updated.stored_dict(),
            )
        except Exception as exc:
            raise _error("ai_runtime_store_unavailable") from exc
        if swapped is not True:
            raise _error("ai_runtime_revision_conflict")
        return self.get()

    def _business_key(
        self,
        selection: AIRuntimeSelection,
        credential: BackendCredentialState,
        scope: str,
    ) -> tuple[str, int, int, str, tuple[tuple[str, str], ...]]:
        return (
            selection.provider_id,
            selection.revision,
            credential.generation,
            scope,
            tuple((task, selection.task_models[task]) for task in AI_BUSINESS_TASKS[scope]),
        )

    def _model_key(
        self,
        selection: AIRuntimeSelection,
        credential: BackendCredentialState,
        model: str,
    ) -> tuple[str, int, int, str, tuple[str, ...]]:
        return (
            selection.provider_id,
            selection.revision,
            credential.generation,
            model,
            self.REQUIRED_CAPABILITIES,
        )

    def business_verification(self, scope: str) -> int | None:
        if scope not in AI_BUSINESS_SCOPES:
            raise _error("ai_runtime_state_invalid")
        selection = self._read()
        credential = self._credential(selection.provider_id)
        if not credential.configured or not self._attestation_valid(selection, credential):
            return None
        key = self._business_key(selection, credential, scope)
        now = self._now()
        with self._business_lock:
            for cached, expiry in tuple(self._business_verified.items()):
                if expiry <= now:
                    self._business_verified.pop(cached, None)
            expiry = self._business_verified.get(key)
        return expiry if expiry is not None and now < expiry else None

    def record_business_verification(
        self,
        scope: str,
        *,
        expected_provider_id: str,
        expected_revision: int,
    ) -> int:
        if scope not in AI_BUSINESS_SCOPES:
            raise _error("ai_runtime_state_invalid")
        selection = self._read()
        credential = self._credential(selection.provider_id)
        if (
            selection.provider_id != expected_provider_id
            or selection.revision != expected_revision
            or not credential.configured
            or not self._attestation_valid(selection, credential)
        ):
            raise _error("ai_runtime_verification_required")
        key = self._business_key(selection, credential, scope)
        now = self._now()
        with self._business_lock:
            cached = self._business_verified.get(key)
            if cached is not None and now < cached:
                return cached
            for cached_key, cached_expiry in tuple(self._models_verified.items()):
                if cached_expiry <= now:
                    self._models_verified.pop(cached_key, None)
            models = sorted(
                {selection.task_models[task] for task in AI_BUSINESS_TASKS[scope]}
            )
            pending_models = [
                model
                for model in models
                if self._models_verified.get(
                    self._model_key(selection, credential, model), 0
                )
                <= now
            ]
        try:
            for model in pending_models:
                result = self._verifier.verify_model(
                    provider_id=selection.provider_id,
                    model=model,
                    credential_ref=credential.credential_ref,
                    required_capabilities=self.REQUIRED_CAPABILITIES,
                )
                if (
                    result.provider_id != selection.provider_id
                    or result.model != model
                    or result.structured_json is not True
                    or result.tool_calling is not True
                ):
                    raise _error("ai_runtime_verification_failed")
        except AIRuntimeStateError:
            raise
        except AIProviderError as exc:
            next_action = {
                "ai_provider_not_configured": "save_credential",
                "ai_provider_unavailable": "retry_connection",
                "ai_provider_capability_missing": "select_supported_model",
            }.get(exc.code, "check_provider_configuration")
            raise _error(
                "ai_runtime_verification_failed",
                safe_message=exc.safe_message,
                cause_code=exc.code,
                stage="business_capability_verification",
                next_action=next_action,
            ) from exc
        except Exception as exc:
            raise _error("ai_runtime_verification_failed") from exc
        current_selection = self._read()
        current_credential = self._credential(current_selection.provider_id)
        if (
            current_selection.provider_id != selection.provider_id
            or current_selection.revision != selection.revision
            or dict(current_selection.task_models) != dict(selection.task_models)
            or current_credential.generation != credential.generation
            or not current_credential.configured
            or not self._attestation_valid(current_selection, current_credential)
        ):
            raise _error("ai_runtime_verification_required")
        expiry = now + AI_BUSINESS_VERIFICATION_TTL_SECONDS
        with self._business_lock:
            for cached_key, cached_expiry in tuple(self._business_verified.items()):
                if cached_expiry <= now:
                    self._business_verified.pop(cached_key, None)
            if key not in self._business_verified and len(self._business_verified) >= MAX_BUSINESS_VERIFICATION_RECORDS:
                oldest = min(self._business_verified, key=self._business_verified.get)
                self._business_verified.pop(oldest, None)
            for model in pending_models:
                model_key = self._model_key(
                    current_selection, current_credential, model
                )
                if (
                    model_key not in self._models_verified
                    and len(self._models_verified) >= MAX_BUSINESS_VERIFICATION_RECORDS
                ):
                    oldest = min(self._models_verified, key=self._models_verified.get)
                    self._models_verified.pop(oldest, None)
                self._models_verified[model_key] = expiry
            self._business_verified[key] = expiry
        return expiry

    def require_business_verification(self, scope: str) -> None:
        if self.business_verification(scope) is None:
            raise _error("ai_runtime_verification_required")

    def resolve_runtime(self) -> ResolvedAIRuntime:
        selection = self._read()
        credential = self._credential(selection.provider_id)
        if not credential.configured:
            raise _error("ai_runtime_verification_required")
        if self._attestation_valid(selection, credential):
            activation = "connection_verified"
        else:
            raise _error("ai_runtime_verification_required")
        return ResolvedAIRuntime(
            selection.provider_id,
            selection.task_models,
            credential.credential_ref,
            credential.generation,
            selection.revision,
            activation,
        )

    def action_binding(self, scope: str | None = None) -> RuntimeActionBinding:
        selection = self._read()
        credential = self._credential(selection.provider_id)
        if not credential.configured:
            raise _error("ai_runtime_verification_required")
        if self._attestation_valid(selection, credential):
            activation = "connection_verified"
        else:
            activation = "unverified_configured"
        if scope is not None:
            self.require_business_verification(scope)
        return RuntimeActionBinding(
            selection.provider_id,
            selection.task_models,
            credential.credential_ref,
            credential.generation,
            selection.revision,
            activation,
        )


__all__ = [
    "AIRuntimePublicState",
    "AIRuntimeSelection",
    "AIRuntimeStateError",
    "AIRuntimeStateService",
    "AtomicAIRuntimeStateStore",
    "AI_VERIFICATION_TTL_SECONDS",
    "AI_BUSINESS_SCOPES",
    "AI_BUSINESS_TASKS",
    "AI_BUSINESS_VERIFICATION_TTL_SECONDS",
    "MAX_BUSINESS_VERIFICATION_RECORDS",
    "BackendCredentialState",
    "CredentialStateProvider",
    "Clock",
    "ModelCapabilityResult",
    "ProviderCapabilityVerifier",
    "ResolvedAIRuntime",
    "RuntimeActionBinding",
    "SystemClock",
    "VerificationAttestationSigner",
]
