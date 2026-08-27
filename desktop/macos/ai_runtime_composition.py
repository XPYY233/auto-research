from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from auto_research.ai.business_actions import BusinessPreparedActionRegistry
from auto_research.ai.custom_provider import CustomProviderService
from auto_research.ai.capability_verifier import OpenAICompatibleCapabilityVerifier
from auto_research.ai.consent import AIConsentService
from auto_research.ai.desktop_controller import DesktopAIController
from auto_research.ai.harness_official_sdk import OfficialDeepSeekHarnessRuntime
from auto_research.ai.harness_runtime import DeepSeekHarnessRuntime
from auto_research.ai.harness_contract import HarnessError, verify_cordis_composition
from auto_research.ai.prepared_actions import (
    CompositeContentSnapshotAuthority,
    PreparedActionService,
)
from auto_research.ai.runtime_factory import RuntimeAIClientFactory
from auto_research.evidence.db import EvidenceDB
from auto_research.evidence.federated_search_session import (
    FederatedSearchSessionProtocol,
)
from auto_research.evidence.harness_business_action import (
    HarnessBusinessPorts,
    HarnessScopeBusinessPorts,
    harness_business_ports,
)
from auto_research.evidence.harness_workspace_source import HarnessWorkspaceSource
from auto_research.evidence.literature_extraction_business_action import (
    LiteratureExtractionBusinessPorts,
    literature_extraction_business_ports,
)
from auto_research.evidence.literature_extraction_job import (
    LiteratureExtractionJobStore,
    LiteraturePDFSnapshotAuthority,
)
from auto_research.evidence.literature_extraction_finalizer import (
    AtomicEvidenceDBFinalizer,
)
from auto_research.personal.ai_business_action import (
    PersonalSuggestionBusinessPorts,
    personal_suggestion_business_ports,
)
from auto_research.personal.import_service import PersonalImportService
from auto_research.settings.ai_desktop_service import AIDesktopService
from auto_research.settings.ai_runtime_state import AIRuntimeStateService
from auto_research.settings.ai_readiness import AIReadinessService

from ai_runtime_security import (
    DEFAULT_AI_ATTESTATION_KEY_PATH,
    DEFAULT_AI_RUNTIME_STATE_PATH,
    MacAIExecutionLeaseAuthority,
    MacAtomicAIRuntimeStateStore,
    MacVerificationAttestationSigner,
)
from secure_credentials import (
    DeepSeekCredentialStore,
    ProviderCredentialManager,
    default_provider_credential_manager,
)
from desktop_settings_store import MacAtomicDesktopSettingsStore
from literature_checkpoint_composition import (
    DEFAULT_LITERATURE_PRIVATE_ROOT,
    MacLiteratureCheckpointServices,
    create_mac_literature_checkpoint_services,
)


DEFAULT_CUSTOM_PROVIDER_STATE_PATH = (
    Path.home() / "Library" / "Application Support" / "Auto Research" / "State" /
    "custom-ai-provider-v1.json"
)


class _MacAtomicCustomProviderStore:
    def __init__(self, path: Path | str) -> None:
        self._store = MacAtomicDesktopSettingsStore(path)

    def read(self):
        value = self._store.read()
        if value is None:
            return None
        if not isinstance(value, dict) or set(value) != {"revision", "record"}:
            raise OSError("custom provider state is invalid")
        return value["record"]

    def revision(self) -> int:
        value = self._store.read()
        if value is None:
            return 0
        if not isinstance(value, dict) or set(value) != {"revision", "record"}:
            raise OSError("custom provider state is invalid")
        revision = value["revision"]
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise OSError("custom provider state is invalid")
        return revision

    def compare_and_swap(self, *, expected_revision: int, value):
        return self._store.compare_and_swap(
            expected_revision=expected_revision,
            value={"revision": expected_revision + 1, "record": value},
        )


