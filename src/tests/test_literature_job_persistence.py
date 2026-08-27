from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import fitz
import pytest

import auto_research.evidence.literature_job_persistence as persistence_module
from auto_research.evidence.literature_extraction_job import (
    DEFAULT_TTL_SECONDS,
    FrozenExtractionStage,
    MAX_EXECUTION_LEASE_SECONDS,
    MAX_JOB_TTL_SECONDS,
    LiteratureExtractionJobError,
    LiteratureExtractionJobStore,
    LiteraturePDFSnapshotAuthority,
)
from auto_research.evidence.literature_snapshot_blob import SealedImmutablePDFBlobStore


SESSION_KEY = b"persistence-session-key".ljust(32, b"-")


class FakeClock:
    def __init__(self, value: float = 10_000.0) -> None:
        self.value = value

    def now(self) -> float:
        return self.value


class Papers:
    def __init__(self, path: Path) -> None:
        self.path = path

    def get_paper(self, paper_id: int):
        return {
            "id": paper_id,
            "title": "Persistent evidence paper",
            "doi": "10.1/persistent",
            "pdf_path": str(self.path),
        }


class FakeSealer:
    def __init__(self, key: bytes = b"sealed-snapshot-test-key") -> None:
        self.key = key

    def seal(self, plaintext: bytes, *, associated_data: bytes) -> bytes:
        tag = hmac.new(self.key, associated_data + plaintext, hashlib.sha256).digest()
        return tag + plaintext[::-1]

    def open(self, ciphertext: bytes, *, associated_data: bytes) -> bytes:
        if len(ciphertext) < 32:
            raise ValueError
        tag, body = ciphertext[:32], ciphertext[32:]
        plaintext = body[::-1]
        expected = hmac.new(self.key, associated_data + plaintext, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise ValueError
        return plaintext


def make_pdf(path: Path) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Measured hardness was 3.2 GPa at 300 K.")
    document.save(path)
    document.close()
    return path.read_bytes()


def make_store(
    blob_root: Path,
    clock: FakeClock,
    *,
    ttl_seconds: int | None = None,
) -> LiteratureExtractionJobStore:
    blob_store = SealedImmutablePDFBlobStore(data_root=blob_root, sealer=FakeSealer())
    snapshots = LiteraturePDFSnapshotAuthority(blob_store=blob_store)
    options = {}
    if ttl_seconds is not None:
        options["ttl_seconds"] = ttl_seconds
    return LiteratureExtractionJobStore(
        clock=clock,
        session_key=SESSION_KEY,
        snapshots=snapshots,
        **options,
    )


def assert_error(code: str, callback) -> None:
    with pytest.raises(LiteratureExtractionJobError) as caught:
        callback()
    assert caught.value.code == code
    public = repr(caught.value.public_dict())
    assert "/" not in public
    assert "pdfsnap_" not in public


def test_cross_store_restore_is_deterministic_path_free_and_blob_backed(
    tmp_path: Path,
) -> None:
    source = tmp_path / "private-source-name.pdf"
    pdf_bytes = make_pdf(source)
    clock = FakeClock()
    blob_root = tmp_path / "sealed-blobs"
    first = make_store(blob_root, clock)
    summary = first.create(Papers(source), paper_id=7, session_id="owner")
    token = summary["job_token"]
    job = first._jobs[token]
    assert job.expires_at - job.issued_at == MAX_JOB_TTL_SECONDS
    snapshot_record = first._snapshots._records[job.snapshot_handle]
    assert snapshot_record.source_path is None
    assert snapshot_record.snapshot is None

    payload = first.export_private_state(token, session_id="owner")
    assert payload == first.export_private_state(token, session_id="owner")
    assert pdf_bytes not in payload
    assert str(source).encode() not in payload
    assert b"pdf_path" not in payload
    assert b"api_key" not in payload
    state = json.loads(payload)
    assert state["snapshot"]["pdf_sha256"] == hashlib.sha256(pdf_bytes).hexdigest()
    assert len(state["snapshot"]["content_fingerprint"]) == 64

    source.unlink()
    restored = make_store(blob_root, clock)
    restored_summary = restored.restore_private_state(payload, session_id="owner")
    assert restored_summary["job_token"] == token
    assert restored.peek_stage(token, session_id="owner").stage_fingerprint == (
        first.peek_stage(token, session_id="owner").stage_fingerprint
    )
    restored.assert_source_fresh(token, session_id="owner")
    restored_job = restored._jobs[token]
    snapshot = restored._snapshots.snapshot_for_finalization(
        restored_job.snapshot_handle,
        expected_sha256=restored_job.snapshot.pdf_sha256,
    )
    assert snapshot.verified_bytes(restored_job.snapshot.pdf_sha256) == pdf_bytes


def test_restore_rejects_tamper_wrong_session_expiry_and_duplicate(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    make_pdf(source)
    clock = FakeClock()
    blob_root = tmp_path / "sealed-blobs"
    first = make_store(blob_root, clock)
    summary = first.create(Papers(source), paper_id=1, session_id="owner")
    payload = first.export_private_state(summary["job_token"], session_id="owner")

    tampered = json.loads(payload)
    tampered["paper"]["title"] = "changed"
    assert_error(
        "literature_job_state_invalid",
        lambda: make_store(blob_root, clock).restore_private_state(
            json.dumps(tampered).encode(), session_id="owner"
        ),
    )
    assert_error(
        "literature_job_invalid",
        lambda: make_store(blob_root, clock).restore_private_state(
            payload, session_id="different-session"
        ),
    )

    rebound = LiteratureExtractionJobStore(
        clock=clock,
        session_key=b"new-process-session-key".ljust(32, b"-"),
        snapshots=LiteraturePDFSnapshotAuthority(
            blob_store=SealedImmutablePDFBlobStore(
                data_root=blob_root,
                sealer=FakeSealer(),
            )
        ),
    )
    rebound.restore_private_state(
        payload,
        session_id="new-desktop-session",
        allow_authenticated_session_rebind=True,
    )
    rebound.peek_stage(summary["job_token"], session_id="new-desktop-session")

    restored = make_store(blob_root, clock)
    restored.restore_private_state(payload, session_id="owner")
    assert_error(
        "literature_job_duplicate",
        lambda: restored.restore_private_state(payload, session_id="owner"),
    )

    expired_clock = FakeClock(clock.value + MAX_JOB_TTL_SECONDS + 1)
    assert_error(
        "literature_job_expired",
        lambda: make_store(blob_root, expired_clock).restore_private_state(
            payload, session_id="owner"
        ),
    )


def test_snapshot_identity_tamper_fails_even_with_recomputed_state_digest(
    tmp_path: Path,
) -> None:
    source = tmp_path / "paper.pdf"
    make_pdf(source)
    clock = FakeClock()
    blob_root = tmp_path / "sealed-blobs"
    first = make_store(blob_root, clock)
    summary = first.create(Papers(source), paper_id=1, session_id="owner")
    state = json.loads(first.export_private_state(summary["job_token"], session_id="owner"))
    state["snapshot"]["pdf_sha256"] = "0" * 64
    context = {
        "pdf_sha256": state["snapshot"]["pdf_sha256"],
        "page_count": state["snapshot"]["page_count"],
        "pages": state["snapshot"]["pages"],
    }
    state["snapshot"]["content_fingerprint"] = hashlib.sha256(
        json.dumps(
            context, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    state_without_digest = {key: value for key, value in state.items() if key != "state_fingerprint"}
    state["state_fingerprint"] = hashlib.sha256(
        json.dumps(
            state_without_digest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert_error(
        "literature_job_state_invalid",
        lambda: make_store(blob_root, clock).restore_private_state(
            json.dumps(
                state, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode(),
            session_id="owner",
        ),
    )


def test_claimed_job_restores_as_unclaimed_prepared_and_active_claim_is_not_cleaned(
    tmp_path: Path,
) -> None:
    source = tmp_path / "paper.pdf"
    make_pdf(source)
    clock = FakeClock()
    blob_root = tmp_path / "sealed-blobs"
    first = make_store(blob_root, clock, ttl_seconds=60)
    summary = first.create(Papers(source), paper_id=1, session_id="owner")
    token = summary["job_token"]
    first.claim_stage(token, session_id="owner")
    payload = first.export_private_state(token, session_id="owner")

    restored = make_store(blob_root, clock, ttl_seconds=60)
    restored.restore_private_state(payload, session_id="owner")
    restored_job = restored._jobs[token]
    assert restored_job.status == "prepared"
    assert restored_job.claimed is False
    restored.peek_stage(token, session_id="owner")

    clock.value = first._jobs[token].expires_at + 1
    first._cleanup(clock.value)
    assert token in first._jobs
    clock.value = first._jobs[token].claim_expires_at + 1
    first._cleanup(clock.value)
    assert token not in first._jobs


def test_restore_preserves_explicit_24_hour_expiry_across_default_store(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    make_pdf(source)
    clock = FakeClock()
    blob_root = tmp_path / "sealed-blobs"
    first = make_store(blob_root, clock, ttl_seconds=MAX_JOB_TTL_SECONDS)
    summary = first.create(Papers(source), paper_id=1, session_id="owner")
    token = summary["job_token"]
    original_expiry = first._jobs[token].expires_at
    payload = first.export_private_state(token, session_id="owner")

    clock.value += 12 * 60 * 60
    restored = make_store(blob_root, clock)
    restored.restore_private_state(payload, session_id="owner")
    assert restored._jobs[token].expires_at == original_expiry
    assert original_expiry - restored._jobs[token].issued_at == MAX_JOB_TTL_SECONDS


def test_memory_default_ttl_remains_the_legacy_thirty_minutes(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    make_pdf(source)
    clock = FakeClock()
    store = LiteratureExtractionJobStore(clock=clock, session_key=SESSION_KEY)
    summary = store.create(Papers(source), paper_id=1, session_id="owner")
    job = store._jobs[summary["job_token"]]
    assert job.expires_at - job.issued_at == DEFAULT_TTL_SECONDS == 30 * 60


def test_validated_recovery_keeps_quality_and_finalization_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    pdf_bytes = make_pdf(source)
    clock = FakeClock()
    blob_root = tmp_path / "sealed-blobs"
    first = make_store(blob_root, clock)
    summary = first.create(Papers(source), paper_id=1, session_id="owner")
    token = summary["job_token"]
    job = first._jobs[token]
    job.stage = FrozenExtractionStage.create(
        "third_review", (), job.snapshot.content_fingerprint, clock.now()
    )
    job.status = "validated"
    job.validated_quality_result = {"quality_status": "passed"}
    payload = first.export_private_state(token, session_id="owner")

    restored = make_store(blob_root, clock)
    restored.restore_private_state(payload, session_id="owner")
    job = restored._jobs[token]
    assert job.status == "validated"
    assert job.validated_package is None
    assert dict(job.validated_quality_result) == {"quality_status": "passed"}
    snapshot = restored._snapshots.snapshot_for_finalization(
        job.snapshot_handle, expected_sha256=job.snapshot.pdf_sha256
    )
    assert snapshot.verified_bytes(job.snapshot.pdf_sha256) == pdf_bytes


def test_private_state_size_limit_and_memory_mode_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "paper.pdf"
    make_pdf(source)
    memory_store = LiteratureExtractionJobStore(session_key=SESSION_KEY)
    summary = memory_store.create(Papers(source), paper_id=1, session_id="owner")
    assert_error(
        "literature_persistence_unavailable",
        lambda: memory_store.export_private_state(summary["job_token"], session_id="owner"),
    )

    persistent = make_store(tmp_path / "sealed-blobs", FakeClock())
    summary = persistent.create(Papers(source), paper_id=2, session_id="owner")
    monkeypatch.setattr(persistence_module, "MAX_PRIVATE_STATE_BYTES", 128)
    assert_error(
        "literature_job_state_too_large",
        lambda: persistent.export_private_state(summary["job_token"], session_id="owner"),
    )


def test_restore_rejects_expiry_beyond_24_hour_maximum(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    make_pdf(source)
    clock = FakeClock()
    blob_root = tmp_path / "sealed-blobs"
    first = make_store(blob_root, clock)
    summary = first.create(Papers(source), paper_id=1, session_id="owner")
    state = json.loads(first.export_private_state(summary["job_token"], session_id="owner"))
    state["expires_at"] = state["issued_at"] + MAX_JOB_TTL_SECONDS + 1
    unsigned = {key: value for key, value in state.items() if key != "state_fingerprint"}
    state["state_fingerprint"] = hashlib.sha256(
        json.dumps(
            unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    assert_error(
        "literature_job_state_invalid",
        lambda: make_store(blob_root, clock).restore_private_state(
            json.dumps(
                state, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode(),
            session_id="owner",
        ),
    )


def test_claim_lease_constant_remains_bounded() -> None:
    assert MAX_EXECUTION_LEASE_SECONDS == 10 * 60
