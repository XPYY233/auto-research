from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Iterator, Protocol, Sequence

from auto_research.personal.import_service import SelectionSnapshotProviderError
from auto_research.portable_file_ops import remove_tree


SUPPORTED_EXTENSIONS = frozenset({".csv", ".tsv", ".xlsx"})
MAX_NATIVE_PATH_CHARS = 1_024
DEFAULT_MAX_FILE_BYTES = 20 * 1024 * 1024
DEFAULT_SELECTION_TTL_SECONDS = 3_600.0
DEFAULT_MAX_ACTIVE_SELECTIONS = 16
_SELECTION_ID_RE = re.compile(r"^personal_selection_[A-Za-z0-9_-]{16,96}$")


class PersonalFileSelectionError(SelectionSnapshotProviderError):
    def public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "personal-file-selection-error-v1",
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


_MESSAGES = {
    "personal_selection_invalid": "文件选择无效，请重新选择。",
    "personal_selection_multiple": "每次只能选择一个实验数据文件。",
    "personal_selection_file_url": "不接受文件网址，请使用系统文件选择器。",
    "personal_selection_extension": "请选择 CSV、TSV 或 XLSX 文件。",
    "personal_selection_path_too_long": "所选文件位置过长，请移动后重新选择。",
    "personal_selection_missing": "所选文件已不存在，请重新选择。",
    "personal_selection_symlink": "不能选择文件快捷链接，请选择原始文件。",
    "personal_selection_not_regular": "请选择一个普通的本机数据文件。",
    "personal_selection_nonlocal": "实验数据文件必须先保存到本机磁盘。",
    "personal_selection_locality_unknown": "无法确认文件位于本机磁盘，已停止操作。",
    "personal_selection_too_large": "实验数据文件超过预览大小限制。",
    "personal_selection_expired": "文件选择已过期，请重新选择。",
    "personal_selection_changed": "文件在选择后发生变化，请重新选择。",
    "personal_selection_capacity": "待处理的个人文件过多，请稍后再试。",
    "personal_selection_snapshot_failed": "无法安全读取所选文件，请重新选择。",
}


def _error(code: str) -> PersonalFileSelectionError:
    return PersonalFileSelectionError(code, _MESSAGES[code], retryable=True)


@dataclass(frozen=True)
class PersonalFileSelectionSnapshot:
    selection_id: str
    size_bytes: int
    expires_in_seconds: int

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "personal-file-selection-v1",
            "selection_id": self.selection_id,
            "source": "file_picker",
            "size_bytes": self.size_bytes,
            "expires_in_seconds": self.expires_in_seconds,
        }


@dataclass(frozen=True)
class PersonalFileSnapshot:
    path: Path


@dataclass(frozen=True)
class _Identity:
    device: int
    inode: int
    size_bytes: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True)
class _Record:
    selection_id: str
    path: Path
    identity: _Identity
    expires_at: float


LocalVolumeProbe = Callable[[Path], bool | None]
PathValidator = Callable[[object], Path]


def windows_local_volume_probe(path: Path) -> bool | None:
    if os.name != "nt":
        return None
    try:
        import ctypes

        anchor = PureWindowsPath(str(path)).anchor
        drive_type = int(ctypes.windll.kernel32.GetDriveTypeW(str(anchor)))
        if drive_type in {2, 3, 5, 6}:  # removable, fixed, optical, RAM disk
            return True
        if drive_type == 4:  # network
            return False
    except Exception:
        return None
    return None


def _identity(status: os.stat_result) -> _Identity:
    return _Identity(
        int(status.st_dev),
        int(status.st_ino),
        int(status.st_size),
        int(status.st_mtime_ns),
        int(status.st_ctime_ns),
    )


