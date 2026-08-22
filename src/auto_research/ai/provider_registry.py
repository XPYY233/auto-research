from __future__ import annotations

from dataclasses import dataclass
import re
import threading
from types import MappingProxyType
from typing import Iterable, Mapping
from urllib.parse import urlsplit


PROVIDER_REGISTRY_VERSION = 1
OPENAI_CHAT_COMPLETIONS_PROTOCOL = "openai-chat-completions-v1"
MODEL_VALIDATION_BUILTIN = "builtin-reviewed"
MODEL_VALIDATION_CONNECTION_REQUIRED = "connection-test-required"
RUNTIME_ACTIVATION_LEGACY = "legacy-compatible"
RUNTIME_ACTIVATION_CONNECTION_REQUIRED = "connection-test-required"

CAPABILITY_AGENT = "agent_capable"
CAPABILITY_STRUCTURED_JSON = "structured_json"
CAPABILITY_TOOL_CALLING = "tool_calling"
CAPABILITY_VENDOR_THINKING = "vendor_thinking_control"
KNOWN_CAPABILITIES = frozenset(
    {
        CAPABILITY_AGENT,
        CAPABILITY_STRUCTURED_JSON,
        CAPABILITY_TOOL_CALLING,
        CAPABILITY_VENDOR_THINKING,
    }
)

TASK_EXTRACTION = "extraction"
TASK_ANALYSIS = "analysis"
TASK_LIBRARIAN_PLANNING = "librarian_planning"
TASK_LIBRARIAN_SYNTHESIS = "librarian_synthesis"
TASK_IDS = (
    TASK_EXTRACTION,
    TASK_ANALYSIS,
    TASK_LIBRARIAN_PLANNING,
    TASK_LIBRARIAN_SYNTHESIS,
)
_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class TrustedProviderRegistryError(ValueError):
    """A path-free rejection from the built-in provider registry."""

    def __init__(self, code: str, safe_message: str):
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": "ai-provider-error-v1",
            "code": self.code,
            "message": self.safe_message,
            "retryable": False,
        }


@dataclass(frozen=True)
class ProviderCapabilities:
    agent_capable: bool
    structured_json: bool
    tool_calling: bool
    vendor_thinking_control: bool = False

    def supports(self, capability: str) -> bool:
        if capability not in KNOWN_CAPABILITIES:
            return False
        return bool(getattr(self, capability))

    def public_dict(self) -> dict[str, bool]:
        return {
            CAPABILITY_AGENT: self.agent_capable,
            CAPABILITY_STRUCTURED_JSON: self.structured_json,
            CAPABILITY_TOOL_CALLING: self.tool_calling,
            CAPABILITY_VENDOR_THINKING: self.vendor_thinking_control,
        }


@dataclass(frozen=True)
class TrustedProviderProfile:
    provider_id: str
    display_name: str
    protocol: str
    chat_endpoint: str
    capabilities: ProviderCapabilities
    default_task_models: Mapping[str, str]
    allowed_task_models: Mapping[str, tuple[str, ...]]
    model_validation: str
    runtime_activation: str
    provider_kind: str = "builtin"

    def __post_init__(self) -> None:
        # ``frozen=True`` does not freeze a caller-owned mapping.  Copy it so a
        # profile can never change after it has passed the registry audit.
        object.__setattr__(
            self,
            "default_task_models",
            MappingProxyType(dict(self.default_task_models)),
        )
        object.__setattr__(
            self,
            "allowed_task_models",
            MappingProxyType(
                {
                    task: tuple(models)
                    for task, models in self.allowed_task_models.items()
                }
            ),
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "protocol": self.protocol,
            "capabilities": self.capabilities.public_dict(),
            "requires_explicit_models": not bool(self.default_task_models),
            "model_validation": self.model_validation,
            "runtime_activation": self.runtime_activation,
            "provider_kind": self.provider_kind,
            "model_options": {
                task: list(models)
                for task, models in self.allowed_task_models.items()
            },
        }


_DEEPSEEK_DEFAULT_MODELS = MappingProxyType(
    {
        TASK_EXTRACTION: "deepseek-v4-pro",
        TASK_ANALYSIS: "deepseek-v4-pro",
        TASK_LIBRARIAN_PLANNING: "deepseek-v4-flash",
        TASK_LIBRARIAN_SYNTHESIS: "deepseek-v4-pro",
    }
)
_DEEPSEEK_MODEL_OPTIONS = MappingProxyType(
    {
        task: ("deepseek-v4-pro", "deepseek-v4-flash") for task in TASK_IDS
    }
)
_OPENAI_MODEL_OPTIONS = MappingProxyType(
    {
        task: ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")
        for task in TASK_IDS
    }
)
_OPENAI_DEFAULT_MODELS = MappingProxyType(
    {task: "gpt-5.6-terra" for task in TASK_IDS}
)

