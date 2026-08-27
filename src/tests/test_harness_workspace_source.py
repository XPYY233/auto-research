from __future__ import annotations

from types import SimpleNamespace
import unittest

from auto_research.ai.harness_contract import HarnessError
from auto_research.evidence.harness_federated_backend import (
    sanitize_workspace_documents,
)
from auto_research.evidence.harness_workspace_source import HarnessWorkspaceSource


class _Index:
    def __init__(self, row):
        self.row = dict(row)
        self.search_calls = 0

    def ensure_fresh(self):
        return {"fingerprint": "f" * 64}

    def source_fingerprint(self):
        return "f" * 64

    def search(self, _query, **_kwargs):
        self.search_calls += 1
        return SimpleNamespace(rows=(dict(self.row),), total=1)

    def get(self, entity_type, entity_id):
        if entity_type != self.row["entity_type"] or entity_id != self.row["entity_id"]:
            raise KeyError
        return dict(self.row)


class HarnessWorkspaceSourceTests(unittest.TestCase):
    def test_get_resolves_stable_public_identity_without_accepting_row_id(self):
        row = {
            "entity_type": "item",
            "entity_id": 31,
            "paper_id": 7,
            "article_title": "Irradiation hardening",
            "doi": "10.1000/hardening",
            "year": 2026,
            "first_author": "A. Researcher",
            "stable_key": "hardness-after-irradiation",
            "meaning": "辐照后硬度",
            "value_text": "4.2",
            "unit": "GPa",
            "source_page": 5,
            "quality_gate_status": "published",
        }
        public = sanitize_workspace_documents((row,))[0]
        source = object.__new__(HarnessWorkspaceSource)
        source._index = _Index(row)
        source._identity_lock = __import__("threading").RLock()
        source._identity_fingerprint = ""
        source._identity_cache = {}

        resolved = source.get(
            entity_type="item", entity_uid=public["entity_uid"]
        )

        self.assertEqual(resolved["entity_uid"], public["entity_uid"])
        self.assertNotIn("entity_id", resolved)
        self.assertEqual(source._index.search_calls, 1)
        source.get(entity_type="item", entity_uid=public["entity_uid"])
        self.assertEqual(source._index.search_calls, 1)

    def test_get_rejects_unknown_public_identity(self):
        row = {
            "entity_type": "finding",
            "entity_id": 9,
            "paper_id": 4,
            "article_title": "Irradiation finding",
            "doi": "10.1000/finding",
            "year": 2025,
            "first_author": "B. Researcher",
            "stable_key": "finding-one",
            "finding_text": "硬度上升",
            "source_page": 3,
            "quality_gate_status": "published",
        }
        source = object.__new__(HarnessWorkspaceSource)
        source._index = _Index(row)
        source._identity_lock = __import__("threading").RLock()
        source._identity_fingerprint = ""
        source._identity_cache = {}

        with self.assertRaises(HarnessError) as raised:
            source.get(entity_type="finding", entity_uid="entity_finding_" + "0" * 32)
        self.assertEqual(raised.exception.code, "harness_tool_forbidden")


if __name__ == "__main__":
    unittest.main()
