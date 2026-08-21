from __future__ import annotations

import os
import shutil
import stat
import time
from pathlib import Path
from typing import Callable, TypeVar


WINDOWS_TRANSIENT_FILE_ERRORS = frozenset({5, 32, 33})
WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
WINDOWS_FILE_RETRY_DELAYS = (0.025, 0.05, 0.1, 0.2, 0.4, 0.5, 0.5, 0.5, 0.5)

_T = TypeVar("_T")


def _with_windows_file_retry(operation: Callable[[], _T]) -> _T:
    """Retry only Windows transient sharing/access violations.

    Antivirus, indexing and preview handlers can briefly retain a file after a
    writer closes it.  All other errors remain fail-closed and are re-raised
    immediately.
    """

    for attempt in range(len(WINDOWS_FILE_RETRY_DELAYS) + 1):
        try:
            return operation()
        except OSError as exc:
            if (
                getattr(exc, "winerror", None) not in WINDOWS_TRANSIENT_FILE_ERRORS
                or attempt >= len(WINDOWS_FILE_RETRY_DELAYS)
            ):
                raise
            time.sleep(WINDOWS_FILE_RETRY_DELAYS[attempt])
    raise AssertionError("unreachable Windows file retry state")


def replace_file(source: Path | str, destination: Path | str) -> None:
    _with_windows_file_retry(lambda: os.replace(source, destination))


def unlink_file(path: Path | str, *, missing_ok: bool = False) -> None:
    candidate = Path(path)
    _with_windows_file_retry(lambda: candidate.unlink(missing_ok=missing_ok))


def remove_tree(path: Path | str, *, missing_ok: bool = False) -> None:
    candidate = Path(path)

    def remove() -> None:
        try:
            shutil.rmtree(candidate)
        except FileNotFoundError:
            if not missing_ok:
                raise

    _with_windows_file_retry(remove)


def best_effort_remove_tree(path: Path | str) -> bool:
    try:
        remove_tree(path, missing_ok=True)
    except OSError:
        return False
    return True


def is_link_or_reparse(path: Path | str) -> bool:
    candidate = Path(path)
    try:
        metadata = candidate.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(metadata.st_mode) or bool(
        int(getattr(metadata, "st_file_attributes", 0)) & WINDOWS_REPARSE_POINT
    )


__all__ = [
    "WINDOWS_FILE_RETRY_DELAYS",
    "WINDOWS_TRANSIENT_FILE_ERRORS",
    "best_effort_remove_tree",
    "is_link_or_reparse",
    "replace_file",
    "remove_tree",
    "unlink_file",
]
