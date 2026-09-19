from __future__ import annotations

import json
import hashlib
import hmac
import time
from dataclasses import replace
from pathlib import Path

import fitz
import pytest

from auto_research.ai.business_actions import (
    BusinessActionError,
    LiteratureDerivedBudgetBusinessAIClient,
)
from auto_research.ai.activity import bind_activity_observer
from auto_research.ai.prepared_actions import PreparedOutbound
from auto_research.evidence.db import EvidenceDB
from auto_research.evidence.literature_checkpoint_runtime import (
    LiteratureCheckpointCall,
    LiteratureCheckpointRuntime,
    decode_execution_state,
)
from auto_research.evidence.literature_extraction_business_action import (
    EvidenceDBLiteratureJobStarter,
    LiteratureExtractionBusinessAssembler,
    LiteratureExtractionBusinessExecutor,
    LiteratureExtractionBusinessProjector,
    literature_extraction_business_ports,
)
from auto_research.evidence.literature_extraction_finalizer import (
    AtomicEvidenceDBFinalizer,
)
from auto_research.evidence.literature_extraction_job import (
    LiteratureExtractionJobError,
    LiteratureExtractionJobStore,
    LiteraturePDFSnapshotAuthority,
)
from auto_research.evidence.literature_extraction_checkpoint_workflow import (
    _project_literature_error,
)
from auto_research.evidence.literature_extraction_budget import (
    task_budget_for_page_blocks,
)
from auto_research.evidence.literature_extraction_stages import (
    ExistingLiteratureStagePlanner,
)
from auto_research.evidence.literature_job_persistence import decode_job_private_state
from auto_research.evidence.literature_snapshot_blob import SealedImmutablePDFBlobStore
from auto_research.evidence.search_index import EvidenceSearchIndex
from auto_research.evidence.source_highlight import (
    get_source_view,
    render_source_highlight_png,
)
from auto_research.evidence.literature_task_checkpoint import (
    LiteratureTaskCheckpointError,
    LiteratureTaskManifest,
)
from auto_research.evidence.literature_task_checkpoint_service import (
    LiteratureTaskCheckpointService,
)
from auto_research.evidence.literature_task_checkpoint_store import (
    SealedSQLiteLiteratureCheckpointStore,
)
from auto_research.evidence.uploads import UploadService
from auto_research.evidence.visual_evidence import list_visual_assets


def _make_pdf(path: Path, *, pages: int = 1) -> None:
    document = fitz.open()
    for index in range(pages):
        page = document.new_page()
        page.insert_text(
            (72, 72),
            f"The measured hardness was 3.2 GPa at 300 K in sample A. Page {index + 1}.",
        )
    document.save(path)
    document.close()


def _scientific_pdf_bytes() -> bytes:
    """Small real PDF fixture covering text, table and figure evidence."""

    document = fitz.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "The measured hardness was 3.2 GPa at 300 K in sample A.",
    )

    # A vector figure followed by a formal caption. Generic visual discovery
    # must crop it without interpreting or inventing curve values.
    page.draw_rect(
        fitz.Rect(72, 110, 300, 235),
        color=(0.1, 0.3, 0.7),
        fill=(0.85, 0.9, 1.0),
    )
    page.draw_line((90, 215), (270, 140), color=(0.1, 0.3, 0.7), width=2)
    page.insert_text((72, 255), "Figure 1. Irradiation hardness overview.")

    page.insert_text((72, 320), "Table 1. Measured hardness summary.")
    table = fitz.Rect(72, 340, 430, 445)
    for y in (340, 375, 410, 445):
        page.draw_line((table.x0, y), (table.x1, y), color=(0, 0, 0))
    for x in (72, 250, 430):
        page.draw_line((x, table.y0), (x, table.y1), color=(0, 0, 0))
    page.insert_text((82, 362), "Condition")
    page.insert_text((260, 362), "Hardness")
    page.insert_text((82, 397), "300 K")
    page.insert_text((260, 397), "3.2 GPa")
    page.insert_text((82, 432), "Source")
    page.insert_text((260, 432), "Measured")
    page.insert_textbox(
        fitz.Rect(72, 480, 520, 650),
        (
            "Results and discussion. The experiment compares the irradiation response "
            "of sample A under a controlled temperature. The hardness measurement is "
            "reported together with its unit, material identity, experimental condition, "
            "and method so that the scientific record can be independently verified. "
            "The figure is descriptive and no curve points are inferred from pixels."
        ),
        fontsize=10,
    )

    payload = document.tobytes()
    document.close()
    return payload


@pytest.fixture
def evidence(tmp_path: Path):
    db = EvidenceDB(tmp_path / "evidence.sqlite")
    db.init()
    pdf = tmp_path / "paper.pdf"
    _make_pdf(pdf)
    paper_id = db.upsert_paper(
        title="Safe experiment", doi="10.1/safe", pdf_path=str(pdf)
    )
    return db, paper_id


def _counts(db: EvidenceDB) -> dict[str, int]:
    with db.connect() as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "quality_pipeline_runs", "quality_candidates",
                "data_items", "data_versions",
            )
        }


