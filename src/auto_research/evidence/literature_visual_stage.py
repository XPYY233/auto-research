"""Stage PDF visual evidence before one atomic publication transaction."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping

from auto_research.paths import DATA_DIR

from .db import EVIDENCE_DB_PATH, EvidenceDB
from .literature_extraction_job import LiteratureExtractionJobError
from .visual_evidence import (
    _generic_specs,
    _render_crop,
    _target_specs,
    _upsert_asset,
    link_data_items_to_visuals,
)


@dataclass(frozen=True)
class StagedVisualAsset:
    spec: Mapping[str, Any]
    staged_path: Path
    final_path: Path
    sha256: str


@dataclass
class StagedVisualEvidence:
    root: Path
    assets: tuple[StagedVisualAsset, ...]
    created_paths: list[Path]

    def cleanup(self, *, rollback_published: bool = False) -> None:
        if rollback_published:
            for path in reversed(self.created_paths):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        shutil.rmtree(self.root, ignore_errors=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_visual_evidence(
    db: EvidenceDB,
    *,
    paper_id: int,
    expected_pdf_sha256: str,
) -> StagedVisualEvidence:
    paper = db.get_paper(paper_id)
    if not paper:
        raise LiteratureExtractionJobError(
            "literature_paper_missing", "目标文献不存在，未生成视觉证据"
        )
    pdf_path = Path(str(paper.get("pdf_path") or ""))
    if not pdf_path.is_file() or pdf_path.is_symlink():
        raise LiteratureExtractionJobError(
            "literature_pdf_missing", "目标 PDF 不可用，未生成视觉证据"
        )
    if _sha256(pdf_path) != expected_pdf_sha256:
        raise LiteratureExtractionJobError(
            "literature_source_changed", "目标 PDF 已变化，未发布任何新记录"
        )
    specs = _target_specs(paper) or _generic_specs(pdf_path)
    staging_parent = db.path.parent / ".visual-staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix=f"paper-{paper_id}-", dir=staging_parent))
    output_root = (
        DATA_DIR / "evidence" / "visual_assets"
        if db.path.resolve() == EVIDENCE_DB_PATH.resolve()
        else db.path.parent / "visual_assets"
    )
    identity = hashlib.sha256(
        f"{str(paper.get('doi') or '').lower()}|{paper.get('title') or ''}".encode("utf-8")
    ).hexdigest()[:10]
    staged: list[StagedVisualAsset] = []
    try:
        for index, raw in enumerate(specs):
            spec = dict(raw)
            asset_type = str(spec.get("asset_type") or "")
            number = spec.get("number")
            page = spec.get("page")
            bbox = spec.get("bbox")
            if (
                asset_type not in {"table", "figure"}
                or isinstance(number, bool)
                or not isinstance(number, int)
                or number < 1
                or isinstance(page, bool)
                or not isinstance(page, int)
                or page < 1
                or not isinstance(bbox, list)
                or len(bbox) != 4
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    for value in bbox
                )
                or float(bbox[2]) <= float(bbox[0])
                or float(bbox[3]) <= float(bbox[1])
            ):
                raise LiteratureExtractionJobError(
                    "literature_visual_invalid", "视觉证据定位无效，未发布任何新记录"
                )
            temporary = root / f"{index:04d}-{asset_type}-{number}.png"
            image_sha = _render_crop(pdf_path, page, list(bbox), temporary)
            if _sha256(temporary) != image_sha:
                raise LiteratureExtractionJobError(
                    "literature_visual_hash_mismatch", "视觉证据校验失败，未发布任何新记录"
                )
            final = (
                output_root
                / f"paper_{paper_id:03d}_{identity}"
                / f"{asset_type}_{number:02d}_{image_sha[:12]}.png"
            )
            staged.append(StagedVisualAsset(spec, temporary, final, image_sha))
        return StagedVisualEvidence(root, tuple(staged), [])
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise


def publish_staged_visual_evidence(
    db: EvidenceDB,
    *,
    connection: Any,
    paper: Mapping[str, Any],
    staged: StagedVisualEvidence,
) -> dict[str, Any]:
    published: list[dict[str, Any]] = []
    for asset in staged.assets:
        if not asset.staged_path.is_file() or _sha256(asset.staged_path) != asset.sha256:
            raise LiteratureExtractionJobError(
                "literature_visual_hash_mismatch", "视觉证据暂存文件已变化，未发布任何新记录"
            )
        asset.final_path.parent.mkdir(parents=True, exist_ok=True)
        if asset.final_path.exists():
            if not asset.final_path.is_file() or _sha256(asset.final_path) != asset.sha256:
                raise LiteratureExtractionJobError(
                    "literature_visual_conflict", "视觉证据目标文件冲突，未发布任何新记录"
                )
            asset.staged_path.unlink()
        else:
            os.replace(asset.staged_path, asset.final_path)
            staged.created_paths.append(asset.final_path)
        published.append(
            _upsert_asset(
                db,
                dict(paper),
                {
                    **dict(asset.spec),
                    "_image_path": str(asset.final_path),
                    "_image_sha256": asset.sha256,
                    "review_status": str(asset.spec.get("review_status") or "draft"),
                },
                connection=connection,
            )
        )
    links = link_data_items_to_visuals(db, int(paper["id"]), connection=connection)
    return {
        "asset_count": len(published),
        "table_count": sum(row["asset_type"] == "table" for row in published),
        "figure_count": sum(row["asset_type"] == "figure" for row in published),
        "manual_review_count": sum(row.get("review_status") != "verified" for row in published),
        "asset_hashes": [str(row["image_sha256"]) for row in published],
        "links": links,
    }


__all__ = [
    "StagedVisualEvidence",
    "prepare_visual_evidence",
    "publish_staged_visual_evidence",
]
