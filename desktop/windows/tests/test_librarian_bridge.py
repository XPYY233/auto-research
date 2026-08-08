from __future__ import annotations

import sys
import unittest
from pathlib import Path


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT))
try:
    import librarian_bridge as MODULE
finally:
    sys.path.pop(0)


class _Runtime:
    def __init__(self) -> None:
        self.calls = []
        self.response = {
            "response_format": "reasoning-presentation-v2",
            "answer": "answer",
            "report": {"review_map": [{"theme": "defects"}]},
            "results": [{
                "entity_type": "finding",
                "source_scope": "private",
                "source_id": "private-1",
                "entity_uid": "private:finding:1",
                "agent_bundle_uid": "bundle-1",
            }],
            "intent": {"id": "research_review"},
            "retrieval_policy": "review_map",
            "research_state": {"conversation_id": "conversation-1"},
            "state_token": "signed-state",
            "suggested_actions": [{"text": "解释R1"}],
            "review_map": [{"theme": "defects"}],
        }

    def run(self, question, **kwargs):
        self.calls.append((question, kwargs))
        return self.response


class LibrarianBridgeTests(unittest.TestCase):
    def test_v3_request_and_response_are_forwarded_unchanged(self) -> None:
        runtime = _Runtime()
        bridge = MODULE.LibrarianV3BridgeAdapter(runtime)
        state = {"conversation_id": "conversation-1"}
        result = bridge.chat(
            "解释R1",
            history=[{"role": "user", "content": "previous"}],
            research_state=state,
            state_token="signed-state",
            conversation_id="conversation-1",
        )
        self.assertIs(result, runtime.response)
        self.assertEqual(runtime.calls[0][0], "解释R1")
        self.assertIs(runtime.calls[0][1]["research_state"], state)
        self.assertEqual(result["results"][0]["agent_bundle_uid"], "bundle-1")

    def test_missing_runtime_is_stable_and_path_free(self) -> None:
        bridge = MODULE.LibrarianV3BridgeAdapter(None)
        self.assertFalse(bridge.available)
        with self.assertRaises(MODULE.LibrarianBridgeError) as raised:
            bridge.chat("question")
        self.assertEqual(raised.exception.code, "librarian_unavailable")
        self.assertNotIn("path", str(raised.exception.public_dict()).casefold())


if __name__ == "__main__":
    unittest.main()
