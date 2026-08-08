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

    def __post_init__(self) -> None:
        if self.source_scope not in SOURCE_SCOPES:
            raise ValueError("unsupported source scope")
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
        return cls("official", source_id, fingerprint, source)

    @classmethod
    def private(
        cls,
        source: StructuredEvidenceSource,
        *,
        source_id: str,
        fingerprint: str,
    ) -> "SearchSourceRegistration":
        return cls("private", source_id, fingerprint, source)

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

    def clear_official(self) -> dict[str, Any]: ...

    def clear_private(self) -> dict[str, Any]: ...

    def search(self, query: str = "", **filters: Any) -> FederatedSearchPage: ...

    def get(self, *, source_scope: str, source_id: str, entity_uid: str) -> dict[str, Any]: ...


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
        self._private: SearchSourceRegistration | None = None
        self._engine: FederatedEvidenceSearch | None = None
        if official is not None and official.source_scope != "official":
            raise ValueError("official registration must use official scope")
        if private is not None and private.source_scope != "private":
            raise ValueError("private registration must use private scope")
        if official is not None or private is not None:
            try:
                engine = self._build_engine(official, private)
            except Exception:
                raise FederatedSearchSessionError(
                    "federated_source_activation_failed",
                    "离线搜索源未能安全建立。",
                ) from None
            self._official = official
            self._private = private
            self._engine = engine

    @property
    def is_ready(self) -> bool:
        with self._lock:
            return self._engine is not None

    def status(self) -> dict[str, Any]:
        with self._lock:
            official = self._official
            private = self._private
            engine = self._engine
        return {
            "schema_version": "federated-search-readiness-v2",
            "official_ready": official is not None and engine is not None,
            "private_ready": private is not None and engine is not None,
            "federated_ready": engine is not None,
            "document_count": int(engine.document_count) if engine is not None else 0,
            "official_source": official.public_identity() if official is not None else None,
            "private_source": private.public_identity() if private is not None else None,
        }

    def install_official(self, registration: SearchSourceRegistration) -> dict[str, Any]:
        self._require_scope(registration, "official")
        return self._replace(official=registration)

    def install_private(self, registration: SearchSourceRegistration) -> dict[str, Any]:
        self._require_scope(registration, "private")
        return self._replace(private=registration)

    def refresh_private(self, registration: SearchSourceRegistration) -> dict[str, Any]:
        """Re-index a newly snapshotted confirmed/indexable private source."""

        return self.install_private(registration)

    def clear_official(self) -> dict[str, Any]:
        return self._replace(clear_official=True)

    def clear_private(self) -> dict[str, Any]:
        return self._replace(clear_private=True)

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

    def _replace(
        self,
        *,
        official: SearchSourceRegistration | None = None,
        private: SearchSourceRegistration | None = None,
        clear_official: bool = False,
        clear_private: bool = False,
    ) -> dict[str, Any]:
        with self._mutation_lock:
            with self._lock:
                next_official = None if clear_official else (official or self._official)
                next_private = None if clear_private else (private or self._private)
            if next_official is None and next_private is None:
                rebuilt = None
            else:
                try:
                    rebuilt = self._build_engine(next_official, next_private)
                except Exception:
                    raise FederatedSearchSessionError(
                        "federated_source_activation_failed",
                        "离线搜索源未能安全建立；原有可用搜索保持不变。",
                    ) from None
            with self._lock:
                self._official = next_official
                self._private = next_private
                self._engine = rebuilt
                return self.status()

    def _build_engine(
        self,
        official: SearchSourceRegistration | None,
        private: SearchSourceRegistration | None,
    ) -> FederatedEvidenceSearch:
        registrations = tuple(item for item in (official, private) if item is not None)
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
