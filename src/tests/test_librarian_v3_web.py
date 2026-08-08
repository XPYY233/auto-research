from __future__ import annotations

import io
import json
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from auto_research.evidence.webapp import (
    MAX_LIBRARIAN_CHAT_REQUEST_BYTES,
    MAX_LIBRARIAN_RESEARCH_STATE_BYTES,
    EvidenceHandler,
    LibrarianChatRequestError,
    execute_librarian_chat_request,
    validate_librarian_chat_request,
)


ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT / "src" / "auto_research" / "evidence" / "web"


class _Runtime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def run(self, question: str, **kwargs):
        self.calls.append((question, kwargs))
        return {"answer": "ok"}


class LibrarianV3WebAPITests(unittest.TestCase):
    @staticmethod
    def _handler(*headers: tuple[str, str], body: bytes = b"") -> EvidenceHandler:
        handler = EvidenceHandler.__new__(EvidenceHandler)
        message = Message()
        for name, value in headers:
            message.add_header(name, value)
        handler.headers = message
        handler.rfile = io.BytesIO(body)
        return handler

    def test_legacy_question_and_history_remain_compatible(self) -> None:
        runtime = _Runtime()
        body = {
            "question": "钨的空位形成能是多少？",
            "history": [{"role": "user", "content": "先查钨"}],
        }
        result = execute_librarian_chat_request(
            object(),  # type: ignore[arg-type]
            body,
            runtime_factory=lambda _db: runtime,  # type: ignore[arg-type]
        )
        self.assertEqual(result, {"answer": "ok"})
        self.assertEqual(
            runtime.calls,
            [(body["question"], {"history": body["history"]})],
        )

    def test_runtime_patch_seam_used_by_existing_http_contract_is_preserved(self) -> None:
        runtime = _Runtime()
        with patch(
            "auto_research.evidence.webapp.LibrarianAgentRuntime",
            lambda _db: runtime,
        ):
            execute_librarian_chat_request(
                object(),  # type: ignore[arg-type]
                {"question": "旧请求", "history": []},
            )
        self.assertEqual(runtime.calls, [("旧请求", {"history": []})])

    def test_v3_state_fields_are_forwarded_without_interpretation(self) -> None:
        runtime = _Runtime()
        state = {
            "schema_version": "research-state-v1",
            "conversation_id": "conversation-0123456789",
        }
        body = {
            "question": "详细解释R1",
            "history": [],
            "conversation_id": "conversation-0123456789",
            "research_state": state,
            "state_token": "signed-token",
        }
        execute_librarian_chat_request(
            object(),  # type: ignore[arg-type]
            body,
            runtime_factory=lambda _db: runtime,  # type: ignore[arg-type]
        )
        self.assertEqual(
            runtime.calls[0][1],
            {
                "history": [],
                "conversation_id": body["conversation_id"],
                "research_state": state,
                "state_token": "signed-token",
            },
        )

    def test_request_whitelist_rejects_old_scope_and_unknown_fields(self) -> None:
        for extra in ({"paper_ids": []}, {"path": "/private/user/library.sqlite"}):
            with self.subTest(extra=extra):
                with self.assertRaises(LibrarianChatRequestError) as raised:
                    validate_librarian_chat_request({
                        "question": "钨的辐照缺陷",
                        "history": [],
                        **extra,
                    })
                payload = raised.exception.public_dict()
                self.assertEqual(payload["code"], "librarian_request_invalid")
                self.assertNotIn("/private/user", json.dumps(payload))

    def test_history_conversation_token_and_state_are_bounded(self) -> None:
        cases = (
            (
                {"question": "有效问题", "history": [{"role": "system", "content": "x"}]},
                "librarian_history_invalid",
            ),
            (
                {"question": "有效问题", "history": [], "conversation_id": "bad conversation"},
                "librarian_conversation_invalid",
            ),
            (
                {"question": "有效问题", "history": [], "state_token": "x" * 513},
                "librarian_state_token_invalid",
            ),
            (
                {
                    "question": "有效问题",
                    "history": [],
                    "research_state": {"padding": "x" * MAX_LIBRARIAN_RESEARCH_STATE_BYTES},
                },
                "librarian_research_state_too_large",
            ),
        )
        for body, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(LibrarianChatRequestError) as raised:
                    validate_librarian_chat_request(body)
                self.assertEqual(raised.exception.code, code)

    def test_route_reader_requires_json_and_has_a_smaller_body_cap(self) -> None:
        raw = b'{"question":"ok","history":[]}'
        wrong_type = self._handler(
            ("Content-Type", "text/plain"),
            ("Content-Length", str(len(raw))),
            body=raw,
        )
        with self.assertRaises(LibrarianChatRequestError) as content_type:
            wrong_type.read_librarian_json()
        self.assertEqual(content_type.exception.code, "librarian_content_type_invalid")

        oversized = self._handler(
            ("Content-Type", "application/json"),
            ("Content-Length", str(MAX_LIBRARIAN_CHAT_REQUEST_BYTES + 1)),
        )
        with self.assertRaises(LibrarianChatRequestError) as too_large:
            oversized.read_librarian_json()
        self.assertEqual(too_large.exception.code, "librarian_request_too_large")


class LibrarianV3WebUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        cls.app_css = (WEB_DIR / "app.css").read_text(encoding="utf-8")

    def test_conversation_state_is_memory_only_and_resettable(self) -> None:
        self.assertIn("librarianResearchContexts: new Map()", self.app_js)
        self.assertIn("conversation_id: state.librarianSessionId", self.app_js)
        self.assertIn("request.research_state = context.research_state", self.app_js)
        self.assertIn("request.state_token = context.state_token", self.app_js)
        save_slice = self.app_js[
            self.app_js.index("function saveLibrarianSession"):
            self.app_js.index("async function loadLibrarianHistory")
        ]
        self.assertNotIn("research_state", save_slice)
        self.assertNotIn("state_token", save_slice)
        reset_slice = self.app_js[
            self.app_js.index("function resetLibrarian"):
            self.app_js.index("function bindLibrarianSuggestions")
        ]
        self.assertIn("clearLibrarianResearchContext();", reset_slice)

    def test_v3_rendering_and_recovery_are_present(self) -> None:
        for marker in (
            "function librarianIntentHtml",
            "function librarianReviewMapHtml",
            "result.suggested_actions",
            "result.review_map",
            "result.intent",
            "data-librarian-intent",
            "重新建立研究状态",
            "本轮不需要检索论文证据，因此没有证据卡片；这不是检索失败。",
        ):
            self.assertIn(marker, self.app_js)
        for selector in (
            ".librarian-intent",
            ".librarian-review-map",
            ".librarian-state-recovery",
        ):
            self.assertIn(selector, self.app_css)

    def test_submit_uses_one_endpoint_and_no_longer_sends_paper_scope(self) -> None:
        submit = self.app_js[
            self.app_js.index("async function submitLibrarian"):
            self.app_js.index("function getLatestLibrarianBriefSnapshot")
        ]
        self.assertEqual(submit.count("/api/agents/librarian/chat"), 1)
        self.assertIn("JSON.stringify(librarianResearchRequest(question, history))", submit)
        self.assertNotIn("paper_ids", submit)
        self.assertNotIn("console.log", submit)


if __name__ == "__main__":
    unittest.main()
