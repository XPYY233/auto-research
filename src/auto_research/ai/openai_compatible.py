from __future__ import annotations

from .provider_errors import (
    AIProviderError,
    AIProviderNotConfigured,
    AIProviderResponseError,
    AIProviderUnavailableError,
    AIProviderOutcomeUnknownError,
    AIProviderCapabilityError,
)

import json
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Mapping, TypeVar

import requests

from .provider_registry import (
    CAPABILITY_AGENT,
    CAPABILITY_STRUCTURED_JSON,
    CAPABILITY_TOOL_CALLING,
    CAPABILITY_VENDOR_THINKING,
    KNOWN_CAPABILITIES,
    MODEL_VALIDATION_BUILTIN,
    RUNTIME_ACTIVATION_CONNECTION_REQUIRED,
    TASK_ANALYSIS,
    TASK_EXTRACTION,
    TASK_IDS,
    TASK_LIBRARIAN_PLANNING,
    TASK_LIBRARIAN_SYNTHESIS,
    trusted_chat_endpoint,
    trusted_provider_profile,
    TrustedProviderRegistryError,
    validated_task_models,
)


_CREDENTIAL_REF_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_Result = TypeVar("_Result")
_BACKEND_ACTIVATIONS = frozenset({"legacy_compatible", "connection_verified"})
_VERIFICATION_MAX_TOKENS = 256
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024

if TYPE_CHECKING:
    from auto_research.settings.ai_runtime_state import ResolvedAIRuntime


class _DecodedJSONResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def json(self) -> object:
        return self._payload


@dataclass(frozen=True)
class OpenAICompatibleSettings:
    provider_id: str
    task_models: Mapping[str, str]
    api_key: str | None = field(default=None, repr=False)
    credential_ref: str | None = None
    timeout_seconds: int = 180
    max_attempts: int = 2
    retry_base_seconds: int = 0
    verification_mode: bool = field(default=False, repr=False)
    _backend_activation: tuple[str, str, tuple[tuple[str, str], ...]] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        try:
            profile = trusted_provider_profile(self.provider_id)
            models = validated_task_models(profile.provider_id, self.task_models)
        except TrustedProviderRegistryError as exc:
            raise AIProviderResponseError(exc.safe_message) from exc
        if self.credential_ref is not None and _CREDENTIAL_REF_RE.fullmatch(
            str(self.credential_ref)
        ) is None:
            raise AIProviderResponseError("AI 凭据引用格式无效。")
        if self.api_key is not None and (
            not isinstance(self.api_key, str) or not self.api_key.strip()
        ):
            raise AIProviderResponseError("AI 凭据格式无效。")
        try:
            timeout_seconds = int(self.timeout_seconds)
            max_attempts = int(self.max_attempts)
            retry_base_seconds = int(self.retry_base_seconds)
        except (TypeError, ValueError) as exc:
            raise AIProviderResponseError("AI 运行参数格式无效。") from exc
        object.__setattr__(self, "provider_id", profile.provider_id)
        object.__setattr__(self, "task_models", models)
        object.__setattr__(self, "timeout_seconds", min(max(timeout_seconds, 10), 1800))
        object.__setattr__(self, "max_attempts", min(max(max_attempts, 1), 8))
        object.__setattr__(self, "retry_base_seconds", min(max(retry_base_seconds, 0), 30))
        if not isinstance(self.verification_mode, bool):
            raise AIProviderResponseError("AI 验证模式格式无效。")

    @classmethod
    def from_resolved_runtime(
        cls,
        runtime: "ResolvedAIRuntime",
        *,
        api_key: str,
        timeout_seconds: int = 180,
        max_attempts: int = 2,
        retry_base_seconds: int = 0,
    ) -> "OpenAICompatibleSettings":
        """Construct ordinary runtime settings from backend authority only."""
        from auto_research.settings.ai_runtime_state import ResolvedAIRuntime

        if not isinstance(runtime, ResolvedAIRuntime) or runtime.activation not in _BACKEND_ACTIVATIONS:
            raise AIProviderResponseError("AI 后端激活状态无效。")
        value = cls(
            provider_id=runtime.provider_id,
            task_models=runtime.task_models,
            api_key=api_key,
            credential_ref=runtime.credential_ref,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            retry_base_seconds=retry_base_seconds,
        )
        object.__setattr__(
            value,
            "_backend_activation",
            (
                runtime.activation,
                runtime.provider_id,
                tuple(sorted(runtime.task_models.items())),
            ),
        )
        return value

    @property
    def models_verified(self) -> bool:
        profile = trusted_provider_profile(self.provider_id)
        legacy = (
            profile.model_validation == MODEL_VALIDATION_BUILTIN
            and profile.runtime_activation != RUNTIME_ACTIVATION_CONNECTION_REQUIRED
        )
        activation = self._backend_activation
        backend_verified = activation == (
            "connection_verified",
            self.provider_id,
            tuple(sorted(self.task_models.items())),
        )
        return legacy or backend_verified

    @property
    def available(self) -> bool:
        return bool(self.api_key) and self.models_verified

    def model_for_task(self, task: str) -> str:
        aliases = {"verification": TASK_EXTRACTION}
        normalized = aliases.get(task, task)
        if normalized not in TASK_IDS:
            raise AIProviderResponseError("AI 任务类型无法识别。")
        return self.task_models[normalized]

    def public_status(self) -> dict[str, Any]:
        profile = trusted_provider_profile(self.provider_id)
        return {
            "schema_version": "ai-provider-runtime-status-v1",
            "provider_id": profile.provider_id,
            "display_name": profile.display_name,
            "configured": self.available,
            "available": self.available,
            "credential_configured": bool(self.api_key),
            "verified": self.models_verified,
            "availability": "available" if self.available else (
                "verification_required" if not self.models_verified else "credential_required"
            ),
            "task_models": dict(self.task_models),
            "capabilities": profile.capabilities.public_dict(),
            "timeout_seconds": self.timeout_seconds,
            "max_attempts": self.max_attempts,
        }

