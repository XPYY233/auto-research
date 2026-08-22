from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .evidence_v12_export import plan_evidence_v12_export
from .official_package_assets import OfficialPackageAssetError, plan_official_pdf_payloads
from .portable_repository import (
    PortableExportPlan,
    stable_entity_uid,
    stable_paper_uid,
)


class OfficialPackageV2ReleaseError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OfficialPackageV2Inputs:
    """Private release inputs plus a path-free audit summary.

    Paths are deliberately retained only in this in-memory maintainer object.
    They must never be serialized into the package, report, or application DTO.
    """

    export_plan: PortableExportPlan
    paper_pdf_paths: Mapping[str, Path]
    binary_assets: Mapping[str, Path]
    pdf_total_bytes: int
    pdf_snapshot_hash_matches: int
    pdf_snapshot_hash_mismatches: int
    pdf_override_count: int
    visual_total_bytes: int
    visual_review_counts: Mapping[str, int]
    excluded_papers: tuple[Mapping[str, str], ...] = ()

    def public_summary(self) -> dict[str, Any]:
        return {
            "schema_version": "official-package-v2-input-audit-v1",
            "paper_count": len(self.paper_pdf_paths),
            "pdf_count": len(self.paper_pdf_paths),
            "pdf_total_bytes": self.pdf_total_bytes,
            "pdf_snapshot_hash_matches": self.pdf_snapshot_hash_matches,
            "pdf_snapshot_hash_mismatches": self.pdf_snapshot_hash_mismatches,
            "pdf_override_count": self.pdf_override_count,
            "visual_asset_count": len(self.binary_assets),
            "visual_total_bytes": self.visual_total_bytes,
            "visual_review_counts": dict(sorted(self.visual_review_counts.items())),
            "excluded_papers": [dict(row) for row in self.excluded_papers],
            "identity_audit": "matched",
            "visual_content_hash_audit": "matched",
            "scientific_review_complete": bool(self.visual_review_counts)
            and set(self.visual_review_counts) <= {"verified"},
        }


def apply_official_package_v2_exclusions(
    plan: PortableExportPlan,
    excluded_dois: Mapping[str, str] | None,
) -> tuple[PortableExportPlan, tuple[Mapping[str, str], ...]]:
    """Filter only the declared distribution plan, never the source snapshot."""

    exclusions = {
        str(doi).strip().casefold(): str(reason).strip()
        for doi, reason in dict(excluded_dois or {}).items()
        if str(doi).strip() and str(reason).strip()
    }
    if not exclusions:
        return plan, ()
    declarations: list[Mapping[str, str]] = []
    excluded_uids: set[str] = set()
    papers: list[Mapping[str, Any]] = []
    for paper in plan.papers:
        doi = str(paper.get("doi") or "").strip()
        reason = exclusions.get(doi.casefold())
        if reason:
            excluded_uids.add(str(paper["paper_uid"]))
            declarations.append({"doi": doi, "reason": reason})
        else:
            papers.append(paper)
    unresolved = set(exclusions) - {row["doi"].casefold() for row in declarations}
    if unresolved:
        raise OfficialPackageV2ReleaseError(
            "release_exclusion_invalid", "排除声明引用了稳定快照中不存在的 DOI"
        )
    entities = tuple(
        entity for entity in plan.entities
        if str(entity.get("paper_uid") or "") not in excluded_uids
    )
    if not papers:
        raise OfficialPackageV2ReleaseError(
            "release_paper_scope", "排除声明不能移除全部论文"
        )
    return (
        PortableExportPlan(
            papers=tuple(papers),
            entities=entities,
            dropped_by_reason=plan.dropped_by_reason,
            private_source_sha256=plan.private_source_sha256,
        ),
        tuple(sorted(declarations, key=lambda row: row["doi"].casefold())),
    )


def load_official_package_v2_exclusions(path: Path | str) -> dict[str, str]:
    source = Path(path).expanduser()
    if source.is_symlink() or not source.is_file():
        raise OfficialPackageV2ReleaseError(
            "release_exclusion_invalid", "官方资料包排除声明缺失或不是普通文件"
        )
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfficialPackageV2ReleaseError(
            "release_exclusion_invalid", "官方资料包排除声明无法读取"
        ) from exc
    if (
        not isinstance(payload, Mapping)
        or set(payload) != {"schema_version", "excluded_papers"}
        or payload.get("schema_version") != "official-package-v2-exclusions-v1"
        or not isinstance(payload.get("excluded_papers"), list)
    ):
        raise OfficialPackageV2ReleaseError(
            "release_exclusion_invalid", "官方资料包排除声明结构无效"
        )
    result: dict[str, str] = {}
    for row in payload["excluded_papers"]:
        if not isinstance(row, Mapping) or set(row) != {"doi", "reason"}:
            raise OfficialPackageV2ReleaseError(
                "release_exclusion_invalid", "官方资料包排除条目无效"
            )
        doi = str(row.get("doi") or "").strip()
        reason = str(row.get("reason") or "").strip()
        if not doi or not reason or doi.casefold() in result:
            raise OfficialPackageV2ReleaseError(
                "release_exclusion_invalid", "官方资料包排除条目重复或为空"
            )
        result[doi.casefold()] = reason
    return result


