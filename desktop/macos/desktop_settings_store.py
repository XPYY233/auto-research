from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from pathlib import Path
from typing import Any, Mapping


MAX_SETTINGS_FILE_BYTES = 32_768
DEFAULT_SETTINGS_PATH = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Auto Research"
    / "State"
    / "settings-v1.json"
)


class MacAtomicDesktopSettingsStore:
    """Device-local atomic JSON store for non-sensitive UI preferences."""

    def __init__(self, path: Path | str = DEFAULT_SETTINGS_PATH) -> None:
        self.path = Path(path).expanduser()
        self._lock = threading.RLock()

    def _directory(self) -> Path:
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
            raise OSError("desktop settings directory is unsafe")
        os.chmod(directory, 0o700)
        return directory

    def _read_unlocked(self) -> Mapping[str, Any] | None:
        if not self.path.exists():
            return None
        metadata = self.path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise OSError("desktop settings file is unsafe")
        if metadata.st_size > MAX_SETTINGS_FILE_BYTES:
            raise OSError("desktop settings file is too large")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.path, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise OSError("desktop settings file is unsafe")
            data = os.read(descriptor, MAX_SETTINGS_FILE_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(data) > MAX_SETTINGS_FILE_BYTES:
            raise OSError("desktop settings file is too large")
        value = json.loads(data.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("desktop settings file must contain an object")
        return value

    def read(self) -> Mapping[str, Any] | None:
        with self._lock:
            return self._read_unlocked()

    def compare_and_swap(
        self,
        *,
        expected_revision: int,
        value: Mapping[str, Any],
    ) -> bool:
        with self._lock:
            directory = self._directory()
            current = self._read_unlocked()
            revision = 0 if current is None else current.get("revision")
            if revision != expected_revision:
                return False
            encoded = json.dumps(
                dict(value),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(encoded) > MAX_SETTINGS_FILE_BYTES:
                raise OSError("desktop settings value is too large")
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=".settings-v1-",
                    dir=directory,
                    delete=False,
                ) as handle:
                    temporary = Path(handle.name)
                    os.fchmod(handle.fileno(), 0o600)
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                if self.path.exists() and self.path.is_symlink():
                    raise OSError("desktop settings file is unsafe")
                os.replace(temporary, self.path)
                os.chmod(self.path, 0o600)
                directory_descriptor = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            return True


__all__ = [
    "DEFAULT_SETTINGS_PATH",
    "MAX_SETTINGS_FILE_BYTES",
    "MacAtomicDesktopSettingsStore",
]
