from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))

from package_job_state import PackageImportJobCoordinator  # noqa: E402
from package_selection_broker import (  # noqa: E402
    MAX_NATIVE_PATH_BYTES,
    PackageSelectionBroker,
    PackageSelectionError,
    PackageSelectionSource,
    macos_local_volume_probe,
)


class MutableClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class PackageSelectionBrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="auto-research-package-selection-test-"
        )
        self.root = Path(self.temporary.name)
        self.clock = MutableClock()
        self.next_selection = 0

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create_package(self, name: str = "official.aresearch", content: bytes = b"package") -> Path:
        path = self.root / name
        path.write_bytes(content)
        return path

    def broker(
        self,
        *,
        local: bool | None = True,
        ttl: float = 30.0,
        capacity: int = 4,
    ) -> PackageSelectionBroker:
        def selection_id_factory() -> str:
            self.next_selection += 1
            return f"selection_{self.next_selection:016d}"

        return PackageSelectionBroker(
            local_volume_probe=lambda _path: local,
            selection_id_factory=selection_id_factory,
            clock=self.clock,
            ttl_seconds=ttl,
            max_active_selections=capacity,
        )

    def test_three_native_sources_share_one_opaque_contract(self) -> None:
        for source in PackageSelectionSource:
            with self.subTest(source=source):
                package = self.create_package(f"{source.value}.aresearch")
                snapshot = self.broker().select(source, package)
                coordinator = PackageImportJobCoordinator(
                    job_id_factory=lambda: "package_job_0000000000000001"
                )
                job = coordinator.begin_import(snapshot.selection_id)
                self.assertEqual(job.stage.value, "queued")
                self.assertEqual(snapshot.source, source)

    def test_public_dtos_never_expose_paths(self) -> None:
        package = self.create_package("private-topic.aresearch", b"abc")
        broker = self.broker()
        snapshot = broker.select(PackageSelectionSource.FILE_PICKER, package)
        resolved = broker.resolve(snapshot.selection_id)

        for public in (snapshot.public_dict(), resolved.public_dict()):
            serialized = json.dumps(public, ensure_ascii=False)
            self.assertNotIn(str(self.root), serialized)
            self.assertNotIn("private-topic", serialized)
            self.assertNotIn("path", serialized.casefold())
            self.assertEqual(public["size_bytes"], 3)
        self.assertEqual(resolved.path.read_bytes(), b"abc")

    def test_rejects_multiple_file_url_relative_wrong_extension_and_directory(self) -> None:
        package = self.create_package()
        directory = self.root / "folder.aresearch"
        directory.mkdir()
        cases = (
            ([package, package], "package_selection_multiple"),
            (f"file://{package}", "package_selection_file_url"),
            ("relative.aresearch", "package_selection_invalid"),
            (self.root / "wrong.zip", "package_selection_extension"),
            (directory, "package_selection_not_regular"),
        )
        broker = self.broker()
        for candidate, expected in cases:
            with self.subTest(candidate=candidate):
                with self.assertRaises(PackageSelectionError) as raised:
                    broker.select(PackageSelectionSource.FILE_PICKER, candidate)
                self.assertEqual(raised.exception.code, expected)

    def test_rejects_symlink_and_missing_file(self) -> None:
        package = self.create_package()
        link = self.root / "link.aresearch"
        try:
            link.symlink_to(package)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        broker = self.broker()
        with self.assertRaises(PackageSelectionError) as symlinked:
            broker.select(PackageSelectionSource.DRAG_DROP, link)
        self.assertEqual(symlinked.exception.code, "package_selection_symlink")

        with self.assertRaises(PackageSelectionError) as missing:
            broker.select(PackageSelectionSource.FILE_ASSOCIATION, self.root / "gone.aresearch")
        self.assertEqual(missing.exception.code, "package_selection_missing")

    def test_rejects_nonlocal_and_unknown_volume_status(self) -> None:
        package = self.create_package()
        with self.assertRaises(PackageSelectionError) as remote:
            self.broker(local=False).select(PackageSelectionSource.FILE_PICKER, package)
        self.assertEqual(remote.exception.code, "package_selection_nonlocal")

        with self.assertRaises(PackageSelectionError) as unknown:
            self.broker(local=None).select(PackageSelectionSource.FILE_PICKER, package)
        self.assertEqual(unknown.exception.code, "package_selection_locality_unknown")

    def test_default_probe_fails_closed_when_native_metadata_is_unavailable(self) -> None:
        result = macos_local_volume_probe(self.root)
        self.assertIn(result, (True, False, None))
        if result is None:
            broker = PackageSelectionBroker(
                selection_id_factory=lambda: "selection_0000000000000001",
                clock=self.clock,
            )
            with self.assertRaises(PackageSelectionError) as raised:
                broker.select(PackageSelectionSource.FILE_PICKER, self.create_package())
            self.assertEqual(raised.exception.code, "package_selection_locality_unknown")

    def test_native_volume_probe_accepts_boolean_metadata_only(self) -> None:
        key = "volume-is-local"

        class Number:
            def __init__(self, value: bool) -> None:
                self.value = value

            def boolValue(self) -> bool:
                return self.value

        class URL:
            def __init__(self, value: object) -> None:
                self.value = value

            def resourceValuesForKeys_error_(self, _keys, _error):
                return {key: self.value}, None

        for value, expected in ((Number(True), True), (Number(False), False), ("yes", None)):
            with self.subTest(value=value):
                foundation = types.SimpleNamespace(
                    NSURL=types.SimpleNamespace(fileURLWithPath_=lambda _path: URL(value)),
                    NSURLVolumeIsLocalKey=key,
                )
                with patch.dict(sys.modules, {"Foundation": foundation}):
                    self.assertIs(macos_local_volume_probe(self.root), expected)

    def test_overlong_path_is_rejected_before_file_access(self) -> None:
        candidate = Path("/") / ("a" * (MAX_NATIVE_PATH_BYTES + 1))
        with self.assertRaises(PackageSelectionError) as raised:
            self.broker().select(PackageSelectionSource.FILE_PICKER, candidate)
        self.assertEqual(raised.exception.code, "package_selection_path_too_long")

    def test_ttl_expiration_and_capacity_are_bounded(self) -> None:
        broker = self.broker(ttl=5.0, capacity=1)
        first = broker.select(PackageSelectionSource.FILE_PICKER, self.create_package("one.aresearch"))
        with self.assertRaises(PackageSelectionError) as capacity:
            broker.select(PackageSelectionSource.FILE_PICKER, self.create_package("two.aresearch"))
        self.assertEqual(capacity.exception.code, "package_selection_capacity")

        self.clock.value += 5.0
        with self.assertRaises(PackageSelectionError) as expired:
            broker.resolve(first.selection_id)
        self.assertEqual(expired.exception.code, "package_selection_expired")
        second = broker.select(PackageSelectionSource.FILE_PICKER, self.root / "two.aresearch")
        self.assertNotEqual(first.selection_id, second.selection_id)

    def test_replacing_selected_file_is_detected_and_revokes_selection(self) -> None:
        package = self.create_package(content=b"original")
        replacement = self.create_package("replacement.aresearch", b"changed!")
        broker = self.broker()
        snapshot = broker.select(PackageSelectionSource.DRAG_DROP, package)
        os.replace(replacement, package)

        with self.assertRaises(PackageSelectionError) as changed:
            broker.resolve(snapshot.selection_id)
        self.assertEqual(changed.exception.code, "package_selection_changed")
        with self.assertRaises(PackageSelectionError) as revoked:
            broker.resolve(snapshot.selection_id)
        self.assertEqual(revoked.exception.code, "package_selection_expired")

    def test_size_or_mtime_change_is_detected_even_without_path_change(self) -> None:
        package = self.create_package(content=b"original")
        broker = self.broker()
        snapshot = broker.select(PackageSelectionSource.FILE_PICKER, package)
        package.write_bytes(b"longer replacement")

        with self.assertRaises(PackageSelectionError) as changed:
            broker.resolve(snapshot.selection_id)
        self.assertEqual(changed.exception.code, "package_selection_changed")

    def test_revoke_is_idempotent(self) -> None:
        broker = self.broker()
        snapshot = broker.select(PackageSelectionSource.FILE_PICKER, self.create_package())
        broker.revoke(snapshot.selection_id)
        broker.revoke(snapshot.selection_id)
        broker.revoke("not-valid")
        with self.assertRaises(PackageSelectionError) as raised:
            broker.resolve(snapshot.selection_id)
        self.assertEqual(raised.exception.code, "package_selection_expired")

    def test_errors_are_path_free(self) -> None:
        private_path = self.root / "secret-title.txt"
        with self.assertRaises(PackageSelectionError) as raised:
            self.broker().select(PackageSelectionSource.FILE_PICKER, private_path)
        serialized = json.dumps(raised.exception.public_dict(), ensure_ascii=False)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn("secret-title", serialized)
        self.assertEqual(
            set(raised.exception.public_dict()),
            {"code", "message", "retryable"},
        )


if __name__ == "__main__":
    unittest.main()
