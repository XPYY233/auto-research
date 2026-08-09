from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any


_RENDERER_FORBIDDEN_KEYS = frozenset(
    {
        "active_fingerprint",
        "file_id",
        "sha256",
        "source_file_id",
    }
)
_RENDERER_ALLOWED_ID_KEYS = frozenset({"import_id", "series_id"})


def project_personal_renderer_payload(value: Any) -> Any:
    """Return a detached JSON projection safe for the desktop renderer."""

    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        for raw_key, item in value.items():
            if not isinstance(raw_key, str):
                raise TypeError("personal renderer payload keys must be strings")
            key = raw_key.casefold()
            if (
                key in _RENDERER_FORBIDDEN_KEYS
                or key == "path"
                or key.endswith("_path")
                or (key.endswith("_id") and key not in _RENDERER_ALLOWED_ID_KEYS)
            ):
                continue
            projected[raw_key] = project_personal_renderer_payload(item)
        return projected
    if isinstance(value, (list, tuple)):
        return [project_personal_renderer_payload(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return copy.deepcopy(value)
    raise TypeError("personal renderer payload contains an unsupported value")
