from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))

from personal_file_selection_broker import (  # noqa: E402
    PersonalFileSelectionBroker,
    PersonalFileSelectionError,
    PersonalFileSelectionSource,
)


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"personal_selection_{self.value:016d}"


class PersonalFileSelectionBrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.clock = _Clock()
        self.broker = PersonalFileSelectionBroker(
            local_volume_probe=lambda _path: True,
            selection_id_factory=_Ids(),
            clock=self.clock,
            ttl_seconds=60,
            max_active_selections=4,
            max_file_bytes=1_024,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _file(self, name: str = "experiment.csv", content: bytes = b"x,y\n1,2\n") -> Path:
        path = self.root / name
        path.write_bytes(content)
        return path

    def test_supported_files_produce_opaque_path_free_dto(self) -> None:
        for name in ("experiment.csv", "experiment.tsv", "experiment.xlsx"):
            with self.subTest(name=name):
                snapshot = self.broker.select(
                    PersonalFileSelectionSource.FILE_PICKER,
                    self._file(name),
                )
                payload = snapshot.public_dict()
                serialized = json.dumps(payload)
                self.assertEqual(payload["source"], "file_picker")
                self.assertTrue(payload["selection_id"].startswith("personal_selection_"))
                self.assertNotIn(str(self.root), serialized)
                self.assertNotIn(name, serialized)
                self.assertNotIn("path", payload)

    def test_snapshot_is_private_immutable_copy_and_is_cleaned(self) -> None:
        source = self._file(content=b"a,b\n3,4\n")
        selection = self.broker.select(
            PersonalFileSelectionSource.FILE_PICKER,
            source,
        )
        with self.broker.snapshot(selection.selection_id) as copied:
            private_path = copied.path
            self.assertEqual(private_path.read_bytes(), source.read_bytes())
            self.assertNotEqual(private_path.parent, source.parent)
            public = self.broker.resolve(selection.selection_id).public_dict()
            self.assertNotIn(str(source), json.dumps(public))
        self.assertFalse(private_path.exists())

    def test_replacement_after_selection_is_revoked(self) -> None:
        source = self._file()
        selection = self.broker.select(
            PersonalFileSelectionSource.FILE_PICKER,
            source,
        )
        replacement = self._file("replacement.csv", b"x,y\n9,9\n")
        os.replace(replacement, source)

        with self.assertRaises(PersonalFileSelectionError) as raised:
            self.broker.resolve(selection.selection_id)
        self.assertEqual(raised.exception.code, "personal_selection_changed")
        with self.assertRaises(PersonalFileSelectionError) as expired:
            self.broker.resolve(selection.selection_id)
        self.assertEqual(expired.exception.code, "personal_selection_expired")

    def test_expiry_and_capacity_are_bounded(self) -> None:
        first = self.broker.select(
            PersonalFileSelectionSource.FILE_PICKER,
            self._file("one.csv"),
        )
        self.clock.value += 61
        with self.assertRaises(PersonalFileSelectionError) as expired:
            self.broker.resolve(first.selection_id)
        self.assertEqual(expired.exception.code, "personal_selection_expired")

        limited = PersonalFileSelectionBroker(
            local_volume_probe=lambda _path: True,
            selection_id_factory=_Ids(),
            max_active_selections=1,
        )
        limited.select(PersonalFileSelectionSource.FILE_PICKER, self._file("two.csv"))
        with self.assertRaises(PersonalFileSelectionError) as capacity:
            limited.select(PersonalFileSelectionSource.FILE_PICKER, self._file("three.csv"))
        self.assertEqual(capacity.exception.code, "personal_selection_capacity")

    def test_unsafe_native_inputs_fail_closed_without_path_leak(self) -> None:
        directory = self.root / "folder.csv"
        directory.mkdir()
        source = self._file("source.csv")
        symlink = self.root / "shortcut.csv"
        symlink.symlink_to(source)
        cases = (
            ([source], "personal_selection_multiple"),
            (f"file://{source}", "personal_selection_file_url"),
            ("relative.csv", "personal_selection_invalid"),
            (self._file("legacy.xls"), "personal_selection_extension"),
            (directory, "personal_selection_not_regular"),
            (symlink, "personal_selection_symlink"),
            (self.root / "missing.csv", "personal_selection_missing"),
        )
        for candidate, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(PersonalFileSelectionError) as raised:
                    self.broker.select(PersonalFileSelectionSource.FILE_PICKER, candidate)
                self.assertEqual(raised.exception.code, expected)
                self.assertNotIn(str(self.root), json.dumps(raised.exception.public_dict()))

    def test_nonlocal_unknown_and_oversize_are_rejected(self) -> None:
        source = self._file(content=b"x" * 10)
        for probe, expected in (
            (lambda _path: False, "personal_selection_nonlocal"),
            (lambda _path: None, "personal_selection_locality_unknown"),
        ):
            broker = PersonalFileSelectionBroker(local_volume_probe=probe)
            with self.assertRaises(PersonalFileSelectionError) as raised:
                broker.select(PersonalFileSelectionSource.FILE_PICKER, source)
            self.assertEqual(raised.exception.code, expected)

        broker = PersonalFileSelectionBroker(
            local_volume_probe=lambda _path: True,
            max_file_bytes=5,
        )
        with self.assertRaises(PersonalFileSelectionError) as raised:
            broker.select(PersonalFileSelectionSource.FILE_PICKER, source)
        self.assertEqual(raised.exception.code, "personal_selection_too_large")


if __name__ == "__main__":
    unittest.main()