class OpenAICompatibleClient:
    """OpenAI Chat Completions adapter for audited, fixed-endpoint providers."""

    def __init__(
        self,
        settings: OpenAICompatibleSettings,
        session=None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        profile = trusted_provider_profile(settings.provider_id)
        if not profile.capabilities.supports(CAPABILITY_AGENT):
            raise AIProviderCapabilityError(CAPABILITY_AGENT)
        self.settings = settings
        self.profile = profile
        self.session = session if session is not None else requests
        self._monotonic = monotonic

    def _read_bounded_response(self, response: Any, *, deadline: float) -> Any:
        iterator = getattr(response, "iter_content", None)
        if not callable(iterator):
            return response
        body = bytearray()
        close = getattr(response, "close", None)
        try:
            for chunk in iterator(chunk_size=64 * 1024):
                if self._monotonic() > deadline:
                    raise AIProviderOutcomeUnknownError(
                        "AI 提供商响应超过单次调用时限；结果状态未知，未自动重试。"
                    )
                if not chunk:
                    continue
                body.extend(chunk)
                if len(body) > _MAX_RESPONSE_BYTES:
                    raise AIProviderOutcomeUnknownError(
                        "AI 提供商响应超过安全大小；结果状态未知，未自动重试。"
                    )
            if self._monotonic() > deadline:
                raise AIProviderOutcomeUnknownError(
                    "AI 提供商响应超过单次调用时限；结果状态未知，未自动重试。"
                )
        finally:
            if callable(close):
                close()
        try:
            payload = json.loads(bytes(body).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AIProviderResponseError("AI 提供商返回格式无效。") from exc
        return _DecodedJSONResponse(payload)

    def _require(self, capability: str) -> None:
        if not self.profile.capabilities.supports(capability):
            raise AIProviderCapabilityError(capability)

    def _execute(
        self,
        payload: dict[str, Any],
        parser: Callable[[Any], _Result],
        *,
        verification_request: bool = False,
    ) -> _Result:
        if not self.settings.api_key:
            raise AIProviderNotConfigured("AI 提供商尚未配置本机凭据。")
        if not self.settings.models_verified and not (
            self.settings.verification_mode and verification_request
        ):
            raise AIProviderCapabilityError("model_set_verification")
        endpoint = trusted_chat_endpoint(self.profile.provider_id)
        last_error: Exception | None = None
        for attempt in range(self.settings.max_attempts):
            deadline = self._monotonic() + self.settings.timeout_seconds
            try:
                response = self.session.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {self.settings.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=(20, self.settings.timeout_seconds),
                    allow_redirects=False,
                    stream=True,
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt + 1 < self.settings.max_attempts:
                    self._wait(attempt)
                    continue
                raise AIProviderUnavailableError("AI 提供商网络请求未能完成。") from exc
            if 300 <= response.status_code < 400:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
                raise AIProviderResponseError("AI 提供商返回了不允许的重定向。")
            if not response.ok:
                transient = response.status_code == 429 or response.status_code >= 500
                close = getattr(response, "close", None)
                if callable(close):
                    close()
                if transient and attempt + 1 < self.settings.max_attempts:
                    last_error = AIProviderResponseError("transient provider response")
                    self._wait(attempt)
                    continue
                error_type = AIProviderUnavailableError if transient else AIProviderResponseError
                raise error_type(f"AI 提供商请求失败：HTTP {response.status_code}")
            try:
                return parser(
                    self._read_bounded_response(response, deadline=deadline)
                )
            except AIProviderOutcomeUnknownError:
                raise
            except AIProviderResponseError as exc:
                last_error = exc
                if attempt + 1 < self.settings.max_attempts:
                    self._wait(attempt)
                    continue
                raise
        raise AIProviderResponseError("AI 提供商连续返回不可用响应。") from last_error

    def _wait(self, attempt: int) -> None:
        delay = min(self.settings.retry_base_seconds * (2**attempt), 30)
        if delay:
            time.sleep(delay)

    def request_json(
        self,
        messages: list[dict[str, str]],
        *,
        task: str = TASK_EXTRACTION,
        max_tokens: int = 16_000,
        thinking: bool | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        return self._request_json(
            messages,
            task=task,
            max_tokens=max_tokens,
            thinking=thinking,
            temperature=temperature,
            verification_request=False,
        )

    def _request_json(
        self,
        messages: list[dict[str, str]],
        *,
        task: str,
        max_tokens: int,
        thinking: bool | None,
        temperature: float | None,
        verification_request: bool,
    ) -> dict[str, Any]:
        self._require(CAPABILITY_STRUCTURED_JSON)
        if thinking is not None:
            self._require(CAPABILITY_VENDOR_THINKING)
        payload: dict[str, Any] = {
            "model": self.settings.model_for_task(task),
            "messages": messages,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        payload[
            "max_completion_tokens"
            if self.profile.provider_id == "openai"
            else "max_tokens"
        ] = max_tokens
        if thinking is not None:
            payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
        elif verification_request and self.profile.provider_id == "deepseek":
            # Capability verification checks response shape, not reasoning
            # quality.  Disabling thinking makes the bounded probe stable and
            # prevents hidden reasoning tokens from consuming its output cap.
            payload["thinking"] = {"type": "disabled"}
        if temperature is not None:
            payload["temperature"] = min(max(float(temperature), 0.0), 1.5)
        def parse(response: Any) -> dict[str, Any]:
            try:
                content = response.json()["choices"][0]["message"]["content"]
                if not str(content or "").strip():
                    raise AIProviderResponseError("AI 提供商返回了空内容。")
                try:
                    value = json.loads(content)
                except json.JSONDecodeError:
                    value = json.loads(content, strict=False)
                if not isinstance(value, dict):
                    raise AIProviderResponseError("AI 提供商未返回 JSON 对象。")
                return value
            except AIProviderResponseError:
                raise
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise AIProviderResponseError("AI 提供商返回格式无效。") from exc

        return self._execute(
            payload,
            parse,
            verification_request=verification_request,
        )

    def request_tool_message(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        task: str = TASK_ANALYSIS,
        max_tokens: int = 3_200,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        return self._request_tool_message(
            messages,
            tools,
            task=task,
            max_tokens=max_tokens,
            temperature=temperature,
            tool_choice="auto",
            verification_request=False,
        )

    def _request_tool_message(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        task: str,
        max_tokens: int,
        temperature: float,
        tool_choice: str | Mapping[str, Any],
        verification_request: bool,
    ) -> dict[str, Any]:
        if tools:
            self._require(CAPABILITY_TOOL_CALLING)
        payload: dict[str, Any] = {
            "model": self.settings.model_for_task(task),
            "messages": messages,
            "temperature": min(max(float(temperature), 0.0), 1.5),
            "stream": False,
        }
        payload[
            "max_completion_tokens"
            if self.profile.provider_id == "openai"
            else "max_tokens"
        ] = max_tokens
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        elif self.profile.provider_id == "deepseek":
            # Harness terminal turns already receive a frozen, locally
            # verified evidence packet and must spend their bounded output
            # budget on the schema-constrained answer.  DeepSeek V4 may
            # otherwise consume the entire completion allowance as hidden
            # reasoning and return an empty ``content`` field.  That made a
            # successful connection/capability probe look "available" while
            # every real Librarian or selected-evidence action failed at the
            # final provider-response gate.  Tool-planning turns keep the
            # provider default; only a no-tool terminal turn is made explicit.
            payload["thinking"] = {"type": "disabled"}
        if verification_request and self.profile.provider_id == "deepseek":
            # DeepSeek reasoning mode rejects a forced function tool_choice.
            # The fixed capability probe deliberately disables thinking so it
            # can verify tool-call support without changing ordinary runtime
            # requests or weakening the exact-call assertion below.
            payload["thinking"] = {"type": "disabled"}
        def parse(response: Any) -> dict[str, Any]:
            try:
                message = response.json()["choices"][0]["message"]
                if not isinstance(message, dict):
                    raise TypeError("assistant message is not an object")
                content = message.get("content") or ""
                tool_calls = message.get("tool_calls") or []
                if not isinstance(tool_calls, list) or not all(
                    isinstance(call, dict) for call in tool_calls
                ):
                    raise TypeError("tool calls are not an array of objects")
                if not str(content).strip() and not tool_calls:
                    raise AIProviderResponseError("AI 提供商返回了空消息。")
                return {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls,
                }
            except AIProviderResponseError:
                raise
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise AIProviderResponseError("AI 提供商返回格式无效。") from exc

        return self._execute(
            payload,
            parse,
            verification_request=verification_request,
        )

    def verify_structured_json_capability(self) -> bool:
        """Run the one fixed structured-output capability probe."""

        result = self._request_json(
            [
                {
                    "role": "system",
                    "content": 'Return only this JSON object: {"status":"ok"}.',
                },
                {"role": "user", "content": "Run the fixed capability check."},
            ],
            task=TASK_ANALYSIS,
            # Reasoning models may spend part of the completion budget in
            # reasoning_content before emitting the tiny JSON answer.  A
            # 32-token probe produced a valid HTTP 200 but an empty content
            # field on DeepSeek V4 Pro.  This remains a bounded, low-cost
            # verification request while leaving room for the final object.
            max_tokens=_VERIFICATION_MAX_TOKENS,
            thinking=None,
            temperature=0.0,
            verification_request=True,
        )
        if result != {"status": "ok"}:
            raise AIProviderResponseError("AI 提供商结构化输出验证未通过。")
        return True

    def verify_tool_calling_capability(self) -> bool:
        """Run the one fixed tool-call probe without executing the tool."""

        tool_name = "auto_research_capability_check"
        message = self._request_tool_message(
            [
                {
                    "role": "system",
                    "content": "Call the required capability-check tool exactly once.",
                },
                {"role": "user", "content": "Run the fixed capability check."},
            ],
            [
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": "Verify tool calling without executing a tool.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "verification": {
                                    "type": "string",
                                    "enum": ["tool_calling"],
                                }
                            },
                            "required": ["verification"],
                            "additionalProperties": False,
                        },
                    },
                }
            ],
            task=TASK_ANALYSIS,
            max_tokens=_VERIFICATION_MAX_TOKENS,
            temperature=0.0,
            tool_choice={"type": "function", "function": {"name": tool_name}},
            verification_request=True,
        )
        calls = message.get("tool_calls")
        if not isinstance(calls, list) or len(calls) != 1:
            raise AIProviderResponseError("AI 提供商工具调用验证未通过。")
        try:
            function = calls[0]["function"]
            arguments = json.loads(function["arguments"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AIProviderResponseError("AI 提供商工具调用验证未通过。") from exc
        if function.get("name") != tool_name or arguments != {"verification": "tool_calling"}:
            raise AIProviderResponseError("AI 提供商工具调用验证未通过。")
        return True

    def smoke_test(self) -> dict[str, Any]:
        """Compatibility alias for the fixed structured-output probe."""

        if not self.settings.models_verified and not self.settings.verification_mode:
            raise AIProviderCapabilityError("model_set_verification")
        self.verify_structured_json_capability()
        return {
            "ok": True,
            "provider_id": self.profile.provider_id,
            "model": self.settings.model_for_task(TASK_ANALYSIS),
        }
