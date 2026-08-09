from __future__ import annotations

import json
import threading
import unittest
from collections.abc import Iterable, Mapping
from typing import Any

from auto_research.evidence.federated_search import FederatedEvidenceSearch
from auto_research.evidence.federated_search_session import (
    FederatedSearchSession,
    FederatedSearchSessionError,
    FederatedSearchSessionProtocol,
    SearchSourceRegistration,
)


def document(
    scope: str,
    source_id: str,
    entity_type: str,
    uid: str,
    text: str,
) -> dict[str, Any]:
    return {
        "schema_version": "synthetic-evidence-document-v1",
        "entity_type": entity_type,
        "source_scope": scope,
        "source_id": source_id,
        "entity_uid": uid,
        "display_title": text,
        "meaning_text": text,
        "context_text": f"{text} 实验条件",
        "source_excerpt": "合成测试证据",
    }


class MemorySource:
    def __init__(self, documents: Iterable[Mapping[str, Any]]) -> None:
        self.documents = tuple(dict(item) for item in documents)

    def iter_search_documents(self):
        yield from self.documents


class BrokenSource:
    def iter_search_documents(self):
        raise RuntimeError("/Users/private/repository.sqlite must never escape")
        yield


class BlockingSource(MemorySource):
    def __init__(self, documents, started: threading.Event, release: threading.Event) -> None:
        super().__init__(documents)
        self.started = started
        self.release = release

    def iter_search_documents(self):
        self.started.set()
        if not self.release.wait(timeout=2):
            raise RuntimeError("test replacement timed out")
        yield from self.documents


class FlakySource(MemorySource):
    def __init__(self, documents) -> None:
        super().__init__(documents)
        self.fail = False

    def iter_search_documents(self):
        if self.fail:
            raise RuntimeError("source refresh failed")
        yield from self.documents


def official_registration(version: str = "v1", *, source: object | None = None):
    source_id = f"official-{version}"
    source = source or MemorySource((
        document("official", source_id, "item", f"official-item-{version}", "钨 硬度"),
        document("official", source_id, "finding", f"official-finding-{version}", "辐照缺陷"),
    ))
    return SearchSourceRegistration.official(
        source, source_id=source_id, fingerprint=f"sha256:official-{version}"
    )


def private_registration(version: str = "v1", *, source: object | None = None):
    source_id = f"private-{version}"
    source = source or MemorySource((
        document("private", source_id, "table", f"private-table-{version}", "样品硬度表"),
        document("private", source_id, "figure", f"private-figure-{version}", "个人趋势图"),
    ))
    return SearchSourceRegistration.private(
        source, source_id=source_id, fingerprint=f"revision:private-{version}"
    )


