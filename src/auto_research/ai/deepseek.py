from __future__ import annotations

import json
import os
import getpass
import subprocess
from dataclasses import dataclass
from typing import Any

import requests


DEFAULT_KEYCHAIN_SERVICE = "auto-research-deepseek"


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


@dataclass(frozen=True)
class DeepSeekSettings:
    api_key: str | None
    base_url: str = "https://api.deepseek.com"
    extraction_model: str = "deepseek-v4-pro"
    analysis_model: str = "deepseek-v4-flash"
    timeout_seconds: int = 180
    credential_source: str | None = None

    @classmethod
    def from_env(cls) -> "DeepSeekSettings":
        environment_key = os.environ.get("DEEPSEEK_API_KEY") or None
        keychain_service = os.environ.get("DEEPSEEK_KEYCHAIN_SERVICE", DEFAULT_KEYCHAIN_SERVICE)
        keychain_key = None if environment_key else _read_project_keychain(keychain_service)
        api_key = environment_key or keychain_key
        return cls(
            api_key=api_key,
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            extraction_model=os.environ.get("DEEPSEEK_EXTRACTION_MODEL", "deepseek-v4-pro"),
            analysis_model=os.environ.get("DEEPSEEK_ANALYSIS_MODEL", "deepseek-v4-flash"),
            timeout_seconds=int(os.environ.get("DEEPSEEK_TIMEOUT_SECONDS", "180")),
            credential_source=(
                "DEEPSEEK_API_KEY" if environment_key
                else f"macOS Keychain:{keychain_service}" if keychain_key
                else None
            ),
        )

    def public_status(self) -> dict[str, Any]:
        return {
            "provider": "deepseek",
            "configured": bool(self.api_key),
            "base_url": self.base_url,
            "extraction_model": self.extraction_model,
            "analysis_model": self.analysis_model,
            "credential_source": self.credential_source,
        }


class DeepSeekClient:
    """Small runtime adapter; Codex is never called by the released application."""

    def __init__(self, settings: DeepSeekSettings | None = None, session=None):
        self.settings = settings or DeepSeekSettings.from_env()
        self.session = session or requests

    def request_json(self, messages: list[dict[str, str]], *, task: str = "extraction",
                     max_tokens: int = 16_000, thinking: bool | None = None) -> dict[str, Any]:
        if not self.settings.api_key:
            raise DeepSeekNotConfigured(
                "DeepSeek 尚未配置；请在本机环境变量 DEEPSEEK_API_KEY 中设置密钥"
            )
        model = (
            self.settings.extraction_model if task in {"extraction", "verification"}
            else self.settings.analysis_model
        )
        payload = {
            "model": model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
            "stream": False,
        }
        if thinking is not None:
            payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
        last_error: Exception | None = None
        for attempt in range(2):
            response = self.session.post(
                f"{self.settings.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.settings.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.settings.timeout_seconds,
            )
            if not response.ok:
                raise DeepSeekResponseError(f"DeepSeek API 请求失败：HTTP {response.status_code}")
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
                if attempt == 0:
                    continue
        raise DeepSeekResponseError("DeepSeek 连续两次返回不可用的 JSON") from last_error

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
