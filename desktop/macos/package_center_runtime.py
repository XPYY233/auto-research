from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Mapping

from auto_research.evidence.federated_search_session import (
    FederatedSearchSessionProtocol,
)
from auto_research.personal.private_repository import PrivateExperimentRepository
from auto_research.personal.transfer_merge import PersonalTransferMergeService
from auto_research.product.runtime_api import (
    EvidenceV12LiteraturePayloadSource,
    ExplicitLiteratureFilterResolver,
    LiteratureCollectionPayloadPlanner,
    PackageCenterError,
    PackageTransferActivationService,
    PersonalExperimentsPayloadPlanner,
    PrivateRepositoryPersonalPayloadSource,
    StructuredPackageCenterPayloadAdapter,
    TransferPackageKind,
    audit_transfer_payload_tree,
    export_transfer_package_with_checksum,
    import_transfer_package,
    list_installed_transfer_packages,
    read_personal_transfer_snapshot,
    run_package_job_in_background,
    verify_transfer_package,
)

from package_center_services import (
    DesktopPackageCenterServices,
    create_desktop_package_center_services,
)
from package_export_destination_broker import PackageExportDestinationBroker
from package_import_service import PackageImportService
from package_selection_broker import PackageSelectionBroker


class PackageCenterRuntimeError(RuntimeError):
    """Path-free startup failure for the desktop composition boundary."""


def _transfer_recovery_status(
    *,
    restored_count: int,
    failed_count: int = 0,
    failed: bool = False,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema": "package-center-recovery-v1",
        "state": "degraded" if failed else "ready",
        "restored_count": int(restored_count),
        "failed_count": int(failed_count),
        "retryable": bool(failed),
    }
    if failed:
        value.update(
            {
                "error": {
                    "code": "transfer_restore_failed",
                    "message": "部分用户文献资料包未能安全恢复；原有可用搜索保持不变。",
                    "retryable": True,
                },
                "retry_action": "reimport_user_package",
            }
        )
    return value


def create_workspace_snapshot(
    source_database: Path | str,
    *,
    snapshot_root: Path | str,
) -> Path:
    """Create one consistent SQLite backup of a potentially WAL-active v12 DB."""

    source = Path(source_database).expanduser().absolute()
    root = Path(snapshot_root).expanduser().absolute()
    try:
        source_info = source.lstat()
        if source.is_symlink() or not stat.S_ISREG(source_info.st_mode):
            raise OSError
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            raise OSError
        if os.name != "nt":
            os.chmod(root, 0o700)
    except OSError:
        raise PackageCenterRuntimeError("文献导出快照目录不可用。") from None

    destination = root / "workspace-v12-session.sqlite"
    temporary = root / f".workspace-v12-{uuid.uuid4().hex}.sqlite"
    if destination.is_symlink():
        raise PackageCenterRuntimeError("文献导出快照目录不安全。")
    source_connection: sqlite3.Connection | None = None
    target_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
        source_connection.execute("PRAGMA query_only=ON")
        target_connection = sqlite3.connect(temporary)
        source_connection.backup(target_connection)
        row = target_connection.execute("PRAGMA integrity_check").fetchone()
        if row is None or str(row[0]).casefold() != "ok":
            raise sqlite3.DatabaseError("snapshot integrity check failed")
        target_connection.commit()
        target_connection.close()
        target_connection = None
        source_connection.close()
        source_connection = None
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        descriptor = os.open(
            temporary,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, destination)
        directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return destination
    except (OSError, sqlite3.Error):
        raise PackageCenterRuntimeError("无法建立当前会话的文献导出快照。") from None
    finally:
        if target_connection is not None:
            target_connection.close()
        if source_connection is not None:
            source_connection.close()
        temporary.unlink(missing_ok=True)


class _NoPdfResolver:
    @staticmethod
    def resolve(_paper_uid: str, _paper: Mapping[str, Any]) -> None:
        return None


class NoAutomaticLiteratureLicenseVerifier:
    """Do not infer redistribution rights from unverified workspace metadata."""

    @staticmethod
    def verify(_paper_uid: str, _paper: Mapping[str, Any]) -> None:
        return None