_TRUSTED_PROVIDERS: Mapping[str, TrustedProviderProfile] = MappingProxyType(
    {
        "deepseek": TrustedProviderProfile(
            provider_id="deepseek",
            display_name="DeepSeek",
            protocol=OPENAI_CHAT_COMPLETIONS_PROTOCOL,
            chat_endpoint="https://api.deepseek.com/chat/completions",
            capabilities=ProviderCapabilities(
                agent_capable=True,
                structured_json=True,
                tool_calling=True,
                vendor_thinking_control=True,
            ),
            default_task_models=_DEEPSEEK_DEFAULT_MODELS,
            allowed_task_models=_DEEPSEEK_MODEL_OPTIONS,
            model_validation=MODEL_VALIDATION_BUILTIN,
            # Historical credentials remain readable, but a stored secret is
            # no longer treated as proof that the account/endpoint is usable.
            runtime_activation=RUNTIME_ACTIVATION_CONNECTION_REQUIRED,
        ),
        "openai": TrustedProviderProfile(
            provider_id="openai",
            display_name="OpenAI",
            protocol=OPENAI_CHAT_COMPLETIONS_PROTOCOL,
            chat_endpoint="https://api.openai.com/v1/chat/completions",
            capabilities=ProviderCapabilities(
                agent_capable=True,
                structured_json=True,
                tool_calling=True,
                vendor_thinking_control=False,
            ),
            default_task_models=_OPENAI_DEFAULT_MODELS,
            allowed_task_models=_OPENAI_MODEL_OPTIONS,
            model_validation=MODEL_VALIDATION_BUILTIN,
            runtime_activation=RUNTIME_ACTIVATION_CONNECTION_REQUIRED,
        ),
    }
)

_CUSTOM_PROVIDER_ID = "custom"
_CUSTOM_PROVIDER_LOCK = threading.RLock()
_CUSTOM_PROVIDER: TrustedProviderProfile | None = None
_CUSTOM_ENDPOINT_GUARD = None


def _validate_endpoint(provider_id: str, endpoint: str) -> None:
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise RuntimeError("trusted provider registry contains an invalid endpoint") from exc
    expected = {
        "deepseek": ("api.deepseek.com", "/chat/completions"),
        "openai": ("api.openai.com", "/v1/chat/completions"),
    }.get(provider_id)
    if (
        expected is None
        or parsed.scheme != "https"
        or parsed.hostname != expected[0]
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != expected[1]
    ):
        raise RuntimeError("trusted provider registry contains an unsafe endpoint")


def _validate_registry() -> None:
    for provider_id, profile in _TRUSTED_PROVIDERS.items():
        if provider_id != profile.provider_id:
            raise RuntimeError("trusted provider registry identity mismatch")
        if profile.protocol != OPENAI_CHAT_COMPLETIONS_PROTOCOL:
            raise RuntimeError("trusted provider registry protocol mismatch")
        if profile.model_validation not in {
            MODEL_VALIDATION_BUILTIN,
            MODEL_VALIDATION_CONNECTION_REQUIRED,
        }:
            raise RuntimeError("trusted provider registry model validation mismatch")
        if profile.runtime_activation not in {
            RUNTIME_ACTIVATION_LEGACY,
            RUNTIME_ACTIVATION_CONNECTION_REQUIRED,
        }:
            raise RuntimeError("trusted provider registry runtime activation mismatch")
        _validate_endpoint(provider_id, profile.chat_endpoint)
        if set(profile.default_task_models) - set(TASK_IDS):
            raise RuntimeError("trusted provider registry contains an unknown task")
        if set(profile.allowed_task_models) != set(TASK_IDS):
            raise RuntimeError("trusted provider registry model catalog is incomplete")
        for model in profile.default_task_models.values():
            if _MODEL_NAME_RE.fullmatch(model) is None:
                raise RuntimeError("trusted provider registry contains an invalid model")
        for task, model in profile.default_task_models.items():
            if model not in profile.allowed_task_models[task]:
                raise RuntimeError("trusted provider registry default model is not allowed")
        for models in profile.allowed_task_models.values():
            if not models or any(_MODEL_NAME_RE.fullmatch(model) is None for model in models):
                raise RuntimeError("trusted provider registry contains an invalid model catalog")


_validate_registry()


def trusted_provider_profile(provider_id: str) -> TrustedProviderProfile:
    normalized = str(provider_id or "").strip().casefold()
    profile = _TRUSTED_PROVIDERS.get(normalized)
    if profile is None and normalized == _CUSTOM_PROVIDER_ID:
        with _CUSTOM_PROVIDER_LOCK:
            profile = _CUSTOM_PROVIDER
    if profile is None:
        raise TrustedProviderRegistryError(
            "ai_provider_untrusted",
            "所选 AI 提供商尚未通过应用安全审计。",
        )
    return profile


