from __future__ import annotations

import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol


TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
SAFE_NAME_RE = re.compile(r"^[^/\\\x00]{1,180}\.aresearch$", re.IGNORECASE)
WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul", "clock$"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)


def _safe_windows_package_name(name: str) -> bool:
    if SAFE_NAME_RE.fullmatch(name) is None or ":" in name or name.endswith((" ", ".")):
        return False
    stem = name[: -len(".aresearch")].rstrip(" .").casefold()
    return bool(stem) and stem not in WINDOWS_RESERVED_NAMES


class PackageExportDestinationError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable


@dataclass(frozen=True)
class PackageExportDestinationSnapshot:
    destination_token: str
    expires_in_seconds: int

    def public_dict(self) -> dict[str, object]:
        return {
            "destination_token": self.destination_token,
            "expires_in_seconds": self.expires_in_seconds,
        }


@dataclass(frozen=True)
class ResolvedPackageExportDestination:
    path: Path


@dataclass(frozen=True)
class _Record:
    path: Path
    parent_device: int
    parent_inode: int
    expires_at: float


class SaveWindow(Protocol):
    def choose_save_file(
        self, *, title: str, extension: str, suggested_name: str
    ) -> str | None: ...


class WindowsPackageExportDestinationBroker:
    """One-shot, expiring destination handles; renderer never receives paths."""

    def __init__(
        self,
        *,
        ttl_seconds: int = 300,
        maximum_entries: int = 32,
        clock: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] | None = None,
        local_volume_probe: Callable[[Path], bool] | None = None,
    ) -> None:
        if not 1 <= int(ttl_seconds) <= 3600:
            raise ValueError("destination TTL is invalid")
        if not 1 <= int(maximum_entries) <= 256:
            raise ValueError("destination capacity is invalid")
        self._ttl = int(ttl_seconds)
        self._maximum = int(maximum_entries)
        self._clock = clock
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(24))
        self._local_volume_probe = local_volume_probe or self._is_local_volume
        self._records: dict[str, _Record] = {}
        self._lock = threading.RLock()

    def select(self, raw_path: str | Path) -> PackageExportDestinationSnapshot:
        raw = str(raw_path or "")
        if not raw or raw.startswith(("\\\\", "//")) or raw.casefold().startswith("file:"):
            self._fail("package_destination_invalid", "请选择本机保存位置。")
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute() or candidate.is_symlink():
            self._fail("package_destination_invalid", "请选择本机普通文件位置。")
        if len(raw) > 1024 or not _safe_windows_package_name(candidate.name):
            self._fail("package_destination_invalid", "资料包文件名无效。")
        parent = candidate.parent
        try:
            if self._is_reparse(parent) or not parent.is_dir():
                self._fail("package_destination_invalid", "保存目录不存在或不安全。")
            canonical_parent = parent.resolve(strict=True)
            if self._local_volume_probe(canonical_parent) is not True:
                self._fail("package_destination_nonlocal", "资料包只能保存到本机磁盘。")
            metadata = canonical_parent.stat()
        except PackageExportDestinationError:
            raise
        except OSError:
            self._fail("package_destination_invalid", "无法安全读取保存目录。")
        destination = canonical_parent / candidate.name
        if destination.exists() or destination.is_symlink():
            self._fail("package_destination_exists", "目标文件已存在，请换一个名称。")
        now = self._clock()
        with self._lock:
            self._prune(now)
            if len(self._records) >= self._maximum:
                self._fail("package_destination_capacity", "保存选择过多，请稍后重试。")
            token = self._token_factory()
            if TOKEN_RE.fullmatch(token) is None or token in self._records:
                raise RuntimeError("destination token factory returned an invalid token")
            self._records[token] = _Record(
                destination,
                int(metadata.st_dev),
                int(metadata.st_ino),
                now + self._ttl,
            )
        return PackageExportDestinationSnapshot(token, self._ttl)

    def resolve(self, destination_token: str) -> ResolvedPackageExportDestination:
        if TOKEN_RE.fullmatch(str(destination_token or "")) is None:
            self._fail("package_destination_expired", "保存位置已失效，请重新选择。")
        now = self._clock()
        with self._lock:
            self._prune(now)
            record = self._records.pop(destination_token, None)
        if record is None:
            self._fail("package_destination_expired", "保存位置已失效，请重新选择。")
        try:
            parent = record.path.parent
            metadata = parent.stat()
            if (
                parent.is_symlink()
                or not parent.is_dir()
                or self._is_reparse(parent)
                or self._local_volume_probe(parent) is not True
                or int(metadata.st_dev) != record.parent_device
                or int(metadata.st_ino) != record.parent_inode
                or record.path.exists()
                or record.path.is_symlink()
            ):
                self._fail("package_destination_changed", "保存位置发生变化，请重新选择。")
        except PackageExportDestinationError:
            raise
        except OSError:
            self._fail("package_destination_changed", "保存位置发生变化，请重新选择。")
        return ResolvedPackageExportDestination(record.path)

    def _prune(self, now: float) -> None:
        for token in [key for key, value in self._records.items() if value.expires_at <= now]:
            self._records.pop(token, None)

    @staticmethod
    def _is_reparse(path: Path) -> bool:
        try:
            status = path.lstat()
        except OSError:
            return True
        attributes = int(getattr(status, "st_file_attributes", 0))
        return path.is_symlink() or bool(attributes & 0x400)

    @staticmethod
    def _is_local_volume(path: Path) -> bool:
        if os.name != "nt":
            return True
        try:
            import ctypes

            anchor = str(path.anchor or "")
            if not anchor:
                return False
            drive_type = int(ctypes.windll.kernel32.GetDriveTypeW(anchor))
            # removable, fixed and RAM disks are local; remote/unknown/no-root
            # volumes fail closed.
            return drive_type in {2, 3, 6}
        except Exception:
            return False

    @staticmethod
    def _fail(code: str, message: str) -> None:
        raise PackageExportDestinationError(code, message)


class WindowsPackageExportDestinationAdapter:
    def __init__(self, broker: WindowsPackageExportDestinationBroker, window: SaveWindow) -> None:
        self.broker = broker
        self.window = window

    def select_package_export_destination(self, suggested_name: str = "") -> dict[str, object]:
        name = suggested_name if SAFE_NAME_RE.fullmatch(str(suggested_name or "")) else "Auto-Research-export.aresearch"
        try:
            selected = self.window.choose_save_file(
                title="导出 Auto Research 资料包",
                extension=".aresearch",
                suggested_name=name,
            )
            if not selected:
                return {"ok": True, "cancelled": True}
            snapshot = self.broker.select(selected)
        except PackageExportDestinationError as exc:
            return {
                "ok": False,
                "cancelled": False,
                "error": {
                    "code": exc.code,
                    "message": exc.safe_message,
                    "retryable": exc.retryable,
                },
            }
        except Exception:
            return {
                "ok": False,
                "cancelled": False,
                "error": {
                    "code": "package_destination_unavailable",
                    "message": "系统保存窗口暂时不可用。",
                    "retryable": True,
                },
            }
        return {
            "ok": True,
            "cancelled": False,
            "destination": snapshot.public_dict(),
        }


__all__ = [
    "PackageExportDestinationError",
    "WindowsPackageExportDestinationAdapter",
    "WindowsPackageExportDestinationBroker",
]
