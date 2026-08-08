from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from auto_research.personal.import_service import (
    PersonalImportService,
    PersonalImportServiceError,
)


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


class _Service:
    def __init__(self) -> None:
        self.calls = []
        self.source = object()

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

    def private_search_source(self):
        return self.source


class PersonalImportBridgeTests(unittest.TestCase):
    def test_bridge_delegates_payload_and_refreshes_only_after_confirm(self) -> None:
        service = _Service()
        refreshed = []
        bridge = MODULE.PersonalImportBridgeAdapter(
            service,  # type: ignore[arg-type]
            private_source_listener=lambda source, fingerprint: refreshed.append(
                (source, fingerprint)
            ),
        )
        payload = {"sheet_index": 0, "columns": [{"meaning_confirmed": True}]}
        self.assertEqual(bridge.preview("personal_selection_0123456789abcdef")["stage"], "previewed")
        self.assertEqual(bridge.save_draft("personal_import_0123456789abcdef", payload)["stage"], "draft_saved")
        self.assertIs(service.calls[1][2], payload)
        self.assertEqual(refreshed, [])
        confirmed = bridge.confirm(
            "personal_import_0123456789abcdef",
            expected_revision=2,
        )
        self.assertTrue(confirmed["indexable"])
        self.assertEqual(refreshed, [(service.source, "revision:3")])

    def test_shared_error_schema_passes_through_path_free(self) -> None:
        error = PersonalImportServiceError(
            "RUN_REVISION_CONFLICT",
            "实验草稿已被更新。",
            retryable=False,
            details={"expected_revision": 1, "actual_revision": 2},
        )
        self.assertEqual(
            MODULE.PersonalImportBridgeAdapter.public_error(error),
            error.public_dict(),
        )

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
            bridge = MODULE.PersonalImportBridgeAdapter(
                service,
                private_source_listener=lambda private, fingerprint: refreshed.append(
                    (private.source_id, fingerprint)
                ),
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
            self.assertEqual(refreshed[0][1], f"revision:{confirmed['revision']}")
            self.assertNotIn(str(root), str(preview))


if __name__ == "__main__":
    unittest.main()
