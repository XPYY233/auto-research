from __future__ import annotations

import threading
from http import HTTPStatus
from typing import Any, Iterable, Protocol
from urllib.parse import parse_qs, urlparse

from auto_research.evidence.federated_search import FederatedEvidenceSearch
from auto_research.product import ActiveOfficialPackage, OfficialEvidenceRepository


FEDERATED_SEARCH_PATH = "/api/desktop/federated-search"
FEDERATED_EVIDENCE_PATH = "/api/desktop/federated-evidence"


class FederatedHTTPHandler(Protocol):
    path: str

    def json_response(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None: ...


class DesktopFederatedSearchError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: HTTPStatus) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def public_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


class DesktopFederatedSearchService:
    """Atomic, read-only index over audited repositories injected by the desktop."""

    def __init__(self, *, private_source: object | None = None) -> None:
        self._private_source = private_source
        self._active: ActiveOfficialPackage | None = None
        self._search: FederatedEvidenceSearch | None = None
        self._lock = threading.RLock()

    def install_official_repository(
        self,
        active: ActiveOfficialPackage,
        repository: OfficialEvidenceRepository,
    ) -> None:
        if (
            repository.package_id != active.package_id
            or repository.package_version != active.package_version
            or repository.content_fingerprint != active.content_fingerprint
        ):
            raise DesktopFederatedSearchError(
                "federated_repository_identity_invalid",
                "官方资料库身份校验失败，未更新搜索索引。",
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        sources: list[object] = [repository]
        if self._private_source is not None:
            sources.append(self._private_source)
        rebuilt = FederatedEvidenceSearch(sources)
        with self._lock:
            self._active = active
            self._search = rebuilt

    def status(self) -> dict[str, Any]:
        with self._lock:
            if self._active is None or self._search is None:
                return {"ready": False, "document_count": 0}
            return {
                "ready": True,
                "document_count": self._search.document_count,
                "package_id": self._active.package_id,
                "package_version": self._active.package_version,
                "content_fingerprint": self._active.content_fingerprint,
            }

    def clear(self) -> None:
        with self._lock:
            self._active = None
            self._search = None

    def search(self, **kwargs: Any) -> dict[str, Any]:
        service = self._require_search()
        try:
            return service.search(**kwargs).as_dict()
        except (TypeError, ValueError) as exc:
            raise DesktopFederatedSearchError(
                "federated_search_invalid",
                "官方资料库搜索条件无效。",
                status=HTTPStatus.BAD_REQUEST,
            ) from exc

    def get(self, *, source_scope: str, source_id: str, entity_uid: str) -> dict[str, Any]:
        service = self._require_search()
        try:
            return service.get(
                source_scope=source_scope,
                source_id=source_id,
                entity_uid=entity_uid,
            )
        except ValueError as exc:
            raise DesktopFederatedSearchError(
                "federated_identity_invalid",
                "官方证据身份无效。",
                status=HTTPStatus.BAD_REQUEST,
            ) from exc
        except KeyError as exc:
            raise DesktopFederatedSearchError(
                "federated_evidence_not_found",
                "未找到对应的官方证据。",
                status=HTTPStatus.NOT_FOUND,
            ) from exc

    def _require_search(self) -> FederatedEvidenceSearch:
        with self._lock:
            if self._search is None:
                raise DesktopFederatedSearchError(
                    "evidence_package_required",
                    "请先导入并启用官方资料包。",
                    status=HTTPStatus.CONFLICT,
                )
            return self._search


class FederatedSearchAPI:
    def __init__(self, service: DesktopFederatedSearchService) -> None:
        self.service = service

    def handle_get(self, handler: FederatedHTTPHandler) -> bool:
        parsed = urlparse(handler.path)
        if parsed.path not in {FEDERATED_SEARCH_PATH, FEDERATED_EVIDENCE_PATH}:
            return False
        query = parse_qs(parsed.query, keep_blank_values=True)
        try:
            if parsed.path == FEDERATED_SEARCH_PATH:
                payload = self.service.search(**self._search_arguments(query))
            else:
                payload = self.service.get(**self._identity_arguments(query))
        except DesktopFederatedSearchError as exc:
            handler.json_response(exc.public_dict(), exc.status)
        else:
            handler.json_response(payload)
        return True

    @classmethod
    def _search_arguments(cls, query: dict[str, list[str]]) -> dict[str, Any]:
        allowed = {"q", "page", "page_size", "entity_type", "source_scope", "source_id"}
        if set(query) - allowed:
            cls._invalid_search()
        return {
            "query": cls._single(query, "q", default=""),
            "page": cls._positive_integer(query, "page", default=1),
            "page_size": cls._positive_integer(query, "page_size", default=20),
            "entity_types": cls._multiple(query, "entity_type"),
            "source_scopes": cls._multiple(query, "source_scope"),
            "source_ids": cls._multiple(query, "source_id"),
        }

    @classmethod
    def _identity_arguments(cls, query: dict[str, list[str]]) -> dict[str, str]:
        required = {"source_scope", "source_id", "entity_uid"}
        if set(query) != required:
            cls._invalid_identity()
        values: dict[str, str] = {}
        for key in sorted(required):
            candidates = query.get(key) or []
            if len(candidates) != 1 or not candidates[0]:
                cls._invalid_identity()
            values[key] = candidates[0]
        return values

    @staticmethod
    def _single(
        query: dict[str, list[str]],
        key: str,
        *,
        default: str | None = None,
    ) -> str:
        values = query.get(key)
        if values is None:
            if default is not None:
                return default
            FederatedSearchAPI._invalid_search()
        if len(values) != 1:
            FederatedSearchAPI._invalid_search()
        return values[0]

    @staticmethod
    def _positive_integer(
        query: dict[str, list[str]],
        key: str,
        *,
        default: int,
    ) -> int:
        values = query.get(key)
        if values is None:
            return default
        if len(values) != 1 or not values[0].isascii() or not values[0].isdigit():
            FederatedSearchAPI._invalid_search()
        value = int(values[0])
        if value < 1:
            FederatedSearchAPI._invalid_search()
        return value

    @staticmethod
    def _multiple(query: dict[str, list[str]], key: str) -> Iterable[str] | None:
        values = query.get(key)
        if values is None:
            return None
        if not values or any(not value for value in values):
            FederatedSearchAPI._invalid_search()
        return tuple(values)

    @staticmethod
    def _invalid_search() -> None:
        raise DesktopFederatedSearchError(
            "federated_search_invalid",
            "官方资料库搜索条件无效。",
            status=HTTPStatus.BAD_REQUEST,
        )

    @staticmethod
    def _invalid_identity() -> None:
        raise DesktopFederatedSearchError(
            "federated_identity_invalid",
            "官方证据身份无效。",
            status=HTTPStatus.BAD_REQUEST,
        )
