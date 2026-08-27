"""Stage PDF visual evidence before one atomic publication transaction."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any, Iterator, Mapping

from auto_research.paths import DATA_DIR

from .db import EVIDENCE_DB_PATH, EvidenceDB
from .literature_extraction_job import (
    ImmutablePDFSnapshot,
    LiteratureExtractionJobError,
)
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


@dataclass(frozen=True)
class _StableSnapshotFile:
    fd: int
    path: Path
    expected_sha256: str
    expected_size: int

    def assert_unchanged(self) -> None:
        """Fail closed if the private materialization was replaced or altered."""

        try:
            descriptor_stat = os.fstat(self.fd)
            path_stat = os.lstat(self.path)
            if (
                not stat.S_ISREG(descriptor_stat.st_mode)
                or not stat.S_ISREG(path_stat.st_mode)
                or not os.path.samestat(descriptor_stat, path_stat)
                or descriptor_stat.st_size != self.expected_size
                or path_stat.st_size != self.expected_size
            ):
                raise OSError("snapshot identity changed")
            position = os.lseek(self.fd, 0, os.SEEK_CUR)
            os.lseek(self.fd, 0, os.SEEK_SET)
            digest = hashlib.sha256()
            remaining = self.expected_size
            while remaining:
                chunk = os.read(self.fd, min(1024 * 1024, remaining))
                if not chunk:
                    raise OSError("snapshot was truncated")
                digest.update(chunk)
                remaining -= len(chunk)
            if os.read(self.fd, 1) or digest.hexdigest() != self.expected_sha256:
                raise OSError("snapshot content changed")
            os.lseek(self.fd, position, os.SEEK_SET)
        except (OSError, ValueError) as exc:
            raise LiteratureExtractionJobError(
                "literature_visual_hash_mismatch",
                "视觉证据使用的 PDF 快照已变化，未发布任何新记录",
            ) from exc


@contextmanager
def _materialized_snapshot(
    root: Path,
    snapshot: ImmutablePDFSnapshot,
    *,
    expected_pdf_sha256: str,
) -> Iterator[_StableSnapshotFile]:
    """Materialize immutable bytes privately for legacy PyMuPDF helpers.

    The original paper path is never reopened.  The descriptor stays open and
    its identity/content are checked before and after every helper invocation,
    so a path replacement can only abort staging, never publish mixed bytes.
    """

    content = snapshot.verified_bytes(expected_pdf_sha256)
    fd, raw_path = tempfile.mkstemp(prefix="source-", suffix=".pdf", dir=root)
    path = Path(raw_path)
    try:
        if callable(getattr(os, "fchmod", None)):
            os.fchmod(fd, 0o600)
        offset = 0
        while offset < len(content):
            written = os.write(fd, content[offset:])
            if written <= 0:
                raise OSError("snapshot write failed")
            offset += written
        os.fsync(fd)
        stable = _StableSnapshotFile(fd, path, expected_pdf_sha256, len(content))
        stable.assert_unchanged()
        yield stable
    except LiteratureExtractionJobError:
        raise
    except OSError as exc:
        raise LiteratureExtractionJobError(
            "literature_visual_unavailable",
            "无法安全暂存 PDF 快照，未发布任何新记录",
        ) from exc
    finally:
        try:
            os.close(fd)
        finally:
            path.unlink(missing_ok=True)


def prepare_visual_evidence(
    db: EvidenceDB,
    *,
    paper_id: int,
    expected_pdf_sha256: str,
    pdf_snapshot: ImmutablePDFSnapshot,
) -> StagedVisualEvidence:
    paper = db.get_paper(paper_id)
    if not paper:
        raise LiteratureExtractionJobError(
            "literature_paper_missing", "目标文献不存在，未生成视觉证据"
        )
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
        with _materialized_snapshot(
            root, pdf_snapshot, expected_pdf_sha256=expected_pdf_sha256
        ) as source:
            specs = _target_specs(paper)
            if not specs:
                source.assert_unchanged()
                specs = _generic_specs(source.path)
                source.assert_unchanged()
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
                source.assert_unchanged()
                image_sha = _render_crop(source.path, page, list(bbox), temporary)
                source.assert_unchanged()
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
        "review_candidates": [
            {
                "asset_id": int(row["id"]),
                "asset_type": str(row["asset_type"]),
                "label": str(row["label"]),
                "caption": str(row.get("caption") or "")[:4000],
                "page_start": int(row["page_start"]),
                "image_sha256": str(row["image_sha256"]),
            }
            for row in published
        ],
        "links": links,
    }


__all__ = [
    "StagedVisualEvidence",
    "prepare_visual_evidence",
    "publish_staged_visual_evidence",
]