def _extraction_payload():
    return {
        "data": [{
            "value_text": "3.2",
            "meaning": "测得硬度",
            "unit": "GPa",
            "context_explanation": "样品A在300 K条件",
            "source_page": 1,
            "source_locator": "Results",
            "source_excerpt": "measured hardness was 3.2 GPa at 300 K",
            "evidence_type": "measured",
            "source_precision": "exact_text",
        }],
        "findings": [],
        "pending_tasks": [],
    }


def _verification_payload(messages):
    content = messages[1]["content"]
    candidates = json.loads(
        content.split("\n\nSource pages:", 1)[0].split("Verify these candidates:\n", 1)[1]
    )
    return {
        "verdicts": [{
            "candidate_id": candidate["candidate_id"],
            "verdict": "supported",
            "reason": "matched",
        } for candidate in candidates]
    }


class _Sealer:
    def __init__(self, key: bytes = b"literature-business-test-key") -> None:
        self.key = key

    def seal(self, plaintext: bytes, *, associated_data: bytes) -> bytes:
        tag = hmac.new(self.key, associated_data + plaintext, hashlib.sha256).digest()
        return tag + plaintext[::-1]

    def open(self, ciphertext: bytes, *, associated_data: bytes) -> bytes:
        tag, body = ciphertext[:32], ciphertext[32:]
        plaintext = body[::-1]
        expected = hmac.new(
            self.key, associated_data + plaintext, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(tag, expected):
            raise ValueError
        return plaintext


def _persistent_job_store(root: Path, *, session_key: bytes = b"x" * 32):
    snapshots = LiteraturePDFSnapshotAuthority(
        blob_store=SealedImmutablePDFBlobStore(
            data_root=root / "pdf-blobs",
            sealer=_Sealer(),
        )
    )
    return LiteratureExtractionJobStore(
        session_key=session_key,
        snapshots=snapshots,
    )


def _checkpoint_runtime(root: Path) -> LiteratureCheckpointRuntime:
    store = SealedSQLiteLiteratureCheckpointStore(
        data_root=root / "checkpoints",
        sealer=_Sealer(b"checkpoint-business-test-key"),
    )
    return LiteratureCheckpointRuntime(
        LiteratureTaskCheckpointService(store=store)
    )


class _RawProvider:
    def __init__(self, *, fail_at: int | None = None) -> None:
        self.calls = 0
        self.fail_at = fail_at
        self.received: list[tuple[dict[str, object], ...]] = []

    def request_json(self, messages, **kwargs):
        self.calls += 1
        self.received.append(tuple(dict(message) for message in messages))
        if self.fail_at == self.calls:
            raise RuntimeError("provider failed")
        if "Verify these candidates:" in messages[1]["content"]:
            return _verification_payload(messages)
        return _extraction_payload()


def _prepared_action(draft, *, session_digest: str = "1" * 64) -> PreparedOutbound:
    now = int(time.time())
    job_handle = draft.outbound["job_handle"]
    return PreparedOutbound(
        action_id="action_" + hashlib.sha256(job_handle.encode()).hexdigest(),
        session_digest=session_digest,
        scope="literature_extraction",
        provider_id="deepseek",
        runtime_revision=3,
        credential_generation=2,
        runtime_activation="connection_verified",
        runtime_task_models=(
            ("analysis", "deepseek-v4-flash"),
            ("extraction", "deepseek-v4-pro"),
        ),
        task="extraction",
        task_models=(
            ("analysis", "deepseek-v4-flash"),
            ("extraction", "deepseek-v4-pro"),
        ),
        models=("deepseek-v4-flash", "deepseek-v4-pro"),
        executor_id="literature_extraction_executor",
        executor_version="v1",
        estimated_calls=draft.estimated_calls,
        max_calls=draft.max_calls,
        max_tokens=draft.max_tokens,
        outbound={
            "payload": draft.outbound,
            "call_plan": [call.canonical_dict() for call in draft.call_plan],
        },
        outbound_digest="2" * 64,
        manifest_digest="3" * 64,
        units=draft.content_units,
        byte_count=1,
        issued_at=now - 1,
        expires_at=now + 300,
    )


def _budget_client(action: PreparedOutbound, raw: _RawProvider):
    return LiteratureDerivedBudgetBusinessAIClient(client=raw, action=action)


def _checkpoint_task_id(job_token: str) -> str:
    return "literature_" + hashlib.sha256(job_token.encode()).hexdigest()


def _checkpoint_owner_id(job_token: str) -> str:
    return "literature-worker:" + hashlib.sha256(
        ("literature-owner-v1:" + job_token).encode()
    ).hexdigest()


def _start_checkpoint(
    runtime: LiteratureCheckpointRuntime,
    store: LiteratureExtractionJobStore,
    action: PreparedOutbound,
    *,
    session_id: str,
):
    payload = action.outbound["payload"]
    token = payload["job_handle"]
    job_state = store.export_private_state(token, session_id=session_id)
    decoded = decode_job_private_state(job_state)
    manifest = LiteratureTaskManifest(
        task_id=_checkpoint_task_id(token),
        session_digest=action.session_digest,
        provider_id=action.provider_id,
        runtime_revision=action.runtime_revision,
        credential_generation=action.credential_generation,
        task_models=action.task_models,
        executor_id=action.executor_id,
        executor_version=action.executor_version,
        pdf_snapshot_fingerprint=decoded.snapshot["content_fingerprint"],
        max_calls=action.max_calls,
        max_tokens=action.max_tokens,
        issued_at=action.issued_at,
        expires_at=int(decoded.expires_at),
    )
    return runtime.start(
        manifest=manifest,
        job_state=job_state,
        stage=payload["stage"],
        stage_fingerprint=payload["stage_fingerprint"],
    )


class _RecordingRuntime:
    def __init__(self, runtime: LiteratureCheckpointRuntime, events: list[str]) -> None:
        self.runtime = runtime
        self.events = events
        self.fail_complete_once = False

    def __getattr__(self, name):
        return getattr(self.runtime, name)

    def complete(self, checkpoint, **kwargs):
        if self.fail_complete_once:
            self.fail_complete_once = False
            self.events.append("checkpoint_complete_failed")
            raise LiteratureTaskCheckpointError(
                "literature_checkpoint_store_unavailable"
            )
        result = self.runtime.complete(checkpoint, **kwargs)
        self.events.append("checkpoint_complete")
        return result


class _RecordingFinalizer(AtomicEvidenceDBFinalizer):
    def __init__(self, db: EvidenceDB, events: list[str]) -> None:
        super().__init__(db)
        self.events = events

    def finalize(self, package):
        result = super().finalize(package)
        self.events.append("published")
        return result


def test_initial_request_is_server_started_with_fixed_bounds_and_no_write(evidence) -> None:
    db, paper_id = evidence
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    starter = EvidenceDBLiteratureJobStarter(db, store)
    assembler = LiteratureExtractionBusinessAssembler(
        store, session_id="owner", starter=starter
    )
    before = _counts(db)
    draft = assembler.assemble({"paper_id": paper_id, "force_rescan": False})
    assert draft.outbound["stage"] == "initial_focus"
    assert draft.estimated_calls == len(draft.call_plan)
    assert draft.max_calls == 12
    assert draft.max_tokens == 92_000
    assert draft.outbound["task_max_calls"] == 12
    assert draft.outbound["task_max_tokens"] == 92_000
    assert draft.outbound["planner_version"] == "v2"
    assert draft.outbound["initial_content_fingerprint"]
    assert store.summary(draft.outbound["job_handle"], session_id="owner")[
        "sending_scope"
    ]["pdf_page_count"] == 1
    assert _counts(db) == before

    # Cancelling before consent/execution leaves only an expiring memory job.
    retry = assembler.assemble({"paper_id": paper_id, "force_rescan": False})
    assert retry.outbound["job_handle"] != draft.outbound["job_handle"]
    assert _counts(db) == before


def test_task_budget_uses_actual_frozen_page_blocks_and_stays_below_policy() -> None:
    assert task_budget_for_page_blocks(1).__dict__ == {
        "max_calls": 12, "max_tokens": 92_000,
    }
    assert task_budget_for_page_blocks(2).__dict__ == {
        "max_calls": 20, "max_tokens": 164_000,
    }
    assert task_budget_for_page_blocks(4).__dict__ == {
        "max_calls": 36, "max_tokens": 308_000,
    }
    assert task_budget_for_page_blocks(16).__dict__ == {
        "max_calls": 132, "max_tokens": 1_172_000,
    }


def test_starter_covers_supported_article_without_eight_page_truncation(tmp_path: Path) -> None:
    db = EvidenceDB(tmp_path / "evidence.sqlite")
    db.init()
    pdf = tmp_path / "long.pdf"
    _make_pdf(pdf, pages=10)
    paper_id = db.upsert_paper(title="Long paper", doi="10.1/long", pdf_path=str(pdf))
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    summary = EvidenceDBLiteratureJobStarter(db, store).start(
        paper_id=paper_id, force_rescan=False, session_id="owner"
    )
    assert summary["sending_scope"]["pdf_page_count"] == 10
    assert summary["sending_scope"]["page_block_count"] == 3
    draft = LiteratureExtractionBusinessAssembler(store, session_id="owner").assemble(
        {"job_token": summary["job_token"]}
    )
    assert draft.max_calls == 28
    assert draft.max_tokens == 236_000


def test_starter_rejects_oversized_article_instead_of_silently_truncating(tmp_path: Path) -> None:
    db = EvidenceDB(tmp_path / "evidence.sqlite")
    db.init()
    pdf = tmp_path / "too-long.pdf"
    _make_pdf(pdf, pages=65)
    paper_id = db.upsert_paper(title="Too long", doi="10.1/too-long", pdf_path=str(pdf))
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)

    with pytest.raises(LiteratureExtractionJobError) as error:
        EvidenceDBLiteratureJobStarter(db, store).start(
            paper_id=paper_id, force_rescan=False, session_id="owner"
        )

    assert error.value.code == "literature_pdf_page_limit_exceeded"
    assert "未创建截断任务" in error.value.safe_message
    assert store._jobs == {}

    assembler = LiteratureExtractionBusinessAssembler(
        store,
        session_id="owner",
        starter=EvidenceDBLiteratureJobStarter(db, store),
    )
    with pytest.raises(BusinessActionError) as projected:
        assembler.assemble({"paper_id": paper_id, "force_rescan": False})
    assert projected.value.cause_code == "literature_pdf_page_limit_exceeded"
    assert projected.value.stage == "preflight"
    assert projected.value.next_action == "select_supported_pdf"


