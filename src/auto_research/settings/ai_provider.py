from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from auto_research.ai.openai_compatible import OpenAICompatibleSettings
from auto_research.ai.provider_registry import (
    CAPABILITY_AGENT,
    CAPABILITY_STRUCTURED_JSON,
    CAPABILITY_TOOL_CALLING,
    MODEL_VALIDATION_BUILTIN,
    RUNTIME_ACTIVATION_CONNECTION_REQUIRED,
    TASK_IDS,
    TrustedProviderRegistryError,
    trusted_provider_profile,
    trusted_provider_public_catalog,
    validated_task_models,
)


SETTINGS_SCHEMA_VERSION = "ai-provider-settings-v1"
SELECTION_SCHEMA_VERSION = "ai-provider-selection-v1"
_CREDENTIAL_REF = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_ALLOWED_SELECTION_FIELDS = frozenset(
    {"schema_version", "provider_id", "credential_ref", "task_models"}
)


class AIProviderSettingsError(ValueError):
    def __init__(self, code: str, safe_message: str):
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": "settings-error-v1",
            "code": self.code,
            "message": self.safe_message,
            "retryable": False,
        }


class CredentialResolver(Protocol):
    def resolve(self, credential_ref: str) -> str | None: ...


@dataclass(frozen=True)
class AIProviderSelection:
    provider_id: str
    credential_ref: str
    task_models: Mapping[str, str]
    schema_version: str = SELECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SELECTION_SCHEMA_VERSION:
            raise AIProviderSettingsError(
                "ai_settings_schema_unsupported", "AI 设置版本不受支持。"
            )
        try:
            profile = trusted_provider_profile(self.provider_id)
            models = validated_task_models(profile.provider_id, self.task_models)
        except TrustedProviderRegistryError as exc:
            raise AIProviderSettingsError(exc.code, exc.safe_message) from exc
        credential_ref = str(self.credential_ref or "").strip()
        if not _CREDENTIAL_REF.fullmatch(credential_ref):
            raise AIProviderSettingsError(
                "ai_credential_ref_invalid", "AI 凭据引用格式无效。"
            )
        object.__setattr__(self, "provider_id", profile.provider_id)
        object.__setattr__(self, "credential_ref", credential_ref)
        object.__setattr__(self, "task_models", models)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AIProviderSelection":
        if not isinstance(value, Mapping):
            raise AIProviderSettingsError("ai_settings_invalid", "AI 设置格式无效。")
        unknown = set(value) - _ALLOWED_SELECTION_FIELDS
        if unknown:
            raise AIProviderSettingsError(
                "ai_settings_fields_invalid", "AI 设置包含不允许的字段。"
            )
        schema = str(value.get("schema_version") or SELECTION_SCHEMA_VERSION)
        if schema != SELECTION_SCHEMA_VERSION:
            raise AIProviderSettingsError(
                "ai_settings_schema_unsupported", "AI 设置版本不受支持。"
            )
        provider_id = str(value.get("provider_id") or "").strip().casefold()
        try:
            profile = trusted_provider_profile(provider_id)
        except ValueError as exc:
            raise AIProviderSettingsError(
                "ai_provider_untrusted", "所选 AI 提供商尚未通过应用安全审计。"
            ) from exc
        credential_ref = str(value.get("credential_ref") or "").strip()
        if not _CREDENTIAL_REF.fullmatch(credential_ref):
            raise AIProviderSettingsError(
                "ai_credential_ref_invalid", "AI 凭据引用格式无效。"
            )
        raw_models = value.get("task_models")
        if raw_models in (None, {}):
            raw_models = profile.default_task_models
        return cls(
            provider_id=profile.provider_id,
            credential_ref=credential_ref,
            task_models=raw_models,
        )

    @classmethod
    def default(cls) -> "AIProviderSelection":
        return cls.from_mapping(
            {
                "provider_id": "deepseek",
                "credential_ref": "deepseek.default",
            }
        )

    def persistable_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "provider_id": self.provider_id,
            "credential_ref": self.credential_ref,
            "task_models": dict(self.task_models),
        }


class AISettingsService:
    """Validate provider choices without storing or serializing a secret."""

    REQUIRED_AGENT_CAPABILITIES = (
        CAPABILITY_AGENT,
        CAPABILITY_STRUCTURED_JSON,
        CAPABILITY_TOOL_CALLING,
    )

    def catalog(self) -> tuple[dict[str, object], ...]:
        return trusted_provider_public_catalog(self.REQUIRED_AGENT_CAPABILITIES)

    def validate(self, value: Mapping[str, Any] | AIProviderSelection) -> AIProviderSelection:
        selection = value if isinstance(value, AIProviderSelection) else AIProviderSelection.from_mapping(value)
        profile = trusted_provider_profile(selection.provider_id)
        missing = [
            capability
            for capability in self.REQUIRED_AGENT_CAPABILITIES
            if not profile.capabilities.supports(capability)
        ]
        if missing:
            raise AIProviderSettingsError(
                "ai_provider_capability_missing",
                "所选 AI 提供商不支持图书管理员所需能力。",
            )
        return selection

    def public_settings(
        self,
        value: Mapping[str, Any] | AIProviderSelection,
        *,
        credential_configured: bool,
    ) -> dict[str, object]:
        selection = self.validate(value)
        profile = trusted_provider_profile(selection.provider_id)
        verified = (
            profile.model_validation == MODEL_VALIDATION_BUILTIN
            and profile.runtime_activation != RUNTIME_ACTIVATION_CONNECTION_REQUIRED
        )
        available = bool(credential_configured) and verified
        return {
            "schema_version": SETTINGS_SCHEMA_VERSION,
            "provider": profile.public_dict(),
            "configured": available,
            "available": available,
            "credential_configured": bool(credential_configured),
            "verified": verified,
            "availability": "available" if available else (
                "verification_required" if not verified else "credential_required"
            ),
            "task_models": dict(selection.task_models),
        }

    def runtime_settings(
        self,
        value: Mapping[str, Any] | AIProviderSelection,
        credential_resolver: CredentialResolver,
        *,
        timeout_seconds: int = 180,
        max_attempts: int = 2,
        retry_base_seconds: int = 0,
        verification_mode: bool = False,
    ) -> OpenAICompatibleSettings:
        selection = self.validate(value)
        profile = trusted_provider_profile(selection.provider_id)
        if (
            (
                profile.model_validation != MODEL_VALIDATION_BUILTIN
                or profile.runtime_activation == RUNTIME_ACTIVATION_CONNECTION_REQUIRED
            )
            and verification_mode is not True
        ):
            raise AIProviderSettingsError(
                "ai_model_verification_required",
                "所选 AI 模型尚未通过后端连通性与能力验证。",
            )
        try:
            api_key = credential_resolver.resolve(selection.credential_ref)
        except Exception as exc:
            raise AIProviderSettingsError(
                "ai_credential_unavailable", "本机 AI 凭据暂时不可用。"
            ) from exc
        return OpenAICompatibleSettings(
            provider_id=selection.provider_id,
            credential_ref=selection.credential_ref,
            task_models=selection.task_models,
            api_key=api_key or None,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            retry_base_seconds=retry_base_seconds,
            verification_mode=verification_mode,
        )
