from __future__ import annotations

import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Callable, Protocol, Sequence


PACKAGE_EXTENSION = ".aresearch"
MAX_WINDOWS_PACKAGE_PATH_CHARS = 1_024
ALLOWED_INPUT_SOURCES = frozenset({"file-picker", "drag-drop", "file-association"})
WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul", "clock$"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)


class PackageInputError(RuntimeError):
    """Path-redacted error raised before a package reaches the importer."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LocalFileIdentity:
    canonical_path: str
    size: int
    device: int
    inode: int
    modified_ns: int


class FileSystemProbe(Protocol):
    def inspect_regular_local_file(self, raw_path: str) -> LocalFileIdentity: ...


def _validate_windows_path_text(raw_path: str) -> PureWindowsPath:
    value = str(raw_path or "")
    if not value or len(value) > MAX_WINDOWS_PACKAGE_PATH_CHARS:
        raise PackageInputError("invalid_path", "资料包路径为空或过长")
    normalized = value.replace("/", "\\")
    folded = normalized.casefold()
    if (
        normalized.startswith("\\\\")
        or folded.startswith(("\\\\?\\", "\\\\.\\", "\\??\\", "\\device\\"))
    ):
        raise PackageInputError("non_local_path", "只接受本机磁盘中的资料包")
    path = PureWindowsPath(normalized)
    if not path.is_absolute() or not path.drive or ".." in path.parts:
        raise PackageInputError("invalid_path", "资料包必须是本机绝对路径")
    if normalized.count(":") != 1 or normalized[1:2] != ":":
        raise PackageInputError("invalid_path", "资料包路径不能包含数据流或额外设备名")
    for part in path.parts[1:]:
        normalized_part = part.rstrip(" .")
        base = normalized_part.split(".", 1)[0].casefold()
        if not normalized_part or base in WINDOWS_RESERVED_NAMES:
            raise PackageInputError("invalid_path", "资料包路径包含 Windows 保留名称")
    if path.suffix.casefold() != PACKAGE_EXTENSION:
        raise PackageInputError("wrong_extension", "请选择一个 .aresearch 资料包")
    return path


class WindowsFileSystemProbe:
    """Inspect a package without following links or accepting reparse points."""

    def inspect_regular_local_file(self, raw_path: str) -> LocalFileIdentity:
        _validate_windows_path_text(raw_path)
        if os.name != "nt":
            raise PackageInputError("windows_only", "本地资料包检查只能在 Windows 运行")
        candidate = Path(raw_path)
        try:
            metadata = candidate.lstat()
        except OSError as exc:
            raise PackageInputError("unavailable", "所选资料包无法读取") from exc
        attributes = int(getattr(metadata, "st_file_attributes", 0))
        if candidate.is_symlink() or attributes & WINDOWS_REPARSE_POINT:
            raise PackageInputError("link_rejected", "资料包不能是链接或重解析点")
        if not stat.S_ISREG(metadata.st_mode):
            raise PackageInputError("not_regular_file", "请选择单个普通资料包文件")
        try:
            canonical = candidate.resolve(strict=True)
        except OSError as exc:
            raise PackageInputError("unavailable", "所选资料包无法安全解析") from exc
        _validate_windows_path_text(str(canonical))
        return LocalFileIdentity(
            canonical_path=str(canonical),
            size=int(metadata.st_size),
            device=int(metadata.st_dev),
            inode=int(metadata.st_ino),
            modified_ns=int(metadata.st_mtime_ns),
        )


class PackageInputHandle:
    """Opaque UI-safe handle. It never renders or exposes the accepted path."""

    __slots__ = ("handle_id", "source", "_broker_token", "_identity")

    def __init__(
        self,
        *,
        handle_id: str,
        source: str,
        broker_token: object,
        identity: LocalFileIdentity,
    ) -> None:
        self.handle_id = handle_id
        self.source = source
        self._broker_token = broker_token
        self._identity = identity

    def public_result(self) -> dict[str, object]:
        return {
            "accepted": True,
            "handle_id": self.handle_id,
            "source": self.source,
            "filename": "selected.aresearch",
        }

    def __repr__(self) -> str:
        return f"PackageInputHandle(handle_id={self.handle_id!r}, source={self.source!r})"


class PackageInputBroker:
    """Validate UI inputs and release a controlled path only to the importer."""

    def __init__(
        self,
        probe: FileSystemProbe | None = None,
        *,
        handle_factory: Callable[[], str] = lambda: secrets.token_urlsafe(24),
        native_path_factory: Callable[[str], object] = Path,
    ) -> None:
        self.probe = probe or WindowsFileSystemProbe()
        self.handle_factory = handle_factory
        self.native_path_factory = native_path_factory
        self._broker_token = object()

    def accept(self, candidates: Sequence[str], *, source: str) -> PackageInputHandle:
        if source not in ALLOWED_INPUT_SOURCES:
            raise PackageInputError("invalid_source", "资料包输入来源无效")
        if isinstance(candidates, (str, bytes)) or len(candidates) != 1:
            raise PackageInputError("single_file_required", "一次只能导入一个资料包文件")
        raw_path = str(candidates[0] or "")
        _validate_windows_path_text(raw_path)
        identity = self.probe.inspect_regular_local_file(raw_path)
        canonical = _validate_windows_path_text(identity.canonical_path)
        handle_id = str(self.handle_factory())
        if not handle_id or len(handle_id) < 16:
            raise PackageInputError("handle_failure", "无法建立安全资料包句柄")
        return PackageInputHandle(
            handle_id=handle_id,
            source=source,
            broker_token=self._broker_token,
            identity=LocalFileIdentity(
                canonical_path=str(canonical),
                size=identity.size,
                device=identity.device,
                inode=identity.inode,
                modified_ns=identity.modified_ns,
            ),
        )

    def resolve_for_import(self, handle: PackageInputHandle) -> object:
        if not isinstance(handle, PackageInputHandle) or handle._broker_token is not self._broker_token:
            raise PackageInputError("invalid_handle", "资料包句柄无效或已失去授权")
        current = self.probe.inspect_regular_local_file(handle._identity.canonical_path)
        if current != handle._identity:
            raise PackageInputError("file_changed", "资料包在选择后发生变化，请重新选择")
        return self.native_path_factory(current.canonical_path)
