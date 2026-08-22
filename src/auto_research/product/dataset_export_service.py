"""Platform-neutral orchestration for deterministic dataset bundle exports."""

from __future__ import annotations

from dataclasses import dataclass
import secrets
import threading
import time
from typing import Any, Callable, Protocol

from .dataset_bundle import DatasetBundlePlan
from .package_center import PackageJobService, JobSubmitter, run_package_job_inline
from .package_center_models import (
    PackageCenterError,
    assert_path_free,
    normalize_token,
    safe_port_error,
)
from .package_job_contract import PackageJobStage, PackageOperation


DATASET_EXPORT_PLAN_SCHEMA = "dataset-export-plan-v1"
MAX_DATASET_PLAN_CACHE = 32


@dataclass(frozen=True)
class DatasetExportCandidate:
    source_fingerprint: str
    bundle_plan: DatasetBundlePlan


class DatasetExportSource(Protocol):
    def plan(self, *, include_private: bool) -> DatasetExportCandidate: ...

    def current_source_fingerprint(self, candidate: DatasetExportCandidate) -> str: ...


class DatasetDestinationResolver(Protocol):
    def resolve(self, destination_token: str) -> Any: ...


class DatasetPublisher(Protocol):
    def __call__(
        self,
        plan: DatasetBundlePlan,
        destination: Any,
        *,
        rights_acknowledged: bool,
        unreviewed_acknowledged: bool,
    ) -> Any: ...


@dataclass(frozen=True)
class _StoredDatasetPlan:
    token: str
    candidate: DatasetExportCandidate
    created_at: float
    expires_at: float


