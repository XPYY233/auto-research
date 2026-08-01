from __future__ import annotations

import json
import os
import getpass
import subprocess
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import requests


DEFAULT_KEYCHAIN_SERVICE = "auto-research-deepseek"
TRUSTED_DEEPSEEK_API_HOST = "api.deepseek.com"


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    try:
        value = int(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        return default
    return min(max(value, minimum), maximum)


def _read_project_keychain(service: str) -> str | None:
    if not service or os.name != "posix":
        return None
    try:
        result = subprocess.run(
            [
                "security", "find-generic-password", "-a", getpass.getuser(),
                "-s", service, "-w",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    key = result.stdout.strip() if result.returncode == 0 else ""
    return key or None


class DeepSeekNotConfigured(RuntimeError):
    pass


class DeepSeekResponseError(RuntimeError):
    pass


class DeepSeekUnavailableError(DeepSeekResponseError):
    """The provider/network is unavailable; changing prompt shape will not help."""


def trusted_deepseek_chat_url(base_url: str) -> str:
    """Return the chat endpoint only for the supported credential recipient.

    The API key is an Authorization bearer credential. An environment variable
    must not be able to redirect it to an arbitrary HTTP(S) endpoint.
    """

    try:
        parsed = urlsplit(str(base_url or ""))
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise DeepSeekResponseError("DeepSeek API 地址无效；已阻止发送凭据") from exc
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold() != TRUSTED_DEEPSEEK_API_HOST
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise DeepSeekResponseError("DeepSeek API 地址不受信任；已阻止发送凭据")
    return f"https://{TRUSTED_DEEPSEEK_API_HOST}/chat/completions"


@dataclass(frozen=True)
class DeepSeekSettings:
    api_key: str | None
    base_url: str = "https://api.deepseek.com"
    extraction_model: str = "deepseek-v4-pro"
    analysis_model: str = "deepseek-v4-pro"
    librarian_planning_model: str = "deepseek-v4-flash"
    librarian_synthesis_model: str = "deepseek-v4-pro"
    timeout_seconds: int = 180
    max_attempts: int = 2
    retry_base_seconds: int = 0
    credential_source: str | None = None

    @classmethod
    def from_env(cls) -> "DeepSeekSettings":
        environment_key = os.environ.get("DEEPSEEK_API_KEY") or None
        keychain_service = os.environ.get("DEEPSEEK_KEYCHAIN_SERVICE", DEFAULT_KEYCHAIN_SERVICE)
        keychain_key = None if environment_key else _read_project_keychain(keychain_service)
        api_key = environment_key or keychain_key
        analysis_model = os.environ.get("DEEPSEEK_ANALYSIS_MODEL", "deepseek-v4-pro")
        return cls(
            api_key=api_key,
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            extraction_model=os.environ.get("DEEPSEEK_EXTRACTION_MODEL", "deepseek-v4-pro"),
            analysis_model=analysis_model,
            librarian_planning_model=os.environ.get(
                "DEEPSEEK_LIBRARIAN_PLANNING_MODEL", "deepseek-v4-flash"
            ),
            librarian_synthesis_model=os.environ.get(
                "DEEPSEEK_LIBRARIAN_SYNTHESIS_MODEL", analysis_model
            ),
            timeout_seconds=_env_int(
                "DEEPSEEK_TIMEOUT_SECONDS", 180, minimum=10, maximum=1800
            ),
            max_attempts=_env_int(
                "DEEPSEEK_MAX_ATTEMPTS", 4, minimum=2, maximum=8
            ),
            retry_base_seconds=_env_int(
                "DEEPSEEK_RETRY_BASE_SECONDS", 2, minimum=0, maximum=30
            ),
            credential_source=(
                "DEEPSEEK_API_KEY" if environment_key
                else f"macOS Keychain:{keychain_service}" if keychain_key
                else None
            ),
        )

    def public_status(self) -> dict[str, Any]:
        try:
            trusted_deepseek_chat_url(self.base_url)
        except DeepSeekResponseError:
            public_base_url = None
            endpoint_trusted = False
        else:
            public_base_url = f"https://{TRUSTED_DEEPSEEK_API_HOST}"
            endpoint_trusted = True
        return {
            "provider": "deepseek",
            "configured": bool(self.api_key),
            "base_url": public_base_url,
            "endpoint_trusted": endpoint_trusted,
            "extraction_model": self.extraction_model,
            "analysis_model": self.analysis_model,
            "librarian_planning_model": self.librarian_planning_model,
            "librarian_synthesis_model": self.librarian_synthesis_model,
            "timeout_seconds": self.timeout_seconds,
            "max_attempts": self.max_attempts,
            "credential_source": self.credential_source,
        }


class DeepSeekClient:
    """Small runtime adapter; Codex is never called by the released application."""

    def __init__(self, settings: DeepSeekSettings | None = None, session=None):
        self.settings = settings or DeepSeekSettings.from_env()
        self.session = session or requests

    def _model_for_task(self, task: str) -> str:
        if task in {"extraction", "verification"}:
            return self.settings.extraction_model
        if task == "librarian_planning":
            return self.settings.librarian_planning_model
        if task == "librarian_synthesis":
            return self.settings.librarian_synthesis_model
        return self.settings.analysis_model

    def request_json(self, messages: list[dict[str, str]], *, task: str = "extraction",
                     max_tokens: int = 16_000, thinking: bool | None = None,
                     temperature: float | None = None) -> dict[str, Any]:
        if not self.settings.api_key:
            raise DeepSeekNotConfigured(
                "DeepSeek 尚未配置；请在本机环境变量 DEEPSEEK_API_KEY 中设置密钥"
            )
        endpoint = trusted_deepseek_chat_url(self.settings.base_url)
        model = self._model_for_task(task)
        payload = {
            "model": model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
            "stream": False,
        }
        if thinking is not None:
            payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
        if temperature is not None:
            payload["temperature"] = min(max(float(temperature), 0.0), 1.5)
        last_error: Exception | None = None
        def wait_before_retry(attempt: int) -> None:
            delay = min(
                self.settings.retry_base_seconds * (2 ** attempt), 30
            )
            if delay > 0:
                time.sleep(delay)

        for attempt in range(self.settings.max_attempts):
            try:
                response = self.session.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {self.settings.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=(20, self.settings.timeout_seconds),
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt + 1 < self.settings.max_attempts:
                    wait_before_retry(attempt)
                    continue
                raise DeepSeekUnavailableError(
                    f"DeepSeek API 网络请求失败（{self.settings.max_attempts} 次；{type(exc).__name__}）"
                ) from exc
            if not response.ok:
                if (
                    attempt + 1 < self.settings.max_attempts
                    and (response.status_code == 429 or response.status_code >= 500)
                ):
                    last_error = DeepSeekResponseError(
                        f"transient HTTP {response.status_code}"
                    )
                    wait_before_retry(attempt)
                    continue
                error_type = (
                    DeepSeekUnavailableError
                    if response.status_code == 429 or response.status_code >= 500
                    else DeepSeekResponseError
                )
                raise error_type(f"DeepSeek API 请求失败：HTTP {response.status_code}")
            try:
                content = response.json()["choices"][0]["message"]["content"]
                if not content or not str(content).strip():
                    raise DeepSeekResponseError("DeepSeek 返回了空内容")
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    # JSON mode has occasionally returned literal newlines/control
                    # characters inside strings. Python's non-strict decoder can
                    # recover those without inventing or changing any fields.
                    return json.loads(content, strict=False)
            except (KeyError, IndexError, TypeError, json.JSONDecodeError, DeepSeekResponseError) as exc:
                last_error = exc
                if attempt + 1 < self.settings.max_attempts:
                    wait_before_retry(attempt)
                    continue
        raise DeepSeekResponseError(
            f"DeepSeek 连续 {self.settings.max_attempts} 次返回不可用的 JSON"
        ) from last_error

    def request_tool_message(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        task: str = "analysis",
        max_tokens: int = 3_200,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        """Return one OpenAI-compatible assistant message with optional tool calls."""

        if not self.settings.api_key:
            raise DeepSeekNotConfigured(
                "DeepSeek 尚未配置；请在本机环境变量 DEEPSEEK_API_KEY 中设置密钥"
            )
        endpoint = trusted_deepseek_chat_url(self.settings.base_url)
        model = self._model_for_task(task)
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": min(max(float(temperature), 0.0), 1.5),
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        last_error: Exception | None = None
        for attempt in range(self.settings.max_attempts):
            try:
                response = self.session.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {self.settings.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=(20, self.settings.timeout_seconds),
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt + 1 < self.settings.max_attempts:
                    delay = min(self.settings.retry_base_seconds * (2 ** attempt), 30)
                    if delay:
                        time.sleep(delay)
                    continue
                raise DeepSeekUnavailableError(
                    f"DeepSeek API 网络请求失败（{self.settings.max_attempts} 次；{type(exc).__name__}）"
                ) from exc
            if not response.ok:
                if attempt + 1 < self.settings.max_attempts and (response.status_code == 429 or response.status_code >= 500):
                    last_error = DeepSeekResponseError(f"transient HTTP {response.status_code}")
                    delay = min(self.settings.retry_base_seconds * (2 ** attempt), 30)
                    if delay:
                        time.sleep(delay)
                    continue
                error_type = DeepSeekUnavailableError if response.status_code == 429 or response.status_code >= 500 else DeepSeekResponseError
                raise error_type(f"DeepSeek API 请求失败：HTTP {response.status_code}")
            try:
                message = response.json()["choices"][0]["message"]
                if not isinstance(message, dict):
                    raise TypeError("assistant message is not an object")
                content = message.get("content")
                tool_calls = message.get("tool_calls") or []
                if not str(content or "").strip() and not tool_calls:
                    raise DeepSeekResponseError("DeepSeek 返回了空内容")
                return {
                    "role": "assistant",
                    "content": content or "",
                    "tool_calls": tool_calls,
                }
            except (KeyError, IndexError, TypeError, ValueError, DeepSeekResponseError) as exc:
                last_error = exc
                if attempt + 1 < self.settings.max_attempts:
                    continue
        raise DeepSeekResponseError(
            f"DeepSeek 连续 {self.settings.max_attempts} 次返回不可用的工具调用消息"
        ) from last_error

    def smoke_test(self) -> dict[str, Any]:
        result = self.request_json(
            [
                {
                    "role": "system",
                    "content": 'Return json only: {"status":"ok","provider":"deepseek"}.',
                },
                {"role": "user", "content": "Return the requested json connection check."},
            ],
            task="analysis",
            max_tokens=64,
        )
        if result.get("status") != "ok":
            raise DeepSeekResponseError("DeepSeek 连通测试返回了非预期状态")
        return {"ok": True, "provider": "deepseek", "model": self.settings.analysis_model}