def normalize_official_package_v2_approved_scope(
    *,
    original_plan: PortableExportPlan,
    filtered_plan: PortableExportPlan,
    approved_paper_uids: Iterable[str],
) -> frozenset[str]:
    """Narrow an immutable approval only through declared exclusions."""

    approved = frozenset(
        str(value).strip() for value in approved_paper_uids if str(value).strip()
    )
    original = frozenset(str(row["paper_uid"]) for row in original_plan.papers)
    filtered = frozenset(str(row["paper_uid"]) for row in filtered_plan.papers)
    if not approved or approved not in {original, filtered}:
        raise OfficialPackageV2ReleaseError(
            "release_paper_scope", "批准论文清单与稳定导出计划不一致"
        )
    return filtered


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _safe_source_file(
    raw_path: object,
    expected_sha256: object,
    *,
    label: str,
    require_snapshot_hash: bool,
) -> tuple[Path, int, bool]:
    path = Path(str(raw_path or "")).expanduser()
    if path.is_symlink() or not path.is_file():
        raise OfficialPackageV2ReleaseError(
            "release_asset_missing", f"{label} 缺失或不是普通文件"
        )
    expected = str(expected_sha256 or "").strip().lower()
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        raise OfficialPackageV2ReleaseError(
            "release_asset_hash_missing", f"{label} 缺少稳定 SHA-256"
        )
    actual, size = _sha256_file(path)
    matches_snapshot = actual == expected
    if require_snapshot_hash and not matches_snapshot:
        raise OfficialPackageV2ReleaseError(
            "release_asset_changed", f"{label} 与稳定快照记录不一致"
        )
    return path.resolve(), size, matches_snapshot


