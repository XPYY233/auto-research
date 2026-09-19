from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import fitz
import pytest

from auto_research.evidence.db import EvidenceDB
from auto_research.evidence.literature_extraction_finalizer import AtomicEvidenceDBFinalizer
from auto_research.evidence.literature_extraction_job import (
    ImmutablePDFSnapshot,
    LiteratureExtractionJobError,
    LiteratureExtractionJobStore,
    ValidatedLiteraturePackage,
)
from auto_research.evidence.table_structure_store import (
    TableStructureStore,
    TableStructureStoreError,
)


def make_pdf(path: Path, text: str = "The measured hardness was 3.2 GPa at 300 K.") -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def make_table_pdf(path: Path) -> None:
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    for x in (72, 220, 368):
        page.draw_line((x, 100), (x, 180), color=(0, 0, 0), width=1)
    for y in (100, 140, 180):
        page.draw_line((72, y), (368, y), color=(0, 0, 0), width=1)
    page.insert_text((84, 125), "Temperature")
    page.insert_text((235, 125), "Hardness")
    page.insert_text((84, 165), "300 K")
    page.insert_text((235, 165), "3.2 GPa")
    document.save(path)
    document.close()


def table_spec() -> list[dict[str, object]]:
    return [
        {
            "asset_type": "table",
            "number": 1,
            "page": 1,
            "bbox": [70.0, 98.0, 370.0, 182.0],
            "caption": "Table 1. Measured hardness.",
            "display_name": "Measured hardness",
            "physical_quantities": [],
            "variables": {},
            "materials": [],
            "conditions": "300 K",
            "methods": "indentation",
            "context": "",
            "tags": [],
            "source_context": "",
        }
    ]


def data_candidate() -> dict:
    return {
        "candidate_id": "c01p1-0000",
        "value_text": "3.2",
        "meaning": "测得硬度",
        "unit": "GPa",
        "context_explanation": "样品A在300 K条件；纳米压痕",
        "source_page": 1,
        "source_locator": "Results",
        "source_excerpt": "measured hardness was 3.2 GPa at 300 K",
        "evidence_type": "measured",
        "source_precision": "exact_text",
        "local_evidence": {"passed": True},
        "ai_verification": {"verdict": "supported"},
    }


def finding_candidate() -> dict:
    return {
        "candidate_id": "f01p1-0000",
        "finding_text": "未观察到空洞",
        "meaning": "空洞观察结果",
        "context_explanation": "样品A在辐照后经TEM观察",
        "source_page": 1,
        "source_locator": "Results",
        "source_excerpt": "no voids were observed",
        "source_precision": "exact_text",
        "local_evidence": {"passed": True},
        "ai_verification": {"verdict": "supported"},
    }


def record(key: str, entity_type: str, gate: str, candidate: dict) -> dict:
    return {
        "entity_type": entity_type,
        "candidate_key": key,
        "chosen_source": "extractor_a",
        "candidate": candidate,
        "alternate": None,
        "agreement_score": 91.0 if gate == "dual_pass" else 0.0,
        "factuality_score": 100.0,
        "completeness_score": 100.0,
        "evidence_score": 100.0,
        "overall_score": 95.0 if gate != "manual_review" else 70.0,
        "gate_status": gate,
        "gate_reason": "quality gate",
    }


def package(paper_id: int, title: str, doi: str, pdf_path: Path) -> ValidatedLiteraturePackage:
    snapshot = ImmutablePDFSnapshot.create(pdf_path.read_bytes())
    records = [
        record("data-pass", "data", "dual_pass", data_candidate()),
        record("finding-pass", "finding", "third_pass", finding_candidate()),
        record("data-manual", "data", "manual_review", {
            **data_candidate(), "candidate_id": "c01p1-0001", "value_text": "4.1",
        }),
    ]
    result = {
        "schema_version": "literature-extraction-validated-v1",
        "coverage": {
            "numeric_items": True,
            "qualitative_findings": True,
            "visual_evidence_ready": False,
            "atomic_commit_ready": False,
        },
        "records": records,
        "summary": {
            "candidate_count": 3,
            "dual_pass_count": 1,
            "third_pass_count": 1,
            "manual_review_count": 1,
            "quality_threshold": 85.0,
        },
    }
    return ValidatedLiteraturePackage(
        paper_id=paper_id,
        paper=MappingProxyType({"title": title, "doi": doi}),
        snapshot_fingerprint="a" * 64,
        pdf_sha256=snapshot.sha256,
        pdf_snapshot=snapshot,
        experiment_profile=MappingProxyType({"paper_mode": "experimental"}),
        quality_result=MappingProxyType(result),
    )