class SnapshotRegisteredPdfResolver:
    """Resolve only PDF paths registered in the immutable session snapshot."""

    def __init__(self, snapshot: Path | str, *, workspace_root: Path | str) -> None:
        self._workspace_root = Path(workspace_root).expanduser().absolute()
        self._paths = self._read_registered_paths(Path(snapshot))

    def resolve(self, paper_uid: str, _paper: Mapping[str, Any]) -> Path | None:
        raw = self._paths.get(str(paper_uid))
        if not raw:
            return None
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self._workspace_root / path
        return path.absolute()

    @staticmethod
    def _read_registered_paths(snapshot: Path) -> dict[str, str]:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                f"{snapshot.absolute().as_uri()}?mode=ro&immutable=1", uri=True
            )
            rows = tuple(
                connection.execute(
                    "SELECT id,pdf_path FROM papers ORDER BY id"
                )
            )
        except sqlite3.Error:
            raise PackageCenterRuntimeError("文献 PDF 登记信息无法读取。") from None
        finally:
            if connection is not None:
                connection.close()
        probe = EvidenceV12LiteraturePayloadSource(
            snapshot,
            pdf_resolver=_NoPdfResolver(),
            license_verifier=NoAutomaticLiteratureLicenseVerifier(),
        )
        identifiers = tuple(str(int(row[0])) for row in rows)
        if not identifiers:
            return {}
        try:
            paper_uids = probe.resolve_selection_ids(identifiers)
        except Exception:
            raise PackageCenterRuntimeError("文献 PDF 稳定身份无法建立。") from None
        return {
            uid: str(row[1] or "").strip()
            for uid, row in zip(paper_uids, rows, strict=True)
        }


class RepositoryPersonalFileResolver:
    def __init__(self, repository: PrivateExperimentRepository) -> None:
        self._repository = repository

    def resolve(self, _source_id: str, source_file: Mapping[str, Any]) -> Path:
        file_id = str(source_file.get("file_id") or "")
        if not file_id:
            raise PackageCenterRuntimeError("私人实验原始表格身份无效。")
        return self._repository.private_path_for_file(file_id)


class _UnusedLiteraturePlanner:
    @staticmethod
    def plan(_selection):
        raise PackageCenterRuntimeError("文献导出必须使用当前计划快照。")


class FreshWorkspacePackagePlanner:
    """Refresh v12 through SQLite backup for every plan and stale check."""

    def __init__(
        self,
        *,
        workspace_database: Path | str,
        workspace_root: Path | str,
        personal_planner: PersonalExperimentsPayloadPlanner,
        snapshot_parent: Path | str,
        materialize_parent: Path | str,
    ) -> None:
        self._database = Path(workspace_database)
        self._workspace_root = Path(workspace_root)
        self._personal = personal_planner
        self._snapshot_parent = Path(snapshot_parent).expanduser().absolute()
        try:
            self._snapshot_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self._snapshot_parent.is_symlink() or not self._snapshot_parent.is_dir():
                raise OSError
            if os.name != "nt":
                os.chmod(self._snapshot_parent, 0o700)
        except OSError:
            raise PackageCenterRuntimeError("文献导出快照目录不安全。") from None
        self._materialize_parent = Path(materialize_parent).expanduser().absolute()
        self._materializer = StructuredPackageCenterPayloadAdapter(
            literature_planner=_UnusedLiteraturePlanner(),
            personal_planner=self._personal,
            workspace_parent=self._materialize_parent,
        )
        self._lock = threading.Lock()

    def plan(self, *, kind: str, scope: str, selection: Any):
        if str(kind) != "literature_collection":
            return self._materializer.plan(kind=kind, scope=scope, selection=selection)
        return self._with_fresh_literature(
            lambda adapter: adapter.plan(kind=kind, scope=scope, selection=selection)
        )

    def current_content_fingerprint(self, candidate):
        if int(getattr(candidate, "paper_count", 0)) <= 0:
            return self._materializer.current_content_fingerprint(candidate)
        return self._with_fresh_literature(
            lambda adapter: adapter.current_content_fingerprint(candidate)
        )

    def materialize(self, candidate, *, rights_confirmations):
        if int(getattr(candidate, "paper_count", 0)) > 0:
            current = self.current_content_fingerprint(candidate)
            if current != candidate.content_fingerprint:
                raise PackageCenterError(
                    "package_plan_stale",
                    "源数据已发生变化，请重新生成导出计划。",
                )
        return self._materializer.materialize(
            candidate,
            rights_confirmations=rights_confirmations,
        )

    def _with_fresh_literature(self, operation):
        with self._lock:
            try:
                operation_root = Path(
                    tempfile.mkdtemp(
                        prefix=".v12-plan-",
                        dir=self._snapshot_parent,
                    )
                )
            except OSError:
                raise PackageCenterRuntimeError(
                    "无法建立当前计划的文献导出快照。"
                ) from None
            try:
                snapshot = create_workspace_snapshot(
                    self._database,
                    snapshot_root=operation_root,
                )
                source = EvidenceV12LiteraturePayloadSource(
                    snapshot,
                    pdf_resolver=SnapshotRegisteredPdfResolver(
                        snapshot, workspace_root=self._workspace_root
                    ),
                    license_verifier=NoAutomaticLiteratureLicenseVerifier(),
                    filter_resolver=ExplicitLiteratureFilterResolver(),
                )
                adapter = StructuredPackageCenterPayloadAdapter(
                    literature_planner=LiteratureCollectionPayloadPlanner(source),
                    personal_planner=self._personal,
                    workspace_parent=self._materialize_parent,
                )
                return operation(adapter)
            finally:
                shutil.rmtree(operation_root, ignore_errors=True)