class _HarnessReadiness:
    def __init__(self, runtime: DeepSeekHarnessRuntime | None) -> None:
        self._runtime = runtime

    def readiness(self):
        if self._runtime is None:
            return {"state": "unavailable", "reason_code": "harness_runtime_unavailable", "next_action": "repair_harness_runtime"}
        try:
            dependencies = self._runtime.dependency_metadata()
            dependencies.verify_production_protocols()
            verify_cordis_composition(self._runtime.composition_metadata())
        except HarnessError as exc:
            return {"state": "unavailable", "reason_code": exc.code, "next_action": "repair_harness_runtime"}
        except Exception:
            return {"state": "unavailable", "reason_code": "harness_runtime_unavailable", "next_action": "repair_harness_runtime"}
        return {"state": "ready", "reason_code": "harness_runtime_ready", "next_action": "none"}


@dataclass(frozen=True)
class MacAIRuntimeServices:
    execution_lock: threading.RLock
    credential_manager: ProviderCredentialManager
    legacy_deepseek_store: DeepSeekCredentialStore
    state_store: MacAtomicAIRuntimeStateStore
    runtime_state: AIRuntimeStateService
    desktop_service: AIDesktopService
    consent_service: AIConsentService
    prepared_actions: PreparedActionService
    controller: DesktopAIController
    client_factory: RuntimeAIClientFactory
    execution_lease: MacAIExecutionLeaseAuthority
    custom_provider: CustomProviderService
    readiness: AIReadinessService
    database: EvidenceDB | None = None
    personal_import_service: PersonalImportService | None = None
    desktop_session_id: str | None = None
    federated_search_session: FederatedSearchSessionProtocol | None = None
    harness_runtime: DeepSeekHarnessRuntime | None = None
    snapshot_authority: CompositeContentSnapshotAuthority | None = None
    business_actions: BusinessPreparedActionRegistry | None = None
    personal_suggestion_ports: PersonalSuggestionBusinessPorts | None = None
    harness_ports: HarnessBusinessPorts | None = None
    selected_evidence_chat_ports: HarnessScopeBusinessPorts | None = None
    librarian_ports: HarnessScopeBusinessPorts | None = None
    literature_extraction_ports: LiteratureExtractionBusinessPorts | None = None
    literature_jobs: LiteratureExtractionJobStore | None = None
    literature_checkpoints: MacLiteratureCheckpointServices | None = None


_SERVICES: MacAIRuntimeServices | None = None
_SERVICES_LOCK = threading.Lock()


def disable_legacy_environment_credentials() -> None:
    """Prevent desktop product code from treating maintainer env as BYOK."""

    os.environ.pop("DEEPSEEK_API_KEY", None)


