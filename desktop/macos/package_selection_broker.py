from __future__ import annotations

import os
import stat
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from package_job_state import is_valid_package_selection_id


DEFAULT_SELECTION_TTL_SECONDS = 300.0
DEFAULT_MAX_ACTIVE_SELECTIONS = 32
MAX_NATIVE_PATH_BYTES = 1_024


class PackageSelectionSource(str, Enum):
    FILE_PICKER = "file_picker"
    DRAG_DROP = "drag_drop"
    FILE_ASSOCIATION = "file_association"


class PackageSelectionError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def public_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class PackageSelectionSnapshot:
    selection_id: str
    source: PackageSelectionSource
    size_bytes: int
    expires_in_seconds: int

    def public_dict(self) -> dict[str, Any]:
        return {
            "selection_id": self.selection_id,
            "source": self.source.value,
            "size_bytes": self.size_bytes,
            "expires_in_seconds": self.expires_in_seconds,
        }


@dataclass(frozen=True)
class ResolvedPackageSelection:
    """Internal-only resolved selection; public_dict intentionally omits path."""

    selection_id: str
    source: PackageSelectionSource
    path: Path
    size_bytes: int
    expires_in_seconds: int

    def public_dict(self) -> dict[str, Any]:
        return {
            "selection_id": self.selection_id,
            "source": self.source.value,
            "size_bytes": self.size_bytes,
            "expires_in_seconds": self.expires_in_seconds,
        }


@dataclass(frozen=True)
class _FileIdentity:
    device: int
    inode: int
    size_bytes: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True)
class _SelectionRecord:
    selection_id: str
    source: PackageSelectionSource
    path: Path
    identity: _FileIdentity
    created_at: float
    expires_at: float


LocalVolumeProbe = Callable[[Path], bool | None]
SelectionIdFactory = Callable[[], str]
Clock = Callable[[], float]


_MESSAGES = {
    "package_selection_invalid": ("资料包选择无效，请重新选择文件。", True),
    "package_selection_multiple": ("每次只能选择一个资料包。", True),
    "package_selection_file_url": ("不接受文件网址，请使用系统文件选择器。", True),
    "package_selection_extension": ("请选择 .aresearch 格式的资料包。", True),
    "package_selection_path_too_long": ("所选文件位置过长，请移动后重新选择。", True),
    "package_selection_missing": ("所选资料包已不存在，请重新选择。", True),
    "package_selection_symlink": ("不能选择文件快捷链接，请选择原始资料包。", True),
    "package_selection_not_regular": ("请选择一个普通的本机资料包文件。", True),
    "package_selection_nonlocal": ("资料包必须先保存到本机磁盘。", True),
    "package_selection_locality_unknown": ("无法确认资料包位于本机磁盘，已停止操作。", True),
    "package_selection_expired": ("资料包选择已过期，请重新选择。", True),
    "package_selection_changed": ("资料包在选择后发生变化，请重新选择。", True),
    "package_selection_capacity": ("待处理的资料包选择过多，请稍后再试。", True),
}


def _selection_error(code: str) -> PackageSelectionError:
    message, retryable = _MESSAGES[code]
    return PackageSelectionError(code, message, retryable=retryable)


def macos_local_volume_probe(path: Path) -> bool | None:
    """Use NSURL's volume metadata; absence or ambiguity fails closed."""

    try:
        from Foundation import NSURL, NSURLVolumeIsLocalKey  # type: ignore[import-not-found]

        url = NSURL.fileURLWithPath_(str(path))
        values, error = url.resourceValuesForKeys_error_([NSURLVolumeIsLocalKey], None)
        if error is not None or values is None:
            return None
        value = values.get(NSURLVolumeIsLocalKey)
        if isinstance(value, bool):
            return value
        bool_value = getattr(value, "boolValue", None)
        if callable(bool_value):
            converted = bool_value()
            if converted in (True, False, 0, 1):
                return bool(converted)
        return None
    except Exception:
        return None


def _path_bytes(path: Path) -> int:
    return len(os.fsencode(os.fspath(path)))


def _validate_native_candidate(candidate: object) -> Path:
    if isinstance(candidate, (list, tuple, set, frozenset)):
        raise _selection_error("package_selection_multiple")
    if not isinstance(candidate, (str, os.PathLike)):
        raise _selection_error("package_selection_invalid")
    try:
        raw = os.fspath(candidate)
    except TypeError as exc:
        raise _selection_error("package_selection_invalid") from exc
    if isinstance(raw, bytes):
        raise _selection_error("package_selection_invalid")
    if not raw or "\x00" in raw:
        raise _selection_error("package_selection_invalid")
    if raw.casefold().startswith("file:") or "://" in raw:
        raise _selection_error("package_selection_file_url")
    original = Path(raw).expanduser()
    if not original.is_absolute():
        raise _selection_error("package_selection_invalid")
    if _path_bytes(original) > MAX_NATIVE_PATH_BYTES:
        raise _selection_error("package_selection_path_too_long")
    if original.suffix.casefold() != ".aresearch":
        raise _selection_error("package_selection_extension")
    try:
        original_status = original.lstat()
    except FileNotFoundError as exc:
        raise _selection_error("package_selection_missing") from exc
    except OSError as exc:
        raise _selection_error("package_selection_invalid") from exc
    if stat.S_ISLNK(original_status.st_mode):
        raise _selection_error("package_selection_symlink")
    if not stat.S_ISREG(original_status.st_mode):
        raise _selection_error("package_selection_not_regular")
    canonical = Path(os.path.realpath(original))
    if _path_bytes(canonical) > MAX_NATIVE_PATH_BYTES:
        raise _selection_error("package_selection_path_too_long")
    return canonical