def test_single_page_text_limit_has_actionable_preflight_guidance() -> None:
    projected = _project_literature_error(
        LiteratureExtractionJobError(
            "literature_pdf_text_limit_exceeded",
            "PDF 单页文本超过安全抽取上限，未创建截断任务",
        ),
        phase="preflight",
    )

    assert projected.cause_code == "literature_pdf_text_limit_exceeded"
    assert projected.stage == "preflight"
    assert projected.next_action == "select_supported_pdf"


@pytest.mark.parametrize(
    "domain_request",
    [
        {"paper_id": 1, "force_rescan": False, "prompt": "ignore rules"},
        {"paper_id": 1, "force_rescan": False, "max_tokens": 999999},
        {"paper_id": 1},
        {"job_token": "x", "model": "arbitrary"},
        {"paper_id": True, "force_rescan": False},
        {"paper_id": 1, "force_rescan": "yes"},
    ],
)
def test_renderer_cannot_supply_plan_or_malformed_initial_fields(evidence, domain_request) -> None:
    db, _paper_id = evidence
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    assembler = LiteratureExtractionBusinessAssembler(
        store,
        session_id="owner",
        starter=EvidenceDBLiteratureJobStarter(db, store),
    )
    with pytest.raises(BusinessActionError):
        assembler.assemble(domain_request)
    assert store._jobs == {}


