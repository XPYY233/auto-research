from __future__ import annotations

from collections import Counter
from typing import Iterator, Mapping, Protocol

from .literature_extraction_recovery import (
    LiteratureExtractionRecoveryError,
)
from .literature_task_checkpoint import LiteratureTaskCheckpoint, LiteratureTaskCheckpointError


RECOVERY_SWEEP_SCHEMA_VERSION = "literature-extraction-recovery-sweep-v1"


class CheckpointTaskLister(Protocol):
    def iter_task_ids(self) -> Iterator[str]: ...

    def load(self, task_id: str) -> LiteratureTaskCheckpoint: ...


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
        if (
            not callable(getattr(checkpoints, "iter_task_ids", None))
            or not callable(getattr(checkpoints, "load", None))
            or not callable(getattr(recovery, "recover_task", None))
        ):
            raise ValueError("literature recovery sweep composition is invalid")
        self._checkpoints = checkpoints
        self._recovery = recovery

    def run(self, *, limit: int = 16) -> Mapping[str, object]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 64:
            raise ValueError("literature recovery sweep limit is invalid")
        scanned = recovered = already_completed = attempts = 0
        errors: Counter[str] = Counter()
        completed: list[tuple[int, str]] = []
        try:
            for task_id in self._checkpoints.iter_task_ids():
                scanned += 1
                try:
                    checkpoint = self._checkpoints.load(task_id)
                    if checkpoint.state == "completed":
                        # Preserve acknowledgement/snapshot cleanup after a
                        # crash, without using the unfinished-task budget.
                        completed.append((checkpoint.updated_at, task_id))
                        completed.sort(key=lambda item: (-item[0], item[1]))
                        del completed[limit:]
                        continue
                    if (
                        checkpoint.state not in {"running", "paused", "validated"}
                        or checkpoint.stage not in {"validated", "finalizing"}
                    ):
                        errors["literature_recovery_not_ready"] += 1
                        continue
                    if attempts >= limit:
                        errors["literature_recovery_deferred"] += 1
                        continue
                    attempts += 1
                    result = self._recovery.recover_task(task_id)
                    if result.get("already_completed") is True:
                        already_completed += 1
                    else:
                        recovered += 1
                except (LiteratureExtractionRecoveryError, LiteratureTaskCheckpointError) as exc:
                    errors[exc.code] += 1
        except LiteratureTaskCheckpointError as exc:
            errors[exc.code] += 1
        for _updated_at, task_id in completed:
            try:
                result = self._recovery.recover_task(task_id)
                if result.get("already_completed") is True:
                    already_completed += 1
                else:
                    recovered += 1
            except (LiteratureExtractionRecoveryError, LiteratureTaskCheckpointError) as exc:
                errors[exc.code] += 1
        return self._report(
            scanned=scanned, recovered=recovered, already_completed=already_completed, errors=errors,
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
