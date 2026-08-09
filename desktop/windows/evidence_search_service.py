from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from auto_research.evidence.federated_search_session import (
    FederatedSearchSession,
    FederatedSearchSessionError,
    FederatedSearchSessionProtocol,
    PrivatePdfLeaseProtocol,
    SearchSourceRegistration,
)


ENTITY_TYPES = frozenset({"item", "finding", "table", "figure"})
SOURCE_SCOPES = frozenset({"official", "private"})
_WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
_FORBIDDEN_KEYS = frozenset(
    {
        "absolute_path",
        "data_root",
        "database_path",
        "file_path",
        "pdf_path",
        "relative_path",
    }
)


class EvidenceSearchError(RuntimeError):
    """Stable, path-free Windows search bridge failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _public_active_identity(active_package: Any) -> dict[str, str]:
    value = active_package.public_dict()
    expected = {"package_id", "package_version", "content_fingerprint"}
    if not isinstance(value, Mapping) or set(value) != expected:
        raise EvidenceSearchError(
            "offline_search_activation_failed",
            "官方资料包身份无法用于离线搜索。",
        )
    return {key: str(value[key]) for key in sorted(expected)}


def _private_identity(source: object) -> str:
    source_id = getattr(source, "source_id", None)
    if not isinstance(source_id, str) or not source_id.strip():
        raise EvidenceSearchError(
            "offline_search_activation_failed",
            "私人实验搜索源缺少稳定身份。",
        )
    return source_id


def _validate_path_free(value: Any, *, depth: int = 0) -> None:
    if depth > 10:
        raise EvidenceSearchError("search_projection_invalid", "搜索结果结构无效。")
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).strip().casefold()
            if not key or key in _FORBIDDEN_KEYS or key.endswith("_path"):
                raise EvidenceSearchError(
                    "search_projection_invalid", "搜索结果包含非公开字段。"
                )
            _validate_path_free(item, depth=depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_path_free(item, depth=depth + 1)
        return
    if isinstance(value, str):
        text = value.strip()
        if text.casefold().startswith(
            ("file://", "sqlite://", "/users/", "/home/", "/private/", "/tmp/", "/var/")
        ):
            raise EvidenceSearchError(
                "search_projection_invalid", "搜索结果包含本机位置。"
            )
        if _WINDOWS_PATH_RE.match(text) or text.startswith(("\\\\", "//")):
            raise EvidenceSearchError(
                "search_projection_invalid", "搜索结果包含本机位置。"
            )


def _validate_document(document: Any) -> dict[str, Any]:
    if not isinstance(document, Mapping):
        raise EvidenceSearchError("search_projection_invalid", "搜索结果不是公开证据对象。")
    value = copy.deepcopy(dict(document))
    if value.get("entity_type") not in ENTITY_TYPES:
        raise EvidenceSearchError("search_projection_invalid", "搜索结果包含未知证据类型。")
    if value.get("source_scope") not in SOURCE_SCOPES:
        raise EvidenceSearchError("search_projection_invalid", "搜索结果包含未知来源范围。")
    for field in ("source_id", "entity_uid"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise EvidenceSearchError("search_projection_invalid", "搜索结果缺少稳定来源身份。")
    _validate_path_free(value)
    return value


def _translate_session_error(error: FederatedSearchSessionError) -> EvidenceSearchError:
    codes = {
        "federated_search_unavailable": "offline_search_unavailable",
        "federated_source_activation_failed": "offline_search_activation_failed",
    }
    return EvidenceSearchError(
        codes.get(error.code, "search_failed"),
        str(error),
    )


class WindowsEvidenceSearchService:
    """Thin Windows projection over the shared atomic federated session."""

    def __init__(
        self,
        *,
        session: FederatedSearchSessionProtocol | None = None,
        engine_factory: Any | None = None,
        private_source: object | None = None,
        private_fingerprint: str = "confirmed-index-v1",
    ) -> None:
        if session is not None and (engine_factory is not None or private_source is not None):
            raise ValueError("an injected session cannot be combined with source construction")
        if session is None:
            kwargs = {"engine_factory": engine_factory} if engine_factory is not None else {}
            session = FederatedSearchSession(**kwargs)
        self.session = session
        if private_source is not None:
            self.activate_private_source(private_source, fingerprint=private_fingerprint)

    def status(self) -> dict[str, Any]:
        value = self.session.status()
        if value.get("schema_version") != "federated-search-readiness-v2":
            raise EvidenceSearchError("search_projection_invalid", "搜索就绪状态契约无效。")
        _validate_path_free(value)
        return copy.deepcopy(value)

    @property
    def is_ready(self) -> bool:
        return self.status()["federated_ready"] is True

    @property
    def official_ready(self) -> bool:
        return self.status()["official_ready"] is True

    @property
    def private_ready(self) -> bool:
        return self.status()["private_ready"] is True

    def deactivate(self) -> None:
        self.session.clear_official()
        self.session.clear_private()

    def deactivate_official_repository(self) -> None:
        try:
            self.session.clear_official()
        except FederatedSearchSessionError as exc:
            raise _translate_session_error(exc) from None

    def clear_private_source(self) -> None:
        try:
            self.session.clear_private()
        except FederatedSearchSessionError as exc:
            raise _translate_session_error(exc) from None

    def refresh_private_source(
        self,
        source: object,
        *,
        source_id: str,
        fingerprint: str,
    ) -> None:
        try:
            registration = SearchSourceRegistration.private(
                source,  # type: ignore[arg-type]
                source_id=source_id,
                fingerprint=fingerprint,
            )
            self.session.refresh_private(registration)
        except FederatedSearchSessionError as exc:
            raise _translate_session_error(exc) from None
        except (TypeError, ValueError):
            raise EvidenceSearchError(
                "offline_search_activation_failed",
                "私人实验搜索源无法安全建立。",
            ) from None

    def activate_private_source(
        self,
        source: object,
        fingerprint: str = "confirmed-index-v1",
    ) -> None:
        self.refresh_private_source(
            source,
            source_id=_private_identity(source),
            fingerprint=fingerprint,
        )

    def activate_literature_collection(
        self,
        source: object,
        *,
        source_id: str,
        fingerprint: str,
    ) -> None:
        try:
            registration = SearchSourceRegistration.literature_collection(
                source,  # type: ignore[arg-type]
                source_id=source_id,
                fingerprint=fingerprint,
            )
            self.session.upsert_private(registration)
        except FederatedSearchSessionError as exc:
            raise _translate_session_error(exc) from None
        except (TypeError, ValueError):
            raise EvidenceSearchError(
                "offline_search_activation_failed",
                "论文集合搜索源无法安全建立。",
            ) from None

    def activate_official_repository(self, *, active_package: Any, repository: Any) -> None:
        try:
            identity = _public_active_identity(active_package)
            registration = SearchSourceRegistration.official(
                repository,
                source_id=identity["package_id"],
                fingerprint=identity["content_fingerprint"],
            )
            self.session.install_official(registration)
        except EvidenceSearchError:
            raise
        except FederatedSearchSessionError as exc:
            raise _translate_session_error(exc) from None
        except (TypeError, ValueError):
            raise EvidenceSearchError(
                "offline_search_activation_failed",
                "四类离线搜索未能安全建立。",
            ) from None

    def search(self, query: str = "", **filters: Any) -> dict[str, Any]:
        try:
            page = self.session.search(query, **filters).as_dict()
            if not isinstance(page, Mapping) or page.get("schema_version") != "federated-search-page-v1":
                raise EvidenceSearchError("search_projection_invalid", "搜索页契约无效。")
            value = copy.deepcopy(dict(page))
            results = value.get("results")
            if not isinstance(results, list):
                raise EvidenceSearchError("search_projection_invalid", "搜索页缺少结果列表。")
            public_hits = []
            for raw_hit in results:
                if not isinstance(raw_hit, Mapping) or "document" not in raw_hit:
                    raise EvidenceSearchError("search_projection_invalid", "搜索命中契约无效。")
                hit = copy.deepcopy(dict(raw_hit))
                hit["document"] = _validate_document(hit["document"])
                public_hits.append(hit)
            value["results"] = public_hits
            _validate_path_free(value)
            return value
        except EvidenceSearchError:
            raise
        except FederatedSearchSessionError as exc:
            raise _translate_session_error(exc) from None
        except (TypeError, ValueError):
            raise EvidenceSearchError("search_request_invalid", "搜索条件无效。") from None
        except Exception:
            raise EvidenceSearchError("search_failed", "离线搜索未能完成。") from None

    def get(self, *, source_scope: str, source_id: str, entity_uid: str) -> dict[str, Any]:
        try:
            document = self.session.get(
                source_scope=source_scope,
                source_id=source_id,
                entity_uid=entity_uid,
            )
            return _validate_document(document)
        except EvidenceSearchError:
            raise
        except FederatedSearchSessionError as exc:
            raise _translate_session_error(exc) from None
        except KeyError:
            raise EvidenceSearchError("evidence_not_found", "未找到指定证据。") from None
        except (TypeError, ValueError):
            raise EvidenceSearchError("search_request_invalid", "证据身份无效。") from None
        except Exception:
            raise EvidenceSearchError("search_failed", "离线证据读取未能完成。") from None

    def open_private_pdf(
        self,
        *,
        source_id: str,
        paper_uid: str,
    ) -> PrivatePdfLeaseProtocol:
        """Return only the shared same-descriptor lease, never a filesystem path."""

        try:
            lease = self.session.open_private_pdf(source_id, paper_uid)
            if not isinstance(lease, PrivatePdfLeaseProtocol):
                try:
                    lease.close()
                except Exception:
                    pass
                raise EvidenceSearchError(
                    "federated_pdf_unavailable",
                    "论文 PDF 无法安全打开。",
                )
            return lease
        except EvidenceSearchError:
            raise
        except (TypeError, ValueError):
            raise EvidenceSearchError(
                "federated_identity_invalid", "论文集合身份无效。"
            ) from None
        except FederatedSearchSessionError as exc:
            if exc.code in {"private_source_not_found", "private_pdf_unavailable"}:
                raise EvidenceSearchError(
                    "federated_pdf_not_found", "该论文集合没有可打开的 PDF。"
                ) from None
            raise EvidenceSearchError(
                "federated_pdf_changed",
                "论文 PDF 缺失或发生变化，请重新导入资料包。",
            ) from None
        except Exception:
            raise EvidenceSearchError(
                "federated_pdf_unavailable", "论文 PDF 无法安全打开。"
            ) from None
