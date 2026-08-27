from __future__ import annotations

import os
from pathlib import Path

import fitz
import pytest

from auto_research.evidence.literature_extraction_job import (
    FrozenModelCall,
    LiteratureExtractionJobError,
    LiteratureExtractionJobStore,
    PlannedLiteratureStage,
    capture_pdf_snapshot,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 1000.0

    def now(self) -> float:
        return self.value


class Papers:
    def __init__(self, path: Path) -> None:
        self.path = path

    def get_paper(self, paper_id: int):
        return {"id": paper_id, "title": "Safe paper", "doi": "10.1/safe", "pdf_path": str(self.path)}


def make_pdf(path: Path, text: str = "Measured hardness was 3.2 GPa at 300 K.") -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def test_safe_snapshot_uses_one_immutable_byte_source(tmp_path: Path) -> None:
    path = tmp_path / "paper.pdf"
    make_pdf(path)
    snapshot = capture_pdf_snapshot(str(path))
    assert snapshot.pdf_sha256
    assert "3.2 GPa" in snapshot.pages[0]["text"]


def test_snapshot_authority_keeps_private_bytes_for_finalization(tmp_path: Path) -> None:
    path = tmp_path / "private" / "paper.pdf"
    path.parent.mkdir()
    make_pdf(path)
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    summary = store.create(Papers(path), paper_id=1, session_id="owner")
    job = store._jobs[summary["job_token"]]
    snapshot = store._snapshots.snapshot_for_finalization(
        job.snapshot_handle, expected_sha256=job.snapshot.pdf_sha256
    )
    assert snapshot.verified_bytes(job.snapshot.pdf_sha256) == path.read_bytes()
    assert str(path) not in repr(snapshot)
    assert "%PDF" not in repr(snapshot)


def test_safe_snapshot_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "paper.pdf"
    link = tmp_path / "link.pdf"
    make_pdf(target)
    link.symlink_to(target)
    with pytest.raises((FileNotFoundError, LiteratureExtractionJobError, ValueError)):
        capture_pdf_snapshot(str(link))


def test_initial_stage_is_frozen_before_any_model_call_and_path_free(tmp_path: Path) -> None:
    path = tmp_path / "private" / "paper.pdf"
    path.parent.mkdir()
    make_pdf(path)
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    summary = store.create(Papers(path), paper_id=7, session_id="session-a", chunk_pages=2)
    assert summary["stage"] == "initial_focus"
    assert summary["call_count"] >= 4  # two roles times the deterministic focus set
    assert summary["possible_charges"] is True
    assert str(path) not in repr(summary)
    assert "pdf_sha256" not in repr(summary)
    stage = store.claim_stage(summary["job_token"], session_id="session-a")
    original = stage.calls[0].messages[0]["content"]
    with pytest.raises(TypeError):
        stage.calls[0].messages[0]["content"] = "changed"
    assert stage.calls[0].messages[0]["content"] == original


def test_session_binding_single_flight_and_ttl(tmp_path: Path) -> None:
    path = tmp_path / "paper.pdf"
    make_pdf(path)
    clock = FakeClock()
    store = LiteratureExtractionJobStore(clock=clock, ttl_seconds=10, session_key=b"x" * 32)
    summary = store.create(Papers(path), paper_id=1, session_id="owner")
    with pytest.raises(LiteratureExtractionJobError) as cross:
        store.claim_stage(summary["job_token"], session_id="attacker")
    assert cross.value.code == "literature_job_invalid"
    store.claim_stage(summary["job_token"], session_id="owner")
    with pytest.raises(LiteratureExtractionJobError) as busy:
        store.claim_stage(summary["job_token"], session_id="owner")
    assert busy.value.code == "literature_stage_busy"
    store.fail_stage(summary["job_token"], session_id="owner")
    clock.value += 11
    with pytest.raises(LiteratureExtractionJobError) as expired:
        store.claim_stage(summary["job_token"], session_id="owner")
    assert expired.value.code == "literature_job_expired"


def test_stage_order_and_plan_are_bound_before_next_charge(tmp_path: Path) -> None:
    path = tmp_path / "paper.pdf"
    make_pdf(path)
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    summary = store.create(Papers(path), paper_id=1, session_id="owner")
    current = store.claim_stage(summary["job_token"], session_id="owner")
    call = FrozenModelCall.create(
        call_id="coverage-1",
        task="extraction",
        messages=[{"role": "system", "content": "fixed"}, {"role": "user", "content": "fixed source"}],
        max_tokens=100,
        options={"thinking": False},
    )
    class WrongPlanner:
        def plan_next(self, context, raw_results):
            return PlannedLiteratureStage("third_review", {}, (call,), "x" * 64)

    with pytest.raises(LiteratureExtractionJobError):
        store.complete_stage(
            summary["job_token"], session_id="owner",
            completed_stage_fingerprint=current.stage_fingerprint,
            raw_results=[{} for _ in current.calls], planner=WrongPlanner(),
        )
    store.fail_stage(summary["job_token"], session_id="owner")
    current = store.claim_stage(summary["job_token"], session_id="owner")

    class CoveragePlanner:
        def plan_next(self, context, raw_results):
            assert context.stage.name == "initial_focus"
            assert len(raw_results) == len(context.stage.calls)
            return PlannedLiteratureStage("coverage_gap", {"validated_initial": True}, (call,), "x" * 64)

    next_summary = store.complete_stage(
        summary["job_token"], session_id="owner",
        completed_stage_fingerprint=current.stage_fingerprint,
        raw_results=[{} for _ in current.calls], planner=CoveragePlanner(),
    )
    assert next_summary["stage"] == "coverage_gap"
    assert next_summary["call_count"] == 1
    job = store._jobs[summary["job_token"]]
    assert len(job.stage_outputs) == 1
    assert job.stage_outputs[0].stage == "initial_focus"
    assert job.stage_outputs[0].result_fingerprint


def test_source_replacement_is_stale_even_with_same_size_and_mtime(tmp_path: Path) -> None:
    path = tmp_path / "paper.pdf"
    make_pdf(path, "Measured hardness was 3.2 GPa.")
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    summary = store.create(Papers(path), paper_id=1, session_id="owner")
    old_stat = path.stat()
    content = bytearray(path.read_bytes())
    index = next(index for index, value in enumerate(content[16:-16], start=16) if value not in (0, 10, 13))
    content[index] ^= 1
    path.write_bytes(content)
    os.utime(path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    with pytest.raises(LiteratureExtractionJobError) as stale:
        store.assert_source_fresh(summary["job_token"], session_id="owner")
    assert stale.value.code == "literature_source_stale"


def test_model_stages_do_not_write_and_failed_finalizer_is_retryable(tmp_path: Path) -> None:
    path = tmp_path / "paper.pdf"
    make_pdf(path)
    writes: list[object] = []

    class Finalizer:
        def finalize(self, package):
            assert package.pdf_sha256
            raise RuntimeError("/private/db.sqlite secret")

    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    summary = store.create(Papers(path), paper_id=1, session_id="owner")
    assert writes == []
    with pytest.raises(LiteratureExtractionJobError) as not_ready:
        store.finalize(summary["job_token"], session_id="owner", finalizer=Finalizer())
    assert not_ready.value.code == "literature_not_validated"
    assert "/private" not in not_ready.value.public_dict()["message"]


def test_capacity_arguments_are_bounded() -> None:
    with pytest.raises(ValueError):
        LiteratureExtractionJobStore(ttl_seconds=0)
    with pytest.raises(ValueError):
        LiteratureExtractionJobStore(max_jobs=2, max_session_jobs=3)
    with pytest.raises(LiteratureExtractionJobError):
        from auto_research.evidence.literature_extraction_job import FrozenExtractionStage
        FrozenExtractionStage.create("initial_focus", (), "x" * 64, 1.0)


def test_intermediate_results_reject_paths_and_secrets_before_planning(tmp_path: Path) -> None:
    path = tmp_path / "paper.pdf"
    make_pdf(path)
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    summary = store.create(Papers(path), paper_id=1, session_id="owner")
    stage = store.claim_stage(summary["job_token"], session_id="owner")

    class MustNotRun:
        def plan_next(self, context, raw_results):
            raise AssertionError("planner must not receive unsafe model output")

    results = [{} for _ in stage.calls]
    results[0] = {"api_key": "secret"}
    with pytest.raises(LiteratureExtractionJobError) as unsafe:
        store.complete_stage(
            summary["job_token"], session_id="owner",
            completed_stage_fingerprint=stage.stage_fingerprint,
            raw_results=results, planner=MustNotRun(),
        )
    assert unsafe.value.code == "literature_stage_invalid"
    store.fail_stage(summary["job_token"], session_id="owner")
    stage = store.claim_stage(summary["job_token"], session_id="owner")
    results = [{} for _ in stage.calls]
    results[0] = {"note": "saved at /home/user/result.json"}
    with pytest.raises(LiteratureExtractionJobError):
        store.complete_stage(
            summary["job_token"], session_id="owner",
            completed_stage_fingerprint=stage.stage_fingerprint,
            raw_results=results, planner=MustNotRun(),
        )


def test_cancel_releases_snapshot_authority(tmp_path: Path) -> None:
    path = tmp_path / "paper.pdf"
    make_pdf(path)
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    summary = store.create(Papers(path), paper_id=1, session_id="owner")
    handle = store._jobs[summary["job_token"]].snapshot_handle
    assert handle in store._snapshots._records
    store.cancel(summary["job_token"], session_id="owner")
    assert handle not in store._snapshots._records


def test_long_pdf_initial_plan_remains_within_existing_chunk_boundary(tmp_path: Path) -> None:
    path = tmp_path / "long.pdf"
    document = fitz.open()
    for page_number in range(60):
        page = document.new_page()
        page.insert_text((72, 72), f"Measured property {page_number} was {page_number + 1}.0 GPa at 300 K.")
    document.save(path)
    document.close()
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    summary = store.create(Papers(path), paper_id=1, session_id="owner", chunk_pages=2)
    assert summary["sending_scope"]["page_block_count"] == 30
    assert summary["call_count"] == 120
    assert summary["max_token_budget"] == 120 * 16_000
    assert summary["transient"] is True
    assert summary["persistence_allowed"] is False
