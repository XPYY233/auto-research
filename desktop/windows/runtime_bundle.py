from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping


BUNDLE_CONTRACT_VERSION = 1
RUNTIME_MANIFEST_NAME = "bundled-runtime-manifest.json"
ENTRYPOINT_NAME = "Auto Research.exe"
REQUIRED_COMPONENTS = frozenset(
    {
        "python-runtime",
        "python-stdlib",
        "sqlite-runtime",
        "cryptography",
        "pymupdf",
        "pywebview",
        "webview2-loader",
        "auto-research-core",
        "shared-web-assets",
    }
)
FORBIDDEN_EXTERNAL_COMMANDS = frozenset({"python", "python3", "py", "git", "node", "npm"})
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_PART_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._ -]{0,126}$")
WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul", "clock$"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)


class RuntimeBundleError(RuntimeError):
    """Raised when a Windows candidate is not genuinely self-contained."""


@dataclass(frozen=True)
class RuntimeBundleReport:
    desktop_version: str
    python_version: str
    architecture: str
    component_count: int
    total_size: int
    entrypoint: Path


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _safe_relative(value: str) -> PurePosixPath:
    text = str(value or "")
    if not text or "\\" in text or "\x00" in text:
        raise RuntimeBundleError("内置运行时路径必须使用安全包内相对路径")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RuntimeBundleError("内置运行时路径不能是绝对路径或包含上级跳转")
    if any(
        not SAFE_PART_RE.fullmatch(part)
        or part.endswith((" ", "."))
        or part.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES
        for part in path.parts
    ):
        raise RuntimeBundleError("内置运行时路径包含不稳定字符")
    return path


def _candidate_file(root: Path, relative: PurePosixPath) -> Path:
    candidate = root.joinpath(*relative.parts)
    try:
        resolved_root = root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise RuntimeBundleError(f"内置运行时文件缺失或越界：{relative.as_posix()}") from exc
    if candidate.is_symlink() or not resolved.is_file():
        raise RuntimeBundleError(f"内置运行时组件必须是普通文件：{relative.as_posix()}")
    return resolved


def create_runtime_manifest(
    candidate_root: Path,
    *,
    desktop_version: str,
    python_version: str,
    components: Mapping[str, str],
) -> dict[str, object]:
    root = candidate_root.expanduser().resolve(strict=True)
    if set(components) != REQUIRED_COMPONENTS:
        missing = sorted(REQUIRED_COMPONENTS - set(components))
        extra = sorted(set(components) - REQUIRED_COMPONENTS)
        raise RuntimeBundleError(
            f"内置运行时组件清单不完整；缺少 {missing or '无'}，额外 {extra or '无'}"
        )
    entrypoint_relative = _safe_relative(ENTRYPOINT_NAME)
    entrypoint = _candidate_file(root, entrypoint_relative)
    entries: dict[str, dict[str, object]] = {}
    casefold_paths: set[str] = {entrypoint_relative.as_posix().casefold()}
    for name in sorted(components):
        relative = _safe_relative(components[name])
        folded = relative.as_posix().casefold()
        if folded in casefold_paths:
            raise RuntimeBundleError("内置运行时路径在 Windows 上发生大小写冲突")
        casefold_paths.add(folded)
        path = _candidate_file(root, relative)
        digest, size = _sha256_file(path)
        entries[name] = {
            "path": relative.as_posix(),
            "sha256": digest,
            "size": size,
        }
    entrypoint_hash, entrypoint_size = _sha256_file(entrypoint)
    return {
        "contract_version": BUNDLE_CONTRACT_VERSION,
        "desktop_version": str(desktop_version),
        "target": "windows-x64",
        "python": {"version": str(python_version), "bundled": True},
        "entrypoint": {
            "path": entrypoint_relative.as_posix(),
            "sha256": entrypoint_hash,
            "size": entrypoint_size,
        },
        "components": entries,
        "external_commands": [],
        "required_environment_variables": [],
        "requires_source_checkout": False,
    }