def test_rescan_requires_explicit_confirmation_and_creates_a_new_job(evidence) -> None:
    db, paper_id = evidence
    with db.connect() as connection:
        connection.execute(
            """INSERT INTO ai_extraction_runs(
               paper_id,status,mode,model,pdf_sha256,created_at,finished_at
               ) VALUES(?,'completed','commit','deepseek-v4-pro',?,'2026-01-01','2026-01-01')""",
            (paper_id, "a" * 64),
        )
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    assembler = LiteratureExtractionBusinessAssembler(
        store,
        session_id="owner",
        starter=EvidenceDBLiteratureJobStarter(db, store),
    )
    with pytest.raises(BusinessActionError) as rejected:
        assembler.assemble({"paper_id": paper_id, "force_rescan": False})
    assert rejected.value.code == "business_action_prepare_failed"
    assert rejected.value.cause_code == "literature_rescan_confirmation_required"
    assert rejected.value.stage == "preflight"
    assert rejected.value.next_action == "confirm_rescan"
    assert rejected.value.public_dict()["cause_code"] == "literature_rescan_confirmation_required"
    assert rejected.value.__cause__.code == "literature_rescan_confirmation_required"
    assert store._jobs == {}

    first = assembler.assemble({"paper_id": paper_id, "force_rescan": True})
    second = assembler.assemble({"paper_id": paper_id, "force_rescan": True})
    assert first.outbound["job_handle"] != second.outbound["job_handle"]


def test_continuation_remains_bound_to_starting_session(evidence) -> None:
    db, paper_id = evidence
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    owner = LiteratureExtractionBusinessAssembler(
        store,
        session_id="owner",
        starter=EvidenceDBLiteratureJobStarter(db, store),
    )
    first = owner.assemble({"paper_id": paper_id, "force_rescan": False})
    attacker = LiteratureExtractionBusinessAssembler(
        store,
        session_id="other-session",
        starter=EvidenceDBLiteratureJobStarter(db, store),
    )
    with pytest.raises(BusinessActionError) as rejected:
        attacker.assemble({"job_token": first.outbound["job_handle"]})
    assert rejected.value.code == "business_action_prepare_failed"


def test_queued_cancel_persists_terminal_checkpoint_without_provider(evidence) -> None:
    db, paper_id = evidence
    root = db.path.parent
    store = _persistent_job_store(root / "job-state")
    runtime = _checkpoint_runtime(root / "checkpoint-state")
    ports = literature_extraction_business_ports(
        store,
        session_id="owner",
        db=db,
        finalizer=AtomicEvidenceDBFinalizer(db),
        checkpoint_runtime=runtime,
    )
    draft = ports.assembler.assemble({"paper_id": paper_id, "force_rescan": False})
    action = _prepared_action(draft)

    ports.executor.request_cancel(action=action, phase="queued")

    task_id = _checkpoint_task_id(draft.outbound["job_handle"])
    persisted = runtime._service.load(task_id)
    assert persisted.state == "cancelled"
    assert persisted.spent_calls == 0
    assert persisted.receipts == ()
    with pytest.raises(LiteratureTaskCheckpointError) as recovered:
        runtime.recover_job_state(task_id)
    assert recovered.value.code == "literature_task_cancelled"