@pytest.fixture
def evidence(tmp_path: Path):
    db = EvidenceDB(tmp_path / "evidence.sqlite")
    db.init()
    pdf = tmp_path / "paper.pdf"
    make_pdf(pdf)
    paper_id = db.upsert_paper(
        title="Safe experiment", doi="10.1/safe", pdf_path=str(pdf),
    )
    return db, pdf, paper_id


def counts(db: EvidenceDB) -> dict[str, int]:
    with db.connect() as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("quality_pipeline_runs", "quality_candidates", "data_items", "data_versions")
        }


def test_atomic_success_publishes_only_quality_passed_text_records(evidence) -> None:
    db, pdf, paper_id = evidence
    result = AtomicEvidenceDBFinalizer(db).finalize(
        package(paper_id, "Safe experiment", "10.1/safe", pdf)
    )
    assert result["schema_version"] == "literature-extraction-commit-result-v2"
    assert result["status"] == "completed"
    assert result["candidate_count"] == 3
    assert result["published_item_count"] == 2
    assert result["visual_evidence_ready"] is False
    assert result["visual_stage_status"] == "not_found"
    assert result["extraction_receipt"]["schema_version"] == "literature-extraction-receipt-v1"
    assert result["publication_receipt"]["schema_version"] == "literature-publication-receipt-v1"
    assert result["dataset_receipt"]["schema_version"] == "dataset-membership-receipt-v1"
    assert counts(db) == {
        "quality_pipeline_runs": 1,
        "quality_candidates": 3,
        "data_items": 2,
        "data_versions": 2,
    }
    with db.connect() as connection:
        manual = connection.execute(
            "SELECT published_item_id FROM quality_candidates WHERE gate_status='manual_review'"
        ).fetchone()
        assert manual["published_item_id"] is None
        summary = json.loads(connection.execute(
            "SELECT summary_json FROM quality_pipeline_runs"
        ).fetchone()["summary_json"])
    assert len(summary["commit_fingerprint"]) == 64
    assert summary["visual_stage_completed"] is True
    assert summary["visual_evidence_ready"] is False
    assert summary["visual_stage_status"] == "not_found"


def test_repeat_is_idempotent_inside_transaction_lock(evidence) -> None:
    db, pdf, paper_id = evidence
    finalizer = AtomicEvidenceDBFinalizer(db)
    payload = package(paper_id, "Safe experiment", "10.1/safe", pdf)
    first = finalizer.finalize(payload)
    before = counts(db)
    second = finalizer.finalize(payload)
    assert first["visual_stage_status"] == "not_found"
    assert second["visual_stage_status"] == "not_found"
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert counts(db) == before


