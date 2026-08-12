from __future__ import annotations

from typing import Protocol

from auto_research.settings.ai_runtime_state import AIRuntimeStateService

from .openai_compatible import (
    AIProviderNotConfigured,
    OpenAICompatibleClient,
    OpenAICompatibleSettings,
)


class RuntimeCredentialResolver(Protocol):
    def resolve(self, credential_ref: str) -> str | None: ...


class RuntimeAIClientFactory:
    """Create ordinary clients only from the backend's current activation."""

    def __init__(
        self,
        *,
        runtime_state: AIRuntimeStateService,
        credential_resolver: RuntimeCredentialResolver,
        session=None,
    ) -> None:
        self._runtime_state = runtime_state
        self._credentials = credential_resolver
        self._session = session

    def create(self) -> OpenAICompatibleClient:
        runtime = self._runtime_state.resolve_runtime()
        try:
            api_key = self._credentials.resolve(runtime.credential_ref)
        except Exception as exc:
            raise AIProviderNotConfigured("AI 提供商尚未配置本机凭据。") from exc
        if not isinstance(api_key, str) or not api_key.strip():
            raise AIProviderNotConfigured("AI 提供商尚未配置本机凭据。")
        settings = OpenAICompatibleSettings.from_resolved_runtime(
            runtime,
            api_key=api_key,
        )
        return OpenAICompatibleClient(settings, session=self._session)


__all__ = ["RuntimeAIClientFactory", "RuntimeCredentialResolver"]
