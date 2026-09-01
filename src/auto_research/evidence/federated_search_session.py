from __future__ import annotations

import re
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .federated_search import (
    FederatedEvidenceSearch,
    FederatedSearchPage,
    StructuredEvidenceSource,
)


SOURCE_SCOPES = frozenset({"official", "private"})
SOURCE_KINDS = frozenset(
    {"official_repository", "personal_experiments", "literature_collection"}
)
_WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")


class FederatedSearchSessionError(RuntimeError):
    """Stable path-free lifecycle failure for platform adapters."""

    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message

    def public_dict(self) -> dict[str, str]:
        return {
            "schema_version": "federated-search-session-error-v1",
            "code": self.code,
            "safe_message": self.safe_message,
        }


def _stable_identity(value: Any, field_name: str) -> str:
    cleaned = " ".join(str(value or "").split()).strip()
    if not cleaned or len(cleaned) > 500:
        raise ValueError(f"{field_name} is invalid")
    lowered = cleaned.casefold()
    if (
        lowered.startswith(("file://", "sqlite://", "/users/", "/home/", "/private/", "/tmp/"))
        or _WINDOWS_PATH_RE.match(cleaned)
        or cleaned.startswith(("\\\\", "//"))
    ):
        raise ValueError(f"{field_name} must be path-free")
    return cleaned


@dataclass(frozen=True)
class SearchSourceRegistration:
    """One opaque read-only source plus its public stable identity."""

    source_scope: str
    source_id: str
    fingerprint: str
    source: StructuredEvidenceSource
    source_kind: str

    def __post_init__(self) -> None:
        if self.source_scope not in SOURCE_SCOPES:
            raise ValueError("unsupported source scope")
        if self.source_kind not in SOURCE_KINDS:
            raise ValueError("unsupported source kind")
        if (self.source_scope == "official") != (
            self.source_kind == "official_repository"
        ):
            raise ValueError("source scope and kind do not match")
        object.__setattr__(self, "source_id", _stable_identity(self.source_id, "source_id"))
        object.__setattr__(self, "fingerprint", _stable_identity(self.fingerprint, "fingerprint"))
        if not callable(getattr(self.source, "iter_search_documents", None)):
            raise TypeError("source must provide iter_search_documents()")

    @classmethod
    def official(
        cls,
        source: StructuredEvidenceSource,
        *,
        source_id: str,
        fingerprint: str,
    ) -> "SearchSourceRegistration":
        return cls("official", source_id, fingerprint, source, "official_repository")

    @classmethod
    def private(
        cls,
        source: StructuredEvidenceSource,
        *,
        source_id: str,
        fingerprint: str,
    ) -> "SearchSourceRegistration":
        return cls("private", source_id, fingerprint, source, "personal_experiments")

    @classmethod
    def literature_collection(
        cls,
        source: StructuredEvidenceSource,
        *,
        source_id: str,
        fingerprint: str,
    ) -> "SearchSourceRegistration":
        return cls("private", source_id, fingerprint, source, "literature_collection")

    def public_identity(self) -> dict[str, str]:
        return {
            "source_scope": self.source_scope,
            "source_id": self.source_id,
            "fingerprint": self.fingerprint,
        }


@runtime_checkable
class FederatedSearchSessionProtocol(Protocol):
    """Narrow lifecycle/search boundary consumed by macOS and Windows adapters."""

    def status(self) -> dict[str, Any]: ...

    def install_official(self, registration: SearchSourceRegistration) -> dict[str, Any]: ...

    def install_private(self, registration: SearchSourceRegistration) -> dict[str, Any]: ...

    def refresh_private(self, registration: SearchSourceRegistration) -> dict[str, Any]: ...

    def upsert_private(self, registration: SearchSourceRegistration) -> dict[str, Any]: ...

    def remove_private(self, source_id: str) -> dict[str, Any]: ...

    def open_private_pdf(
        self, source_id: str, paper_uid: str
    ) -> "PrivatePdfLeaseProtocol": ...

    def open_pdf(
        self, source_scope: str, source_id: str, paper_uid: str
    ) -> "PrivatePdfLeaseProtocol": ...

    def open_asset(
        self, source_scope: str, source_id: str, entity_uid: str
    ) -> "VisualAssetLeaseProtocol": ...

    def clear_official(self) -> dict[str, Any]: ...

    def clear_private(self) -> dict[str, Any]: ...

    def search(self, query: str = "", **filters: Any) -> FederatedSearchPage: ...

    def get(self, *, source_scope: str, source_id: str, entity_uid: str) -> dict[str, Any]: ...


