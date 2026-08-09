from __future__ import annotations

import sys
import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path

from auto_research.personal.import_service import (
    PersonalImportService,
    PersonalImportServiceError,
)
from auto_research.personal.public_projection import project_personal_renderer_payload


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT))
try:
    import personal_file_selection as FILE_SELECTION
    import personal_import_bridge as MODULE
finally:
    sys.path.pop(0)


class _Result:
    def __init__(self, stage: str) -> None:
        self.stage = stage
        self.revision = 3

    def public_dict(self):
        return {
            "schema_version": "personal-import-status-v1",
            "stage": self.stage,
            "indexable": self.stage == "indexable",
        }


class _Suggestion:
    def public_dict(self):
        return {
            "schema_version": "personal-import-suggestion-v1",
            "provider": "DeepSeek",
            "columns": [
                {
                    "source_name": "Hardness [GPa]",
                    "role": "dependent",
                    "meaning": "硬度",
                    "unit": "GPa",
                    "confidence": 0.96,
                    "rationale": "列名与单位明确",
                }
            ],
            "series": [],
            "requires_human_review": True,
        }


class _Service:
    def __init__(self) -> None:
        self.calls = []
        self.snapshot = type(
            "Snapshot",
            (),
            {
                "source_id": "private-source",
                "content_fingerprint": "f" * 64,
                "document_count": 1,
            },
        )()

    def preview(self, selection_id):
        self.calls.append(("preview", selection_id))
        return _Result("previewed")

    def status(self, import_id):
        self.calls.append(("status", import_id))
        return _Result("draft_saved")

    def save_draft(self, import_id, payload):
        self.calls.append(("draft", import_id, payload))
        return _Result("draft_saved")

    def confirm(self, import_id, *, expected_revision):
        self.calls.append(("confirm", import_id, expected_revision))
        return _Result("indexable")

    def suggest(self, import_id, *, sheet_index):
        self.calls.append(("suggest", import_id, sheet_index))
        return _Suggestion()

    def import_reviewed(self, import_id, payload, *, reviewed):
        self.calls.append(("reviewed", import_id, payload, reviewed))
        return _Result("indexable")

    def private_search_snapshot(self):
        return self.snapshot


class _Search:
    def __init__(self) -> None:
        self.calls = []
        self.fail = False

    def refresh_private_source(self, source, *, source_id, fingerprint):
        self.calls.append((source, source_id, fingerprint))
        if self.fail:
            raise RuntimeError("private path must not escape")


