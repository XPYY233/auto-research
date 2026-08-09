from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from auto_research.personal.experiment_contract import (
    ColumnMapping,
    MeasurementSeriesDraft,
    PersonalExperimentDraft,
    PersonalSourceFile,
    TabularImportPreview,
)
from auto_research.personal.private_repository import (
    PrivateExperimentRepository,
    PrivateProject,
    PrivateSample,
)
from auto_research.product.package_payload_sources import (
    EvidenceV12LiteraturePayloadSource,
    ExplicitLiteratureFilterResolver,
    LiteratureFilterResolution,
    LiteratureLicenseVerification,
    PrivateRepositoryPersonalPayloadSource,
)
from auto_research.product.package_transfer_payloads import (
    LiteratureCollectionPayloadPlanner,
    PayloadSelection,
    PersonalExperimentsPayloadPlanner,
)
from auto_research.product.transfer_package import TransferPackageError


def _build_v12_snapshot(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE papers (
              id INTEGER PRIMARY KEY, doi TEXT, title TEXT, year INTEGER,
              first_author TEXT, corresponding_author TEXT, material_focus TEXT
            );
            CREATE TABLE data_items (
              id INTEGER PRIMARY KEY, paper_id INTEGER, stable_key TEXT, origin_type TEXT
            );
            CREATE TABLE data_versions (
              id INTEGER PRIMARY KEY, item_id INTEGER, version_no INTEGER,
              value_text TEXT, meaning TEXT, unit TEXT, article_title TEXT, doi TEXT,
              context_explanation TEXT, source_page INTEGER, source_locator TEXT,
              source_excerpt TEXT, review_action TEXT
            );
            CREATE VIEW v_current_six_column_data AS
              SELECT i.id item_id,i.paper_id,i.stable_key,i.origin_type,v.version_no,
                     v.value_text,v.meaning,v.unit,v.article_title,v.doi,v.context_explanation,
                     v.source_page,v.source_locator,v.source_excerpt,v.review_action,
                     v.value_text original_value_text,v.meaning original_meaning,
                     v.unit original_unit,v.context_explanation original_context_explanation,
                     v.source_page original_source_page,v.source_locator original_source_locator,
                     v.source_excerpt original_source_excerpt
              FROM data_items i JOIN data_versions v ON v.item_id=i.id
              WHERE v.version_no=(SELECT MAX(v2.version_no) FROM data_versions v2 WHERE v2.item_id=i.id);
            CREATE TABLE visual_assets (
              id INTEGER PRIMARY KEY, paper_id INTEGER, asset_type TEXT, label TEXT,
              asset_number INTEGER, display_name TEXT, caption TEXT, page_start INTEGER,
              page_end INTEGER, physical_quantities_json TEXT, variables_json TEXT,
              materials_json TEXT, conditions_text TEXT, methods_text TEXT,
              context_explanation TEXT, tags_json TEXT, source_context TEXT,
              review_status TEXT
            );
            CREATE TABLE visual_asset_reviews (
              id INTEGER PRIMARY KEY, asset_id INTEGER, version_no INTEGER,
              review_action TEXT, fields_json TEXT
            );
            CREATE TABLE quality_candidates (
              id INTEGER PRIMARY KEY, published_item_id INTEGER, published_asset_id INTEGER,
              gate_status TEXT, overall_score REAL, candidate_json TEXT
            );
            CREATE TABLE data_item_visual_links (
              item_id INTEGER, asset_id INTEGER, relation_kind TEXT
            );
            """
        )
        connection.executemany(
            "INSERT INTO papers VALUES (?,?,?,?,?,?,?)",
            (
                (1, "10.1000/source.1", "Tungsten irradiation", 2024, "A. Li", "B. Wu", "W"),
                (2, "10.1000/source.2", "SiC irradiation", 2025, "C. Xu", "D. Sun", "SiC"),
            ),
        )
        connection.execute("INSERT INTO data_items VALUES (1,1,'dose','automatic')")
        connection.execute(
            """INSERT INTO data_versions VALUES
               (1,1,0,'5','辐照剂量','dpa','Tungsten irradiation','10.1000/source.1',
                '300 K','2','Table 1','dose was 5 dpa','automatic')"""
        )
        connection.execute(
            "INSERT INTO quality_candidates VALUES (1,1,NULL,'dual_pass',98.0,'{}')"
        )
        connection.commit()
    finally:
        connection.close()


class _PdfResolver:
    def __init__(self, paths: dict[str, Path | None]) -> None:
        self.paths = paths
        self.calls: list[str] = []

    def resolve(self, paper_uid: str, paper: dict) -> Path | None:
        self.calls.append(paper_uid)
        return self.paths.get(paper_uid)


class _LicenseVerifier:
    def __init__(self, verified: bool = True) -> None:
        self.verified = verified

    def verify(self, paper_uid: str, paper: dict):
        return LiteratureLicenseVerification("cc-by-4.0", self.verified)


class _FilterResolver:
    def __init__(self, identifiers: tuple[int, ...], *, current: bool = True) -> None:
        self.resolution = LiteratureFilterResolution(identifiers, "a" * 64)
        self.current = current
        self.tokens: list[str] = []

    def resolve(self, filter_token: str) -> LiteratureFilterResolution:
        self.tokens.append(filter_token)
        return self.resolution

    def is_current(
        self, filter_token: str, resolution: LiteratureFilterResolution
    ) -> bool:
        return self.current and resolution == self.resolution


class _PrivateFileResolver:
    def __init__(self, repository: PrivateExperimentRepository) -> None:
        self.repository = repository
        self.seen: list[dict] = []

    def resolve(self, source_id: str, source_file: dict) -> Path:
        self.seen.append(dict(source_file))
        return self.repository.private_path_for_file(str(source_file["file_id"]))


class PackagePayloadSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="payload-source-test-")
        self.root = Path(self.temporary.name)
        self.snapshot = self.root / "stable-v12.sqlite"
        _build_v12_snapshot(self.snapshot)
        self.pdf = self.root / "paper.pdf"
        self.pdf.write_bytes(b"%PDF-1.7\n%%EOF\n")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _literature_source(
        self, *, filter_resolver: _FilterResolver | None = None, verified: bool = True
    ) -> EvidenceV12LiteraturePayloadSource:
        probe = EvidenceV12LiteraturePayloadSource(
            self.snapshot,
            pdf_resolver=_PdfResolver({}),
            license_verifier=_LicenseVerifier(),
        )
        uids = probe.resolve_selection_ids(("1", "2"))
        return EvidenceV12LiteraturePayloadSource(
            self.snapshot,
            pdf_resolver=_PdfResolver({uids[0]: self.pdf, uids[1]: None}),
            license_verifier=_LicenseVerifier(verified),
            filter_resolver=filter_resolver,
        )

    def test_selected_numeric_ids_are_resolved_to_stable_paper_uids(self) -> None:
        source = self._literature_source()
        plan = LiteratureCollectionPayloadPlanner(source).plan(
            PayloadSelection("selected", selected_ids=("1",))
        )
        selected_uid = source.resolve_selection_ids(("1",))[0]
        self.assertEqual(plan.selection.selected_ids, (selected_uid,))
        self.assertEqual(plan.record_count, 1)
        self.assertEqual(plan.entity_count, 1)
        self.assertEqual(plan.optional_file_count, 1)
        self.assertEqual(plan.missing_pdf_count, 0)
        self.assertTrue(plan._payload.pdfs[0].license_verified)
        self.assertEqual(plan._payload.pdfs[0].license_id, "cc-by-4.0")
        self.assertNotIn(str(self.root), json.dumps(plan.public_dict(), ensure_ascii=False))

    def test_all_includes_metadata_only_papers_and_only_published_entities(self) -> None:
        plan = LiteratureCollectionPayloadPlanner(self._literature_source()).plan(
            PayloadSelection("all")
        )
        self.assertEqual(plan.record_count, 2)
        self.assertEqual(plan.entity_count, 1)
        self.assertEqual(plan.missing_pdf_count, 1)
        self.assertEqual({row["entity_type"] for row in plan._payload.entities}, {"item"})

    def test_filtered_requires_injected_frozen_resolver_and_rechecks_it(self) -> None:
        selection = PayloadSelection("filtered", filter_token='{"query":"SiC"}')
        with self.assertRaises(TransferPackageError) as missing:
            LiteratureCollectionPayloadPlanner(self._literature_source()).plan(selection)
        self.assertEqual(missing.exception.code, "transfer_filter_resolver_required")

        resolver = _FilterResolver((2,))
        plan = LiteratureCollectionPayloadPlanner(
            self._literature_source(filter_resolver=resolver)
        ).plan(selection)
        self.assertEqual(plan.record_count, 1)
        self.assertEqual(plan.entity_count, 0)
        self.assertEqual(resolver.tokens, ['{"query":"SiC"}'])

        changed = _FilterResolver((2,), current=False)
        with self.assertRaises(TransferPackageError) as stale:
            LiteratureCollectionPayloadPlanner(
                self._literature_source(filter_resolver=changed)
            ).plan(selection)
        self.assertEqual(stale.exception.code, "transfer_filter_changed")

    def test_explicit_filter_resolver_freezes_visible_paper_ids(self) -> None:
        resolver = ExplicitLiteratureFilterResolver()
        token = json.dumps(
            {"query": "irradiation", "paper_ids": [2, 1]},
            ensure_ascii=False,
        )
        resolution = resolver.resolve(token)
        self.assertEqual(resolution.local_paper_ids, (2, 1))
        self.assertTrue(resolver.is_current(token, resolution))
        self.assertFalse(
            resolver.is_current(
                json.dumps({"query": "irradiation", "paper_ids": [1]}),
                resolution,
            )
        )
        with self.assertRaises(TransferPackageError):
            resolver.resolve(json.dumps({"paper_ids": [], "path": "/tmp/db"}))

    def test_unverified_license_never_becomes_automatic_open_access(self) -> None:
        plan = LiteratureCollectionPayloadPlanner(
            self._literature_source(verified=False)
        ).plan(PayloadSelection("selected", selected_ids=("1",)))
        candidate = plan._payload.pdfs[0]
        self.assertFalse(candidate.license_verified)
        self.assertIsNone(candidate.license_id)
        self.assertEqual(plan.rights_required_count, 1)

    def _confirmed_repository(self) -> tuple[PrivateExperimentRepository, Path]:
        repository = PrivateExperimentRepository(self.root / "private-library")
        repository.add_project(PrivateProject("project-1", "W-Ta 项目"))
        repository.add_sample(PrivateSample("sample-1", "project-1", "W-Ta-01", "W-Ta"))
        content = b"dose,hardness\n0,3.2\n1,4.0\n"
        source_path = self.root / "measurements.csv"
        source_path.write_bytes(content)
        source_file = PersonalSourceFile(
            "file-primary",
            "measurements.csv",
            "text/csv",
            hashlib.sha256(content).hexdigest(),
            len(content),
        )
        repository.register_source_file(source_file, source_path)
        preview = TabularImportPreview(
            source_file=source_file,
            sheet_name="Sheet1",
            row_count=2,
            columns=(
                ColumnMapping(
                    "dose", "independent", "number", True, "辐照剂量", True, "dpa", True
                ),
                ColumnMapping(
                    "hardness", "dependent", "number", True, "纳米硬度", True, "GPa", True
                ),
            ),
        )
        draft = PersonalExperimentDraft(
            "run-local-1",
            "W-Ta 项目",
            "室温纳米压痕",
            "W-Ta-01",
            "纳米压痕",
            preview,
            series=(MeasurementSeriesDraft("series-local-1", "硬度-剂量", "dose", "hardness"),),
            conditions={"temperature": "300 K"},
            user_note="已核验",
            confirmation_state="draft",
        )
        saved = repository.save_experiment(
            draft, project_id="project-1", sample_id="sample-1"
        )
        repository.save_experiment(
            replace(draft, confirmation_state="confirmed"),
            project_id="project-1",
            sample_id="sample-1",
            expected_revision=saved.revision,
        )
        return repository, source_path

    def test_personal_source_emits_v2_structure_and_internal_table_binding(self) -> None:
        repository, _ = self._confirmed_repository()
        resolver = _PrivateFileResolver(repository)
        source = PrivateRepositoryPersonalPayloadSource(
            repository, file_resolver=resolver
        )
        payload = source.read_selection(PayloadSelection("all"))
        self.assertEqual(len(payload.records), 1)
        record = payload.records[0]
        self.assertEqual(record["sheet_name"], "Sheet1")
        self.assertEqual(record["row_count"], 2)
        self.assertEqual([row["source_name"] for row in record["columns"]], ["dose", "hardness"])
        self.assertEqual(record["series"][0]["x_column"], "dose")
        self.assertEqual(record["series"][0]["y_column"], "hardness")
        self.assertEqual(record["measurements"][0]["unit"], "GPa")
        self.assertEqual(record["notes"][0]["text"], "已核验")
        self.assertEqual(payload.tables[0].run_uid, record["run_uid"])
        encoded = json.dumps(record, ensure_ascii=False)
        self.assertNotIn("run-local-1", encoded)
        self.assertNotIn("file-primary", encoded)
        self.assertNotIn(str(self.root), encoded)

        plan = PersonalExperimentsPayloadPlanner(source).plan(PayloadSelection("all"))
        self.assertEqual(plan.record_count, 1)
        self.assertEqual(plan.entity_count, 1)
        self.assertEqual(plan.optional_file_count, 1)
        self.assertEqual(plan._payload.tables[0].run_uid, plan._payload.records[0]["run_uid"])
        self.assertNotIn(str(self.root), json.dumps(plan.public_dict(), ensure_ascii=False))

    def test_personal_source_rejects_changed_primary_table(self) -> None:
        repository, _ = self._confirmed_repository()
        resolver = _PrivateFileResolver(repository)
        source = PrivateRepositoryPersonalPayloadSource(
            repository, file_resolver=resolver
        )
        stored = repository.private_path_for_file("file-primary")
        stored.write_bytes(b"changed")
        with self.assertRaises(TransferPackageError) as raised:
            source.read_selection(PayloadSelection("all"))
        self.assertEqual(raised.exception.code, "transfer_source_changed")


if __name__ == "__main__":
    unittest.main()