def test_running_cancel_stops_after_persisting_successful_paid_receipt(evidence) -> None:
    db, paper_id = evidence
    root = db.path.parent
    store = _persistent_job_store(root / "job-state")
    runtime = _checkpoint_runtime(root / "checkpoint-state")
    ports = literature_extraction_business_ports(
        store,
        session_id="owner",
        db=db,
        finalizer=AtomicEvidenceDBFinalizer(db),
        checkpoint_runtime=runtime,
    )
    draft = ports.assembler.assemble({"paper_id": paper_id, "force_rescan": False})
    action = _prepared_action(draft)
    task_id = _checkpoint_task_id(draft.outbound["job_handle"])

    class _CancelAfterFirst(_RawProvider):
        def request_json(self, messages, **kwargs):
            result = super().request_json(messages, **kwargs)
            runtime.request_cancel(task_id)
            return result

    raw = _CancelAfterFirst()
    with pytest.raises(BusinessActionError) as cancelled:
        ports.executor.execute(action=action, ai_client=_budget_client(action, raw))
    assert cancelled.value.cause_code == "literature_task_cancelled"
    assert cancelled.value.stage == "cancelled"
    assert raw.calls == 1
    persisted = runtime._service.load(task_id)
    assert persisted.state == "cancelled"
    assert persisted.receipts[0].state == "succeeded"


def test_cancel_during_unknown_provider_result_never_claims_cancelled(evidence) -> None:
    db, paper_id = evidence
    root = db.path.parent
    store = _persistent_job_store(root / "job-state")
    runtime = _checkpoint_runtime(root / "checkpoint-state")
    ports = literature_extraction_business_ports(
        store,
        session_id="owner",
        db=db,
        finalizer=AtomicEvidenceDBFinalizer(db),
        checkpoint_runtime=runtime,
    )
    draft = ports.assembler.assemble({"paper_id": paper_id, "force_rescan": False})
    action = _prepared_action(draft)
    task_id = _checkpoint_task_id(draft.outbound["job_handle"])

    class _CancelThenFail(_RawProvider):
        def request_json(self, messages, **kwargs):
            self.calls += 1
            runtime.request_cancel(task_id)
            raise RuntimeError("provider outcome unknown")

    raw = _CancelThenFail()
    with pytest.raises(BusinessActionError) as failed:
        ports.executor.execute(action=action, ai_client=_budget_client(action, raw))
    assert failed.value.cause_code == "literature_call_outcome_unknown"
    assert runtime._service.load(task_id).state == "outcome_unknown"
    assert raw.calls == 1


def test_restart_preflight_restores_authenticated_job_without_lease_or_model(
    evidence,
) -> None:
    db, paper_id = evidence
    root = db.path.parent
    blob_root = root / "shared-job-state"
    runtime = _checkpoint_runtime(root / "checkpoint-state")
    original = _persistent_job_store(blob_root, session_key=b"a" * 32)
    draft = LiteratureExtractionBusinessAssembler(
        original,
        session_id="old-session",
        starter=EvidenceDBLiteratureJobStarter(db, original),
        checkpoint_runtime=runtime,
    ).assemble({"paper_id": paper_id, "force_rescan": False})
    action = _prepared_action(draft)
    checkpoint = _start_checkpoint(
        runtime, original, action, session_id="old-session"
    )
    assert checkpoint.lease_owner_digest is None

    restarted = _persistent_job_store(blob_root, session_key=b"b" * 32)
    assembler = LiteratureExtractionBusinessAssembler(
        restarted,
        session_id="new-session",
        checkpoint_runtime=runtime,
    )
    request = {"job_token": draft.outbound["job_handle"]}
    assembler.preflight(request)
    # Repeated free preflight is idempotent: it only peeks the restored job.
    assembler.preflight(request)
    resumed = assembler.assemble(request)
    assert resumed.outbound["stage"] == draft.outbound["stage"]
    recovered, _job_state = runtime.recover_job_state(
        _checkpoint_task_id(draft.outbound["job_handle"])
    )
    assert recovered.lease_owner_digest is None
    assert recovered.state == "authorized"


def test_legacy_global_budget_checkpoint_requires_zero_call_restart(evidence) -> None:
    db, paper_id = evidence
    root = db.path.parent
    store = _persistent_job_store(root / "job-state")
    runtime = _checkpoint_runtime(root / "checkpoint-state")
    draft = LiteratureExtractionBusinessAssembler(
        store,
        session_id="owner",
        starter=EvidenceDBLiteratureJobStarter(db, store),
        checkpoint_runtime=runtime,
    ).assemble({"paper_id": paper_id, "force_rescan": False})
    action = _prepared_action(draft)
    checkpoint = _start_checkpoint(runtime, store, action, session_id="owner")
    legacy = replace(
        checkpoint,
        manifest=replace(
            checkpoint.manifest,
            max_calls=512,
            max_tokens=8_200_000,
        ),
    )
    executor = LiteratureExtractionBusinessExecutor(
        store,
        ExistingLiteratureStagePlanner(),
        session_id="owner",
        checkpoint_runtime=runtime,
    )
    with pytest.raises(LiteratureTaskCheckpointError) as rejected:
        executor._assert_checkpoint_binding(
            checkpoint=legacy,
            action=action,
            job_token=draft.outbound["job_handle"],
        )
    assert rejected.value.code == "literature_checkpoint_policy_changed"
    assert rejected.value.public_dict()["retryable"] is False


