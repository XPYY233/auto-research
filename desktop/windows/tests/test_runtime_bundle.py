from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "runtime_bundle.py"
SPEC = importlib.util.spec_from_file_location("windows_runtime_bundle", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def create_fake_candidate(root: Path) -> dict[str, str]:
    (root / MODULE.ENTRYPOINT_NAME).write_bytes(b"fake-windows-executable")
    components: dict[str, str] = {}
    for index, name in enumerate(sorted(MODULE.REQUIRED_COMPONENTS)):
        relative = f"_internal/{index:02d}-{name}.bin"
        path = root / "_internal" / f"{index:02d}-{name}.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"fake:{name}".encode("utf-8"))
        components[name] = relative
    return components


class RuntimeBundleTests(unittest.TestCase):
    def test_complete_fake_bundle_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = MODULE.create_runtime_manifest(
                root,
                desktop_version="0.1.0-dev.1",
                python_version="3.12.10",
                components=create_fake_candidate(root),
            )
            MODULE.write_runtime_manifest(root, manifest)
            report = MODULE.verify_runtime_bundle(root)
            self.assertEqual(report.component_count, len(MODULE.REQUIRED_COMPONENTS))
            self.assertEqual(report.python_version, "3.12.10")

    def test_modified_component_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            components = create_fake_candidate(root)
            manifest = MODULE.create_runtime_manifest(
                root,
                desktop_version="0.1.0-dev.1",
                python_version="3.12.10",
                components=components,
            )
            MODULE.write_runtime_manifest(root, manifest)
            (root / components["cryptography"]).write_bytes(b"tampered")
            with self.assertRaises(MODULE.RuntimeBundleError):
                MODULE.verify_runtime_bundle(root)

    def test_missing_required_component_is_rejected_before_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            components = create_fake_candidate(root)
            components.pop("pymupdf")
            with self.assertRaises(MODULE.RuntimeBundleError):
                MODULE.create_runtime_manifest(
                    root,
                    desktop_version="0.1.0-dev.1",
                    python_version="3.12.10",
                    components=components,
                )

    def test_external_commands_or_environment_requirements_are_rejected(self) -> None:
        for field, value in (
            ("external_commands", ["git"]),
            ("required_environment_variables", ["AUTO_RESEARCH_PROJECT_ROOT"]),
            ("requires_source_checkout", True),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                manifest = MODULE.create_runtime_manifest(
                    root,
                    desktop_version="0.1.0-dev.1",
                    python_version="3.12.10",
                    components=create_fake_candidate(root),
                )
                manifest[field] = value
                MODULE.write_runtime_manifest(root, manifest)
                with self.assertRaises(MODULE.RuntimeBundleError):
                    MODULE.verify_runtime_bundle(root)


if __name__ == "__main__":
    unittest.main()
