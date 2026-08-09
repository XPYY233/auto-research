from __future__ import annotations

from http import HTTPStatus
from typing import Any, Mapping, Protocol

from auto_research.product.runtime_api import (
    PackageCenter,
    PackageCenterError,
    PackageExportService,
    PackageJobService,
    PackageTransferImportService,
)


class PackageCenterSummaryProvider(Protocol):
    def summary(self) -> dict[str, Any]: ...


class PackageCenterBridge(Protocol):
    def status(self) -> dict[str, Any]: ...
    def inspect(self, selection_token: str) -> dict[str, Any]: ...
    def plan_export(self, kind: str, scope: str, selection: Any) -> dict[str, Any]: ...
    def start_export(
        self,
        plan_token: str,
        rights_confirmations: Mapping[str, Any],
        destination_token: str,
    ) -> dict[str, Any]: ...
    def start_import(
        self,
        selection_token: str,
        *,
        checksum_ack: bool,
        expected_sha: str,
        keep_conflicts: bool,
    ) -> dict[str, Any]: ...
    def get_job(self, job_id: str) -> dict[str, Any]: ...


class WindowsPackageCenterBridgeAdapter:
    """Thin Windows projection over the shared package-center services.

    All planning, archive, checksum, rights, import and activation rules remain
    in ``auto_research.product``.  This adapter only names the calls used by the
    protected loopback bridge.
    """

    def __init__(
        self,
        *,
        summary_provider: PackageCenterSummaryProvider,
        center: PackageCenter,
        export_service: PackageExportService,
        import_service: PackageTransferImportService,
        jobs: PackageJobService,
    ) -> None:
        self._summary_provider = summary_provider
        self._center = center
        self._export_service = export_service
        self._import_service = import_service
        self._jobs = jobs

    def status(self) -> dict[str, Any]:
        return self._summary_provider.summary()

    def inspect(self, selection_token: str) -> dict[str, Any]:
        return self._center.inspect(selection_token)

    def plan_export(self, kind: str, scope: str, selection: Any) -> dict[str, Any]:
        return self._export_service.plan(kind, scope, selection)

    def start_export(
        self,
        plan_token: str,
        rights_confirmations: Mapping[str, Any],
        destination_token: str,
    ) -> dict[str, Any]:
        return self._export_service.start(
            plan_token,
            rights_confirmations,
            destination_token,
        )

    def start_import(
        self,
        selection_token: str,
        *,
        checksum_ack: bool,
        expected_sha: str,
        keep_conflicts: bool,
    ) -> dict[str, Any]:
        return self._import_service.start(
            selection_token,
            checksum_ack=checksum_ack,
            expected_sha=expected_sha,
            keep_conflicts=keep_conflicts,
        )

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._jobs.get(job_id)

    @staticmethod
    def public_error(error: BaseException) -> tuple[dict[str, Any], HTTPStatus]:
        if not isinstance(error, PackageCenterError):
            error = PackageCenterError(
                "package_center_unavailable",
                "Windows 资料包中心运行时尚未安全接通。",
                retryable=False,
            )
        if error.code == "package_job_not_found":
            status = HTTPStatus.NOT_FOUND
        elif error.code in {
            "package_busy",
            "package_plan_stale",
            "package_destination_exists",
        }:
            status = HTTPStatus.CONFLICT
        elif error.code in {"package_size", "package_result_too_large"}:
            status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        elif error.code in {
            "package_center_unavailable",
            "package_inspect_failed",
            "package_export_failed",
            "transfer_import_failed",
            "package_catalog_unavailable",
        }:
            status = HTTPStatus.SERVICE_UNAVAILABLE
        else:
            status = HTTPStatus.BAD_REQUEST
        return error.public_dict(), status


class UnavailablePackageCenterBridge:
    """Fail closed until Windows receives safe v12/private resolver injection."""

    @staticmethod
    def _unavailable() -> None:
        raise PackageCenterError(
            "package_center_unavailable",
            "Windows 资料包中心运行时尚未安全接通。",
            retryable=False,
        )

    def status(self) -> dict[str, Any]:
        self._unavailable()

    def inspect(self, _selection_token: str) -> dict[str, Any]:
        self._unavailable()

    def plan_export(self, _kind: str, _scope: str, _selection: Any) -> dict[str, Any]:
        self._unavailable()

    def start_export(
        self,
        _plan_token: str,
        _rights_confirmations: Mapping[str, Any],
        _destination_token: str,
    ) -> dict[str, Any]:
        self._unavailable()

    def start_import(
        self,
        _selection_token: str,
        *,
        checksum_ack: bool,
        expected_sha: str,
        keep_conflicts: bool,
    ) -> dict[str, Any]:
        del checksum_ack, expected_sha, keep_conflicts
        self._unavailable()

    def get_job(self, _job_id: str) -> dict[str, Any]:
        self._unavailable()


__all__ = [
    "PackageCenterBridge",
    "UnavailablePackageCenterBridge",
    "WindowsPackageCenterBridgeAdapter",
]
