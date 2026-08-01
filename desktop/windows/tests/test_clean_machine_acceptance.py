from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


WINDOWS_ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RUNTIME_BUNDLE = load_module("runtime_bundle", WINDOWS_ROOT / "runtime_bundle.py")
ACCEPTANCE = load_module("windows_clean_machine_acceptance", WINDOWS_ROOT / "clean_machine_acceptance.py")


def create_fake_candidate(root: Path) -> None:
    (root / RUNTIME_BUNDLE.ENTRYPOINT_NAME).write_bytes(b"fake-executable")
    components: dict[str, str] = {}
    for index, name in enumerate(sorted(RUNTIME_BUNDLE.REQUIRED_COMPONENTS)):
        relative = f"_internal/{index:02d}-{name}.bin"
        path = root / "_internal" / f"{index:02d}-{name}.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode("utf-8"))
        components[name] = relative
    manifest = RUNTIME_BUNDLE.create_runtime_manifest(
        root,
        desktop_version="0.1.0-dev.1",
        python_version="3.12.10",
        components=components,
    )
    RUNTIME_BUNDLE.write_runtime_manifest(root, manifest)


class FakeCandidateRunner:
    def __init__(self, overrides: dict[str, object] | None = None) -> None:
        self.overrides = overrides or {}
        self.calls: list[tuple[Path, Path, dict[str, str]]] = []

    def run(self, executable, *, cwd, environment):
        values = dict(environment)
        self.calls.append((Path(executable), Path(cwd), values))
        report: dict[str, object] = {
            "bundled_python": True,
            "requires_source_checkout": False,
            "external_commands": [],
            "required_environment_variables": [],
            "first_run_entry": "import-evidence-package",
            "data_root": str(Path(values["LOCALAPPDATA"]) / "Auto Research"),
        }
        report.update(self.overrides)
        return report


class CleanMachineAcceptanceTests(unittest.TestCase):
    def test_fake_runtime_passes_without_path_or_development_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate"
            candidate.mkdir()
            create_fake_candidate(candidate)
            runner = FakeCandidateRunner()
            report = ACCEPTANCE.run_clean_machine_acceptance(
                candidate,
                runner=runner,
                sandbox_root=root / "sandbox",
            )
            self.assertTrue(report.used_empty_working_directory)
            self.assertTrue(report.used_clean_environment)
            _, cwd, environment = runner.calls[0]
            self.assertEqual(environment["PATH"], "")
            self.assertFalse(any(cwd.iterdir()))
            self.assertFalse(any(key.startswith("AUTO_RESEARCH_") for key in environment))

    def test_source_checkout_or_external_tool_report_is_rejected(self) -> None:
        for overrides in (
            {"requires_source_checkout": True},
            {"external_commands": ["python", "git"]},
            {"required_environment_variables": ["AUTO_RESEARCH_PROJECT_ROOT"]},
        ):
            with self.subTest(overrides=overrides), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                candidate = root / "candidate"
                candidate.mkdir()
                create_fake_candidate(candidate)
                with self.assertRaises(ACCEPTANCE.CleanMachineAcceptanceError):
                    ACCEPTANCE.run_clean_machine_acceptance(
                        candidate,
                        runner=FakeCandidateRunner(overrides),
                        sandbox_root=root / "sandbox",
                    )

    def test_wrong_first_run_entry_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate"
            candidate.mkdir()
            create_fake_candidate(candidate)
            with self.assertRaises(ACCEPTANCE.CleanMachineAcceptanceError):
                ACCEPTANCE.run_clean_machine_acceptance(
                    candidate,
                    runner=FakeCandidateRunner({"first_run_entry": "choose-source-checkout"}),
                    sandbox_root=root / "sandbox",
                )


if __name__ == "__main__":
    unittest.main()