def _validated_path(candidate: object) -> Path:
    if isinstance(candidate, (list, tuple, set, frozenset)):
        raise _error("personal_selection_multiple")
    if not isinstance(candidate, (str, os.PathLike)):
        raise _error("personal_selection_invalid")
    raw = os.fspath(candidate)
    if isinstance(raw, bytes) or not raw or "\x00" in raw:
        raise _error("personal_selection_invalid")
    lowered = raw.casefold()
    if lowered.startswith("file:") or "://" in lowered:
        raise _error("personal_selection_file_url")
    windows_path = PureWindowsPath(raw)
    if (
        not windows_path.is_absolute()
        or str(windows_path).startswith(("\\\\", "//"))
        or lowered.startswith(("\\\\?\\", "\\\\.\\"))
        or ".." in windows_path.parts
    ):
        raise _error("personal_selection_invalid")
    if len(raw) > MAX_NATIVE_PATH_CHARS:
        raise _error("personal_selection_path_too_long")
    if windows_path.suffix.casefold() not in SUPPORTED_EXTENSIONS:
        raise _error("personal_selection_extension")
    path = Path(raw)
    try:
        status = path.lstat()
    except FileNotFoundError:
        raise _error("personal_selection_missing") from None
    except OSError:
        raise _error("personal_selection_invalid") from None
    attributes = int(getattr(status, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    if stat.S_ISLNK(status.st_mode) or attributes & reparse_flag:
        raise _error("personal_selection_symlink")
    if not stat.S_ISREG(status.st_mode):
        raise _error("personal_selection_not_regular")
    return path


class WindowsPersonalFileSelectionBroker:
    """Windows-native opaque selection provider for the shared import service."""

    def __init__(
        self,
        *,
        local_volume_probe: LocalVolumeProbe = windows_local_volume_probe,
        selection_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        ttl_seconds: float = DEFAULT_SELECTION_TTL_SECONDS,
        max_active_selections: int = DEFAULT_MAX_ACTIVE_SELECTIONS,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        path_validator: PathValidator = _validated_path,
    ) -> None:
        if not 1 <= float(ttl_seconds) <= 3_600:
            raise ValueError("selection TTL must be between 1 and 3600 seconds")
        if not 1 <= int(max_active_selections) <= 128:
            raise ValueError("selection capacity must be between 1 and 128")
        if not 1 <= int(max_file_bytes) <= 100 * 1024 * 1024:
            raise ValueError("personal file size limit is invalid")
        self._local_volume_probe = local_volume_probe
        self._selection_id_factory = selection_id_factory or self._new_id
        self._clock = clock
        self._ttl = float(ttl_seconds)
        self._capacity = int(max_active_selections)
        self._max_bytes = int(max_file_bytes)
        self._path_validator = path_validator
        self._records: dict[str, _Record] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _new_id() -> str:
        import secrets

        return f"personal_selection_{secrets.token_urlsafe(24)}"

    def select(self, candidate: object) -> PersonalFileSelectionSnapshot:
        path = self._path_validator(candidate)
        local = self._local_volume_probe(path)
        if local is False:
            raise _error("personal_selection_nonlocal")
        if local is not True:
            raise _error("personal_selection_locality_unknown")
        try:
            identity = _identity(path.stat())
        except OSError:
            raise _error("personal_selection_invalid") from None
        if identity.size_bytes > self._max_bytes:
            raise _error("personal_selection_too_large")
        now = self._clock()
        selection_id = self._selection_id_factory()
        if not isinstance(selection_id, str) or _SELECTION_ID_RE.fullmatch(selection_id) is None:
            raise _error("personal_selection_invalid")
        with self._lock:
            self._prune(now)
            if len(self._records) >= self._capacity:
                raise _error("personal_selection_capacity")
            self._records[selection_id] = _Record(
                selection_id,
                path,
                identity,
                now + self._ttl,
            )
        return PersonalFileSelectionSnapshot(
            selection_id,
            identity.size_bytes,
            max(0, int(self._ttl)),
        )

    @contextmanager
    def snapshot(self, selection_id: str) -> Iterator[PersonalFileSnapshot]:
        temporary_root: Path | None = None
        try:
            with self._lock:
                record = self._resolve(selection_id)
                before = _identity(record.path.stat())
                if before != record.identity:
                    self._records.pop(selection_id, None)
                    raise _error("personal_selection_changed")
                temporary_root = Path(tempfile.mkdtemp(
                    prefix="auto-research-windows-personal-selection-"
                ))
                destination = temporary_root / record.path.name
                with record.path.open("rb") as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                after = _identity(record.path.stat())
                if after != record.identity or destination.stat().st_size != record.identity.size_bytes:
                    self._records.pop(selection_id, None)
                    raise _error("personal_selection_changed")
            yield PersonalFileSnapshot(destination)
        except PersonalFileSelectionError:
            raise
        except OSError:
            with self._lock:
                self._records.pop(selection_id, None)
            raise _error("personal_selection_snapshot_failed") from None
        finally:
            if temporary_root is not None:
                # Windows antivirus and preview handlers may briefly retain a
                # copied workbook after the parser closes it.  Bound the retry
                # and keep any failure path-free.
                try:
                    remove_tree(temporary_root, missing_ok=True)
                except OSError:
                    with self._lock:
                        self._records.pop(selection_id, None)
                    raise _error("personal_selection_snapshot_failed") from None

    def revoke(self, selection_id: str) -> None:
        with self._lock:
            self._records.pop(selection_id, None)

    def _resolve(self, selection_id: str) -> _Record:
        if not isinstance(selection_id, str) or _SELECTION_ID_RE.fullmatch(selection_id) is None:
            raise _error("personal_selection_invalid")
        now = self._clock()
        self._prune(now)
        record = self._records.get(selection_id)
        if record is None:
            raise _error("personal_selection_expired")
        try:
            current = self._path_validator(record.path)
            identity = _identity(current.stat())
        except PersonalFileSelectionError as exc:
            self._records.pop(selection_id, None)
            if exc.code in {
                "personal_selection_missing",
                "personal_selection_symlink",
                "personal_selection_not_regular",
            }:
                raise _error("personal_selection_changed") from exc
            raise
        if current != record.path or identity != record.identity:
            self._records.pop(selection_id, None)
            raise _error("personal_selection_changed")
        return record

    def _prune(self, now: float) -> None:
        for selection_id in tuple(self._records):
            if self._records[selection_id].expires_at <= now:
                self._records.pop(selection_id, None)


class PersonalFileWindowBridge(Protocol):
    def choose_files(
        self, *, title: str, extensions: tuple[str, ...], multiple: bool
    ) -> Sequence[str]: ...


class WindowsPersonalFileInputAdapter:
    def __init__(
        self,
        broker: WindowsPersonalFileSelectionBroker,
        window: PersonalFileWindowBridge,
    ) -> None:
        self.broker = broker
        self.window = window

    def choose_file(self) -> dict[str, Any]:
        candidates = self.window.choose_files(
            title="选择个人实验数据",
            extensions=tuple(sorted(SUPPORTED_EXTENSIONS)),
            multiple=False,
        )
        if len(candidates) != 1:
            raise _error("personal_selection_multiple")
        return self.broker.select(candidates[0]).public_dict()

    def select_personal_data_file(self) -> dict[str, Any]:
        try:
            candidates = self.window.choose_files(
                title="选择个人实验数据",
                extensions=tuple(sorted(SUPPORTED_EXTENSIONS)),
                multiple=False,
            )
            if not candidates:
                return {"ok": True, "cancelled": True}
            if isinstance(candidates, (str, bytes)) or len(candidates) != 1:
                raise _error("personal_selection_multiple")
            selection = self.broker.select(candidates[0]).public_dict()
        except PersonalFileSelectionError as exc:
            return {
                "ok": False,
                "cancelled": False,
                "error": exc.public_dict(),
            }
        except Exception:
            return {
                "ok": False,
                "cancelled": False,
                "error": {
                    "schema_version": "personal-file-selection-error-v1",
                    "code": "personal_picker_unavailable",
                    "message": "系统文件选择器无法打开，请稍后重试。",
                    "retryable": True,
                },
            }
        return {"ok": True, "cancelled": False, "selection": selection}
