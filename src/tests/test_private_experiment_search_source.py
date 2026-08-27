from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

from auto_research.evidence.federated_search import validate_public_evidence_document
from auto_research.personal.experiment_contract import (
    ColumnMapping,
    MeasurementSeriesDraft,
    PersonalArtifactDraft,
    PersonalExperimentDraft,
    PersonalSourceFile,
    TabularImportPreview,
)
from auto_research.personal.private_repository import (
    PrivateExperimentRepository,
    PrivateProject,
    PrivateSample,
)
from auto_research.personal.search_source import (
    EvidenceSearchDocument,
    EvidenceSearchSource,
    PrivateRepositorySearchSource,
    PrivateSearchSnapshot,
    PrivateTablePublicIdentity,
)
from auto_research.personal.table_detail import PersonalTableDetailService


class PrivateExperimentSearchSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "private-data"
        self.repo = PrivateExperimentRepository(self.root)
        self.repo.add_project(PrivateProject("project-1", "W-Ta 辐照实验"))
        self.repo.add_sample(PrivateSample("sample-1", "project-1", "W-Ta-03", "W-Ta"))
        self.revisions: dict[str, int] = {}

    def tearDown(self):
        self.tmp.cleanup()

    def _file(self, name: str, content: bytes, media_type: str) -> tuple[Path, PersonalSourceFile]:
        path = Path(self.tmp.name) / name
        path.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        return path, PersonalSourceFile(
            file_id=f"file-{digest[:12]}",
            original_name=name,
            media_type=media_type,
            sha256=digest,
            size_bytes=len(content),
        )

    def _draft(self, run_id: str = "run-1") -> tuple[PersonalExperimentDraft, dict[str, Path]]:
        table_path, table = self._file(
            "measurements.csv",
            b"dose,hardness\n0,3.2\n1,4.0\n",
            "text/csv",
        )
        plot_path, plot = self._file("trend.png", b"plot-bytes", "image/png")
        preview = TabularImportPreview(
            source_file=table,
            sheet_name="Sheet1",
            row_count=2,
            columns=(
                ColumnMapping(
                    "dose",
                    "independent",
                    "number",
                    role_confirmed=True,
                    meaning="辐照剂量",
                    meaning_confirmed=True,
                    unit="dpa",
                    unit_confirmed=True,
                ),
                ColumnMapping(
                    "hardness",
                    "dependent",
                    "number",
                    role_confirmed=True,
                    meaning="纳米硬度",
                    meaning_confirmed=True,
                    unit="GPa",
                    unit_confirmed=True,
                ),
            ),
        )
        draft = PersonalExperimentDraft(
            draft_id=run_id,
            project_name="W-Ta 辐照实验",
            run_name="室温纳米压痕",
            sample_name="W-Ta-03",
            method="纳米压痕",
            preview=preview,
            series=(
                MeasurementSeriesDraft(
                    "series-1",
                    "硬度-剂量趋势",
                    "dose",
                    "hardness",
                    description="随剂量增加硬度升高",
                ),
            ),
            supporting_files=(plot,),
            artifacts=(
                PersonalArtifactDraft(
                    "attachment-1",
                    "plot",
                    "硬度趋势图",
                    plot.file_id,
                    linked_series_ids=("series-1",),
                    user_description="横轴剂量，纵轴硬度",
                ),
            ),
            conditions={"temperature": "室温"},
            user_note="三次重复测量结果一致",
            confirmation_state="draft",
        )
        return draft, {table.file_id: table_path, plot.file_id: plot_path}

    def _register_and_save(self, draft: PersonalExperimentDraft, paths: dict[str, Path]) -> None:
        for source in (draft.preview.source_file, *draft.supporting_files):
            self.repo.register_source_file(source, paths[source.file_id])
        result = self.repo.save_experiment(
            draft, project_id="project-1", sample_id="sample-1"
        )
        assert result.revision is not None
        self.revisions[draft.draft_id] = result.revision

    def _confirm(self, draft: PersonalExperimentDraft) -> None:
        self.repo.save_experiment(
            replace(draft, confirmation_state="confirmed"),
            project_id="project-1",
            sample_id="sample-1",
            expected_revision=self.revisions[draft.draft_id],
        )

    def test_confirmed_run_maps_to_the_existing_four_entity_types(self):
        draft, paths = self._draft()
        self._register_and_save(draft, paths)
        self._confirm(draft)
        source = PrivateRepositorySearchSource(self.repo)

        documents = source.list_documents()
        self.assertIsInstance(source, EvidenceSearchSource)
        self.assertEqual(tuple(source.iter_search_documents()), documents)
        self.assertEqual(
            {
                document.entity_type
                for document in source.iter_search_documents(entity_types=("item",))
            },
            {"item"},
        )
        self.assertEqual(
            [document.entity_type for document in documents],
            ["item", "table", "figure", "finding"],
        )
        for document in documents:
            self.assertEqual(document.source_scope, "private")
            self.assertEqual(document.source_id, self.repo.repository_id)
            self.assertTrue(document.entity_uid.startswith(f"private:{document.entity_type}:"))
            self.assertEqual(document.project_name, "W-Ta 辐照实验")
            self.assertEqual(document.sample_name, "W-Ta-03")
            self.assertEqual(dict(document.conditions), {"temperature": "室温"})
            self.assertEqual(
                set(document.as_dict()),
                {
                    "schema_version",
                    "entity_type",
                    "source_scope",
                    "source_id",
                    "entity_uid",
                    "display_title",
                    "meaning_text",
                    "context_text",
                    "project_name",
                    "sample_name",
                    "material",
                    "method",
                    "conditions",
                    "value_text",
                    "unit",
                    "source_label",
                    "source_excerpt",
                    "tags",
                    "search_text",
                },
            )

        item, table, figure, finding = documents
        self.assertEqual(item.meaning_text, "纳米硬度")
        self.assertEqual(item.unit, "GPa")
        self.assertIn("辐照剂量", item.context_text)
        self.assertIn("2 行，2 列", table.context_text)
        self.assertEqual(table.source_label, "Sheet1")
        self.assertEqual(figure.source_label, "趋势图")
        self.assertEqual(finding.source_excerpt, "三次重复测量结果一致")

    def test_confirmed_table_identity_matches_search_and_table_detail(self):
        draft, paths = self._draft()
        self._register_and_save(draft, paths)
        self._confirm(draft)
        source = PrivateRepositorySearchSource(self.repo)

        identity = source.confirmed_table_identity(
            run_id=draft.draft_id,
            sheet_name=draft.preview.sheet_name,
        )
        table = next(
            document
            for document in source.list_documents()
            if document.entity_type == "table"
        )
        self.assertIsInstance(identity, PrivateTablePublicIdentity)
        self.assertEqual(identity.source_id, table.source_id)
        self.assertEqual(identity.entity_uid, table.entity_uid)
        page = PersonalTableDetailService(self.repo).get_page(
            source_id=identity.source_id,
            entity_uid=identity.entity_uid,
        )
        self.assertEqual(page.sheet_name, "Sheet1")
        encoded = json.dumps(identity.__dict__, ensure_ascii=False)
        for forbidden in (
            draft.draft_id,
            draft.preview.source_file.file_id,
            draft.preview.source_file.sha256,
            str(self.root),
        ):
            self.assertNotIn(forbidden, encoded)

    def test_table_identity_rejects_draft_or_wrong_sheet(self):
        draft, paths = self._draft()
        self._register_and_save(draft, paths)
        source = PrivateRepositorySearchSource(self.repo)
        for sheet_name in ("Sheet1", "forged-sheet"):
            with self.assertRaises(ValueError):
                source.confirmed_table_identity(
                    run_id=draft.draft_id,
                    sheet_name=sheet_name,
                )

    def test_adapter_excludes_drafts_and_database_non_indexable_confirmed_rows(self):
        draft, paths = self._draft()
        self._register_and_save(draft, paths)
        source = PrivateRepositorySearchSource(self.repo)
        self.assertEqual(source.list_documents(), ())

        self._confirm(draft)
        self.assertEqual(len(source.list_documents()), 4)
        with self.repo.connect(write=True) as conn:
            conn.execute(
                "UPDATE column_mappings SET meaning_confirmed=0 WHERE run_id=?",
                (draft.draft_id,),
            )
        self.assertEqual(source.list_documents(), ())

    def test_projection_is_stable_path_free_and_contains_no_repository_ids(self):
        draft, paths = self._draft()
        self._register_and_save(draft, paths)
        self._confirm(draft)
        source = PrivateRepositorySearchSource(self.repo)
        before_hash = hashlib.sha256(self.repo.database_path.read_bytes()).hexdigest()
        first = [document.as_dict() for document in source.list_documents()]
        after_hash = hashlib.sha256(self.repo.database_path.read_bytes()).hexdigest()

        reopened = PrivateRepositorySearchSource(PrivateExperimentRepository(self.root))
        second = [document.as_dict() for document in reopened.list_documents()]
        self.assertEqual(first, second)
        self.assertEqual(before_hash, after_hash)

        encoded = json.dumps(first, ensure_ascii=False)
        for private_value in (
            str(self.root),
            str(Path(self.tmp.name)),
            "relative_path",
            "database_path",
            "run-1",
            "series-1",
            "attachment-1",
            draft.preview.source_file.file_id,
            draft.supporting_files[0].file_id,
            draft.preview.source_file.sha256,
        ):
            self.assertNotIn(private_value, encoded)

    def test_snapshot_is_immutable_stable_and_changes_only_for_public_confirmed_content(self):
        first_draft, first_paths = self._draft()
        self._register_and_save(first_draft, first_paths)
        self._confirm(first_draft)
        source = PrivateRepositorySearchSource(self.repo)

        first = source.snapshot()
        reopened = PrivateRepositorySearchSource(
            PrivateExperimentRepository(self.root)
        ).snapshot()
        self.assertIsInstance(first, PrivateSearchSnapshot)
        self.assertIsInstance(first, EvidenceSearchSource)
        self.assertEqual(first.source_scope, "private")
        self.assertEqual(first.source_id, self.repo.repository_id)
        self.assertEqual(first.document_count, 4)
        self.assertEqual(len(first.content_fingerprint), 64)
        self.assertEqual(first.content_fingerprint, reopened.content_fingerprint)
        self.assertEqual(first.documents, reopened.documents)
        self.assertEqual(tuple(first.iter_search_documents()), first.documents)
        with self.assertRaises(FrozenInstanceError):
            setattr(first, "documents", ())

        second_draft, second_paths = self._draft("run-2")
        second_series = replace(
            second_draft.series[0],
            series_id="series-2",
            name="第二批硬度-剂量趋势",
        )
        second_artifact = replace(
            second_draft.artifacts[0],
            artifact_id="attachment-2",
            linked_series_ids=("series-2",),
        )
        second_draft = replace(
            second_draft,
            run_name="第二批室温纳米压痕",
            series=(second_series,),
            artifacts=(second_artifact,),
        )
        self._register_and_save(second_draft, second_paths)
        after_draft = source.snapshot()
        self.assertEqual(after_draft.content_fingerprint, first.content_fingerprint)
        self.assertEqual(after_draft.document_count, first.document_count)

        self._confirm(second_draft)
        after_confirm = source.snapshot()
        self.assertNotEqual(after_confirm.content_fingerprint, first.content_fingerprint)
        self.assertEqual(after_confirm.document_count, 8)
        self.assertEqual(first.document_count, 4)

    def test_empty_snapshot_fingerprint_binds_schema_scope_and_source_identity(self):
        first = PrivateSearchSnapshot(source_id="private-source-a", documents=())
        second = PrivateSearchSnapshot(source_id="private-source-b", documents=())
        envelope = {
            "schema_version": "private-search-snapshot-v1",
            "source_scope": "private",
            "source_id": "private-source-a",
            "documents": [],
        }
        expected = hashlib.sha256(
            json.dumps(
                envelope,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        self.assertEqual(first.content_fingerprint, expected)
        self.assertNotEqual(first.content_fingerprint, second.content_fingerprint)

    def test_snapshot_rejects_duplicate_identity_and_local_path_canary(self):
        draft, paths = self._draft()
        self._register_and_save(draft, paths)
        self._confirm(draft)
        source = PrivateRepositorySearchSource(self.repo)
        document = source.snapshot().documents[0]

        with self.assertRaises(ValueError) as duplicate:
            PrivateSearchSnapshot(
                source_id=source.source_id,
                documents=(document, document),
            )
        self.assertEqual(
            str(duplicate.exception),
            "private search snapshot contains duplicate evidence identity",
        )

        canary = "/Users/private/library.sqlite api-token=sk-sensitive-canary"
        unsafe = replace(document, context_text=canary)
        with self.assertRaises(ValueError) as rejected:
            PrivateSearchSnapshot(source_id=source.source_id, documents=(unsafe,))
        self.assertNotIn(canary, str(rejected.exception))

    def test_shared_public_dto_validator_rejects_internal_fields_without_echo(self):
        draft, paths = self._draft()
        self._register_and_save(draft, paths)
        self._confirm(draft)
        document = PrivateRepositorySearchSource(self.repo).snapshot().documents[0]
        unsafe = document.as_dict()
        unsafe["run_id"] = "private-run-canary"

        with self.assertRaises(ValueError) as rejected:
            validate_public_evidence_document(unsafe)
        self.assertNotIn("private-run-canary", str(rejected.exception))

    def test_confirmed_notes_become_distinct_findings_without_note_ids(self):
        draft, paths = self._draft()
        self._register_and_save(draft, paths)
        self._confirm(draft)
        self.repo.add_note(
            "private-note-id",
            "样品边缘区域未纳入统计",
            sample_id="sample-1",
        )

        documents = PrivateRepositorySearchSource(self.repo).list_documents()
        findings = [document for document in documents if document.entity_type == "finding"]
        self.assertEqual(len(findings), 2)
        self.assertEqual(len({document.entity_uid for document in findings}), 2)
        encoded = json.dumps([document.as_dict() for document in findings], ensure_ascii=False)
        self.assertNotIn("private-note-id", encoded)
        self.assertIn("样品边缘区域未纳入统计", encoded)

    def test_document_dto_rejects_unknown_type_and_scope(self):
        common = {
            "source_id": "source-1",
            "entity_uid": "private:item:opaque",
            "display_title": "测试",
            "meaning_text": "含义",
            "context_text": "上下文",
            "project_name": "项目",
            "sample_name": "样品",
            "material": None,
            "method": "方法",
        }
        with self.assertRaises(ValueError):
            EvidenceSearchDocument(entity_type="run", source_scope="private", **common)
        with self.assertRaises(ValueError):
            EvidenceSearchDocument(entity_type="item", source_scope="local", **common)


if __name__ == "__main__":
    unittest.main()
