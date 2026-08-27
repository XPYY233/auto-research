from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from auto_research.personal.experiment_contract import (
    ColumnMapping,
    PersonalSourceFile,
    TabularImportPreview,
)
from auto_research.personal.import_service import (
    PersonalImportService,
    PersonalImportServiceError,
    SelectionSnapshotProviderError,
)
from auto_research.personal.private_repository import (
    PrivateOperationResult,
    PrivateRepositoryError,
)
from auto_research.personal.tabular_preview import TabularFilePreview


SELECTION_ID = "personal_selection_0123456789abcdef"
IMPORT_ID = "personal_import_0123456789abcdef"


def _preview_value() -> TabularFilePreview:
    raw = b"x" * 42
    source = PersonalSourceFile(
        file_id="personal-file-0123456789abcdef",
        original_name="experiment.csv",
        media_type="text/csv",
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
    )
    sheet = TabularImportPreview(
        source_file=source,
        sheet_name="Sheet1",
        row_count=2,
        columns=(
            ColumnMapping(
                source_name="Dose (dpa)",
                role="condition",
                data_type="number",
                unit="dpa",
                sample_values=("0", "1"),
            ),
            ColumnMapping(
                source_name="Hardness [GPa]",
                role="dependent",
                data_type="number",
                unit="GPa",
                sample_values=("3.2", "4.0"),
            ),
        ),
    )
    return TabularFilePreview(
        source_file=source,
        detected_format="csv",
        sheets=(sheet,),
    )


class _MemorySelectionProvider:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.revoked: list[str] = []
        self.error: SelectionSnapshotProviderError | None = None
        self.error_on_exit: SelectionSnapshotProviderError | None = None

    @contextmanager
    def snapshot(self, selection_id: str):
        if self.error is not None:
            raise self.error
        if selection_id != SELECTION_ID:
            raise SelectionSnapshotProviderError(
                "personal_selection_invalid",
                "文件选择无效，请重新选择。",
                retryable=True,
            )
        yield SimpleNamespace(path=self.path)
        if self.error_on_exit is not None:
            raise self.error_on_exit

    def revoke(self, selection_id: str) -> None:
        self.revoked.append(selection_id)


class _FileSelectionProvider:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.revoked: list[str] = []

    @contextmanager
    def snapshot(self, selection_id: str):
        if selection_id != SELECTION_ID:
            raise SelectionSnapshotProviderError(
                "personal_selection_invalid",
                "文件选择无效，请重新选择。",
                retryable=True,
            )
        yield SimpleNamespace(path=self.path)

    def revoke(self, selection_id: str) -> None:
        self.revoked.append(selection_id)


class _MemoryRepository:
    repository_id = "private-memory-tests"

    def __init__(self) -> None:
        self.revision = 0
        self.fail_save = False
        self.operations: list[str] = []

    def add_project(self, _project) -> PrivateOperationResult:
        self.operations.append("project")
        return PrivateOperationResult("add_project", "project", "project")

    def add_sample(self, _sample) -> PrivateOperationResult:
        self.operations.append("sample")
        return PrivateOperationResult("add_sample", "sample", "sample")

    def register_source_file(self, source, _selected) -> PrivateOperationResult:
        self.operations.append("source")
        return PrivateOperationResult(
            "register_source_file",
            "source_file",
            source.file_id,
            import_state="previewed",
        )

    def save_experiment(
        self,
        draft,
        *,
        project_id: str,
        sample_id: str,
        expected_revision: int | None,
    ) -> PrivateOperationResult:
        del project_id, sample_id
        self.operations.append("save")
        if self.fail_save:
            raise PrivateRepositoryError(
                "PRIVATE_DB_WRITE_FAILED",
                "暂时无法保存私人实验数据。",
                details={"operation": "save_experiment"},
            )
        if expected_revision is not None and expected_revision != self.revision:
            raise PrivateRepositoryError(
                "RUN_REVISION_CONFLICT",
                "实验草稿已被更新。",
                details={
                    "expected_revision": expected_revision,
                    "actual_revision": self.revision,
                },
            )
        self.revision += 1
        if draft.confirmation_state == "confirmed":
            return PrivateOperationResult(
                "save_experiment",
                "experiment_run",
                draft.draft_id,
                import_state="indexable",
                confirmation_state="confirmed",
                indexable=True,
                revision=self.revision,
            )
        return PrivateOperationResult(
            "save_experiment",
            "experiment_run",
            draft.draft_id,
            import_state="draft_saved",
            confirmation_state="draft",
            revision=self.revision,
        )

    def list_personal_search_documents(self) -> list[dict[str, Any]]:
        return []


class PersonalImportServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "experiment.csv"
        self.source.write_bytes(b"x" * 42)
        self.provider = _MemorySelectionProvider(self.source)
        self.repository = _MemoryRepository()
        self.previewer_calls: list[Path] = []

        def previewer(path: Path, *, limits) -> TabularFilePreview:
            del limits
            self.previewer_calls.append(path)
            return _preview_value()

        self.service = PersonalImportService(
            data_root=self.root / "private-library",
            selection_provider=self.provider,
            repository=self.repository,  # type: ignore[arg-type]
            previewer=previewer,
            import_id_factory=lambda: IMPORT_ID,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _payload(*, confirmed: bool, expected_revision: int | None = None):
        value: dict[str, Any] = {
            "sheet_index": 0,
            "project": {"name": "W-Ta 辐照实验"},
            "sample": {"name": "W-Ta-01", "material": "W-Ta"},
            "run": {
                "name": "纳米压痕批次 1",
                "method": "纳米压痕",
                "conditions": {"temperature": "室温"},
            },
            "columns": [
                {
                    "source_name": "Dose (dpa)",
                    "role": "independent",
                    "role_confirmed": confirmed,
                    "meaning": "辐照剂量",
                    "meaning_confirmed": confirmed,
                    "unit": "dpa",
                    "unit_confirmed": confirmed,
                },
                {
                    "source_name": "Hardness [GPa]",
                    "role": "dependent",
                    "role_confirmed": confirmed,
                    "meaning": "硬度",
                    "meaning_confirmed": confirmed,
                    "unit": "GPa",
                    "unit_confirmed": confirmed,
                },
            ],
            "series": [
                {
                    "series_id": "hardness-dose",
                    "name": "硬度随剂量变化",
                    "x_column": "Dose (dpa)",
                    "y_column": "Hardness [GPa]",
                }
            ],
        }
        if expected_revision is not None:
            value["expected_revision"] = expected_revision
        return value

    def test_preview_uses_narrow_provider_and_public_dto_is_path_free(self) -> None:
        result = self.service.preview(SELECTION_ID)
        serialized = json.dumps(result.public_dict(), ensure_ascii=False)
        self.assertEqual(result.status.stage.value, "previewed")
        self.assertEqual(
            self.previewer_calls,
            [self.source],
        )
        self.assertNotIn(str(self.root), serialized)
        self.assertEqual(self.repository.operations, [])

    def test_provider_error_is_translated_without_platform_dependency(self) -> None:
        self.provider.error = SelectionSnapshotProviderError(
            "personal_selection_changed",
            "文件在选择后发生变化，请重新选择。",
            retryable=True,
        )
        with self.assertRaises(PersonalImportServiceError) as raised:
            self.service.preview(SELECTION_ID)
        self.assertEqual(raised.exception.code, "personal_selection_changed")
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(self.repository.operations, [])

    def test_provider_cleanup_failure_removes_completed_private_staging_copy(self) -> None:
        self.provider.error_on_exit = SelectionSnapshotProviderError(
            "personal_selection_snapshot_failed",
            "无法清理选择副本。",
            retryable=True,
        )
        with self.assertRaises(PersonalImportServiceError) as raised:
            self.service.preview(SELECTION_ID)
        self.assertEqual(raised.exception.code, "personal_selection_snapshot_failed")
        staging = self.service.data_root / ".import-staging"
        self.assertEqual(list(staging.glob("*")), [])

    def test_preview_must_be_saved_before_confirm(self) -> None:
        self.service.preview(SELECTION_ID)
        with self.assertRaises(PersonalImportServiceError) as raised:
            self.service.confirm(IMPORT_ID, expected_revision=1)
        self.assertEqual(raised.exception.code, "INVALID_IMPORT_TRANSITION")
        self.assertEqual(self.service.status(IMPORT_ID).stage.value, "previewed")

    def test_explicit_flags_revision_cas_and_confirmed_immutability(self) -> None:
        self.service.preview(SELECTION_ID)
        draft = self.service.save_draft(IMPORT_ID, self._payload(confirmed=False))
        self.assertEqual(draft.stage.value, "draft_saved")
        self.assertEqual(draft.revision, 1)
        self.assertFalse(draft.indexable)
        self.assertEqual(self.provider.revoked, [SELECTION_ID])

        with self.assertRaises(PersonalImportServiceError) as incomplete:
            self.service.confirm(IMPORT_ID, expected_revision=1)
        self.assertEqual(incomplete.exception.code, "RUN_CONFIRMATION_INCOMPLETE")

        with self.assertRaises(PersonalImportServiceError) as stale:
            self.service.save_draft(
                IMPORT_ID,
                self._payload(confirmed=True, expected_revision=99),
            )
        self.assertEqual(stale.exception.code, "RUN_REVISION_CONFLICT")
        self.assertEqual(self.repository.revision, 1)

        updated = self.service.save_draft(
            IMPORT_ID,
            self._payload(confirmed=True, expected_revision=1),
        )
        self.assertEqual(updated.revision, 2)
        confirmed = self.service.confirm(IMPORT_ID, expected_revision=2)
        self.assertTrue(confirmed.indexable)
        self.assertEqual(confirmed.revision, 3)

        with self.assertRaises(PersonalImportServiceError) as immutable:
            self.service.save_draft(
                IMPORT_ID,
                self._payload(confirmed=True, expected_revision=3),
            )
        self.assertEqual(immutable.exception.code, "personal_import_already_confirmed")

    def test_service_startup_removes_only_expired_owned_staging_files(self) -> None:
        data_root = self.root / "orphan-cleanup-library"
        staging = data_root / ".import-staging"
        staging.mkdir(parents=True)
        orphan = staging / f"{IMPORT_ID}-abcdefgh.csv"
        unrelated = staging / "user-kept.csv"
        fresh = staging / f"{IMPORT_ID}-ijklmnop.csv"
        for path in (orphan, unrelated, fresh):
            path.write_text("value", encoding="utf-8")
        old = time.time() - 600
        os.utime(orphan, (old, old))
        os.utime(unrelated, (old, old))

        PersonalImportService(
            data_root=data_root,
            selection_provider=self.provider,
            repository=self.repository,  # type: ignore[arg-type]
            previewer=lambda path, *, limits: _preview_value(),
            import_id_factory=lambda: IMPORT_ID,
            session_ttl_seconds=300,
        )

        self.assertFalse(orphan.exists())
        self.assertTrue(unrelated.exists())
        self.assertTrue(fresh.exists())

    def test_concurrent_confirm_only_succeeds_once(self) -> None:
        self.service.preview(SELECTION_ID)
        draft = self.service.save_draft(IMPORT_ID, self._payload(confirmed=True))

        def confirm_once() -> str:
            try:
                self.service.confirm(IMPORT_ID, expected_revision=draft.revision or 0)
            except PersonalImportServiceError as exc:
                return exc.code
            return "success"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = sorted(executor.map(lambda _item: confirm_once(), range(2)))
        self.assertEqual(outcomes, ["personal_import_already_confirmed", "success"])

    def test_repository_failure_keeps_session_non_indexable(self) -> None:
        self.service.preview(SELECTION_ID)
        self.repository.fail_save = True
        with self.assertRaises(PersonalImportServiceError) as raised:
            self.service.save_draft(IMPORT_ID, self._payload(confirmed=True))
        self.assertEqual(raised.exception.code, "PRIVATE_DB_WRITE_FAILED")
        status = self.service.status(IMPORT_ID)
        self.assertEqual(status.stage.value, "previewed")
        self.assertFalse(status.indexable)

    def test_confirmation_flags_must_be_boolean(self) -> None:
        self.service.preview(SELECTION_ID)
        payload = copy.deepcopy(self._payload(confirmed=True))
        payload["columns"][0]["unit_confirmed"] = "yes"
        with self.assertRaises(PersonalImportServiceError) as raised:
            self.service.save_draft(IMPORT_ID, payload)
        self.assertEqual(raised.exception.code, "personal_confirmation_flag_required")
        self.assertEqual(self.repository.operations, [])

    def test_shared_service_integrates_bounded_parser_and_private_repository(self) -> None:
        source = self.root / "experiment.csv"
        source.write_text(
            "Dose (dpa),Hardness [GPa]\n0,3.2\n1,4.0\n",
            encoding="utf-8",
        )
        provider = _FileSelectionProvider(source)
        service = PersonalImportService(
            data_root=self.root / "real-private-library",
            selection_provider=provider,
            import_id_factory=lambda: IMPORT_ID,
        )
        preview = service.preview(SELECTION_ID)
        self.assertEqual(preview.preview.detected_format, "csv")
        draft = service.save_draft(IMPORT_ID, self._payload(confirmed=True))
        confirmed = service.confirm(IMPORT_ID, expected_revision=draft.revision or 0)
        self.assertTrue(confirmed.indexable)
        self.assertGreater(len(service.private_search_source().list_documents()), 0)
        snapshot = service.private_search_snapshot()
        self.assertGreater(snapshot.document_count, 0)
        self.assertEqual(snapshot.source_scope, "private")
        self.assertEqual(len(snapshot.content_fingerprint), 64)
        self.assertEqual(provider.revoked, [SELECTION_ID])
        self.assertNotIn(
            str(self.root),
            json.dumps(confirmed.public_dict(), ensure_ascii=False),
        )

    def test_preview_snapshot_survives_original_file_changes(self) -> None:
        source = self.root / "changing.csv"
        source.write_text(
            "Dose (dpa),Hardness [GPa]\n0,3.2\n1,4.0\n",
            encoding="utf-8",
        )
        provider = _FileSelectionProvider(source)
        service = PersonalImportService(
            data_root=self.root / "snapshot-private-library",
            selection_provider=provider,
            import_id_factory=lambda: IMPORT_ID,
        )
        service.preview(SELECTION_ID)
        source.write_text("this file changed after preview\n", encoding="utf-8")
        confirmed = service.import_reviewed(
            IMPORT_ID,
            self._payload(confirmed=False),
            reviewed=True,
        )
        self.assertTrue(confirmed.indexable)
        self.assertEqual(provider.revoked, [SELECTION_ID])
        self.assertEqual(
            list((service.data_root / ".import-staging").glob("*")),
            [],
        )

    def test_one_review_action_marks_visible_fields_and_is_idempotent(self) -> None:
        self.service.preview(SELECTION_ID)
        payload = self._payload(confirmed=False)
        payload["run"]["conditions"] = {}
        payload["series"] = []
        first = self.service.import_reviewed(
            IMPORT_ID,
            payload,
            reviewed=True,
        )
        second = self.service.import_reviewed(
            IMPORT_ID,
            payload,
            reviewed=True,
        )
        self.assertTrue(first.indexable)
        self.assertEqual(first, second)
        self.assertEqual(first.revision, 2)

    def test_review_action_preserves_revision_cas_after_partial_draft(self) -> None:
        self.service.preview(SELECTION_ID)
        self.service.save_draft(IMPORT_ID, self._payload(confirmed=True))
        stale = self._payload(confirmed=False, expected_revision=99)
        with self.assertRaises(PersonalImportServiceError) as raised:
            self.service.import_reviewed(
                IMPORT_ID,
                stale,
                reviewed=True,
            )
        self.assertEqual(raised.exception.code, "RUN_REVISION_CONFLICT")
        self.assertEqual(self.service.status(IMPORT_ID).stage.value, "draft_saved")

    def test_review_action_requires_literal_user_confirmation(self) -> None:
        self.service.preview(SELECTION_ID)
        with self.assertRaises(PersonalImportServiceError) as raised:
            self.service.import_reviewed(
                IMPORT_ID,
                self._payload(confirmed=False),
                reviewed=False,
            )
        self.assertEqual(raised.exception.code, "personal_review_required")
        self.assertEqual(self.service.status(IMPORT_ID).stage.value, "previewed")

    def test_expired_preview_removes_private_staging_snapshot(self) -> None:
        clock = [100.0]
        service = PersonalImportService(
            data_root=self.root / "expiring-private-library",
            selection_provider=self.provider,
            previewer=lambda _path, *, limits: _preview_value(),
            import_id_factory=lambda: IMPORT_ID,
            clock=lambda: clock[0],
            session_ttl_seconds=300,
        )
        service.preview(SELECTION_ID)
        staging = service.data_root / ".import-staging"
        self.assertEqual(len(list(staging.glob("*"))), 1)
        clock[0] += 301
        with self.assertRaises(PersonalImportServiceError) as raised:
            service.status(IMPORT_ID)
        self.assertEqual(raised.exception.code, "personal_import_session_expired")
        self.assertEqual(list(staging.glob("*")), [])

    def _real_tabular_service(self, content: str) -> PersonalImportService:
        source = self.root / "real-experiment.csv"
        source.write_text(content, encoding="utf-8")
        return PersonalImportService(
            data_root=self.root / f"real-private-{len(content)}",
            selection_provider=_FileSelectionProvider(source),
            repository=_MemoryRepository(),  # type: ignore[arg-type]
            import_id_factory=lambda: IMPORT_ID,
        )

    def test_tabular_page_reads_real_staged_snapshot_with_bounded_pagination(self) -> None:
        service = self._real_tabular_service(
            "Dose (dpa),Hardness [GPa]\n0,3.2\n1,4.0\n2,4.4\n"
        )
        service.preview(SELECTION_ID)

        first = service.tabular_page(
            IMPORT_ID,
            sheet_index=0,
            page=1,
            page_size=2,
        ).public_dict()
        second = service.tabular_page(
            IMPORT_ID,
            sheet_index=0,
            page=2,
            page_size=2,
        ).public_dict()

        self.assertEqual(first["schema_version"], "personal-tabular-page-v1")
        self.assertEqual(first["columns"], ["Dose (dpa)", "Hardness [GPa]"])
        self.assertEqual(first["rows"], [["0", "3.2"], ["1", "4.0"]])
        self.assertEqual(first["total_rows"], 3)
        self.assertTrue(first["has_next"])
        self.assertEqual(second["rows"], [["2", "4.4"]])
        self.assertFalse(second["has_next"])
        serialized = json.dumps(first, ensure_ascii=False).casefold()
        for forbidden in ("path", "sha256", "file_id", "source_file_id", "draft_id"):
            self.assertNotIn(forbidden, serialized)

    def test_tabular_page_rejects_invalid_bounds_and_expired_session(self) -> None:
        clock = [100.0]
        source = self.root / "bounds.csv"
        source.write_text("x,y\n1,2\n", encoding="utf-8")
        service = PersonalImportService(
            data_root=self.root / "bounds-private",
            selection_provider=_FileSelectionProvider(source),
            repository=_MemoryRepository(),  # type: ignore[arg-type]
            import_id_factory=lambda: IMPORT_ID,
            clock=lambda: clock[0],
            session_ttl_seconds=300,
        )
        service.preview(SELECTION_ID)
        for kwargs in (
            {"sheet_index": -1, "page": 1, "page_size": 50},
            {"sheet_index": 0, "page": 0, "page_size": 50},
            {"sheet_index": 0, "page": 1, "page_size": 101},
        ):
            with self.assertRaises(PersonalImportServiceError) as raised:
                service.tabular_page(IMPORT_ID, **kwargs)
            self.assertEqual(raised.exception.code, "personal_request_invalid")
        clock[0] += 301
        with self.assertRaises(PersonalImportServiceError) as raised:
            service.tabular_page(IMPORT_ID, sheet_index=0)
        self.assertEqual(raised.exception.code, "personal_import_session_expired")

    def test_tabular_page_detects_staged_snapshot_change(self) -> None:
        service = self._real_tabular_service("x,y\n1,2\n")
        service.preview(SELECTION_ID)
        staged = next((service.data_root / ".import-staging").iterdir())
        staged.write_bytes(b"x,y\n9,8\n")

        with self.assertRaises(PersonalImportServiceError) as raised:
            service.tabular_page(IMPORT_ID, sheet_index=0)
        self.assertEqual(raised.exception.code, "personal_tabular_changed")
        self.assertFalse(raised.exception.retryable)
        self.assertNotIn(str(staged), json.dumps(raised.exception.public_dict()))

    def test_tabular_page_rejects_preview_metadata_mismatch(self) -> None:
        self.service.preview(SELECTION_ID)
        with self.assertRaises(PersonalImportServiceError) as raised:
            self.service.tabular_page(IMPORT_ID, sheet_index=0)
        self.assertEqual(raised.exception.code, "personal_tabular_changed")
        self.assertFalse(raised.exception.retryable)

    def test_indexable_import_does_not_bypass_moved_staging_snapshot(self) -> None:
        service = self._real_tabular_service(
            "Dose (dpa),Hardness [GPa]\n0,3.2\n1,4.0\n"
        )
        service.preview(SELECTION_ID)
        confirmed = service.import_reviewed(
            IMPORT_ID,
            self._payload(confirmed=False),
            reviewed=True,
        )
        self.assertTrue(confirmed.indexable)

        with self.assertRaises(PersonalImportServiceError) as raised:
            service.tabular_page(IMPORT_ID, sheet_index=0)
        self.assertEqual(
            raised.exception.code,
            "personal_tabular_snapshot_unavailable",
        )
        self.assertFalse(raised.exception.retryable)


if __name__ == "__main__":
    unittest.main()
