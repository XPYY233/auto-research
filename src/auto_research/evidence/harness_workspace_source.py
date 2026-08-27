"""Bounded, read-only workspace evidence source for DeepSeek Harness.

This adapter is deliberately smaller than the Search V2 service.  It exposes
only published evidence already present in the disposable search index and
never reads personal experiments, paths, drafts, or review notes.
"""

from __future__ import annotations

import hashlib
import json
import threading
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
        self._identity_lock = threading.RLock()
        self._identity_fingerprint = ""
        self._identity_cache: dict[tuple[str, str], int] = {}

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
        if entity_type not in ENTITY_TYPES or not isinstance(entity_uid, str) or not entity_uid:
            raise HarnessError("harness_tool_invalid")
        try:
            source_id, fingerprint = self.binding()
            cache_key = (entity_type, entity_uid)
            with self._identity_lock:
                if fingerprint != self._identity_fingerprint:
                    self._identity_cache.clear()
                    self._identity_fingerprint = fingerprint
                entity_id = self._identity_cache.get(cache_key)
                if entity_id is None:
                    entity_id = self._resolve_public_identity(
                        entity_type=entity_type,
                        entity_uid=entity_uid,
                        expected_source_id=source_id,
                    )
                    self._identity_cache[cache_key] = entity_id
            row = self._index.get(entity_type, entity_id)
            public = sanitize_workspace_documents(
                (row,), expected_source_id=source_id
            )[0]
            if public["entity_uid"] != entity_uid:
                raise HarnessError("harness_tool_forbidden")
            return public
        except HarnessError:
            raise
        except Exception as exc:
            raise HarnessError("harness_tool_forbidden") from exc

    def _resolve_public_identity(
        self, *, entity_type: str, entity_uid: str, expected_source_id: str
    ) -> int:
        offset = 0
        while True:
            page = self._index.search(
                "",
                entity_types=(entity_type,),
                quality_filter="published",
                limit=500,
                offset=offset,
                refresh=False,
            )
            for row in page.rows:
                public = sanitize_workspace_documents(
                    (row,), expected_source_id=expected_source_id
                )[0]
                internal_id = row.get("entity_id")
                if (
                    not isinstance(internal_id, bool)
                    and isinstance(internal_id, int)
                    and internal_id > 0
                ):
                    self._identity_cache[
                        (str(public["entity_type"]), str(public["entity_uid"]))
                    ] = internal_id
            resolved = self._identity_cache.get((entity_type, entity_uid))
            if resolved is not None:
                return resolved
            rows = len(page.rows)
            offset += rows
            if rows == 0 or offset >= int(page.total):
                raise HarnessError("harness_tool_forbidden")


__all__ = ["HarnessWorkspaceSource", "WORKSPACE_SOURCE_ID"]
