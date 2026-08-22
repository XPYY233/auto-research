from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping


OFFICIAL_PACKAGE_CONTRACT_V2 = "official-package-v2"
OFFICIAL_DISTRIBUTION_SCOPE = "internal-group-restricted"
OFFICIAL_PDF_RIGHTS = "internal-group-noncommercial-rights-unverified"
MAX_OFFICIAL_PDF_BYTES = 1024 * 1024 * 1024
MAX_OFFICIAL_PDFS_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
PAPER_UID_RE = re.compile(r"^paper_[0-9a-f]{32}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PORTABLE_PATH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


class OfficialPackageAssetError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OfficialPaperPdf:
    paper_uid: str
    relative_path: str
    sha256: str
    size_bytes: int
    rights: str = OFFICIAL_PDF_RIGHTS

    def manifest_dict(self) -> dict[str, Any]:
        return {
            "paper_uid": self.paper_uid,
            "path": self.relative_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "media_type": "application/pdf",
            "rights": self.rights,
        }


class OfficialPdfLease:
    def __init__(
        self,
        descriptor: int,
        *,
        source_id: str,
        paper_uid: str,
        size_bytes: int,
    ) -> None:
        self._descriptor = descriptor
        self._closed = False
        self.source_id = source_id
        self.paper_uid = paper_uid
        self.size_bytes = size_bytes
        self.media_type = "application/pdf"

    def read(self, size: int = 1024 * 1024) -> bytes:
        if self._closed:
            raise ValueError("PDF lease is closed")
        if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 4 * 1024 * 1024:
            raise ValueError("PDF lease read size is invalid")
        return os.read(self._descriptor, size)

    def close(self) -> None:
        if not self._closed:
            os.close(self._descriptor)
            self._closed = True

    def public_metadata(self) -> dict[str, Any]:
        return {
            "schema_version": "official-pdf-lease-v1",
            "source_scope": "official",
            "source_id": self.source_id,
            "paper_uid": self.paper_uid,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
        }

    def __enter__(self) -> "OfficialPdfLease":
        if self._closed:
            raise ValueError("PDF lease is closed")
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - defensive cleanup
        try:
            self.close()
        except Exception:
            pass


def _portable_relative_path(value: object) -> str:
    raw = str(value or "")
    path = PurePosixPath(raw)
    if (
        not raw
        or not PORTABLE_PATH_RE.fullmatch(raw)
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or raw != path.as_posix()
    ):
        raise OfficialPackageAssetError(
            "official_asset_manifest_invalid", "官方资料包包含无效资产路径"
        )
    return raw


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _regular_pdf(path: Path) -> tuple[str, int]:
    if path.is_symlink() or not path.is_file():
        raise OfficialPackageAssetError(
            "official_pdf_invalid", "官方资料包 PDF 缺失或不是普通文件"
        )
    digest, size = _sha256_file(path)
    if size < 5 or size > MAX_OFFICIAL_PDF_BYTES:
        raise OfficialPackageAssetError(
            "official_pdf_size", "官方资料包 PDF 为空或超过安全上限"
        )
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise OfficialPackageAssetError(
                "official_pdf_invalid", "官方资料包文件不是有效 PDF"
            )
    return digest, size


def plan_official_pdf_payloads(
    paper_pdf_paths: Mapping[str, Path | str],
    *,
    expected_paper_uids: Iterable[str],
) -> tuple[tuple[OfficialPaperPdf, ...], dict[str, Path]]:
    """Create a fail-closed PDF inventory from an explicit maintainer mapping."""

    expected = {str(value) for value in expected_paper_uids}
    supplied = {str(value) for value in paper_pdf_paths}
    if not expected or any(not PAPER_UID_RE.fullmatch(value) for value in expected):
        raise OfficialPackageAssetError(
            "official_pdf_identity", "官方资料包论文身份无效"
        )
    if supplied != expected:
        raise OfficialPackageAssetError(
            "official_pdf_coverage", "官方资料包必须为每篇论文显式提供 PDF"
        )
    rows: list[OfficialPaperPdf] = []
    payloads: dict[str, Path] = {}
    total = 0
    for paper_uid in sorted(expected):
        source = Path(paper_pdf_paths[paper_uid]).expanduser().resolve()
        digest, size = _regular_pdf(source)
        total += size
        if total > MAX_OFFICIAL_PDFS_TOTAL_BYTES:
            raise OfficialPackageAssetError(
                "official_pdf_total_size", "官方资料包 PDF 总大小超过 2 GB 上限"
            )
        relative = f"papers/{paper_uid}.pdf"
        rows.append(OfficialPaperPdf(paper_uid, relative, digest, size))
        payloads[relative] = source
    return tuple(rows), payloads


def validate_official_package_asset_manifest(
    manifest: Mapping[str, Any],
    *,
    install_root: Path | str | None = None,
    expected_paper_uids: Iterable[str] | None = None,
) -> tuple[OfficialPaperPdf, ...]:
    contract = manifest.get("official_package_contract")
    if contract is None:
        return ()
    if contract != OFFICIAL_PACKAGE_CONTRACT_V2:
        raise OfficialPackageAssetError(
            "official_asset_contract_unsupported", "官方资料包资产契约不受支持"
        )
    if manifest.get("distribution_scope") != OFFICIAL_DISTRIBUTION_SCOPE:
        raise OfficialPackageAssetError(
            "official_asset_scope_invalid", "官方资料包必须声明课题组内部受限使用"
        )
    rows = manifest.get("paper_pdfs")
    if not isinstance(rows, list) or not rows:
        raise OfficialPackageAssetError(
            "official_pdf_inventory_missing", "官方资料包缺少 PDF 清单"
        )
    expected = None if expected_paper_uids is None else {str(value) for value in expected_paper_uids}
    output: list[OfficialPaperPdf] = []
    seen_papers: set[str] = set()
    seen_paths: set[str] = set()
    total = 0
    root = Path(install_root).expanduser() if install_root is not None else None
    for raw in rows:
        if not isinstance(raw, Mapping) or set(raw) != {
            "paper_uid", "path", "sha256", "size_bytes", "media_type", "rights"
        }:
            raise OfficialPackageAssetError(
                "official_pdf_inventory_invalid", "官方资料包 PDF 清单字段无效"
            )
        paper_uid = str(raw.get("paper_uid") or "")
        relative = _portable_relative_path(raw.get("path"))
        digest = str(raw.get("sha256") or "")
        try:
            size = int(raw.get("size_bytes"))
        except (TypeError, ValueError) as exc:
            raise OfficialPackageAssetError(
                "official_pdf_inventory_invalid", "官方资料包 PDF 大小无效"
            ) from exc
        if (
            not PAPER_UID_RE.fullmatch(paper_uid)
            or relative != f"papers/{paper_uid}.pdf"
            or not SHA256_RE.fullmatch(digest)
            or not 5 <= size <= MAX_OFFICIAL_PDF_BYTES
            or raw.get("media_type") != "application/pdf"
            or raw.get("rights") != OFFICIAL_PDF_RIGHTS
            or paper_uid in seen_papers
            or relative.casefold() in seen_paths
        ):
            raise OfficialPackageAssetError(
                "official_pdf_inventory_invalid", "官方资料包 PDF 清单身份不一致"
            )
        total += size
        if total > MAX_OFFICIAL_PDFS_TOTAL_BYTES:
            raise OfficialPackageAssetError(
                "official_pdf_total_size", "官方资料包 PDF 总大小超过 2 GB 上限"
            )
        if root is not None:
            candidate = root.joinpath(*PurePosixPath(relative).parts)
            actual_digest, actual_size = _regular_pdf(candidate)
            if actual_digest != digest or actual_size != size:
                raise OfficialPackageAssetError(
                    "official_pdf_changed", "官方资料包 PDF 校验失败"
                )
        seen_papers.add(paper_uid)
        seen_paths.add(relative.casefold())
        output.append(OfficialPaperPdf(paper_uid, relative, digest, size))
    if expected is not None and seen_papers != expected:
        raise OfficialPackageAssetError(
            "official_pdf_coverage", "官方资料包 PDF 与论文清单不一致"
        )
    return tuple(sorted(output, key=lambda value: value.paper_uid))


def open_official_pdf_lease(
    root: Path,
    row: OfficialPaperPdf,
    *,
    source_id: str,
) -> OfficialPdfLease:
    path = root.joinpath(*PurePosixPath(row.relative_path).parts)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise OfficialPackageAssetError(
            "official_pdf_changed", "官方资料包 PDF 缺失或发生变化"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or int(metadata.st_size) != row.size_bytes:
            raise OfficialPackageAssetError(
                "official_pdf_changed", "官方资料包 PDF 缺失或发生变化"
            )
        digest = hashlib.sha256()
        header = os.read(descriptor, 5)
        digest.update(header)
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            header != b"%PDF-"
            or int(after.st_size) != row.size_bytes
            or digest.hexdigest() != row.sha256
        ):
            raise OfficialPackageAssetError(
                "official_pdf_changed", "官方资料包 PDF 缺失或发生变化"
            )
        os.lseek(descriptor, 0, os.SEEK_SET)
        return OfficialPdfLease(
            descriptor,
            source_id=source_id,
            paper_uid=row.paper_uid,
            size_bytes=row.size_bytes,
        )
    except Exception:
        os.close(descriptor)
        raise


__all__ = [
    "OFFICIAL_DISTRIBUTION_SCOPE",
    "OFFICIAL_PACKAGE_CONTRACT_V2",
    "OFFICIAL_PDF_RIGHTS",
    "OfficialPackageAssetError",
    "OfficialPaperPdf",
    "OfficialPdfLease",
    "open_official_pdf_lease",
    "plan_official_pdf_payloads",
    "validate_official_package_asset_manifest",
]
