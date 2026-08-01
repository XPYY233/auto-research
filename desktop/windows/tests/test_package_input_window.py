from __future__ import annotations

import sys
import unittest
from pathlib import Path, PureWindowsPath


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT))
try:
    import package_input as INPUT
    import package_input_window as WINDOW
finally:
    sys.path.pop(0)


class FakeProbe:
    def __init__(self, raw: str) -> None:
        self.identity = INPUT.LocalFileIdentity(str(PureWindowsPath(raw)), 10, 1, 2, 3)

    def inspect_regular_local_file(self, raw_path: str):
        return self.identity


class FakeWindow:
    def __init__(self, selected) -> None:
        self.selected = selected
        self.calls = []

    def choose_files(self, **kwargs):
        self.calls.append(kwargs)
        return self.selected


class PackageInputWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = r"C:\Users\Researcher\Downloads\evidence.aresearch"
        self.broker = INPUT.PackageInputBroker(
            FakeProbe(self.raw),
            handle_factory=lambda: "opaque-handle-1234567890",
            native_path_factory=PureWindowsPath,
        )

    def test_picker_requests_one_aresearch_file(self) -> None:
        window = FakeWindow([self.raw])
        adapter = WINDOW.PackageInputWindowAdapter(self.broker, window)
        handle = adapter.choose_package()
        self.assertEqual(handle.source, "file-picker")
        self.assertEqual(window.calls[0]["extensions"], (".aresearch",))
        self.assertFalse(window.calls[0]["multiple"])

    def test_drop_and_file_association_reuse_broker(self) -> None:
        adapter = WINDOW.PackageInputWindowAdapter(self.broker, FakeWindow([]))
        self.assertEqual(adapter.accept_drop([self.raw]).source, "drag-drop")
        self.assertEqual(
            adapter.accept_file_association([self.raw]).source,
            "file-association",
        )

    def test_cancel_or_multi_drop_is_rejected_without_path_response(self) -> None:
        adapter = WINDOW.PackageInputWindowAdapter(self.broker, FakeWindow([]))
        for candidates in ([], [self.raw, self.raw]):
            with self.subTest(candidates=candidates):
                with self.assertRaises(INPUT.PackageInputError) as rejected:
                    adapter.accept_drop(candidates)
                self.assertEqual(rejected.exception.code, "single_file_required")
                self.assertNotIn(self.raw, str(rejected.exception))


if __name__ == "__main__":
    unittest.main()
