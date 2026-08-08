from __future__ import annotations

from http import HTTPStatus
from typing import Any, Iterable, Protocol
from urllib.parse import parse_qs, urlparse

from auto_research.evidence.federated_search_session import (
    FederatedSearchSession,
    FederatedSearchSessionError,
    FederatedSearchSessionProtocol,
    SearchSourceRegistration,
)
from auto_research.product.runtime_api import ActiveOfficialPackage, OfficialEvidenceRepository


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
    """Thin macOS adapter over the shared atomic federated-search session."""

    def __init__(
        self,
        *,
        session: FederatedSearchSessionProtocol | None = None,
    ) -> None:
        self.session = session if session is not None else FederatedSearchSession()

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
        try:
            self.session.install_official(
                SearchSourceRegistration.official(
                    repository,
                    source_id=active.package_id,
                    fingerprint=active.content_fingerprint,
                )
            )
        except (FederatedSearchSessionError, TypeError, ValueError):
            raise DesktopFederatedSearchError(
                "federated_repository_identity_invalid",
                "官方资料库身份校验失败，未更新搜索索引。",
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            ) from None

    def refresh_private_source(
        self,
        source: object,
        *,
        source_id: str,
        fingerprint: str,
    ) -> None:
        try:
            registration = SearchSourceRegistration.private(
                source,
                source_id=source_id,
                fingerprint=fingerprint,
            )
            self.session.refresh_private(registration)
        except (FederatedSearchSessionError, TypeError, ValueError):
            raise DesktopFederatedSearchError(
                "federated_private_source_invalid",
                "私人实验搜索源未能安全建立，原有搜索保持不变。",
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            ) from None

    def status(self) -> dict[str, Any]:
        return dict(self.session.status())

    def clear_official_repository(self) -> None:
        try:
            self.session.clear_official()
        except FederatedSearchSessionError:
            raise DesktopFederatedSearchError(
                "federated_repository_reset_failed",
                "官方资料搜索源暂未清除，原有搜索保持不变。",
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            ) from None

    def search(self, **kwargs: Any) -> dict[str, Any]:
        try:
            return self.session.search(**kwargs).as_dict()
        except FederatedSearchSessionError as exc:
            raise self._session_unavailable(exc) from None
        except (TypeError, ValueError) as exc:
            raise DesktopFederatedSearchError(
                "federated_search_invalid",
                "离线资料搜索条件无效。",
                status=HTTPStatus.BAD_REQUEST,
            ) from exc

    def get(self, *, source_scope: str, source_id: str, entity_uid: str) -> dict[str, Any]:
        try:
            return self.session.get(
                source_scope=source_scope,
                source_id=source_id,
                entity_uid=entity_uid,
            )
        except FederatedSearchSessionError as exc:
            raise self._session_unavailable(exc) from None
        except ValueError as exc:
            raise DesktopFederatedSearchError(
                "federated_identity_invalid",
                "离线证据身份无效。",
                status=HTTPStatus.BAD_REQUEST,
            ) from exc
        except KeyError as exc:
            raise DesktopFederatedSearchError(
                "federated_evidence_not_found",
                "未找到对应的离线证据。",
                status=HTTPStatus.NOT_FOUND,
            ) from exc

    @staticmethod
    def _session_unavailable(
        error: FederatedSearchSessionError,
    ) -> DesktopFederatedSearchError:
        if error.code == "federated_search_unavailable":
            return DesktopFederatedSearchError(
                "evidence_package_required",
                "请先启用官方资料包或确认私人实验数据。",
                status=HTTPStatus.CONFLICT,
            )
        return DesktopFederatedSearchError(
            "federated_search_unavailable",
            "离线搜索暂时不可用，原有数据未改变。",
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )


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
