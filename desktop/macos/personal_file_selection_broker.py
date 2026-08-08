from __future__ import annotations

import os
import re
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterator


DEFAULT_SELECTION_TTL_SECONDS = 3_600.0
DEFAULT_MAX_ACTIVE_SELECTIONS = 16
DEFAULT_MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_NATIVE_PATH_BYTES = 1_024
SUPPORTED_EXTENSIONS = frozenset({".csv", ".tsv", ".xlsx"})
_SELECTION_ID_RE = re.compile(r"^personal_selection_[A-Za-z0-9_-]{16,96}$")


class PersonalFileSelectionSource(str, Enum):
    FILE_PICKER = "file_picker"


class PersonalFileSelectionError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "personal-file-selection-error-v1",
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class PersonalFileSelectionSnapshot:
    selection_id: str
    source: PersonalFileSelectionSource
    size_bytes: int
    expires_in_seconds: int

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "personal-file-selection-v1",
            "selection_id": self.selection_id,
            "source": self.source.value,
            "size_bytes": self.size_bytes,
            "expires_in_seconds": self.expires_in_seconds,
        }


@dataclass(frozen=True)
class ResolvedPersonalFileSelection:
    """Internal-only selection resolution. The public projection omits its path."""

    selection_id: str
    source: PersonalFileSelectionSource
    path: Path
    size_bytes: int
    expires_in_seconds: int

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "personal-file-selection-v1",
            "selection_id": self.selection_id,
            "source": self.source.value,
            "size_bytes": self.size_bytes,
            "expires_in_seconds": self.expires_in_seconds,
        }


@dataclass(frozen=True)
class PersonalFileSnapshot:
    """Private immutable copy passed to the bounded core parser or repository."""

    selection_id: str
    source: PersonalFileSelectionSource
    path: Path
    size_bytes: int


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
    source: PersonalFileSelectionSource
    path: Path
    identity: _FileIdentity
    created_at: float
    expires_at: float


LocalVolumeProbe = Callable[[Path], bool | None]
SelectionIdFactory = Callable[[], str]
Clock = Callable[[], float]


_MESSAGES = {
    "personal_selection_invalid": ("文件选择无效，请重新选择。", True),
    "personal_selection_multiple": ("每次只能选择一个实验数据文件。", True),
    "personal_selection_file_url": ("不接受文件网址，请使用系统文件选择器。", True),
    "personal_selection_extension": ("请选择 CSV、TSV 或 XLSX 文件。", True),
    "personal_selection_name": ("文件名不符合安全要求，请重命名后再选择。", True),
    "personal_selection_path_too_long": ("所选文件位置过长，请移动后重新选择。", True),
    "personal_selection_missing": ("所选文件已不存在，请重新选择。", True),
    "personal_selection_symlink": ("不能选择文件快捷链接，请选择原始文件。", True),
    "personal_selection_not_regular": ("请选择一个普通的本机数据文件。", True),
    "personal_selection_nonlocal": ("实验数据文件必须先保存到本机磁盘。", True),
    "personal_selection_locality_unknown": ("无法确认文件位于本机磁盘，已停止操作。", True),
    "personal_selection_too_large": ("实验数据文件超过预览大小限制。", True),
    "personal_selection_expired": ("文件选择已过期，请重新选择。", True),
    "personal_selection_changed": ("文件在选择后发生变化，请重新选择。", True),
    "personal_selection_capacity": ("待处理的个人文件过多，请稍后再试。", True),
    "personal_selection_snapshot_failed": ("无法安全读取所选文件，请重新选择。", True),
}


def _selection_error(code: str) -> PersonalFileSelectionError:
    message, retryable = _MESSAGES[code]
    return PersonalFileSelectionError(code, message, retryable=retryable)


def is_valid_personal_selection_id(value: object) -> bool:
    return isinstance(value, str) and _SELECTION_ID_RE.fullmatch(value) is not None


def macos_local_volume_probe(path: Path) -> bool | None:
    """Read the native volume-local flag. Missing or ambiguous data fails closed."""

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
    except Exception:
        return None
    return None


def _path_bytes(path: Path) -> int:
    return len(os.fsencode(os.fspath(path)))


def _validate_basename(path: Path) -> None:
    name = path.name
    if (
        not name
        or name in {".", ".."}
        or ":" in name
        or any(ord(character) < 32 for character in name)
    ):
        raise _selection_error("personal_selection_name")