def _open_snapshot(path: Path) -> sqlite3.Connection:
    source = path.expanduser()
    if source.is_symlink() or not source.is_file():
        raise OfficialPackageV2ReleaseError(
            "release_snapshot_invalid", "官方资料包源必须是普通 SQLite 快照"
        )
    resolved = source.resolve()
    if any(Path(str(resolved) + suffix).exists() for suffix in ("-wal", "-shm")):
        raise OfficialPackageV2ReleaseError(
            "release_snapshot_live", "官方资料包源仍存在 WAL/SHM"
        )
    try:
        connection = sqlite3.connect(
            f"{resolved.as_uri()}?mode=ro&immutable=1", uri=True
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        return connection
    except sqlite3.Error as exc:
        raise OfficialPackageV2ReleaseError(
            "release_snapshot_invalid", "无法只读打开官方资料包源快照"
        ) from exc


def plan_official_package_v2_inputs(
    snapshot: Path | str,
    *,
    approved_paper_uids: Iterable[str],
    paper_pdf_overrides: Mapping[str, Path | str] | None = None,
    export_plan: PortableExportPlan | None = None,
    excluded_dois: Mapping[str, str] | None = None,
) -> OfficialPackageV2Inputs:
    """Resolve complete PDF and visual payloads from one immutable v12 snapshot.

    The export plan remains the authority for which visual entities are
    publishable. Extra draft assets in the private workspace are ignored.
    Every selected file must match the hash already recorded in the snapshot.
    """

    source = Path(snapshot)
    original_plan = export_plan or plan_evidence_v12_export(source)
    plan, exclusion_declarations = apply_official_package_v2_exclusions(
        original_plan, excluded_dois
    )
    approved = normalize_official_package_v2_approved_scope(
        original_plan=original_plan,
        filtered_plan=plan,
        approved_paper_uids=approved_paper_uids,
    )
    overrides = {
        str(paper_uid): Path(path).expanduser()
        for paper_uid, path in dict(paper_pdf_overrides or {}).items()
    }
    if not set(overrides) <= set(approved):
        raise OfficialPackageV2ReleaseError(
            "release_pdf_override_scope", "PDF 修复映射引用未批准论文"
        )
    visual_entity_uids = frozenset(
        str(
            row.get("entity_uid")
            or stable_entity_uid(
                str(row.get("paper_uid") or ""),
                str(row.get("entity_type") or ""),
                row.get("identity_key"),
            )
        )
        for row in plan.entities
        if str(row.get("entity_type") or "") in {"table", "figure"}
    )

    connection = _open_snapshot(source)
    try:
        local_to_public: dict[int, str] = {}
        pdf_paths: dict[str, Path] = {}
        pdf_sizes: dict[str, int] = {}
        pdf_hash_matches = 0
        pdf_hash_mismatches = 0
        for row in connection.execute(
            """SELECT id,doi,title,year,first_author,pdf_path,pdf_sha256
               FROM papers ORDER BY id"""
        ):
            paper_uid = stable_paper_uid(
                doi=row["doi"],
                title=row["title"],
                year=row["year"],
                first_author=row["first_author"],
            )
            local_to_public[int(row["id"])] = paper_uid
            if paper_uid not in approved:
                continue
            raw_pdf_path = overrides.get(paper_uid, row["pdf_path"])
            path, size, matches_snapshot = _safe_source_file(
                raw_pdf_path,
                row["pdf_sha256"],
                label="论文 PDF",
                require_snapshot_hash=False,
            )
            if paper_uid in pdf_paths:
                raise OfficialPackageV2ReleaseError(
                    "release_paper_collision", "稳定论文身份发生碰撞"
                )
            pdf_paths[paper_uid] = path
            pdf_sizes[paper_uid] = size
            if matches_snapshot:
                pdf_hash_matches += 1
            else:
                pdf_hash_mismatches += 1

        # This additionally verifies the PDF header, exact coverage, per-file
        # limit, and aggregate 2 GiB package limit.
        try:
            plan_official_pdf_payloads(pdf_paths, expected_paper_uids=approved)
        except OfficialPackageAssetError as exc:
            raise OfficialPackageV2ReleaseError(exc.code, str(exc)) from exc

        visual_paths: dict[str, Path] = {}
        visual_sizes: dict[str, int] = {}
        review_counts: Counter[str] = Counter()
        for row in connection.execute(
            """SELECT paper_id,asset_type,label,asset_number,image_path,image_sha256,
                      review_status
               FROM visual_assets ORDER BY paper_id,asset_type,asset_number,id"""
        ):
            paper_uid = local_to_public.get(int(row["paper_id"]))
            if paper_uid is None:
                raise OfficialPackageV2ReleaseError(
                    "release_asset_identity", "图表引用未知论文"
                )
            entity_type = str(row["asset_type"])
            logical_identity = (
                f"visual:{entity_type}:{row['label']}:{int(row['asset_number'])}"
            )
            entity_uid = stable_entity_uid(paper_uid, entity_type, logical_identity)
            if entity_uid not in visual_entity_uids:
                continue
            path, size, _matches_snapshot = _safe_source_file(
                row["image_path"],
                row["image_sha256"],
                label="图表截图",
                require_snapshot_hash=True,
            )
            if entity_uid in visual_paths:
                raise OfficialPackageV2ReleaseError(
                    "release_asset_collision", "图表稳定身份发生碰撞"
                )
            visual_paths[entity_uid] = path
            visual_sizes[entity_uid] = size
            review_counts[str(row["review_status"] or "unknown")] += 1
    finally:
        connection.close()

    if set(pdf_paths) != set(approved):
        raise OfficialPackageV2ReleaseError(
            "release_pdf_coverage", "官方资料包未完整覆盖已批准论文的 PDF"
        )
    if set(visual_paths) != set(visual_entity_uids):
        raise OfficialPackageV2ReleaseError(
            "release_visual_coverage", "官方资料包未完整覆盖导出计划中的图表截图"
        )
    return OfficialPackageV2Inputs(
        export_plan=plan,
        paper_pdf_paths=dict(sorted(pdf_paths.items())),
        binary_assets=dict(sorted(visual_paths.items())),
        pdf_total_bytes=sum(pdf_sizes.values()),
        pdf_snapshot_hash_matches=pdf_hash_matches,
        pdf_snapshot_hash_mismatches=pdf_hash_mismatches,
        pdf_override_count=len(overrides),
        visual_total_bytes=sum(visual_sizes.values()),
        visual_review_counts=dict(review_counts),
        excluded_papers=exclusion_declarations,
    )


__all__ = [
    "OfficialPackageV2Inputs",
    "OfficialPackageV2ReleaseError",
    "apply_official_package_v2_exclusions",
    "load_official_package_v2_exclusions",
    "normalize_official_package_v2_approved_scope",
    "plan_official_package_v2_inputs",
]