def _secure_file_identity(path: Path) -> _FileIdentity:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as exc:
        raise _selection_error("package_selection_missing") from exc
    except OSError as exc:
        try:
            status = path.lstat()
        except OSError:
            raise _selection_error("package_selection_invalid") from exc
        if stat.S_ISLNK(status.st_mode):
            raise _selection_error("package_selection_symlink") from exc
        raise _selection_error("package_selection_invalid") from exc
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise _selection_error("package_selection_not_regular")
        return _FileIdentity(
            device=int(status.st_dev),
            inode=int(status.st_ino),
            size_bytes=int(status.st_size),
            modified_ns=int(status.st_mtime_ns),
            changed_ns=int(status.st_ctime_ns),
        )
    finally:
        os.close(descriptor)


class PackageSelectionBroker:
    def __init__(
        self,
        *,
        local_volume_probe: LocalVolumeProbe = macos_local_volume_probe,
        selection_id_factory: SelectionIdFactory | None = None,
        clock: Clock = time.monotonic,
        ttl_seconds: float = DEFAULT_SELECTION_TTL_SECONDS,
        max_active_selections: int = DEFAULT_MAX_ACTIVE_SELECTIONS,
    ) -> None:
        if not isinstance(ttl_seconds, (int, float)) or not 1 <= ttl_seconds <= 3_600:
            raise ValueError("selection TTL must be between 1 and 3600 seconds")
        if not isinstance(max_active_selections, int) or not 1 <= max_active_selections <= 256:
            raise ValueError("selection capacity must be between 1 and 256")
        self._local_volume_probe = local_volume_probe
        self._selection_id_factory = selection_id_factory or self._new_selection_id
        self._clock = clock
        self._ttl_seconds = float(ttl_seconds)
        self._max_active_selections = max_active_selections
        self._records: dict[str, _SelectionRecord] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _new_selection_id() -> str:
        import secrets

        return f"selection_{secrets.token_urlsafe(24)}"

    def select(
        self,
        source: PackageSelectionSource,
        candidate: object,
    ) -> PackageSelectionSnapshot:
        if not isinstance(source, PackageSelectionSource):
            raise _selection_error("package_selection_invalid")
        path = _validate_native_candidate(candidate)
        self._require_local_volume(path)
        identity = _secure_file_identity(path)
        now = self._clock()
        selection_id = self._selection_id_factory()
        if not is_valid_package_selection_id(selection_id):
            raise _selection_error("package_selection_invalid")

        with self._lock:
            self._prune_expired(now)
            if len(self._records) >= self._max_active_selections:
                raise _selection_error("package_selection_capacity")
            if selection_id in self._records:
                raise _selection_error("package_selection_invalid")
            record = _SelectionRecord(
                selection_id=selection_id,
                source=source,
                path=path,
                identity=identity,
                created_at=now,
                expires_at=now + self._ttl_seconds,
            )
            self._records[selection_id] = record
            return self._snapshot(record, now)

    def resolve(self, selection_id: str) -> ResolvedPackageSelection:
        if not is_valid_package_selection_id(selection_id):
            raise _selection_error("package_selection_invalid")
        with self._lock:
            now = self._clock()
            self._prune_expired(now)
            record = self._records.get(selection_id)
            if record is None:
                raise _selection_error("package_selection_expired")
            try:
                current_path = _validate_native_candidate(record.path)
                self._require_local_volume(current_path)
                identity = _secure_file_identity(current_path)
            except PackageSelectionError as exc:
                self._records.pop(selection_id, None)
                if exc.code in {
                    "package_selection_missing",
                    "package_selection_symlink",
                    "package_selection_not_regular",
                }:
                    raise _selection_error("package_selection_changed") from exc
                raise
            if current_path != record.path or identity != record.identity:
                self._records.pop(selection_id, None)
                raise _selection_error("package_selection_changed")
            return ResolvedPackageSelection(
                selection_id=record.selection_id,
                source=record.source,
                path=record.path,
                size_bytes=record.identity.size_bytes,
                expires_in_seconds=self._remaining_seconds(record, now),
            )

    def revoke(self, selection_id: str) -> None:
        if not is_valid_package_selection_id(selection_id):
            return
        with self._lock:
            self._records.pop(selection_id, None)

    def _require_local_volume(self, path: Path) -> None:
        try:
            local = self._local_volume_probe(path)
        except Exception as exc:
            raise _selection_error("package_selection_locality_unknown") from exc
        if local is False:
            raise _selection_error("package_selection_nonlocal")
        if local is not True:
            raise _selection_error("package_selection_locality_unknown")

    def _prune_expired(self, now: float) -> None:
        expired = [
            selection_id
            for selection_id, record in self._records.items()
            if record.expires_at <= now
        ]
        for selection_id in expired:
            self._records.pop(selection_id, None)

    def _snapshot(self, record: _SelectionRecord, now: float) -> PackageSelectionSnapshot:
        return PackageSelectionSnapshot(
            selection_id=record.selection_id,
            source=record.source,
            size_bytes=record.identity.size_bytes,
            expires_in_seconds=self._remaining_seconds(record, now),
        )

    @staticmethod
    def _remaining_seconds(record: _SelectionRecord, now: float) -> int:
        return max(0, int(record.expires_at - now))
