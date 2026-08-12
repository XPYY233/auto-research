from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT))
try:
    import settings_store as MODULE
finally:
    sys.path.pop(0)


def snapshot(revision: int, theme: str = "system") -> dict[str, object]:
    return {
        "schema_version": "desktop-settings-v1",
        "revision": revision,
        "appearance": {"theme": theme, "density": "comfortable"},
        "locale": {"selected": "zh-CN", "supported": ["zh-CN"]},
    }


class WindowsSettingsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name) / "LocalAppData" / "Auto Research" / "State"
        self.store = MODULE.WindowsAtomicDesktopSettingsStore(self.state)

    def test_default_location_is_state_settings_v1_and_missing_is_empty(self) -> None:
        self.assertEqual(self.store.path, self.state / "settings-v1.json")
        self.assertIsNone(self.store.read())

    def test_compare_and_swap_is_atomic_and_preserves_revision(self) -> None:
        self.assertTrue(self.store.compare_and_swap(expected_revision=0, value=snapshot(1)))
        self.assertEqual(self.store.read(), snapshot(1))
        self.assertFalse(
            self.store.compare_and_swap(
                expected_revision=0,
                value=snapshot(2, "dark"),
            )
        )
        self.assertEqual(self.store.read(), snapshot(1))
        self.assertTrue(
            self.store.compare_and_swap(
                expected_revision=1,
                value=snapshot(2, "dark"),
            )
        )
        self.assertEqual(self.store.read(), snapshot(2, "dark"))
        self.assertEqual(list(self.state.glob(".settings-v1-*")), [])

    def test_symlink_oversize_and_non_object_files_fail_closed(self) -> None:
        self.state.mkdir(parents=True)
        target = self.state / "target.json"
        target.write_text("{}", encoding="utf-8")
        try:
            self.store.path.symlink_to(target)
        except (OSError, NotImplementedError):
            pass
        else:
            with self.assertRaises(OSError):
                self.store.read()
            self.store.path.unlink()

        self.store.path.write_bytes(b"x" * (MODULE.MAX_SETTINGS_FILE_BYTES + 1))
        with self.assertRaises(OSError):
            self.store.read()
        self.store.path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.read()

    def test_settings_are_non_secret_and_encoded_value_is_bounded(self) -> None:
        value = snapshot(1)
        self.assertTrue(self.store.compare_and_swap(expected_revision=0, value=value))
        rendered = self.store.path.read_text(encoding="utf-8").casefold()
        for forbidden in ("api_key", "credential", "secret", "/users/", "c:\\users\\"):
            self.assertNotIn(forbidden, rendered)
        oversized = {"revision": 1, "padding": "x" * MODULE.MAX_SETTINGS_FILE_BYTES}
        with self.assertRaises(OSError):
            MODULE.WindowsAtomicDesktopSettingsStore(
                Path(self.temporary.name) / "other"
            ).compare_and_swap(expected_revision=0, value=oversized)


if __name__ == "__main__":
    unittest.main()