def create_mac_ai_runtime_services(
    *,
    state_path: Path | str = DEFAULT_AI_RUNTIME_STATE_PATH,
    attestation_key_path: Path | str = DEFAULT_AI_ATTESTATION_KEY_PATH,
    custom_provider_path: Path | str | None = None,
    custom_host_resolver=None,
    credential_manager: ProviderCredentialManager | None = None,
    verifier_session=None,
    client_session=None,
    database: EvidenceDB | None = None,
    personal_import_service: PersonalImportService | None = None,
    desktop_session_id: str | None = None,
    federated_search_session: FederatedSearchSessionProtocol | None = None,
    harness_runtime: DeepSeekHarnessRuntime | None = None,
    harness_cordis_path: Path | str | None = None,
    literature_private_root: Path | str | None = None,
) -> MacAIRuntimeServices:
    business_inputs = (
        database,
        personal_import_service,
        desktop_session_id,
        federated_search_session,
    )
    if any(value is not None for value in business_inputs) and not all(
        value is not None for value in business_inputs
    ):
        raise ValueError("mac AI business composition inputs must be complete")
    if all(value is not None for value in business_inputs) and (
        harness_runtime is None and harness_cordis_path is None
    ):
        raise ValueError("mac AI Harness runtime input is required")
    if desktop_session_id is not None and (
        not isinstance(desktop_session_id, str)
        or not desktop_session_id
        or len(desktop_session_id) > 256
    ):
        raise ValueError("desktop AI session is invalid")
    execution_lock = (
        credential_manager.execution_lock
        if credential_manager is not None
        else threading.RLock()
    )
    manager = credential_manager or default_provider_credential_manager(
        execution_lock=execution_lock
    )
    custom_state_path = (
        Path(custom_provider_path)
        if custom_provider_path is not None
        else Path(state_path).with_name("custom-ai-provider-v1.json")
    )
    custom_provider = CustomProviderService(
        _MacAtomicCustomProviderStore(custom_state_path), resolver=custom_host_resolver
    )
    state_store = MacAtomicAIRuntimeStateStore(
        state_path,
        execution_lock=execution_lock,
    )
    signer = MacVerificationAttestationSigner(attestation_key_path)
    verifier = OpenAICompatibleCapabilityVerifier(
        manager,
        session=verifier_session,
    )
    runtime_state = AIRuntimeStateService(
        store=state_store,
        credential_states=manager,
        verifier=verifier,
        signer=signer,
    )
    effective_harness_runtime = harness_runtime
    if database is not None and personal_import_service is not None:
        effective_harness_runtime = effective_harness_runtime or OfficialDeepSeekHarnessRuntime(
            cordis_path=Path(str(harness_cordis_path))
        )
    readiness = AIReadinessService(runtime_state, _HarnessReadiness(effective_harness_runtime))
    desktop_service = AIDesktopService(
        runtime_state=runtime_state,
        credential_manager=manager,
        readiness=readiness,
        custom_provider=custom_provider,
    )
    consent_service = AIConsentService()
    prepared_actions = PreparedActionService(
        runtime_state=runtime_state,
        consents=consent_service,
    )
    execution_lease = MacAIExecutionLeaseAuthority(execution_lock)
    client_factory = RuntimeAIClientFactory(
        runtime_state=runtime_state,
        credential_resolver=manager,
        execution_lease_authority=execution_lease,
        session=client_session,
    )
    personal_ports = None
    selected_ports = None
    librarian_ports = None
    literature_ports = None
    literature_jobs = None
    literature_checkpoints = None
    snapshots = None
    business_actions = None
    harness_ports = None
    if database is not None and personal_import_service is not None:
        personal_ports = personal_suggestion_business_ports(personal_import_service)
        harness_ports = harness_business_ports(
            session=federated_search_session,
            runtime=effective_harness_runtime,
            workspace=HarnessWorkspaceSource(database),
        )
        selected_ports = harness_ports.selected_evidence_chat
        librarian_ports = harness_ports.librarian
        checkpoint_root = (
            Path(literature_private_root)
            if literature_private_root is not None
            else _default_literature_private_root_for_state(Path(state_path))
        )
        literature_checkpoints = create_mac_literature_checkpoint_services(
            private_root=checkpoint_root,
        )
        literature_jobs = LiteratureExtractionJobStore(
            snapshots=LiteraturePDFSnapshotAuthority(
                blob_store=literature_checkpoints.snapshot_blobs,
            )
        )
        literature_ports = literature_extraction_business_ports(
            literature_jobs,
            session_id=str(desktop_session_id),
            db=database,
            finalizer=AtomicEvidenceDBFinalizer(database),
            checkpoint_runtime=literature_checkpoints.checkpoint_runtime,
        )
        snapshots = CompositeContentSnapshotAuthority(
            {
                "personal_table": personal_ports.snapshots,
                "harness_literature": harness_ports.snapshots,
                "literature_extraction_stage": literature_ports.snapshots,
            }
        )
        prepared_actions = PreparedActionService(
            runtime_state=runtime_state,
            consents=consent_service,
            snapshots=snapshots,
        )
        business_actions = BusinessPreparedActionRegistry(
            prepared_actions=prepared_actions,
            client_factory=client_factory,
            assemblers={
                "personal_suggestion": personal_ports.assembler,
                "selected_evidence_chat": selected_ports.assembler,
                "librarian": librarian_ports.assembler,
                "literature_extraction": literature_ports.assembler,
            },
            executors={
                "personal_suggestion": personal_ports.executor,
                "selected_evidence_chat": selected_ports.executor,
                "librarian": librarian_ports.executor,
                "literature_extraction": literature_ports.executor,
            },
            projectors={
                "personal_suggestion": personal_ports.projector,
                "selected_evidence_chat": selected_ports.projector,
                "librarian": librarian_ports.projector,
                "literature_extraction": literature_ports.projector,
            },
            readiness_gate=runtime_state,
        )
    controller = DesktopAIController(
        settings=desktop_service,
        prepared_actions=prepared_actions,
        business_actions=business_actions,
    )
    return MacAIRuntimeServices(
        execution_lock=execution_lock,
        credential_manager=manager,
        legacy_deepseek_store=manager.deepseek_legacy_view(),
        state_store=state_store,
        runtime_state=runtime_state,
        desktop_service=desktop_service,
        consent_service=consent_service,
        prepared_actions=prepared_actions,
        controller=controller,
        client_factory=client_factory,
        execution_lease=execution_lease,
        custom_provider=custom_provider,
        readiness=readiness,
        database=database,
        personal_import_service=personal_import_service,
        desktop_session_id=desktop_session_id,
        federated_search_session=federated_search_session,
        harness_runtime=effective_harness_runtime,
        snapshot_authority=snapshots,
        business_actions=business_actions,
        personal_suggestion_ports=personal_ports,
        harness_ports=harness_ports,
        selected_evidence_chat_ports=selected_ports,
        librarian_ports=librarian_ports,
        literature_extraction_ports=literature_ports,
        literature_jobs=literature_jobs,
        literature_checkpoints=literature_checkpoints,
    )


