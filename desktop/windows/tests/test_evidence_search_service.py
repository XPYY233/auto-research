from __future__ import annotations

import sys
import unittest
from pathlib import Path


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(WINDOWS_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))
try:
    import evidence_search_bridge as BRIDGE
    import evidence_search_service as SERVICE
    from auto_research.evidence.federated_search import FederatedEvidenceSearch
finally:
    sys.path.pop(0)
    sys.path.pop(0)


class FakeActivePackage:
    def public_dict(self):
        return {
            "package_id": "official-preview",
            "package_version": "0.4.0-preview.1",
            "content_fingerprint": "c" * 64,
        }


class FakeSource:
    def __init__(self, documents):
        self.documents = tuple(documents)

    def iter_search_documents(self):
        return iter(self.documents)


def document(entity_type: str, index: int, *, scope: str = "official"):
    return {
        "entity_type": entity_type,
        "source_scope": scope,
        "source_id": "official-preview" if scope == "official" else "private-project",
        "entity_uid": f"{entity_type}_{index}",
        "display_title": f"Tungsten {entity_type}",
        "meaning": "irradiation temperature",
        "value_text": "300",
        "unit": "K",
    }


class EvidenceSearchServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.official = FakeSource(
            [document(kind, index) for index, kind in enumerate(("item", "finding", "table", "figure"))]
        )

    @staticmethod
    def service(**kwargs):
        return SERVICE.WindowsEvidenceSearchService(
            engine_factory=FederatedEvidenceSearch, **kwargs
        )

    def test_real_federated_engine_returns_four_types_and_stable_identity(self) -> None:
        private = FakeSource([document("item", 9, scope="private")])
        service = self.service(private_source=private)
        service.activate_official_repository(
            active_package=FakeActivePackage(), repository=self.official
        )
        self.assertTrue(service.is_ready)
        page = service.search("", page_size=20)
        documents = [hit["document"] for hit in page["results"]]
        self.assertEqual(
            {item["entity_type"] for item in documents},
            {"item", "finding", "table", "figure"},
        )
        self.assertEqual(
            {item["source_scope"] for item in documents}, {"official", "private"}
        )
        selected = documents[0]
        fetched = service.get(
            source_scope=selected["source_scope"],
            source_id=selected["source_id"],
            entity_uid=selected["entity_uid"],
        )
        self.assertEqual(fetched, selected)
        self.assertNotIn("id", fetched)
        self.assertNotIn("paper_id", fetched)

    def test_filters_are_passed_to_frozen_engine_without_reimplementation(self) -> None:
        service = self.service()
        service.activate_official_repository(
            active_package=FakeActivePackage(), repository=self.official
        )
        page = service.search(
            "temperature",
            entity_types=["figure"],
            source_scopes=["official"],
            source_ids=["official-preview"],
        )
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["results"][0]["document"]["entity_type"], "figure")

    def test_bad_source_or_projection_fails_closed_without_path(self) -> None:
        bad = FakeSource(
            [
                {
                    **document("item", 1),
                    "pdf_path": r"C:\Users\Researcher\secret.pdf",
                }
            ]
        )
        service = self.service()
        with self.assertRaises(SERVICE.EvidenceSearchError) as raised:
            service.activate_official_repository(
                active_package=FakeActivePackage(), repository=bad
            )
        self.assertEqual(raised.exception.code, "offline_search_activation_failed")
        self.assertFalse(service.is_ready)
        self.assertNotIn("C:\\Users", str(raised.exception))

    def test_bridge_preserves_public_dto_and_redacts_arbitrary_errors(self) -> None:
        service = self.service()
        service.activate_official_repository(
            active_package=FakeActivePackage(), repository=self.official
        )
        bridge = BRIDGE.EvidenceSearchBridgeAdapter(service)
        page = bridge.search("Tungsten", page_size=2)
        identity = page["results"][0]["document"]
        fetched = bridge.get(
            source_scope=identity["source_scope"],
            source_id=identity["source_id"],
            entity_uid=identity["entity_uid"],
        )
        self.assertEqual(fetched["entity_uid"], identity["entity_uid"])
        redacted = bridge.public_error(RuntimeError(r"failed at C:\private\db.sqlite"))
        self.assertEqual(redacted["code"], "search_failed")
        self.assertNotIn("C:\\private", str(redacted))
        typed = bridge.public_error(
            SERVICE.EvidenceSearchError(
                "search_failed", r"failed at C:\private\db.sqlite"
            )
        )
        self.assertNotIn("C:\\private", str(typed))


if __name__ == "__main__":
    unittest.main()
