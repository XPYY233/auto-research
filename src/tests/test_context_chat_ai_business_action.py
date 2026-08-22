from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fitz

from auto_research.ai.business_actions import (
    BUSINESS_ACTION_SCOPES,
    BusinessActionError,
    BusinessPreparedActionRegistry,
)
from auto_research.ai.consent import AIConsentService
from auto_research.ai.prepared_actions import PreparedActionError, PreparedActionService
from auto_research.evidence.context_chat import (
    _read_stable_pdf_snapshot,
    prepare_context_chat,
)
from auto_research.evidence.context_chat_ai_business_action import (
    SELECTED_EVIDENCE_CHAT_MAX_TOKENS,
    SelectedEvidenceChatBusinessAssembler,
    SelectedEvidenceChatBusinessExecutor,
    SelectedEvidenceChatBusinessProjector,
    SelectedEvidenceChatSnapshotAuthority,
)
from auto_research.settings.ai_runtime_state import RuntimeActionBinding


class _Clock:
    def now(self) -> int:
        return 10_000


class _Runtime:
    def action_binding(self) -> RuntimeActionBinding:
        return RuntimeActionBinding(
            "deepseek",
            {
                "extraction": "deepseek-v4-pro",
                "analysis": "deepseek-v4-pro",
                "librarian_planning": "deepseek-v4-flash",
                "librarian_synthesis": "deepseek-v4-pro",
            },
            "deepseek.default",
            2,
            4,
            "connection_verified",
        )


class _DB:
    def __init__(self, pdf_path: Path) -> None:
        self.pdf_path = pdf_path
        self.paper = {
            "id": 7,
            "title": "Irradiation Hardness Study",
            "doi": "10.1000/example",
            "pdf_path": str(pdf_path),
        }
        self.item = {
            "item_id": 11,
            "paper_id": 7,
            "value_text": "4.0",
            "unit": "GPa",
            "meaning": "辐照后硬度",
            "context_explanation": "300 °C 辐照后的纳米压痕结果",
            "source_locator": "Table 3",
            "source_excerpt": "Hardness reached 4.0 GPa.",
            "source_page": 2,
        }
        self.get_paper_calls = 0

    def get_paper(self, paper_id: int):
        self.get_paper_calls += 1
        return dict(self.paper) if paper_id == 7 else None


class _RawClient:
    settings = SimpleNamespace(extraction_model="deepseek-v4-pro")

    def __init__(self, payload=None) -> None:
        self.payload = payload or {
            "answer": "该结果表示300 °C辐照后的硬度。[PDF第2页]",
            "evidence_pages": [2, 99],
            "evidence_notes": ["第2页给出温度和硬度。"],
            "limitations": ["没有未辐照对照。"],
        }
        self.calls = []

    def request_json(self, messages, **kwargs):
        self.calls.append((copy.deepcopy(messages), copy.deepcopy(kwargs)))
        return copy.deepcopy(self.payload)


class _Factory:
    def __init__(self, client: _RawClient) -> None:
        self.client = client
        self.calls = 0

    @contextmanager
    def acquire_bound(self, _action, *, max_attempts=1):
        self.calls += 1
        self.max_attempts = max_attempts
        yield self.client


class ContextChatBusinessActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.pdf = self.root / "paper.pdf"
        document = fitz.open()
        for number in range(1, 4):
            page = document.new_page()
            text = (
                "The specimen preparation method is described here."
                if number != 2
                else "The sample was irradiated at 300 degrees C. Table 3 reports hardness of 4.0 GPa."
            )
            page.insert_text((72, 72), text)
        document.save(self.pdf)
        document.close()
        self.db = _DB(self.pdf)
        self.item_patch = patch(
            "auto_research.evidence.context_chat.get_data_item",
            side_effect=lambda _db, item_id: dict(self.db.item)
            if item_id == 11
            else (_ for _ in ()).throw(KeyError(item_id)),
        )
        self.item_patch.start()
        self.assembler = SelectedEvidenceChatBusinessAssembler(self.db)
        self.executor = SelectedEvidenceChatBusinessExecutor()
        self.projector = SelectedEvidenceChatBusinessProjector()
        self.clock = _Clock()
        self.prepared = PreparedActionService(
            runtime_state=_Runtime(),
            consents=AIConsentService(
                clock=self.clock,
                secret_key=b"context-chat-consent-key-at-least-32-bytes",
            ),
            snapshots=SelectedEvidenceChatSnapshotAuthority(self.db),
            clock=self.clock,
        )

    def tearDown(self) -> None:
        self.item_patch.stop()
        self.temporary.cleanup()

    def _request(self):
        return {
            "entity_type": "item",
            "entity_id": 11,
            "question": "这个硬度对应什么实验条件？",
            "history": [{"role": "user", "content": "先看温度"}],
        }

    def _registry(self, client: _RawClient) -> BusinessPreparedActionRegistry:
        factory = _Factory(client)
        return BusinessPreparedActionRegistry(
            prepared_actions=self.prepared,
            client_factory=factory,
            assemblers={scope: self.assembler for scope in BUSINESS_ACTION_SCOPES},
            executors={scope: self.executor for scope in BUSINESS_ACTION_SCOPES},
            projectors={scope: self.projector for scope in BUSINESS_ACTION_SCOPES},
            clock=self.clock,
        )

    def _consume(self, summary, session="selected-session"):
        consent = self.prepared.issue_consent(
            action_id=summary["action_id"], session_id=session
        )
        return self.prepared.consume(
            action_id=summary["action_id"],
            consent_nonce=consent["nonce"],
            session_id=session,
        )

    def test_assembler_reuses_exact_domain_messages_and_rejects_ai_controls(self) -> None:
        domain = prepare_context_chat(self.db, **self._request())
        draft = self.assembler.assemble(self._request())
        call = draft.call_plan[0].canonical_dict()
        self.assertEqual(call["messages"], [dict(item) for item in domain.messages])
        self.assertEqual(call["task"], "extraction")
        self.assertEqual(call["max_tokens"], SELECTED_EVIDENCE_CHAT_MAX_TOKENS)
        self.assertEqual(call["options"], {"thinking": False, "temperature": 0.2})
        self.assertEqual(draft.max_calls, 1)
        self.assertEqual(len(draft.content_units), 1)

        for key, value in (
            ("provider_id", "openai"),
            ("model", "gpt-5.6-sol"),
            ("endpoint", "https://example.invalid"),
            ("task", "analysis"),
            ("max_tokens", 1),
            ("tools", []),
            ("pdf_text", "forged"),
        ):
            forged = {**self._request(), key: value}
            with self.subTest(key=key), self.assertRaises(BusinessActionError):
                self.assembler.assemble(forged)

    def test_full_pdf_fingerprint_invalidates_before_consent(self) -> None:
        registry = self._registry(_RawClient())
        summary = registry.prepare(
            scope="selected_evidence_chat",
            session_id="selected-session",
            request=self._request(),
        )
        with self.pdf.open("ab") as handle:
            handle.write(b"\n% changed after prepare")
        with self.assertRaises(PreparedActionError) as stale:
            self.prepared.issue_consent(
                action_id=summary["action_id"], session_id="selected-session"
            )
        self.assertEqual(stale.exception.code, "prepared_action_stale")

    def test_same_size_same_mtime_replacement_is_stale(self) -> None:
        registry = self._registry(_RawClient())
        summary = registry.prepare(
            scope="selected_evidence_chat",
            session_id="selected-session",
            request=self._request(),
        )
        original = self.pdf.read_bytes()
        original_stat = self.pdf.stat()
        replacement = bytearray(original)
        replacement[-1] ^= 1
        replacement_path = self.root / "replacement.pdf"
        replacement_path.write_bytes(replacement)
        os.utime(
            replacement_path,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        os.replace(replacement_path, self.pdf)
        os.utime(
            self.pdf,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        self.assertEqual(self.pdf.stat().st_size, original_stat.st_size)
        self.assertEqual(self.pdf.stat().st_mtime_ns, original_stat.st_mtime_ns)
        with self.assertRaises(PreparedActionError) as stale:
            self.prepared.issue_consent(
                action_id=summary["action_id"], session_id="selected-session"
            )
        self.assertEqual(stale.exception.code, "prepared_action_stale")

    def test_symlink_and_read_time_change_fail_closed(self) -> None:
        link = self.root / "linked.pdf"
        link.symlink_to(self.pdf)
        self.db.paper["pdf_path"] = str(link)
        with self.assertRaises(BusinessActionError) as symlink:
            self.assembler.assemble(self._request())
        self.assertEqual(symlink.exception.code, "business_action_prepare_failed")

        self.db.paper["pdf_path"] = str(self.pdf)
        real_identity = (
            self.pdf.stat().st_dev,
            self.pdf.stat().st_ino,
            self.pdf.stat().st_size,
            self.pdf.stat().st_mtime_ns,
            self.pdf.stat().st_ctime_ns,
        )
        identities = [real_identity, (*real_identity[:-1], real_identity[-1] + 1)]

        def changing_identity(_value):
            return identities.pop(0) if identities else real_identity

        with patch(
            "auto_research.evidence.context_chat._stat_identity",
            side_effect=changing_identity,
        ), self.assertRaises(ValueError):
            _read_stable_pdf_snapshot(str(self.pdf))

    def test_prepare_uses_one_pdf_open_for_messages_and_source_hash(self) -> None:
        real_open = os.open
        opened = 0

        def counted_open(*args, **kwargs):
            nonlocal opened
            opened += 1
            return real_open(*args, **kwargs)

        with patch(
            "auto_research.evidence.context_chat.os.open",
            side_effect=counted_open,
        ):
            prepared = prepare_context_chat(self.db, **self._request())
        self.assertEqual(opened, 1)
        self.assertEqual(len(prepared.source_fingerprint), 64)
        self.assertEqual(len(prepared.content_fingerprint), 64)

    def test_registry_executes_once_without_rereading_or_writing(self) -> None:
        client = _RawClient()
        registry = self._registry(client)
        summary = registry.prepare(
            scope="selected_evidence_chat",
            session_id="selected-session",
            request=self._request(),
        )
        action = self._consume(summary)
        reads_before = self.db.get_paper_calls
        item_before = copy.deepcopy(self.db.item)
        result = registry.execute(action)

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(registry._client_factory.calls, 1)
        self.assertEqual(registry._client_factory.max_attempts, 1)
        self.assertEqual(self.db.get_paper_calls, reads_before)
        self.assertEqual(self.db.item, item_before)
        self.assertEqual(result["evidence_pages"], [2])
        self.assertEqual(result["context_pages"], [1, 2, 3])
        self.assertEqual(result["entity"]["id"], 11)
        serialized = json.dumps(result, ensure_ascii=False)
        for forbidden in (str(self.root), "pdf_path", "api_key", "action_id", "content_fingerprint"):
            self.assertNotIn(forbidden, serialized)

        with self.assertRaises(BusinessActionError) as replayed:
            registry.execute(action)
        self.assertEqual(replayed.exception.code, "business_action_replayed")
        self.assertEqual(len(client.calls), 1)

    def test_invalid_model_result_and_projector_extras_fail_closed(self) -> None:
        registry = self._registry(_RawClient({"answer": ""}))
        summary = registry.prepare(
            scope="selected_evidence_chat",
            session_id="selected-session",
            request=self._request(),
        )
        with self.assertRaises(BusinessActionError) as invalid:
            registry.execute(self._consume(summary))
        self.assertEqual(invalid.exception.code, "business_action_result_invalid")

        result = {
            "answer": "answer",
            "evidence_pages": [2],
            "evidence_notes": [],
            "limitations": [],
            "context_pages": [2],
            "entity": {
                "type": "item",
                "id": 11,
                "summary": "summary",
                "paper_title": "title",
                "doi": None,
            },
            "model": "deepseek-v4-pro",
            "pdf_path": str(self.pdf),
        }
        with self.assertRaises(BusinessActionError) as extra:
            self.projector.project(result)
        self.assertEqual(extra.exception.code, "business_action_result_invalid")

        for invalid_answer in (123, "x" * 12_001):
            with self.subTest(invalid_answer=type(invalid_answer).__name__):
                invalid_value = dict(result)
                invalid_value.pop("pdf_path")
                invalid_value["answer"] = invalid_answer
                with self.assertRaises(BusinessActionError) as invalid_type:
                    self.projector.project(invalid_value)
                self.assertEqual(
                    invalid_type.exception.code,
                    "business_action_result_invalid",
                )

    def test_selected_adapter_cannot_prepare_under_other_scope(self) -> None:
        registry = self._registry(_RawClient())
        with self.assertRaises(BusinessActionError) as rejected:
            registry.prepare(
                scope="personal_suggestion",
                session_id="wrong-scope",
                request=self._request(),
            )
        self.assertEqual(rejected.exception.code, "business_action_invalid")


if __name__ == "__main__":
    unittest.main()
