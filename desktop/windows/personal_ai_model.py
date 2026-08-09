from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Protocol

from auto_research.ai.deepseek import (
    DeepSeekClient,
    DeepSeekNotConfigured,
    DeepSeekSettings,
)


class RuntimeSecretResolver(Protocol):
    def resolve_for_runtime(self) -> str | None: ...


class RequestJSONClient(Protocol):
    def request_json(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]: ...


class WindowsDeepSeekPersonalSuggestionModel:
    """Resolve the current user-owned Credential Manager secret per AI action.

    The renderer never receives this object or the resolved key.  Resolving on
    each call also means save/delete operations take effect without restarting
    the application, while preview and offline search remain independent of AI.
    """

    def __init__(
        self,
        resolver: RuntimeSecretResolver,
        *,
        client_factory: Callable[[DeepSeekSettings], RequestJSONClient] = DeepSeekClient,
    ) -> None:
        self._resolver = resolver
        self._client_factory = client_factory

    def request_json(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        api_key = self._resolver.resolve_for_runtime()
        if not api_key:
            raise DeepSeekNotConfigured("DeepSeek 尚未配置")
        settings = replace(
            DeepSeekSettings.from_env(),
            api_key=api_key,
            credential_source="Windows Credential Manager",
        )
        return self._client_factory(settings).request_json(messages, **kwargs)
