from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT))
try:
    import personal_file_selection as MODULE
finally:
    sys.path.pop(0)


class _Ids:
    def __call__(self) -> str:
        return "personal_selection_0123456789abcdef"


def _test_path(candidate: object) -> Path:
    path = Path(candidate)
    if not path.is_absolute() or path.suffix.casefold() not in MODULE.SUPPORTED_EXTENSIONS:
        raise MODULE._error("personal_selection_invalid")
    if not path.exists():
        raise MODULE._error("personal_selection_missing")
    if path.is_symlink():
        raise MODULE._error("personal_selection_symlink")
    if not path.is_file():
        raise MODULE._error("personal_selection_not_regular")
    return path


class PersonalFileSelectionTests(unittest.TestCase):
    def test_opaque_snapshot_is_path_free_and_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "experiment.csv"
            source.write_text("x,y\n1,2\n", encoding="utf-8")
            broker = MODULE.WindowsPersonalFileSelectionBroker(
                local_volume_probe=lambda _path: True,
                selection_id_factory=_Ids(),
                path_validator=_test_path,
            )
            selected = broker.select(source)
            public = selected.public_dict()
            self.assertNotIn(str(source), json.dumps(public))
            self.assertNotIn("path", public)
            with broker.snapshot(selected.selection_id) as snapshot:
                self.assertEqual(snapshot.path.read_bytes(), source.read_bytes())
                self.assertNotEqual(snapshot.path.parent, source.parent)
            broker.revoke(selected.selection_id)
            with self.assertRaises(MODULE.PersonalFileSelectionError):
                with broker.snapshot(selected.selection_id):
                    pass

    def test_windows_native_contract_rejects_network_device_and_wrong_extension(self) -> None:
        for candidate in (
            r"\\server\share\experiment.csv",
            r"\\?\C:\secret\experiment.csv",
            r"C:\Users\Researcher\experiment.xls",
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(MODULE.PersonalFileSelectionError):
                    MODULE._validated_path(candidate)

    def test_shared_ui_native_facade_has_exact_path_free_cancel_envelope(self) -> None:
        class Window:
            selected = []

            def choose_files(self, **_kwargs):
                return self.selected

        window = Window()
        broker = MODULE.WindowsPersonalFileSelectionBroker(
            local_volume_probe=lambda _path: True,
            selection_id_factory=_Ids(),
            path_validator=_test_path,
        )
        adapter = MODULE.WindowsPersonalFileInputAdapter(broker, window)
        self.assertEqual(
            adapter.select_personal_data_file(),
            {"ok": True, "cancelled": True},
        )
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "experiment.csv"
            source.write_text("x,y\n1,2\n", encoding="utf-8")
            window.selected = [str(source)]
            payload = adapter.select_personal_data_file()
        self.assertEqual(set(payload), {"ok", "cancelled", "selection"})
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["cancelled"])
        self.assertNotIn(str(source), json.dumps(payload))
        self.assertNotIn("path", json.dumps(payload).casefold())


if __name__ == "__main__":
    unittest.main()
