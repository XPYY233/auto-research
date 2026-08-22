from __future__ import annotations

import argparse
import base64
import copy
import json
import os
import re
import shutil
import stat
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from auto_research.portable_file_ops import best_effort_remove_tree, replace_file

from .evidence_package import build_evidence_package, verify_evidence_package
from .evidence_v12_export import plan_evidence_v12_export
from .official_package_assets import (
    OFFICIAL_DISTRIBUTION_SCOPE,
    OFFICIAL_PACKAGE_CONTRACT_V2,
    plan_official_pdf_payloads,
)
from .official_package_store import (
    import_official_evidence_package,
    open_active_official_repository,
)
from .portable_repository import (
    DATABASE_CONTRACT,
    DATABASE_PATH,
    BinaryAssetPermission,
    DISTRIBUTION_SCHEMA_VERSION,
    IDENTITY_VERSION,
    PROVENANCE_PATH,
    RIGHTS_PATH,
    PortableExportPlan,
    ReleasePolicy,
    materialize_portable_repository,
    provenance_for_papers,
)
from .trusted_publishers import (
    assert_trusted_package_identity,
    trusted_public_keys,
)


@dataclass(frozen=True)
class InternalPreviewBuildReport:
    package_id: str
    package_version: str
    signer_key_id: str
    package_path: str
    package_size_bytes: int
    package_sha256: str
    manifest_sha256: str
    repository_sha256: str
    content_fingerprint: str
    source_snapshot_sha256: str
    paper_count: int
    entity_count: int
    item_count: int
    finding_count: int
    table_count: int
    figure_count: int
    dropped_by_reason: dict[str, int]
    verified_import_seconds: float
    distribution_scope: str = "internal-preview-only"
    includes_pdfs: bool = False
    includes_binary_assets: bool = False
    pdf_count: int = 0
    binary_asset_count: int = 0

    def public_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["package_path"] = Path(self.package_path).name
        return value


PAPER_UID_RE = re.compile(r"^paper_[0-9a-f]{32}$")
EXCERPT_KEYS = frozenset({"source_excerpt", "source_context", "caption"})
MAX_EXCERPT_CHARS = 1000
MAX_EXCERPT_CHARS_PER_PAPER = 5000
MAX_EXCERPT_CHARS_TOTAL = 200_000


def normalize_approved_paper_uids(values: Iterable[str]) -> frozenset[str]:
    normalized: set[str] = set()
    for raw in values:
        uid = str(raw).strip()
        if not uid or not PAPER_UID_RE.fullmatch(uid):
            raise RuntimeError("批准论文清单包含无效 paper_uid")
        if uid in normalized:
            raise RuntimeError("批准论文清单包含重复 paper_uid")
        normalized.add(uid)
    if not normalized:
        raise RuntimeError("必须显式提供至少一篇已批准论文")
    return frozenset(normalized)


def _budget_official_excerpts(plan: PortableExportPlan) -> PortableExportPlan:
    """Apply the published excerpt budget without changing scientific fields."""

    seen_by_paper: dict[str, set[str]] = {}
    totals_by_paper: dict[str, int] = {}
    total = 0

    def visit(value: object, *, paper_uid: str) -> object:
        nonlocal total
        if isinstance(value, dict):
            result: dict[object, object] = {}
            for key, child in value.items():
                if key in EXCERPT_KEYS and child:
                    excerpt = " ".join(str(child).split())[:MAX_EXCERPT_CHARS]
                    if not excerpt:
                        result[key] = ""
                        continue
                    known = seen_by_paper.setdefault(paper_uid, set())
                    if excerpt in known:
                        result[key] = excerpt
                        continue
                    paper_total = totals_by_paper.get(paper_uid, 0)
                    if (
                        paper_total + len(excerpt) > MAX_EXCERPT_CHARS_PER_PAPER
                        or total + len(excerpt) > MAX_EXCERPT_CHARS_TOTAL
                    ):
                        result[key] = ""
                        continue
                    known.add(excerpt)
                    totals_by_paper[paper_uid] = paper_total + len(excerpt)
                    total += len(excerpt)
                    result[key] = excerpt
                else:
                    result[key] = visit(child, paper_uid=paper_uid)
            return result
        if isinstance(value, list):
            return [visit(child, paper_uid=paper_uid) for child in value]
        return value

    entities: list[dict[str, object]] = []
    for source in plan.entities:
        entity = copy.deepcopy(dict(source))
        paper_uid = str(entity.get("paper_uid") or "")
        entity["payload"] = visit(entity.get("payload") or {}, paper_uid=paper_uid)
        entities.append(entity)
    return PortableExportPlan(
        papers=plan.papers,
        entities=tuple(entities),
        dropped_by_reason=plan.dropped_by_reason,
        private_source_sha256=plan.private_source_sha256,
    )


