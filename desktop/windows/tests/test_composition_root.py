from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path, PureWindowsPath


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT))
try:
    import composition_root as MODULE
    import os_compatibility as COMPATIBILITY
    import package_input as INPUT
finally:
    sys.path.pop(0)


class FakePathRuntime:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.state_directory = root / "State"
        self.official_data_root = root / "Repositories" / "Official"
        self.private_data_root = root / "Repositories" / "Private"
        self.mutex_identity = r"C:\Users\Researcher\AppData\Local\Auto Research"
        self.prepare_count = 0

    def prepare_directories(self):
        self.prepare_count += 1
        for path in (
            self.root,
            self.state_directory,
            self.official_data_root,
            self.private_data_root,
        ):
            path.mkdir(parents=True, exist_ok=True)


class FakeCredentialBackend:
    def __init__(self) -> None:
        self.values = {}

    def read(self, target):
        return self.values.get(target)

    def write(self, target, secret):
        self.values[target] = bytes(secret)

    def delete(self, target):
        self.values.pop(target, None)


class FakeProbe:
    def inspect_regular_local_file(self, raw_path):
        return INPUT.LocalFileIdentity(str(PureWindowsPath(raw_path)), 10, 1, 2, 3)


class FakePicker:
    contract_version = 1

    def __init__(self) -> None:
        self.selected = [r"C:\Users\Researcher\Downloads\evidence.aresearch"]

    def choose_files(self, **_kwargs):
        return self.selected


class FakeGuard:
    def __init__(self, identity, events) -> None:
        self.identity = identity
        self.events = events

    def acquire(self):
        self.events.append("guard-acquire")
        return self

    def close(self):
        self.events.append("guard-close")


class FakeServer:
    def __init__(self, events) -> None:
        self.server_address = ("127.0.0.1", 49321)
        self.events = events
        self.closed = threading.Event()

    def serve_forever(self):
        self.events.append("server-run")
        self.closed.wait(timeout=2)

    def shutdown(self):
        self.events.append("server-shutdown")
        self.closed.set()

    def server_close(self):
        self.events.append("server-close")


class FakeSharedBridge:
    contract_version = 1

    def __init__(self, events) -> None:
        self.events = events
        self.services = None
        self.calls = []

    def build_server(self, **kwargs):
        self.services = kwargs.pop("services")
        self.calls.append(kwargs)
        return FakeServer(self.events)


class FakeReleaseContract:
    windows_version = "0.8.0-internal.1"

    def platform_version(self, platform):
        if platform != "windows":
            raise KeyError(platform)
        return {
            "desktop_version": self.windows_version,
            "installer_ready": False,
        }


class FakeWindow:
    def __init__(self, events) -> None:
        self.events = events

    def show(self, **kwargs):
        self.events.append("window-show")
        self.url = kwargs["url"]


class FakeOfficialApi:
    def trusted_public_keys(self, *, channel):
        return {"fake-key": b"key"}

    def import_official_evidence_package(self, package_path, **kwargs):
        raise AssertionError("package import is not part of shell startup")

    def open_active_official_repository(self, **kwargs):
        error = RuntimeError("no package")
        error.code = "active_package_missing"
        raise error


class FakeLibrarianRuntime:
    def run(self, question, **kwargs):
        return {
            "answer": question,
            "report": {"review_map": []},
            "results": [],
            "response_format": "reasoning-presentation-v2",
            "intent": {"id": "research_question"},
            "retrieval_policy": "focused",
            "research_state": {"conversation_id": kwargs.get("conversation_id")},
            "state_token": "token",
            "suggested_actions": [],
            "review_map": [],
        }


def compatibility():
    return COMPATIBILITY.WindowsCompatibility(
        product_name="Windows 11",
        build=26100,
        architecture="x64",
        support_level="primary",
        stable_release_supported=True,
        webview2_compatible=True,
        warning="",
    )


class CompositionRootTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.events = []
        self.paths = FakePathRuntime(Path(self.temporary.name) / "LocalAppData" / "Auto Research")
        self.shared = FakeSharedBridge(self.events)
        self.window = FakeWindow(self.events)
        self.backend = FakeCredentialBackend()
        self.root = MODULE.WindowsCompositionRoot(
            path_runtime=self.paths,
            current_app_version="0.8.0-internal.1",
            release_contract=FakeReleaseContract(),
            shared_http_bridge=self.shared,
            package_window_bridge=FakePicker(),
            official_api=FakeOfficialApi(),
            librarian_runtime=FakeLibrarianRuntime(),
            credential_backend=self.backend,
            package_probe=FakeProbe(),
            native_path_factory=PureWindowsPath,
            window=self.window,
            guard_factory=lambda identity: FakeGuard(identity, self.events),
            compatibility_detector=compatibility,
        )

    def test_composition_connects_existing_modules_to_shared_bridge_services(self) -> None:
        composition = self.root.compose()
        self.assertEqual(composition.compatibility.product_name, "Windows 11")
        selected = composition.services.package_input.choose_package()
        self.assertEqual(selected.source, "file-picker")
        credential_status = composition.services.deepseek_credentials.save("sk-user-owned")
        self.assertTrue(credential_status["configured"])
        self.assertNotIn("sk-user-owned", str(credential_status))
        self.assertEqual(
            composition.services.deepseek_credentials.resolve_for_runtime(), "sk-user-owned"
        )
        self.assertEqual(len(composition.history_key_provider.get_or_create_key()), 32)
        self.assertFalse(composition.search_service.is_ready)
        self.assertFalse(composition.search_service.private_ready)
        self.assertFalse(composition.search_service.official_ready)
        self.assertIs(
            composition.services.package_import.service,
            composition.package_import_service,
        )
        self.assertIs(
            composition.services.evidence_search.service,
            composition.search_service,
        )
        self.assertIs(
            composition.services.personal_import.service,
            composition.personal_import_service,
        )
        self.assertEqual(
            composition.services.settings.get()["schema_version"],
            "desktop-settings-v1",
        )
        self.assertEqual(
            type(composition.personal_import_service._suggestion_model).__name__,
            "WindowsDeepSeekPersonalSuggestionModel",
        )
        librarian = composition.services.librarian.chat(
            "research",
            conversation_id="conversation-1",
        )
        self.assertEqual(librarian["retrieval_policy"], "focused")
        readiness = composition.services.readiness.status().public_dict()
        self.assertFalse(readiness["private_ready"])
        self.assertFalse(readiness["federated_ready"])
        self.assertTrue(readiness["librarian_ready"])
        self.assertTrue(
            callable(composition.native_desktop_bridge.select_personal_data_file)
        )
        self.assertTrue(
            callable(
                composition.native_desktop_bridge.select_package_export_destination
            )
        )
        with self.assertRaises(Exception) as raised:
            composition.services.package_center.status()
        self.assertEqual(
            getattr(raised.exception, "code", None),
            "package_center_unavailable",
        )

    def test_launch_injects_services_into_real_loopback_lifecycle_and_cleans_up(self) -> None:
        report = self.root.launch()
        self.assertEqual(report.port, 49321)
        self.assertIsNotNone(self.shared.services)
        self.assertEqual(self.shared.calls[0]["host"], "127.0.0.1")
        self.assertEqual(self.shared.calls[0]["port"], 0)
        self.assertEqual(self.shared.calls[0]["first_run_entry"], "import-evidence-package")
        self.assertEqual(
            self.shared.calls[0]["release"]["version"],
            "0.8.0-internal.1",
        )
        self.assertGreaterEqual(len(self.shared.calls[0]["bootstrap_token"]), 32)
        self.assertIn("window-show", self.events)
        self.assertEqual(self.events[-1], "guard-close")
        self.assertEqual(self.paths.prepare_count, 1)

    def test_unfrozen_shared_bridge_fails_before_credentials_server_or_window(self) -> None:
        root = MODULE.WindowsCompositionRoot(
            path_runtime=self.paths,
            current_app_version="0.8.0-internal.1",
            release_contract=FakeReleaseContract(),
            credential_backend=self.backend,
            package_probe=FakeProbe(),
            window=self.window,
            guard_factory=lambda identity: FakeGuard(identity, self.events),
            compatibility_detector=compatibility,
        )
        with self.assertRaises(MODULE.WindowsCompositionError):
            root.compose()
        self.assertEqual(self.events, [])
        self.assertEqual(self.backend.values, {})

    def test_unfrozen_package_picker_fails_before_server_or_window(self) -> None:
        root = MODULE.WindowsCompositionRoot(
            path_runtime=self.paths,
            current_app_version="0.8.0-internal.1",
            release_contract=FakeReleaseContract(),
            shared_http_bridge=self.shared,
            credential_backend=self.backend,
            package_probe=FakeProbe(),
            window=self.window,
            guard_factory=lambda identity: FakeGuard(identity, self.events),
            compatibility_detector=compatibility,
        )
        with self.assertRaises(MODULE.WindowsCompositionError):
            root.compose()
        self.assertEqual(self.events, [])
        self.assertEqual(self.backend.values, {})

    def test_non_internal_version_is_rejected(self) -> None:
        with self.assertRaises(MODULE.WindowsCompositionError):
            MODULE.WindowsCompositionRoot(
                path_runtime=self.paths,
                current_app_version="0.4.0",
                release_contract=FakeReleaseContract(),
            )

    def test_release_contract_mismatch_fails_before_composition(self) -> None:
        with self.assertRaises(MODULE.WindowsCompositionError):
            MODULE.WindowsCompositionRoot(
                path_runtime=self.paths,
                current_app_version="0.7.0-internal.1",
                release_contract=FakeReleaseContract(),
            )

    def test_machine_readable_dependency_manifest_is_internal_and_complete(self) -> None:
        manifest_path = WINDOWS_ROOT / "production-dependencies.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["release_status"], "internal-development")
        self.assertIn("pending", manifest["dependency_closure_status"])
        self.assertFalse(manifest["installer_ready"])
        self.assertIn("composition_root", manifest["windows_modules"])
        self.assertIn("launcher", manifest["windows_modules"])
        self.assertIn(
            "auto_research.evidence.federated_search",
            manifest["shared_runtime_modules"],
        )
        self.assertIn("auto_research.product.package_center", manifest["shared_runtime_modules"])
        self.assertIn("package_center.js", manifest["shared_web_assets"])
        self.assertIn("ai_consent.js", manifest["shared_web_assets"])
        self.assertIn("workbench.css", manifest["shared_web_assets"])
        self.assertIn("workbench.js", manifest["shared_web_assets"])
        self.assertEqual(
            set(manifest["shared_web_assets"]),
            {
                "index.html",
                "app.css",
                "ai_consent.js",
                "app.js",
                "desktop_product.js",
                "package_center.js",
                "librarian_brief.js",
                "workbench.css",
                "workbench.js",
            },
        )
        self.assertNotIn(
            "auto_research.product.internal_preview_builder",
            manifest["shared_runtime_modules"],
        )
        for module in manifest["windows_modules"]:
            self.assertTrue((WINDOWS_ROOT / f"{module}.py").is_file(), module)
        for module in manifest["build_gate_modules"]:
            self.assertTrue((WINDOWS_ROOT / f"{module}.py").is_file(), module)
        self.assertIn("pending", manifest["required_injections"]["package_center_runtime"])


if __name__ == "__main__":
    unittest.main()
