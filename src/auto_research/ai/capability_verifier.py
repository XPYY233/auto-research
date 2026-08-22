from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from .openai_compatible import (
    AIProviderError,
    AIProviderNotConfigured,
    AIProviderResponseError,
    OpenAICompatibleClient,
    OpenAICompatibleSettings,
)
from .provider_registry import (
    CAPABILITY_STRUCTURED_JSON,
    CAPABILITY_TOOL_CALLING,
    TASK_IDS,
    trusted_provider_profile,
)

if TYPE_CHECKING:
    from auto_research.settings.ai_runtime_state import ModelCapabilityResult


class BackendCredentialResolver(Protocol):
    def resolve(self, credential_ref: str) -> str | None: ...


class OpenAICompatibleCapabilityVerifier:
    """Verify one audited provider/model using two bounded fixed probes."""

    REQUIRED_CAPABILITIES = frozenset(
        {CAPABILITY_STRUCTURED_JSON, CAPABILITY_TOOL_CALLING}
    )

    def __init__(self, credential_resolver: BackendCredentialResolver, *, session=None):
        self._credential_resolver = credential_resolver
        self._session = session

    def verify_model(
        self,
        *,
        provider_id: str,
        model: str,
        credential_ref: str,
        required_capabilities: tuple[str, ...],
    ) -> "ModelCapabilityResult":
        from auto_research.settings.ai_runtime_state import ModelCapabilityResult

        try:
            profile = trusted_provider_profile(provider_id)
            if set(required_capabilities) != self.REQUIRED_CAPABILITIES:
                raise AIProviderResponseError("AI 能力验证请求不受支持。")
            task_models = {task: model for task in TASK_IDS}
            api_key = self._credential_resolver.resolve(credential_ref)
            if not isinstance(api_key, str) or not api_key.strip():
                raise AIProviderNotConfigured("AI 提供商尚未配置本机凭据。")
            settings = OpenAICompatibleSettings(
                provider_id=profile.provider_id,
                task_models=task_models,
                api_key=api_key,
                credential_ref=credential_ref,
                timeout_seconds=30,
                max_attempts=1,
                retry_base_seconds=0,
                verification_mode=True,
            )
            client = OpenAICompatibleClient(settings, session=self._session)
            structured_json = client.verify_structured_json_capability()
            tool_calling = client.verify_tool_calling_capability()
        except AIProviderError:
            raise
        except Exception as exc:
            raise AIProviderResponseError("AI 提供商能力验证未能完成。") from exc
        if structured_json is not True or tool_calling is not True:
            raise AIProviderResponseError("AI 提供商能力验证未通过。")
        return ModelCapabilityResult(
            provider_id=profile.provider_id,
            model=model,
            structured_json=True,
            tool_calling=True,
        )

    def verify_connection(
        self,
        *,
        provider_id: str,
        model: str,
        credential_ref: str,
    ) -> "ModelCapabilityResult":
        """Run one minimal JSON probe; business capabilities are verified later."""

        from auto_research.settings.ai_runtime_state import ModelCapabilityResult

        try:
            profile = trusted_provider_profile(provider_id)
            if model not in profile.allowed_task_models["analysis"]:
                raise AIProviderResponseError("AI 连接验证模型不受支持。")
            api_key = self._credential_resolver.resolve(credential_ref)
            if not isinstance(api_key, str) or not api_key.strip():
                raise AIProviderNotConfigured("AI 提供商尚未配置本机凭据。")
            client = OpenAICompatibleClient(
                OpenAICompatibleSettings(
                    provider_id=profile.provider_id,
                    task_models={task: model for task in TASK_IDS},
                    api_key=api_key,
                    credential_ref=credential_ref,
                    timeout_seconds=30,
                    max_attempts=1,
                    retry_base_seconds=0,
                    verification_mode=True,
                ),
                session=self._session,
            )
            structured = client.verify_structured_json_capability()
        except AIProviderError:
            raise
        except Exception as exc:
            raise AIProviderResponseError("AI 提供商连接验证未能完成。") from exc
        if structured is not True:
            raise AIProviderResponseError("AI 提供商连接验证未通过。")
        return ModelCapabilityResult(profile.provider_id, model, True, False)


__all__ = ["BackendCredentialResolver", "OpenAICompatibleCapabilityVerifier"]
