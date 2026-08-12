from __future__ import annotations

import json
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


AI_RUNTIME_STATE_SCHEMA_VERSION = "ai-runtime-state-v1"
AI_RUNTIME_PUBLIC_SCHEMA_VERSION = "ai-runtime-public-state-v1"
AI_RUNTIME_ERROR_SCHEMA_VERSION = "ai-runtime-state-error-v1"
AI_VERIFICATION_ATTESTATION_VERSION = "ai-verification-attestation-v1"

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
        "token",
    }
)
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
    def __init__(self, code: str, safe_message: str, *, retryable: bool) -> None:
        if code not in _ERROR_CODES:
            raise ValueError("unsupported AI runtime state error code")
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": AI_RUNTIME_ERROR_SCHEMA_VERSION,
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.retryable,
        }


def _error(code: str) -> AIRuntimeStateError:
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
    return AIRuntimeStateError(code, message, retryable=retryable)


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
            if not isinstance(self.attestation, Mapping) or set(self.attestation) != _ATTESTATION_KEYS:
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

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": AI_RUNTIME_PUBLIC_SCHEMA_VERSION,
            "revision": self.revision,
            "provider_id": self.provider_id,
            "task_models": dict(self.task_models),
            "configured": self.configured,
            "verified": self.verified,
            "availability": self.availability,
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


def _claims(
    selection: AIRuntimeSelection,
    credential_generation: int,
) -> bytes:
    value = {
        "schema_version": AI_VERIFICATION_ATTESTATION_VERSION,
        "registry_version": PROVIDER_REGISTRY_VERSION,
        "provider_id": selection.provider_id,
        "task_models": dict(selection.task_models),
        "credential_generation": credential_generation,
        "selection_revision": selection.revision,
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
    ) -> None:
        self._store = store
        self._credential_states = credential_states
        self._verifier = verifier
        self._signer = signer

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
        token = value.get("token")
        if not isinstance(token, str) or not token:
            return False
        try:
            return self._signer.verify(token, _claims(selection, credential.generation)) is True
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

    def record_verification(self) -> AIRuntimePublicState:
        selection = self._read()
        credential = self._credential(selection.provider_id)
        if not credential.configured:
            raise _error("ai_runtime_verification_failed")
        try:
            for model in sorted(set(selection.task_models.values())):
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
            attested_selection = AIRuntimeSelection(
                selection.provider_id,
                selection.task_models,
                selection.revision + 1,
            )
            claims = _claims(attested_selection, credential.generation)
            token = self._signer.issue(claims)
            if not isinstance(token, str) or not token:
                raise _error("ai_runtime_verification_failed")
        except AIRuntimeStateError:
            raise
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

    def resolve_runtime(self) -> ResolvedAIRuntime:
        selection = self._read()
        credential = self._credential(selection.provider_id)
        profile = trusted_provider_profile(selection.provider_id)
        if not credential.configured:
            raise _error("ai_runtime_verification_required")
        if profile.runtime_activation == RUNTIME_ACTIVATION_LEGACY:
            activation = "legacy_compatible"
        elif self._attestation_valid(selection, credential):
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


__all__ = [
    "AIRuntimePublicState",
    "AIRuntimeSelection",
    "AIRuntimeStateError",
    "AIRuntimeStateService",
    "AtomicAIRuntimeStateStore",
    "BackendCredentialState",
    "CredentialStateProvider",
    "ModelCapabilityResult",
    "ProviderCapabilityVerifier",
    "ResolvedAIRuntime",
    "VerificationAttestationSigner",
]
