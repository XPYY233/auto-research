from __future__ import annotations

import unittest

from auto_research.ai.openai_compatible import (
    AIProviderCapabilityError,
    AIProviderResponseError,
    OpenAICompatibleClient,
    OpenAICompatibleSettings,
)


MODELS = {
    "extraction": "gpt-5.6-terra",
    "analysis": "gpt-5.6-terra",
    "librarian_planning": "gpt-5.6-terra",
    "librarian_synthesis": "gpt-5.6-terra",
}


class _Response:
    ok = True
    status_code = 200

    def __init__(self, message):
        self.message = message

    def json(self):
        return {"choices": [{"message": self.message}]}


class _Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, endpoint, **kwargs):
        self.calls.append((endpoint, kwargs))
        return self.response


class _FalseySession(_Session):
    def __bool__(self):
        return False


class OpenAICompatibleProviderTests(unittest.TestCase):
    def settings(self, provider="openai", models=None):
        return OpenAICompatibleSettings(
            provider_id=provider,
            task_models=models or MODELS,
            api_key="sk-test-secret",
            credential_ref=f"{provider}.default",
            max_attempts=1,
            verification_mode=provider == "openai",
        )

    def test_openai_json_request_uses_only_registry_endpoint_and_no_redirects(self) -> None:
        session = _Session(_Response({"content": '{"status":"ok"}'}))
        client = OpenAICompatibleClient(self.settings(), session=session)
        self.assertEqual(client.smoke_test()["provider_id"], "openai")
        endpoint, kwargs = session.calls[0]
        self.assertEqual(endpoint, "https://api.openai.com/v1/chat/completions")
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["json"]["response_format"], {"type": "json_object"})
        self.assertEqual(kwargs["json"]["max_completion_tokens"], 32)
        self.assertNotIn("max_tokens", kwargs["json"])
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sk-test-secret")

    def test_injected_requests_compatible_session_is_used_even_when_falsey(self) -> None:
        session = _FalseySession(_Response({"content": '{"status":"ok"}'}))
        client = OpenAICompatibleClient(self.settings(), session=session)
        self.assertTrue(client.smoke_test()["ok"])
        self.assertEqual(len(session.calls), 1)

    def test_openai_tool_request_uses_selected_planning_model(self) -> None:
        session = _Session(
            _Response(
                {
                    "content": "",
                    "tool_calls": [{"id": "call-1", "type": "function"}],
                }
            )
        )
        client = OpenAICompatibleClient(
            OpenAICompatibleSettings(
                provider_id="deepseek",
                task_models={
                    "extraction": "deepseek-v4-pro",
                    "analysis": "deepseek-v4-pro",
                    "librarian_planning": "deepseek-v4-flash",
                    "librarian_synthesis": "deepseek-v4-pro",
                },
                api_key="sk-test-secret",
                max_attempts=1,
            ),
            session=session,
        )
        result = client.request_tool_message(
            [{"role": "user", "content": "plan"}],
            [{"type": "function", "function": {"name": "search", "parameters": {}}}],
            task="librarian_planning",
        )
        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertEqual(session.calls[0][1]["json"]["model"], "deepseek-v4-flash")

    def test_vendor_thinking_control_is_not_sent_to_openai(self) -> None:
        client = OpenAICompatibleClient(self.settings(), session=_Session(None))
        with self.assertRaises(AIProviderCapabilityError) as raised:
            client.request_json([], thinking=True)
        self.assertEqual(raised.exception.capability, "vendor_thinking_control")

    def test_deepseek_profile_keeps_existing_thinking_shape(self) -> None:
        models = {
            "extraction": "deepseek-v4-pro",
            "analysis": "deepseek-v4-pro",
            "librarian_planning": "deepseek-v4-flash",
            "librarian_synthesis": "deepseek-v4-pro",
        }
        session = _Session(_Response({"content": '{"ok":true}'}))
        client = OpenAICompatibleClient(
            self.settings("deepseek", models=models), session=session
        )
        client.request_json([], thinking=False)
        endpoint, kwargs = session.calls[0]
        self.assertEqual(endpoint, "https://api.deepseek.com/chat/completions")
        self.assertEqual(kwargs["json"]["thinking"], {"type": "disabled"})
        self.assertEqual(kwargs["json"]["max_tokens"], 16_000)
        self.assertNotIn("max_completion_tokens", kwargs["json"])

    def test_redirect_is_rejected_without_following_location(self) -> None:
        response = _Response({"content": "ignored"})
        response.ok = False
        response.status_code = 307
        client = OpenAICompatibleClient(self.settings(), session=_Session(response))
        with self.assertRaises(AIProviderResponseError):
            client.request_json([])

    def test_direct_runtime_settings_reject_unsafe_model_and_credential_ref(self) -> None:
        bad_models = dict(MODELS)
        bad_models["analysis"] = "https://evil.example/model"
        with self.assertRaises(AIProviderResponseError):
            self.settings(models=bad_models)
        with self.assertRaises(AIProviderResponseError):
            OpenAICompatibleSettings(
                provider_id="openai",
                task_models=MODELS,
                api_key="secret",
                credential_ref="/tmp/key",
            )

    def test_public_error_contract_contains_no_request_or_secret(self) -> None:
        error = AIProviderCapabilityError("tool_calling")
        payload = error.public_dict()
        self.assertEqual(payload["code"], "ai_provider_capability_missing")
        self.assertFalse(payload["retryable"])
        self.assertNotIn("secret", repr(payload))
        unknown = AIProviderCapabilityError("/Users/private/key").public_dict()
        self.assertEqual(unknown["details"], {"capability": "unknown"})

    def test_openai_public_status_requires_account_verification(self) -> None:
        status = self.settings().public_status()
        rendered = repr(status)
        self.assertFalse(status["configured"])
        self.assertFalse(status["available"])
        self.assertFalse(status["verified"])
        self.assertEqual(status["availability"], "verification_required")
        self.assertNotIn("endpoint", rendered)
        self.assertNotIn("credential_ref", rendered)

    def test_unlisted_openai_model_fails_closed_before_network(self) -> None:
        with self.assertRaises(AIProviderResponseError):
            OpenAICompatibleSettings(
                provider_id="openai",
                task_models={**MODELS, "analysis": "gpt-arbitrary"},
                api_key="sk-test-secret",
                credential_ref="openai.default",
                max_attempts=1,
            )

    def test_reviewed_openai_allows_only_verification_smoke_until_activated(self) -> None:
        session = _Session(_Response({"content": '{"status":"ok"}'}))
        client = OpenAICompatibleClient(self.settings(), session=session)
        with self.assertRaises(AIProviderCapabilityError):
            client.request_json([])
        with self.assertRaises(AIProviderCapabilityError):
            client.request_tool_message(
                [],
                [{"type": "function", "function": {"name": "unsafe", "parameters": {}}}],
            )
        self.assertTrue(client.smoke_test()["ok"])
        self.assertEqual(len(session.calls), 1)


if __name__ == "__main__":
    unittest.main()
