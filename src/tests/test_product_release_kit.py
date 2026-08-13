from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from auto_research.product_release_kit import (
    ReleaseKitError,
    build_macos_release_kit,
    build_macos_user_kit,
)


class ProductReleaseKitTests(unittest.TestCase):
    def test_builds_atomic_kit_with_independent_app_and_package(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-kit-test-") as temporary:
            root = Path(temporary)
            dmg = root / "Auto-Research-0.6.1-preview.1-macOS-arm64.dmg"
            package = root / "auto-research-internal-evidence-0.2.0-preview.1.aresearch"
            guide = root / "快速开始.md"
            dmg.write_bytes(b"dmg")
            package.write_bytes(b"package")
            guide.write_text("guide", encoding="utf-8")
            result = build_macos_release_kit(
                dmg_path=dmg,
                official_package_path=package,
                quickstart_path=guide,
                output_directory=root / "kit",
                release_name="Auto-Research-0.6.1-preview.1-internal-kit",
            )
            self.assertTrue((result.directory / "SHA256SUMS.txt").is_file())
            with zipfile.ZipFile(result.zip_path) as archive:
                names = set(archive.namelist())
            self.assertIn(
                "Auto-Research-0.6.1-preview.1-internal-kit/"
                "auto-research-internal-evidence-0.2.0-preview.1.aresearch",
                names,
            )
            self.assertIn(
                "Auto-Research-0.6.1-preview.1-internal-kit/"
                "Auto-Research-0.6.1-preview.1-macOS-arm64.dmg",
                names,
            )
            self.assertEqual(len(result.zip_sha256), 64)

    def test_refuses_overwrite_and_symlink_sources(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-kit-test-") as temporary:
            root = Path(temporary)
            sources = []
            for name, content in (("a.dmg", b"a"), ("b.aresearch", b"b"), ("c.md", b"c")):
                path = root / name
                path.write_bytes(content)
                sources.append(path)
            (root / "kit").mkdir()
            with self.assertRaisesRegex(ReleaseKitError, "拒绝覆盖"):
                build_macos_release_kit(
                    dmg_path=sources[0],
                    official_package_path=sources[1],
                    quickstart_path=sources[2],
                    output_directory=root / "kit",
                    release_name="kit",
                )

    def test_builds_explicit_user_kit_without_source_or_database_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="user-kit-test-") as temporary:
            root = Path(temporary)
            files = []
            for name in (
                "Auto-Research-0.8.0-preview.1-macOS-arm64.dmg",
                "official.aresearch",
                "example.aresearch",
                "Sample-Experiment.csv",
                "用户指南.md",
                "RELEASE_ACCEPTANCE.json",
            ):
                path = root / name
                path.write_bytes(name.encode("utf-8"))
                files.append(path)
            result = build_macos_user_kit(
                files=files,
                output_directory=root / "UserKit",
                release_name="Auto-Research-0.8.0-preview.1-build16-UserKit",
            )
            self.assertTrue((result.directory / "SHA256SUMS.txt").is_file())
            with zipfile.ZipFile(result.zip_path) as archive:
                names = archive.namelist()
            self.assertFalse(any(name.endswith((".bundle", ".sqlite", ".db")) for name in names))
            self.assertEqual(len(result.zip_sha256), 64)

    def test_user_kit_rejects_source_bundle_and_database(self) -> None:
        with tempfile.TemporaryDirectory(prefix="user-kit-test-") as temporary:
            root = Path(temporary)
            dmg = root / "app.dmg"
            package = root / "official.aresearch"
            forbidden = root / "complete-history.bundle"
            for path in (dmg, package, forbidden):
                path.write_bytes(b"fixture")
            with self.assertRaisesRegex(ReleaseKitError, "类型"):
                build_macos_user_kit(
                    files=(dmg, package, forbidden),
                    output_directory=root / "UserKit",
                    release_name="UserKit",
                )


if __name__ == "__main__":
    unittest.main()