@runtime_checkable
class PrivatePdfLeaseProtocol(Protocol):
    """Path-free, same-file-descriptor lease used only by protected PDF streaming."""

    @property
    def source_id(self) -> str: ...

    @property
    def paper_uid(self) -> str: ...

    @property
    def size_bytes(self) -> int: ...

    @property
    def media_type(self) -> str: ...

    def read(self, size: int = 1024 * 1024) -> bytes: ...

    def close(self) -> None: ...

    def public_metadata(self) -> Mapping[str, Any]: ...

    def __enter__(self) -> "PrivatePdfLeaseProtocol": ...

    def __exit__(self, *_exc: Any) -> None: ...


@runtime_checkable
class VisualAssetLeaseProtocol(Protocol):
    """Path-free descriptor lease used by protected federated image streaming."""

    @property
    def source_id(self) -> str: ...

    @property
    def entity_uid(self) -> str: ...

    @property
    def size_bytes(self) -> int: ...

    @property
    def media_type(self) -> str: ...

    def read(self, size: int = 1024 * 1024) -> bytes: ...

    def close(self) -> None: ...

    def public_metadata(self) -> Mapping[str, Any]: ...

    def __enter__(self) -> "VisualAssetLeaseProtocol": ...

    def __exit__(self, *_exc: Any) -> None: ...


class _IdentityBoundSource:
    """Validate lifecycle identity while leaving DTOs unchanged for the engine."""

    def __init__(self, registration: SearchSourceRegistration) -> None:
        self.registration = registration

    def iter_search_documents(self) -> Iterable[Mapping[str, Any] | Any]:
        for raw in self.registration.source.iter_search_documents():
            if isinstance(raw, Mapping):
                document = raw
            else:
                serializer = getattr(raw, "as_dict", None)
                if not callable(serializer):
                    raise TypeError("search source yielded an unsupported document")
                document = serializer()
            if not isinstance(document, Mapping):
                raise TypeError("search document must be a mapping")
            if (
                document.get("source_scope") != self.registration.source_scope
                or document.get("source_id") != self.registration.source_id
            ):
                raise ValueError("search source identity mismatch")
            # Freeze the exact identity-checked mapping; a stateful serializer
            # must not return a different document when the engine reads it.
            yield dict(document)


EngineFactory = Callable[[Iterable[StructuredEvidenceSource]], FederatedEvidenceSearch]


