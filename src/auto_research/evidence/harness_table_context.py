"""Read the UI's verified table authority into a version-bound AI context.

The renderer never submits cells. Cached handles retain only public identities;
the prepared-action gate rereads the same authority before any paid execution.
"""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
import secrets
import threading
from typing import Any, Mapping

from auto_research.ai.business_actions import BusinessActionError
from auto_research.ai.prepared_actions import ContentUnit
from .federated_search import validate_public_projection

TABLE_CONTEXT_KIND = "harness_selected_table"
MAX_TABLE_CONTEXT_CHARS = 24_000
MAX_TABLE_CONTEXT_CELLS = 500
_ABSENT_CODES = frozenset({
    "official_table_structure_not_found", "official_table_structure_pending",
})


def _encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class HarnessTableContextAuthority:
    def __init__(self, *, official: Any = None, workspace: Any = None) -> None:
        self._official = official
        self._workspace = workspace
        self._identities: OrderedDict[str, dict[str, str]] = OrderedDict()
        self._lock = threading.RLock()

    def freeze(self, identity: Mapping[str, str]) -> tuple[dict[str, object], ContentUnit]:
        binding = {key: identity[key] for key in ("source_scope", "source_id", "entity_type", "entity_uid")}
        value = self._read(binding)
        encoded = _encoded(value)
        digest = hashlib.sha256(encoded).hexdigest()
        handle = "table-context-" + secrets.token_hex(24)
        with self._lock:
            while len(self._identities) >= 128:
                self._identities.popitem(last=False)
            self._identities[handle] = binding
        return value, ContentUnit(
            kind=TABLE_CONTEXT_KIND, stable_source_identity=handle,
            snapshot_fingerprint=digest, length=len(encoded), sha256=digest,
        )

    def fingerprint_for(self, *, kind: str, stable_source_identity: str) -> str:
        with self._lock:
            identity = self._identities.get(stable_source_identity)
        if kind != TABLE_CONTEXT_KIND or identity is None:
            raise ValueError("table context expired")
        return hashlib.sha256(_encoded(self._read(identity))).hexdigest()

    def _read(self, identity: Mapping[str, str]) -> dict[str, object]:
        try:
            if identity["entity_type"] != "table" or identity["source_scope"] not in {"official", "workspace"}:
                raise ValueError("unsupported table source")
            validate_public_projection(dict(identity))
            result: dict[str, object] = {
                "schema_version": "selected-table-context-v1",
                "entity": dict(identity), "status": "unavailable",
            }
            if identity["source_scope"] == "official":
                if self._official is None:
                    return result
                try:
                    grid = self._official.get(identity["entity_uid"], source_id=identity["source_id"], include_unverified=False)
                except Exception as exc:
                    if getattr(exc, "code", None) in _ABSENT_CODES:
                        return result
                    raise
                if not isinstance(grid, Mapping) or any(grid.get(key) != value for key, value in identity.items()):
                    raise ValueError("table identity changed")
            else:
                if self._workspace is None:
                    return result
                detail = self._workspace.get(**identity)
                if not isinstance(detail, Mapping) or any(detail.get(key) != value for key, value in identity.items()):
                    raise ValueError("table identity changed")
                grid = detail.get("table_structure")
                if isinstance(grid, Mapping) and grid.get("available") is False and grid.get("status") == "unavailable":
                    return result
            if not isinstance(grid, Mapping):
                raise ValueError("table response invalid")
            if grid.get("status") in {"candidate", "rejected", "pending"}:
                return result
            if grid.get("status") != "verified" or type(grid.get("version")) is not int or grid["version"] < 1:
                raise ValueError("table is not verified")
            rows = grid.get("rows")
            if not isinstance(rows, list) or not rows or len(rows) > 500:
                raise ValueError("table rows invalid")
            width = len(rows[0]) if isinstance(rows[0], list) else 0
            if not 1 <= width <= 100 or len(rows) * width > 20_000:
                raise ValueError("table dimensions invalid")
            if any(not isinstance(row, list) or len(row) != width or any(type(cell) is not str for cell in row) for row in rows):
                raise ValueError("table cells invalid")
            if len(rows) * width > MAX_TABLE_CONTEXT_CELLS or sum(len(cell) for row in rows for cell in row) > MAX_TABLE_CONTEXT_CHARS:
                raise BusinessActionError(
                    "business_action_prepare_failed", cause_code="selected_table_context_too_large",
                    stage="selected_table_preflight", next_action="open_table_detail",
                )
            result.update(status="verified", version=grid["version"], rows=[list(row) for row in rows],
                          row_count=len(rows), column_count=width)
            # No paths, internal IDs, reviewer notes, asset bytes or timestamps.
            validate_public_projection(result)
            return result
        except BusinessActionError:
            raise
        except Exception as exc:
            raise BusinessActionError(
                "business_action_prepare_failed", cause_code="selected_table_context_unavailable",
                stage="selected_table_preflight", next_action="refresh_evidence_detail",
            ) from exc
