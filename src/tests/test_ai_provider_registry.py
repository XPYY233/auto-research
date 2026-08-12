from __future__ import annotations

import unittest

from auto_research.ai.provider_registry import (
    CAPABILITY_AGENT,
    CAPABILITY_STRUCTURED_JSON,
    CAPABILITY_TOOL_CALLING,
    TrustedProviderRegistryError,
    trusted_chat_endpoint,
    trusted_provider_profile,
    trusted_provider_public_catalog,
)


class TrustedProviderRegistryTests(unittest.TestCase):
    def test_catalog_contains_only_audited_agent_capable_providers(self) -> None:
        catalog = trusted_provider_public_catalog(
            (CAPABILITY_AGENT, CAPABILITY_STRUCTURED_JSON, CAPABILITY_TOOL_CALLING)
        )
        self.assertEqual([item["provider_id"] for item in catalog], ["deepseek", "openai"])
        self.assertTrue(all("endpoint" not in item for item in catalog))
        self.assertEqual(catalog[1]["model_validation"], "connection-test-required")
        self.assertNotIn("api_key", repr(catalog))

    def test_endpoints_are_fixed_by_provider_identity(self) -> None:
        self.assertEqual(
            trusted_chat_endpoint("deepseek"),
            "https://api.deepseek.com/chat/completions",
        )
        self.assertEqual(
            trusted_chat_endpoint("openai"),
            "https://api.openai.com/v1/chat/completions",
        )

    def test_unknown_provider_and_url_shaped_identity_fail_closed(self) -> None:
        for value in (
            "custom",
            "https://api.deepseek.com",
            "api.deepseek.com",
            "deepseek@evil.example",
            "openai/../evil",
        ):
            with self.subTest(value=value), self.assertRaises(TrustedProviderRegistryError):
                trusted_provider_profile(value)

    def test_unknown_capability_is_rejected(self) -> None:
        with self.assertRaises(TrustedProviderRegistryError) as raised:
            trusted_provider_public_catalog(("browse_arbitrary_url",))
        self.assertEqual(raised.exception.code, "ai_capability_unknown")
        self.assertEqual(
            raised.exception.public_dict()["schema_version"],
            "ai-provider-error-v1",
        )

    def test_profile_task_models_are_deeply_immutable(self) -> None:
        profile = trusted_provider_profile("deepseek")
        with self.assertRaises(TypeError):
            profile.default_task_models["analysis"] = "changed"  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
