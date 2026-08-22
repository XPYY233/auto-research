from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest

from auto_research.ai.business_actions import BusinessActionError
from auto_research.evidence.db import EvidenceDB
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
    LiteratureExtractionJobStore,
)
from auto_research.evidence.literature_extraction_stages import (
    ExistingLiteratureStagePlanner,
)


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


class _StageClient:
    def __init__(self) -> None:
        self.calls = 0

    def request_json(self, messages, **kwargs):
        self.calls += 1
        if "Verify these candidates:" in messages[1]["content"]:
            return _verification_payload(messages)
        return _extraction_payload()


class _Action:
    def __init__(self, outbound) -> None:
        self.outbound = {"payload": outbound}


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
    assert store.summary(draft.outbound["job_handle"], session_id="owner")[
        "sending_scope"
    ]["pdf_page_count"] == 1
    assert _counts(db) == before

    # Cancelling before consent/execution leaves only an expiring memory job.
    retry = assembler.assemble({"paper_id": paper_id, "force_rescan": False})
    assert retry.outbound["job_handle"] != draft.outbound["job_handle"]
    assert _counts(db) == before


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
    assert "确认重新扫描" in rejected.value.next_action
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


def test_continuations_finish_with_atomic_commit_and_strict_public_dto(evidence) -> None:
    db, paper_id = evidence
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    ports = literature_extraction_business_ports(
        store,
        session_id="owner",
        db=db,
        finalizer=AtomicEvidenceDBFinalizer(db),
    )
    request = {"paper_id": paper_id, "force_rescan": False}
    public = None
    total_calls = 0
    for expected_stage in ("coverage_gap", "coverage_verification", "completed"):
        draft = ports.assembler.assemble(request)
        client = _StageClient()
        result = ports.executor.execute(
            action=_Action(draft.outbound), ai_client=client
        )
        total_calls += client.calls
        public = ports.projector.project(result)
        if expected_stage != "completed":
            assert public["stage"] == expected_stage
            request = {"job_token": public["job_token"]}
        else:
            assert public["schema_version"] == "literature-extraction-commit-result-v2"
            assert public["status"] == "completed"
            assert public["paper"] == {"title": "Safe experiment", "doi": "10.1/safe"}
            assert public["candidate_count"] == 1
            assert public["published_item_count"] == 1
            assert public["visual_evidence_ready"] is True
            assert public["search_index"]["status"] == "refreshed"
    assert total_calls > 0
    assert _counts(db)["quality_pipeline_runs"] == 1
    assert str(db.path) not in repr(public)
    assert "job_token" not in public
    assert "pdf_sha256" not in repr(public)

    # The completed in-memory job is consumed; it cannot charge again.
    with pytest.raises(BusinessActionError):
        ports.assembler.assemble(request)


def test_final_stage_without_trusted_finalizer_never_claims_saved(evidence) -> None:
    db, paper_id = evidence
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    assembler = LiteratureExtractionBusinessAssembler(
        store,
        session_id="owner",
        starter=EvidenceDBLiteratureJobStarter(db, store),
    )
    executor = LiteratureExtractionBusinessExecutor(
        store, ExistingLiteratureStagePlanner(), session_id="owner"
    )
    request = {"paper_id": paper_id, "force_rescan": False}
    before = _counts(db)
    for _ in range(2):
        draft = assembler.assemble(request)
        result = executor.execute(
            action=_Action(draft.outbound), ai_client=_StageClient()
        )
        request = {"job_token": result["summary"]["job_token"]}
    draft = assembler.assemble(request)
    with pytest.raises(BusinessActionError) as failed:
        executor.execute(action=_Action(draft.outbound), ai_client=_StageClient())
    assert failed.value.__cause__.code == "literature_commit_unavailable"
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
        "table_candidate_count": 0,
        "figure_candidate_count": 0,
        "idempotent": False,
        "extraction_receipt": {"schema_version": "literature-extraction-receipt-v1"},
        "publication_receipt": {"schema_version": "literature-publication-receipt-v1"},
        "dataset_receipt": {"schema_version": "dataset-membership-receipt-v1"},
        "search_index": {"status": "refreshed"},
    }
    assert projector.project({"summary": valid}) == valid
    with pytest.raises(BusinessActionError):
        projector.project({"summary": {**valid, "pdf_path": "/private/a.pdf"}})
    with pytest.raises(BusinessActionError):
        projector.project({"summary": {**valid, "visual_evidence_ready": False}})
    with pytest.raises(BusinessActionError):
        projector.project({
            "summary": {**valid, "paper": {**valid["paper"], "pdf_path": "/tmp/a.pdf"}}
        })
