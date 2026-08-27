from __future__ import annotations

import os
from pathlib import Path

import fitz
import pytest

from auto_research.evidence.db import EvidenceDB
from auto_research.evidence.literature_extraction_job import (
    ImmutablePDFSnapshot,
    LiteratureExtractionJobError,
)
from auto_research.evidence.literature_visual_stage import prepare_visual_evidence


def make_visual_pdf(path: Path, *, number: int, label: str, color: tuple[float, ...]) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.draw_rect(fitz.Rect(72, 90, 320, 245), color=color, fill=color)
    page.insert_text((72, 275), f"Figure {number}. {label}")
    page.insert_text((72, 315), "Measured hardness was 3.2 GPa at 300 K.")
    document.save(path)
    document.close()
    return path.read_bytes()


def make_db(tmp_path: Path, pdf: Path) -> tuple[EvidenceDB, int]:
    db = EvidenceDB(tmp_path / "evidence.sqlite")
    db.init()
    paper_id = db.upsert_paper(
        title="Snapshot visual paper",
        doi="10.1/snapshot-visual",
        pdf_path=str(pdf),
    )
    return db, paper_id


def test_visual_discovery_and_render_use_captured_bytes_not_current_source_path(
    tmp_path: Path,
) -> None:
    source = tmp_path / "paper.pdf"
    original = make_visual_pdf(
        source, number=1, label="Original snapshot figure.", color=(1.0, 0.0, 0.0)
    )
    db, paper_id = make_db(tmp_path, source)
    snapshot = ImmutablePDFSnapshot.create(original)
    replacement = tmp_path / "replacement.pdf"
    make_visual_pdf(
        replacement, number=2, label="Replacement path figure.", color=(0.0, 0.0, 1.0)
    )
    replacement.replace(source)

    staged = prepare_visual_evidence(
        db,
        paper_id=paper_id,
        expected_pdf_sha256=snapshot.sha256,
        pdf_snapshot=snapshot,
    )
    try:
        assert len(staged.assets) == 1
        assert staged.assets[0].spec["number"] == 1
        assert "Original snapshot" in staged.assets[0].spec["caption"]
        assert "Replacement path" not in staged.assets[0].spec["caption"]
        assert staged.assets[0].staged_path.read_bytes().startswith(b"\x89PNG")
        assert not list(staged.root.glob("source-*.pdf"))
    finally:
        staged.cleanup()


def test_materialized_snapshot_replacement_after_validation_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "paper.pdf"
    original = make_visual_pdf(
        source, number=1, label="Original snapshot figure.", color=(1.0, 0.0, 0.0)
    )
    db, paper_id = make_db(tmp_path, source)
    snapshot = ImmutablePDFSnapshot.create(original)
    replacement = tmp_path / "replacement.pdf"
    replacement_bytes = make_visual_pdf(
        replacement, number=2, label="Replacement path figure.", color=(0.0, 0.0, 1.0)
    )

    from auto_research.evidence import literature_visual_stage as visual_stage

    original_render = visual_stage._render_crop

    def replace_after_validation(path: Path, page: int, bbox: list[float], output: Path) -> str:
        attacker = path.with_name("attacker.pdf")
        attacker.write_bytes(replacement_bytes)
        os.replace(attacker, path)
        return original_render(path, page, bbox, output)

    monkeypatch.setattr(visual_stage, "_render_crop", replace_after_validation)
    with pytest.raises(LiteratureExtractionJobError) as raised:
        prepare_visual_evidence(
            db,
            paper_id=paper_id,
            expected_pdf_sha256=snapshot.sha256,
            pdf_snapshot=snapshot,
        )
    assert raised.value.code == "literature_visual_hash_mismatch"
    assert not (tmp_path / "visual_assets").exists()


def test_snapshot_hash_mismatch_fails_before_visual_discovery(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    original = make_visual_pdf(
        source, number=1, label="Original snapshot figure.", color=(1.0, 0.0, 0.0)
    )
    db, paper_id = make_db(tmp_path, source)
    snapshot = ImmutablePDFSnapshot.create(original)
    with pytest.raises(LiteratureExtractionJobError) as raised:
        prepare_visual_evidence(
            db,
            paper_id=paper_id,
            expected_pdf_sha256="0" * 64,
            pdf_snapshot=snapshot,
        )
    assert raised.value.code == "literature_source_stale"
