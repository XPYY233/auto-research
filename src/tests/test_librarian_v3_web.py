from __future__ import annotations

import io
import json
import unittest
from email.message import Message
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

from auto_research.evidence.webapp import (
    LIBRARIAN_TRANSPORT_FAILED_CODE,
    LIBRARIAN_TRANSPORT_FAILED_MESSAGE,
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


class _AllowAllRateLimiter:
    @staticmethod
    def allow(_client_key: str) -> bool:
        return True


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

    def test_route_redacts_unexpected_runtime_canary_from_transport_response(self) -> None:
        canary = (
            "provider failure /Users/private/library.sqlite "
            "api-token=sk-sensitive-canary parser-detail"
        )
        handler = EvidenceHandler.__new__(EvidenceHandler)
        handler.path = "/api/agents/librarian/chat"
        handler.read_only = False
        handler.client_address = ("127.0.0.1", 49152)
        handler.agent_rate_limiter = _AllowAllRateLimiter()
        handler.db = object()  # type: ignore[assignment]
        handler.read_librarian_json = lambda: {"question": "钨的缺陷", "history": []}
        responses: list[tuple[dict, HTTPStatus]] = []
        handler.json_response = lambda payload, status=HTTPStatus.OK: responses.append(
            (payload, status)
        )

        with patch(
            "auto_research.evidence.webapp.execute_librarian_chat_request",
            side_effect=RuntimeError(canary),
        ):
            handler.do_POST()

        self.assertEqual(
            responses,
            [
                (
                    {
                        "error": LIBRARIAN_TRANSPORT_FAILED_MESSAGE,
                        "code": LIBRARIAN_TRANSPORT_FAILED_CODE,
                    },
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            ],
        )
        self.assertNotIn(canary, json.dumps(responses, ensure_ascii=False, default=str))


class LibrarianV3WebUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        cls.app_css = "\n".join(
            (WEB_DIR / name).read_text(encoding="utf-8")
            for name in ("app.css", "workbench.css")
        )

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
        self.assertIn("clearLibrarianPendingSynthesis();", reset_slice)
        save_slice = self.app_js[
            self.app_js.index("function saveLibrarianSession"):
            self.app_js.index("async function loadLibrarianHistory")
        ]
        self.assertNotIn("job_token", save_slice)

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
        self.assertEqual(submit.count("/api/agents/librarian/chat"), 0)
        self.assertIn("librarianResearchRequest(question, history)", submit)
        self.assertNotIn("paper_ids", submit)
        self.assertNotIn("console.log", submit)
        self.assertLess(submit.index("authorizePreparedAIAction('librarian', 'librarian'"), submit.index("executePreparedAIAction('librarian'"))
        self.assertIn("未向 ${aiProviderLabel()} 发送任何内容", submit)

    def test_two_stage_librarian_requires_separate_consent_and_keeps_job_token_ephemeral(self) -> None:
        for marker in (
            "let librarianPendingSynthesis = null",
            "librarian-ai-stage-v1",
            "requires_second_consent !== true",
            "function showLibrarianSynthesisStage",
            "function continueLibrarianSynthesis",
            "{ job_token: pending.jobToken }",
            "阶段 1/2",
            "阶段 2/2",
            "已停止，未进行第二次收费调用",
        ):
            self.assertIn(marker, self.app_js)
        self.assertIn('id="librarian-synthesis-stage"', (WEB_DIR / "index.html").read_text(encoding="utf-8"))
        self.assertIn('id="librarian-synthesis-continue"', (WEB_DIR / "index.html").read_text(encoding="utf-8"))
        self.assertNotIn("job_token", self.app_js[self.app_js.index("function saveLibrarianSession"):self.app_js.index("async function loadLibrarianHistory")])
        persistence = self.app_js[
            self.app_js.index("function readLocalLibrarianHistory"):
            self.app_js.index("function resetLibrarian")
        ]
        self.assertNotIn("job_token", persistence)
        for marker in ("toast(`", "toast('", "toast(\""):
            for line in self.app_js.splitlines():
                if marker in line:
                    self.assertNotIn("job_token", line)
        index = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("job_token", index)
        search_mode = self.app_js[
            self.app_js.index("function setSearchExperience"):
            self.app_js.index("function moveTabFocus")
        ]
        self.assertIn("if (mode !== 'agent') clearLibrarianPendingSynthesis();", search_mode)

    def test_local_zero_model_result_skips_consent_and_execution(self) -> None:
        authorization = self.app_js[
            self.app_js.index("async function authorizePreparedAIAction"):
            self.app_js.index("async function prepareAIAction")
        ]
        self.assertLess(authorization.index('prepared?.librarian_core_version === "librarian-v3"'), authorization.index("currentAIConsentContext(scope)"))
        self.assertIn("return { local_result: prepared }", authorization)

    def test_transient_failure_does_not_append_or_persist_a_fake_assistant_answer(self) -> None:
        failure = self.app_js[
            self.app_js.index("function failLibrarianRequest"):
            self.app_js.index("async function submitLibrarian")
        ]
        self.assertNotIn("librarianMessages.push", failure)
        self.assertNotIn("saveLibrarianSession", failure)
        self.assertIn("safe_failure_code: failureCode", failure)

    def test_transport_canary_cannot_enter_librarian_history_meta_or_toast(self) -> None:
        submit = self.app_js[
            self.app_js.index("async function submitLibrarian"):
            self.app_js.index("function getLatestLibrarianBriefSnapshot")
        ]
        catch = submit[submit.index("} catch (error) {"):]
        self.assertNotIn("error.message", catch)
        self.assertIn(
            "图书管理员暂时无法开始 AI 请求；精确检索仍可正常使用。",
            submit,
        )
        self.assertIn("failLibrarianRequest(error, context.recoveryQuestion)", catch)
        failure = self.app_js[
            self.app_js.index("function failLibrarianRequest"):
            self.app_js.index("async function submitLibrarian")
        ]
        self.assertNotIn("error.message", failure)
        self.assertIn("safe_failure_code: failureCode", failure)
        self.assertIn(
            "toast('图书管理员暂时无法完成本次请求，请稍后重试。', true)",
            failure,
        )
        self.assertIn("function librarianTransportFailureCode(error)", self.app_js)
        self.assertIn("'librarian_transport_failed'", self.app_js)


if __name__ == "__main__":
    unittest.main()
