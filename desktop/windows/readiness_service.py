from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


class SearchReadiness(Protocol):
    def status(self) -> Mapping[str, object]: ...


class CredentialStatus(Protocol):
    def status(self) -> Mapping[str, object]: ...


class LibrarianStatus(Protocol):
    @property
    def available(self) -> bool: ...


class ReadinessV2Error(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

    def public_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


@dataclass(frozen=True)
class DesktopReadinessV2:
    official_ready: bool
    private_ready: bool
    federated_ready: bool
    ai_key_configured: bool
    librarian_ready: bool

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "desktop-readiness-v2",
            "official_ready": self.official_ready,
            "private_ready": self.private_ready,
            "federated_ready": self.federated_ready,
            "ai_key_configured": self.ai_key_configured,
            "librarian_ready": self.librarian_ready,
            "can_search_offline": self.federated_ready,
            "can_use_ai": self.librarian_ready,
        }


class WindowsReadinessV2Service:
    def __init__(
        self,
        *,
        search: SearchReadiness,
        credentials: CredentialStatus,
        librarian: LibrarianStatus,
    ) -> None:
        self.search = search
        self.credentials = credentials
        self.librarian = librarian

    def status(self) -> DesktopReadinessV2:
        try:
            credential_status = self.credentials.status()
            configured = credential_status.get("configured") is True
        except Exception:
            raise ReadinessV2Error(
                "credential_store_unavailable",
                "Windows 安全凭据存储不可用。",
            ) from None
        search_status = self.search.status()
        if search_status.get("schema_version") != "federated-search-readiness-v2":
            raise ReadinessV2Error(
                "search_readiness_invalid",
                "离线搜索就绪状态无效。",
            )
        return DesktopReadinessV2(
            official_ready=search_status.get("official_ready") is True,
            private_ready=search_status.get("private_ready") is True,
            federated_ready=search_status.get("federated_ready") is True,
            ai_key_configured=configured,
            librarian_ready=bool(self.librarian.available and configured),
        )
