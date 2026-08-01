from __future__ import annotations

import sys
import unittest
from pathlib import Path, PureWindowsPath


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT))
try:
    import package_input as MODULE
finally:
    sys.path.pop(0)


class FakeProbe:
    def __init__(self) -> None:
        self.identities: dict[str, MODULE.LocalFileIdentity | Exception] = {}
        self.calls: list[str] = []

    def add(
        self,
        path: str,
        *,
        size: int = 100,
        device: int = 1,
        inode: int = 2,
        modified_ns: int = 3,
    ) -> None:
        canonical = str(PureWindowsPath(path))
        self.identities[path] = MODULE.LocalFileIdentity(
            canonical, size, device, inode, modified_ns
        )
        self.identities[canonical] = self.identities[path]

    def inspect_regular_local_file(self, raw_path: str) -> MODULE.LocalFileIdentity:
        self.calls.append(raw_path)
        value = self.identities.get(raw_path)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise MODULE.PackageInputError("unavailable", "所选资料包无法读取")
        return value


class PackageInputTests(unittest.TestCase):
    def test_picker_drop_and_association_share_one_safe_contract(self) -> None:
        raw = r"C:\Users\Researcher\Downloads\evidence.aresearch"
        for source in ("file-picker", "drag-drop", "file-association"):
            with self.subTest(source=source):
                probe = FakeProbe()
                probe.add(raw)
                broker = MODULE.PackageInputBroker(
                    probe,
                    handle_factory=lambda: "opaque-handle-1234567890",
                    native_path_factory=PureWindowsPath,
                )
                handle = broker.accept([raw], source=source)
                public = handle.public_result()
                self.assertTrue(public["accepted"])
                self.assertNotIn(raw, repr(handle))
                self.assertNotIn(raw, str(public))
                controlled = broker.resolve_for_import(handle)
                self.assertEqual(controlled, PureWindowsPath(raw))
                self.assertEqual(len(probe.calls), 2)

    def test_multiple_directory_link_or_changed_file_is_rejected(self) -> None:
        raw = r"C:\Data\evidence.aresearch"
        probe = FakeProbe()
        probe.add(raw)
        broker = MODULE.PackageInputBroker(
            probe,
            handle_factory=lambda: "opaque-handle-1234567890",
            native_path_factory=PureWindowsPath,
        )
        with self.assertRaises(MODULE.PackageInputError) as multiple:
            broker.accept([raw, raw], source="drag-drop")
        self.assertEqual(multiple.exception.code, "single_file_required")

        for code in ("not_regular_file", "link_rejected"):
            probe.identities[raw] = MODULE.PackageInputError(code, "所选输入不可用")
            with self.subTest(code=code):
                with self.assertRaises(MODULE.PackageInputError) as rejected:
                    broker.accept([raw], source="file-picker")
                self.assertEqual(rejected.exception.code, code)

        probe.add(raw, size=100, inode=2)
        handle = broker.accept([raw], source="file-picker")
        probe.add(raw, size=100, inode=2, modified_ns=4)
        with self.assertRaises(MODULE.PackageInputError) as changed:
            broker.resolve_for_import(handle)
        self.assertEqual(changed.exception.code, "file_changed")

    def test_unc_device_relative_wrong_extension_and_long_paths_are_rejected_without_probe(self) -> None:
        values = (
            r"\\server\share\evidence.aresearch",
            r"\\?\C:\Data\evidence.aresearch",
            r"\\.\C:\Data\evidence.aresearch",
            r"C:\Data\..\evidence.aresearch",
            r"relative\evidence.aresearch",
            r"C:\Data\evidence.zip",
            "C:\\" + "a" * 230 + ".aresearch",
        )
        probe = FakeProbe()
        broker = MODULE.PackageInputBroker(probe)
        for value in values:
            with self.subTest(value=value):
                with self.assertRaises(MODULE.PackageInputError) as rejected:
                    broker.accept([value], source="file-picker")
                self.assertNotIn(value, str(rejected.exception))
        self.assertEqual(probe.calls, [])

    def test_handle_from_another_broker_cannot_be_resolved(self) -> None:
        raw = r"C:\Data\evidence.aresearch"
        first_probe = FakeProbe()
        first_probe.add(raw)
        first = MODULE.PackageInputBroker(
            first_probe, handle_factory=lambda: "opaque-handle-1234567890"
        )
        second = MODULE.PackageInputBroker(FakeProbe())
        handle = first.accept([raw], source="file-picker")
        with self.assertRaises(MODULE.PackageInputError) as rejected:
            second.resolve_for_import(handle)
        self.assertEqual(rejected.exception.code, "invalid_handle")


if __name__ == "__main__":
    unittest.main()
