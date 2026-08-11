from __future__ import annotations

import time
import unittest
from typing import Any, Iterable, Mapping

from auto_research.evidence.federated_search import (
    FederatedEvidenceSearch,
    StructuredEvidenceSource,
)
from auto_research.personal.search_source import EvidenceSearchDocument


class MappingSource:
    def __init__(self, documents: Iterable[Mapping[str, Any]]) -> None:
        self.documents = tuple(documents)

    def iter_search_documents(self):
        yield from self.documents


class DtoSource:
    def __init__(self, documents: Iterable[EvidenceSearchDocument]) -> None:
        self.documents = tuple(documents)

    def iter_search_documents(self):
        yield from self.documents


def official_document(
    entity_type: str,
    uid: str,
    *,
    title: str,
    meaning: str,
    context: str = "",
    source_id: str = "official-preview",
) -> dict[str, Any]:
    return {
        "schema_version": "official-evidence-document-v1",
        "entity_type": entity_type,
        "source_scope": "official",
        "source_id": source_id,
        "entity_uid": uid,
        "article_title": title,
        "display_title": meaning,
        "meaning_text": meaning,
        "context_text": context,
        "evidence_text": "原文证据",
        "metadata_text": "W-Ta 辐照",
        "doi": "10.1000/example",
    }


def private_document(uid: str, *, title: str, meaning: str) -> EvidenceSearchDocument:
    return EvidenceSearchDocument(
        entity_type="item",
        source_scope="private",
        source_id="personal-repository",
        entity_uid=uid,
        display_title=title,
        meaning_text=meaning,
        context_text="项目：离子辐照；样品：W-Ta-03；方法：纳米压痕",
        project_name="离子辐照",
        sample_name="W-Ta-03",
        material="W-Ta",
        method="纳米压痕",
        conditions=(("temperature", "室温"),),
        unit="GPa",
        tags=("私人实验", "硬度"),
    )


class FederatedEvidenceSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        official = [
            official_document(
                "item",
                "entity-item-1",
                title="Ion irradiation of W-Ta",
                meaning="辐照后力学性能",
                context="室温 He 离子辐照后硬度升高",
            ),
            official_document(
                "finding",
                "entity-finding-1",
                title="Defect evolution in tungsten",
                meaning="空位团簇随剂量增加",
                context="透射电镜观察",
            ),
            official_document(
                "table",
                "entity-table-1",
                title="Mechanical response",
                meaning="Table 2 · 辐照条件",
                context="温度与剂量汇总",
            ),
            official_document(
                "figure",
                "entity-figure-1",
                title="Mechanical response",
                meaning="Figure 3 · 硬度趋势",
                context="横轴剂量，纵轴硬度",
            ),
        ]
        private = [
            private_document(
                "private:item:stable-1",
                title="W-Ta-03 硬度-剂量",
                meaning="纳米硬度",
            )
        ]
        self.service = FederatedEvidenceSearch(
            (MappingSource(official), DtoSource(private))
        )

    def test_sources_are_platform_neutral_and_private_dto_is_reused(self) -> None:
        self.assertIsInstance(MappingSource(()), StructuredEvidenceSource)
        self.assertIsInstance(DtoSource(()), StructuredEvidenceSource)
        page = self.service.search("W-Ta-03")
        self.assertEqual(page.total, 1)
        document = page.hits[0].document
        self.assertEqual(document["schema_version"], "evidence-search-document-v1")
        self.assertEqual(document["entity_type"], "item")
        self.assertEqual(document["source_scope"], "private")

    def test_empty_query_browses_with_stable_pagination(self) -> None:
        first = self.service.search("", page=1, page_size=2)
        second = self.service.search("", page=2, page_size=2)
        self.assertEqual(first.total, 5)
        self.assertTrue(first.has_next)
        self.assertEqual(len(first.hits), 2)
        self.assertEqual(len(second.hits), 2)
        identities = [
            hit.document["entity_uid"] for hit in (*first.hits, *second.hits)
        ]
        self.assertEqual(len(identities), len(set(identities)))
        repeated = self.service.search("", page=1, page_size=2)
        self.assertEqual(
            [hit.document["entity_uid"] for hit in first.hits],
            [hit.document["entity_uid"] for hit in repeated.hits],
        )

    def test_weighted_keywords_rank_title_and_meaning_above_context(self) -> None:
        page = self.service.search("硬度", page_size=10)
        self.assertEqual(page.total, 3)
        self.assertEqual(page.hits[0].document["entity_uid"], "private:item:stable-1")
        self.assertEqual(page.hits[1].document["entity_uid"], "entity-figure-1")
        self.assertGreater(page.hits[1].score, page.hits[2].score)
        self.assertGreater(page.hits[0].score, 0)
        self.assertEqual(page.hits[0].matched_terms, ("硬度",))

    def test_multi_term_coverage_excludes_one_word_noise_and_keeps_three_of_four(self) -> None:
        only_common = official_document(
            "item",
            "only-common-term",
            title="Generic irradiation evidence",
            meaning="辐照实验",
        )
        only_common["metadata_text"] = "generic evidence"
        documents = (
            only_common,
            official_document(
                "item",
                "three-of-four",
                title="W Ta irradiation evidence",
                meaning="W Ta 辐照实验",
            ),
            official_document(
                "item",
                "all-four",
                title="W Ta He irradiation evidence",
                meaning="W Ta 合金 He 辐照",
            ),
        )
        service = FederatedEvidenceSearch((MappingSource(documents),))
        page = service.search("W Ta He 辐照")
        self.assertEqual(
            [hit.document["entity_uid"] for hit in page.hits],
            ["all-four", "three-of-four"],
        )
        self.assertEqual(len(page.hits[0].matched_terms), 4)
        self.assertEqual(len(page.hits[1].matched_terms), 3)

    def test_one_or_two_term_queries_require_each_term_and_keep_short_chinese_terms(self) -> None:
        documents = (
            official_document(
                "item",
                "tungsten-hardness",
                title="钨的纳米硬度",
                meaning="硬度随温度变化",
            ),
            official_document(
                "item",
                "hardness-only",
                title="硬度测量",
                meaning="辐照后力学性能",
            ),
        )
        service = FederatedEvidenceSearch((MappingSource(documents),))
        for query in ("钨", "硬度", "温度"):
            with self.subTest(query=query):
                self.assertGreater(service.search(query).total, 0)
        combined = service.search("钨 硬度")
        self.assertEqual(combined.total, 1)
        self.assertEqual(combined.hits[0].document["entity_uid"], "tungsten-hardness")

    def test_empty_query_browse_is_not_subject_to_coverage_gate(self) -> None:
        page = self.service.search("")
        self.assertEqual(page.total, self.service.document_count)
        self.assertTrue(all(hit.matched_terms == () for hit in page.hits))

    def test_four_type_and_source_filters_can_be_combined(self) -> None:
        private_only = self.service.search(
            "",
            entity_types=("item",),
            source_scopes=("private",),
        )
        self.assertEqual(private_only.total, 1)
        official_visuals = self.service.search(
            "",
            entity_types=("table", "figure"),
            source_scopes=("official",),
            source_ids=("official-preview",),
        )
        self.assertEqual(
            {hit.document["entity_type"] for hit in official_visuals.hits},
            {"table", "figure"},
        )
        with self.assertRaises(ValueError):
            self.service.search("", entity_types=("experiment",))

    def test_get_uses_complete_stable_identity_and_returns_a_copy(self) -> None:
        document = self.service.get(
            source_scope="official",
            source_id="official-preview",
            entity_uid="entity-item-1",
        )
        self.assertEqual(document["entity_type"], "item")
        document["display_title"] = "changed by caller"
        again = self.service.get(
            source_scope="official",
            source_id="official-preview",
            entity_uid="entity-item-1",
        )
        self.assertEqual(again["display_title"], "辐照后力学性能")
        with self.assertRaises(KeyError):
            self.service.get(
                source_scope="private",
                source_id="official-preview",
                entity_uid="entity-item-1",
            )

    def test_output_keeps_public_dto_nested_under_ranking_metadata(self) -> None:
        payload = self.service.search("硬度", page_size=1).as_dict()
        self.assertEqual(payload["schema_version"], "federated-search-page-v1")
        result = payload["results"][0]
        self.assertEqual(set(result), {"score", "matched_terms", "document"})
        self.assertIn(result["document"]["entity_type"], {"item", "table", "figure", "finding"})
        self.assertIn("source_scope", result["document"])
        self.assertIn("source_id", result["document"])
        self.assertIn("entity_uid", result["document"])

    def test_duplicate_identity_and_private_fields_fail_closed(self) -> None:
        duplicate = official_document(
            "item",
            "same",
            title="A",
            meaning="A",
        )
        with self.assertRaises(ValueError):
            FederatedEvidenceSearch((MappingSource((duplicate, duplicate)),))
        unsafe = dict(duplicate, entity_uid="unsafe", pdf_path="/Users/test/paper.pdf")
        with self.assertRaises(ValueError):
            FederatedEvidenceSearch((MappingSource((unsafe,)),))
        fifth = dict(duplicate, entity_uid="fifth", entity_type="experiment")
        with self.assertRaises(ValueError):
            FederatedEvidenceSearch((MappingSource((fifth,)),))

    def test_public_projection_rejects_local_paths_and_storage_uris(self) -> None:
        unsafe_values = (
            "/private/var/data.db",
            "/tmp/import.csv",
            "/var/folders/cache",
            "/etc/passwd",
            "/usr/local/bin/tool",
            "/root/private.db",
            "/srv/evidence",
            "/mnt/share",
            "/media/usb/data.xlsx",
            "/Applications/Auto Research.app",
            "/Library/Application Support/Auto Research",
            "/System/Library/CoreServices",
            "~/Documents/private.csv",
            "file:///Users/test/private.csv",
            r"file:C:\Users\test\private.csv",
            "sqlite:///tmp/private.db",
            "sqlite:relative.db",
            r"\\server\share\private.db",
            "//server/share/private.db",
            r"C:\Users\test\private.db",
            "saved at /Users/name/a.pdf",
            "db=sqlite:/tmp/a.db",
        )
        for index, unsafe_value in enumerate(unsafe_values):
            with self.subTest(unsafe_value=unsafe_value):
                document = official_document(
                    "item",
                    f"unsafe-path-{index}",
                    title="Unsafe path",
                    meaning="公开含义",
                )
                document["source_excerpt"] = unsafe_value
                with self.assertRaisesRegex(ValueError, "local path"):
                    FederatedEvidenceSearch((MappingSource((document,)),))

        public_url = official_document(
            "item",
            "public-url-is-not-a-local-path",
            title="Public source URL",
            meaning="公开含义",
        )
        public_url["source_excerpt"] = "available at https://example.org/evidence"
        service = FederatedEvidenceSearch((MappingSource((public_url,)),))
        self.assertEqual(service.document_count, 1)

    def test_public_projection_rejects_internal_ids_but_keeps_stable_identity(self) -> None:
        forbidden_id_keys = (
            "file_id",
            "source_file_id",
            "project_id",
            "sample_id",
            "draft_id",
            "import_id",
            "operation_id",
        )
        for forbidden_key in forbidden_id_keys:
            with self.subTest(forbidden_key=forbidden_key):
                document = official_document(
                    "item",
                    f"unsafe-{forbidden_key}",
                    title="Internal identity",
                    meaning="公开含义",
                )
                document[forbidden_key] = "private-internal-value"
                with self.assertRaisesRegex(ValueError, "private field"):
                    FederatedEvidenceSearch((MappingSource((document,)),))

        safe = official_document(
            "item",
            "entity_uid-remains-public",
            title="Stable public identity",
            meaning="公开含义",
            source_id="source_id-remains-public",
        )
        service = FederatedEvidenceSearch((MappingSource((safe,)),))
        result = service.search("").hits[0].document
        self.assertEqual(result["source_id"], "source_id-remains-public")
        self.assertEqual(result["entity_uid"], "entity_uid-remains-public")

    def test_scientific_capsule_identifier_does_not_weaken_private_id_guard(self) -> None:
        table = official_document(
            "table",
            "table-with-capsule-identifier",
            title="Irradiation capsule conditions",
            meaning="Table 1 · Capsule conditions",
        )
        table["variables"] = {
            "capsule_id": "未指定",
            "design_temperature": "400°C",
        }
        service = FederatedEvidenceSearch((MappingSource((table,)),))
        self.assertEqual(service.document_count, 1)
        self.assertEqual(
            service.search("400°C").hits[0].document["variables"]["capsule_id"],
            "未指定",
        )

        for forbidden_key in ("run_id", "file_id", "pdf_path"):
            with self.subTest(forbidden_key=forbidden_key):
                unsafe = dict(table, entity_uid=f"unsafe-{forbidden_key}")
                unsafe["variables"] = {forbidden_key: "private-internal-value"}
                with self.assertRaisesRegex(ValueError, "private field"):
                    FederatedEvidenceSearch((MappingSource((unsafe,)),))

    def test_page_and_query_bounds_are_enforced(self) -> None:
        with self.assertRaises(ValueError):
            self.service.search("x", page=0)
        with self.assertRaises(ValueError):
            self.service.search("x", page_size=101)
        with self.assertRaises(ValueError):
            self.service.search("x" * 501)

    def test_4356_document_query_has_bounded_in_memory_latency(self) -> None:
        documents = [
            official_document(
                ("item", "finding", "table", "figure")[index % 4],
                f"entity-{index:04d}",
                title=f"Tungsten irradiation study {index}",
                meaning=(
                    "纳米硬度与剂量关系"
                    if index % 17 == 0
                    else "辐照实验公开证据"
                ),
                context="W-Ta alloy room temperature ion irradiation",
            )
            for index in range(4_356)
        ]
        service = FederatedEvidenceSearch((MappingSource(documents),))
        started = time.perf_counter()
        page = service.search("纳米硬度 剂量", page_size=25)
        elapsed = time.perf_counter() - started
        self.assertEqual(service.document_count, 4_356)
        self.assertEqual(page.total, 257)
        self.assertEqual(len(page.hits), 25)
        self.assertLess(elapsed, 1.5)


if __name__ == "__main__":
    unittest.main()
