from __future__ import annotations

import unittest

from auto_research.desktop.research_memory import (
    ResearchMemoryError,
    ResearchMemoryService,
)


class MemoryStore:
    storage_label = "test-aes-256-gcm"

    def __init__(self) -> None:
        self.snapshot = {"revision": 0, "items": []}

    def load(self):
        return {"revision": self.snapshot["revision"], "items": list(self.snapshot["items"])}

    def save(self, snapshot):
        self.snapshot = snapshot


def item(**overrides):
    value = {
        "user_approved": True,
        "origin": "assistant_suggested",
        "title": "高温辐照后的硬度变化",
        "content": "已核验证据显示，在给定温度与剂量下硬度发生变化。",
        "source_refs": [
            {
                "source_scope": "official",
                "source_id": "official-package-v2",
                "entity_type": "item",
                "entity_uid": "item:stable:1",
                "paper_uid": "paper_stable_1",
                "doi": "10.1000/example",
                "page": 7,
                "title": "硬度测量",
            }
        ],
    }
    value.update(overrides)
    return value


class ResearchMemoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryStore()
        self.service = ResearchMemoryService(self.store)

    def test_requires_explicit_approval_and_verified_public_source(self) -> None:
        with self.assertRaisesRegex(ResearchMemoryError, "明确确认"):
            self.service.mutate({"action": "create", "expected_revision": 0, "item": item(user_approved=False)})
        with self.assertRaisesRegex(ResearchMemoryError, "公开文献证据"):
            self.service.mutate(
                {
                    "action": "create",
                    "expected_revision": 0,
                    "item": item(source_refs=[{**item()["source_refs"][0], "source_scope": "private"}]),
                }
            )
        self.assertEqual(self.service.get().revision, 0)

    def test_create_edit_delete_and_clear_use_revision_cas(self) -> None:
        created = self.service.mutate({"action": "create", "expected_revision": 0, "item": item()})
        self.assertEqual(created.revision, 1)
        self.assertEqual(len(created.items), 1)
        memory = created.items[0]
        self.assertEqual(memory["approval"], "user_approved")
        self.assertNotIn("user_approved", memory)

        with self.assertRaisesRegex(ResearchMemoryError, "刷新后重试"):
            self.service.mutate({"action": "delete", "expected_revision": 0, "memory_uid": memory["memory_uid"]})

        updated = self.service.mutate(
            {
                "action": "update",
                "expected_revision": 1,
                "item": {
                    **memory,
                    "user_approved": True,
                    "title": "用户修订后的标题",
                    "content": "用户重新核验并修订的结论。",
                },
            }
        )
        self.assertEqual(updated.items[0]["title"], "用户修订后的标题")
        cleared = self.service.mutate({"action": "clear", "expected_revision": 2, "confirm_clear": True})
        self.assertEqual(cleared.revision, 3)
        self.assertEqual(cleared.items, ())

    def test_forbids_paths_credentials_and_unknown_request_fields(self) -> None:
        for content in (
            "/Users/researcher/secret.pdf",
            "api_key = sk-abcdefghijklmnop",
        ):
            with self.subTest(content=content), self.assertRaises(ResearchMemoryError):
                self.service.mutate(
                    {"action": "create", "expected_revision": 0, "item": item(content=content)}
                )
        with self.assertRaisesRegex(ResearchMemoryError, "字段无效"):
            self.service.mutate(
                {
                    "action": "create",
                    "expected_revision": 0,
                    "item": item(),
                    "path": "/tmp/leak",
                }
            )

    def test_authenticated_snapshot_is_revalidated_before_projection(self) -> None:
        created = self.service.mutate(
            {"action": "create", "expected_revision": 0, "item": item()}
        )
        corrupted = dict(created.items[0])
        corrupted["source_refs"] = [
            {**corrupted["source_refs"][0], "source_scope": "private"}
        ]
        self.store.snapshot = {"revision": 1, "items": [corrupted]}
        with self.assertRaises(ResearchMemoryError) as raised:
            self.service.get()
        self.assertEqual(raised.exception.code, "research_memory_store_unavailable")
        self.assertEqual(raised.exception.http_status, 503)


if __name__ == "__main__":
    unittest.main()
