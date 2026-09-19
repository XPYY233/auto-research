"""Stable provider errors shared by clients and runtime state."""
from __future__ import annotations

from .provider_registry import KNOWN_CAPABILITIES


class AIProviderError(RuntimeError):
    code = "ai_provider_error"
    retryable = False

    def __init__(self, safe_message: str):
        super().__init__(safe_message)
        self.safe_message = safe_message

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": "ai-provider-error-v1",
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.retryable,
        }


class AIProviderNotConfigured(AIProviderError):
    code = "ai_provider_not_configured"


class AIProviderResponseError(AIProviderError):
    code = "ai_provider_response_invalid"


class AIProviderUnavailableError(AIProviderResponseError):
    code = "ai_provider_unavailable"
    retryable = True


class AIProviderOutcomeUnknownError(AIProviderResponseError):
    """The provider may have charged the call, so it must not be retried."""

    code = "ai_provider_outcome_unknown"


class AIProviderCapabilityError(AIProviderResponseError):
    def __init__(self, capability: str):
        super().__init__("所选 AI 提供商不支持当前任务所需能力。")
        self.code = "ai_provider_capability_missing"
        self.capability = capability if capability in KNOWN_CAPABILITIES else "unknown"

    def public_dict(self) -> dict[str, object]:
        value = super().public_dict()
        value["details"] = {"capability": self.capability}
        return value
