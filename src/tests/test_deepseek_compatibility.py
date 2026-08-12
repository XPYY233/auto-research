from __future__ import annotations

import inspect
import unittest

from auto_research.ai.deepseek import DeepSeekClient, DeepSeekSettings


class _Response:
    ok = True
    status_code = 200

    @staticmethod
    def json():
        return {"choices": [{"message": {"content": '{"status":"ok"}'}}]}


class _Session:
    calls = []

    @classmethod
    def post(cls, endpoint, **kwargs):
        cls.calls.append((endpoint, kwargs))
        return _Response()


class DeepSeekCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        _Session.calls = []

    def test_legacy_client_delegates_to_one_redirect_safe_transport(self) -> None:
        client = DeepSeekClient(DeepSeekSettings(api_key="fake"), session=_Session())
        self.assertEqual(client.request_json([]), {"status": "ok"})
        endpoint, kwargs = _Session.calls[0]
        self.assertEqual(endpoint, "https://api.deepseek.com/chat/completions")
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["json"]["max_tokens"], 16_000)

    def test_compatibility_module_has_no_duplicate_http_transport(self) -> None:
        source = inspect.getsource(__import__(
            "auto_research.ai.deepseek", fromlist=["DeepSeekClient"]
        ))
        self.assertNotIn("requests.post", source)
        self.assertNotIn("session.post", source)
        self.assertNotIn("status_code == 429", source)

    def test_public_status_omits_legacy_recipient_and_credential_metadata(self) -> None:
        status = DeepSeekSettings(
            api_key="fake",
            credential_source="/Users/private/key",
        ).public_status()
        self.assertNotIn("base_url", status)
        self.assertNotIn("credential_source", status)
        self.assertNotIn("/Users/", repr(status))


if __name__ == "__main__":
    unittest.main()