class DesktopPackageCenterRuntimeBuilder:
    """Production DI only; all transfer algorithms remain in shared core."""

    def __init__(
        self,
        *,
        workspace_database: Path | str,
        workspace_root: Path | str,
        private_repository: PrivateExperimentRepository,
        search_session: FederatedSearchSessionProtocol,
    ) -> None:
        self._workspace_database = Path(workspace_database)
        self._workspace_root = Path(workspace_root)
        self._private_repository = private_repository
        self._search_session = search_session

    def __call__(
        self,
        *,
        package_service: PackageImportService,
        package_broker: PackageSelectionBroker,
        destination_broker: PackageExportDestinationBroker,
        data_root: Path,
        current_app_version: str,
    ) -> DesktopPackageCenterServices:
        personal_source = PrivateRepositoryPersonalPayloadSource(
            self._private_repository,
            file_resolver=RepositoryPersonalFileResolver(self._private_repository),
        )
        planner = FreshWorkspacePackagePlanner(
            workspace_database=self._workspace_database,
            workspace_root=self._workspace_root,
            personal_planner=PersonalExperimentsPayloadPlanner(personal_source),
            snapshot_parent=data_root / "package-center-session",
            materialize_parent=data_root / "package-center-workspace",
        )
        personal_merger = PersonalTransferMergeService(
            self._private_repository,
            payload_auditor=audit_transfer_payload_tree,
            snapshot_reader=read_personal_transfer_snapshot,
        )
        activator = PackageTransferActivationService(
            search_session=self._search_session,
            personal_merger=personal_merger,
        )

        def inspect(source: Path):
            return verify_transfer_package(
                source,
                require_structured_payload=True,
            )

        def import_transfer(source: Path, **kwargs: Any):
            return import_transfer_package(
                source,
                destination_root=data_root,
                **kwargs,
            )

        restored = 0
        restore_failures = 0
        listing_failed = False
        try:
            installed_packages = list_installed_transfer_packages(
                data_root,
                kind=TransferPackageKind.LITERATURE_COLLECTION,
            )
        except Exception:
            # Installed user packages are low-trust input.  A malformed tree
            # must not prevent official/private sources or the rest of the App
            # from starting, and no un-audited package is registered.
            installed_packages = ()
            listing_failed = True
            restore_failures = 1
        for installed in installed_packages:
            try:
                # FederatedSearchSession upserts atomically: a failed source
                # build keeps every previously active source unchanged.
                activator.activate(installed, keep_conflicts=False)
            except Exception:
                restore_failures += 1
                continue
            restored += 1
        recovery = _transfer_recovery_status(
            restored_count=restored,
            failed_count=restore_failures,
            failed=listing_failed or restore_failures > 0,
        )

        return create_desktop_package_center_services(
            package_service=package_service,
            package_broker=package_broker,
            destination_broker=destination_broker,
            data_root=data_root,
            current_app_version=current_app_version,
            payload_planner=planner,
            transfer_inspector=inspect,
            transfer_exporter=export_transfer_package_with_checksum,
            transfer_importer=import_transfer,
            transfer_activator=activator,
            job_submitter=run_package_job_in_background,
            source_status={
                "literature": {
                    "mode": "per_plan_snapshot",
                    "refresh": "every_plan_and_export",
                },
                "personal": {
                    "mode": "current_private_repository",
                    "source_id": personal_source.source_id,
                },
                "restored_literature_packages": restored,
                "recovery": recovery,
            },
        )


__all__ = [
    "DesktopPackageCenterRuntimeBuilder",
    "FreshWorkspacePackagePlanner",
    "NoAutomaticLiteratureLicenseVerifier",
    "PackageCenterRuntimeError",
    "RepositoryPersonalFileResolver",
    "SnapshotRegisteredPdfResolver",
    "create_workspace_snapshot",
]
