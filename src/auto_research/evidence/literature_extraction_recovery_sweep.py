from __future__ import annotations

from collections import Counter
from typing import Mapping, Protocol

from .literature_extraction_recovery import (
    LiteratureExtractionRecoveryError,
)
from .literature_task_checkpoint import LiteratureTaskCheckpointError


RECOVERY_SWEEP_SCHEMA_VERSION = "literature-extraction-recovery-sweep-v1"


class CheckpointTaskLister(Protocol):
    def list_task_ids(self, *, limit: int = 64) -> tuple[str, ...]: ...


class RecoverableLiteratureTask(Protocol):
    def recover_task(self, task_id: str) -> Mapping[str, object]: ...


class LiteratureExtractionRecoverySweep:
    """Bounded startup recovery without exposing task or job identities."""

    def __init__(
        self,
        *,
        checkpoints: CheckpointTaskLister,
        recovery: RecoverableLiteratureTask,
    ) -> None:
        if not callable(getattr(checkpoints, "list_task_ids", None)) or not callable(
            getattr(recovery, "recover_task", None)
        ):
            raise ValueError("literature recovery sweep composition is invalid")
        self._checkpoints = checkpoints
        self._recovery = recovery

    def run(self, *, limit: int = 16) -> Mapping[str, object]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 64:
            raise ValueError("literature recovery sweep limit is invalid")
        try:
            task_ids = self._checkpoints.list_task_ids(limit=limit)
        except LiteratureTaskCheckpointError as exc:
            return self._report(scanned=0, recovered=0, already_completed=0, errors={exc.code: 1})
        recovered = 0
        already_completed = 0
        errors: Counter[str] = Counter()
        for task_id in task_ids:
            try:
                result = self._recovery.recover_task(task_id)
                if result.get("already_completed") is True:
                    already_completed += 1
                else:
                    recovered += 1
            except LiteratureExtractionRecoveryError as exc:
                errors[exc.code] += 1
        return self._report(
            scanned=len(task_ids),
            recovered=recovered,
            already_completed=already_completed,
            errors=errors,
        )

    @staticmethod
    def _report(
        *,
        scanned: int,
        recovered: int,
        already_completed: int,
        errors: Mapping[str, int],
    ) -> Mapping[str, object]:
        return {
            "schema_version": RECOVERY_SWEEP_SCHEMA_VERSION,
            "scanned": scanned,
            "recovered": recovered,
            "already_completed": already_completed,
            "skipped_or_blocked": sum(errors.values()),
            "issues": [
                {"code": code, "count": count}
                for code, count in sorted(errors.items())
            ],
        }


__all__ = [
    "CheckpointTaskLister",
    "LiteratureExtractionRecoverySweep",
    "RECOVERY_SWEEP_SCHEMA_VERSION",
    "RecoverableLiteratureTask",
]