def test_table_structure_is_atomic_idempotent_and_receipted(
    evidence, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, pdf, paper_id = evidence
    make_table_pdf(pdf)
    payload = package(paper_id, "Safe experiment", "10.1/safe", pdf)

    from auto_research.evidence import literature_visual_stage as visual_stage

    monkeypatch.setattr(visual_stage, "_target_specs", lambda _paper: table_spec())
    finalizer = AtomicEvidenceDBFinalizer(db)
    first = finalizer.finalize(payload)
    second = finalizer.finalize(payload)
    assert first["table_structure_candidate_count"] == 1
    assert first["table_structure_manual_review_count"] == 0
    assert first["table_structure_unavailable_count"] == 0
    assert first["publication_receipt"]["table_structure_candidate_count"] == 1
    assert second["idempotent"] is True
    with db.connect() as connection:
        asset_id = int(connection.execute("SELECT id FROM visual_assets").fetchone()[0])
        assert connection.execute(
            "SELECT COUNT(*) FROM table_structure_versions"
        ).fetchone()[0] == 1
        summary = json.loads(connection.execute(
            "SELECT summary_json FROM quality_pipeline_runs"
        ).fetchone()[0])
    assert summary["table_structure_candidate_count"] == 1
    with pytest.raises(TableStructureStoreError) as pending:
        TableStructureStore(db).latest(visual_asset_id=asset_id)
    assert pending.value.code == "table_structure_store_pending"


def test_table_structure_store_failure_rolls_back_entire_finalization(
    evidence, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, pdf, paper_id = evidence
    make_table_pdf(pdf)
    payload = package(paper_id, "Safe experiment", "10.1/safe", pdf)

    from auto_research.evidence import literature_visual_stage as visual_stage

    monkeypatch.setattr(visual_stage, "_target_specs", lambda _paper: table_spec())

    def fail_save(self, **_kwargs):
        raise TableStructureStoreError("table_structure_store_unavailable")

    monkeypatch.setattr(TableStructureStore, "save_candidate_in_transaction", fail_save)
    with pytest.raises(LiteratureExtractionJobError) as failure:
        AtomicEvidenceDBFinalizer(db).finalize(payload)
    assert failure.value.code == "literature_table_structure_store_failed"
    with db.connect() as connection:
        for table in (
            "quality_pipeline_runs",
            "quality_candidates",
            "data_items",
            "data_versions",
            "visual_assets",
            "table_structure_versions",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert not [path for path in (db.path.parent / "visual_assets").rglob("*.png")]


def test_same_process_concurrent_commit_is_single_flight_and_idempotent(evidence) -> None:
    db, pdf, paper_id = evidence
    finalizer = AtomicEvidenceDBFinalizer(db)
    payload = package(paper_id, "Safe experiment", "10.1/safe", pdf)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: finalizer.finalize(payload), range(2)))
    assert sorted(result["idempotent"] for result in results) == [False, True]
    assert counts(db) == {
        "quality_pipeline_runs": 1,
        "quality_candidates": 3,
        "data_items": 2,
        "data_versions": 2,
    }


@pytest.mark.parametrize("fault_stage", ["after_run", "after_candidate", "before_commit"])
def test_any_write_failure_rolls_back_every_new_row(evidence, fault_stage: str) -> None:
    db, pdf, paper_id = evidence
    with db.connect() as connection:
        item = connection.execute(
            "INSERT INTO data_items(paper_id,stable_key,origin_type,created_at) VALUES(?,?,?,?)",
            (paper_id, "existing_manual", "manual", "2026-01-01T00:00:00+00:00"),
        )
        connection.execute(
            """INSERT INTO data_versions(
               item_id,version_no,value_text,meaning,unit,article_title,doi,
               context_explanation,source_page,source_locator,source_excerpt,
               editor,edit_note,review_action,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                int(item.lastrowid), 0, "8.0", "既有人工证据", "GPa",
                "Safe experiment", "10.1/safe", "既有记录", 1, "Manual", "8.0 GPa",
                "Researcher", None, "manual", "2026-01-01T00:00:00+00:00",
            ),
        )
    before = counts(db)

    def fail(stage: str) -> None:
        if stage == fault_stage:
            raise RuntimeError("injected failure /private/db.sqlite")

    with pytest.raises(RuntimeError):
        AtomicEvidenceDBFinalizer(db, fault_injector=fail).finalize(
            package(paper_id, "Safe experiment", "10.1/safe", pdf)
        )
    assert counts(db) == before
    with db.connect() as connection:
        row = connection.execute(
            "SELECT stable_key,origin_type FROM data_items"
        ).fetchone()
    assert dict(row) == {"stable_key": "existing_manual", "origin_type": "manual"}


def test_inconsistent_gate_summary_is_rejected_before_write(evidence) -> None:
    db, pdf, paper_id = evidence
    source = package(paper_id, "Safe experiment", "10.1/safe", pdf)
    payload = dict(source.quality_result)
    payload["summary"] = {**payload["summary"], "dual_pass_count": 99}
    malformed = ValidatedLiteraturePackage(
        paper_id=source.paper_id,
        paper=source.paper,
        snapshot_fingerprint=source.snapshot_fingerprint,
        pdf_sha256=source.pdf_sha256,
        pdf_snapshot=source.pdf_snapshot,
        experiment_profile=source.experiment_profile,
        quality_result=payload,
    )
    with pytest.raises(LiteratureExtractionJobError):
        AtomicEvidenceDBFinalizer(db).finalize(malformed)
    assert counts(db)["quality_pipeline_runs"] == 0


def test_finalizer_renders_from_captured_bytes_after_source_path_replacement(
    evidence, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, pdf, paper_id = evidence
    store = LiteratureExtractionJobStore(session_key=b"x" * 32)

    class Papers:
        def get_paper(self, requested: int):
            return db.get_paper(requested)

    summary = store.create(Papers(), paper_id=paper_id, session_id="owner")
    job = store._jobs[summary["job_token"]]
    original = package(paper_id, "Safe experiment", "10.1/safe", pdf)
    original = replace(
        original,
        pdf_snapshot=store._snapshots.snapshot_for_finalization(
            job.snapshot_handle, expected_sha256=job.snapshot.pdf_sha256
        ),
    )
    job.status = "validated"
    job.validated_package = original
    replacement = pdf.with_name("replacement.pdf")
    make_pdf(replacement, "Figure 9. Replacement source must not be rendered.")
    assert_fresh = store._snapshots.assert_fresh

    def replace_after_freshness_check(handle: str) -> None:
        assert_fresh(handle)
        replacement.replace(pdf)

    monkeypatch.setattr(store._snapshots, "assert_fresh", replace_after_freshness_check)
    result = store.finalize(
        summary["job_token"],
        session_id="owner",
        finalizer=AtomicEvidenceDBFinalizer(db),
    )
    assert result["status"] == "completed"
    assert original.pdf_snapshot.verified_bytes(original.pdf_sha256) != pdf.read_bytes()
    assert counts(db)["quality_pipeline_runs"] == 1


def test_successful_commit_closes_the_latest_explicit_upload_waiting_record(evidence) -> None:
    db, pdf, paper_id = evidence
    with db.connect() as connection:
        connection.execute(
            """INSERT INTO processing_jobs(
               paper_id,document_id,job_type,status,provider,message,created_at,updated_at
               ) VALUES(?,NULL,'extract','blocked','prepared-action','等待用户启动','2026-01-01','2026-01-01')""",
            (paper_id,),
        )
    result = AtomicEvidenceDBFinalizer(db).finalize(
        package(paper_id, "Safe experiment", "10.1/safe", pdf)
    )
    assert result["status"] == "completed"
    job = db.list_processing_jobs()[0]
    assert job["status"] == "completed"
    assert job["provider"] == "prepared-action"
    assert "原子发布已完成" in job["message"]


def test_job_store_requires_trusted_finalizer_and_rechecks_source(evidence) -> None:
    db, pdf, paper_id = evidence

    class Papers:
        def get_paper(self, requested: int):
            assert requested == paper_id
            return db.get_paper(requested)

    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    summary = store.create(Papers(), paper_id=paper_id, session_id="owner")
    job = store._jobs[summary["job_token"]]
    job.status = "validated"
    payload = package(paper_id, "Safe experiment", "10.1/safe", pdf)
    job.validated_package = replace(
        payload,
        pdf_snapshot=store._snapshots.snapshot_for_finalization(
            job.snapshot_handle, expected_sha256=job.snapshot.pdf_sha256
        ),
    )

    class Untrusted:
        def finalize(self, package):
            raise AssertionError("must not run")

    with pytest.raises(LiteratureExtractionJobError) as unavailable:
        store.finalize(summary["job_token"], session_id="owner", finalizer=Untrusted())
    assert unavailable.value.code == "literature_commit_unavailable"
    replacement = pdf.with_name("replacement.pdf")
    make_pdf(replacement, "Changed source content")
    replacement.replace(pdf)
    with pytest.raises(LiteratureExtractionJobError) as stale:
        store.finalize(
            summary["job_token"], session_id="owner", finalizer=AtomicEvidenceDBFinalizer(db)
        )
    assert stale.value.code == "literature_source_stale"
    assert counts(db)["quality_pipeline_runs"] == 0


def test_job_store_trusted_finalize_waits_for_durable_ack_before_cleanup(evidence) -> None:
    db, pdf, paper_id = evidence

    class Papers:
        def get_paper(self, requested: int):
            return db.get_paper(requested)

    store = LiteratureExtractionJobStore(session_key=b"x" * 32)
    summary = store.create(Papers(), paper_id=paper_id, session_id="owner")
    job = store._jobs[summary["job_token"]]
    snapshot_handle = job.snapshot_handle
    job.status = "validated"
    payload = package(paper_id, "Safe experiment", "10.1/safe", pdf)
    job.validated_package = replace(
        payload,
        pdf_snapshot=store._snapshots.snapshot_for_finalization(
            job.snapshot_handle, expected_sha256=job.snapshot.pdf_sha256
        ),
    )
    result = store.finalize(
        summary["job_token"], session_id="owner", finalizer=AtomicEvidenceDBFinalizer(db)
    )
    assert result["status"] == "completed"
    assert result["visual_evidence_ready"] is False
    assert result["visual_stage_status"] == "not_found"
    assert summary["job_token"] in store._jobs
    assert snapshot_handle in store._snapshots._records
    store.acknowledge_finalized(summary["job_token"], session_id="owner")
    assert summary["job_token"] not in store._jobs
    assert snapshot_handle not in store._snapshots._records


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_publication_review_catalogue_search_and_restart_agree(evidence, monkeypatch, action):
    """Exercise real services; an index failure must not replay scientific writes."""
    from auto_research.evidence.review_queue import ReviewQueueService
    from auto_research.evidence.search_index import EvidenceSearchIndex
    from auto_research.evidence.webapp import search_paper_catalog

    db, pdf, paper_id = evidence
    payload = package(paper_id, "Safe experiment", "10.1/safe", pdf)
    receipt = AtomicEvidenceDBFinalizer(db).finalize(payload)
    assert receipt["search_index"]["status"] == "refreshed"
    index = EvidenceSearchIndex(db)
    queue = ReviewQueueService(db, search_index=index)
    rows = queue.list()["items"]
    before = search_paper_catalog(db)[0]
    assert before["pending_candidate_count"] == len(rows) == 1
    assert before["extraction_workflow_state"] == "pending_review"
    assert index.search("", quality_filter="published", refresh=False).total == 2

    refresh = index.refresh_papers
    def fail_index(_paper_ids):
        raise OSError("injected index failure")
    monkeypatch.setattr(index, "refresh_papers", fail_index)
    token = rows[0]["review_token"]
    result = queue.review(review_token=token, action=action)
    assert result["status"] == "saved_index_pending"
    saved_counts = counts(db)
    assert queue.list()["total"] == 0
    after = search_paper_catalog(db)[0]
    assert after["pending_candidate_count"] == 0
    assert after["extraction_workflow_state"] == "saved"
    assert after["six_workflow_state"] == "pending_review"  # Human validation is separate.
    assert "已完成" not in after["extraction_workflow_label"]
    # Reading the catalogue cannot silently rebuild a failed index.
    assert index.search("", quality_filter="published", refresh=False).total == 2

    monkeypatch.setattr(index, "refresh_papers", refresh)
    assert queue.review(review_token=token, action=action)["status"] == "saved"
    assert queue.review(review_token=token, action=action)["status"] == "saved"
    assert counts(db) == saved_counts
    expected = 3 if action == "approve" else 2
    assert index.search("", quality_filter="published", refresh=False).total == expected
    # Fresh service objects simulate reopening durable state, without repairing it.
    reopened = EvidenceDB(db.path)
    assert search_paper_catalog(reopened) == [after]
    assert ReviewQueueService(reopened).list()["total"] == 0
    assert EvidenceSearchIndex(reopened).search("", quality_filter="published", refresh=False).total == expected
    assert AtomicEvidenceDBFinalizer(reopened).finalize(payload)["idempotent"] is True
    assert counts(reopened) == saved_counts
    assert ReviewQueueService(reopened).list()["total"] == 0


def test_finding_only_completion_reaches_production_catalogue_renderer(evidence):
    import subprocess
    from auto_research.evidence.webapp import search_paper_catalog

    db, pdf, paper_id = evidence
    original = package(paper_id, "Safe experiment", "10.1/safe", pdf)
    quality = dict(original.quality_result)
    quality["records"] = [record("finding-only", "finding", "third_pass", finding_candidate())]
    quality["summary"] = {"candidate_count": 1, "dual_pass_count": 0,
                          "third_pass_count": 1, "manual_review_count": 0, "quality_threshold": 85.0}
    AtomicEvidenceDBFinalizer(db).finalize(replace(original, quality_result=MappingProxyType(quality)))
    catalogue = search_paper_catalog(db)
    assert catalogue[0]["six_row_count"] == 0
    assert catalogue[0]["extraction_workflow_state"] == "saved"
    runtime = Path(__file__).resolve().parents[1] / "auto_research/evidence/web/fusion_review.js"
    program = r'''
const fs=require('fs'),assert=require('assert');
globalThis.document={readyState:'loading',querySelector:()=>null,querySelectorAll:()=>[],addEventListener:()=>{}};
globalThis.localStorage={getItem:()=>null,setItem:()=>{}};
const catalogue=JSON.parse(fs.readFileSync(0,'utf8'));
globalThis.fetch=async url=>{assert.equal(String(url),'/api/search-papers');return {ok:true,json:async()=>catalogue};};
eval(fs.readFileSync(process.argv[1],'utf8'));
(async()=>{
 const api=globalThis.AutoResearchFusion;
 assert.equal(await api.refreshLiteratureCatalog(),true);
 assert.equal(api.state.papers[0].status,catalogue[0].extraction_workflow_label);
 assert(!api.state.papers[0].status.includes('未扫描'));
 assert(!api.state.papers[0].status.includes('已完成'));
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run(["node", "-e", program, str(runtime)], input=json.dumps(catalogue),
                            text=True, capture_output=True, timeout=8)
    assert result.returncode == 0, result.stderr


def test_visual_only_candidates_are_not_reported_as_unscanned(evidence, monkeypatch):
    from auto_research.evidence import literature_visual_stage as visual_stage
    from auto_research.evidence.review_queue import ReviewQueueService
    from auto_research.evidence.webapp import search_paper_catalog

    db, pdf, paper_id = evidence
    make_table_pdf(pdf)
    monkeypatch.setattr(visual_stage, "_target_specs", lambda _paper: table_spec())
    original = package(paper_id, "Safe experiment", "10.1/safe", pdf)
    quality = dict(original.quality_result)
    quality["records"] = []
    quality["summary"] = {"candidate_count": 0, "dual_pass_count": 0,
                          "third_pass_count": 0, "manual_review_count": 0, "quality_threshold": 85.0}
    receipt = AtomicEvidenceDBFinalizer(db).finalize(replace(original, quality_result=MappingProxyType(quality)))
    assert receipt["table_candidate_count"] == 1
    queue = ReviewQueueService(db).list()
    assert queue["total"] == 1
    assert queue["items"][0]["entity_type"] == "table"
    row = search_paper_catalog(db)[0]
    assert row["six_row_count"] == 0
    assert row["pending_candidate_count"] == 1
    assert row["extraction_workflow_state"] == "pending_review"
