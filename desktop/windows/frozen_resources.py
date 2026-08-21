from __future__ import annotations

import sys
from pathlib import Path


def application_resource(*parts: str) -> Path:
    """Return one bundled resource in source and PyInstaller layouts.

    PyInstaller places data files under ``sys._MEIPASS``.  Resolving them from
    ``__file__`` works in the source checkout but points at the wrong parent
    after freezing.
    """

    if bool(getattr(sys, "frozen", False)):
        root_value = getattr(sys, "_MEIPASS", None)
        if not isinstance(root_value, (str, bytes)) and not hasattr(root_value, "__fspath__"):
            raise RuntimeError("Windows frozen resource root is unavailable")
        root = Path(root_value)
    else:
        root = Path(__file__).resolve().parents[2]
    return root.joinpath(*parts)


__all__ = ["application_resource"]
