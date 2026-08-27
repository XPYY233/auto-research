from __future__ import annotations

from auto_research.evidence.literature_extraction_recovery import (
    LiteratureExtractionRecoveryError,
)
from auto_research.evidence.literature_extraction_recovery_sweep import (
    LiteratureExtractionRecoverySweep,
)
from auto_research.evidence.literature_task_checkpoint import (
    LiteratureTaskCheckpointError,
)


class _Lister:
    def __init__(self, task_ids=(), error: str | None = None) -> None:
        self.task_ids = tuple(task_ids)
        self.error = error
        self.limits: list[int] = []

    def list_task_ids(self, *, limit: int = 64):
        self.limits.append(limit)
        if self.error:
            raise LiteratureTaskCheckpointError(self.error)
        return self.task_ids[:limit]


class _Recovery:
    def __init__(self, results) -> None:
        self.results = dict(results)
        self.calls: list[str] = []

    def recover_task(self, task_id: str):
        self.calls.append(task_id)
        value = self.results[task_id]
        if isinstance(value, str):
            raise LiteratureExtractionRecoveryError(value)
        return value


def test_sweep_recovers_recent_tasks_and_reports_only_aggregate_codes() -> None:
    lister = _Lister(("task_a", "task_b", "task_c"))
    recovery = _Recovery({
        "task_a": {"already_completed": False},
        "task_b": {"already_completed": True},
        "task_c": "literature_recovery_not_ready",
    })

    report = LiteratureExtractionRecoverySweep(
        checkpoints=lister,
        recovery=recovery,
    ).run(limit=3)

    assert report == {
        "schema_version": "literature-extraction-recovery-sweep-v1",
        "scanned": 3,
        "recovered": 1,
        "already_completed": 1,
        "skipped_or_blocked": 1,
        "issues": [{"code": "literature_recovery_not_ready", "count": 1}],
    }
    assert lister.limits == [3]
    assert recovery.calls == ["task_a", "task_b", "task_c"]
    assert "task_a" not in repr(report)


def test_sweep_fails_closed_when_store_is_unavailable() -> None:
    report = LiteratureExtractionRecoverySweep(
        checkpoints=_Lister(error="literature_checkpoint_store_unavailable"),
        recovery=_Recovery({}),
    ).run()

    assert report["scanned"] == 0
    assert report["recovered"] == 0
    assert report["issues"] == [
        {"code": "literature_checkpoint_store_unavailable", "count": 1}
    ]
