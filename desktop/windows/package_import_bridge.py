from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from typing import Any

from package_import_progress import PackageImportProgressCoordinator, PackageImportProgressJob
from package_import_service import PackageImportService
from package_input import PackageInputHandle


Scheduler = Callable[[Callable[[], None]], None]


def _thread_scheduler(task: Callable[[], None]) -> None:
    threading.Thread(
        target=task,
        name="auto-research-windows-package-import",
        daemon=True,
    ).start()


class PackageBridgeError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def public_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


class PackageImportBridgeAdapter:
    """Path-free package status, single-use selection, and async job facade."""

    def __init__(
        self,
        service: PackageImportService,
        *,
        coordinator: PackageImportProgressCoordinator | None = None,
        scheduler: Scheduler = _thread_scheduler,
        job_id_factory: Callable[[], str] = lambda: secrets.token_urlsafe(24),
    ) -> None:
        self.service = service
        self.coordinator = coordinator or PackageImportProgressCoordinator()
        self.scheduler = scheduler
        self.job_id_factory = job_id_factory
        self._selections: dict[str, PackageInputHandle] = {}
        self._jobs: dict[str, PackageImportProgressJob] = {}
        self._lock = threading.RLock()

    def startup_readiness(self) -> dict[str, object]:
        return self.service.refresh_startup_readiness().public_dict()

    def status(self) -> dict[str, object]:
        readiness = self.service.readiness
        active = readiness.active_package if readiness.offline_ready else None
        value: dict[str, object] = {
            "active": active is not None,
            "repository_audited": active is not None,
            "can_search_offline": active is not None,
        }
        if active is not None:
            value.update(active)
        elif readiness.code not in {"official_package_required", "offline_ready"}:
            value["error"] = {
                "code": readiness.code,
                "message": readiness.message,
                "retryable": readiness.code in {"offline_search_unavailable", "active_state_invalid"},
            }
        return value

    def register_selection(self, handle: PackageInputHandle) -> dict[str, object]:
        if not isinstance(handle, PackageInputHandle):
            raise PackageBridgeError("package_selection_invalid", "资料包选择无效。")
        with self._lock:
            self._selections[handle.handle_id] = handle
        return {
            "selection_id": handle.handle_id,
            "source": "file_picker",
            "size_bytes": 0,
            "expires_in_seconds": 300,
        }

    def start_import(self, selection_id: str) -> dict[str, object]:
        if not isinstance(selection_id, str):
            raise PackageBridgeError("package_selection_invalid", "资料包选择请求无效。")
        with self._lock:
            handle = self._selections.pop(selection_id, None)
            if handle is None:
                raise PackageBridgeError("package_selection_invalid", "资料包选择已失效，请重新选择。")
            job_id = self.job_id_factory()
            if not isinstance(job_id, str) or len(job_id) < 16 or job_id in self._jobs:
                raise PackageBridgeError("package_job_transition_invalid", "资料包任务无法安全建立。")
            job = self.coordinator.new_job(handle)
            self._jobs[job_id] = job

        def execute() -> None:
            self.coordinator.run_job(job, self.service)

        try:
            self.scheduler(execute)
        except Exception:
            job.fail("package_import_failed")
        return self._job_public(job_id, job)

    def get_job(self, job_id: str) -> dict[str, object]:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise PackageBridgeError(
                "package_job_not_found",
                "未找到资料包任务。",
                retryable=False,
            )
        return self._job_public(job_id, job)

    def import_package(self, handle: PackageInputHandle) -> dict[str, object]:
        job = self.coordinator.run(handle, self.service)
        return {
            "job": job.snapshot().as_public_dict(),
            "readiness": self.service.readiness.public_dict(),
        }

    @staticmethod
    def _job_public(job_id: str, job: PackageImportProgressJob) -> dict[str, object]:
        snapshot = job.snapshot().as_public_dict()
        stage_progress = snapshot["stage_progress"]
        assert isinstance(stage_progress, dict)
        return {
            "job_id": job_id,
            "operation": "import",
            "stage": snapshot["stage"],
            "progress": round(float(stage_progress["fraction"]) * 100),
            "terminal": snapshot["stage"] in {"completed", "failed"},
            **({"error": snapshot["error"]} if snapshot["error"] is not None else {}),
        }
