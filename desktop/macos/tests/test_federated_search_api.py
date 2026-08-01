from __future__ import annotations

import json
import sys
import unittest
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = DESKTOP_ROOT.parents[1]
for path in (PROJECT_ROOT / "src", DESKTOP_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from auto_research.product import ActiveOfficialPackage  # noqa: E402
from federated_search_api import (  # noqa: E402
    DesktopFederatedSearchError,
    DesktopFederatedSearchService,
    FederatedSearchAPI,
)


def _document(entity_type: str, entity_uid: str) -> dict:
    return {
        "schema_version": "official-evidence-document-v1",
        "entity_type": entity_type,
        "source_scope": "official",
        "source_id": "official-preview",
        "entity_uid": entity_uid,
        "article_title": "Tungsten irradiation study",
        "display_title": "辐照后硬度变化",
        "source_excerpt": "硬度随剂量增加。",
        "doi": "10.1000/example",
        "source_page": 5,
    }


class _Repository:
    package_id = "official-preview"
    package_version = "0.1.0-preview.1"
    content_fingerprint = "a" * 64

    def __init__(self, documents=None) -> None:
        self.documents = documents or (
            _document("item", "entity-item-1"),
            _document("figure", "entity-figure-1"),
        )

    def iter_search_documents(self):
        yield from self.documents


def _active() -> ActiveOfficialPackage:
    return ActiveOfficialPackage(
        package_id="official-preview",
        package_version="0.1.0-preview.1",
        content_fingerprint="a" * 64,
        manifest_sha256="b" * 64,
    )


class _Handler:
    def __init__(self, path: str) -> None:
        self.path = path
        self.responses: list[tuple[object, HTTPStatus]] = []

    def json_response(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.responses.append((payload, status))


class DesktopFederatedSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = DesktopFederatedSearchService()
        self.api = FederatedSearchAPI(self.service)

    def test_missing_package_fails_closed(self) -> None:
        with self.assertRaises(DesktopFederatedSearchError) as raised:
            self.service.search(query="")
        self.assertEqual(raised.exception.code, "evidence_package_required")
        self.assertEqual(raised.exception.status, HTTPStatus.CONFLICT)
        self.assertEqual(self.service.status(), {"ready": False, "document_count": 0})

    def test_install_rebuilds_from_audited_repository_identity(self) -> None:
        repository = _Repository()
        self.service.install_official_repository(_active(), repository)
        status = self.service.status()
        self.assertTrue(status["ready"])
        self.assertEqual(status["document_count"], 2)
        page = self.service.search(query="硬度", source_scopes=("official",))
        self.assertEqual(page["total"], 2)
        self.assertEqual(page["results"][0]["document"]["source_scope"], "official")
        serialized = json.dumps(page, ensure_ascii=False).casefold()
        self.assertNotIn("paper_id", serialized)
        self.assertNotIn("item_id", serialized)
        self.assertNotIn("path", serialized)

    def test_repository_identity_mismatch_does_not_replace_index(self) -> None:
        mismatched = _Repository()
        mismatched.package_version = "different"
        with self.assertRaises(DesktopFederatedSearchError) as raised:
            self.service.install_official_repository(_active(), mismatched)
        self.assertEqual(
            raised.exception.code, "federated_repository_identity_invalid"
        )
        self.assertFalse(self.service.status()["ready"])

    def test_search_route_supports_browse_paging_and_repeated_filters(self) -> None:
        self.service.install_official_repository(_active(), _Repository())
        handler = _Handler(
            "/api/desktop/federated-search?q=&page=1&page_size=10"
            "&entity_type=item&entity_type=figure&source_scope=official"
            "&source_id=official-preview"
        )
        self.assertTrue(self.api.handle_get(handler))
        payload, status = handler.responses[0]
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["schema_version"], "federated-search-page-v1")

    def test_evidence_route_requires_complete_stable_identity(self) -> None:
        self.service.install_official_repository(_active(), _Repository())
        handler = _Handler(
            "/api/desktop/federated-evidence?source_scope=official"
            "&source_id=official-preview&entity_uid=entity-figure-1"
        )
        self.assertTrue(self.api.handle_get(handler))
        payload, status = handler.responses[0]
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["entity_type"], "figure")
        self.assertNotIn("id", payload)

        incomplete = _Handler(
            "/api/desktop/federated-evidence?source_scope=official"
            "&entity_uid=entity-figure-1"
        )
        self.assertTrue(self.api.handle_get(incomplete))
        self.assertEqual(incomplete.responses[0][1], HTTPStatus.BAD_REQUEST)
        self.assertEqual(
            incomplete.responses[0][0]["code"], "federated_identity_invalid"
        )

        repeated = _Handler(
            "/api/desktop/federated-evidence?source_scope=official"
            "&source_id=official-preview&source_id=other"
            "&entity_uid=entity-figure-1"
        )
        self.assertTrue(self.api.handle_get(repeated))
        self.assertEqual(
            repeated.responses[0][0]["code"], "federated_identity_invalid"
        )

    def test_invalid_filters_and_unknown_routes_are_bounded(self) -> None:
        self.service.install_official_repository(_active(), _Repository())
        invalid = _Handler(
            "/api/desktop/federated-search?page_size=101&entity_type=experiment"
        )
        self.assertTrue(self.api.handle_get(invalid))
        self.assertEqual(invalid.responses[0][1], HTTPStatus.BAD_REQUEST)
        self.assertEqual(invalid.responses[0][0]["code"], "federated_search_invalid")
        self.assertFalse(self.api.handle_get(_Handler("/api/desktop/unknown")))

    def test_clear_removes_stale_index_after_active_package_failure(self) -> None:
        self.service.install_official_repository(_active(), _Repository())
        self.assertTrue(self.service.status()["ready"])
        self.service.clear()
        self.assertFalse(self.service.status()["ready"])
        with self.assertRaises(DesktopFederatedSearchError) as raised:
            self.service.search(query="")
        self.assertEqual(raised.exception.code, "evidence_package_required")

    def test_core_public_dto_guard_rejects_local_paths(self) -> None:
        unsafe = _document("item", "unsafe")
        unsafe["pdf_path"] = "/Users/private/paper.pdf"
        with self.assertRaises(ValueError):
            self.service.install_official_repository(_active(), _Repository((unsafe,)))


if __name__ == "__main__":
    unittest.main()