def trusted_chat_endpoint(provider_id: str) -> str:
    """Resolve a credential recipient solely from the built-in registry."""
    profile = trusted_provider_profile(provider_id)
    if profile.provider_kind == "custom":
        with _CUSTOM_PROVIDER_LOCK:
            guard = _CUSTOM_ENDPOINT_GUARD
        if not callable(guard) or guard(profile.chat_endpoint) is not True:
            raise TrustedProviderRegistryError(
                "ai_provider_endpoint_changed",
                "自定义 AI 提供商地址未通过当前安全核验。",
            )
    return profile.chat_endpoint


def validated_task_models(
    provider_id: str,
    task_models: Mapping[str, object],
) -> Mapping[str, str]:
    """Return one immutable, complete set of path-free task model names."""

    profile = trusted_provider_profile(provider_id)
    if not isinstance(task_models, Mapping) or set(task_models) != set(TASK_IDS):
        raise TrustedProviderRegistryError(
            "ai_task_models_incomplete", "请为每项 AI 任务选择受支持的模型。"
        )
    models: dict[str, str] = {}
    for task in TASK_IDS:
        model = str(task_models.get(task) or "").strip()
        if (
            _MODEL_NAME_RE.fullmatch(model) is None
            or model not in profile.allowed_task_models[task]
        ):
            raise TrustedProviderRegistryError(
                "ai_model_invalid", "所选 AI 模型名称格式无效。"
            )
        models[task] = model
    return MappingProxyType(models)


def trusted_provider_public_catalog(
    required_capabilities: Iterable[str] = (),
) -> tuple[dict[str, object], ...]:
    required = frozenset(str(value or "").strip() for value in required_capabilities)
    unknown = required - KNOWN_CAPABILITIES
    if unknown:
        raise TrustedProviderRegistryError(
            "ai_capability_unknown", "请求的 AI 能力无法识别。"
        )
    with _CUSTOM_PROVIDER_LOCK:
        custom = (_CUSTOM_PROVIDER,) if _CUSTOM_PROVIDER is not None else ()
    return tuple(
        profile.public_dict()
        for profile in (*_TRUSTED_PROVIDERS.values(), *custom)
        if all(profile.capabilities.supports(capability) for capability in required)
    )


def install_custom_provider_profile(
    profile: TrustedProviderProfile,
    *,
    endpoint_guard,
) -> None:
    """Install the single backend-reviewed custom provider for this process."""

    if (
        not isinstance(profile, TrustedProviderProfile)
        or profile.provider_id != _CUSTOM_PROVIDER_ID
        or profile.provider_kind != "custom"
        or profile.protocol != OPENAI_CHAT_COMPLETIONS_PROTOCOL
        or profile.runtime_activation != RUNTIME_ACTIVATION_CONNECTION_REQUIRED
        or not callable(endpoint_guard)
    ):
        raise TrustedProviderRegistryError(
            "ai_provider_untrusted", "自定义 AI 提供商配置无效。"
        )
    with _CUSTOM_PROVIDER_LOCK:
        global _CUSTOM_PROVIDER, _CUSTOM_ENDPOINT_GUARD
        _CUSTOM_PROVIDER = profile
        _CUSTOM_ENDPOINT_GUARD = endpoint_guard


def clear_custom_provider_profile() -> None:
    with _CUSTOM_PROVIDER_LOCK:
        global _CUSTOM_PROVIDER, _CUSTOM_ENDPOINT_GUARD
        _CUSTOM_PROVIDER = None
        _CUSTOM_ENDPOINT_GUARD = None


__all__ = [
    "CAPABILITY_AGENT",
    "CAPABILITY_STRUCTURED_JSON",
    "CAPABILITY_TOOL_CALLING",
    "CAPABILITY_VENDOR_THINKING",
    "MODEL_VALIDATION_BUILTIN",
    "MODEL_VALIDATION_CONNECTION_REQUIRED",
    "OPENAI_CHAT_COMPLETIONS_PROTOCOL",
    "ProviderCapabilities",
    "RUNTIME_ACTIVATION_CONNECTION_REQUIRED",
    "RUNTIME_ACTIVATION_LEGACY",
    "PROVIDER_REGISTRY_VERSION",
    "TASK_IDS",
    "TrustedProviderProfile",
    "TrustedProviderRegistryError",
    "clear_custom_provider_profile",
    "install_custom_provider_profile",
    "trusted_chat_endpoint",
    "trusted_provider_profile",
    "trusted_provider_public_catalog",
    "validated_task_models",
]
