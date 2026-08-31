from __future__ import annotations

import inspect
import io
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
from auto_research.evidence.federated_search_session import SearchSourceRegistration  # noqa: E402
from federated_search_api import (  # noqa: E402
    DesktopFederatedSearchError,
    DesktopFederatedSearchService,
    FederatedSearchAPI,
)


def _document(
    entity_type: str,
    entity_uid: str,
    *,
    source_scope: str = "official",
    source_id: str | None = None,
    text: str = "辐照后硬度变化",
) -> dict:
    source_id = source_id or (
        "official-preview" if source_scope == "official" else "private-lab"
    )
    return {
        "schema_version": "official-evidence-document-v1",
        "entity_type": entity_type,
        "source_scope": source_scope,
        "source_id": source_id,
        "entity_uid": entity_uid,
        "article_title": "Tungsten irradiation study",
        "display_title": text,
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


class _PrivateSource:
    def __init__(self, documents=None) -> None:
        self.documents = documents or (
            _document(
                "table",
                "private-table-1",
                source_scope="private",
                text="私人样品硬度表",
            ),
            _document(
                "figure",
                "private-figure-1",
                source_scope="private",
                text="私人实验趋势图",
            ),
        )

    def iter_search_documents(self):
        yield from self.documents


class _BrokenSource:
    def iter_search_documents(self):
        raise RuntimeError("private implementation detail")
        yield


class _PdfLease:
    media_type = "application/pdf"

    def __init__(
        self,
        *,
        source_scope: str = "private",
        source_id: str = "literature-test",
        paper_uid: str = "paper-test",
    ) -> None:
        self.source_scope = source_scope
        self.source_id = source_id
        self.paper_uid = paper_uid
        self._payload = io.BytesIO(b"%PDF-1.7\n%%EOF\n")
        self.size_bytes = len(self._payload.getvalue())
        self.closed = False

    def read(self, size=1024 * 1024):
        return self._payload.read(size)

    def close(self):
        self.closed = True

    def public_metadata(self):
        return {
            "schema_version": "private-pdf-lease-v1",
            "source_scope": self.source_scope,
            "source_id": self.source_id,
            "paper_uid": self.paper_uid,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
        }

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


class _PdfSource:
    def __init__(self) -> None:
        self.last_lease = None

    def iter_search_documents(self):
        document = _document(
            "item",
            "private-item-pdf",
            source_scope="private",
            source_id="literature-test",
        )
        document.update(
            {
                "paper_uid": "paper-test",
                "collection_kind": "literature_collection",
                "pdf_available": True,
            }
        )
        yield document

    def open_pdf(self, paper_uid):
        if paper_uid != "paper-test":
            return None
        self.last_lease = _PdfLease()
        return self.last_lease


class _OfficialPdfSource(_Repository):
    def __init__(self) -> None:
        document = _document("item", "official-item-pdf")
        document.update(
            {
                "paper_uid": "paper-test",
                "collection_kind": "literature_collection",
                "pdf_available": True,
            }
        )
        super().__init__((document,))
        self.last_lease = None

    def open_pdf(self, paper_uid):
        if paper_uid != "paper-test":
            return None
        self.last_lease = _PdfLease(
            source_scope="official",
            source_id="official-preview",
        )
        return self.last_lease


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
        self.wfile = io.BytesIO()
        self.status = None
        self.headers = {}

    def json_response(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.responses.append((payload, status))

    def send_response(self, status: HTTPStatus) -> None:
        self.status = status

    def send_header(self, name: str, value: str) -> None:
        self.headers[name] = value

    def end_headers(self) -> None:
        return None


class DesktopFederatedSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = DesktopFederatedSearchService()
        self.api = FederatedSearchAPI(self.service)

    def test_missing_package_fails_closed(self) -> None:
        with self.assertRaises(DesktopFederatedSearchError) as raised:
            self.service.search(query="")
        self.assertEqual(raised.exception.code, "evidence_package_required")
        self.assertEqual(raised.exception.status, HTTPStatus.CONFLICT)
        self.assertEqual(
            self.service.status(),
            {
                "schema_version": "federated-search-readiness-v2",
                "official_ready": False,
                "private_ready": False,
                "federated_ready": False,
                "document_count": 0,
                "official_source": None,
                "private_source": None,
            },
        )

    def test_install_rebuilds_from_audited_repository_identity(self) -> None:
        repository = _Repository()
        self.service.install_official_repository(_active(), repository)
        status = self.service.status()
        self.assertTrue(status["official_ready"])
        self.assertFalse(status["private_ready"])
        self.assertTrue(status["federated_ready"])
        self.assertEqual(status["document_count"], 2)
        self.assertEqual(status["official_source"]["source_id"], "official-preview")
        self.assertEqual(status["official_source"]["fingerprint"], "a" * 64)
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
        self.assertFalse(self.service.status()["federated_ready"])

    def test_official_private_and_both_source_combinations_are_searchable(self) -> None:
        official_only = DesktopFederatedSearchService()
        official_only.install_official_repository(_active(), _Repository())
        self.assertEqual(official_only.search(query="", page_size=10)["total"], 2)
        self.assertTrue(official_only.status()["official_ready"])
        self.assertFalse(official_only.status()["private_ready"])

        private_only = DesktopFederatedSearchService()
        private_only.refresh_private_source(
            _PrivateSource(),
            source_id="private-lab",
            fingerprint="revision:private-1",
        )
        self.assertEqual(private_only.search(query="", page_size=10)["total"], 2)
        self.assertFalse(private_only.status()["official_ready"])
        self.assertTrue(private_only.status()["private_ready"])

        combined = DesktopFederatedSearchService()
        combined.refresh_private_source(
            _PrivateSource(),
            source_id="private-lab",
            fingerprint="revision:private-1",
        )
        combined.install_official_repository(_active(), _Repository())
        page = combined.search(query="", page_size=10)
        self.assertEqual(page["total"], 4)
        self.assertEqual(
            {hit["document"]["source_scope"] for hit in page["results"]},
            {"official", "private"},
        )
        self.assertTrue(combined.status()["official_ready"])
        self.assertTrue(combined.status()["private_ready"])

    def test_clear_official_preserves_private_source_and_search(self) -> None:
        self.service.refresh_private_source(
            _PrivateSource(),
            source_id="private-lab",
            fingerprint="revision:private-1",
        )
        self.service.install_official_repository(_active(), _Repository())

        self.service.clear_official_repository()

        status = self.service.status()
        self.assertFalse(status["official_ready"])
        self.assertTrue(status["private_ready"])
        self.assertTrue(status["federated_ready"])
        self.assertEqual(status["document_count"], 2)
        page = self.service.search(query="", page_size=10)
        self.assertEqual(
            {hit["document"]["source_scope"] for hit in page["results"]},
            {"private"},
        )

    def test_failed_private_refresh_preserves_previous_fingerprint(self) -> None:
        self.service.install_official_repository(_active(), _Repository())
        self.service.refresh_private_source(
            _PrivateSource(),
            source_id="private-lab",
            fingerprint="revision:private-1",
        )
        before = self.service.status()

        with self.assertRaises(DesktopFederatedSearchError) as raised:
            self.service.refresh_private_source(
                _BrokenSource(),
                source_id="private-lab",
                fingerprint="revision:private-2",
            )

        self.assertEqual(raised.exception.code, "federated_private_source_invalid")
        self.assertEqual(self.service.status(), before)
        self.assertEqual(
            self.service.status()["private_source"]["fingerprint"],
            "revision:private-1",
        )
        status = self.service.status()
        self.assertTrue(status["official_ready"])
        self.assertEqual(status["official_source"]["fingerprint"], "a" * 64)
        self.assertEqual(self.service.search(query="", page_size=10)["total"], 4)

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

    def test_imported_collection_pdf_streams_from_descriptor_lease(self) -> None:
        source = _PdfSource()
        self.service.session.upsert_private(
            SearchSourceRegistration.literature_collection(
                source,
                source_id="literature-test",
                fingerprint="pdf-fingerprint-1",
            )
        )
        handler = _Handler(
            "/api/desktop/federated-pdf?source_scope=private"
            "&source_id=literature-test&paper_uid=paper-test"
        )
        self.assertTrue(self.api.handle_get(handler))
        self.assertEqual(handler.status, HTTPStatus.OK)
        self.assertEqual(handler.headers["Content-Type"], "application/pdf")
        self.assertEqual(handler.headers["Cache-Control"], "no-store")
        self.assertEqual(handler.wfile.getvalue(), b"%PDF-1.7\n%%EOF\n")
        self.assertTrue(source.last_lease.closed)
        self.assertNotIn("path", json.dumps(source.last_lease.public_metadata()))

        invalid = _Handler(
            "/api/desktop/federated-pdf?source_id=literature-test"
        )
        self.assertTrue(self.api.handle_get(invalid))
        self.assertEqual(invalid.responses[0][1], HTTPStatus.BAD_REQUEST)

    def test_official_pdf_requires_explicit_official_source_identity(self) -> None:
        source = _OfficialPdfSource()
        self.service.install_official_repository(_active(), source)
        handler = _Handler(
            "/api/desktop/federated-pdf?source_scope=official"
            "&source_id=official-preview&paper_uid=paper-test"
        )
        self.assertTrue(self.api.handle_get(handler))
        self.assertEqual(handler.status, HTTPStatus.OK)
        self.assertEqual(handler.headers["Content-Type"], "application/pdf")
        self.assertEqual(handler.wfile.getvalue(), b"%PDF-1.7\n%%EOF\n")
        self.assertTrue(source.last_lease.closed)

        wrong_scope = _Handler(
            "/api/desktop/federated-pdf?source_scope=private"
            "&source_id=official-preview&paper_uid=paper-test"
        )
        self.assertTrue(self.api.handle_get(wrong_scope))
        self.assertEqual(wrong_scope.responses[0][1], HTTPStatus.NOT_FOUND)

    def test_invalid_filters_and_unknown_routes_are_bounded(self) -> None:
        self.service.install_official_repository(_active(), _Repository())
        invalid = _Handler(
            "/api/desktop/federated-search?page_size=101&entity_type=experiment"
        )
        self.assertTrue(self.api.handle_get(invalid))
        self.assertEqual(invalid.responses[0][1], HTTPStatus.BAD_REQUEST)
        self.assertEqual(invalid.responses[0][0]["code"], "federated_search_invalid")
        self.assertFalse(self.api.handle_get(_Handler("/api/desktop/unknown")))

    def test_clear_last_official_source_fails_closed(self) -> None:
        self.service.install_official_repository(_active(), _Repository())
        self.assertTrue(self.service.status()["federated_ready"])
        self.service.clear_official_repository()
        self.assertFalse(self.service.status()["federated_ready"])
        with self.assertRaises(DesktopFederatedSearchError) as raised:
            self.service.search(query="")
        self.assertEqual(raised.exception.code, "evidence_package_required")

    def test_core_public_dto_guard_rejects_local_paths(self) -> None:
        unsafe = _document("item", "unsafe")
        unsafe["pdf_path"] = "/Users/private/paper.pdf"
        with self.assertRaises(DesktopFederatedSearchError) as raised:
            self.service.install_official_repository(_active(), _Repository((unsafe,)))
        self.assertEqual(
            raised.exception.code,
            "federated_repository_identity_invalid",
        )
        self.assertNotIn("/Users/private", str(raised.exception))

    def test_service_has_no_local_federated_engine_lifecycle(self) -> None:
        source = inspect.getsource(DesktopFederatedSearchService)
        self.assertNotIn("self._active", source)
        self.assertNotIn("self._private_source", source)
        self.assertNotIn("self._search", source)
        self.assertNotIn("FederatedEvidenceSearch", source)
        self.assertTrue(hasattr(self.service, "session"))


if __name__ == "__main__":
    unittest.main()
