from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
import os
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DESKTOP_ROOT = PROJECT_ROOT / "desktop" / "macos"
SOURCE_ROOT = PROJECT_ROOT / "src"
for path in (DESKTOP_ROOT, SOURCE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ai_runtime_composition  # noqa: E402
from ai_runtime_composition import (  # noqa: E402
    create_mac_ai_runtime_services,
    disable_legacy_environment_credentials,
)
from secure_credentials import (  # noqa: E402
    DEEPSEEK_PROVIDER,
    OPENAI_PROVIDER,
    ProviderCredentialManager,
)


class MemoryBackend:
    storage_label = "memory"

    def __init__(self) -> None:
        self.value = None

    def exists(self):
        return self.value is not None

    def read(self):
        return self.value

    def write(self, value):
        self.value = value

    def delete(self):
        self.value = None


class RejectNetwork:
    def request(self, *_args, **_kwargs):
        raise AssertionError("network must not be used during composition")


class MacAIRuntimeCompositionTests(unittest.TestCase):
    def test_desktop_startup_discards_ambient_deepseek_key(self) -> None:
        previous = os.environ.get("DEEPSEEK_API_KEY")
        os.environ["DEEPSEEK_API_KEY"] = "sk-ambient-must-not-run"
        try:
            disable_legacy_environment_credentials()
            self.assertNotIn("DEEPSEEK_API_KEY", os.environ)
        finally:
            if previous is not None:
                os.environ["DEEPSEEK_API_KEY"] = previous
            else:
                os.environ.pop("DEEPSEEK_API_KEY", None)

    def test_one_graph_reuses_manager_and_has_no_business_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = ProviderCredentialManager(
                {
                    DEEPSEEK_PROVIDER: MemoryBackend(),
                    OPENAI_PROVIDER: MemoryBackend(),
                }
            )
            services = create_mac_ai_runtime_services(
                state_path=Path(temporary) / "state.json",
                attestation_key_path=Path(temporary) / "attestation.key",
                credential_manager=manager,
                verifier_session=RejectNetwork(),
                client_session=RejectNetwork(),
            )
            self.assertIs(services.credential_manager, manager)
            self.assertIs(services.legacy_deepseek_store.manager, manager)
            self.assertIs(services.execution_lock, manager.execution_lock)
            self.assertEqual(len(services.controller.route_contract()), 9)
            self.assertFalse(hasattr(services, "business_actions"))
            catalog = services.desktop_service.catalog()
            self.assertEqual(
                {item["provider_id"] for item in catalog["providers"]},
                {"deepseek", "openai"},
            )
            self.assertEqual(services.desktop_service.get()["provider_id"], "deepseek")
            self.assertFalse(
                services.desktop_service.credential_status("deepseek")["configured"]
            )

    def test_process_accessor_constructs_exactly_one_graph(self) -> None:
        sentinel = object()
        previous = ai_runtime_composition._SERVICES
        ai_runtime_composition._SERVICES = None
        try:
            with mock.patch.object(
                ai_runtime_composition,
                "create_mac_ai_runtime_services",
                return_value=sentinel,
            ) as factory:
                self.assertIs(ai_runtime_composition.mac_ai_runtime_services(), sentinel)
                self.assertIs(ai_runtime_composition.mac_ai_runtime_services(), sentinel)
                factory.assert_called_once_with()
        finally:
            ai_runtime_composition._SERVICES = previous

    def test_execution_lease_excludes_runtime_patch_and_credential_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = ProviderCredentialManager(
                {
                    DEEPSEEK_PROVIDER: MemoryBackend(),
                    OPENAI_PROVIDER: MemoryBackend(),
                }
            )
            services = create_mac_ai_runtime_services(
                state_path=Path(temporary) / "state.json",
                attestation_key_path=Path(temporary) / "attestation.key",
                credential_manager=manager,
            )
            finished = threading.Event()

            def mutate() -> None:
                services.desktop_service.patch(
                    {
                        "provider_id": "deepseek",
                        "task_models": {
                            "extraction": "deepseek-v4-pro",
                            "analysis": "deepseek-v4-pro",
                            "librarian_planning": "deepseek-v4-flash",
                            "librarian_synthesis": "deepseek-v4-pro",
                        },
                        "expected_revision": 0,
                    }
                )
                services.desktop_service.credential_save(
                    "deepseek", "sk-mutual-exclusion-test"
                )
                services.desktop_service.credential_delete("deepseek")
                finished.set()

            with services.execution_lease.acquire(object()):
                thread = threading.Thread(target=mutate)
                thread.start()
                time.sleep(0.02)
                self.assertFalse(finished.is_set())
            thread.join(1)
            self.assertTrue(finished.is_set())

    def test_launcher_smoke_and_production_inject_one_runtime_graph(self) -> None:
        launcher = (DESKTOP_ROOT / "launcher.py").read_text(encoding="utf-8")
        self.assertIn('"desktop_ai_providers": "/api/desktop/ai/providers"', launcher)
        self.assertIn('"desktop_ai_settings": "/api/desktop/ai/settings"', launcher)
        self.assertIn(
            '"desktop_ai_deepseek_credential": "/api/desktop/ai/credentials/deepseek"',
            launcher,
        )
        self.assertNotIn("/api/desktop/ai/providers/deepseek/test", launcher)
        self.assertIn("credential_store=ai_services.legacy_deepseek_store", launcher)
        self.assertIn("desktop_ai_api=MacDesktopAIAPI(ai_services.controller)", launcher)
        self.assertNotIn("default_deepseek_credential_store", launcher)
        self.assertNotIn("read_for_runtime()", launcher)
        self.assertIn("disable_legacy_environment_credentials()", launcher)


if __name__ == "__main__":
    unittest.main()
