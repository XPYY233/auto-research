from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from pathlib import Path
from typing import Any, Mapping

from auto_research.portable_file_ops import is_link_or_reparse, replace_file, unlink_file


MAX_SETTINGS_FILE_BYTES = 32_768
SETTINGS_FILENAME = "settings-v1.json"


class WindowsAtomicDesktopSettingsStore:
    """Atomic non-secret settings store under the Windows application State root."""

    def __init__(
        self,
        state_directory: Path | str,
        *,
        filename: str = SETTINGS_FILENAME,
    ) -> None:
        if not isinstance(filename, str) or not filename or Path(filename).name != filename:
            raise ValueError("desktop settings filename is invalid")
        self.path = Path(state_directory) / filename
        self._lock = threading.RLock()

    def _directory(self) -> Path:
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True)
        if is_link_or_reparse(directory) or not directory.is_dir():
            raise OSError("desktop settings directory is unsafe")
        return directory

    def _read_unlocked(self) -> Mapping[str, Any] | None:
        try:
            metadata = self.path.lstat()
        except FileNotFoundError:
            return None
        if (
            stat.S_ISLNK(metadata.st_mode)
            or int(getattr(metadata, "st_file_attributes", 0))
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            or not stat.S_ISREG(metadata.st_mode)
        ):
            raise OSError("desktop settings file is unsafe")
        if metadata.st_size > MAX_SETTINGS_FILE_BYTES:
            raise OSError("desktop settings file is too large")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(self.path, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise OSError("desktop settings file is unsafe")
            if (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino):
                raise OSError("desktop settings file changed while opening")
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
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                if self.path.exists() and self.path.is_symlink():
                    raise OSError("desktop settings file is unsafe")
                replace_file(temporary, self.path)
            finally:
                if temporary is not None:
                    try:
                        unlink_file(temporary, missing_ok=True)
                    except OSError:
                        pass
            return True


__all__ = [
    "MAX_SETTINGS_FILE_BYTES",
    "SETTINGS_FILENAME",
    "WindowsAtomicDesktopSettingsStore",
]
