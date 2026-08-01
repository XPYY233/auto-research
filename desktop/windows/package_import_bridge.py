from __future__ import annotations

from package_import_progress import PackageImportProgressCoordinator
from package_import_service import PackageImportService
from package_input import PackageInputHandle


class PackageImportBridgeAdapter:
    """Path-free native bridge surface for startup and package import."""

    def __init__(
        self,
        service: PackageImportService,
        *,
        coordinator: PackageImportProgressCoordinator | None = None,
    ) -> None:
        self.service = service
        self.coordinator = coordinator or PackageImportProgressCoordinator()

    def startup_readiness(self) -> dict[str, object]:
        return self.service.refresh_startup_readiness().public_dict()

    def import_package(self, handle: PackageInputHandle) -> dict[str, object]:
        job = self.coordinator.run(handle, self.service)
        return {
            "job": job.snapshot().as_public_dict(),
            "readiness": self.service.readiness.public_dict(),
        }
