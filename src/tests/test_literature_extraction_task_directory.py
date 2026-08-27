from __future__ import annotations

from types import SimpleNamespace

from auto_research.evidence.literature_extraction_task_directory import (
    LiteratureExtractionTaskDirectory,
)
from auto_research.evidence.literature_task_checkpoint import (
    LiteratureTaskCheckpointError,
)


class _Store:
    def __init__(self, checkpoints=(), error: str | None = None) -> None:
        self.checkpoints = dict(checkpoints)
        self.error = error

    def list_task_ids(self, *, limit: int = 64):
        if self.error:
            raise LiteratureTaskCheckpointError(self.error)
        return tuple(self.checkpoints)[:limit]

    def load(self, task_id: str):
        return self.checkpoints[task_id]


def _checkpoint(*, state="paused", stage="coverage_gap"):
    return SimpleNamespace(
        state=state,
        stage=stage,
        private_payload=b"sealed-state",
        receipts=(SimpleNamespace(state="succeeded"), SimpleNamespace(state="planned")),
        spent_calls=1,
        updated_at=1234,
        manifest=SimpleNamespace(max_calls=8, expires_at=5678),
    )


def test_directory_projects_only_safe_reconnectable_state(monkeypatch) -> None:
    checkpoint = _checkpoint()
    monkeypatch.setattr(
        "auto_research.evidence.literature_extraction_task_directory.decode_execution_state",
        lambda _payload: SimpleNamespace(job_state=b"job-state"),
    )
    monkeypatch.setattr(
        "auto_research.evidence.literature_extraction_task_directory._decode_checkpoint_job_state",
        lambda _payload: SimpleNamespace(
            token="opaque_resume_token_abcdefghijklmnopqrstuvwxyz",
            paper={"title": "A paper", "doi": "10.1/safe"},
        ),
    )
    directory = LiteratureExtractionTaskDirectory(
        checkpoints=_Store((("task-secret", checkpoint),)),
        startup_recovery={"recovered": 1, "already_completed": 2},
    )

    result = directory.status()

    assert result["schema_version"] == "literature-extraction-task-directory-v1"
    assert result["startup_recovery"] == {
        "recovered": 1,
        "already_completed": 2,
        "skipped_or_blocked": 0,
    }
    assert result["tasks"] == [{
        "resume_token": "opaque_resume_token_abcdefghijklmnopqrstuvwxyz",
        "state": "paused",
        "stage": "coverage_gap",
        "paper": {"title": "A paper", "doi": "10.1/safe"},
        "completed_calls": 1,
        "spent_calls": 1,
        "max_calls": 8,
        "updated_at": 1234,
        "expires_at": 5678,
        "next_action": "resume_extraction",
    }]
    rendered = repr(result).casefold()
    assert "task-secret" not in rendered
    assert "sealed-state" not in rendered
    assert "job-state" not in rendered
    assert "/users/" not in rendered


def test_directory_reports_store_failure_without_backend_details() -> None:
    result = LiteratureExtractionTaskDirectory(
        checkpoints=_Store(error="literature_checkpoint_store_unavailable"),
    ).status()

    assert result["tasks"] == []
    assert result["issues"] == [
        {"code": "literature_checkpoint_store_unavailable", "count": 1}
    ]
