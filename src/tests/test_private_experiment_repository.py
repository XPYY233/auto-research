from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from auto_research.personal.experiment_contract import (
    ColumnMapping,
    MeasurementSeriesDraft,
    PersonalArtifactDraft,
    PersonalExperimentDraft,
    PersonalSourceFile,
    TabularImportPreview,
)
from auto_research.personal.private_repository import (
    DATABASE_NAME,
    SCHEMA_VERSION,
    PrivateExperimentRepository,
    PrivateOperationResult,
    PrivateProject,
    PrivateRepositoryError,
    PrivateSample,
)


class PrivateExperimentRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "private-data"
        self.repo = PrivateExperimentRepository(self.root)
        self.repo.add_project(PrivateProject("project-1", "W-Ta 辐照实验", "个人实验项目"))
        self.repo.add_sample(PrivateSample("sample-1", "project-1", "W-Ta-03", "W-Ta"))

    def tearDown(self):
        self.tmp.cleanup()

    def make_file(self, name: str, content: bytes, media_type: str) -> tuple[Path, PersonalSourceFile]:
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

    def draft(self, *, state: str = "draft", confirmed: bool = True) -> tuple[PersonalExperimentDraft, dict[str, Path]]:
        table_path, table = self.make_file(
            "measurements.csv",
            b"dose,hardness\n0,3.2\n1,4.0\n",
            "text/csv",
        )
        plot_path, plot = self.make_file("trend.png", b"fake-png-bytes", "image/png")
        preview = TabularImportPreview(
            source_file=table,
            sheet_name="Sheet1",
            row_count=2,
            columns=(
                ColumnMapping(
                    "dose",
                    "condition",
                    "number",
                    role_confirmed=confirmed,
                    meaning="辐照剂量",
                    meaning_confirmed=confirmed,
                    unit="dpa",
                    unit_confirmed=confirmed,
                ),
                ColumnMapping(
                    "hardness",
                    "dependent",
                    "number",
                    role_confirmed=confirmed,
                    meaning="纳米硬度",
                    meaning_confirmed=confirmed,
                    unit="GPa",
                    unit_confirmed=confirmed,
                ),
            ),
        )
        draft = PersonalExperimentDraft(
            draft_id="run-1",
            project_name="W-Ta 辐照实验",
            run_name="室温纳米压痕",
            sample_name="W-Ta-03",
            method="纳米压痕",
            preview=preview,
            series=(MeasurementSeriesDraft("series-1", "硬度-剂量", "dose", "hardness"),),
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
            user_note="三次重复测量",
            confirmation_state=state,
        )
        return draft, {table.file_id: table_path, plot.file_id: plot_path}

    def register_draft_files(self, draft: PersonalExperimentDraft, paths: dict[str, Path]) -> None:
        for source in (draft.preview.source_file, *draft.supporting_files):
            self.repo.register_source_file(source, paths[source.file_id])

    def save_confirmed(self, draft: PersonalExperimentDraft) -> PrivateOperationResult:
        draft_version = replace(draft, confirmation_state="draft")
        saved = self.repo.save_experiment(
            draft_version,
            project_id="project-1",
            sample_id="sample-1",
        )
        return self.repo.save_experiment(
            replace(draft, confirmation_state="confirmed"),
            project_id="project-1",
            sample_id="sample-1",
            expected_revision=saved.revision,
        )

    def test_schema_v1_is_isolated_under_explicit_data_root(self):
        self.assertEqual(self.repo.database_path, self.root.resolve() / DATABASE_NAME)
        self.assertTrue(self.repo.database_path.is_file())
        with self.repo.connect() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            names = {row[1] for row in conn.execute("PRAGMA database_list")}
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        self.assertEqual(names, {"main"})
        self.assertTrue(
            {"projects", "samples", "experiment_runs", "measurement_series", "attachments", "notes"}
            <= tables
        )
        with self.assertRaises(ValueError):
            PrivateExperimentRepository("")

    def test_files_are_copied_inside_private_root_and_database_path_is_relative(self):
        draft, paths = self.draft()
        source = draft.preview.source_file
        self.repo.register_source_file(source, paths[source.file_id])
        stored = self.repo.private_path_for_file(source.file_id)
        self.assertTrue(stored.is_file())
        self.assertIn(self.root.resolve(), stored.parents)
        self.assertEqual(stored.read_bytes(), paths[source.file_id].read_bytes())
        with self.repo.connect() as conn:
            relative = str(
                conn.execute(
                    "SELECT relative_path FROM source_files WHERE file_id=?", (source.file_id,)
                ).fetchone()[0]
            )
        self.assertFalse(Path(relative).is_absolute())
        self.assertNotIn(str(Path(self.tmp.name)), relative)

    def test_draft_can_be_saved_then_confirmed_and_only_confirmed_is_searchable(self):
        draft, paths = self.draft(state="draft")
        self.register_draft_files(draft, paths)
        draft_result = self.repo.save_experiment(
            draft, project_id="project-1", sample_id="sample-1"
        )
        self.assertEqual(draft_result.import_state, "draft_saved")
        self.assertFalse(draft_result.indexable)
        self.assertEqual(self.repo.list_personal_search_documents(), [])

        confirmed = replace(draft, confirmation_state="confirmed")
        confirmed_result = self.repo.save_experiment(
            confirmed,
            project_id="project-1",
            sample_id="sample-1",
            expected_revision=draft_result.revision,
        )
        self.assertEqual(confirmed_result.import_state, "indexable")
        self.assertEqual(confirmed_result.confirmation_state, "confirmed")
        self.assertTrue(confirmed_result.indexable)
        documents = self.repo.list_personal_search_documents()
        self.assertEqual(len(documents), 1)
        document = documents[0]
        self.assertEqual(document["source_domain"], "personal")
        self.assertEqual(document["source_scope"], "private")
        self.assertEqual(document["source_id"], self.repo.repository_id)
        self.assertEqual(document["entity_uid"], "personal:experiment_run:run-1")
        self.assertEqual(document["measurement_meanings"], ["辐照剂量", "纳米硬度"])
        self.assertEqual(document["attachments"][0]["linked_series_ids"], ["series-1"])
        self.assertEqual(document["notes"], ["三次重复测量"])

    def test_public_projection_contains_no_private_paths(self):
        draft, paths = self.draft(state="confirmed")
        self.register_draft_files(draft, paths)
        self.save_confirmed(draft)
        encoded = json.dumps(self.repo.list_personal_search_documents(), ensure_ascii=False)
        self.assertNotIn(str(self.root), encoded)
        self.assertNotIn(str(Path(self.tmp.name)), encoded)
        self.assertNotIn("relative_path", encoded)
        self.assertNotIn("database_path", encoded)

    def test_confirmed_run_requires_role_meaning_and_unit_confirmation(self):
        draft, paths = self.draft(state="confirmed", confirmed=False)
        self.register_draft_files(draft, paths)
        issues = draft.confirmation_issues()
        self.assertIn("unconfirmed_column:dose", issues)
        with self.assertRaises(ValueError):
            self.repo.save_experiment(draft, project_id="project-1", sample_id="sample-1")

    def test_each_confirmation_flag_is_independently_required(self):
        draft, paths = self.draft(state="confirmed")
        self.register_draft_files(draft, paths)
        for field_name in ("role_confirmed", "meaning_confirmed", "unit_confirmed"):
            with self.subTest(field_name=field_name):
                broken = replace(draft.preview.columns[0], **{field_name: False})
                preview = replace(
                    draft.preview,
                    columns=(broken, draft.preview.columns[1]),
                )
                with self.assertRaises(ValueError):
                    self.repo.save_experiment(
                        replace(draft, preview=preview),
                        project_id="project-1",
                        sample_id="sample-1",
                    )

    def test_projection_gate_rechecks_database_flags(self):
        draft, paths = self.draft(state="confirmed")
        self.register_draft_files(draft, paths)
        self.save_confirmed(draft)
        with self.repo.connect(write=True) as conn:
            conn.execute(
                "UPDATE column_mappings SET unit_confirmed=0 WHERE run_id=? AND source_name=?",
                ("run-1", "hardness"),
            )
        self.assertEqual(self.repo.list_personal_search_documents(), [])

    def test_project_sample_series_attachment_and_notes_are_persisted(self):
        draft, paths = self.draft(state="confirmed")
        self.register_draft_files(draft, paths)
        self.save_confirmed(draft)
        self.repo.add_note("sample-note-1", "样品边缘区域", sample_id="sample-1")
        self.repo.add_note("project-note-1", "项目共用仪器 A", project_id="project-1")
        self.repo.add_note("run-note-extra", "第二天复核", run_id="run-1")
        with self.repo.connect() as conn:
            counts = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "projects",
                    "samples",
                    "experiment_runs",
                    "measurement_series",
                    "attachments",
                    "attachment_series",
                    "notes",
                )
            }
        self.assertEqual(counts["projects"], 1)
        self.assertEqual(counts["samples"], 1)
        self.assertEqual(counts["experiment_runs"], 1)
        self.assertEqual(counts["measurement_series"], 1)
        self.assertEqual(counts["attachments"], 1)
        self.assertEqual(counts["attachment_series"], 1)
        self.assertEqual(counts["notes"], 4)
        document_notes = self.repo.list_personal_search_documents()[0]["notes"]
        self.assertIn("样品边缘区域", document_notes)
        self.assertIn("项目共用仪器 A", document_notes)
        self.assertIn("第二天复核", document_notes)

    def test_hash_mismatch_fails_without_registering_or_copying_file(self):
        path, source = self.make_file("data.csv", b"original", "text/csv")
        path.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.repo.register_source_file(source, path)
        with self.repo.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0]
        self.assertEqual(count, 0)
        self.assertEqual(list(self.repo.files_root.rglob("*-data.csv")), [])

    def test_sample_cannot_be_used_with_another_project(self):
        self.repo.add_project(PrivateProject("project-2", "另一个项目"))
        draft, paths = self.draft()
        self.register_draft_files(draft, paths)
        with self.assertRaises(ValueError):
            self.repo.save_experiment(draft, project_id="project-2", sample_id="sample-1")

    def test_confirmed_run_is_immutable_and_repository_identity_survives_reopen(self):
        draft, paths = self.draft(state="confirmed")
        self.register_draft_files(draft, paths)
        self.save_confirmed(draft)
        before = self.repo.list_personal_search_documents()
        with self.assertRaises(PrivateRepositoryError) as caught:
            self.repo.save_experiment(draft, project_id="project-1", sample_id="sample-1")
        self.assertEqual(caught.exception.code, "RUN_CONFIRMED_IMMUTABLE")
        self.assertEqual(self.repo.list_personal_search_documents(), before)
        original_id = self.repo.repository_id
        reopened = PrivateExperimentRepository(self.root)
        self.assertEqual(reopened.repository_id, original_id)

    def test_operation_results_follow_preview_draft_confirmed_indexable_contract(self):
        draft, paths = self.draft(state="draft")
        source = draft.preview.source_file
        previewed = self.repo.register_source_file(source, paths[source.file_id])
        self.assertEqual(
            previewed.as_dict(),
            {
                "schema_version": "private-operation-result-v1",
                "ok": True,
                "operation": "register_source_file",
                "entity_type": "source_file",
                "entity_id": source.file_id,
                "import_state": "previewed",
                "confirmation_state": None,
                "indexable": False,
                "changed": True,
                "revision": None,
            },
        )
        repeated = self.repo.register_source_file(source, paths[source.file_id])
        self.assertEqual(repeated.import_state, "previewed")
        self.assertFalse(repeated.changed)
        supporting = draft.supporting_files[0]
        self.repo.register_source_file(supporting, paths[supporting.file_id])

        saved = self.repo.save_experiment(
            draft, project_id="project-1", sample_id="sample-1"
        )
        self.assertEqual(saved.import_state, "draft_saved")
        confirmed = self.repo.save_experiment(
            replace(draft, confirmation_state="confirmed"),
            project_id="project-1",
            sample_id="sample-1",
            expected_revision=saved.revision,
        )
        self.assertEqual(confirmed.import_state, "indexable")
        self.assertEqual(confirmed.confirmation_state, "confirmed")
        self.assertTrue(confirmed.indexable)

    def test_direct_confirmation_is_rejected_with_path_free_stable_error(self):
        draft, paths = self.draft(state="confirmed")
        self.register_draft_files(draft, paths)
        with self.assertRaises(PrivateRepositoryError) as caught:
            self.repo.save_experiment(
                draft, project_id="project-1", sample_id="sample-1"
            )
        error = caught.exception
        self.assertEqual(error.code, "INVALID_IMPORT_TRANSITION")
        self.assertEqual(error.details["current_state"], "previewed")
        self.assertEqual(error.details["required_state"], "draft_saved")
        encoded = json.dumps(error.as_dict(), ensure_ascii=False)
        self.assertNotIn(str(self.root), encoded)
        self.assertNotIn(str(Path(self.tmp.name)), encoded)
        self.assertNotIn("sqlite", encoded.lower())

    def test_duplicate_error_hides_sqlite_and_private_paths(self):
        with self.assertRaises(PrivateRepositoryError) as caught:
            self.repo.add_project(PrivateProject("project-1", "重复项目"))
        error = caught.exception
        self.assertEqual(error.code, "DUPLICATE_ID")
        encoded = json.dumps(error.as_dict(), ensure_ascii=False)
        self.assertNotIn("UNIQUE constraint failed", encoded)
        self.assertNotIn(str(self.root), encoded)
        self.assertEqual(set(error.details), {"entity_type"})

    def test_error_details_reject_path_fields(self):
        with self.assertRaises(ValueError):
            PrivateRepositoryError(
                "UNSAFE_TEST",
                "安全消息",
                details={"path": str(self.root)},
            )

    def test_note_result_and_missing_target_use_stable_contract(self):
        result = self.repo.add_note("project-note", "项目备注", project_id="project-1")
        self.assertEqual(result.entity_type, "note")
        self.assertTrue(result.changed)
        with self.assertRaises(PrivateRepositoryError) as caught:
            self.repo.add_note("missing-note", "不存在", run_id="missing-run")
        self.assertEqual(caught.exception.code, "RUN_NOT_FOUND")

    def test_schema_initialization_rolls_back_partial_ddl(self):
        root = Path(self.tmp.name) / "atomic-schema"
        root.mkdir()
        database = root / DATABASE_NAME
        database.write_bytes(b"")
        os.chmod(database, 0o600)

        def fail_after_first_statement(conn: sqlite3.Connection, _script: str) -> None:
            conn.execute("CREATE TABLE partial_schema(value TEXT)")
            raise sqlite3.OperationalError("injected schema failure")

        with patch.object(
            PrivateExperimentRepository,
            "_execute_schema_statements",
            side_effect=fail_after_first_statement,
        ):
            with self.assertRaises(PrivateRepositoryError) as caught:
                PrivateExperimentRepository(root)
        self.assertEqual(caught.exception.code, "PRIVATE_DB_UNAVAILABLE")
        with sqlite3.connect(database) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(tables, set())
        self.assertEqual(version, 0)

    def test_files_root_symlink_is_rejected(self):
        root = Path(self.tmp.name) / "files-link-root"
        outside = Path(self.tmp.name) / "outside-files"
        root.mkdir()
        outside.mkdir()
        try:
            (root / "files").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symbolic links are unavailable on this platform")
        with self.assertRaises(PrivateRepositoryError) as caught:
            PrivateExperimentRepository(root)
        self.assertEqual(caught.exception.code, "PRIVATE_FILES_UNSAFE")
        self.assertFalse((root / DATABASE_NAME).exists())

    def test_destination_symlink_is_rejected_without_touching_target(self):
        path, source = self.make_file("data.csv", b"source", "text/csv")
        destination = (
            self.repo.files_root
            / source.sha256[:2]
            / source.sha256
            / f"{source.file_id}-{source.original_name}"
        )
        destination.parent.mkdir(parents=True)
        outside = Path(self.tmp.name) / "outside-target"
        outside.write_bytes(b"keep")
        try:
            destination.symlink_to(outside)
        except OSError:
            self.skipTest("symbolic links are unavailable on this platform")
        with self.assertRaises(PrivateRepositoryError) as caught:
            self.repo.register_source_file(source, path)
        self.assertEqual(caught.exception.code, "PRIVATE_PATH_UNSAFE")
        self.assertEqual(outside.read_bytes(), b"keep")
        self.assertTrue(destination.is_symlink())

    def test_permission_failure_fails_closed_before_database_creation(self):
        root = Path(self.tmp.name) / "permission-failure"
        with patch(
            "auto_research.personal.private_repository.os.chmod",
            side_effect=PermissionError("injected"),
        ):
            with self.assertRaises(PrivateRepositoryError) as caught:
                PrivateExperimentRepository(root)
        self.assertEqual(caught.exception.code, "PRIVATE_PERMISSIONS_UNSAFE")
        self.assertFalse((root / DATABASE_NAME).exists())

    def test_destination_permission_failure_removes_only_owned_file(self):
        path, source = self.make_file("data.csv", b"source", "text/csv")
        real_chmod = os.chmod

        def fail_final_file(target, mode):
            if Path(target).name.endswith("-data.csv"):
                raise PermissionError("injected")
            return real_chmod(target, mode)

        with patch(
            "auto_research.personal.private_repository.os.chmod",
            side_effect=fail_final_file,
        ):
            with self.assertRaises(PrivateRepositoryError) as caught:
                self.repo.register_source_file(source, path)
        self.assertEqual(caught.exception.code, "PRIVATE_PERMISSIONS_UNSAFE")
        with self.repo.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0]
        self.assertEqual(count, 0)
        self.assertEqual(
            [item for item in self.repo.files_root.rglob("*") if item.is_file()],
            [],
        )

    def test_stale_draft_cannot_overwrite_confirmed_revision(self):
        draft, paths = self.draft()
        self.register_draft_files(draft, paths)
        saved = self.repo.save_experiment(
            draft,
            project_id="project-1",
            sample_id="sample-1",
        )
        confirmed = self.repo.save_experiment(
            replace(draft, confirmation_state="confirmed"),
            project_id="project-1",
            sample_id="sample-1",
            expected_revision=saved.revision,
        )
        self.assertEqual(confirmed.revision, 2)

        stale_repository = PrivateExperimentRepository(self.root)
        with self.assertRaises(PrivateRepositoryError) as caught:
            stale_repository.save_experiment(
                replace(draft, run_name="陈旧草稿"),
                project_id="project-1",
                sample_id="sample-1",
                expected_revision=saved.revision,
            )
        self.assertEqual(caught.exception.code, "RUN_CONFIRMED_IMMUTABLE")
        document = self.repo.list_personal_search_documents()[0]
        self.assertEqual(document["display_title"], "室温纳米压痕")

    def test_concurrent_draft_updates_use_revision_compare_and_swap(self):
        draft, paths = self.draft()
        self.register_draft_files(draft, paths)
        saved = self.repo.save_experiment(
            draft,
            project_id="project-1",
            sample_id="sample-1",
        )
        barrier = threading.Barrier(2)

        def update(repository: PrivateExperimentRepository, name: str):
            barrier.wait(timeout=5)
            try:
                result = repository.save_experiment(
                    replace(draft, run_name=name),
                    project_id="project-1",
                    sample_id="sample-1",
                    expected_revision=saved.revision,
                )
                return ("ok", result.revision)
            except PrivateRepositoryError as exc:
                return (exc.code, exc.details.get("actual_revision"))

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(update, PrivateExperimentRepository(self.root), "更新 A"),
                executor.submit(update, PrivateExperimentRepository(self.root), "更新 B"),
            ]
            outcomes = [future.result(timeout=10) for future in futures]
        self.assertEqual(sorted(item[0] for item in outcomes), ["RUN_REVISION_CONFLICT", "ok"])
        self.assertEqual({item[1] for item in outcomes}, {2})
        with self.repo.connect() as conn:
            row = conn.execute(
                "SELECT name,revision,confirmation_state FROM experiment_runs WHERE run_id=?",
                (draft.draft_id,),
            ).fetchone()
        self.assertIn(str(row["name"]), {"更新 A", "更新 B"})
        self.assertEqual(int(row["revision"]), 2)
        self.assertEqual(str(row["confirmation_state"]), "draft")

    def test_concurrent_conflicting_file_registration_preserves_winner(self):
        first_path, first = self.make_file("first.csv", b"first", "text/csv")
        second_path, second_identity = self.make_file("second.csv", b"second", "text/csv")
        second = replace(second_identity, file_id=first.file_id)
        barrier = threading.Barrier(2)

        class RacingRepository(PrivateExperimentRepository):
            def _stage_source_file(self, selected, temporary, source):
                PrivateExperimentRepository._stage_source_file(selected, temporary, source)
                barrier.wait(timeout=5)

        def register(repository, source, path):
            try:
                result = repository.register_source_file(source, path)
                return ("ok", result.changed)
            except PrivateRepositoryError as exc:
                return (exc.code, False)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(register, RacingRepository(self.root), first, first_path),
                executor.submit(register, RacingRepository(self.root), second, second_path),
            ]
            outcomes = [future.result(timeout=10) for future in futures]
        self.assertEqual(sorted(item[0] for item in outcomes), ["DUPLICATE_ID", "ok"])
        stored = self.repo.private_path_for_file(first.file_id)
        self.assertTrue(stored.is_file())
        self.assertIn(stored.read_bytes(), {b"first", b"second"})
        files = [item for item in self.repo.files_root.rglob("*") if item.is_file()]
        self.assertEqual(files, [stored])
        self.assertEqual(list(self.repo.files_root.rglob("*.tmp")), [])

    def test_post_publish_database_failure_cleans_owned_destination(self):
        path, source = self.make_file("fault.csv", b"source", "text/csv")

        class FailingRepository(PrivateExperimentRepository):
            @staticmethod
            def _insert_source_file_row(conn, source, relative):
                raise sqlite3.OperationalError("injected insert failure")

        repository = FailingRepository(self.root)
        with self.assertRaises(PrivateRepositoryError) as caught:
            repository.register_source_file(source, path)
        self.assertEqual(caught.exception.code, "SOURCE_FILE_UNAVAILABLE")
        with self.repo.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0]
        self.assertEqual(count, 0)
        self.assertEqual(
            [item for item in self.repo.files_root.rglob("*") if item.is_file()],
            [],
        )

    def test_projection_rejects_series_that_references_ignored_column(self):
        draft, paths = self.draft(state="confirmed")
        self.register_draft_files(draft, paths)
        self.save_confirmed(draft)
        with self.repo.connect(write=True) as conn:
            conn.execute(
                "UPDATE column_mappings SET role='ignore' WHERE run_id=? AND source_name=?",
                (draft.draft_id, "dose"),
            )
        self.assertEqual(self.repo.list_personal_search_documents(), [])

    def test_draft_rejects_series_that_references_ignored_column(self):
        draft, paths = self.draft()
        ignored = replace(
            draft.preview.columns[0],
            role="ignore",
            role_confirmed=True,
            meaning_confirmed=True,
            unit_confirmed=True,
        )
        invalid = replace(
            draft,
            preview=replace(
                draft.preview,
                columns=(ignored, draft.preview.columns[1]),
            ),
        )
        self.register_draft_files(invalid, paths)
        with self.assertRaises(PrivateRepositoryError) as caught:
            self.repo.save_experiment(
                invalid,
                project_id="project-1",
                sample_id="sample-1",
            )
        self.assertEqual(caught.exception.code, "INVALID_SERIES_COLUMN")
        self.assertEqual(
            caught.exception.details["issue_codes"],
            ["ignored_x_column"],
        )
        with self.repo.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM experiment_runs").fetchone()[0]
        self.assertEqual(count, 0)

    def test_unknown_existing_database_is_not_adopted(self):
        other_root = Path(self.tmp.name) / "unknown"
        other_root.mkdir()
        with sqlite3.connect(other_root / DATABASE_NAME) as conn:
            conn.execute("CREATE TABLE unrelated(value TEXT)")
        with self.assertRaises(ValueError):
            PrivateExperimentRepository(other_root)

    def test_database_symlink_is_rejected_before_sqlite_open(self):
        link_root = Path(self.tmp.name) / "linked"
        link_root.mkdir()
        target = Path(self.tmp.name) / "outside.sqlite"
        target.write_bytes(b"not a private repository")
        try:
            (link_root / DATABASE_NAME).symlink_to(target)
        except OSError:
            self.skipTest("symbolic links are unavailable on this platform")
        with self.assertRaises(ValueError):
            PrivateExperimentRepository(link_root)

    def test_future_schema_is_rejected(self):
        future_root = Path(self.tmp.name) / "future"
        future_root.mkdir()
        with sqlite3.connect(future_root / DATABASE_NAME) as conn:
            conn.execute("PRAGMA user_version=99")
        with self.assertRaises(ValueError):
            PrivateExperimentRepository(future_root)


if __name__ == "__main__":
    unittest.main()