def _validate_native_candidate(candidate: object) -> Path:
    if isinstance(candidate, (list, tuple, set, frozenset)):
        raise _selection_error("personal_selection_multiple")
    if not isinstance(candidate, (str, os.PathLike)):
        raise _selection_error("personal_selection_invalid")
    try:
        raw = os.fspath(candidate)
    except TypeError as exc:
        raise _selection_error("personal_selection_invalid") from exc
    if isinstance(raw, bytes) or not raw or "\x00" in raw:
        raise _selection_error("personal_selection_invalid")
    if raw.casefold().startswith("file:") or "://" in raw:
        raise _selection_error("personal_selection_file_url")
    original = Path(raw).expanduser()
    if not original.is_absolute():
        raise _selection_error("personal_selection_invalid")
    if _path_bytes(original) > MAX_NATIVE_PATH_BYTES:
        raise _selection_error("personal_selection_path_too_long")
    _validate_basename(original)
    if original.suffix.casefold() not in SUPPORTED_EXTENSIONS:
        raise _selection_error("personal_selection_extension")
    try:
        original_status = original.lstat()
    except FileNotFoundError as exc:
        raise _selection_error("personal_selection_missing") from exc
    except OSError as exc:
        raise _selection_error("personal_selection_invalid") from exc
    if stat.S_ISLNK(original_status.st_mode):
        raise _selection_error("personal_selection_symlink")
    if not stat.S_ISREG(original_status.st_mode):
        raise _selection_error("personal_selection_not_regular")
    canonical = Path(os.path.realpath(original))
    if _path_bytes(canonical) > MAX_NATIVE_PATH_BYTES:
        raise _selection_error("personal_selection_path_too_long")
    return canonical


def _identity_from_status(status: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        device=int(status.st_dev),
        inode=int(status.st_ino),
        size_bytes=int(status.st_size),
        modified_ns=int(status.st_mtime_ns),
        changed_ns=int(status.st_ctime_ns),
    )


def _open_regular_file(path: Path) -> tuple[int, _FileIdentity]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as exc:
        raise _selection_error("personal_selection_missing") from exc
    except OSError as exc:
        try:
            status = path.lstat()
        except OSError:
            raise _selection_error("personal_selection_invalid") from exc
        if stat.S_ISLNK(status.st_mode):
            raise _selection_error("personal_selection_symlink") from exc
        raise _selection_error("personal_selection_invalid") from exc
    status = os.fstat(descriptor)
    if not stat.S_ISREG(status.st_mode):
        os.close(descriptor)
        raise _selection_error("personal_selection_not_regular")
    return descriptor, _identity_from_status(status)


