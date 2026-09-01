from __future__ import annotations

from types import MappingProxyType, SimpleNamespace
import unittest

from auto_research.ai.harness_contract import HarnessError
from auto_research.evidence.harness_federated_backend import (
    HarnessFederatedBackend,
    evidence_identities,
    official_candidates,
    official_source_binding,
)


def document(uid="item-1", kind="item", **updates):
    value = {
        "source_scope": "official",
        "source_id": "official-v1",
        "entity_type": kind,
        "entity_uid": uid,
        "paper_uid": "paper-1",
        "bundle_uid": "bundle-1",
        "article_title": "Tungsten irradiation",
        "doi": "10.1000/example",
        "meaning": "辐照硬度",
        "value_text": "4.2 GPa",
        "source_page": 5,
        "source_locator": "Table 2",
        "source_excerpt": "The hardness was 4.2 GPa.",
    }
    value.update(updates)
    return value


class Session:
    def __init__(self, documents=None):
        self.documents = list(documents or [document()])
        self.fingerprint = "f" * 64

    def status(self):
        return {
            "official_ready": True,
            "official_source": {
                "source_scope": "official",
                "source_id": "official-v1",
                "fingerprint": self.fingerprint,
            },
        }

    def search(self, query="", **filters):
        self.last_filters = filters
        return SimpleNamespace(
            hits=tuple(SimpleNamespace(document=row) for row in self.documents)
        )

    def get(self, **identity):
        for row in self.documents:
            if row["entity_uid"] == identity["entity_uid"]:
                return dict(row)
        raise KeyError("missing")


class HarnessFederatedBackendTests(unittest.TestCase):
    def test_candidates_are_official_bounded_and_path_free(self):
        session = Session([document(), document("figure-1", "figure", caption="Figure 1")])
        rows = official_candidates(session, query="硬度")
        self.assertEqual(len(rows), 2)
        self.assertEqual(session.last_filters["source_scopes"], ("official",))
        self.assertEqual(session.last_filters["source_ids"], ("official-v1",))
        self.assertEqual({item.source_scope for item in evidence_identities(rows)}, {"official"})
        self.assertNotIn("path", str(rows).casefold())

    def test_backend_fixed_tools_use_only_frozen_records(self):
        backend = HarnessFederatedBackend([document()])
        rows = backend.exact_search(
            {"query": "硬度", "entity_types": ["item"], "limit": 10}
        )
        self.assertEqual(rows[0]["ref"], "R1")
        identity = {
            "source_scope": "official",
            "source_id": "official-v1",
            "entity_type": "item",
            "entity_uid": "item-1",
        }
        self.assertEqual(backend.evidence_detail(identity)["value_text"], "4.2 GPa")
        self.assertFalse(backend.source_view(identity)["available"])
        self.assertEqual(backend.citation_verify(["R1"])[0]["bundle_uid"], "bundle-1")
        self.assertEqual(backend.recommend_papers({"question": "硬度", "limit": 3})[0]["paper_uid"], "paper-1")

    def test_prepared_action_frozen_official_table_is_revalidated(self):
        frozen = MappingProxyType(
            document(
                "table-1",
                "table",
                label="Table 1",
                materials=("316H", "HEA"),
                physical_quantities=("hardness",),
                tags=("irradiation",),
                variables=MappingProxyType(
                    {"columns": ("material", "hardness_GPa")}
                ),
            )
        )
        backend = HarnessFederatedBackend((frozen,))
        row = backend.documents[0]
        self.assertEqual(row["materials"], ["316H", "HEA"])
        self.assertEqual(
            row["variables"], {"columns": ["material", "hardness_GPa"]}
        )
        self.assertEqual(backend.citation_verify(["R1"])[0]["entity_uid"], "table-1")

    def test_private_or_internal_fields_fail_closed(self):
        private = document(source_scope="private", source_id="mine")
        with self.assertRaises(HarnessError) as caught:
            HarnessFederatedBackend([private])
        self.assertEqual(caught.exception.code, "harness_private_forbidden")
        with self.assertRaises(HarnessError):
            HarnessFederatedBackend([document(paper_id=7)])

    def test_source_fingerprint_changes_with_active_official_source(self):
        session = Session()
        source_id, before = official_source_binding(session)
        session.fingerprint = "e" * 64
        self.assertEqual(source_id, "official-v1")
        self.assertNotEqual(before, official_source_binding(session)[1])


if __name__ == "__main__":
    unittest.main()
