"""Activate strictly audited user transfer packages without touching official state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

from auto_research.evidence.federated_search_session import (
    FederatedSearchSessionProtocol,
    SearchSourceRegistration,
)

from .package_transfer_payloads import (
    audit_transfer_payload_tree,
    open_transferred_literature_repository,
)
from .transfer_package import ImportedTransferPackage, TransferPackageKind


class PackageTransferActivationError(RuntimeError):
    def __init__(self, code: str, safe_message: str, *, retryable: bool = True) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable


@runtime_checkable
class PreparedPersonalTransferMerge(Protocol):
    @property
    def snapshot(self) -> Any: ...

    @property
    def outcome(self) -> str: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class PersonalTransferMergeService(Protocol):
    def prepare(
        self,
        install_path: Any,
        manifest: Mapping[str, Any],
        *,
        package_sha256: str,
        keep_conflicts: bool,
    ) -> PreparedPersonalTransferMerge: ...


@dataclass(frozen=True)
class ActivatedTransferPackage:
    imported: ImportedTransferPackage
    source_id: str
    source_fingerprint: str
    activation_outcome: str
    document_count: int

    def public_dict(self) -> dict[str, Any]:
        value = self.imported.public_dict()
        value.update(
            {
                "activation_outcome": self.activation_outcome,
                "search_ready": True,
                "source_scope": "private",
                "source_id": self.source_id,
                "source_fingerprint": self.source_fingerprint,
                "document_count": self.document_count,
            }
        )
        return value


class PackageTransferActivationService:
    """Bind an installed transfer package to exactly one private search lifecycle."""

    def __init__(
        self,
        *,
        search_session: FederatedSearchSessionProtocol,
        personal_merger: PersonalTransferMergeService,
    ) -> None:
        if not isinstance(search_session, FederatedSearchSessionProtocol):
            raise TypeError("search_session must implement FederatedSearchSessionProtocol")
        if not isinstance(personal_merger, PersonalTransferMergeService):
            raise TypeError("personal_merger must implement PersonalTransferMergeService")
        self._search = search_session
        self._personal = personal_merger

    def activate(
        self,
        imported: Any,
        *,
        keep_conflicts: bool,
    ) -> ActivatedTransferPackage:
        if not isinstance(imported, ImportedTransferPackage):
            raise PackageTransferActivationError(
                "transfer_activation_invalid",
                "资料包激活结果无效。",
                retryable=False,
            )
        if not isinstance(keep_conflicts, bool):
            raise PackageTransferActivationError(
                "package_conflict_policy_invalid",
                "冲突处理选项无效。",
                retryable=False,
            )
        audit_transfer_payload_tree(imported.install_path, imported.manifest)
        if imported.kind is TransferPackageKind.LITERATURE_COLLECTION:
            source = open_transferred_literature_repository(
                imported.install_path,
                imported.manifest,
            )
            registration = SearchSourceRegistration.literature_collection(
                source,
                source_id=source.source_id,
                fingerprint=source.content_fingerprint,
            )
            status = self._search.upsert_private(registration)
            return ActivatedTransferPackage(
                imported,
                source.source_id,
                source.content_fingerprint,
                "activated",
                int(status.get("document_count") or 0),
            )

        prepared = self._personal.prepare(
            imported.install_path,
            imported.manifest,
            package_sha256=imported.package_sha256,
            keep_conflicts=keep_conflicts,
        )
        committed = False
        try:
            snapshot = prepared.snapshot
            registration = SearchSourceRegistration.private(
                snapshot,
                source_id=str(snapshot.source_id),
                fingerprint=str(snapshot.content_fingerprint),
            )
            prepared.commit()
            committed = True
            try:
                status = self._search.refresh_private(registration)
            except Exception:
                prepared.rollback()
                committed = False
                raise
            return ActivatedTransferPackage(
                imported,
                str(snapshot.source_id),
                str(snapshot.content_fingerprint),
                str(prepared.outcome),
                int(status.get("document_count") or 0),
            )
        except PackageTransferActivationError:
            raise
        except Exception as exc:
            code = str(getattr(exc, "code", "transfer_activation_failed"))
            message = str(
                getattr(
                    exc,
                    "safe_message",
                    "资料包已安全安装，但尚未进入搜索；可稍后重试激活。",
                )
            )
            raise PackageTransferActivationError(code, message) from None
        finally:
            if not committed:
                try:
                    prepared.rollback()
                except Exception:
                    pass
            prepared.close()


__all__ = [
    "ActivatedTransferPackage",
    "PackageTransferActivationError",
    "PackageTransferActivationService",
    "PersonalTransferMergeService",
    "PreparedPersonalTransferMerge",
]
