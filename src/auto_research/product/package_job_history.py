"""Best-effort bridge from transient package jobs to durable operation history.

The bridge deliberately keeps package job identifiers only in process memory.
The injected recorder is responsible for deriving and persisting its own stable,
path-free operation identity from the trusted ``package-job-v1`` projection.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Mapping, Protocol


_OPERATION_UID = re.compile(r"^[0-9a-f]{64}$")


class PackageJobHistoryRecorder(Protocol):
    """Narrow backend authority consumed by ``PackageJobService``."""

    def record(self, job: object) -> tuple[str, int]: ...

    def mark_receipt_stored(
        self,
        operation_uid: str,
        *,
        expected_revision: int,
    ) -> object: ...


@dataclass(frozen=True)
class _HistoryBinding:
    operation_uid: str
    revision: int


class BestEffortPackageJobHistory:
    """Observe package jobs without ever changing their business outcome."""

    def __init__(self, recorder: PackageJobHistoryRecorder | None) -> None:
        self._recorder = recorder
        self._lock = threading.RLock()
        self._bindings: dict[str, _HistoryBinding] = {}

    def record(self, job: Mapping[str, object]) -> None:
        recorder = self._recorder
        job_id = job.get("job_id")
        if recorder is None or not isinstance(job_id, str):
            return
        try:
            operation_uid, revision = recorder.record(dict(job))
        except Exception:
            return
        binding = _binding(operation_uid, revision)
        if binding is None:
            return
        with self._lock:
            self._bindings[job_id] = binding

    def mark_receipt_stored(self, job: Mapping[str, object]) -> None:
        recorder = self._recorder
        job_id = job.get("job_id")
        if recorder is None or not isinstance(job_id, str):
            return
        with self._lock:
            binding = self._bindings.get(job_id)
        if binding is None or not self._mark(recorder, binding):
            binding = self._refresh_pending_binding(recorder, job, binding)
            if binding is None or not self._mark(recorder, binding):
                return
        with self._lock:
            self._bindings[job_id] = binding

    @staticmethod
    def _mark(
        recorder: PackageJobHistoryRecorder,
        binding: _HistoryBinding,
    ) -> bool:
        try:
            recorder.mark_receipt_stored(
                binding.operation_uid,
                expected_revision=binding.revision,
            )
        except Exception:
            return False
        return True

    @staticmethod
    def _refresh_pending_binding(
        recorder: PackageJobHistoryRecorder,
        job: Mapping[str, object],
        previous: _HistoryBinding | None,
    ) -> _HistoryBinding | None:
        if job.get("stage") != "completed" or job.get("receipt_status") != "stored":
            return None
        pending = dict(job)
        pending["receipt_status"] = "pending"
        try:
            operation_uid, revision = recorder.record(pending)
        except Exception:
            return None
        refreshed = _binding(operation_uid, revision)
        if (
            refreshed is None
            or previous is not None
            and refreshed.operation_uid != previous.operation_uid
        ):
            return None
        return refreshed


def _binding(operation_uid: object, revision: object) -> _HistoryBinding | None:
    if (
        not isinstance(operation_uid, str)
        or _OPERATION_UID.fullmatch(operation_uid) is None
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
    ):
        return None
    return _HistoryBinding(operation_uid, revision)


__all__ = ["BestEffortPackageJobHistory", "PackageJobHistoryRecorder"]