class DatasetExportService:
    """Keep dataset planning, stale detection and jobs identical on every desktop."""

    def __init__(
        self,
        *,
        source: DatasetExportSource,
        destination_resolver: DatasetDestinationResolver,
        publisher: DatasetPublisher,
        jobs: PackageJobService,
        plan_ttl_seconds: int = 600,
        clock: Callable[[], float] = time.monotonic,
        job_submitter: JobSubmitter = run_package_job_inline,
    ) -> None:
        if not 30 <= int(plan_ttl_seconds) <= 3600:
            raise ValueError("plan_ttl_seconds must be between 30 and 3600")
        self._source = source
        self._destination_resolver = destination_resolver
        self._publisher = publisher
        self._jobs = jobs
        self._ttl = int(plan_ttl_seconds)
        self._clock = clock
        self._job_submitter = job_submitter
        self._plans: dict[str, _StoredDatasetPlan] = {}
        self._lock = threading.RLock()

    def plan(self, *, include_private: bool) -> dict[str, Any]:
        if not isinstance(include_private, bool):
            raise PackageCenterError("dataset_request_invalid", "数据集导出范围无效。")
        try:
            candidate = self._source.plan(include_private=include_private)
        except Exception as exc:
            raise safe_port_error(
                exc,
                fallback_code="dataset_plan_failed",
                fallback_message="无法生成数据集导出计划。",
            ) from None
        if not isinstance(candidate, DatasetExportCandidate):
            raise PackageCenterError("dataset_plan_invalid", "数据集导出计划无效。")
        fingerprint = str(candidate.source_fingerprint)
        if len(fingerprint) != 64 or any(value not in "0123456789abcdef" for value in fingerprint):
            raise PackageCenterError("dataset_plan_invalid", "数据集来源指纹无效。")
        now = self._clock()
        token = secrets.token_urlsafe(24)
        stored = _StoredDatasetPlan(token, candidate, now, now + self._ttl)
        with self._lock:
            self._prune_locked(now)
            while len(self._plans) >= MAX_DATASET_PLAN_CACHE:
                oldest = min(self._plans.values(), key=lambda value: value.created_at)
                self._plans.pop(oldest.token, None)
            self._plans[token] = stored
        result = {
            "plan_token": token,
            "expires_in_seconds": self._ttl,
            **candidate.bundle_plan.public_dict(),
            "schema_version": DATASET_EXPORT_PLAN_SCHEMA,
            "rights_ack_required": bool(candidate.bundle_plan.rights_risks),
            "unreviewed_ack_required": bool(candidate.bundle_plan.unreviewed_count),
        }
        assert_path_free(result)
        return result

    def start(
        self,
        plan_token: str,
        destination_token: str,
        *,
        rights_acknowledged: bool,
        unreviewed_acknowledged: bool,
    ) -> dict[str, Any]:
        token = normalize_token(plan_token, label="dataset_plan")
        destination = normalize_token(destination_token, label="dataset_destination")
        if not isinstance(rights_acknowledged, bool) or not isinstance(unreviewed_acknowledged, bool):
            raise PackageCenterError("dataset_request_invalid", "数据集风险确认无效。")
        stored = self._get_plan(token)
        job_id = self._jobs._begin(PackageOperation.DATASET_EXPORT)
        try:
            self._job_submitter(
                lambda: self._run_job(
                    job_id,
                    stored=stored,
                    destination_token=destination,
                    rights_acknowledged=rights_acknowledged,
                    unreviewed_acknowledged=unreviewed_acknowledged,
                )
            )
        except Exception as exc:
            self._jobs._fail(
                job_id,
                safe_port_error(
                    exc,
                    fallback_code="dataset_job_start_failed",
                    fallback_message="数据集导出任务无法启动。",
                ),
            )
        return self._jobs.get(job_id)

    def _run_job(
        self,
        job_id: str,
        *,
        stored: _StoredDatasetPlan,
        destination_token: str,
        rights_acknowledged: bool,
        unreviewed_acknowledged: bool,
    ) -> None:
        try:
            self._jobs._advance(job_id, PackageJobStage.PLAN)
            current = self._source.current_source_fingerprint(stored.candidate)
            if current != stored.candidate.source_fingerprint:
                raise PackageCenterError("dataset_plan_stale", "源数据已变化，请重新生成数据集计划。")
            self._jobs._advance(job_id, PackageJobStage.SNAPSHOT_SOURCE)
            self._jobs._advance(job_id, PackageJobStage.RIGHTS_AUDIT)
            if stored.candidate.bundle_plan.rights_risks and not rights_acknowledged:
                raise PackageCenterError("dataset_rights_unconfirmed", "数据权利风险尚未确认。")
            if stored.candidate.bundle_plan.unreviewed_count and not unreviewed_acknowledged:
                raise PackageCenterError("dataset_unreviewed_unconfirmed", "未审核记录风险尚未确认。")
            self._jobs._advance(job_id, PackageJobStage.BUILD_ARCHIVE)
            resolved = self._destination_resolver.resolve(destination_token)
            raw_published = self._publisher(
                stored.candidate.bundle_plan,
                resolved,
                rights_acknowledged=rights_acknowledged,
                unreviewed_acknowledged=unreviewed_acknowledged,
            )
            if not isinstance(raw_published, dict):
                raise PackageCenterError("dataset_result_invalid", "数据集导出结果无效。")
            published = dict(raw_published)
            assert_path_free(published)
            self._jobs._advance(job_id, PackageJobStage.VERIFY_CHECKSUMS)
            self._jobs._advance(job_id, PackageJobStage.PUBLISH)
            self._jobs._complete(job_id, outcome="exported", result=published)
        except Exception as exc:
            self._jobs._fail(
                job_id,
                safe_port_error(
                    exc,
                    fallback_code="dataset_export_failed",
                    fallback_message="数据集未导出，现有数据不受影响。",
                ),
            )

    def _get_plan(self, token: str) -> _StoredDatasetPlan:
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            stored = self._plans.get(token)
        if stored is None:
            raise PackageCenterError("dataset_plan_expired", "数据集计划不存在或已过期。")
        return stored

    def _prune_locked(self, now: float) -> None:
        for token in [key for key, value in self._plans.items() if value.expires_at <= now]:
            self._plans.pop(token, None)


__all__ = [
    "DATASET_EXPORT_PLAN_SCHEMA",
    "DatasetDestinationResolver",
    "DatasetExportCandidate",
    "DatasetExportService",
    "DatasetExportSource",
    "DatasetPublisher",
]
