from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest

from auto_research.evidence.db import EvidenceDB
from auto_research.evidence.literature_extraction_job import (
    ImmutablePDFSnapshot,
    LiteratureExtractionJobError,
)
from auto_research.evidence.literature_visual_stage import (
    prepare_visual_evidence,
    publish_staged_visual_evidence,
)
from auto_research.evidence.table_structure import (
    PublicTableIdentity,
    rebind_table_structure_candidate,
)
from auto_research.evidence.table_structure_store import (
    TableStructureStore,
    TableStructureStoreError,
)


TABLE_BBOX = [70.0, 98.0, 370.0, 182.0]


def _make_pdf(path: Path, *, ruled_table: bool) -> bytes:
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    if ruled_table:
        for x in (72, 220, 368):
            page.draw_line((x, 100), (x, 180), color=(0, 0, 0), width=1)
        for y in (100, 140, 180):
            page.draw_line((72, y), (368, y), color=(0, 0, 0), width=1)
        page.insert_text((84, 125), "Temperature")
        page.insert_text((235, 125), "Hardness")
        page.insert_text((84, 165), "300 K")
        page.insert_text((235, 165), "3.2 GPa")
    else:
        page.draw_rect(fitz.Rect(*TABLE_BBOX), color=(0, 0, 0))
        page.insert_text((84, 125), "Table image without recoverable cell rules")
    document.save(path)
    document.close()
    return path.read_bytes()


def _spec() -> list[dict[str, object]]:
    return [
        {
            "asset_type": "table",
            "number": 1,
            "page": 1,
            "bbox": list(TABLE_BBOX),
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


def _database(tmp_path: Path, source: Path) -> tuple[EvidenceDB, int]:
    database = EvidenceDB(tmp_path / "evidence.sqlite")
    database.init()
    paper_id = database.upsert_paper(
        title="Structured table paper",
        doi="10.1/structured-table",
        pdf_path=str(source),
    )
    return database, paper_id


def _publish(database: EvidenceDB, paper_id: int, staged):
    paper = database.get_paper(paper_id)
    with database.connect() as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = publish_staged_visual_evidence(
                database,
                connection=connection,
                paper=paper,
                staged=staged,
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            staged.cleanup(rollback_published=True)
            raise


def test_real_table_uses_snapshot_and_publishes_unverified_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "paper.pdf"
    content = _make_pdf(source, ruled_table=True)
    database, paper_id = _database(tmp_path, source)
    snapshot = ImmutablePDFSnapshot.create(content)
    source.unlink()

    from auto_research.evidence import literature_visual_stage as visual_stage

    monkeypatch.setattr(visual_stage, "_target_specs", lambda _paper: _spec())
    staged = prepare_visual_evidence(
        database,
        paper_id=paper_id,
        expected_pdf_sha256=snapshot.sha256,
        pdf_snapshot=snapshot,
    )
    try:
        candidate = staged.assets[0].table_structure
        assert candidate is not None
        assert candidate.rows == (("Temperature", "Hardness"), ("300 K", "3.2 GPa"))
        result = _publish(database, paper_id, staged)
        assert result["table_structure_candidate_count"] == 1
        assert result["table_structure_manual_review_count"] == 0
        assert result["table_structure_unavailable_count"] == 0
        with database.connect() as connection:
            asset_id = int(connection.execute("SELECT id FROM visual_assets").fetchone()[0])
            assert connection.execute(
                "SELECT COUNT(*) FROM table_structure_versions"
            ).fetchone()[0] == 1
        store = TableStructureStore(database)
        with pytest.raises(TableStructureStoreError) as pending:
            store.latest(visual_asset_id=asset_id)
        assert pending.value.code == "table_structure_store_pending"
        public = store.latest(visual_asset_id=asset_id, include_unverified=True)
        assert public["entity_uid"] == str(asset_id)
        assert public["status"] == "candidate"
        serialized = json.dumps(public, ensure_ascii=False)
        assert str(source) not in serialized
    finally:
        staged.cleanup()


def test_missing_structure_preserves_screenshot_and_reports_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "paper.pdf"
    content = _make_pdf(source, ruled_table=False)
    database, paper_id = _database(tmp_path, source)
    snapshot = ImmutablePDFSnapshot.create(content)

    from auto_research.evidence import literature_visual_stage as visual_stage

    monkeypatch.setattr(visual_stage, "_target_specs", lambda _paper: _spec())
    staged = prepare_visual_evidence(
        database,
        paper_id=paper_id,
        expected_pdf_sha256=snapshot.sha256,
        pdf_snapshot=snapshot,
    )
    try:
        assert staged.assets[0].table_structure is None
        assert staged.assets[0].table_structure_failure_code == "table_structure_not_found"
        result = _publish(database, paper_id, staged)
        assert result["asset_count"] == 1
        assert result["table_structure_unavailable_count"] == 1
        with database.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM visual_assets").fetchone()[0] == 1
            assert connection.execute(
                "SELECT COUNT(*) FROM table_structure_versions"
            ).fetchone()[0] == 0
    finally:
        staged.cleanup()


def test_structure_store_failure_rolls_back_visual_and_candidate_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "paper.pdf"
    content = _make_pdf(source, ruled_table=True)
    database, paper_id = _database(tmp_path, source)
    snapshot = ImmutablePDFSnapshot.create(content)

    from auto_research.evidence import literature_visual_stage as visual_stage

    monkeypatch.setattr(visual_stage, "_target_specs", lambda _paper: _spec())
    staged = prepare_visual_evidence(
        database,
        paper_id=paper_id,
        expected_pdf_sha256=snapshot.sha256,
        pdf_snapshot=snapshot,
    )

    def fail_save(self, **_kwargs):
        raise TableStructureStoreError("table_structure_store_unavailable")

    monkeypatch.setattr(TableStructureStore, "save_candidate_in_transaction", fail_save)
    with pytest.raises(LiteratureExtractionJobError) as failure:
        _publish(database, paper_id, staged)
    assert failure.value.code == "literature_table_structure_store_failed"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM visual_assets").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM table_structure_versions"
        ).fetchone()[0] == 0
    assert not [path for path in (tmp_path / "visual_assets").rglob("*.png")]


def test_rebind_changes_only_identity_and_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "paper.pdf"
    content = _make_pdf(source, ruled_table=True)
    database, paper_id = _database(tmp_path, source)
    snapshot = ImmutablePDFSnapshot.create(content)

    from auto_research.evidence import literature_visual_stage as visual_stage

    monkeypatch.setattr(visual_stage, "_target_specs", lambda _paper: _spec())
    staged = prepare_visual_evidence(
        database,
        paper_id=paper_id,
        expected_pdf_sha256=snapshot.sha256,
        pdf_snapshot=snapshot,
    )
    try:
        original = staged.assets[0].table_structure
        assert original is not None
        rebound = rebind_table_structure_candidate(
            original, PublicTableIdentity("workspace", "workspace", "17")
        )
        assert rebound.identity.entity_uid == "17"
        assert rebound.content_fingerprint != original.content_fingerprint
        assert rebound.page == original.page
        assert rebound.bbox == original.bbox
        assert rebound.status == original.status
        assert rebound.reason_codes == original.reason_codes
        assert rebound.rows == original.rows
        assert rebound.cells == original.cells
    finally:
        staged.cleanup()