def load_approved_paper_uids(path: Path | str) -> frozenset[str]:
    """Read one canonical paper_uid per non-empty UTF-8 line."""

    source = Path(path).expanduser()
    descriptor = -1
    try:
        descriptor = os.open(
            source,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 2 * 1024 * 1024:
            raise RuntimeError("批准论文清单不存在或不安全")
        payload = b""
        while len(payload) <= 2 * 1024 * 1024:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            payload += chunk
        if len(payload) != metadata.st_size:
            raise RuntimeError("批准论文清单读取过程中发生变化")
        lines = payload.decode("utf-8").splitlines()
    except RuntimeError:
        raise
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeError("批准论文清单必须是 UTF-8 文本") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return normalize_approved_paper_uids(line for line in lines if line.strip())


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _binary_asset_permissions(
    values: dict[str, Path | str],
) -> dict[str, BinaryAssetPermission]:
    permissions: dict[str, BinaryAssetPermission] = {}
    for entity_uid, raw_path in sorted(values.items()):
        path = Path(raw_path).expanduser()
        suffix = path.suffix.casefold()
        if suffix == ".png":
            media_type = "image/png"
        elif suffix in {".jpg", ".jpeg"}:
            media_type = "image/jpeg"
        else:
            raise RuntimeError("官方二进制资产只允许 PNG 或 JPEG")
        permissions[str(entity_uid)] = BinaryAssetPermission(
            sha256=_sha256_file(path), media_type=media_type
        )
    return permissions


def load_path_mapping(path: Path | str) -> dict[str, Path]:
    source = Path(path).expanduser()
    if source.is_symlink() or not source.is_file() or source.stat().st_size > 4 * 1024 * 1024:
        raise RuntimeError("资产路径映射不存在或不安全")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("资产路径映射必须是 UTF-8 JSON 对象") from exc
    if not isinstance(value, dict) or not value:
        raise RuntimeError("资产路径映射必须是非空对象")
    output: dict[str, Path] = {}
    for identity, raw_path in value.items():
        key = str(identity).strip()
        candidate = Path(str(raw_path)).expanduser()
        if not key or candidate.is_symlink() or not candidate.is_file():
            raise RuntimeError("资产路径映射包含无效文件")
        output[key] = candidate.resolve()
    return output


def initialize_preview_signing_key(path: Path | str) -> Ed25519PrivateKey:
    key_path = Path(path).expanduser()
    key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if key_path.parent.is_symlink() or key_path.is_symlink():
        raise RuntimeError("内部预览签名密钥路径不安全")
    if key_path.exists():
        raise RuntimeError("内部预览签名密钥已存在；拒绝静默轮换")
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    descriptor = os.open(
        key_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
        0o600,
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise RuntimeError("内部预览签名密钥写入失败")
            view = view[written:]
        os.fsync(descriptor)
    except Exception:
        try:
            key_path.unlink()
        except OSError:
            pass
        raise
    finally:
        os.close(descriptor)
    return key


def load_preview_signing_key(path: Path | str) -> Ed25519PrivateKey:
    key_path = Path(path).expanduser()
    if key_path.is_symlink() or not key_path.exists():
        raise RuntimeError("内部预览签名密钥不存在或路径不安全")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(key_path, flags)
    except OSError as exc:
        raise RuntimeError("无法安全读取内部预览签名密钥") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise RuntimeError("内部预览签名密钥权限或所有者无效")
        raw = b""
        while len(raw) < 33:
            chunk = os.read(descriptor, 33 - len(raw))
            if not chunk:
                break
            raw += chunk
    finally:
        os.close(descriptor)
    if len(raw) != 32:
        raise RuntimeError("内部预览签名密钥长度无效")
    return Ed25519PrivateKey.from_private_bytes(raw)


def preview_public_key_base64(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def build_internal_preview_package(
    *,
    source_snapshot: Path | str,
    output_directory: Path | str,
    signing_key_path: Path | str,
    expected_source_sha256: str,
    approved_paper_uids: Iterable[str],
    package_id: str = "auto-research-internal-evidence",
    package_version: str = "0.2.0-preview.1",
    signer_key_id: str = "auto-research-internal-preview-2026-v1",
    current_app_version: str = "0.6.0-preview.1",
    app_minimum: str = "0.6.0",
    app_maximum_exclusive: str = "1.0.0",
    paper_pdf_paths: dict[str, Path | str] | None = None,
    binary_assets: dict[str, Path | str] | None = None,
) -> InternalPreviewBuildReport:
    output = Path(output_directory).expanduser().resolve()
    if output.exists() or output.is_symlink():
        raise RuntimeError("输出目录已存在，拒绝覆盖")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent)
    )
    try:
        report = _build_internal_preview_package_in_directory(
            source_snapshot=source_snapshot,
            staging_directory=staging,
            signing_key_path=signing_key_path,
            expected_source_sha256=expected_source_sha256,
            approved_paper_uids=approved_paper_uids,
            package_id=package_id,
            package_version=package_version,
            signer_key_id=signer_key_id,
            current_app_version=current_app_version,
            app_minimum=app_minimum,
            app_maximum_exclusive=app_maximum_exclusive,
            paper_pdf_paths=paper_pdf_paths,
            binary_assets=binary_assets,
        )
        replace_file(staging, output)
        return report
    finally:
        if staging.exists():
            best_effort_remove_tree(staging)


def _build_internal_preview_package_in_directory(
    *,
    source_snapshot: Path | str,
    staging_directory: Path,
    signing_key_path: Path | str,
    expected_source_sha256: str,
    approved_paper_uids: Iterable[str],
    package_id: str,
    package_version: str,
    signer_key_id: str,
    current_app_version: str,
    app_minimum: str,
    app_maximum_exclusive: str,
    paper_pdf_paths: dict[str, Path | str] | None = None,
    binary_assets: dict[str, Path | str] | None = None,
) -> InternalPreviewBuildReport:
    output = staging_directory
    repository_root = output / f"{package_id}-{package_version}-repository"
    package_path = output / f"{package_id}-{package_version}.aresearch"
    report_path = output / f"{package_id}-{package_version}-build-report.json"
    for candidate in (repository_root, package_path, report_path):
        if candidate.exists() or candidate.is_symlink():
            raise RuntimeError(f"输出已存在，拒绝覆盖：{candidate.name}")

    expected_digest = str(expected_source_sha256).strip().lower()
    if len(expected_digest) != 64 or any(
        character not in "0123456789abcdef" for character in expected_digest
    ):
        raise RuntimeError("必须提供有效的稳定源快照 SHA-256")
    plan = plan_evidence_v12_export(source_snapshot)
    if plan.private_source_sha256 != expected_digest:
        raise RuntimeError("稳定源快照 SHA-256 不匹配，拒绝构建资料包")
    unexplained_drops = {
        str(reason): int(count)
        for reason, count in plan.dropped_by_reason.items()
        if int(count) != 0
    }
    if unexplained_drops:
        raise RuntimeError("官方资料包发布要求 dropped_by_reason 全部为零")
    approved = normalize_approved_paper_uids(approved_paper_uids)
    planned = frozenset(str(paper["paper_uid"]) for paper in plan.papers)
    if approved != planned:
        missing = len(planned - approved)
        extra = len(approved - planned)
        raise RuntimeError(
            f"批准论文清单与待发布论文不一致（缺少 {missing}，多出 {extra}）"
        )
    plan = _budget_official_excerpts(plan)
    pdf_rows = ()
    pdf_payloads: dict[str, Path] = {}
    if paper_pdf_paths is not None:
        pdf_rows, pdf_payloads = plan_official_pdf_payloads(
            paper_pdf_paths, expected_paper_uids=approved
        )
    asset_sources = dict(binary_assets or {})
    asset_permissions = _binary_asset_permissions(asset_sources)
    repository = materialize_portable_repository(
        plan,
        repository_root,
        package_id=package_id,
        package_version=package_version,
        release_policy=ReleasePolicy(
            distribution_scope=(
                OFFICIAL_DISTRIBUTION_SCOPE
                if pdf_rows or asset_sources
                else "internal-preview-only"
            ),
            allowed_paper_uids=approved,
            allow_structured_evidence=True,
            allow_short_excerpts=True,
            maximum_excerpt_chars=MAX_EXCERPT_CHARS,
            maximum_excerpt_chars_per_paper=MAX_EXCERPT_CHARS_PER_PAPER,
            maximum_excerpt_chars_total=MAX_EXCERPT_CHARS_TOTAL,
            binary_asset_allowlist=asset_permissions,
            accepted_dropped_by_reason={},
        ),
        provenance=provenance_for_papers(
            plan.papers, publisher="Auto Research internal preview"
        ),
        binary_assets=asset_sources,
    )
    type_counts = Counter(str(entity["entity_type"]) for entity in plan.entities)
    counts = {
        "papers": repository.paper_count,
        "entities": repository.entity_count,
        "items": type_counts["item"],
        "findings": type_counts["finding"],
        "tables": type_counts["table"],
        "figures": type_counts["figure"],
    }
    publisher_name = "Auto Research internal preview"
    publisher = assert_trusted_package_identity(
        key_id=signer_key_id,
        package_id=package_id,
        publisher_name=publisher_name,
    )
    key = load_preview_signing_key(signing_key_path)
    registered_key = trusted_public_keys(channel=publisher.channel).get(signer_key_id)
    if registered_key != key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ):
        raise RuntimeError("签名私钥与应用内置信任公钥不匹配")
    manifest = {
        "format": "auto-research-evidence-package",
        "format_version": 1,
        "package_id": package_id,
        "package_version": package_version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "publisher": {"name": publisher_name},
        "evidence_schema": DISTRIBUTION_SCHEMA_VERSION,
        "database_contract": DATABASE_CONTRACT,
        "identity_version": IDENTITY_VERSION,
        "content_fingerprint": repository.content_fingerprint,
        "content_counts": counts,
        "app_compatibility": {
            "minimum": app_minimum,
            "maximum_exclusive": app_maximum_exclusive,
        },
        "database_path": DATABASE_PATH,
        "rights_path": RIGHTS_PATH,
        "provenance_path": PROVENANCE_PATH,
    }
    if pdf_rows or asset_sources:
        if not pdf_rows:
            raise RuntimeError("official-package-v2 必须包含完整 PDF 清单")
        manifest.update(
            {
                "official_package_contract": OFFICIAL_PACKAGE_CONTRACT_V2,
                "distribution_scope": OFFICIAL_DISTRIBUTION_SCOPE,
                "rights_notice": "仅限课题组内部非商业使用；未声明逐篇再分发权已清理。",
                "paper_pdfs": [row.manifest_dict() for row in pdf_rows],
                "asset_counts": {
                    "paper_pdfs": len(pdf_rows),
                    "visual_assets": repository.asset_count,
                },
            }
        )
    payload_files: dict[str, Path] = {
        DATABASE_PATH: repository.database_path,
        RIGHTS_PATH: repository.rights_path,
        PROVENANCE_PATH: repository.provenance_path,
    }
    for candidate in repository.root.rglob("*"):
        if candidate.is_file():
            payload_files.setdefault(
                candidate.relative_to(repository.root).as_posix(), candidate
            )
    payload_files.update(pdf_payloads)
    build_evidence_package(
        package_path,
        manifest=manifest,
        payload_files=payload_files,
        signing_key=key,
        signer_key_id=signer_key_id,
    )
    trusted = trusted_public_keys(channel=publisher.channel)
    verified = verify_evidence_package(
        package_path,
        trusted_public_keys=trusted,
        current_app_version=current_app_version,
        expected_evidence_schema=DISTRIBUTION_SCHEMA_VERSION,
    )
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="internal-package-import-") as temporary:
        imported = import_official_evidence_package(
            package_path,
            data_root=Path(temporary) / "app-data",
            trusted_public_keys=trusted,
            current_app_version=current_app_version,
        )
        active, active_repository = open_active_official_repository(
            data_root=Path(temporary) / "app-data",
            trusted_public_keys=trusted,
            current_app_version=current_app_version,
        )
        lease = (
            active_repository.open_pdf(next(iter(sorted(approved))))
            if pdf_rows
            else None
        )
        pdf_ready = lease is not None
        if lease is not None:
            lease.close()
        if (
            imported.content_fingerprint != repository.content_fingerprint
            or active.content_fingerprint != repository.content_fingerprint
            or len(active_repository.list_papers()) != repository.paper_count
            or bool(pdf_rows) != pdf_ready
        ):
            raise RuntimeError("内部预览资料包导入后验收失败")
    elapsed = time.perf_counter() - started
    report = InternalPreviewBuildReport(
        package_id=package_id,
        package_version=package_version,
        signer_key_id=signer_key_id,
        package_path=f"{package_id}-{package_version}.aresearch",
        package_size_bytes=package_path.stat().st_size,
        package_sha256=_sha256_file(package_path),
        manifest_sha256=verified.manifest_sha256,
        repository_sha256=repository.database_sha256,
        content_fingerprint=repository.content_fingerprint,
        source_snapshot_sha256=str(plan.private_source_sha256 or ""),
        paper_count=repository.paper_count,
        entity_count=repository.entity_count,
        item_count=type_counts["item"],
        finding_count=type_counts["finding"],
        table_count=type_counts["table"],
        figure_count=type_counts["figure"],
        dropped_by_reason=dict(plan.dropped_by_reason),
        verified_import_seconds=round(elapsed, 3),
        distribution_scope=(
            OFFICIAL_DISTRIBUTION_SCOPE if pdf_rows else "internal-preview-only"
        ),
        includes_pdfs=bool(pdf_rows),
        includes_binary_assets=bool(repository.asset_count),
        pdf_count=len(pdf_rows),
        binary_asset_count=repository.asset_count,
    )
    report_path.write_text(
        json.dumps(report.public_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建 Auto Research 内部预览资料包")
    parser.add_argument("--source-snapshot", required=True)
    parser.add_argument("--output-directory", required=True)
    parser.add_argument("--signing-key", required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--approved-paper-uids-file", required=True)
    parser.add_argument("--package-version", default="0.2.0-preview.1")
    parser.add_argument("--current-app-version", default="0.6.0-preview.1")
    parser.add_argument("--app-minimum", default="0.6.0")
    parser.add_argument("--app-maximum-exclusive", default="1.0.0")
    parser.add_argument("--paper-pdf-map")
    parser.add_argument("--binary-asset-map")
    parser.add_argument("--initialize-signing-key", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.initialize_signing_key:
        initialize_preview_signing_key(arguments.signing_key)
    report = build_internal_preview_package(
        source_snapshot=arguments.source_snapshot,
        output_directory=arguments.output_directory,
        signing_key_path=arguments.signing_key,
        expected_source_sha256=arguments.expected_source_sha256,
        approved_paper_uids=load_approved_paper_uids(
            arguments.approved_paper_uids_file
        ),
        package_version=arguments.package_version,
        current_app_version=arguments.current_app_version,
        app_minimum=arguments.app_minimum,
        app_maximum_exclusive=arguments.app_maximum_exclusive,
        paper_pdf_paths=(
            load_path_mapping(arguments.paper_pdf_map)
            if arguments.paper_pdf_map
            else None
        ),
        binary_assets=(
            load_path_mapping(arguments.binary_asset_map)
            if arguments.binary_asset_map
            else None
        ),
    )
    print(json.dumps(report.public_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