class FederatedSearchSessionTests(unittest.TestCase):
    def test_protocol_and_no_source_fail_closed(self):
        session = FederatedSearchSession()
        self.assertIsInstance(session, FederatedSearchSessionProtocol)
        self.assertEqual(session.status(), {
            "schema_version": "federated-search-readiness-v2",
            "official_ready": False,
            "private_ready": False,
            "federated_ready": False,
            "document_count": 0,
            "official_source": None,
            "private_source": None,
        })
        with self.assertRaisesRegex(FederatedSearchSessionError, "尚未启用") as caught:
            session.search("硬度")
        self.assertEqual(caught.exception.code, "federated_search_unavailable")

    def test_official_private_and_both_are_exact_offline_searchable(self):
        official_only = FederatedSearchSession(official=official_registration())
        self.assertEqual(official_only.search("钨").total, 1)
        self.assertEqual(official_only.search("", source_scopes=("private",)).total, 0)

        private_only = FederatedSearchSession(private=private_registration())
        self.assertEqual(private_only.search("趋势图").total, 1)
        self.assertEqual(private_only.search("", source_scopes=("official",)).total, 0)

        both = FederatedSearchSession(
            official=official_registration(), private=private_registration()
        )
        self.assertEqual(both.search("", page_size=10).total, 4)
        self.assertEqual(both.search("硬度", source_scopes=("official",)).total, 1)
        self.assertEqual(both.search("硬度", source_scopes=("private",)).total, 1)

    def test_search_and_get_preserve_existing_four_type_dto_and_identity(self):
        session = FederatedSearchSession(
            official=official_registration(), private=private_registration()
        )
        page = session.search("", page_size=10)
        self.assertEqual(
            {hit.document["entity_type"] for hit in page.hits},
            {"item", "finding", "table", "figure"},
        )
        selected = page.hits[-1].document
        fetched = session.get(
            source_scope=selected["source_scope"],
            source_id=selected["source_id"],
            entity_uid=selected["entity_uid"],
        )
        self.assertEqual(fetched, dict(selected))
        self.assertEqual(
            {key: fetched[key] for key in ("source_scope", "source_id", "entity_uid")},
            {key: selected[key] for key in ("source_scope", "source_id", "entity_uid")},
        )

    def test_readiness_v2_is_path_free_and_uses_only_stable_identities(self):
        session = FederatedSearchSession(
            official=official_registration(), private=private_registration()
        )
        status = session.status()
        self.assertEqual(status["schema_version"], "federated-search-readiness-v2")
        self.assertTrue(status["official_ready"])
        self.assertTrue(status["private_ready"])
        self.assertTrue(status["federated_ready"])
        self.assertEqual(status["document_count"], 4)
        serialised = json.dumps(status, ensure_ascii=False)
        self.assertNotIn("/Users/", serialised)
        self.assertNotIn("database_path", serialised)
        self.assertEqual(
            set(status["official_source"]),
            {"source_scope", "source_id", "fingerprint"},
        )

    def test_install_replace_and_clear_keep_source_lifecycles_independent(self):
        session = FederatedSearchSession(
            official=official_registration(), private=private_registration()
        )
        session.install_official(official_registration("v2"))
        self.assertEqual(session.search("", page_size=10).total, 4)
        self.assertEqual(session.search("", source_ids=("official-v1",)).total, 0)
        self.assertEqual(session.search("", source_ids=("private-v1",)).total, 2)

        status = session.clear_official()
        self.assertFalse(status["official_ready"])
        self.assertTrue(status["private_ready"])
        self.assertTrue(status["federated_ready"])
        self.assertEqual(status["document_count"], 2)
        self.assertEqual(session.search("", page_size=10).total, 2)

        status = session.clear_private()
        self.assertFalse(status["federated_ready"])
        with self.assertRaises(FederatedSearchSessionError):
            session.search("")

    def test_failed_official_replacement_preserves_old_engine_and_private_source(self):
        session = FederatedSearchSession(
            official=official_registration(), private=private_registration()
        )
        before = session.status()
        broken = SearchSourceRegistration.official(
            BrokenSource(), source_id="official-v2", fingerprint="sha256:broken"
        )
        with self.assertRaises(FederatedSearchSessionError) as caught:
            session.install_official(broken)
        self.assertEqual(caught.exception.code, "federated_source_activation_failed")
        self.assertNotIn("/Users/", caught.exception.safe_message)
        self.assertEqual(session.status(), before)
        self.assertEqual(session.search("", page_size=10).total, 4)

    def test_failed_private_refresh_preserves_previous_confirmed_index(self):
        session = FederatedSearchSession(
            official=official_registration(), private=private_registration()
        )
        before = session.search("个人趋势图").hits[0].document["entity_uid"]
        broken = SearchSourceRegistration.private(
            BrokenSource(), source_id="private-v2", fingerprint="revision:broken"
        )
        with self.assertRaises(FederatedSearchSessionError):
            session.refresh_private(broken)
        self.assertEqual(session.status()["private_source"]["source_id"], "private-v1")
        self.assertEqual(session.search("个人趋势图").hits[0].document["entity_uid"], before)

    def test_multiple_private_sources_coexist_and_personal_refresh_preserves_collections(self):
        session = FederatedSearchSession(private=private_registration("personal-v1"))
        collection = private_registration("literature-v1")
        session.upsert_private(collection)
        self.assertEqual(session.search("", page_size=10).total, 4)
        self.assertEqual(
            session.search("", source_ids=("private-literature-v1",)).total,
            2,
        )

        session.refresh_private(private_registration("personal-v2"))
        self.assertEqual(session.search("", page_size=10).total, 4)
        self.assertEqual(
            session.search("", source_ids=("private-personal-v1",)).total,
            0,
        )
        self.assertEqual(
            session.search("", source_ids=("private-literature-v1",)).total,
            2,
        )

        session.remove_private("private-literature-v1")
        self.assertEqual(session.search("", page_size=10).total, 2)
        self.assertEqual(
            session.status()["private_source"]["source_id"],
            "private-personal-v2",
        )

    def test_failed_private_upsert_preserves_all_existing_sources(self):
        session = FederatedSearchSession(private=private_registration("personal-v1"))
        session.upsert_private(private_registration("literature-v1"))
        before = session.status()
        broken = SearchSourceRegistration.private(
            BrokenSource(),
            source_id="private-literature-v2",
            fingerprint="sha256:broken",
        )
        with self.assertRaises(FederatedSearchSessionError):
            session.upsert_private(broken)
        self.assertEqual(session.status(), before)
        self.assertEqual(session.search("", page_size=10).total, 4)

    def test_collection_first_then_personal_refresh_preserves_collection(self):
        session = FederatedSearchSession()
        session.upsert_private(private_registration("literature-v1"))
        session.refresh_private(private_registration("personal-v1"))
        self.assertEqual(session.search("", page_size=10).total, 4)
        self.assertEqual(
            session.status()["private_source"]["source_id"],
            "private-personal-v1",
        )
        session.refresh_private(private_registration("personal-v2"))
        self.assertEqual(session.search("", page_size=10).total, 4)
        self.assertEqual(
            session.search("", source_ids=("private-literature-v1",)).total,
            2,
        )
        self.assertEqual(
            session.search("", source_ids=("private-personal-v1",)).total,
            0,
        )

    def test_failed_clear_rebuild_preserves_both_previous_sources(self):
        private_source = FlakySource((
            document("private", "private-v1", "table", "private-table-v1", "样品硬度表"),
        ))
        session = FederatedSearchSession(
            official=official_registration(),
            private=private_registration(source=private_source),
        )
        before = session.status()
        private_source.fail = True
        with self.assertRaises(FederatedSearchSessionError):
            session.clear_official()
        self.assertEqual(session.status(), before)
        self.assertEqual(session.search("", page_size=10).total, 3)

    def test_source_registration_scope_and_document_identity_must_match(self):
        wrong = MemorySource((
            document("private", "private-v1", "item", "wrong", "错误来源"),
        ))
        registration = SearchSourceRegistration.official(
            wrong, source_id="official-v1", fingerprint="sha256:wrong"
        )
        with self.assertRaises(FederatedSearchSessionError) as caught:
            FederatedSearchSession(official=registration)
        self.assertEqual(caught.exception.code, "federated_source_activation_failed")
        with self.assertRaises(ValueError):
            SearchSourceRegistration.official(
                MemorySource(()), source_id="/Users/private/source", fingerprint="sha256:x"
            )

    def test_engine_factory_is_reused_without_copying_recall_or_sorting(self):
        calls: list[tuple[object, ...]] = []

        def factory(sources):
            snapshot = tuple(sources)
            calls.append(snapshot)
            return FederatedEvidenceSearch(snapshot)

        session = FederatedSearchSession(engine_factory=factory)
        session.install_official(official_registration())
        session.install_private(private_registration())
        self.assertEqual(len(calls), 2)
        self.assertIsInstance(session.search("硬度"), type(FederatedEvidenceSearch(()).search("")))

    def test_concurrent_read_uses_old_engine_until_atomic_swap(self):
        session = FederatedSearchSession(official=official_registration())
        started = threading.Event()
        release = threading.Event()
        replacement_source = BlockingSource(
            (
                document("official", "official-v2", "item", "official-item-v2", "钽 强度"),
            ),
            started,
            release,
        )
        replacement = official_registration("v2", source=replacement_source)
        errors: list[BaseException] = []

        def replace() -> None:
            try:
                session.install_official(replacement)
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        worker = threading.Thread(target=replace)
        worker.start()
        self.assertTrue(started.wait(timeout=1))
        self.assertEqual(session.search("钨").total, 1)
        self.assertEqual(session.status()["official_source"]["source_id"], "official-v1")
        release.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(session.search("钨").total, 0)
        self.assertEqual(session.search("钽").total, 1)
        self.assertEqual(session.status()["official_source"]["source_id"], "official-v2")


if __name__ == "__main__":
    unittest.main()
