from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from auto_research.ai.business_actions import BusinessActionError
from auto_research.evidence.literature_extraction_business_action import (
    LiteratureExtractionBusinessAssembler,
    LiteratureExtractionBusinessExecutor,
    LiteratureExtractionBusinessProjector,
    LiteratureExtractionStageSnapshotAuthority,
)
from auto_research.evidence.literature_extraction_job import (
    FrozenExtractionStage,
    FrozenModelCall,
    LiteratureExtractionJobError,
    LiteratureExtractionJobStore,
)
from auto_research.evidence.literature_extraction_stages import ExistingLiteratureStagePlanner


class Papers:
    def __init__(self, path: Path) -> None:
        self.path = path

    def get_paper(self, paper_id: int):
        return {
            "id": paper_id,
            "title": "Safe experiment",
            "doi": "10.1/safe",
            "pdf_path": str(self.path),
        }


def make_pdf(path: Path, pages: int = 1) -> None:
    document = fitz.open()
    for index in range(pages):
        page = document.new_page()
        page.insert_text(
            (72, 72),
            f"The measured hardness was 3.2 GPa at 300 K in sample A. Page {index + 1}.",
        )
    document.save(path)
    document.close()


def extraction_payload(page: int = 1):
    return {
        "data": [{
            "value_text": "3.2",
            "meaning": "测得硬度",
            "unit": "GPa",
            "context_explanation": "样品A在300 K条件",
            "source_page": page,
            "source_locator": "Results",
            "source_excerpt": "measured hardness was 3.2 GPa at 300 K",
            "evidence_type": "measured",
            "source_precision": "exact_text",
        }],
        "findings": [],
        "pending_tasks": [],
    }


def complete_with_payloads(store, token, planner, payload_factory):
    stage = store.claim_stage(token, session_id="owner")
    payloads = [payload_factory(call) for call in stage.calls]
    return store.complete_stage(
        token,
        session_id="owner",
        completed_stage_fingerprint=stage.stage_fingerprint,
        raw_results=payloads,
        planner=planner,
    )


def test_real_planner_freezes_each_stage_before_next_model_call(tmp_path: Path) -> None:
    path = tmp_path / "paper.pdf"
    make_pdf(path)
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    planner = ExistingLiteratureStagePlanner()
    summary = store.create(Papers(path), paper_id=1, session_id="owner")

    summary = complete_with_payloads(store, summary["job_token"], planner, lambda _call: extraction_payload())
    assert summary["stage"] == "coverage_gap"
    assert summary["call_count"] == 2
    assert all(call.task == "extraction" for call in store.peek_stage(summary["job_token"], session_id="owner").calls)

    summary = complete_with_payloads(store, summary["job_token"], planner, lambda _call: extraction_payload())
    assert summary["stage"] == "coverage_verification"
    assert summary["call_count"] == 2
    assert all(call.task == "verification" for call in store.peek_stage(summary["job_token"], session_id="owner").calls)

    def verification(call):
        import json
        candidates = json.loads(call.messages[1]["content"].split("\n\nSource pages:", 1)[0].split("Verify these candidates:\n", 1)[1])
        return {"verdicts": [{
            "candidate_id": item["candidate_id"],
            "verdict": "supported",
            "reason": "matched",
        } for item in candidates]}

    summary = complete_with_payloads(store, summary["job_token"], planner, verification)
    assert summary["stage"] == "adversarial_branches"
    assert summary["call_count"] == 0
    summary = store.advance_local_stage(summary["job_token"], session_id="owner", planner=planner)
    assert summary["stage"] == "validated"
    assert summary["call_count"] == 0
    assert summary["possible_charges"] is False
    projected = LiteratureExtractionBusinessProjector().project({"summary": summary})
    assert projected["stage"] == "validated"
    assert projected["call_count"] == 0
    package = store._jobs[summary["job_token"]].validated_package
    assert package is not None
    assert package.quality_result["summary"]["dual_pass_count"] >= 1
    assert package.quality_result["coverage"] == {
        "numeric_items": True,
        "qualitative_findings": True,
        "visual_evidence_ready": False,
        "atomic_commit_ready": False,
    }


