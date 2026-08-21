from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from auto_research.portable_file_ops import best_effort_remove_tree, replace_file


class ReleaseKitError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseKitArtifact:
    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ReleaseKitResult:
    directory: Path
    zip_path: Path
    zip_sha256: str
    artifacts: tuple[ReleaseKitArtifact, ...]


def _hash(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _safe_source(path: Path | str, *, suffixes: Iterable[str]) -> Path:
    source = Path(path).expanduser()
    if source.is_symlink() or not source.is_file():
        raise ReleaseKitError("发布文件缺失或不安全")
    if source.suffix.casefold() not in {suffix.casefold() for suffix in suffixes}:
        raise ReleaseKitError("发布文件类型不符合套件契约")
    return source.resolve()


def _build_atomic_kit(
    *,
    sources: tuple[Path, ...],
    output_directory: Path | str,
    release_name: str,
) -> ReleaseKitResult:
    if not release_name or any(character in release_name for character in "/\\\0"):
        raise ReleaseKitError("发布套件名称无效")
    names = [source.name for source in sources]
    if len(names) != len(set(names)):
        raise ReleaseKitError("发布文件名冲突")
    if "SHA256SUMS.txt" in names:
        raise ReleaseKitError("SHA256SUMS.txt 由套件生成器独占")
    output = Path(output_directory).expanduser().resolve()
    if output.exists() or output.is_symlink():
        raise ReleaseKitError("发布套件目录已存在，拒绝覆盖")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{release_name}-", dir=output.parent))
    try:
        copied: list[Path] = []
        for source in sources:
            destination = staging / source.name
            shutil.copy2(source, destination)
            copied.append(destination)
        artifacts = tuple(
            ReleaseKitArtifact(path=path, sha256=_hash(path)[0], size_bytes=_hash(path)[1])
            for path in copied
        )
        (staging / "SHA256SUMS.txt").write_text(
            "".join(f"{artifact.sha256}  {artifact.path.name}\n" for artifact in artifacts),
            encoding="utf-8",
        )
        replace_file(staging, output)
    finally:
        if staging.exists():
            best_effort_remove_tree(staging)

    zip_path = output.parent / f"{release_name}.zip"
    if zip_path.exists() or zip_path.is_symlink():
        raise ReleaseKitError("发布 ZIP 已存在，拒绝覆盖")
    temporary_zip = output.parent / f".{release_name}.{os.getpid()}.zip"
    try:
        with zipfile.ZipFile(
            temporary_zip,
            "x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            for path in sorted(output.iterdir(), key=lambda item: item.name):
                if path.is_symlink() or not path.is_file():
                    raise ReleaseKitError("发布套件包含不安全文件")
                archive.write(path, arcname=f"{release_name}/{path.name}")
        replace_file(temporary_zip, zip_path)
    finally:
        if temporary_zip.exists():
            temporary_zip.unlink()
    zip_sha256, _ = _hash(zip_path)
    zip_path.with_suffix(zip_path.suffix + ".sha256").write_text(
        f"{zip_sha256}  {zip_path.name}\n",
        encoding="utf-8",
    )
    return ReleaseKitResult(
        directory=output,
        zip_path=zip_path,
        zip_sha256=zip_sha256,
        artifacts=tuple(
            ReleaseKitArtifact(
                path=output / artifact.path.name,
                sha256=artifact.sha256,
                size_bytes=artifact.size_bytes,
            )
            for artifact in artifacts
        ),
    )


def build_macos_release_kit(
    *,
    dmg_path: Path | str,
    official_package_path: Path | str,
    quickstart_path: Path | str,
    output_directory: Path | str,
    release_name: str,
) -> ReleaseKitResult:
    dmg = _safe_source(dmg_path, suffixes={".dmg"})
    package = _safe_source(official_package_path, suffixes={".aresearch"})
    quickstart = _safe_source(quickstart_path, suffixes={".md", ".txt", ".pdf"})
    return _build_atomic_kit(
        sources=(dmg, package, quickstart),
        output_directory=output_directory,
        release_name=release_name,
    )


def build_macos_user_kit(
    *,
    files: Iterable[Path | str],
    output_directory: Path | str,
    release_name: str,
) -> ReleaseKitResult:
    """Build an atomic end-user kit from an explicit, non-source allowlist.

    The caller supplies every artifact intentionally.  Git bundles, database
    files and directories are rejected so a historical scientific database
    cannot accidentally enter an end-user release archive.
    """

    allowed_suffixes = {
        ".aresearch",
        ".csv",
        ".dmg",
        ".json",
        ".md",
        ".sha256",
        ".txt",
    }
    sources = tuple(_safe_source(path, suffixes=allowed_suffixes) for path in files)
    if not sources:
        raise ReleaseKitError("用户套件至少需要一个发布文件")
    if not any(source.suffix.casefold() == ".dmg" for source in sources):
        raise ReleaseKitError("用户套件缺少 macOS 安装镜像")
    if not any(source.suffix.casefold() == ".aresearch" for source in sources):
        raise ReleaseKitError("用户套件缺少资料包")
    return _build_atomic_kit(
        sources=sources,
        output_directory=output_directory,
        release_name=release_name,
    )
