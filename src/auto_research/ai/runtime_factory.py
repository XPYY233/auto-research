from __future__ import annotations

from contextlib import contextmanager
from typing import ContextManager, Protocol

from auto_research.settings.ai_runtime_state import AIRuntimeStateService

from .openai_compatible import (
    AIProviderNotConfigured,
    OpenAICompatibleClient,
    OpenAICompatibleSettings,
)


class RuntimeCredentialResolver(Protocol):
    def resolve(self, credential_ref: str) -> str | None: ...


class BoundCredentialResolver(Protocol):
    def resolve_bound(
        self, credential_ref: str, expected_generation: int
    ) -> str | None: ...


class BoundRuntimeAction(Protocol):
    provider_id: str
    runtime_revision: int
    credential_generation: int
    runtime_activation: str
    runtime_task_models: tuple[tuple[str, str], ...]


class AIExecutionLeaseAuthority(Protocol):
    """Platform lock shared by runtime selection and credential mutations."""

    def acquire(self, action: BoundRuntimeAction) -> ContextManager[None]: ...


class AIRuntimeBindingError(AIProviderNotConfigured):
    code = "ai_runtime_binding_stale"


class RuntimeAIClientFactory:
    """Create ordinary clients only from the backend's current activation."""

    def __init__(
        self,
        *,
        runtime_state: AIRuntimeStateService,
        credential_resolver: RuntimeCredentialResolver,
        execution_lease_authority: AIExecutionLeaseAuthority | None = None,
        session=None,
    ) -> None:
        self._runtime_state = runtime_state
        self._credentials = credential_resolver
        self._lease_authority = execution_lease_authority
        self._session = session

    def create(self, *, max_attempts: int = 2) -> OpenAICompatibleClient:
        if (
            isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or not 1 <= max_attempts <= 8
        ):
            raise AIProviderNotConfigured("AI 运行参数无效。")
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
            max_attempts=max_attempts,
        )
        return OpenAICompatibleClient(settings, session=self._session)

    @contextmanager
    def acquire_bound(
        self,
        action: BoundRuntimeAction,
        *,
        max_attempts: int = 1,
    ):
        authority = self._lease_authority
        if authority is None:
            raise AIRuntimeBindingError("AI 执行租约后端尚未配置。")
        try:
            lease = authority.acquire(action)
        except Exception as exc:
            raise AIRuntimeBindingError("AI 执行租约暂时不可用。") from exc
        if not hasattr(lease, "__enter__") or not hasattr(lease, "__exit__"):
            raise AIRuntimeBindingError("AI 执行租约后端无效。")
        try:
            with lease:
                yield self.create_bound(action, max_attempts=max_attempts)
        except AIRuntimeBindingError:
            raise
        except Exception:
            raise

    def create_bound(
        self,
        action: BoundRuntimeAction,
        *,
        max_attempts: int = 1,
    ) -> OpenAICompatibleClient:
        if (
            isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or not 1 <= max_attempts <= 8
        ):
            raise AIRuntimeBindingError("AI 运行绑定无效。")
        try:
            runtime = self._runtime_state.resolve_runtime()
            expected_models = dict(action.runtime_task_models)
        except Exception as exc:
            if isinstance(exc, AIRuntimeBindingError):
                raise
            raise AIRuntimeBindingError("AI 运行绑定已变化。") from exc
        if (
            runtime.provider_id != action.provider_id
            or runtime.selection_revision != action.runtime_revision
            or runtime.credential_generation != action.credential_generation
            or runtime.activation != action.runtime_activation
            or runtime.activation not in {"legacy_compatible", "connection_verified"}
            or dict(runtime.task_models) != expected_models
        ):
            raise AIRuntimeBindingError("AI 运行绑定已变化。")
        resolver = self._credentials
        resolve_bound = getattr(resolver, "resolve_bound", None)
        if not callable(resolve_bound):
            raise AIRuntimeBindingError("AI 凭据后端不支持原子绑定读取。")
        try:
            api_key = resolve_bound(
                runtime.credential_ref,
                runtime.credential_generation,
            )
        except Exception as exc:
            raise AIRuntimeBindingError("AI 凭据绑定已变化。") from exc
        if not isinstance(api_key, str) or not api_key.strip():
            raise AIRuntimeBindingError("AI 凭据绑定已变化。")
        try:
            current = self._runtime_state.resolve_runtime()
        except Exception as exc:
            raise AIRuntimeBindingError("AI 运行绑定已变化。") from exc
        if (
            current.provider_id != runtime.provider_id
            or current.selection_revision != runtime.selection_revision
            or current.credential_generation != runtime.credential_generation
            or current.activation != runtime.activation
            or dict(current.task_models) != dict(runtime.task_models)
        ):
            raise AIRuntimeBindingError("AI 运行绑定已变化。")
        settings = OpenAICompatibleSettings.from_resolved_runtime(
            current,
            api_key=api_key,
            max_attempts=max_attempts,
        )
        return OpenAICompatibleClient(settings, session=self._session)


__all__ = [
    "AIRuntimeBindingError",
    "AIExecutionLeaseAuthority",
    "BoundCredentialResolver",
    "BoundRuntimeAction",
    "RuntimeAIClientFactory",
    "RuntimeCredentialResolver",
]