def write_runtime_manifest(candidate_root: Path, manifest: Mapping[str, object]) -> Path:
    destination = candidate_root / RUNTIME_MANIFEST_NAME
    destination.write_text(
        json.dumps(dict(manifest), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def verify_runtime_bundle(candidate_root: Path) -> RuntimeBundleReport:
    root = candidate_root.expanduser().resolve(strict=True)
    manifest_path = root / RUNTIME_MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeBundleError("Windows 候选缺少有效内置运行时清单") from exc
    if not isinstance(manifest, dict) or manifest.get("contract_version") != BUNDLE_CONTRACT_VERSION:
        raise RuntimeBundleError("Windows 内置运行时契约版本无效")
    if manifest.get("target") != "windows-x64":
        raise RuntimeBundleError("Windows 内置运行时目标必须是 x64")
    python = manifest.get("python")
    if not isinstance(python, dict) or python.get("bundled") is not True:
        raise RuntimeBundleError("Windows 候选没有声明内置 Python")
    python_version = str(python.get("version") or "")
    if not python_version.startswith("3.12."):
        raise RuntimeBundleError("Windows 候选必须内置经过冻结的 Python 3.12.x")
    if manifest.get("external_commands") != []:
        commands = {str(value).casefold() for value in manifest.get("external_commands", [])}
        forbidden = sorted(commands & FORBIDDEN_EXTERNAL_COMMANDS)
        raise RuntimeBundleError(f"Windows 候选依赖外部命令：{forbidden or sorted(commands)}")
    if manifest.get("required_environment_variables") != []:
        raise RuntimeBundleError("Windows 候选不得要求用户配置环境变量")
    if manifest.get("requires_source_checkout") is not False:
        raise RuntimeBundleError("Windows 候选不得依赖源码目录")

    entrypoint = manifest.get("entrypoint")
    if not isinstance(entrypoint, dict):
        raise RuntimeBundleError("Windows 候选缺少程序入口清单")
    entrypoint_path = _verify_entry(root, entrypoint, expected_path=ENTRYPOINT_NAME)

    components = manifest.get("components")
    if not isinstance(components, dict) or set(components) != REQUIRED_COMPONENTS:
        raise RuntimeBundleError("Windows 候选内置依赖组件不完整")
    total_size = int(entrypoint["size"])
    seen_paths = {ENTRYPOINT_NAME.casefold()}
    for name in sorted(REQUIRED_COMPONENTS):
        entry = components[name]
        if not isinstance(entry, dict):
            raise RuntimeBundleError(f"Windows 组件清单无效：{name}")
        path = _verify_entry(root, entry)
        folded = str(path.relative_to(root)).replace(os.sep, "/").casefold()
        if folded in seen_paths:
            raise RuntimeBundleError("Windows 组件路径重复或大小写冲突")
        seen_paths.add(folded)
        total_size += int(entry["size"])
    return RuntimeBundleReport(
        desktop_version=str(manifest.get("desktop_version") or ""),
        python_version=python_version,
        architecture="x64",
        component_count=len(components),
        total_size=total_size,
        entrypoint=entrypoint_path,
    )


def _verify_entry(root: Path, entry: Mapping[str, object], *, expected_path: str | None = None) -> Path:
    relative = _safe_relative(str(entry.get("path") or ""))
    if expected_path is not None and relative.as_posix() != expected_path:
        raise RuntimeBundleError("Windows 程序入口路径无效")
    expected_hash = str(entry.get("sha256") or "")
    try:
        expected_size = int(entry.get("size"))
    except (TypeError, ValueError) as exc:
        raise RuntimeBundleError(f"Windows 组件大小无效：{relative.as_posix()}") from exc
    if expected_size < 0 or not SHA256_RE.fullmatch(expected_hash):
        raise RuntimeBundleError(f"Windows 组件校验信息无效：{relative.as_posix()}")
    path = _candidate_file(root, relative)
    actual_hash, actual_size = _sha256_file(path)
    if actual_size != expected_size or actual_hash != expected_hash:
        raise RuntimeBundleError(f"Windows 内置运行时被修改：{relative.as_posix()}")
    return path