def test_malformed_or_missing_stage_results_fail_closed_and_release_claim(tmp_path: Path) -> None:
    path = tmp_path / "paper.pdf"
    make_pdf(path)
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    planner = ExistingLiteratureStagePlanner()
    summary = store.create(Papers(path), paper_id=1, session_id="owner")
    stage = store.claim_stage(summary["job_token"], session_id="owner")
    with pytest.raises(LiteratureExtractionJobError):
        store.complete_stage(
            summary["job_token"], session_id="owner",
            completed_stage_fingerprint=stage.stage_fingerprint,
            raw_results=[{"data": "malicious"} for _ in stage.calls], planner=planner,
        )
    assert store.peek_stage(summary["job_token"], session_id="owner").stage_fingerprint == stage.stage_fingerprint
    stage = store.claim_stage(summary["job_token"], session_id="owner")
    with pytest.raises(LiteratureExtractionJobError):
        store.complete_stage(
            summary["job_token"], session_id="owner",
            completed_stage_fingerprint=stage.stage_fingerprint,
            raw_results=[extraction_payload() for _ in stage.calls[:-1]], planner=planner,
        )
    assert store.peek_stage(summary["job_token"], session_id="owner").stage_fingerprint == stage.stage_fingerprint


def test_business_assembler_accepts_bounded_real_stage_under_shared_policy(tmp_path: Path) -> None:
    path = tmp_path / "long.pdf"
    make_pdf(path, pages=8)
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    summary = store.create(Papers(path), paper_id=1, session_id="owner")
    stage = store.peek_stage(summary["job_token"], session_id="owner")
    assert len(stage.calls) > 12
    assembler = LiteratureExtractionBusinessAssembler(store, session_id="owner")
    draft = assembler.assemble({"job_token": summary["job_token"]})
    assert draft.estimated_calls == len(stage.calls)
    assert draft.max_tokens == draft.outbound["task_max_tokens"]
    assert draft.max_tokens >= sum(call.max_tokens for call in stage.calls)
    assert draft.outbound["job_handle"] == summary["job_token"]
    assert "job_token" not in draft.outbound


def test_business_assembler_snapshot_and_projector_use_frozen_stage(tmp_path: Path) -> None:
    path = tmp_path / "paper.pdf"
    make_pdf(path)
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    planner = ExistingLiteratureStagePlanner()
    summary = store.create(Papers(path), paper_id=1, session_id="owner")
    draft = LiteratureExtractionBusinessAssembler(store, session_id="owner").assemble(
        {"job_token": summary["job_token"]}
    )
    assert draft.estimated_calls == summary["call_count"]
    assert draft.max_tokens == draft.outbound["task_max_tokens"]
    assert draft.max_tokens >= summary["max_token_budget"]
    assert LiteratureExtractionStageSnapshotAuthority(store).fingerprint_for(
        kind="literature_extraction_stage",
        stable_source_identity=f"literature-stage:{summary['job_token']}",
    ) == store.peek_stage(summary["job_token"], session_id="owner").stage_fingerprint

    public = LiteratureExtractionBusinessProjector().project({"summary": summary})
    assert public["stage"] == "initial_focus"
    assert str(path) not in repr(public)
    assert "pdf_sha256" not in repr(public)


def test_verification_and_localization_use_reviewed_runtime_task_slots(tmp_path: Path) -> None:
    path = tmp_path / "paper.pdf"
    make_pdf(path)
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    planner = ExistingLiteratureStagePlanner()
    summary = store.create(Papers(path), paper_id=1, session_id="owner")
    summary = complete_with_payloads(
        store, summary["job_token"], planner, lambda _call: extraction_payload()
    )
    summary = complete_with_payloads(
        store, summary["job_token"], planner, lambda _call: extraction_payload()
    )
    assert summary["stage"] == "coverage_verification"
    draft = LiteratureExtractionBusinessAssembler(store, session_id="owner").assemble(
        {"job_token": summary["job_token"]}
    )
    assert {call.task for call in draft.call_plan} == {"extraction"}


def test_business_executor_releases_claim_after_arbitrary_client_failure(tmp_path: Path) -> None:
    path = tmp_path / "paper.pdf"
    make_pdf(path)
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    planner = ExistingLiteratureStagePlanner()
    summary = store.create(Papers(path), paper_id=1, session_id="owner")
    draft = LiteratureExtractionBusinessAssembler(store, session_id="owner").assemble(
        {"job_token": summary["job_token"]}
    )

    class FailingClient:
        def request_json(self, messages, **kwargs):
            raise RuntimeError("provider failed at /private/key")

    class Action:
        outbound = {"payload": draft.outbound}
        max_calls = draft.max_calls
        max_tokens = draft.max_tokens

    with pytest.raises(RuntimeError):
        LiteratureExtractionBusinessExecutor(
            store, planner, session_id="owner"
        ).execute(action=Action(), ai_client=FailingClient())
    stage = store.peek_stage(summary["job_token"], session_id="owner")
    assert stage.name == "initial_focus"
    assert stage.stage_fingerprint == draft.outbound["stage_fingerprint"]


def test_empty_model_stage_is_only_allowed_for_local_capable_transitions() -> None:
    with pytest.raises(LiteratureExtractionJobError):
        FrozenExtractionStage.create("initial_focus", (), "x" * 64, 1.0)
    stage = FrozenExtractionStage.create("adversarial_branches", (), "x" * 64, 1.0)
    assert stage.calls == ()


