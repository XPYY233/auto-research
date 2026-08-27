"""Platform-neutral orchestration for the Auto Research package center.

This module owns no filesystem paths, database handles, archive algorithms, or
desktop state.  Native shells resolve short-lived opaque tokens and injected
ports implement scientific payload planning and transfer package I/O.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .activity_receipts import ActivityReceiptRecorder
from .package_job_contract import (
    PackageJobError,
    PackageJobProgress,
    PackageJobStage,
    PackageOperation,
    advance_package_job,
    begin_package_job,
    fail_package_job,
)
from .package_job_history import (
    BestEffortPackageJobHistory,
    PackageJobHistoryRecorder,
)
from .package_center_models import (
    MAX_TRANSFER_BYTES,
    SHA256_RE,
    DestinationResolver,
    MaterializedPayload,
    PackageCenterError,
    PackageExportPlan,
    PackageKind,
    PackageScope,
    PayloadPlanCandidate,
    PayloadPlanner,
    RightsConfirmation,
    RightsRequirement,
    SelectionResolver,
    TransferExporter,
    TransferImporter,
    TransferActivator,
    TransferInspector,
    assert_path_free,
    normalize_kind,
    normalize_scope,
    normalize_selection,
    normalize_token,
    public_result,
    safe_port_error,
)


MAX_PLAN_CACHE = 128
MAX_JOB_CACHE = 128
JobSubmitter = Callable[[Callable[[], None]], None]


def run_package_job_inline(callback: Callable[[], None]) -> None:
    """Default deterministic executor used by core tests and maintenance tools."""

    callback()


def run_package_job_in_background(callback: Callable[[], None]) -> None:
    """Start one bounded daemon worker after PackageJobService grants single flight."""

    if not callable(callback):
        raise TypeError("package job callback must be callable")
    threading.Thread(
        target=callback,
        name="auto-research-package-job",
        daemon=True,
    ).start()


@dataclass
class _StoredJob:
    job_id: str
    progress: PackageJobProgress
    result: dict[str, Any] | None = None
    receipt_status: str | None = None

    def public_dict(self) -> dict[str, Any]:
        value = self.progress.public_dict()
        value["job_id"] = self.job_id
        if self.result is not None:
            value["result"] = self.result
        if self.receipt_status is not None:
            value["receipt_status"] = self.receipt_status
        assert_path_free(value)
        return value


class PackageJobService:
    """Small in-memory single-flight job registry shared by package actions."""

    def __init__(
        self,
        *,
        max_jobs: int = MAX_JOB_CACHE,
        receipt_recorder: ActivityReceiptRecorder | None = None,
        history_recorder: PackageJobHistoryRecorder | None = None,
    ) -> None:
        if not 1 <= int(max_jobs) <= 1_000:
            raise ValueError("max_jobs must be between 1 and 1000")
        self._max_jobs = int(max_jobs)
        self._lock = threading.RLock()
        self._active_job_id: str | None = None
        self._jobs: dict[str, _StoredJob] = {}
        self._order: list[str] = []
        self._receipt_recorder = receipt_recorder
        self._receipt_recording: set[str] = set()
        self._history = BestEffortPackageJobHistory(history_recorder)

    def get(self, job_id: str) -> dict[str, Any]:
        normalized = normalize_token(job_id, label="package_job")
        with self._lock:
            job = self._jobs.get(normalized)
            if job is None:
                raise PackageCenterError("package_job_not_found", "资料包任务不存在或已过期。")
            return job.public_dict()

    def _begin(self, operation: PackageOperation) -> str:
        with self._lock:
            if self._active_job_id is not None:
                raise PackageCenterError(
                    "package_busy", "已有资料包任务正在进行，请稍后再试。", retryable=True
                )
            self._trim_locked()
            if len(self._order) >= self._max_jobs:
                raise PackageCenterError(
                    "package_busy",
                    "完成回执正在保存，请稍后再开始新任务。",
                    retryable=True,
                )
            job_id = secrets.token_urlsafe(24)
            self._jobs[job_id] = _StoredJob(job_id, begin_package_job(operation))
            self._order.append(job_id)
            self._active_job_id = job_id
            public_job = self._jobs[job_id].public_dict()
        self._history.record(public_job)
        return job_id

    def _advance(self, job_id: str, stage: PackageJobStage) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.progress = advance_package_job(job.progress, stage)
            public_job = job.public_dict()
        self._history.record(public_job)

    def _complete(self, job_id: str, *, outcome: str, result: dict[str, Any]) -> None:
        receipt_operation: PackageOperation | None = None
        with self._lock:
            job = self._jobs[job_id]
            job.progress = advance_package_job(
                job.progress, PackageJobStage.COMPLETED, outcome=outcome
            )
            job.result = result
            if (
                self._receipt_recorder is not None
                and job.progress.operation
                in {PackageOperation.TRANSFER_EXPORT, PackageOperation.DATASET_EXPORT}
            ):
                job.receipt_status = "pending"
                receipt_operation = job.progress.operation
                self._receipt_recording.add(job_id)
            if self._active_job_id == job_id:
                self._active_job_id = None
            completed_job = job.public_dict()
        self._history.record(completed_job)
        if receipt_operation is None:
            return
        stored = False
        try:
            self._receipt_recorder.record_completed(
                operation=receipt_operation,
                outcome=outcome,
                result=dict(result),
            )
        except Exception:
            # The artifact is already published.  Receipt persistence is a
            # separate convenience boundary and must never change the export
            # outcome or trigger another exporter call.
            pass
        else:
            stored = True
        finally:
            stored_job: dict[str, Any] | None = None
            with self._lock:
                self._receipt_recording.discard(job_id)
                job = self._jobs.get(job_id)
                if (
                    stored
                    and job is not None
                    and job.progress.stage is PackageJobStage.COMPLETED
                    and job.receipt_status == "pending"
                ):
                    job.receipt_status = "stored"
                    stored_job = job.public_dict()
            if stored_job is not None:
                self._history.mark_receipt_stored(stored_job)

    def retry_receipt(self, job_id: str) -> dict[str, Any]:
        """Retry only the durable receipt for one already-completed export."""

        normalized = normalize_token(job_id, label="package_job")
        already_stored: dict[str, Any] | None = None
        with self._lock:
            job = self._jobs.get(normalized)
            if job is None:
                raise PackageCenterError(
                    "package_job_not_found", "资料包任务不存在或已过期。"
                )
            if (
                job.progress.stage is not PackageJobStage.COMPLETED
                or job.progress.operation
                not in {
                    PackageOperation.TRANSFER_EXPORT,
                    PackageOperation.DATASET_EXPORT,
                }
                or job.receipt_status not in {"pending", "stored"}
            ):
                raise PackageCenterError(
                    "package_receipt_not_recoverable",
                    "该任务没有可恢复的完成回执。",
                )
            if job.receipt_status == "stored":
                already_stored = job.public_dict()
            if already_stored is not None:
                operation = None
                outcome = None
                result = None
            elif normalized in self._receipt_recording:
                raise PackageCenterError(
                    "package_receipt_recovery_busy",
                    "该完成回执正在恢复，请稍后查看。",
                    retryable=True,
                )
            elif (
                self._receipt_recorder is None
                or job.result is None
                or not isinstance(job.progress.outcome, str)
                or not job.progress.outcome
            ):
                raise PackageCenterError(
                    "package_receipt_not_recoverable",
                    "该任务没有可恢复的完成回执。",
                )
            else:
                operation = job.progress.operation
                outcome = job.progress.outcome
                result = dict(job.result)
                self._receipt_recording.add(normalized)
                pending_job = job.public_dict()

        if already_stored is not None:
            self._history.mark_receipt_stored(already_stored)
            return already_stored
        self._history.record(pending_job)

        try:
            self._receipt_recorder.record_completed(
                operation=operation,
                outcome=outcome,
                result=result,
            )
        except Exception as exc:
            with self._lock:
                self._receipt_recording.discard(normalized)
            raise PackageCenterError(
                "package_receipt_store_unavailable",
                "完成回执暂时无法恢复，请稍后重试。",
                retryable=True,
            ) from exc

        with self._lock:
            job = self._jobs.get(normalized)
            if job is None:
                self._receipt_recording.discard(normalized)
                raise PackageCenterError(
                    "package_job_not_found", "资料包任务不存在或已过期。"
                )
            if (
                job.progress.stage is not PackageJobStage.COMPLETED
                or job.receipt_status != "pending"
            ):
                self._receipt_recording.discard(normalized)
                raise PackageCenterError(
                    "package_receipt_not_recoverable",
                    "该任务没有可恢复的完成回执。",
                )
            job.receipt_status = "stored"
            self._receipt_recording.discard(normalized)
            stored_job = job.public_dict()
        self._history.mark_receipt_stored(stored_job)
        return stored_job

    def _fail(self, job_id: str, error: PackageCenterError) -> None:
        with self._lock:
            job = self._jobs[job_id]
            failure = PackageJobError(
                code=error.code,
                safe_message=error.safe_message,
                stage=job.progress.stage,
                retryable=error.retryable,
            )
            job.progress = fail_package_job(job.progress, failure)
            if self._active_job_id == job_id:
                self._active_job_id = None
            public_job = job.public_dict()
        self._history.record(public_job)

    def _trim_locked(self) -> None:
        while len(self._order) >= self._max_jobs:
            removable = next(
                (
                    candidate
                    for candidate in self._order
                    if candidate != self._active_job_id
                    and candidate not in self._receipt_recording
                ),
                None,
            )
            if removable is None:
                break
            self._order.remove(removable)
            self._jobs.pop(removable, None)


class PackageCenter:
    def __init__(
        self,
        *,
        selection_resolver: SelectionResolver,
        inspector: TransferInspector,
    ) -> None:
        self._selection_resolver = selection_resolver
        self._inspector = inspector

    def inspect(self, selection_token: str) -> dict[str, Any]:
        token = normalize_token(selection_token, label="package_selection")
        try:
            source = self._selection_resolver.resolve(token)
            summary = public_result(self._inspector(source))
        except Exception as exc:
            raise safe_port_error(
                exc,
                fallback_code="package_inspect_failed",
                fallback_message="无法安全检查该资料包。",
            ) from None
        summary = dict(summary)
        summary["checksum_ack_required"] = True
        return summary


class PackageExportService:
    def __init__(
        self,
        *,
        payload_planner: PayloadPlanner,
        destination_resolver: DestinationResolver,
        exporter: TransferExporter,
        jobs: PackageJobService,
        plan_ttl_seconds: int = 600,
        clock: Callable[[], float] = time.monotonic,
        job_submitter: JobSubmitter = run_package_job_inline,
    ) -> None:
        if not 30 <= int(plan_ttl_seconds) <= 3600:
            raise ValueError("plan_ttl_seconds must be between 30 and 3600")
        self._planner = payload_planner
        self._destination_resolver = destination_resolver
        self._exporter = exporter
        self._jobs = jobs
        self._ttl = int(plan_ttl_seconds)
        self._clock = clock
        if not callable(job_submitter):
            raise TypeError("job_submitter must be callable")
        self._job_submitter = job_submitter
        self._lock = threading.RLock()
        self._plans: dict[str, PackageExportPlan] = {}
        self._consumed_destination_tokens: set[str] = set()

    def plan(self, kind: str, scope: str, selection: Any) -> dict[str, Any]:
        normalized_kind = normalize_kind(kind)
        normalized_scope = normalize_scope(scope)
        normalized_selection, selection_fingerprint, selected_count = normalize_selection(
            normalized_scope, selection
        )
        try:
            candidate = self._planner.plan(
                kind=normalized_kind.value,
                scope=normalized_scope.value,
                selection=normalized_selection,
            )
        except Exception as exc:
            raise safe_port_error(
                exc,
                fallback_code="package_plan_failed",
                fallback_message="无法生成资料包计划。",
            ) from None
        self._validate_candidate(candidate, normalized_kind)
        now = self._clock()
        token = secrets.token_urlsafe(24)
        plan = PackageExportPlan(
            token=token,
            kind=normalized_kind,
            scope=normalized_scope,
            selection_fingerprint=selection_fingerprint,
            candidate=candidate,
            created_at=now,
            expires_at=now + self._ttl,
            selected_count=selected_count,
        )
        with self._lock:
            self._prune_plans_locked(now)
            if len(self._plans) >= MAX_PLAN_CACHE:
                oldest = min(self._plans.values(), key=lambda value: value.created_at)
                self._plans.pop(oldest.token, None)
            self._plans[token] = plan
        return plan.public_dict(now=now)

    def start(
        self,
        plan_token: str,
        rights_confirmations: Mapping[str, Any],
        destination_token: str,
    ) -> dict[str, Any]:
        token = normalize_token(plan_token, label="package_plan")
        destination = normalize_token(destination_token, label="package_destination")
        plan = self._get_plan(token)
        if plan.candidate.exceeds_size_limit:
            raise PackageCenterError(
                "transfer_size", "预计传输内容超过 2 GB 上限，请减少选择。"
            )
        confirmations = self._normalize_confirmations(plan, rights_confirmations)
        job_id = self._jobs._begin(PackageOperation.TRANSFER_EXPORT)
        try:
            self._job_submitter(
                lambda: self._run_export_job(
                    job_id,
                    plan=plan,
                    confirmations=confirmations,
                    destination=destination,
                )
            )
        except Exception as exc:
            self._jobs._fail(
                job_id,
                safe_port_error(
                    exc,
                    fallback_code="package_job_start_failed",
                    fallback_message="资料包任务无法启动。",
                ),
            )
        return self._jobs.get(job_id)

    def _run_export_job(
        self,
        job_id: str,
        *,
        plan: PackageExportPlan,
        confirmations: Mapping[str, RightsConfirmation],
        destination: str,
    ) -> None:
        materialized: MaterializedPayload | None = None
        try:
            self._jobs._advance(job_id, PackageJobStage.PLAN)
            current_fingerprint = self._planner.current_content_fingerprint(plan.candidate)
            if current_fingerprint != plan.candidate.content_fingerprint:
                raise PackageCenterError(
                    "package_plan_stale", "源数据已发生变化，请重新生成导出计划。"
                )
            self._jobs._advance(job_id, PackageJobStage.SNAPSHOT_SOURCE)
            self._jobs._advance(job_id, PackageJobStage.RIGHTS_AUDIT)
            materialized = self._planner.materialize(
                plan.candidate,
                rights_confirmations=confirmations,
            )
            if not isinstance(materialized, MaterializedPayload):
                raise PackageCenterError(
                    "package_materialization_invalid", "资料包内容暂存结果无效。"
                )
            self._jobs._advance(job_id, PackageJobStage.BUILD_ARCHIVE)
            with self._lock:
                if destination in self._consumed_destination_tokens:
                    raise PackageCenterError(
                        "package_destination_expired",
                        "导出位置选择已使用，请重新选择保存位置。",
                    )
                self._consumed_destination_tokens.add(destination)
            destination_value = self._destination_resolver.resolve(destination)
            exported = self._exporter(
                materialized.export_value,
                destination_value,
                unencrypted_ack=True,
            )
            self._jobs._advance(job_id, PackageJobStage.VERIFY_CHECKSUMS)
            result = public_result(exported)
            self._jobs._advance(job_id, PackageJobStage.PUBLISH)
            self._jobs._complete(
                job_id,
                outcome=str(result.get("outcome") or "exported"),
                result=result,
            )
        except Exception as exc:
            error = safe_port_error(
                exc,
                fallback_code="package_export_failed",
                fallback_message="资料包导出失败。",
            )
            self._jobs._fail(job_id, error)
        finally:
            if materialized is not None:
                try:
                    materialized.close()
                except Exception:
                    pass

    def _get_plan(self, token: str) -> PackageExportPlan:
        now = self._clock()
        with self._lock:
            self._prune_plans_locked(now)
            plan = self._plans.get(token)
        if plan is None:
            raise PackageCenterError(
                "package_plan_expired", "导出计划不存在或已过期，请重新生成。"
            )
        return plan

    def _prune_plans_locked(self, now: float) -> None:
        expired = [token for token, plan in self._plans.items() if plan.expires_at <= now]
        for token in expired:
            self._plans.pop(token, None)

    @staticmethod
    def _validate_candidate(candidate: Any, kind: PackageKind) -> None:
        if not isinstance(candidate, PayloadPlanCandidate):
            raise PackageCenterError("package_plan_invalid", "资料包计划格式无效。")
        if (
            not candidate.package_id
            or not candidate.package_version
            or not SHA256_RE.fullmatch(candidate.content_fingerprint)
            or candidate.estimated_bytes < 0
            or candidate.estimated_bytes > (1 << 50)
            or candidate.item_count < 0
            or candidate.paper_count < 0
            or candidate.missing_pdf_count < 0
        ):
            raise PackageCenterError("package_plan_invalid", "资料包计划内容无效。")
        if candidate.exceeds_size_limit != (
            candidate.estimated_bytes > MAX_TRANSFER_BYTES
        ):
            raise PackageCenterError("package_plan_invalid", "资料包大小判断不一致。")
        if kind is PackageKind.PERSONAL_EXPERIMENTS and candidate.rights_requirements:
            raise PackageCenterError(
                "package_plan_invalid", "私人实验包不能包含论文 PDF 权利确认。"
            )
        ids = [requirement.paper_uid for requirement in candidate.rights_requirements]
        if (
            len(ids) != len(set(ids))
            or any(not item or len(item) > 128 for item in ids)
            or any(
                not isinstance(requirement, RightsRequirement)
                or not requirement.title.strip()
                or len(requirement.title) > 500
                or len(requirement.reason) > 500
                for requirement in candidate.rights_requirements
            )
        ):
            raise PackageCenterError("package_plan_invalid", "PDF 权利确认项无效。")

    @staticmethod
    def _normalize_confirmations(
        plan: PackageExportPlan, raw: Mapping[str, Any]
    ) -> dict[str, RightsConfirmation]:
        if not isinstance(raw, Mapping):
            raise PackageCenterError(
                "package_risk_ack_required", "请确认资料包传递风险。"
            )
        required_fields = {
            "unencrypted_ack",
            "unauthenticated_source_ack",
            "internal_use_only_ack",
            "paper_rights",
        }
        if set(raw) != required_fields or any(
            raw.get(field) is not True
            for field in (
                "unencrypted_ack",
                "unauthenticated_source_ack",
                "internal_use_only_ack",
            )
        ):
            raise PackageCenterError(
                "package_risk_ack_required",
                "请确认资料包未加密、来源未认证且仅限组内使用。",
            )
        rights = raw.get("paper_rights")
        if not isinstance(rights, Mapping):
            raise PackageCenterError(
                "package_rights_confirmation_invalid", "PDF 权利确认格式无效。"
            )
        required_ids = {
            requirement.paper_uid for requirement in plan.candidate.rights_requirements
        }
        if set(map(str, rights)) != required_ids:
            raise PackageCenterError(
                "package_pdf_rights_required", "每篇受限 PDF 都必须单独确认分享权限。"
            )
        normalized: dict[str, RightsConfirmation] = {}
        for paper_uid, value in rights.items():
            if not isinstance(value, Mapping) or set(value) != {"allowed", "basis"}:
                raise PackageCenterError(
                    "package_rights_confirmation_invalid", "PDF 权利确认格式无效。"
                )
            basis = str(value.get("basis") or "").strip()
            if value.get("allowed") is not True or not basis or len(basis) > 500:
                raise PackageCenterError(
                    "package_pdf_rights_required", "每篇受限 PDF 都必须说明组内分享依据。"
                )
            normalized[str(paper_uid)] = RightsConfirmation(True, basis)
        return normalized


class PackageTransferImportService:
    def __init__(
        self,
        *,
        selection_resolver: SelectionResolver,
        inspector: TransferInspector,
        importer: TransferImporter,
        activator: TransferActivator,
        jobs: PackageJobService,
        job_submitter: JobSubmitter = run_package_job_inline,
    ) -> None:
        self._selection_resolver = selection_resolver
        self._inspector = inspector
        self._importer = importer
        self._activator = activator
        self._jobs = jobs
        if not callable(job_submitter):
            raise TypeError("job_submitter must be callable")
        self._job_submitter = job_submitter
        self._lock = threading.RLock()
        self._consumed_selection_tokens: set[str] = set()

    def start(
        self,
        selection_token: str,
        *,
        checksum_ack: bool,
        expected_sha: str,
        keep_conflicts: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(keep_conflicts, bool):
            raise PackageCenterError(
                "package_conflict_policy_invalid",
                "冲突处理选项无效。",
            )
        token = normalize_token(selection_token, label="package_selection")
        if checksum_ack is not True:
            raise PackageCenterError(
                "transfer_checksum_ack_required",
                "请先通过其他渠道与发送者核对 SHA-256。",
            )
        expected = str(expected_sha or "").casefold()
        if not SHA256_RE.fullmatch(expected):
            raise PackageCenterError(
                "transfer_expected_checksum_invalid", "请输入完整的 64 位 SHA-256。"
            )
        job_id = self._jobs._begin(PackageOperation.TRANSFER_IMPORT)
        try:
            self._job_submitter(
                lambda: self._run_import_job(
                    job_id,
                    token=token,
                    expected=expected,
                    keep_conflicts=keep_conflicts,
                )
            )
        except Exception as exc:
            self._jobs._fail(
                job_id,
                safe_port_error(
                    exc,
                    fallback_code="package_job_start_failed",
                    fallback_message="资料包任务无法启动。",
                ),
            )
        return self._jobs.get(job_id)

    def _run_import_job(
        self,
        job_id: str,
        *,
        token: str,
        expected: str,
        keep_conflicts: bool,
    ) -> None:
        try:
            with self._lock:
                if token in self._consumed_selection_tokens:
                    raise PackageCenterError(
                        "package_selection_expired",
                        "资料包选择已使用，请重新选择文件。",
                    )
                self._consumed_selection_tokens.add(token)
            source = self._selection_resolver.resolve(token)
            self._jobs._advance(job_id, PackageJobStage.SNAPSHOT_SOURCE)
            self._jobs._advance(job_id, PackageJobStage.VERIFY_ARCHIVE)
            inspected = public_result(self._inspector(source))
            try:
                inspected_kind = PackageKind(str(inspected.get("package_kind") or ""))
            except ValueError as exc:
                raise PackageCenterError(
                    "transfer_kind_invalid", "该文件不是可导入的用户资料包。"
                ) from exc
            package_sha = str(inspected.get("package_sha256") or "").casefold()
            if package_sha != expected:
                raise PackageCenterError(
                    "transfer_package_checksum_mismatch",
                    "资料包与发送方提供的 SHA-256 不一致。",
                )
            self._jobs._advance(job_id, PackageJobStage.VERIFY_CHECKSUMS)
            self._jobs._advance(job_id, PackageJobStage.RIGHTS_AUDIT)
            self._jobs._advance(job_id, PackageJobStage.EXTRACT_STAGING)
            imported = self._importer(
                source,
                expected_kind=inspected_kind.value,
                expected_package_sha256=expected,
                checksum_ack=True,
                require_structured_payload=True,
            )
            self._jobs._advance(job_id, PackageJobStage.AUDIT_PAYLOAD)
            self._jobs._advance(job_id, PackageJobStage.ACTIVATE)
            activated = self._activator.activate(
                imported,
                keep_conflicts=keep_conflicts,
            )
            result = public_result(activated)
            self._jobs._complete(
                job_id,
                outcome=str(result.get("outcome") or "imported"),
                result=result,
            )
        except Exception as exc:
            error = safe_port_error(
                exc,
                fallback_code="transfer_import_failed",
                fallback_message="用户资料包导入失败。",
            )
            self._jobs._fail(job_id, error)


__all__ = [
    "DestinationResolver",
    "PackageCenter",
    "PackageCenterError",
    "PackageExportPlan",
    "PackageExportService",
    "PackageJobService",
    "PackageKind",
    "PackageScope",
    "PackageTransferImportService",
    "PayloadPlanCandidate",
    "PayloadPlanner",
    "RightsConfirmation",
    "RightsRequirement",
    "JobSubmitter",
    "SelectionResolver",
    "TransferExporter",
    "TransferImporter",
    "TransferInspector",
    "run_package_job_in_background",
    "run_package_job_inline",
]
