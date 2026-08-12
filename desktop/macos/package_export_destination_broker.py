from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_SAFE_NAME_RE = re.compile(r"^[^/\\\x00]{1,180}\.aresearch$", re.IGNORECASE)


class PackageExportDestinationError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def public_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


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
class _DestinationRecord:
    path: Path
    parent_device: int
    parent_inode: int
    expires_at: float


class PackageExportDestinationBroker:
    """Keep native save paths out of renderer and HTTP DTOs."""

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
        self._local_volume_probe = local_volume_probe or self._probe_local_volume
        self._records: dict[str, _DestinationRecord] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _probe_local_volume(path: Path) -> bool:
        try:
            from Foundation import NSURL, NSURLVolumeIsLocalKey

            url = NSURL.fileURLWithPath_(str(path))
            values, error = url.resourceValuesForKeys_error_([NSURLVolumeIsLocalKey], None)
            if error is not None or values is None:
                return False
            value = values.get(NSURLVolumeIsLocalKey)
            if isinstance(value, bool):
                return value
            bool_value = getattr(value, "boolValue", None)
            if callable(bool_value):
                converted = bool_value()
                if isinstance(converted, bool):
                    return converted
                if type(converted) is int and converted in (0, 1):
                    return bool(converted)
            return False
        except Exception:
            return False

    def select(self, raw_path: str | Path) -> PackageExportDestinationSnapshot:
        if not isinstance(raw_path, (str, Path)):
            self._fail("package_destination_invalid", "请选择本机保存位置。")
        original = Path(raw_path).expanduser()
        if not original.is_absolute() or original.is_symlink():
            self._fail("package_destination_invalid", "请选择本机普通文件位置。")
        if len(str(original)) > 1024 or not _SAFE_NAME_RE.fullmatch(original.name):
            self._fail("package_destination_invalid", "资料包文件名无效。")
        parent = original.parent
        if parent.is_symlink() or not parent.is_dir():
            self._fail("package_destination_invalid", "保存目录不存在或不安全。")
        if original.exists():
            self._fail("package_destination_exists", "目标文件已存在，请换一个名称。")
        try:
            canonical_parent = parent.resolve(strict=True)
            if self._local_volume_probe(canonical_parent) is not True:
                self._fail("package_destination_nonlocal", "资料包只能保存到本机磁盘。")
            metadata = canonical_parent.stat()
        except PackageExportDestinationError:
            raise
        except OSError as exc:
            raise PackageExportDestinationError(
                "package_destination_invalid",
                "无法安全读取保存目录。",
                retryable=True,
            ) from exc
        path = canonical_parent / original.name
        now = self._clock()
        with self._lock:
            self._prune(now)
            if len(self._records) >= self._maximum:
                self._fail("package_destination_capacity", "保存选择过多，请稍后重试。")
            token = self._token_factory()
            if _TOKEN_RE.fullmatch(token) is None or token in self._records:
                raise RuntimeError("destination token factory returned an invalid token")
            self._records[token] = _DestinationRecord(
                path=path,
                parent_device=int(metadata.st_dev),
                parent_inode=int(metadata.st_ino),
                expires_at=now + self._ttl,
            )
        return PackageExportDestinationSnapshot(token, self._ttl)

    def resolve(self, destination_token: str) -> ResolvedPackageExportDestination:
        if not isinstance(destination_token, str) or _TOKEN_RE.fullmatch(destination_token) is None:
            self._fail("package_destination_expired", "保存位置已失效，请重新选择。")
        now = self._clock()
        with self._lock:
            self._prune(now)
            record = self._records.pop(destination_token, None)
        if record is None:
            self._fail("package_destination_expired", "保存位置已失效，请重新选择。")
        try:
            parent = record.path.parent
            if parent.is_symlink() or not parent.is_dir() or self._local_volume_probe(parent) is not True:
                self._fail("package_destination_changed", "保存目录发生变化，请重新选择。")
            metadata = parent.stat()
            if int(metadata.st_dev) != record.parent_device or int(metadata.st_ino) != record.parent_inode:
                self._fail("package_destination_changed", "保存目录发生变化，请重新选择。")
            if record.path.exists() or record.path.is_symlink():
                self._fail("package_destination_exists", "目标文件已存在，请换一个名称。")
        except PackageExportDestinationError:
            raise
        except OSError as exc:
            raise PackageExportDestinationError(
                "package_destination_changed",
                "保存目录发生变化，请重新选择。",
                retryable=True,
            ) from exc
        return ResolvedPackageExportDestination(record.path)

    def revoke(self, destination_token: str) -> None:
        with self._lock:
            self._records.pop(str(destination_token), None)

    def _prune(self, now: float) -> None:
        expired = [token for token, record in self._records.items() if record.expires_at <= now]
        for token in expired:
            self._records.pop(token, None)

    @staticmethod
    def _fail(code: str, message: str) -> None:
        raise PackageExportDestinationError(code, message, retryable=True)
