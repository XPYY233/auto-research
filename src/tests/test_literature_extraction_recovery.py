from __future__ import annotations

import ast
import hashlib
import hmac
import time
from pathlib import Path

import fitz
import pytest

from auto_research.evidence.literature_checkpoint_runtime import (
    LiteratureCheckpointCall,
    LiteratureCheckpointRuntime,
)
from auto_research.evidence.literature_extraction_business_action import (
    LiteratureExtractionBusinessProjector,
)
from auto_research.evidence.literature_extraction_checkpoint_workflow import (
    _checkpoint_owner_id,
    _checkpoint_task_id,
)
from auto_research.evidence.literature_extraction_finalizer import (
    AtomicEvidenceDBFinalizer,
)
from auto_research.evidence.literature_extraction_job import (
    FrozenExtractionStage,
    LiteratureExtractionJobError,
    LiteratureExtractionJobStore,
    LiteraturePDFSnapshotAuthority,
)
from auto_research.evidence.literature_extraction_recovery import (
    LiteratureExtractionFinalizerRecovery,
    LiteratureExtractionRecoveryError,
)
from auto_research.evidence.literature_job_persistence import (
    decode_job_private_state,
)
from auto_research.evidence.literature_snapshot_blob import (
    SealedImmutablePDFBlobStore,
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


class _Sealer:
    def __init__(self, key: bytes) -> None:
        self._key = key

    def seal(self, plaintext: bytes, *, associated_data: bytes) -> bytes:
        tag = hmac.new(self._key, associated_data + plaintext, hashlib.sha256).digest()
        return tag + plaintext[::-1]

    def open(self, ciphertext: bytes, *, associated_data: bytes) -> bytes:
        tag, body = ciphertext[:32], ciphertext[32:]
        plaintext = body[::-1]
        expected = hmac.new(
            self._key, associated_data + plaintext, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(tag, expected):
            raise ValueError
        return plaintext


class _Papers:
    def __init__(self, pdf: Path) -> None:
        self._pdf = pdf

    def get_paper(self, paper_id: int):
        return {
            "id": paper_id,
            "title": "Recovery paper",
            "doi": "10.1/recovery",
            "pdf_path": str(self._pdf),
        }


class _RecordingJobStore(LiteratureExtractionJobStore):
    def __init__(self, *args, events: list[str], **kwargs) -> None:
        self.events = events
        super().__init__(*args, **kwargs)

    def restore_private_state(self, payload: bytes, **kwargs):
        result = super().restore_private_state(payload, **kwargs)
        self.events.append("restore")
        return result

    def acknowledge_finalized(self, job_token: str, *, session_id: str) -> None:
        super().acknowledge_finalized(job_token, session_id=session_id)
        self.events.append("ack")


class _RecordingRuntime(LiteratureCheckpointRuntime):
    def __init__(self, service, *, events: list[str]) -> None:
        super().__init__(service)
        self.events = events
        self.fail_complete_once = False

    def complete(self, checkpoint, **kwargs):
        if self.fail_complete_once:
            self.fail_complete_once = False
            raise LiteratureTaskCheckpointError(
                "literature_checkpoint_store_unavailable"
            )
        result = super().complete(checkpoint, **kwargs)
        self.events.append("complete")
        return result


class _RecordingFinalizer(AtomicEvidenceDBFinalizer):
    def __init__(self, *, events: list[str]) -> None:
        self.events = events
        self.calls = 0

    def finalize(self, package):
        package.pdf_snapshot.verified_bytes(package.pdf_sha256)
        self.calls += 1
        self.events.append("finalize")
        return _commit_result(idempotent=self.calls > 1)


class _RecordingProjector:
    def __init__(self, *, events: list[str], fail: bool = False) -> None:
        self.events = events
        self.fail = fail
        self._projector = LiteratureExtractionBusinessProjector()

    def project(self, result):
        self.events.append("validate")
        if self.fail:
            raise ValueError("unsafe local result")
        return self._projector.project(result)


def _commit_result(*, idempotent: bool):
    return {
        "schema_version": "literature-extraction-commit-result-v2",
        "status": "completed",
        "paper": {"title": "Recovery paper", "doi": "10.1/recovery"},
        "candidate_count": 1,
        "published_item_count": 1,
        "existing_item_count": 0,
        "manual_review_count": 0,
        "visual_evidence_ready": True,
        "table_candidate_count": 0,
        "figure_candidate_count": 0,
        "idempotent": idempotent,
        "extraction_receipt": {"schema_version": "literature-extraction-receipt-v1"},
        "publication_receipt": {"schema_version": "literature-publication-receipt-v1"},
        "dataset_receipt": {"schema_version": "dataset-membership-receipt-v1"},
        "search_index": {"status": "refreshed"},
    }


def _make_pdf(path: Path) -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Measured hardness was 3.2 GPa at 300 K.")
    document.save(path)
    document.close()


def _job_store(root: Path, *, session_key: bytes, events: list[str] | None = None):
    snapshots = LiteraturePDFSnapshotAuthority(
        blob_store=SealedImmutablePDFBlobStore(
            data_root=root / "blobs",
            sealer=_Sealer(b"blob-key"),
        )
    )
    cls = _RecordingJobStore if events is not None else LiteratureExtractionJobStore
    kwargs = {"events": events} if events is not None else {}
    return cls(session_key=session_key, snapshots=snapshots, **kwargs)


def _runtime(root: Path, *, events: list[str]):
    store = SealedSQLiteLiteratureCheckpointStore(
        data_root=root / "checkpoints",
        sealer=_Sealer(b"checkpoint-key"),
    )
    service = LiteratureTaskCheckpointService(store=store)
    return _RecordingRuntime(service, events=events)


def _validated_fixture(tmp_path: Path):
    pdf = tmp_path / "paper.pdf"
    _make_pdf(pdf)
    original = _job_store(tmp_path, session_key=b"a" * 32)
    summary = original.create(_Papers(pdf), paper_id=1, session_id="old-session")
    token = summary["job_token"]
    job = original._jobs[token]
    call = job.stage.calls[0]
    job.stage = FrozenExtractionStage.create(
        "third_review",
        (call,),
        job.snapshot.content_fingerprint,
        time.time(),
    )
    job.validated_quality_result = {}
    job.status = "validated"
    job_state = original.export_private_state(token, session_id="old-session")
    decoded = decode_job_private_state(job_state)

    events: list[str] = []
    runtime = _runtime(tmp_path, events=events)
    task_id = _checkpoint_task_id(token)
    manifest = LiteratureTaskManifest(
        task_id=task_id,
        session_digest="1" * 64,
        provider_id="deepseek",
        runtime_revision=3,
        credential_generation=2,
        task_models=(("analysis", "model-a"), ("extraction", "model-b")),
        executor_id="literature_extraction_executor",
        executor_version="v1",
        pdf_snapshot_fingerprint=decoded.snapshot["content_fingerprint"],
        max_calls=4,
        max_tokens=100_000,
        issued_at=int(time.time()) - 1,
        expires_at=int(decoded.expires_at),
    )
    checkpoint = runtime.start(
        manifest=manifest,
        job_state=job_state,
        stage="third_review",
        stage_fingerprint=job.stage.stage_fingerprint,
    )
    owner = _checkpoint_owner_id(token)
    checkpoint = runtime.recover(task_id, owner_id=owner)
    checkpoint, _ = runtime.execute_stage(
        checkpoint,
        owner_id=owner,
        stage_fingerprint=job.stage.stage_fingerprint,
        calls=(LiteratureCheckpointCall(
            "third_review", "extraction", call.call_digest, call.max_tokens
        ),),
        invoke=lambda _index: {"validated": True},
    )
    checkpoint = runtime.advance_stage(
        checkpoint,
        owner_id=owner,
        job_state=job_state,
        stage="validated",
        stage_fingerprint=job.stage.stage_fingerprint,
    )
    return token, task_id, job_state, runtime, events


def test_finalizer_only_recovery_is_zero_model_and_strictly_ordered(tmp_path: Path) -> None:
    token, task_id, _state, runtime, events = _validated_fixture(tmp_path)
    jobs = _job_store(tmp_path, session_key=b"b" * 32, events=events)
    finalizer = _RecordingFinalizer(events=events)
    recovery = LiteratureExtractionFinalizerRecovery(
        runtime=runtime,
        jobs=jobs,
        finalizer=finalizer,
        projector=_RecordingProjector(events=events),
        session_id="new-session",
    )

    result = recovery.recover(task_id=task_id, job_token=token)

    assert events[-5:] == ["restore", "finalize", "validate", "complete", "ack"]
    assert finalizer.calls == 1
    assert result["schema_version"] == "literature-extraction-recovery-result-v1"
    assert result["status"] == "completed"
    assert result["already_completed"] is False
    assert "task_id" not in repr(result)
    assert token not in repr(result)
    assert str(tmp_path) not in repr(result)
    with pytest.raises(LiteratureExtractionJobError):
        jobs.summary(token, session_id="new-session")


def test_recover_task_resolves_token_only_from_authenticated_checkpoint(
    tmp_path: Path,
) -> None:
    token, task_id, _state, runtime, events = _validated_fixture(tmp_path)
    finalizer = _RecordingFinalizer(events=events)
    recovery = LiteratureExtractionFinalizerRecovery(
        runtime=runtime,
        jobs=_job_store(tmp_path, session_key=b"b" * 32, events=events),
        finalizer=finalizer,
        projector=_RecordingProjector(events=events),
        session_id="new-session",
    )

    result = recovery.recover_task(task_id)

    assert result["status"] == "completed"
    assert finalizer.calls == 1
    assert token not in repr(result)


def test_checkpoint_complete_failure_keeps_job_and_retry_is_idempotent(
    tmp_path: Path,
) -> None:
    token, task_id, _state, runtime, events = _validated_fixture(tmp_path)
    jobs = _job_store(tmp_path, session_key=b"b" * 32, events=events)
    finalizer = _RecordingFinalizer(events=events)
    recovery = LiteratureExtractionFinalizerRecovery(
        runtime=runtime,
        jobs=jobs,
        finalizer=finalizer,
        projector=_RecordingProjector(events=events),
        session_id="new-session",
    )
    runtime.fail_complete_once = True

    with pytest.raises(LiteratureExtractionRecoveryError) as failed:
        recovery.recover(task_id=task_id, job_token=token)
    assert failed.value.code == "literature_checkpoint_store_unavailable"
    assert failed.value.stage == "checkpoint"
    assert failed.value.next_action == "retry_finalization"
    assert jobs.summary(token, session_id="new-session")["stage"] == "validated"
    assert "ack" not in events

    result = recovery.recover(task_id=task_id, job_token=token)
    assert finalizer.calls == 2
    assert result["completion"]["idempotent"] is True
    assert events[-3:] == ["validate", "complete", "ack"]


def test_local_contract_failure_never_completes_or_cleans_snapshot(tmp_path: Path) -> None:
    token, task_id, _state, runtime, events = _validated_fixture(tmp_path)
    jobs = _job_store(tmp_path, session_key=b"b" * 32, events=events)
    recovery = LiteratureExtractionFinalizerRecovery(
        runtime=runtime,
        jobs=jobs,
        finalizer=_RecordingFinalizer(events=events),
        projector=_RecordingProjector(events=events, fail=True),
        session_id="new-session",
    )

    with pytest.raises(LiteratureExtractionRecoveryError) as failed:
        recovery.recover(task_id=task_id, job_token=token)
    assert failed.value.code == "literature_recovery_result_invalid"
    assert failed.value.stage == "local_validation"
    assert jobs.summary(token, session_id="new-session")["stage"] == "validated"
    checkpoint, _ = runtime.recover_job_state(task_id)
    assert checkpoint.state != "completed"
    assert "complete" not in events
    assert "ack" not in events


def test_completed_checkpoint_only_acknowledges_and_is_repeatable(tmp_path: Path) -> None:
    token, task_id, job_state, runtime, events = _validated_fixture(tmp_path)
    checkpoint = runtime.recover(task_id, owner_id=_checkpoint_owner_id(token))
    runtime.complete(
        checkpoint,
        owner_id=_checkpoint_owner_id(token),
        job_state=job_state,
    )
    events.clear()
    jobs = _job_store(tmp_path, session_key=b"b" * 32, events=events)
    finalizer = _RecordingFinalizer(events=events)
    recovery = LiteratureExtractionFinalizerRecovery(
        runtime=runtime,
        jobs=jobs,
        finalizer=finalizer,
        projector=_RecordingProjector(events=events),
        session_id="new-session",
    )

    first = recovery.recover(task_id=task_id, job_token=token)
    second_store = _job_store(tmp_path, session_key=b"c" * 32, events=[])
    second = LiteratureExtractionFinalizerRecovery(
        runtime=runtime,
        jobs=second_store,
        finalizer=finalizer,
        projector=_RecordingProjector(events=[]),
        session_id="another-session",
    ).recover(task_id=task_id, job_token=token)

    assert first["already_completed"] is True
    assert second["already_completed"] is True
    assert first["completion"] is None
    assert finalizer.calls == 0
    assert events == ["restore", "ack"]


@pytest.mark.parametrize(
    ("task_id", "job_token"),
    [
        ("bad", "x" * 40),
        ("literature_" + "0" * 64, "x" * 40),
        ("literature_" + "0" * 64, "/private/tmp/secret"),
    ],
)
def test_invalid_identity_fails_path_free_before_finalizer(
    tmp_path: Path,
    task_id: str,
    job_token: str,
) -> None:
    _token, _task_id, _state, runtime, events = _validated_fixture(tmp_path)
    finalizer = _RecordingFinalizer(events=events)
    recovery = LiteratureExtractionFinalizerRecovery(
        runtime=runtime,
        jobs=_job_store(tmp_path, session_key=b"b" * 32),
        finalizer=finalizer,
        projector=_RecordingProjector(events=events),
        session_id="new-session",
    )

    with pytest.raises(LiteratureExtractionRecoveryError) as failed:
        recovery.recover(task_id=task_id, job_token=job_token)
    public = failed.value.public_dict()
    assert failed.value.code == "literature_recovery_invalid"
    assert finalizer.calls == 0
    assert "/private" not in repr(public)
    assert job_token not in repr(public)


def test_recovery_module_has_no_ai_or_prepared_action_dependency() -> None:
    source = Path(
        "src/auto_research/evidence/literature_extraction_recovery.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert not any(
        module == "auto_research.ai" or module.startswith("auto_research.ai.")
        for module in imported_modules
    )
    assert not any(
        isinstance(node, ast.Name) and "PreparedAction" in node.id
        for node in ast.walk(tree)
    )
    assert "request_json" not in source
    assert "request_tool_message" not in source
