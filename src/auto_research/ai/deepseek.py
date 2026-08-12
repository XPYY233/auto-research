from __future__ import annotations

import os
import getpass
import subprocess
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .openai_compatible import (
    AIProviderCapabilityError,
    AIProviderNotConfigured,
    AIProviderResponseError,
    AIProviderUnavailableError,
    OpenAICompatibleClient,
    OpenAICompatibleSettings,
)
from .provider_registry import trusted_chat_endpoint


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


class DeepSeekNotConfigured(AIProviderNotConfigured):
    """Backward-compatible name for a missing DeepSeek credential."""


class DeepSeekResponseError(AIProviderResponseError):
    """Backward-compatible name for an invalid DeepSeek response."""


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
    return trusted_chat_endpoint("deepseek")


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
            endpoint_trusted = False
        else:
            endpoint_trusted = True
        return {
            "provider": "deepseek",
            "configured": bool(self.api_key),
            "endpoint_trusted": endpoint_trusted,
            "extraction_model": self.extraction_model,
            "analysis_model": self.analysis_model,
            "librarian_planning_model": self.librarian_planning_model,
            "librarian_synthesis_model": self.librarian_synthesis_model,
            "timeout_seconds": self.timeout_seconds,
            "max_attempts": self.max_attempts,
        }


class DeepSeekClient:
    """Thin compatibility facade over the unified trusted-provider client."""

    def __init__(self, settings: DeepSeekSettings | None = None, session=None):
        self.settings = settings or DeepSeekSettings.from_env()
        # Preserve the legacy constructor field only as a fail-closed migration
        # check. It never selects the credential recipient.
        trusted_deepseek_chat_url(self.settings.base_url)
        try:
            runtime_settings = OpenAICompatibleSettings(
                provider_id="deepseek",
                task_models={
                    "extraction": self.settings.extraction_model,
                    "analysis": self.settings.analysis_model,
                    "librarian_planning": self.settings.librarian_planning_model,
                    "librarian_synthesis": self.settings.librarian_synthesis_model,
                },
                api_key=self.settings.api_key,
                timeout_seconds=self.settings.timeout_seconds,
                max_attempts=self.settings.max_attempts,
                retry_base_seconds=self.settings.retry_base_seconds,
            )
            self._client = OpenAICompatibleClient(runtime_settings, session=session)
        except AIProviderResponseError as exc:
            raise DeepSeekResponseError(exc.safe_message) from exc

    def _model_for_task(self, task: str) -> str:
        try:
            return self._client.settings.model_for_task(task)
        except AIProviderResponseError:
            # Legacy behavior treated unknown task names as analysis.
            return self.settings.analysis_model

    @staticmethod
    def _raise_legacy(error: Exception) -> None:
        if isinstance(error, AIProviderNotConfigured):
            raise DeepSeekNotConfigured(
                "DeepSeek 尚未配置；请在本机安全设置中配置密钥"
            ) from error
        if isinstance(error, AIProviderUnavailableError):
            raise DeepSeekUnavailableError(error.safe_message) from error
        if isinstance(error, (AIProviderResponseError, AIProviderCapabilityError)):
            raise DeepSeekResponseError(error.safe_message) from error
        raise error

    def request_json(self, messages: list[dict[str, str]], *, task: str = "extraction",
                     max_tokens: int = 16_000, thinking: bool | None = None,
                     temperature: float | None = None) -> dict[str, Any]:
        resolved_task = task if task in {
            "extraction",
            "verification",
            "analysis",
            "librarian_planning",
            "librarian_synthesis",
        } else "analysis"
        try:
            return self._client.request_json(
                messages,
                task=resolved_task,
                max_tokens=max_tokens,
                thinking=thinking,
                temperature=temperature,
            )
        except (AIProviderNotConfigured, AIProviderResponseError) as exc:
            self._raise_legacy(exc)

    def request_tool_message(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        task: str = "analysis",
        max_tokens: int = 3_200,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        resolved_task = task if task in {
            "extraction",
            "verification",
            "analysis",
            "librarian_planning",
            "librarian_synthesis",
        } else "analysis"
        try:
            return self._client.request_tool_message(
                messages,
                tools,
                task=resolved_task,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except (AIProviderNotConfigured, AIProviderResponseError) as exc:
            self._raise_legacy(exc)

    def smoke_test(self) -> dict[str, Any]:
        try:
            result = self._client.smoke_test()
        except (AIProviderNotConfigured, AIProviderResponseError) as exc:
            self._raise_legacy(exc)
        return {
            "ok": result["ok"],
            "provider": "deepseek",
            "model": self.settings.analysis_model,
        }