class PersonalFileSelectionBroker:
    def __init__(
        self,
        *,
        local_volume_probe: LocalVolumeProbe = macos_local_volume_probe,
        selection_id_factory: SelectionIdFactory | None = None,
        clock: Clock = time.monotonic,
        ttl_seconds: float = DEFAULT_SELECTION_TTL_SECONDS,
        max_active_selections: int = DEFAULT_MAX_ACTIVE_SELECTIONS,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ) -> None:
        if not isinstance(ttl_seconds, (int, float)) or not 1 <= ttl_seconds <= 3_600:
            raise ValueError("selection TTL must be between 1 and 3600 seconds")
        if not isinstance(max_active_selections, int) or not 1 <= max_active_selections <= 128:
            raise ValueError("selection capacity must be between 1 and 128")
        if not isinstance(max_file_bytes, int) or not 1 <= max_file_bytes <= 100 * 1024 * 1024:
            raise ValueError("personal file size limit is invalid")
        self._local_volume_probe = local_volume_probe
        self._selection_id_factory = selection_id_factory or self._new_selection_id
        self._clock = clock
        self._ttl_seconds = float(ttl_seconds)
        self._max_active_selections = max_active_selections
        self._max_file_bytes = max_file_bytes
        self._records: dict[str, _SelectionRecord] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _new_selection_id() -> str:
        import secrets

        return f"personal_selection_{secrets.token_urlsafe(24)}"

    def select(
        self,
        source: PersonalFileSelectionSource,
        candidate: object,
    ) -> PersonalFileSelectionSnapshot:
        if source is not PersonalFileSelectionSource.FILE_PICKER:
            raise _selection_error("personal_selection_invalid")
        path = _validate_native_candidate(candidate)
        self._require_local_volume(path)
        descriptor, identity = _open_regular_file(path)
        os.close(descriptor)
        if identity.size_bytes > self._max_file_bytes:
            raise _selection_error("personal_selection_too_large")
        now = self._clock()
        selection_id = self._selection_id_factory()
        if not is_valid_personal_selection_id(selection_id):
            raise _selection_error("personal_selection_invalid")
        with self._lock:
            self._prune_expired(now)
            if len(self._records) >= self._max_active_selections:
                raise _selection_error("personal_selection_capacity")
            if selection_id in self._records:
                raise _selection_error("personal_selection_invalid")
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

    def resolve(self, selection_id: str) -> ResolvedPersonalFileSelection:
        with self._lock:
            record, now = self._resolve_record(selection_id)
            return ResolvedPersonalFileSelection(
                selection_id=record.selection_id,
                source=record.source,
                path=record.path,
                size_bytes=record.identity.size_bytes,
                expires_in_seconds=self._remaining_seconds(record, now),
            )

    @contextmanager
    def snapshot(self, selection_id: str) -> Iterator[PersonalFileSnapshot]:
        """Copy a still-identical selection through one verified descriptor."""

        temporary: tempfile.TemporaryDirectory[str] | None = None
        descriptor = -1
        try:
            with self._lock:
                record, _now = self._resolve_record(selection_id)
                descriptor, identity = _open_regular_file(record.path)
                if identity != record.identity:
                    self._records.pop(selection_id, None)
                    raise _selection_error("personal_selection_changed")
                temporary = tempfile.TemporaryDirectory(
                    prefix="auto-research-personal-selection-"
                )
                destination = Path(temporary.name) / record.path.name
                output_flags = (
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                )
                output = os.open(destination, output_flags, 0o600)
                copied = 0
                try:
                    while chunk := os.read(descriptor, 1024 * 1024):
                        copied += len(chunk)
                        if copied > self._max_file_bytes:
                            raise _selection_error("personal_selection_too_large")
                        view = memoryview(chunk)
                        while view:
                            written = os.write(output, view)
                            if written <= 0:
                                raise OSError("snapshot write did not progress")
                            view = view[written:]
                    os.fsync(output)
                finally:
                    os.close(output)
                after = _identity_from_status(os.fstat(descriptor))
                if after != record.identity or copied != record.identity.size_bytes:
                    self._records.pop(selection_id, None)
                    raise _selection_error("personal_selection_changed")
            yield PersonalFileSnapshot(
                selection_id=record.selection_id,
                source=record.source,
                path=destination,
                size_bytes=copied,
            )
        except PersonalFileSelectionError:
            raise
        except OSError as exc:
            with self._lock:
                self._records.pop(selection_id, None)
            raise _selection_error("personal_selection_snapshot_failed") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                temporary.cleanup()

    def revoke(self, selection_id: str) -> None:
        if not is_valid_personal_selection_id(selection_id):
            return
        with self._lock:
            self._records.pop(selection_id, None)

    def _resolve_record(self, selection_id: str) -> tuple[_SelectionRecord, float]:
        if not is_valid_personal_selection_id(selection_id):
            raise _selection_error("personal_selection_invalid")
        now = self._clock()
        self._prune_expired(now)
        record = self._records.get(selection_id)
        if record is None:
            raise _selection_error("personal_selection_expired")
        try:
            current_path = _validate_native_candidate(record.path)
            self._require_local_volume(current_path)
            descriptor, identity = _open_regular_file(current_path)
            os.close(descriptor)
        except PersonalFileSelectionError as exc:
            self._records.pop(selection_id, None)
            if exc.code in {
                "personal_selection_missing",
                "personal_selection_symlink",
                "personal_selection_not_regular",
            }:
                raise _selection_error("personal_selection_changed") from exc
            raise
        if current_path != record.path or identity != record.identity:
            self._records.pop(selection_id, None)
            raise _selection_error("personal_selection_changed")
        return record, now

    def _require_local_volume(self, path: Path) -> None:
        try:
            local = self._local_volume_probe(path)
        except Exception as exc:
            raise _selection_error("personal_selection_locality_unknown") from exc
        if local is False:
            raise _selection_error("personal_selection_nonlocal")
        if local is not True:
            raise _selection_error("personal_selection_locality_unknown")

    def _prune_expired(self, now: float) -> None:
        for selection_id in tuple(self._records):
            if self._records[selection_id].expires_at <= now:
                self._records.pop(selection_id, None)

    def _snapshot(
        self,
        record: _SelectionRecord,
        now: float,
    ) -> PersonalFileSelectionSnapshot:
        return PersonalFileSelectionSnapshot(
            selection_id=record.selection_id,
            source=record.source,
            size_bytes=record.identity.size_bytes,
            expires_in_seconds=self._remaining_seconds(record, now),
        )

    @staticmethod
    def _remaining_seconds(record: _SelectionRecord, now: float) -> int:
        return max(0, int(record.expires_at - now))
