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
from auto_research.ai.business_actions import BUSINESS_ACTION_SCOPES  # noqa: E402
from auto_research.ai.harness_contract import (  # noqa: E402
    CORDIS_RUNTIME_PROTOCOL_PIN,
    HARNESS_SDK_PROTOCOL_PIN,
    HarnessDependencyMetadata,
    HarnessDependencySet,
)
from auto_research.ai.harness_official_sdk import safe_composition_metadata  # noqa: E402
from auto_research.evidence.db import EvidenceDB  # noqa: E402
from desktop_product_services import create_desktop_product_services  # noqa: E402
from secure_credentials import (  # noqa: E402
    CUSTOM_PROVIDER,
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


class FakeHarnessRuntime:
    def dependency_metadata(self):
        return HarnessDependencySet(
            HarnessDependencyMetadata.from_pin(HARNESS_SDK_PROTOCOL_PIN),
            HarnessDependencyMetadata.from_pin(CORDIS_RUNTIME_PROTOCOL_PIN),
            "2.12.0",
        )

    def composition_metadata(self):
        return safe_composition_metadata()

    def execute(self, **_kwargs):
        raise AssertionError("Harness must not execute during composition")


class PublicResolver:
    def resolve(self, _hostname):
        return ("93.184.216.34",)


class MacAIRuntimeCompositionTests(unittest.TestCase):
    def test_custom_provider_config_is_atomic_path_free_and_reopens(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = ProviderCredentialManager({
                DEEPSEEK_PROVIDER: MemoryBackend(),
                OPENAI_PROVIDER: MemoryBackend(),
                CUSTOM_PROVIDER: MemoryBackend(),
            })
            first = create_mac_ai_runtime_services(
                state_path=root / "state.json",
                attestation_key_path=root / "attestation.key",
                custom_provider_path=root / "custom.json",
                custom_host_resolver=PublicResolver(),
                credential_manager=manager,
            )
            public = first.desktop_service.custom_provider_save({
                "display_name": "lab gateway",
                "chat_endpoint": "https://ai.example.com/v1/chat/completions",
                "task_models": {
                    "extraction": "model-a", "analysis": "model-a",
                    "librarian_planning": "model-b", "librarian_synthesis": "model-b",
                },
                "expected_revision": 0,
            })
            self.assertEqual(public["revision"], 1)
            second = create_mac_ai_runtime_services(
                state_path=root / "state-2.json",
                attestation_key_path=root / "attestation-2.key",
                custom_provider_path=root / "custom.json",
                custom_host_resolver=PublicResolver(),
                credential_manager=manager,
            )
            reopened = second.desktop_service.custom_provider_get()
            self.assertEqual(reopened["revision"], 1)
            self.assertNotIn("endpoint", repr(reopened).casefold())
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

    def test_one_graph_reuses_manager_and_installs_all_business_ports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = ProviderCredentialManager(
                {
                    DEEPSEEK_PROVIDER: MemoryBackend(),
                    OPENAI_PROVIDER: MemoryBackend(),
                }
            )
            database = EvidenceDB(root / "evidence.sqlite")
            database.init()
            product = create_desktop_product_services(
                data_root=root / "Application Support",
                current_app_version="0.8.0-preview",
            )
            session_id = "desktop-session-" + "s" * 32
            services = create_mac_ai_runtime_services(
                state_path=root / "state.json",
                attestation_key_path=root / "attestation.key",
                credential_manager=manager,
                verifier_session=RejectNetwork(),
                client_session=RejectNetwork(),
                database=database,
                personal_import_service=product.personal_import_service,
                desktop_session_id=session_id,
                federated_search_session=product.federated_search_service.session,
                harness_runtime=FakeHarnessRuntime(),
            )
            self.assertIs(services.credential_manager, manager)
            self.assertIs(services.legacy_deepseek_store.manager, manager)
            self.assertIs(services.execution_lock, manager.execution_lock)
            self.assertEqual(len(services.controller.route_contract()), 16)
            self.assertIs(services.database, database)
            self.assertIs(
                services.personal_import_service,
                product.personal_import_service,
            )
            self.assertEqual(services.desktop_session_id, session_id)
            self.assertIsNotNone(services.business_actions)
            self.assertIs(services.controller._business, services.business_actions)
            self.assertIs(services.controller._prepared, services.prepared_actions)
            self.assertIs(
                services.personal_suggestion_ports.assembler._service,
                product.personal_import_service,
            )
            self.assertIs(
                services.federated_search_session,
                product.federated_search_service.session,
            )
            self.assertIsNotNone(services.harness_ports)
            self.assertEqual(
                services.selected_evidence_chat_ports.assembler._scope,
                "selected_evidence_chat",
            )
            self.assertEqual(services.librarian_ports.assembler._scope, "librarian")
            self.assertTrue(services.selected_evidence_chat_ports.executor.requires_harness_budget)
            self.assertTrue(services.librarian_ports.executor.requires_harness_budget)
            self.assertIs(
                services.literature_extraction_ports.assembler._store,
                services.literature_jobs,
            )
            self.assertIsNotNone(services.literature_checkpoints)
            self.assertTrue(services.literature_jobs._snapshots.persistent)
            self.assertIs(
                services.literature_extraction_ports.assembler._checkpoint_runtime,
                services.literature_checkpoints.checkpoint_runtime,
            )
            self.assertIs(
                services.literature_extraction_ports.executor._checkpoint_runtime,
                services.literature_checkpoints.checkpoint_runtime,
            )
            self.assertIs(
                services.literature_recovery._runtime,
                services.literature_checkpoints.checkpoint_runtime,
            )
            self.assertIs(
                services.literature_recovery._jobs,
                services.literature_jobs,
            )
            self.assertIs(
                services.literature_recovery._projector,
                services.literature_extraction_ports.projector,
            )
            self.assertEqual(
                services.literature_recovery_report,
                {
                    "schema_version": "literature-extraction-recovery-sweep-v1",
                    "scanned": 0,
                    "recovered": 0,
                    "already_completed": 0,
                    "skipped_or_blocked": 0,
                    "issues": [],
                },
            )
            self.assertTrue((root / "literature-tasks-v1").is_dir())
            self.assertFalse(hasattr(services, "literature_finalizer"))
            self.assertFalse(hasattr(services.literature_extraction_ports, "finalizer"))
            self.assertEqual(
                services.literature_extraction_ports.assembler._session_id,
                session_id,
            )
            self.assertEqual(
                set(services.business_actions._assemblers),
                BUSINESS_ACTION_SCOPES,
            )
            self.assertEqual(
                set(services.snapshot_authority._authorities),
                {
                    "personal_table",
                    "harness_literature",
                    "literature_extraction_stage",
                },
            )
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
        database = object()
        personal = object()
        federated = object()
        session_id = "desktop-session-" + "x" * 32
        sentinel = type(
            "Sentinel",
            (),
            {
                "database": database,
                "personal_import_service": personal,
                "desktop_session_id": session_id,
                "federated_search_session": federated,
            },
        )()
        previous = ai_runtime_composition._SERVICES
        ai_runtime_composition._SERVICES = None
        try:
            with mock.patch.object(
                ai_runtime_composition,
                "create_mac_ai_runtime_services",
                return_value=sentinel,
            ) as factory:
                kwargs = {
                    "database": database,
                    "personal_import_service": personal,
                    "desktop_session_id": session_id,
                    "federated_search_session": federated,
                    "harness_cordis_path": Path("/tmp/harness.cordis.yml"),
                }
                self.assertIs(ai_runtime_composition.mac_ai_runtime_services(**kwargs), sentinel)
                self.assertIs(ai_runtime_composition.mac_ai_runtime_services(**kwargs), sentinel)
                factory.assert_called_once_with(**kwargs)
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

    def test_fusion_product_launcher_uses_only_the_reviewed_ai_runtime(self) -> None:
        launcher = (DESKTOP_ROOT / "launcher.py").read_text(encoding="utf-8")
        production = launcher.split("def _run_desktop", 1)[1]
        self.assertNotIn("create_mac_ai_runtime_services", production)
        self.assertIn("mac_ai_runtime_services(", production)
        self.assertIn("disable_legacy_environment_credentials()", production)
        self.assertIn("credential_store=ai_services.legacy_deepseek_store", production)
        self.assertIn("desktop_ai_api=MacDesktopAIAPI(ai_services.controller)", production)
        self.assertIn(
            "personal_import_service=product_services.personal_import_service",
            production,
        )
        self.assertIn(
            "federated_search_session=product_services.federated_search_service.session",
            production,
        )
        self.assertIn("auto-research-harness.runtime.cordis.yml", production)
        self.assertNotIn("librarian_business_ports(", production)
        self.assertNotIn("selected_evidence_chat_business_ports(", production)
        self.assertNotIn("read_for_runtime()", production)
        self.assertIn('experience_mode="fusion-product"', production)
        self.assertNotIn('os.environ["OPENAI_API_KEY"]', production)
        self.assertNotIn('os.environ["DEEPSEEK_API_KEY"]', production)


if __name__ == "__main__":
    unittest.main()
