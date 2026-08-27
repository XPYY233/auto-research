from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any, Protocol
from urllib.parse import urlparse

from auto_research.evidence.search_index import EvidenceSearchIndex


SEARCH_INDEX_RECOVERY_PATH = "/api/desktop/search-index/refresh"
MAX_SEARCH_INDEX_RECOVERY_BYTES = 1024


class SearchIndexRecoveryHTTPHandler(Protocol):
    path: str

    def _content_length(self, maximum: int, *, require_body: bool = False) -> int: ...

    def _read_exact_body(self, length: int) -> bytes: ...

    def json_response(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None: ...


class SearchIndexRecoveryAPI:
    """Explicit, idempotent repair of the disposable local search projection."""

    def __init__(self, index: EvidenceSearchIndex) -> None:
        if not isinstance(index, EvidenceSearchIndex):
            raise TypeError("index must be an EvidenceSearchIndex")
        self.index = index

    @staticmethod
    def is_post_route(path: str) -> bool:
        parsed = urlparse(path)
        return parsed.path == SEARCH_INDEX_RECOVERY_PATH

    def handle_post(self, handler: SearchIndexRecoveryHTTPHandler) -> bool:
        parsed = urlparse(handler.path)
        if parsed.path != SEARCH_INDEX_RECOVERY_PATH:
            return False
        try:
            if parsed.query:
                raise ValueError("query is not allowed")
            length = handler._content_length(
                MAX_SEARCH_INDEX_RECOVERY_BYTES,
                require_body=True,
            )
            body = json.loads(handler._read_exact_body(length).decode("utf-8"))
            if body != {}:
                raise ValueError("body must be empty")
            result = self.index.status()
            counts = result.get("counts")
            if not isinstance(counts, dict) or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in counts.values()
            ):
                raise RuntimeError("search index returned an invalid count")
            documents = sum(counts.values())
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            handler.json_response(
                {
                    "schema_version": "search-index-recovery-error-v1",
                    "code": "search_index_recovery_invalid",
                    "message": "搜索索引恢复请求无效。",
                    "retryable": False,
                    "stage": "index_refresh",
                    "next_action": "none",
                },
                HTTPStatus.BAD_REQUEST,
            )
        except Exception:
            handler.json_response(
                {
                    "schema_version": "search-index-recovery-error-v1",
                    "code": "search_index_recovery_failed",
                    "message": "搜索索引暂未恢复；已保存的科学数据不受影响。",
                    "retryable": True,
                    "stage": "index_refresh",
                    "next_action": "retry_search_index_refresh",
                },
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        else:
            handler.json_response(
                {
                    "schema_version": "search-index-recovery-v1",
                    "status": "ready",
                    "rebuilt": result.get("rebuilt") is True,
                    "incremental": result.get("incremental") is True,
                    "document_count": documents,
                    "stage": "complete",
                    "next_action": "none",
                }
            )
        return True


__all__ = [
    "MAX_SEARCH_INDEX_RECOVERY_BYTES",
    "SEARCH_INDEX_RECOVERY_PATH",
    "SearchIndexRecoveryAPI",
]