def test_succeeded_receipt_prefix_resumes_budget_without_duplicate_provider_call(
    evidence,
) -> None:
    db, paper_id = evidence
    root = db.path.parent
    store = _persistent_job_store(root / "job-state")
    runtime = _checkpoint_runtime(root / "checkpoint-state")
    ports = literature_extraction_business_ports(
        store,
        session_id="owner",
        db=db,
        finalizer=AtomicEvidenceDBFinalizer(db),
        checkpoint_runtime=runtime,
    )
    draft = ports.assembler.assemble({"paper_id": paper_id, "force_rescan": False})
    action = _prepared_action(draft)
    token = draft.outbound["job_handle"]
    _start_checkpoint(runtime, store, action, session_id="owner")
    checkpoint = runtime.recover(
        _checkpoint_task_id(token), owner_id=_checkpoint_owner_id(token)
    )
    stage = store.peek_stage(token, session_id="owner")
    first = stage.calls[0]
    checkpoint, results = runtime.execute_stage(
        checkpoint,
        owner_id=_checkpoint_owner_id(token),
        stage_fingerprint=stage.stage_fingerprint,
        calls=(LiteratureCheckpointCall(
            stage.name,
            "extraction",
            first.call_digest,
            first.max_tokens,
        ),),
        invoke=lambda _index: _extraction_payload(),
    )
    assert results == (_extraction_payload(),)

    raw = _RawProvider()
    result = ports.executor.execute(
        action=action,
        ai_client=_budget_client(action, raw),
    )
    public = ports.projector.project(result)
    completed, _ = runtime.recover_job_state(_checkpoint_task_id(token))
    persisted = decode_execution_state(completed.private_payload)
    assert public["status"] == "completed"
    assert completed.state == "completed"
    assert completed.spent_calls == raw.calls + 1
    assert persisted.completion_result == public
    first_messages = tuple(dict(message) for message in first.messages)
    assert first_messages not in raw.received


def test_one_task_action_finishes_all_dynamic_stages_and_atomic_commit(evidence) -> None:
    db, paper_id = evidence
    root = db.path.parent
    store = _persistent_job_store(root / "job-state")
    runtime = _checkpoint_runtime(root / "checkpoint-state")
    ports = literature_extraction_business_ports(
        store,
        session_id="owner",
        db=db,
        finalizer=AtomicEvidenceDBFinalizer(db),
        checkpoint_runtime=runtime,
    )
    draft = ports.assembler.assemble({"paper_id": paper_id, "force_rescan": False})
    action = _prepared_action(draft)
    raw = _RawProvider()
    client = _budget_client(action, raw)
    activity: list[dict[str, object]] = []
    with bind_activity_observer(lambda event: activity.append(dict(event))):
        result = ports.executor.execute(action=action, ai_client=client)
    public = ports.projector.project(result)
    assert public["schema_version"] == "literature-extraction-commit-result-v2"
    assert public["status"] == "completed"
    assert public["paper"] == {"title": "Safe experiment", "doi": "10.1/safe"}
    assert public["candidate_count"] == 1
    assert public["published_item_count"] == 1
    assert public["visual_evidence_ready"] is False
    assert public["visual_stage_status"] == "not_found"
    assert public["search_index"]["status"] == "refreshed"
    assert raw.calls > draft.estimated_calls
    assert _counts(db)["quality_pipeline_runs"] == 1
    assert str(db.path) not in repr(public)
    assert "job_token" not in public
    assert "pdf_sha256" not in repr(public)
    codes = [event["code"] for event in activity]
    assert codes[0] == "literature_initial_focus"
    assert "literature_coverage_verification" in codes
    assert codes[-1] == "literature_publishing"
    assert all("path" not in repr(event).casefold() for event in activity)

    # The completed in-memory job is consumed; it cannot charge again.
    with pytest.raises(BusinessActionError):
        ports.assembler.assemble({"job_token": draft.outbound["job_handle"]})


