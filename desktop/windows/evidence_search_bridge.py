from __future__ import annotations

from typing import Any, Iterable

from evidence_search_service import EvidenceSearchError, WindowsEvidenceSearchService


PUBLIC_ERROR_MESSAGES = {
    "offline_search_unavailable": "离线搜索尚未准备完成。",
    "offline_search_activation_failed": "四类离线搜索未能安全建立。",
    "search_projection_invalid": "搜索结果未通过公开字段检查。",
    "search_request_invalid": "搜索条件无效。",
    "evidence_not_found": "未找到指定证据。",
    "search_failed": "离线搜索未能完成。",
}


class EvidenceSearchBridgeAdapter:
    """Thin native bridge that returns only stable public DTOs."""

    def __init__(self, service: WindowsEvidenceSearchService) -> None:
        self.service = service

    def search(
        self,
        query: str = "",
        *,
        page: int = 1,
        page_size: int = 20,
        entity_types: Iterable[str] | None = None,
        source_scopes: Iterable[str] | None = None,
        source_ids: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        return self.service.search(
            query,
            page=page,
            page_size=page_size,
            entity_types=entity_types,
            source_scopes=source_scopes,
            source_ids=source_ids,
        )

    def get(self, *, source_scope: str, source_id: str, entity_uid: str) -> dict[str, Any]:
        return self.service.get(
            source_scope=source_scope,
            source_id=source_id,
            entity_uid=entity_uid,
        )

    @staticmethod
    def public_error(error: BaseException) -> dict[str, str]:
        if isinstance(error, EvidenceSearchError):
            code = error.code if error.code in PUBLIC_ERROR_MESSAGES else "search_failed"
            return {"code": code, "message": PUBLIC_ERROR_MESSAGES[code]}
        return {"code": "search_failed", "message": "离线搜索未能完成。"}