def test_unpaired_branch_freezes_exact_third_review_then_manual_on_missing_verdict(tmp_path: Path) -> None:
    path = tmp_path / "paper.pdf"
    make_pdf(path)
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    planner = ExistingLiteratureStagePlanner()
    summary = store.create(Papers(path), paper_id=1, session_id="owner")

    def initial(call):
        return extraction_payload() if call.call_id.startswith("a-") else {
            "data": [], "findings": [], "pending_tasks": []
        }

    summary = complete_with_payloads(store, summary["job_token"], planner, initial)
    summary = complete_with_payloads(store, summary["job_token"], planner, initial)

    def verification(call):
        import json
        candidates = json.loads(call.messages[1]["content"].split("\n\nSource pages:", 1)[0].split("Verify these candidates:\n", 1)[1])
        return {"verdicts": [{
            "candidate_id": item["candidate_id"], "verdict": "supported", "reason": "matched",
        } for item in candidates]}

    summary = complete_with_payloads(store, summary["job_token"], planner, verification)
    assert summary["stage"] == "adversarial_branches"
    summary = store.advance_local_stage(summary["job_token"], session_id="owner", planner=planner)
    assert summary["stage"] == "third_review"
    assert summary["call_count"] == 1
    stage = store.peek_stage(summary["job_token"], session_id="owner")
    assert stage.calls[0].task == "verification"
    assert stage.calls[0].max_tokens == 5_000
    summary = complete_with_payloads(
        store, summary["job_token"], planner, lambda _call: {"verdicts": []}
    )
    assert summary["stage"] == "validated"
    records = store._jobs[summary["job_token"]].validated_package.quality_result["records"]
    assert records[0]["gate_status"] == "manual_review"


def test_completed_stage_cannot_be_replayed_and_source_change_blocks_only_finalize(tmp_path: Path) -> None:
    path = tmp_path / "paper.pdf"
    make_pdf(path)
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    planner = ExistingLiteratureStagePlanner()
    summary = store.create(Papers(path), paper_id=1, session_id="owner")
    old_stage = store.claim_stage(summary["job_token"], session_id="owner")
    summary = store.complete_stage(
        summary["job_token"], session_id="owner",
        completed_stage_fingerprint=old_stage.stage_fingerprint,
        raw_results=[extraction_payload() for _ in old_stage.calls], planner=planner,
    )
    with pytest.raises(LiteratureExtractionJobError):
        store.complete_stage(
            summary["job_token"], session_id="owner",
            completed_stage_fingerprint=old_stage.stage_fingerprint,
            raw_results=[extraction_payload() for _ in old_stage.calls], planner=planner,
        )
    # Later stages still use the captured page text; replacing the path cannot
    # change a frozen plan.  The controlled commit freshness gate rejects it.
    replacement = tmp_path / "replacement.pdf"
    make_pdf(replacement)
    replacement.replace(path)
    assert store.peek_stage(summary["job_token"], session_id="owner").name == "coverage_gap"
    with pytest.raises(LiteratureExtractionJobError) as stale:
        store.assert_source_fresh(summary["job_token"], session_id="owner")
    assert stale.value.code == "literature_source_stale"


def test_validated_package_finalization_remains_fail_closed_without_atomic_adapter(tmp_path: Path) -> None:
    path = tmp_path / "paper.pdf"
    make_pdf(path)
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    planner = ExistingLiteratureStagePlanner()
    summary = store.create(Papers(path), paper_id=1, session_id="owner")
    summary = complete_with_payloads(store, summary["job_token"], planner, lambda _call: extraction_payload())
    summary = complete_with_payloads(store, summary["job_token"], planner, lambda _call: extraction_payload())

    def verification(call):
        import json
        candidates = json.loads(call.messages[1]["content"].split("\n\nSource pages:", 1)[0].split("Verify these candidates:\n", 1)[1])
        return {"verdicts": [{
            "candidate_id": item["candidate_id"], "verdict": "supported", "reason": "matched",
        } for item in candidates]}

    summary = complete_with_payloads(store, summary["job_token"], planner, verification)
    summary = store.advance_local_stage(summary["job_token"], session_id="owner", planner=planner)
    assert summary["stage"] == "validated"
    writes = []

    class UnsafeFinalizer:
        def finalize(self, package):
            writes.append(package)
            return {"ok": True}

    with pytest.raises(LiteratureExtractionJobError) as unavailable:
        store.finalize(
            summary["job_token"], session_id="owner", finalizer=UnsafeFinalizer()
        )
    assert unavailable.value.code == "literature_commit_unavailable"
    assert writes == []
    assert store.summary(summary["job_token"], session_id="owner")["stage"] == "validated"