def test_uploaded_pdf_closes_scientific_chain_through_visual_search_and_source(
    tmp_path: Path,
) -> None:
    """One fixture must traverse the same authorities used by the Mac App."""

    db = EvidenceDB(tmp_path / "evidence.sqlite")
    db.init()
    uploaded = UploadService(db, storage_root=tmp_path / "uploads").upload(
        _scientific_pdf_bytes(),
        "scientific-chain.pdf",
        title="Scientific chain paper",
        doi="10.1/scientific-chain",
    )
    assert uploaded["outcome"] == "accepted"
    assert uploaded["ready_for_extraction"] is True
    paper_id = int(uploaded["paper_id"])

    store = _persistent_job_store(tmp_path / "job-state")
    runtime = _checkpoint_runtime(tmp_path / "checkpoint-state")
    ports = literature_extraction_business_ports(
        store,
        session_id="owner",
        db=db,
        finalizer=AtomicEvidenceDBFinalizer(db),
        checkpoint_runtime=runtime,
    )
    draft = ports.assembler.assemble({"paper_id": paper_id, "force_rescan": False})
    action = _prepared_action(draft)
    result = ports.projector.project(
        ports.executor.execute(
            action=action,
            ai_client=_budget_client(action, _RawProvider()),
        )
    )

    assert result["status"] == "completed"
    assert result["published_item_count"] == 1
    assert result["table_candidate_count"] == 1
    assert result["figure_candidate_count"] == 1
    assert result["visual_evidence_ready"] is True
    assert result["visual_stage_status"] == "ready"
    assert result["search_index"]["status"] == "refreshed"

    assets = list_visual_assets(db, paper_id=paper_id)
    assert {asset["asset_type"] for asset in assets} == {"table", "figure"}
    assert {asset["quality_gate_status"] for asset in assets} == {"manual_review"}
    for asset in assets:
        image = Path(str(asset["image_path"]))
        assert image.read_bytes().startswith(b"\x89PNG")
        assert hashlib.sha256(image.read_bytes()).hexdigest() == asset["image_sha256"]

    search = EvidenceSearchIndex(db)
    page = search.search(
        "hardness",
        entity_types=("item", "table", "figure"),
        paper_ids=(paper_id,),
        refresh=False,
    )
    assert {row["entity_type"] for row in page.rows} == {"item", "table", "figure"}
    published = search.search(
        "hardness",
        entity_types=("item", "table", "figure"),
        paper_ids=(paper_id,),
        quality_filter="published",
        refresh=False,
    )
    assert {row["entity_type"] for row in published.rows} == {"item"}
    item = next(row for row in page.rows if row["entity_type"] == "item")
    source = get_source_view(db, int(item["entity_id"]))
    assert source["page_number"] == 1
    assert source["has_highlight"] is True
    assert source["image_url"].endswith("/source-highlight.png")
    assert render_source_highlight_png(db, int(item["entity_id"])).startswith(b"\x89PNG")


def test_publish_checkpoint_complete_and_acknowledge_are_strictly_ordered(
    evidence,
) -> None:
    db, paper_id = evidence
    root = db.path.parent
    events: list[str] = []
    store = _persistent_job_store(root / "job-state")
    original_ack = store.acknowledge_finalized

    def recording_ack(job_token: str, *, session_id: str) -> None:
        events.append("acknowledge")
        original_ack(job_token, session_id=session_id)

    store.acknowledge_finalized = recording_ack
    runtime = _RecordingRuntime(
        _checkpoint_runtime(root / "checkpoint-state"), events
    )
    ports = literature_extraction_business_ports(
        store,
        session_id="owner",
        db=db,
        finalizer=_RecordingFinalizer(db, events),
        checkpoint_runtime=runtime,
    )
    draft = ports.assembler.assemble({"paper_id": paper_id, "force_rescan": False})
    action = _prepared_action(draft)
    raw = _RawProvider()
    result = ports.executor.execute(
        action=action,
        ai_client=_budget_client(action, raw),
    )
    assert result["summary"]["status"] == "completed"
    assert events[-3:] == ["published", "checkpoint_complete", "acknowledge"]


@pytest.mark.parametrize("late_cancel", [False, True])
def test_checkpoint_complete_failure_retains_job_and_idempotent_retry(
    evidence, late_cancel,
) -> None:
    db, paper_id = evidence
    root = db.path.parent
    events: list[str] = []
    store = _persistent_job_store(root / "job-state")
    original_ack = store.acknowledge_finalized

    def recording_ack(job_token: str, *, session_id: str) -> None:
        events.append("acknowledge")
        original_ack(job_token, session_id=session_id)

    store.acknowledge_finalized = recording_ack
    runtime = _RecordingRuntime(
        _checkpoint_runtime(root / "checkpoint-state"), events
    )
    runtime.fail_complete_once = True
    ports = literature_extraction_business_ports(
        store,
        session_id="owner",
        db=db,
        finalizer=_RecordingFinalizer(db, events),
        checkpoint_runtime=runtime,
    )
    draft = ports.assembler.assemble({"paper_id": paper_id, "force_rescan": False})
    action = _prepared_action(draft)
    raw = _RawProvider()
    client = _budget_client(action, raw)
    token = draft.outbound["job_handle"]
    with pytest.raises(BusinessActionError) as failed:
        ports.executor.execute(action=action, ai_client=client)
    assert failed.value.cause_code == "literature_checkpoint_store_unavailable"
    assert failed.value.stage == "checkpoint"
    assert failed.value.next_action == "retry_current_stage"
    assert store.summary(token, session_id="owner")["stage"] == "validated"
    calls_after_failure = raw.calls
    assert _counts(db)["quality_pipeline_runs"] == 1
    assert "acknowledge" not in events

    if late_cancel:
        ports.executor.request_cancel(action=action, phase="running")
    retried = ports.executor.execute(action=action, ai_client=client)
    assert retried["summary"]["idempotent"] is True
    assert raw.calls == calls_after_failure
    assert events[-3:] == ["published", "checkpoint_complete", "acknowledge"]
    with pytest.raises(LiteratureExtractionJobError):
        store.summary(token, session_id="owner")


