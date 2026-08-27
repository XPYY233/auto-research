from __future__ import annotations

import unittest

from auto_research.evidence.librarian_memory_context import (
    select_revalidated_memories,
)


def memory(uid: str, title: str, content: str, ref: dict) -> dict:
    return {
        "schema_version": "research-memory-item-v1",
        "memory_uid": uid,
        "title": title,
        "content": content,
        "source_refs": [ref],
        "approval": "user_approved",
        "origin": "user_created",
        "created_at": "2026-08-01T00:00:00+00:00",
        "updated_at": "2026-08-02T00:00:00+00:00",
    }


class LibrarianMemoryContextTests(unittest.TestCase):
    def test_only_relevant_revalidated_user_approved_memory_is_returned(self) -> None:
        hardness_ref = {
            "source_scope": "official",
            "source_id": "official-v1",
            "entity_type": "item",
            "entity_uid": "item-1",
            "page": 5,
            "title": "辐照硬度",
        }
        values = [
            memory("mem-hardness", "钨的辐照硬度", "300 °C 后硬度增加", hardness_ref),
            memory("mem-corrosion", "腐蚀电位", "盐水中的电化学结果", hardness_ref),
            {**memory("mem-draft", "辐照硬度草稿", "不应使用", hardness_ref), "approval": "draft"},
        ]

        result = select_revalidated_memories(
            question="钨在300 °C辐照后的硬度如何？",
            history=[],
            items=values,
            validate_ref=lambda ref: ref if ref["entity_uid"] == "item-1" else None,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "钨的辐照硬度")
        self.assertEqual(result[0]["status"], "user_approved_revalidated_context_only")
        self.assertNotIn("memory_uid", result[0])

    def test_unresolved_sources_and_unrelated_memory_are_not_injected(self) -> None:
        item = memory(
            "mem-1",
            "钨辐照硬度",
            "硬度增加",
            {
                "source_scope": "official",
                "source_id": "old-package",
                "entity_type": "item",
                "entity_uid": "missing",
                "page": 2,
                "title": "旧证据",
            },
        )
        self.assertEqual(
            select_revalidated_memories(
                question="钨的辐照硬度",
                history=[],
                items=[item],
                validate_ref=lambda _ref: None,
            ),
            (),
        )
        self.assertEqual(
            select_revalidated_memories(
                question="腐蚀电位",
                history=[],
                items=[item],
                validate_ref=lambda ref: ref,
            ),
            (),
        )

    def test_context_is_bounded_and_never_contains_store_identity(self) -> None:
        ref = {
            "source_scope": "official",
            "source_id": "official-v1",
            "entity_type": "finding",
            "entity_uid": "finding-1",
            "page": 8,
            "title": "硬度结论",
        }
        result = select_revalidated_memories(
            question="硬度",
            history=[],
            items=[memory(f"mem-{index}", f"硬度 {index}", "硬度 " + "甲" * 1200, ref) for index in range(10)],
            validate_ref=lambda value: value,
        )
        self.assertLessEqual(len(result), 4)
        self.assertLessEqual(sum(len(row["title"]) + len(row["content"]) for row in result), 3200)
        self.assertNotIn("mem-", repr(result))


if __name__ == "__main__":
    unittest.main()