class FederatedSearchSession:
    """Atomic official/private lifecycle around the existing immutable search engine."""

    def __init__(
        self,
        *,
        official: SearchSourceRegistration | None = None,
        private: SearchSourceRegistration | None = None,
        engine_factory: EngineFactory = FederatedEvidenceSearch,
    ) -> None:
        self._lock = threading.RLock()
        self._mutation_lock = threading.Lock()
        self._engine_factory = engine_factory
        self._official: SearchSourceRegistration | None = None
        self._privates: dict[str, SearchSourceRegistration] = {}
        # One compatibility slot is reserved for the user's mutable personal
        # repository. Imported read-only literature collections are siblings,
        # never candidates for replacement by refresh_private().
        self._personal_private_id: str | None = None
        self._engine: FederatedEvidenceSearch | None = None
        if official is not None and official.source_scope != "official":
            raise ValueError("official registration must use official scope")
        if private is not None and private.source_scope != "private":
            raise ValueError("private registration must use private scope")
        if private is not None and private.source_kind != "personal_experiments":
            raise ValueError("constructor private source must be personal experiments")
        if official is not None or private is not None:
            try:
                private_sources = {private.source_id: private} if private is not None else {}
                engine = self._build_engine(official, private_sources.values())
            except Exception:
                raise FederatedSearchSessionError(
                    "federated_source_activation_failed",
                    "离线搜索源未能安全建立。",
                ) from None
            self._official = official
            self._privates = private_sources
            self._personal_private_id = private.source_id if private is not None else None
            self._engine = engine

    @property
    def is_ready(self) -> bool:
        with self._lock:
            return self._engine is not None

    def status(self) -> dict[str, Any]:
        with self._lock:
            official = self._official
            private = self._privates.get(self._personal_private_id or "")
            if private is None and self._privates:
                private = self._privates[sorted(self._privates)[0]]
            engine = self._engine
            private_ready = bool(self._privates) and engine is not None
        return {
            "schema_version": "federated-search-readiness-v2",
            "official_ready": official is not None and engine is not None,
            "private_ready": private_ready,
            "federated_ready": engine is not None,
            "document_count": int(engine.document_count) if engine is not None else 0,
            "official_source": official.public_identity() if official is not None else None,
            "private_source": private.public_identity() if private is not None else None,
        }

    def install_official(self, registration: SearchSourceRegistration) -> dict[str, Any]:
        self._require_scope(registration, "official")
        return self._mutate(official=registration)

    def install_private(self, registration: SearchSourceRegistration) -> dict[str, Any]:
        self._require_scope(registration, "private")
        self._require_kind(registration, "personal_experiments")
        return self._mutate(
            replace_privates={registration.source_id: registration},
            personal_private_id=registration.source_id,
        )

    def refresh_private(self, registration: SearchSourceRegistration) -> dict[str, Any]:
        """Re-index a newly snapshotted confirmed/indexable private source."""
        self._require_scope(registration, "private")
        self._require_kind(registration, "personal_experiments")
        return self._mutate(
            refresh_personal=registration,
        )

    def upsert_private(self, registration: SearchSourceRegistration) -> dict[str, Any]:
        """Add or replace one private collection without removing its siblings."""

        self._require_scope(registration, "private")
        self._require_kind(registration, "literature_collection")
        return self._mutate(
            upsert_collection=registration,
        )

    def remove_private(self, source_id: str) -> dict[str, Any]:
        return self._mutate(remove_private_id=_stable_identity(source_id, "source_id"))

    def open_private_pdf(
        self, source_id: str, paper_uid: str
    ) -> PrivatePdfLeaseProtocol:
        """Open an imported PDF as a path-free, same-descriptor lease."""

        return self.open_pdf("private", source_id, paper_uid)

    def open_pdf(
        self, source_scope: str, source_id: str, paper_uid: str
    ) -> PrivatePdfLeaseProtocol:
        """Open a verified official or private PDF without exposing its path."""

        if source_scope not in SOURCE_SCOPES:
            raise ValueError("unsupported source scope")
        unavailable_code = (
            "private_pdf_unavailable"
            if source_scope == "private"
            else "source_pdf_unavailable"
        )
        normalized_source = _stable_identity(source_id, "source_id")
        normalized_paper = _stable_identity(paper_uid, "paper_uid")
        with self._lock:
            if source_scope == "official":
                registration = self._official
                if registration is not None and registration.source_id != normalized_source:
                    registration = None
            else:
                registration = self._privates.get(normalized_source)
        if registration is None:
            raise FederatedSearchSessionError(
                "pdf_source_not_found", "找不到所选的文献来源。"
            )
        if registration.source_kind not in {"literature_collection", "official_repository"}:
            raise FederatedSearchSessionError(
                unavailable_code, "该资料来源不提供论文 PDF。"
            )
        resolver = getattr(registration.source, "open_pdf", None)
        if not callable(resolver):
            raise FederatedSearchSessionError(
                unavailable_code, "该资料来源不提供论文 PDF。"
            )
        try:
            lease = resolver(normalized_paper)
        except Exception as exc:
            code = str(getattr(exc, "code", "source_pdf_changed"))
            if code == "transfer_payload_changed":
                code = (
                    "private_pdf_changed"
                    if source_scope == "private"
                    else "source_pdf_changed"
                )
            elif source_scope == "private" and code == "source_pdf_changed":
                code = "private_pdf_changed"
            raise FederatedSearchSessionError(
                code
                if code in {
                    "source_pdf_changed",
                    "source_pdf_unavailable",
                    "official_pdf_changed",
                    "private_pdf_changed",
                }
                else "source_pdf_changed",
                "论文 PDF 缺失或发生变化，请重新导入资料包。",
            ) from None
        if lease is None:
            raise FederatedSearchSessionError(
                unavailable_code, "该论文没有可用的 PDF。"
            )
        if not isinstance(lease, PrivatePdfLeaseProtocol):
            try:
                lease.close()
            except Exception:
                pass
            raise FederatedSearchSessionError(
                unavailable_code, "该论文 PDF 无法安全打开。"
            )
        metadata = lease.public_metadata()
        if (
            lease.source_id != normalized_source
            or lease.paper_uid != normalized_paper
            or metadata.get("source_scope") != source_scope
        ):
            lease.close()
            raise FederatedSearchSessionError(
                unavailable_code, "论文 PDF 来源身份不一致。"
            )
        return lease

    def open_asset(
        self, source_scope: str, source_id: str, entity_uid: str
    ) -> VisualAssetLeaseProtocol:
        """Open a verified visual asset without exposing its path or asset key."""

        if source_scope not in SOURCE_SCOPES:
            raise ValueError("unsupported source scope")
        normalized_source = _stable_identity(source_id, "source_id")
        normalized_entity = _stable_identity(entity_uid, "entity_uid")
        with self._lock:
            if source_scope == "official":
                registration = self._official
                if registration is not None and registration.source_id != normalized_source:
                    registration = None
            else:
                registration = self._privates.get(normalized_source)
        if registration is None:
            raise FederatedSearchSessionError(
                "asset_source_not_found", "找不到所选的文献来源。"
            )
        if registration.source_kind != "official_repository":
            raise FederatedSearchSessionError(
                "source_asset_unavailable", "该资料来源不提供视觉资产。"
            )
        resolver = getattr(registration.source, "open_entity_asset", None)
        if not callable(resolver):
            raise FederatedSearchSessionError(
                "source_asset_unavailable", "该资料来源不提供视觉资产。"
            )
        try:
            lease = resolver(normalized_entity)
        except Exception as exc:
            code = str(getattr(exc, "code", "source_asset_changed"))
            raise FederatedSearchSessionError(
                code
                if code in {
                    "asset_missing",
                    "asset_checksum",
                    "asset_media",
                    "asset_ambiguous",
                }
                else "source_asset_changed",
                "视觉资产缺失或发生变化，请重新导入资料包。",
            ) from None
        if lease is None:
            raise FederatedSearchSessionError(
                "source_asset_unavailable", "该证据没有可用的视觉资产。"
            )
        if not isinstance(lease, VisualAssetLeaseProtocol):
            try:
                lease.close()
            except Exception:
                pass
            raise FederatedSearchSessionError(
                "source_asset_unavailable", "该视觉资产无法安全打开。"
            )
        try:
            metadata = lease.public_metadata()
            metadata_size = metadata.get("size_bytes")
            valid_identity = (
                isinstance(metadata, Mapping)
                and metadata.get("schema_version")
                == "official-visual-asset-lease-v1"
                and lease.source_id == normalized_source
                and lease.entity_uid == normalized_entity
                and metadata.get("source_scope") == source_scope
                and metadata.get("source_id") == normalized_source
                and metadata.get("entity_uid") == normalized_entity
                and not isinstance(metadata_size, bool)
                and isinstance(metadata_size, int)
                and not isinstance(lease.size_bytes, bool)
                and isinstance(lease.size_bytes, int)
                and metadata_size == lease.size_bytes
                and metadata.get("media_type") == lease.media_type
                and lease.media_type in {"image/png", "image/jpeg"}
            )
        except Exception:
            valid_identity = False
        if not valid_identity:
            lease.close()
            raise FederatedSearchSessionError(
                "source_asset_unavailable", "视觉资产来源身份不一致。"
            )
        return lease

    def clear_official(self) -> dict[str, Any]:
        return self._mutate(clear_official=True)

    def clear_private(self) -> dict[str, Any]:
        return self._mutate(replace_privates={}, personal_private_id=None)

    def search(self, query: str = "", **filters: Any) -> FederatedSearchPage:
        engine = self._require_engine()
        return engine.search(query, **filters)

    def get(self, *, source_scope: str, source_id: str, entity_uid: str) -> dict[str, Any]:
        engine = self._require_engine()
        return engine.get(
            source_scope=source_scope,
            source_id=source_id,
            entity_uid=entity_uid,
        )

    @staticmethod
    def _require_scope(registration: SearchSourceRegistration, scope: str) -> None:
        if not isinstance(registration, SearchSourceRegistration) or registration.source_scope != scope:
            raise ValueError(f"registration must use {scope} scope")

    @staticmethod
    def _require_kind(registration: SearchSourceRegistration, kind: str) -> None:
        if registration.source_kind != kind:
            raise ValueError(f"registration must use {kind} kind")

    def _mutate(
        self,
        *,
        official: SearchSourceRegistration | None = None,
        replace_privates: Mapping[str, SearchSourceRegistration] | None = None,
        personal_private_id: str | None = None,
        refresh_personal: SearchSourceRegistration | None = None,
        upsert_collection: SearchSourceRegistration | None = None,
        remove_private_id: str | None = None,
        clear_official: bool = False,
    ) -> dict[str, Any]:
        with self._mutation_lock:
            with self._lock:
                next_official = None if clear_official else (official or self._official)
                next_privates = (
                    dict(replace_privates)
                    if replace_privates is not None
                    else dict(self._privates)
                )
                next_personal = (
                    personal_private_id
                    if replace_privates is not None
                    else self._personal_private_id
                )
            if refresh_personal is not None:
                if next_personal is not None:
                    next_privates.pop(next_personal, None)
                next_privates[refresh_personal.source_id] = refresh_personal
                next_personal = refresh_personal.source_id
            if upsert_collection is not None:
                next_privates[upsert_collection.source_id] = upsert_collection
            if remove_private_id is not None:
                next_privates.pop(remove_private_id, None)
                if next_personal == remove_private_id:
                    next_personal = None
            if next_personal is not None and next_personal not in next_privates:
                raise ValueError("personal private source is not registered")
            if next_official is None and not next_privates:
                rebuilt = None
            else:
                try:
                    rebuilt = self._build_engine(next_official, next_privates.values())
                except Exception:
                    raise FederatedSearchSessionError(
                        "federated_source_activation_failed",
                        "离线搜索源未能安全建立；原有可用搜索保持不变。",
                    ) from None
            with self._lock:
                self._official = next_official
                self._privates = next_privates
                self._personal_private_id = next_personal
                self._engine = rebuilt
                return self.status()

    def _build_engine(
        self,
        official: SearchSourceRegistration | None,
        private_sources: Iterable[SearchSourceRegistration],
    ) -> FederatedEvidenceSearch:
        registrations = tuple(
            item
            for item in (
                *((official,) if official is not None else ()),
                *sorted(private_sources, key=lambda value: value.source_id),
            )
        )
        if not registrations:
            raise ValueError("at least one search source is required")
        engine = self._engine_factory(tuple(_IdentityBoundSource(item) for item in registrations))
        count = getattr(engine, "document_count", None)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise TypeError("federated engine lacks a valid document_count")
        return engine

    def _require_engine(self) -> FederatedEvidenceSearch:
        with self._lock:
            engine = self._engine
        if engine is None:
            raise FederatedSearchSessionError(
                "federated_search_unavailable",
                "尚未启用官方资料或私人实验搜索源。",
            )
        return engine