def test_final_stage_without_trusted_finalizer_never_claims_saved(evidence) -> None:
    db, paper_id = evidence
    root = db.path.parent
    store = _persistent_job_store(root / "job-state")
    runtime = _checkpoint_runtime(root / "checkpoint-state")
    assembler = LiteratureExtractionBusinessAssembler(
        store,
        session_id="owner",
        starter=EvidenceDBLiteratureJobStarter(db, store),
    )
    executor = LiteratureExtractionBusinessExecutor(
        store,
        ExistingLiteratureStagePlanner(),
        session_id="owner",
        checkpoint_runtime=runtime,
    )
    before = _counts(db)
    draft = assembler.assemble({"paper_id": paper_id, "force_rescan": False})
    action = _prepared_action(draft)
    with pytest.raises(BusinessActionError) as failed:
        executor.execute(
            action=action,
            ai_client=_budget_client(action, _RawProvider()),
        )
    assert failed.value.__cause__.code == "literature_commit_unavailable"
    assert _counts(db) == before


def test_stage_failure_does_not_retry_and_keeps_current_stage_prepared(evidence) -> None:
    db, paper_id = evidence
    root = db.path.parent
    store = _persistent_job_store(root / "job-state")
    runtime = _checkpoint_runtime(root / "checkpoint-state")
    ports = literature_extraction_business_ports(
        store,
        session_id="owner",
        db=db,
        finalizer=AtomicEvidenceDBFinalizer(db),
        checkpoint_runtime=runtime,
    )
    draft = ports.assembler.assemble({"paper_id": paper_id, "force_rescan": False})
    action = _prepared_action(draft)
    raw = _RawProvider(fail_at=len(draft.call_plan) + 1)
    client = _budget_client(action, raw)
    before = _counts(db)
    with pytest.raises(BusinessActionError) as failed:
        ports.executor.execute(action=action, ai_client=client)
    assert failed.value.cause_code == "literature_call_outcome_unknown"
    assert failed.value.stage == "provider_call"
    assert failed.value.next_action == "review_call_outcome"
    assert raw.calls == len(draft.call_plan) + 1
    summary = store.summary(draft.outbound["job_handle"], session_id="owner")
    assert summary["stage"] == "coverage_verification"
    assert _counts(db) == before


def test_projector_rejects_extra_or_false_commit_fields() -> None:
    projector = LiteratureExtractionBusinessProjector()
    valid = {
        "schema_version": "literature-extraction-commit-result-v2",
        "status": "completed",
        "paper": {"title": "Safe experiment", "doi": "10.1/safe"},
        "candidate_count": 1,
        "published_item_count": 1,
        "existing_item_count": 0,
        "manual_review_count": 0,
        "visual_evidence_ready": True,
        "visual_stage_status": "ready",
        "table_candidate_count": 0,
        "figure_candidate_count": 0,
        "table_structure_candidate_count": 0,
        "table_structure_manual_review_count": 0,
        "table_structure_unavailable_count": 0,
        "idempotent": False,
        "extraction_receipt": {"schema_version": "literature-extraction-receipt-v1"},
        "publication_receipt": {"schema_version": "literature-publication-receipt-v1"},
        "dataset_receipt": {"schema_version": "dataset-membership-receipt-v1"},
        "search_index": {"status": "refreshed"},
    }
    assert projector.project({"summary": valid}) == valid
    pre_table = {
        key: value
        for key, value in valid.items()
        if not key.startswith("table_structure_")
    }
    projected_pre_table = projector.project({"summary": pre_table})
    assert projected_pre_table["table_structure_candidate_count"] == 0
    assert projected_pre_table["table_structure_manual_review_count"] == 0
    assert projected_pre_table["table_structure_unavailable_count"] == 0
    legacy = {
        key: value
        for key, value in pre_table.items()
        if key != "visual_stage_status"
    }
    projected_legacy = projector.project({"summary": legacy})
    assert projected_legacy["visual_stage_status"] == "ready"
    assert projected_legacy["table_structure_candidate_count"] == 0
    with pytest.raises(BusinessActionError):
        projector.project({"summary": {**valid, "pdf_path": "/private/a.pdf"}})
    with pytest.raises(BusinessActionError):
        projector.project({"summary": {**valid, "visual_evidence_ready": False}})
    with pytest.raises(BusinessActionError):
        projector.project({
            "summary": {**valid, "table_structure_candidate_count": True}
        })
    with pytest.raises(BusinessActionError):
        projector.project({
            "summary": {**valid, "table_structure_unavailable_count": -1}
        })
    with pytest.raises(BusinessActionError):
        projector.project({
            "summary": {**valid, "paper": {**valid["paper"], "pdf_path": "/tmp/a.pdf"}}
        })