def _default_literature_private_root_for_state(state_path: Path) -> Path:
    if state_path == Path(DEFAULT_AI_RUNTIME_STATE_PATH):
        return DEFAULT_LITERATURE_PRIVATE_ROOT
    state_parent = state_path.parent
    if state_parent.name == "State":
        return state_parent.parent / "Private Data" / "Literature Tasks"
    return state_parent / "literature-tasks-v1"


def mac_ai_runtime_services(
    *,
    database: EvidenceDB | None = None,
    personal_import_service: PersonalImportService | None = None,
    desktop_session_id: str | None = None,
    federated_search_session: FederatedSearchSessionProtocol | None = None,
    harness_cordis_path: Path | str | None = None,
) -> MacAIRuntimeServices:
    global _SERVICES
    with _SERVICES_LOCK:
        if _SERVICES is None:
            if (
                database is None
                or personal_import_service is None
                or desktop_session_id is None
                or federated_search_session is None
                or harness_cordis_path is None
            ):
                raise RuntimeError("mac AI runtime requires the desktop composition inputs")
            _SERVICES = create_mac_ai_runtime_services(
                database=database,
                personal_import_service=personal_import_service,
                desktop_session_id=desktop_session_id,
                federated_search_session=federated_search_session,
                harness_cordis_path=harness_cordis_path,
            )
        elif any(
            value is not None
            for value in (
                database,
                personal_import_service,
                desktop_session_id,
                federated_search_session,
            )
        ):
            if (
                database is not _SERVICES.database
                or personal_import_service is not _SERVICES.personal_import_service
                or desktop_session_id != _SERVICES.desktop_session_id
                or federated_search_session is not _SERVICES.federated_search_session
            ):
                raise RuntimeError("mac AI runtime is already bound to another desktop graph")
        return _SERVICES


__all__ = [
    "MacAIRuntimeServices",
    "create_mac_ai_runtime_services",
    "disable_legacy_environment_credentials",
    "mac_ai_runtime_services",
]
