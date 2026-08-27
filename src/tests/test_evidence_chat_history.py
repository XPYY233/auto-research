from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime, timedelta, timezone

from auto_research.desktop.evidence_chat_history import (
    EvidenceChatHistoryError,
    EvidenceChatHistoryService,
    derive_thread_uid,
)


class MemoryHistoryStore:
    storage_label = "test-aes-256-gcm"

    def __init__(self) -> None:
        self.value: object = None
        self.fail_load = False
        self.fail_save = False
        self.clear_calls = 0

    def load(self) -> object:
        if self.fail_load:
            raise RuntimeError("/Users/researcher/history.enc: key=secret")
        return copy.deepcopy(self.value)

    def save(self, value: object) -> None:
        if self.fail_save:
            raise RuntimeError("sqlite:/tmp/history.db")
        self.value = copy.deepcopy(value)

    def clear(self) -> None:
        self.clear_calls += 1
        self.value = None


class FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: int) -> None:
        self.value += timedelta(**kwargs)


def message(index: int = 1, *, role: str | None = None, content: str | None = None) -> dict[str, object]:
    return {
        "role": role or ("user" if index % 2 else "assistant"),
        "content": content or f"第 {index} 轮证据问题或回答。",
    }


def thread(*, scope: str = "official", uid: str = "item-stable-1", **updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "source_scope": scope,
        "source_id": "official-package-v2" if scope == "official" else "workspace",
        "entity_type": "item",
        "entity_uid": uid,
        "title": "辐照后硬度证据问答",
        "messages": [message(1), message(2)],
    }
    value.update(updates)
    return value


def identity(value: dict[str, object]) -> dict[str, object]:
    return {
        "source_scope": value["source_scope"],
        "source_id": value["source_id"],
        "entity_type": value["entity_type"],
        "entity_uid": value["entity_uid"],
    }


class EvidenceChatHistoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryHistoryStore()
        self.clock = FakeClock()
        self.service = EvidenceChatHistoryService(self.store, clock=self.clock)

    def upsert(self, value: dict[str, object], revision: int) -> dict[str, object]:
        return self.service.mutate(
            {"operation": "upsert", "expected_revision": revision, "thread": value}
        )

    def test_empty_state_has_path_free_public_contract(self) -> None:
        state = self.service.get()
        self.assertEqual(
            state,
            {
                "schema_version": "evidence-chat-history-v1",
                "revision": 0,
                "storage": "test-aes-256-gcm",
                "threads": [],
            },
        )

    def test_upsert_derives_identity_and_preserves_created_at(self) -> None:
        created = self.upsert(thread(), 0)
        saved = created["threads"][0]
        expected_uid = derive_thread_uid(identity(thread()))
        self.assertEqual(saved["thread_uid"], expected_uid)
        self.assertEqual(saved["schema_version"], "evidence-chat-thread-v1")
        created_at = saved["created_at"]

        self.clock.advance(minutes=4)
        updated_thread = thread(
            title="用户继续追问",
            messages=[message(index) for index in range(1, 26)],
        )
        updated = self.upsert(updated_thread, 1)
        current = updated["threads"][0]
        self.assertEqual(current["created_at"], created_at)
        self.assertNotEqual(current["updated_at"], created_at)
        self.assertEqual(len(current["messages"]), 20)
        self.assertEqual(current["messages"][0]["content"], "第 6 轮证据问题或回答。")
        self.assertEqual(current["messages"][-1]["content"], "第 25 轮证据问题或回答。")

    def test_thread_limit_retains_twenty_most_recent(self) -> None:
        revision = 0
        for index in range(21):
            state = self.upsert(thread(uid=f"item-{index}"), revision)
            revision = state["revision"]
            self.clock.advance(seconds=1)
        self.assertEqual(len(state["threads"]), 20)
        uids = {row["entity_uid"] for row in state["threads"]}
        self.assertNotIn("item-0", uids)
        self.assertIn("item-20", uids)

    def test_load_deterministically_prunes_threads_older_than_thirty_days(self) -> None:
        created = self.upsert(thread(), 0)
        self.assertEqual(created["revision"], 1)
        self.clock.advance(days=30, seconds=1)
        pruned = self.service.get()
        self.assertEqual(pruned["threads"], [])
        self.assertEqual(pruned["revision"], 2)
        self.assertEqual(self.store.value["revision"], 2)
        self.assertEqual(self.service.get()["revision"], 2)

    def test_revision_delete_and_confirmed_clear(self) -> None:
        created = self.upsert(thread(), 0)
        with self.assertRaises(EvidenceChatHistoryError) as conflict:
            self.service.mutate(
                {
                    "operation": "delete",
                    "expected_revision": 0,
                    "identity": identity(thread()),
                }
            )
        self.assertEqual(conflict.exception.code, "evidence_chat_history_revision_conflict")
        self.assertEqual(conflict.exception.http_status, 409)

        deleted = self.service.mutate(
            {
                "operation": "delete",
                "expected_revision": created["revision"],
                "identity": identity(thread()),
            }
        )
        self.assertEqual(deleted["threads"], [])
        with self.assertRaises(EvidenceChatHistoryError) as missing:
            self.service.mutate(
                {
                    "operation": "delete",
                    "expected_revision": deleted["revision"],
                    "identity": identity(thread()),
                }
            )
        self.assertEqual((missing.exception.code, missing.exception.http_status), ("evidence_chat_history_not_found", 404))

        restored = self.upsert(thread(), deleted["revision"])
        with self.assertRaisesRegex(EvidenceChatHistoryError, "再次确认"):
            self.service.mutate(
                {"operation": "clear", "expected_revision": restored["revision"], "confirm_clear": False}
            )
        cleared = self.service.mutate(
            {"operation": "clear", "expected_revision": restored["revision"], "confirm_clear": True}
        )
        self.assertEqual(cleared["threads"], [])
        self.assertEqual(self.service.get()["revision"], cleared["revision"])

    def test_official_and_workspace_public_identity_are_separate_and_stable(self) -> None:
        official = self.upsert(thread(scope="official", uid="42"), 0)
        workspace = self.upsert(thread(scope="workspace", uid="42"), 1)
        self.assertEqual(len(workspace["threads"]), 2)
        self.assertNotEqual(
            workspace["threads"][0]["thread_uid"],
            official["threads"][0]["thread_uid"],
        )
        self.assertEqual(workspace["threads"][0]["source_scope"], "workspace")
        serialized = json.dumps(workspace, ensure_ascii=False)
        for forbidden in ("asset_id", "paper_id", "reviewer", "file_id", "database_id"):
            self.assertNotIn(forbidden, serialized)

    def test_private_type_extra_and_client_thread_uid_are_rejected(self) -> None:
        invalid_threads = (
            thread(scope="private"),
            thread(entity_type="dataset"),
            thread(path="/tmp/private.pdf"),
            thread(thread_uid="ech_client_chosen"),
            thread(asset_id=12),
        )
        for value in invalid_threads:
            with self.subTest(value=value), self.assertRaises(EvidenceChatHistoryError):
                self.upsert(value, 0)
        self.assertEqual(self.service.get()["revision"], 0)

    def test_forbidden_payloads_and_overlong_messages_fail_closed(self) -> None:
        contents = (
            "saved at /Users/researcher/paper.pdf",
            "notes are in ~/private/result.txt",
            "api_key=sk-abcdefghijklmnop",
            "paper_id=712 should stay internal",
            "database_id=44 must stay internal",
            "%PDF-1.7\n1 0 obj\n",
            "data:image/png;base64,iVBORw0KGgoAAAA",
            "x" * 12_001,
        )
        for content in contents:
            with self.subTest(content=content[:40]), self.assertRaises(EvidenceChatHistoryError):
                self.upsert(thread(messages=[message(content=content)]), 0)

    def test_annotations_are_strict_bounded_and_path_free(self) -> None:
        valid = thread(
            messages=[
                {
                    "role": "assistant",
                    "content": "当前证据只支持该温度条件。",
                    "annotations": {
                        "pages": [7, 7, 8],
                        "notes": ["数值来自当前条目。"],
                        "limitations": ["不能外推到其他剂量。"],
                    },
                }
            ]
        )
        saved = self.upsert(valid, 0)["threads"][0]["messages"][0]
        self.assertEqual(saved["annotations"]["pages"], [7, 8])

        invalid_annotations = (
            {"pages": [0]},
            {"pages": [1], "prompt": "hidden"},
            {"notes": ["/home/user/private.txt"]},
            {"limitations": "not-a-list"},
            {"pages": list(range(1, 22))},
        )
        for annotations in invalid_annotations:
            with self.subTest(annotations=annotations), self.assertRaises(EvidenceChatHistoryError):
                self.service.mutate(
                    {
                        "operation": "upsert",
                        "expected_revision": 1,
                        "thread": thread(
                            messages=[{"role": "assistant", "content": "回答", "annotations": annotations}]
                        ),
                    }
                )

    def test_unknown_request_fields_and_invalid_operations_are_rejected(self) -> None:
        requests = (
            {"operation": "upsert", "expected_revision": 0, "thread": thread(), "model": "x"},
            {"operation": "clear", "expected_revision": 0, "confirm_clear": True, "path": "/tmp"},
            {"operation": "merge", "expected_revision": 0},
            {"operation": "upsert", "expected_revision": True, "thread": thread()},
        )
        for body in requests:
            with self.subTest(body=body), self.assertRaises(EvidenceChatHistoryError):
                self.service.mutate(body)

    def test_corrupt_authenticated_store_never_projects(self) -> None:
        saved = self.upsert(thread(), 0)
        corrupted = copy.deepcopy(self.store.value)
        corrupted["threads"][0]["thread_uid"] = "ech_tampered"
        self.store.value = corrupted
        with self.assertRaises(EvidenceChatHistoryError) as raised:
            self.service.get()
        self.assertEqual((raised.exception.code, raised.exception.http_status), ("evidence_chat_history_store_unavailable", 503))

        self.store.value = {"schema_version": "wrong", "revision": 1, "threads": saved["threads"]}
        with self.assertRaises(EvidenceChatHistoryError) as wrong_version:
            self.service.get()
        self.assertEqual(wrong_version.exception.code, "evidence_chat_history_store_unavailable")

    def test_store_failures_have_stable_path_free_error_dto(self) -> None:
        self.store.fail_load = True
        with self.assertRaises(EvidenceChatHistoryError) as load_error:
            self.service.get()
        public = load_error.exception.public_dict()
        self.assertEqual(public["schema_version"], "evidence-chat-history-error-v1")
        self.assertEqual(public["code"], "evidence_chat_history_store_unavailable")
        self.assertEqual(public["http_status"], 503)
        self.assertNotIn("Users", json.dumps(public))
        self.assertNotIn("secret", json.dumps(public))

        self.store.fail_load = False
        self.store.fail_save = True
        with self.assertRaises(EvidenceChatHistoryError) as save_error:
            self.upsert(thread(), 0)
        public = save_error.exception.public_dict()
        self.assertEqual(public["code"], "evidence_chat_history_store_unavailable")
        self.assertNotIn("sqlite", json.dumps(public))
        self.assertIsNone(self.store.value)


if __name__ == "__main__":
    unittest.main()
