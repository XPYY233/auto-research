from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Mapping
from typing import Any, Callable


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
    """Stable, path-free Windows search service failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _default_engine_factory(sources: Iterable[object]) -> object:
    from auto_research.evidence.federated_search import FederatedEvidenceSearch

    return FederatedEvidenceSearch(tuple(sources))


def _public_active_identity(active_package: Any) -> dict[str, str]:
    value = active_package.public_dict()
    expected = {"package_id", "package_version", "content_fingerprint"}
    if not isinstance(value, Mapping) or set(value) != expected:
        raise EvidenceSearchError(
            "offline_search_activation_failed",
            "官方资料包身份无法用于离线搜索。",
        )
    return {key: str(value[key]) for key in sorted(expected)}


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
        if _WINDOWS_PATH_RE.match(text):
            raise EvidenceSearchError(
                "search_projection_invalid", "搜索结果包含本机位置。"
            )
        if text.startswith(("\\\\", "//")):
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


class WindowsEvidenceSearchService:
    """Own one fail-closed federated index over audited read-only sources."""

    def __init__(
        self,
        *,
        private_source: object | None = None,
        engine_factory: Callable[[Iterable[object]], object] = _default_engine_factory,
    ) -> None:
        self.private_source = private_source
        self.engine_factory = engine_factory
        self._engine: object | None = None
        self._active_package: dict[str, str] | None = None

    @property
    def is_ready(self) -> bool:
        return self._engine is not None and self._active_package is not None

    @property
    def active_package(self) -> dict[str, str] | None:
        return dict(self._active_package) if self._active_package else None

    def deactivate(self) -> None:
        self._engine = None
        self._active_package = None

    def activate_official_repository(self, *, active_package: Any, repository: Any) -> None:
        # Clear first: a failed rebuild must never leave an older source
        # reachable while readiness refers to the newly activated package.
        self.deactivate()
        try:
            identity = _public_active_identity(active_package)
            sources = [repository]
            if self.private_source is not None:
                sources.append(self.private_source)
            engine = self.engine_factory(tuple(sources))
            document_count = getattr(engine, "document_count", None)
            if isinstance(document_count, bool) or not isinstance(document_count, int):
                raise TypeError("federated engine lacks document_count")
        except EvidenceSearchError:
            raise
        except Exception:
            raise EvidenceSearchError(
                "offline_search_activation_failed",
                "四类离线搜索未能安全建立。",
            ) from None
        self._engine = engine
        self._active_package = identity

    def search(self, query: str = "", **filters: Any) -> dict[str, Any]:
        engine = self._require_engine()
        try:
            page = engine.search(query, **filters).as_dict()
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
            self.deactivate()
            raise
        except (TypeError, ValueError):
            raise EvidenceSearchError("search_request_invalid", "搜索条件无效。") from None
        except Exception:
            self.deactivate()
            raise EvidenceSearchError("search_failed", "离线搜索未能完成。") from None

    def get(self, *, source_scope: str, source_id: str, entity_uid: str) -> dict[str, Any]:
        engine = self._require_engine()
        try:
            document = engine.get(
                source_scope=source_scope,
                source_id=source_id,
                entity_uid=entity_uid,
            )
            return _validate_document(document)
        except EvidenceSearchError:
            self.deactivate()
            raise
        except KeyError:
            raise EvidenceSearchError("evidence_not_found", "未找到指定证据。") from None
        except (TypeError, ValueError):
            raise EvidenceSearchError("search_request_invalid", "证据身份无效。") from None
        except Exception:
            self.deactivate()
            raise EvidenceSearchError("search_failed", "离线证据读取未能完成。") from None

    def _require_engine(self) -> Any:
        if not self.is_ready:
            raise EvidenceSearchError(
                "offline_search_unavailable", "离线搜索尚未准备完成。"
            )
        return self._engine
