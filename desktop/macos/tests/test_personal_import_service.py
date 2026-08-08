from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))

from auto_research.personal.tabular_preview import UnsafeTabularFileError  # noqa: E402
from auto_research.personal.private_repository import (  # noqa: E402
    PrivateExperimentRepository,
    PrivateRepositoryError,
)
from personal_file_selection_broker import (  # noqa: E402
    PersonalFileSelectionBroker,
    PersonalFileSelectionSource,
)
from personal_import_service import (  # noqa: E402
    PersonalImportService,
    PersonalImportServiceError,
)


SELECTION_ID = "personal_selection_0123456789abcdef"
IMPORT_ID = "personal_import_0123456789abcdef"


class PersonalImportServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.library_root = self.root / "Application Support" / "private-library"
        self.source = self.root / "experiment.csv"
        self.source.write_text(
            "Dose (dpa),Hardness [GPa]\n0,3.2\n1,4.0\n",
            encoding="utf-8",
        )
        self.broker = PersonalFileSelectionBroker(
            local_volume_probe=lambda _path: True,
            selection_id_factory=lambda: SELECTION_ID,
        )
        selection = self.broker.select(
            PersonalFileSelectionSource.FILE_PICKER,
            self.source,
        )
        self.assertEqual(selection.selection_id, SELECTION_ID)
        self.service = PersonalImportService(
            data_root=self.library_root,
            broker=self.broker,
            import_id_factory=lambda: IMPORT_ID,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _payload(*, confirmed: bool, expected_revision: int | None = None):
        value = {
            "sheet_index": 0,
            "project": {"name": "W-Ta 辐照实验", "description": "私人项目"},
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

    def _preview(self):
        return self.service.preview(SELECTION_ID)

    def test_preview_is_path_free_read_only_and_uses_bounded_core_parser(self) -> None:
        preview = self._preview()
        payload = preview.public_dict()
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertEqual(payload["status"]["stage"], "previewed")
        self.assertFalse(payload["status"]["indexable"])
        self.assertEqual(payload["preview"]["detected_format"], "csv")
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn("sqlite", serialized.casefold())
        self.assertFalse(self.library_root.exists())

    def test_preview_translates_core_parser_rejection_without_leaking_path(self) -> None:
        with patch(
            "personal_import_service.preview_tabular_file",
            side_effect=UnsafeTabularFileError(f"unsafe {self.source}"),
        ):
            with self.assertRaises(PersonalImportServiceError) as raised:
                self._preview()
        self.assertEqual(raised.exception.code, "personal_preview_rejected")
        self.assertNotIn(str(self.root), json.dumps(raised.exception.public_dict()))
        self.assertFalse(self.library_root.exists())

    def test_preview_cannot_be_confirmed_before_draft(self) -> None:
        self._preview()
        with self.assertRaises(PersonalImportServiceError) as raised:
            self.service.confirm(IMPORT_ID, expected_revision=1)
        self.assertEqual(raised.exception.code, "INVALID_IMPORT_TRANSITION")
        self.assertEqual(self.service.status(IMPORT_ID).stage.value, "previewed")
        self.assertFalse(self.library_root.exists())

    def test_draft_is_not_indexable_until_all_explicit_confirmations(self) -> None:
        self._preview()
        draft = self.service.save_draft(
            IMPORT_ID,
            self._payload(confirmed=False),
        )
        self.assertEqual(draft.stage.value, "draft_saved")
        self.assertEqual(draft.revision, 1)
        self.assertFalse(draft.indexable)
        self.assertEqual(self.service.private_search_source().list_documents(), ())

        with self.assertRaises(PersonalImportServiceError) as raised:
            self.service.confirm(IMPORT_ID, expected_revision=1)
        self.assertEqual(raised.exception.code, "RUN_CONFIRMATION_INCOMPLETE")
        unchanged = self.service.status(IMPORT_ID)
        self.assertEqual(unchanged.revision, 1)
        self.assertFalse(unchanged.indexable)

    def test_revision_cas_then_confirm_makes_private_search_indexable(self) -> None:
        self._preview()
        self.service.save_draft(IMPORT_ID, self._payload(confirmed=False))

        with self.assertRaises(PersonalImportServiceError) as stale:
            self.service.save_draft(
                IMPORT_ID,
                self._payload(confirmed=True, expected_revision=99),
            )
        self.assertEqual(stale.exception.code, "RUN_REVISION_CONFLICT")
        self.assertEqual(self.service.status(IMPORT_ID).revision, 1)

        updated = self.service.save_draft(
            IMPORT_ID,
            self._payload(confirmed=True, expected_revision=1),
        )
        self.assertEqual(updated.revision, 2)
        self.assertFalse(updated.indexable)
        confirmed = self.service.confirm(IMPORT_ID, expected_revision=2)
        self.assertEqual(confirmed.stage.value, "indexable")
        self.assertEqual(confirmed.revision, 3)
        self.assertTrue(confirmed.indexable)
        documents = self.service.private_search_source().list_documents()
        self.assertGreater(len(documents), 0)
        serialized = json.dumps(
            [document.as_dict() for document in documents],
            ensure_ascii=False,
        )
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn("sqlite", serialized.casefold())

        with self.assertRaises(PersonalImportServiceError) as immutable:
            self.service.save_draft(
                IMPORT_ID,
                self._payload(confirmed=True, expected_revision=3),
            )
        self.assertEqual(immutable.exception.code, "personal_import_already_confirmed")

    def test_concurrent_confirm_has_exactly_one_success(self) -> None:
        self._preview()
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
        self.assertTrue(self.service.status(IMPORT_ID).indexable)

    def test_selected_file_replacement_does_not_create_draft_or_index(self) -> None:
        self._preview()
        replacement = self.root / "replacement.csv"
        replacement.write_text("x,y\n9,9\n", encoding="utf-8")
        os.replace(replacement, self.source)

        with self.assertRaises(PersonalImportServiceError) as raised:
            self.service.save_draft(IMPORT_ID, self._payload(confirmed=True))
        self.assertEqual(raised.exception.code, "personal_selection_changed")
        status = self.service.status(IMPORT_ID)
        self.assertEqual(status.stage.value, "previewed")
        self.assertIsNone(status.revision)
        self.assertEqual(self.service.private_search_source().list_documents(), ())
        self.assertEqual(list(self.library_root.rglob("*.tmp")), [])

    def test_repository_failure_leaves_no_draft_or_search_document(self) -> None:
        repository = PrivateExperimentRepository(self.library_root)
        service = PersonalImportService(
            data_root=self.library_root,
            broker=self.broker,
            repository=repository,
            import_id_factory=lambda: IMPORT_ID,
        )
        service.preview(SELECTION_ID)
        with patch.object(
            repository,
            "save_experiment",
            side_effect=PrivateRepositoryError(
                "PRIVATE_DB_WRITE_FAILED",
                "暂时无法保存私人实验数据。",
                details={"operation": "save_experiment"},
            ),
        ):
            with self.assertRaises(PersonalImportServiceError) as raised:
                service.save_draft(IMPORT_ID, self._payload(confirmed=True))
        self.assertEqual(raised.exception.code, "PRIVATE_DB_WRITE_FAILED")
        self.assertTrue(raised.exception.retryable)
        status = service.status(IMPORT_ID)
        self.assertEqual(status.stage.value, "previewed")
        self.assertIsNone(status.revision)
        self.assertEqual(service.private_search_source().list_documents(), ())
        self.assertEqual(list(self.library_root.rglob("*.tmp")), [])

    def test_confirmation_flags_must_be_explicit_booleans(self) -> None:
        self._preview()
        payload = copy.deepcopy(self._payload(confirmed=True))
        payload["columns"][0]["unit_confirmed"] = "yes"
        with self.assertRaises(PersonalImportServiceError) as raised:
            self.service.save_draft(IMPORT_ID, payload)
        self.assertEqual(raised.exception.code, "personal_confirmation_flag_required")
        self.assertEqual(self.service.status(IMPORT_ID).stage.value, "previewed")
        self.assertFalse(self.library_root.exists())


if __name__ == "__main__":
    unittest.main()
