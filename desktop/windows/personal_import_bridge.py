from __future__ import annotations

import threading
from typing import Any, Mapping, Protocol

from auto_research.personal.import_service import (
    PersonalImportService,
    PersonalImportServiceError,
)
from auto_research.personal.public_projection import (
    project_personal_renderer_payload,
)
from auto_research.personal.search_source import PrivateSearchSnapshot


_REFRESH_MESSAGE = "数据已保存，搜索刷新待重试。"
class PrivateSearchRefreshService(Protocol):
    def refresh_private_source(
        self,
        source: object,
        *,
        source_id: str,
        fingerprint: str,
    ) -> None: ...


class PersonalImportBridgeAdapter:
    """Path-free Windows projection over shared import and snapshot contracts."""

    def __init__(
        self,
        service: PersonalImportService,
        *,
        search_service: PrivateSearchRefreshService | None = None,
    ) -> None:
        self.service = service
        self.search_service = search_service
        self._search_status: dict[str, Any] = {
            "schema_version": "personal-search-readiness-v1",
            "state": "not_checked",
            "ready": False,
            "document_count": 0,
        }
        self._lock = threading.RLock()

    def preview(self, selection_id: str) -> dict[str, Any]:
        return project_personal_renderer_payload(
            self.service.preview(selection_id).public_dict()
        )

    def status(self, import_id: str) -> dict[str, Any]:
        return project_personal_renderer_payload(
            self.service.status(import_id).public_dict()
        )

    def save_draft(self, import_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        return project_personal_renderer_payload(
            self.service.save_draft(import_id, payload).public_dict()
        )

    def confirm(self, import_id: str, *, expected_revision: int) -> dict[str, Any]:
        status = self.service.confirm(import_id, expected_revision=expected_revision)
        if self.search_service is not None:
            self._refresh_private_search(skip_empty=False)
        return project_personal_renderer_payload(status.public_dict())

    def restore_private_search(self) -> dict[str, Any]:
        if self.search_service is None:
            return self.search_status()
        try:
            self._refresh_private_search(skip_empty=True)
        except PersonalImportServiceError:
            pass
        return self.search_status()

    def refresh_search(self) -> dict[str, Any]:
        if self.search_service is None:
            raise self._refresh_error()
        self._refresh_private_search(skip_empty=True)
        return self.search_status()

    def search_status(self) -> dict[str, Any]:
        with self._lock:
            value = dict(self._search_status)
            if isinstance(value.get("error"), dict):
                value["error"] = dict(value["error"])
            return project_personal_renderer_payload(value)

    def _refresh_private_search(self, *, skip_empty: bool) -> PrivateSearchSnapshot:
        assert self.search_service is not None
        snapshot = self.service.private_search_snapshot()
        if skip_empty and snapshot.document_count == 0:
            with self._lock:
                self._search_status = {
                    "schema_version": "personal-search-readiness-v1",
                    "state": "empty",
                    "ready": False,
                    "document_count": 0,
                }
            return snapshot
        try:
            self.search_service.refresh_private_source(
                snapshot,
                source_id=snapshot.source_id,
                fingerprint=snapshot.content_fingerprint,
            )
        except Exception:
            error = self._refresh_error()
            with self._lock:
                previous = self._search_status
                if previous.get("ready") is True:
                    self._search_status = {
                        "schema_version": "personal-search-readiness-v1",
                        "state": "stale",
                        "ready": True,
                        "document_count": int(previous["document_count"]),
                        "active_fingerprint": str(previous["active_fingerprint"]),
                        "error": error.public_dict(),
                    }
                else:
                    self._search_status = {
                        "schema_version": "personal-search-readiness-v1",
                        "state": "retry_required",
                        "ready": False,
                        "document_count": 0,
                        "error": error.public_dict(),
                    }
            raise error from None
        with self._lock:
            self._search_status = {
                "schema_version": "personal-search-readiness-v1",
                "state": "ready" if snapshot.document_count > 0 else "empty",
                "ready": snapshot.document_count > 0,
                "document_count": snapshot.document_count,
            }
            if snapshot.document_count > 0:
                self._search_status["active_fingerprint"] = snapshot.content_fingerprint
        return snapshot

    @staticmethod
    def _refresh_error() -> PersonalImportServiceError:
        return PersonalImportServiceError(
            "personal_search_refresh_failed",
            _REFRESH_MESSAGE,
            retryable=True,
        )

    @staticmethod
    def public_error(error: BaseException) -> dict[str, Any]:
        if isinstance(error, PersonalImportServiceError):
            return {
                "schema_version": "personal-import-error-v1",
                "code": error.code,
                "message": error.message,
                "retryable": error.retryable,
            }
        return {
            "schema_version": "personal-import-error-v1",
            "code": "personal_import_failed",
            "message": "个人实验导入暂时无法完成。",
            "retryable": True,
        }
