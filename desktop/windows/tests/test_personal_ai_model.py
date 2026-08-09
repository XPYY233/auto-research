from __future__ import annotations

import sys
import unittest
from pathlib import Path

from auto_research.ai.deepseek import DeepSeekNotConfigured


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT))
try:
    import personal_ai_model as MODULE
finally:
    sys.path.pop(0)


class _Resolver:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def resolve_for_runtime(self):
        self.calls += 1
        return self.value


class _Client:
    def __init__(self, settings, captured):
        captured.append(settings)

    def request_json(self, messages, **kwargs):
        return {"ok": True, "message_count": len(messages), "task": kwargs["task"]}


class PersonalAIModelTests(unittest.TestCase):
    def test_resolves_current_credential_for_each_call_without_public_secret(self) -> None:
        resolver = _Resolver("sk-user-owned")
        captured = []
        model = MODULE.WindowsDeepSeekPersonalSuggestionModel(
            resolver,
            client_factory=lambda settings: _Client(settings, captured),
        )
        result = model.request_json([{"role": "user", "content": "safe"}], task="analysis")
        self.assertEqual(result["task"], "analysis")
        self.assertEqual(resolver.calls, 1)
        self.assertEqual(captured[0].api_key, "sk-user-owned")
        self.assertEqual(captured[0].credential_source, "Windows Credential Manager")
        self.assertNotIn("sk-user-owned", str(result))

        resolver.value = "sk-rotated"
        model.request_json([{"role": "user", "content": "safe"}], task="analysis")
        self.assertEqual(resolver.calls, 2)
        self.assertEqual(captured[1].api_key, "sk-rotated")

    def test_missing_byok_key_fails_before_client_construction(self) -> None:
        resolver = _Resolver(None)
        with self.assertRaises(DeepSeekNotConfigured):
            MODULE.WindowsDeepSeekPersonalSuggestionModel(
                resolver,
                client_factory=lambda _settings: self.fail("client must not be built"),
            ).request_json([], task="analysis")


if __name__ == "__main__":
    unittest.main()
