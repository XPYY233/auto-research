from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from package_export_destination_broker import (
    PackageExportDestinationBroker,
    PackageExportDestinationError,
)


class PackageExportDestinationBrokerTests(unittest.TestCase):
    def test_renderer_receives_only_opaque_token_and_resolve_is_one_time(self) -> None:
        with tempfile.TemporaryDirectory(prefix="package-destination-test-") as temporary:
            root = Path(temporary)
            broker = PackageExportDestinationBroker(
                local_volume_probe=lambda _path: True,
                token_factory=lambda: "destination_1234567890",
            )
            snapshot = broker.select(root / "group-data.aresearch")
            public = snapshot.public_dict()
            self.assertNotIn(str(root), str(public))
            self.assertEqual(public["destination_token"], "destination_1234567890")
            resolved = broker.resolve(snapshot.destination_token)
            self.assertEqual(resolved.path, root.resolve() / "group-data.aresearch")
            with self.assertRaisesRegex(PackageExportDestinationError, "失效"):
                broker.resolve(snapshot.destination_token)

    def test_existing_target_symlink_and_nonlocal_parent_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="package-destination-test-") as temporary:
            root = Path(temporary)
            existing = root / "exists.aresearch"
            existing.write_bytes(b"x")
            local = PackageExportDestinationBroker(local_volume_probe=lambda _path: True)
            with self.assertRaisesRegex(PackageExportDestinationError, "已存在"):
                local.select(existing)
            nonlocal_broker = PackageExportDestinationBroker(local_volume_probe=lambda _path: False)
            with self.assertRaisesRegex(PackageExportDestinationError, "本机磁盘"):
                nonlocal_broker.select(root / "new.aresearch")

    def test_parent_replacement_and_target_creation_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="package-destination-test-") as temporary:
            root = Path(temporary)
            broker = PackageExportDestinationBroker(
                local_volume_probe=lambda _path: True,
                token_factory=lambda: "destination_abcdefghij",
            )
            target = root / "new.aresearch"
            snapshot = broker.select(target)
            target.write_bytes(b"race")
            with self.assertRaisesRegex(PackageExportDestinationError, "已存在"):
                broker.resolve(snapshot.destination_token)


if __name__ == "__main__":
    unittest.main()