class PersonalImportBridgeTests(unittest.TestCase):
    def test_bridge_reuses_shared_renderer_projection(self) -> None:
        self.assertIs(
            MODULE.project_personal_renderer_payload,
            project_personal_renderer_payload,
        )

    def test_bridge_delegates_payload_and_refreshes_only_after_confirm(self) -> None:
        service = _Service()
        search = _Search()
        bridge = MODULE.PersonalImportBridgeAdapter(
            service,  # type: ignore[arg-type]
            search_service=search,
        )
        payload = {"sheet_index": 0, "columns": [{"meaning_confirmed": True}]}
        self.assertEqual(bridge.preview("personal_selection_0123456789abcdef")["stage"], "previewed")
        self.assertEqual(bridge.save_draft("personal_import_0123456789abcdef", payload)["stage"], "draft_saved")
        self.assertIs(service.calls[1][2], payload)
        self.assertEqual(search.calls, [])
        confirmed = bridge.confirm(
            "personal_import_0123456789abcdef",
            expected_revision=2,
        )
        self.assertTrue(confirmed["indexable"])
        self.assertEqual(
            search.calls,
            [(service.snapshot, "private-source", "f" * 64)],
        )
        public_status = bridge.search_status()
        self.assertTrue(public_status["ready"])
        self.assertNotIn("active_fingerprint", public_status)

    def test_shared_error_schema_passes_through_path_free(self) -> None:
        error = PersonalImportServiceError(
            "RUN_REVISION_CONFLICT",
            "实验草稿已被更新。",
            retryable=False,
            details={"expected_revision": 1, "actual_revision": 2},
        )
        public = MODULE.PersonalImportBridgeAdapter.public_error(error)
        self.assertEqual(public["code"], error.code)
        self.assertEqual(public["message"], error.message)
        self.assertNotIn("details", public)
        self.assertNotIn("expected_revision", str(public))
        self.assertEqual(
            MODULE.PersonalImportBridgeAdapter.error_http_status(
                "personal_ai_consent_required"
            ),
            HTTPStatus.PRECONDITION_REQUIRED,
        )
        self.assertEqual(
            MODULE.PersonalImportBridgeAdapter.error_http_status(
                "personal_ai_invalid_response"
            ),
            HTTPStatus.BAD_GATEWAY,
        )

    def test_ai_suggestion_and_reviewed_import_remain_thin_and_path_free(self) -> None:
        service = _Service()
        search = _Search()
        bridge = MODULE.PersonalImportBridgeAdapter(
            service,  # type: ignore[arg-type]
            search_service=search,
        )
        suggestion = bridge.suggest(
            "personal_import_0123456789abcdef",
            sheet_index=0,
        )
        self.assertEqual(suggestion["schema_version"], "personal-import-suggestion-v1")
        self.assertTrue(suggestion["requires_human_review"])
        self.assertNotIn("path", str(suggestion).casefold())
        draft = {"sheet_index": 0, "columns": []}
        imported = bridge.import_reviewed(
            "personal_import_0123456789abcdef",
            draft,
            reviewed=True,
        )
        self.assertTrue(imported["indexable"])
        self.assertIn(
            ("reviewed", "personal_import_0123456789abcdef", draft, True),
            service.calls,
        )
        self.assertEqual(search.calls[-1], (service.snapshot, "private-source", "f" * 64))

    def test_windows_snapshot_runs_shared_preview_draft_confirm_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "experiment.csv"
            source.write_text(
                "Dose (dpa),Hardness [GPa]\n0,3.2\n1,4.0\n",
                encoding="utf-8",
            )

            def validate(candidate):
                return Path(candidate)

            broker = FILE_SELECTION.WindowsPersonalFileSelectionBroker(
                local_volume_probe=lambda _path: True,
                selection_id_factory=lambda: "personal_selection_0123456789abcdef",
                path_validator=validate,
            )
            selection = broker.select(source)
            service = PersonalImportService(
                data_root=root / "private-library",
                selection_provider=broker,
                import_id_factory=lambda: "personal_import_0123456789abcdef",
            )
            refreshed = []
            import evidence_search_service as SEARCH

            search = SEARCH.WindowsEvidenceSearchService()
            bridge = MODULE.PersonalImportBridgeAdapter(
                service,
                search_service=search,
            )
            preview = bridge.preview(selection.selection_id)
            import_id = preview["status"]["import_id"]
            payload = {
                "sheet_index": 0,
                "project": {"name": "W-Ta irradiation"},
                "sample": {"name": "W-Ta-01", "material": "W-Ta"},
                "run": {
                    "name": "Indentation batch 1",
                    "method": "indentation",
                    "conditions": {},
                },
                "columns": [
                    {
                        "source_name": "Dose (dpa)",
                        "role": "independent",
                        "role_confirmed": True,
                        "meaning": "irradiation dose",
                        "meaning_confirmed": True,
                        "unit": "dpa",
                        "unit_confirmed": True,
                    },
                    {
                        "source_name": "Hardness [GPa]",
                        "role": "dependent",
                        "role_confirmed": True,
                        "meaning": "hardness",
                        "meaning_confirmed": True,
                        "unit": "GPa",
                        "unit_confirmed": True,
                    },
                ],
                "series": [
                    {
                        "series_id": "hardness-dose",
                        "name": "Hardness versus dose",
                        "x_column": "Dose (dpa)",
                        "y_column": "Hardness [GPa]",
                    }
                ],
            }
            draft = bridge.save_draft(import_id, payload)
            confirmed = bridge.confirm(
                import_id,
                expected_revision=draft["revision"],
            )
            self.assertEqual(bridge.status(import_id), confirmed)
            self.assertTrue(confirmed["indexable"])
            search_status = bridge.search_status()
            self.assertTrue(search_status["ready"])
            self.assertEqual(search.status()["private_source"]["fingerprint"], service.private_search_snapshot().content_fingerprint)
            serialized = str(preview)
            self.assertNotIn(str(root), serialized)
            self.assertNotIn("sha256", serialized)
            self.assertNotIn("file_id", serialized)

    def test_refresh_failure_keeps_stale_public_state_without_fingerprint(self) -> None:
        service = _Service()
        search = _Search()
        bridge = MODULE.PersonalImportBridgeAdapter(
            service,  # type: ignore[arg-type]
            search_service=search,
        )
        bridge.restore_private_search()
        search.fail = True
        with self.assertRaises(PersonalImportServiceError) as raised:
            bridge.refresh_search()
        self.assertEqual(raised.exception.code, "personal_search_refresh_failed")
        status = bridge.search_status()
        self.assertEqual(status["state"], "stale")
        self.assertTrue(status["ready"])
        self.assertNotIn("active_fingerprint", status)
        self.assertNotIn("path", str(status).casefold())

    def test_empty_startup_snapshot_does_not_register_private_source(self) -> None:
        service = _Service()
        service.snapshot.document_count = 0
        search = _Search()
        bridge = MODULE.PersonalImportBridgeAdapter(
            service,  # type: ignore[arg-type]
            search_service=search,
        )
        self.assertEqual(bridge.restore_private_search()["state"], "empty")
        self.assertEqual(search.calls, [])


if __name__ == "__main__":
    unittest.main()
