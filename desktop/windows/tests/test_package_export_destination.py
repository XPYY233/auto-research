from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT))
try:
    import package_export_destination as MODULE
finally:
    sys.path.pop(0)


class _Window:
    def __init__(self, selected):
        self.selected = selected

    def choose_save_file(self, **_kwargs):
        return self.selected


class PackageExportDestinationTests(unittest.TestCase):
    def test_destination_token_is_path_free_one_shot_and_detects_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "collection.aresearch"
            broker = MODULE.WindowsPackageExportDestinationBroker(
                token_factory=lambda: "destination_0123456789abcdef"
            )
            adapter = MODULE.WindowsPackageExportDestinationAdapter(
                broker, _Window(str(destination))
            )
            public = adapter.select_package_export_destination("collection.aresearch")
            self.assertTrue(public["ok"])
            self.assertNotIn(directory, str(public))
            token = public["destination"]["destination_token"]
            self.assertEqual(
                broker.resolve(token).path,
                destination.parent.resolve(strict=True) / destination.name,
            )
            with self.assertRaises(MODULE.PackageExportDestinationError):
                broker.resolve(token)

            destination.write_bytes(b"occupied")
            denied = adapter.select_package_export_destination("collection.aresearch")
            self.assertFalse(denied["ok"])
            self.assertEqual(denied["error"]["code"], "package_destination_exists")

    def test_cancel_and_unc_path_are_path_free(self):
        broker = MODULE.WindowsPackageExportDestinationBroker()
        self.assertEqual(
            MODULE.WindowsPackageExportDestinationAdapter(broker, _Window(None))
            .select_package_export_destination("export.aresearch"),
            {"ok": True, "cancelled": True},
        )
        denied = MODULE.WindowsPackageExportDestinationAdapter(
            broker, _Window(r"\\server\share\secret.aresearch")
        ).select_package_export_destination("export.aresearch")
        self.assertEqual(denied["error"]["code"], "package_destination_invalid")
        self.assertNotIn("server", str(denied).casefold())

    def test_nonlocal_destination_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            broker = MODULE.WindowsPackageExportDestinationBroker(
                local_volume_probe=lambda _path: False
            )
            denied = MODULE.WindowsPackageExportDestinationAdapter(
                broker, _Window(str(Path(directory) / "export.aresearch"))
            ).select_package_export_destination("export.aresearch")
            self.assertEqual(denied["error"]["code"], "package_destination_nonlocal")


if __name__ == "__main__":
    unittest.main()
