"""Bounded, read-only workspace evidence source for DeepSeek Harness.

This adapter is deliberately smaller than the Search V2 service.  It exposes
only published evidence already present in the disposable search index and
never reads personal experiments, paths, drafts, or review notes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from auto_research.ai.harness_contract import HarnessError

from .db import EvidenceDB
from .harness_federated_backend import (
    MAX_HARNESS_CANDIDATES,
    sanitize_workspace_documents,
)
from .search_index import ENTITY_TYPES, EvidenceSearchIndex


WORKSPACE_SOURCE_ID = "workspace"


class HarnessWorkspaceSource:
    """Freeze published workspace evidence behind a path-free contract."""

    def __init__(self, db: EvidenceDB) -> None:
        if not isinstance(db, EvidenceDB):
            raise TypeError("db must be an EvidenceDB")
        self._index = EvidenceSearchIndex(db)

    def binding(self) -> tuple[str, str]:
        try:
            status = self._index.ensure_fresh()
            fingerprint = str(status.get("fingerprint") or self._index.source_fingerprint())
            if len(fingerprint) != 64:
                raise HarnessError("harness_runtime_unavailable")
            digest = hashlib.sha256(
                json.dumps(
                    {"source_id": WORKSPACE_SOURCE_ID, "fingerprint": fingerprint},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            return WORKSPACE_SOURCE_ID, digest
        except HarnessError:
            raise
        except Exception as exc:
            raise HarnessError("harness_runtime_unavailable") from exc

    def candidates(
        self, *, query: str, limit: int = MAX_HARNESS_CANDIDATES
    ) -> tuple[dict[str, Any], ...]:
        try:
            self.binding()
            page = self._index.search(
                str(query or ""),
                entity_types=ENTITY_TYPES,
                quality_filter="published",
                limit=min(MAX_HARNESS_CANDIDATES, max(1, int(limit))),
                refresh=False,
            )
            return sanitize_workspace_documents(page.rows)
        except HarnessError:
            raise
        except Exception as exc:
            raise HarnessError("harness_runtime_unavailable") from exc

    def get(self, *, entity_type: str, entity_uid: str) -> Mapping[str, Any]:
        if entity_type not in ENTITY_TYPES or not str(entity_uid).isdigit():
            raise HarnessError("harness_tool_invalid")
        try:
            row = self._index.get(entity_type, int(entity_uid))
            return sanitize_workspace_documents((row,))[0]
        except HarnessError:
            raise
        except Exception as exc:
            raise HarnessError("harness_tool_forbidden") from exc


__all__ = ["HarnessWorkspaceSource", "WORKSPACE_SOURCE_ID"]
