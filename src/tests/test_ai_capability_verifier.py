from __future__ import annotations

import unittest

from auto_research.ai.capability_verifier import (
    OpenAICompatibleCapabilityVerifier,
)
from auto_research.ai.openai_compatible import (
    AIProviderError,
    AIProviderNotConfigured,
    AIProviderResponseError,
)


class _Response:
    ok = True
    status_code = 200

    def __init__(self, message):
        self._message = message

    def json(self):
        return {"choices": [{"message": self._message}]}


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, endpoint, **kwargs):
        self.calls.append((endpoint, kwargs))
        if not self.responses:
            raise AssertionError("verification exceeded its two-call budget")
        return self.responses.pop(0)


class _Resolver:
    def __init__(self, key="test-secret-value"):
        self.key = key
        self.refs = []

    def resolve(self, credential_ref):
        self.refs.append(credential_ref)
        return self.key


def _tool_message(name="auto_research_capability_check", arguments=None):
    arguments = arguments or '{"verification":"tool_calling"}'
    return {
        "content": "",
        "tool_calls": [
            {
                "id": "fixed-check",
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        ],
    }


class CapabilityVerifierTests(unittest.TestCase):
    def verifier(self, provider="openai", *, resolver=None, responses=None):
        resolver = resolver or _Resolver()
        session = _Session(
            responses
            or [
                _Response({"content": '{"status":"ok"}'}),
                _Response(_tool_message()),
            ]
        )
        return (
            OpenAICompatibleCapabilityVerifier(resolver, session=session),
            resolver,
            session,
        )

    def verify(self, verifier, provider="openai", model="gpt-5.6-terra"):
        return verifier.verify_model(
            provider_id=provider,
            model=model,
            credential_ref=f"{provider}.default",
            required_capabilities=("structured_json", "tool_calling"),
        )

    def test_openai_runs_exactly_two_fixed_bounded_probes(self):
        verifier, resolver, session = self.verifier()
        result = self.verify(verifier)
        self.assertTrue(result.structured_json)
        self.assertTrue(result.tool_calling)
        self.assertEqual(result.provider_id, "openai")
        self.assertEqual(resolver.refs, ["openai.default"])
        self.assertEqual(len(session.calls), 2)
        for endpoint, kwargs in session.calls:
            self.assertEqual(endpoint, "https://api.openai.com/v1/chat/completions")
            self.assertFalse(kwargs["allow_redirects"])
            self.assertEqual(kwargs["json"]["model"], "gpt-5.6-terra")
            self.assertEqual(kwargs["json"]["max_completion_tokens"], 32)
        structured = session.calls[0][1]["json"]
        self.assertEqual(structured["response_format"], {"type": "json_object"})
        tool = session.calls[1][1]["json"]
        self.assertEqual(
            tool["tool_choice"],
            {
                "type": "function",
                "function": {"name": "auto_research_capability_check"},
            },
        )
        self.assertEqual(len(tool["tools"]), 1)

    def test_connection_probe_is_exactly_one_small_structured_request(self):
        verifier, resolver, session = self.verifier(
            responses=[_Response({"content": '{"status":"ok"}'})]
        )
        result = verifier.verify_connection(
            provider_id="openai",
            model="gpt-5.6-terra",
            credential_ref="openai.default",
        )
        self.assertTrue(result.structured_json)
        self.assertFalse(result.tool_calling)
        self.assertEqual(resolver.refs, ["openai.default"])
        self.assertEqual(len(session.calls), 1)
        self.assertFalse(session.calls[0][1]["allow_redirects"])

    def test_deepseek_uses_same_client_with_provider_payload_shape(self):
        verifier, _, session = self.verifier(provider="deepseek")
        result = self.verify(verifier, "deepseek", "deepseek-v4-pro")
        self.assertEqual(result.provider_id, "deepseek")
        self.assertEqual(len(session.calls), 2)
        for endpoint, kwargs in session.calls:
            self.assertEqual(endpoint, "https://api.deepseek.com/chat/completions")
            self.assertEqual(kwargs["json"]["max_tokens"], 32)
            self.assertNotIn("max_completion_tokens", kwargs["json"])

    def test_tool_response_must_be_the_unique_fixed_call_and_is_not_executed(self):
        for message in (
            {"content": "", "tool_calls": []},
            _tool_message(name="other_tool"),
            _tool_message(arguments='{"verification":"wrong"}'),
            {
                "content": "",
                "tool_calls": [_tool_message()["tool_calls"][0]] * 2,
            },
        ):
            verifier, _, session = self.verifier(
                responses=[
                    _Response({"content": '{"status":"ok"}'}),
                    _Response(message),
                ]
            )
            with self.subTest(message=message), self.assertRaises(AIProviderResponseError):
                self.verify(verifier)
            self.assertEqual(len(session.calls), 2)

    def test_invalid_structured_response_fails_without_partial_result(self):
        verifier, _, session = self.verifier(
            responses=[_Response({"content": '{"status":"wrong"}'})]
        )
        with self.assertRaises(AIProviderResponseError):
            self.verify(verifier)
        self.assertEqual(len(session.calls), 1)

    def test_missing_key_fails_before_any_network_call(self):
        verifier, _, session = self.verifier(resolver=_Resolver(None))
        with self.assertRaises(AIProviderNotConfigured):
            self.verify(verifier)
        self.assertEqual(session.calls, [])

    def test_redirect_is_rejected_and_never_followed(self):
        redirect = _Response({"content": "ignored"})
        redirect.ok = False
        redirect.status_code = 307
        verifier, _, session = self.verifier(responses=[redirect])
        with self.assertRaises(AIProviderResponseError):
            self.verify(verifier)
        self.assertEqual(len(session.calls), 1)
        self.assertFalse(session.calls[0][1]["allow_redirects"])

    def test_public_result_and_errors_never_expose_secret_or_transport(self):
        verifier, _, _ = self.verifier()
        rendered = repr(self.verify(verifier)).casefold()
        for forbidden in ("secret", "endpoint", "token", "credential", "path"):
            self.assertNotIn(forbidden, rendered)
        error = AIProviderResponseError("safe").public_dict()
        rendered_error = repr(error).casefold()
        for forbidden in ("secret", "endpoint", "token", "credential", "path"):
            self.assertNotIn(forbidden, rendered_error)

    def test_unsupported_capability_set_and_unlisted_model_fail_closed(self):
        verifier, _, session = self.verifier()
        with self.assertRaises(AIProviderError):
            verifier.verify_model(
                provider_id="openai",
                model="gpt-5.6-terra",
                credential_ref="openai.default",
                required_capabilities=("structured_json",),
            )
        with self.assertRaises(AIProviderError):
            self.verify(verifier, model="gpt-arbitrary")
        self.assertEqual(session.calls, [])


if __name__ == "__main__":
    unittest.main()
