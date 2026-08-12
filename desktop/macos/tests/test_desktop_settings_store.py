from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = DESKTOP_ROOT.parents[1] / "src"
for path in (DESKTOP_ROOT, SOURCE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from auto_research.settings.desktop_settings import (  # noqa: E402
    DesktopSettingsService,
)
from desktop_settings_store import MacAtomicDesktopSettingsStore  # noqa: E402


class MacAtomicDesktopSettingsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="auto-research-desktop-settings-test-"
        )
        self.path = Path(self.temporary.name) / "State" / "settings-v1.json"
        self.store = MacAtomicDesktopSettingsStore(self.path)
        self.service = DesktopSettingsService(self.store)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_round_trip_is_private_and_cas_protected(self) -> None:
        initial = self.service.get()
        self.assertEqual(initial.revision, 0)
        updated = self.service.patch_preferences(
            {"appearance": {"theme": "dark", "density": "compact"}},
            expected_revision=0,
        )
        self.assertEqual(updated.revision, 1)
        self.assertEqual(self.service.get().preferences.theme, "dark")
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.path.parent).st_mode & 0o777, 0o700)
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotIn("api_key", json.dumps(stored))
        self.assertFalse(
            self.store.compare_and_swap(expected_revision=0, value=stored)
        )

    def test_symlink_file_is_rejected_without_overwriting_target(self) -> None:
        self.path.parent.mkdir(parents=True)
        target = Path(self.temporary.name) / "outside.json"
        target.write_text("untouched", encoding="utf-8")
        self.path.symlink_to(target)
        with self.assertRaises(Exception):
            self.service.get()
        self.assertEqual(target.read_text(encoding="utf-8"), "untouched")

    def test_corrupt_or_oversized_file_fails_closed(self) -> None:
        self.path.parent.mkdir(parents=True)
        for value in (b"not-json", b"{" + b"x" * 40_000):
            with self.subTest(size=len(value)):
                self.path.write_bytes(value)
                with self.assertRaises(Exception):
                    self.service.get()


if __name__ == "__main__":
    unittest.main()
