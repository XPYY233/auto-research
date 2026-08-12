from __future__ import annotations

import unittest

from auto_research.settings.ai_provider import (
    AIProviderSelection,
    AIProviderSettingsError,
    AISettingsService,
)


OPENAI_MODELS = {
    "extraction": "gpt-5",
    "analysis": "gpt-5",
    "librarian_planning": "gpt-5-mini",
    "librarian_synthesis": "gpt-5",
}


class _Resolver:
    def __init__(self, secret: str | None = "sk-runtime-secret"):
        self.secret = secret
        self.refs: list[str] = []

    def resolve(self, credential_ref: str) -> str | None:
        self.refs.append(credential_ref)
        return self.secret


class AIProviderSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = AISettingsService()

    def test_deepseek_default_preserves_existing_task_model_names(self) -> None:
        selection = AIProviderSelection.default()
        self.assertEqual(selection.provider_id, "deepseek")
        self.assertEqual(selection.task_models["extraction"], "deepseek-v4-pro")
        self.assertEqual(selection.task_models["librarian_planning"], "deepseek-v4-flash")

    def test_openai_requires_explicit_complete_task_models(self) -> None:
        with self.assertRaises(AIProviderSettingsError) as raised:
            AIProviderSelection.from_mapping(
                {"provider_id": "openai", "credential_ref": "openai.default"}
            )
        self.assertEqual(raised.exception.code, "ai_task_models_incomplete")
        selection = AIProviderSelection.from_mapping(
            {
                "provider_id": "openai",
                "credential_ref": "openai.default",
                "task_models": OPENAI_MODELS,
            }
        )
        self.assertEqual(selection.task_models, OPENAI_MODELS)

    def test_secret_or_endpoint_fields_are_never_accepted_as_settings(self) -> None:
        for field in ("api_key", "base_url", "endpoint", "credential_path"):
            value = {
                "provider_id": "deepseek",
                "credential_ref": "deepseek.default",
                field: "https://evil.example/steal",
            }
            with self.subTest(field=field), self.assertRaises(AIProviderSettingsError) as raised:
                AIProviderSelection.from_mapping(value)
            self.assertEqual(raised.exception.code, "ai_settings_fields_invalid")

    def test_credential_ref_and_model_are_path_free_ascii_tokens(self) -> None:
        for ref in ("../secret", "/Users/private/key", "deepseek key", "http://evil"):
            with self.subTest(ref=ref), self.assertRaises(AIProviderSettingsError):
                AIProviderSelection.from_mapping(
                    {"provider_id": "deepseek", "credential_ref": ref}
                )
        for model in ("https://evil/model", "../gpt", "gpt model", "模型"):
            value = dict(OPENAI_MODELS)
            value["analysis"] = model
            with self.subTest(model=model), self.assertRaises(AIProviderSettingsError):
                AIProviderSelection.from_mapping(
                    {
                        "provider_id": "openai",
                        "credential_ref": "openai.default",
                        "task_models": value,
                    }
                )

    def test_public_settings_is_path_free_and_contains_no_secret(self) -> None:
        selection = AIProviderSelection.from_mapping(
            {
                "provider_id": "openai",
                "credential_ref": "openai.default",
                "task_models": OPENAI_MODELS,
            }
        )
        payload = self.service.public_settings(selection, credential_configured=True)
        rendered = repr(payload)
        self.assertEqual(payload["schema_version"], "ai-provider-settings-v1")
        self.assertFalse(payload["configured"])
        self.assertFalse(payload["available"])
        self.assertFalse(payload["verified"])
        self.assertEqual(payload["availability"], "verification_required")
        self.assertNotIn("endpoint", rendered)
        self.assertNotIn("credential_ref", rendered)
        self.assertNotIn("sk-runtime-secret", rendered)
        self.assertNotIn("/Users/", rendered)

    def test_runtime_secret_is_resolved_late_and_not_persisted(self) -> None:
        selection = AIProviderSelection.default()
        resolver = _Resolver()
        settings = self.service.runtime_settings(selection, resolver)
        self.assertEqual(resolver.refs, ["deepseek.default"])
        self.assertEqual(settings.api_key, "sk-runtime-secret")
        self.assertNotIn("sk-runtime-secret", repr(settings))
        self.assertNotIn("sk-runtime-secret", repr(settings.public_status()))
        self.assertNotIn("api_key", selection.persistable_dict())

    def test_unverified_openai_runtime_fails_before_resolving_secret(self) -> None:
        selection = AIProviderSelection.from_mapping(
            {
                "provider_id": "openai",
                "credential_ref": "openai.default",
                "task_models": OPENAI_MODELS,
            }
        )
        resolver = _Resolver()
        with self.assertRaises(AIProviderSettingsError) as raised:
            self.service.runtime_settings(selection, resolver)
        self.assertEqual(raised.exception.code, "ai_model_verification_required")
        self.assertEqual(resolver.refs, [])

    def test_direct_selection_is_validated_and_task_models_are_immutable(self) -> None:
        selection = AIProviderSelection(
            provider_id="openai",
            credential_ref="openai.default",
            task_models=OPENAI_MODELS,
        )
        with self.assertRaises(TypeError):
            selection.task_models["analysis"] = "changed"  # type: ignore[index]
        with self.assertRaises(AIProviderSettingsError):
            AIProviderSelection(
                provider_id="openai",
                credential_ref="/tmp/key",
                task_models=OPENAI_MODELS,
            )


if __name__ == "__main__":
    unittest.main()
