from __future__ import annotations

import unittest

from auto_research.ai.openai_compatible import AIProviderCapabilityError
from auto_research.ai.runtime_factory import RuntimeAIClientFactory
from auto_research.settings.ai_runtime_state import (
    AIRuntimeStateError,
    ResolvedAIRuntime,
)


OPENAI_MODELS = {
    "extraction": "gpt-5.6-terra",
    "analysis": "gpt-5.6-terra",
    "librarian_planning": "gpt-5.6-sol",
    "librarian_synthesis": "gpt-5.6-terra",
}
DEEPSEEK_MODELS = {
    "extraction": "deepseek-v4-pro",
    "analysis": "deepseek-v4-pro",
    "librarian_planning": "deepseek-v4-flash",
    "librarian_synthesis": "deepseek-v4-pro",
}


class _Runtime:
    def __init__(self, resolved=None, error=None):
        self.resolved = resolved
        self.error = error

    def resolve_runtime(self):
        if self.error:
            raise self.error
        return self.resolved


class _Credentials:
    def __init__(self, key="sk-backend-secret"):
        self.key = key
        self.refs = []

    def resolve(self, credential_ref):
        self.refs.append(credential_ref)
        return self.key


class RuntimeAIClientFactoryTests(unittest.TestCase):
    def runtime(self, provider, models, activation):
        return ResolvedAIRuntime(
            provider,
            models,
            f"{provider}.default",
            7,
            4,
            activation,
        )

    def test_openai_connection_verified_activates_ordinary_requests(self):
        credentials = _Credentials()
        client = RuntimeAIClientFactory(
            runtime_state=_Runtime(self.runtime("openai", OPENAI_MODELS, "connection_verified")),
            credential_resolver=credentials,
            session=object(),
        ).create()
        self.assertTrue(client.settings.models_verified)
        self.assertFalse(client.settings.verification_mode)
        self.assertEqual(credentials.refs, ["openai.default"])

    def test_direct_openai_settings_and_verification_mode_do_not_activate_business(self):
        from auto_research.ai.openai_compatible import OpenAICompatibleClient, OpenAICompatibleSettings

        settings = OpenAICompatibleSettings(
            provider_id="openai",
            task_models=OPENAI_MODELS,
            api_key="sk-secret",
            verification_mode=True,
        )
        self.assertFalse(settings.models_verified)
        with self.assertRaises(AIProviderCapabilityError):
            OpenAICompatibleClient(settings, session=object()).request_json([])

    def test_deepseek_legacy_compatible_remains_available(self):
        client = RuntimeAIClientFactory(
            runtime_state=_Runtime(self.runtime("deepseek", DEEPSEEK_MODELS, "legacy_compatible")),
            credential_resolver=_Credentials(),
        ).create()
        self.assertTrue(client.settings.models_verified)

    def test_expired_changed_key_or_model_fail_before_credential_resolution(self):
        for label in ("expired", "credential_changed", "model_changed"):
            error = AIRuntimeStateError(
                "ai_runtime_verification_required",
                "AI 提供商尚未完成能力验证。",
                retryable=False,
            )
            credentials = _Credentials()
            factory = RuntimeAIClientFactory(
                runtime_state=_Runtime(error=error),
                credential_resolver=credentials,
            )
            with self.subTest(label=label), self.assertRaises(AIRuntimeStateError):
                factory.create()
            self.assertEqual(credentials.refs, [])

    def test_forged_activation_is_rejected_by_backend_constructor(self):
        from auto_research.ai.openai_compatible import AIProviderResponseError, OpenAICompatibleSettings

        with self.assertRaises(AIProviderResponseError):
            OpenAICompatibleSettings.from_resolved_runtime(
                self.runtime("openai", OPENAI_MODELS, "renderer_verified"),
                api_key="sk-secret",
            )


if __name__ == "__main__":
    unittest.main()
