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

    def iter_task_ids(self):
        if self.error:
            raise LiteratureTaskCheckpointError(self.error)
        return iter(self.checkpoints)

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
        lambda _payload: SimpleNamespace(
            job_state=b"job-state", completion_result=None
        ),
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
        "receipt": None,
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


def test_cancelled_task_is_terminal_but_keeps_paid_call_count(monkeypatch) -> None:
    checkpoint = _checkpoint(state="cancelled", stage="initial_focus")
    monkeypatch.setattr(
        "auto_research.evidence.literature_extraction_task_directory.decode_execution_state",
        lambda _payload: SimpleNamespace(job_state=b"job-state", completion_result=None),
    )
    monkeypatch.setattr(
        "auto_research.evidence.literature_extraction_task_directory._decode_checkpoint_job_state",
        lambda _payload: SimpleNamespace(
            token="opaque_resume_token_abcdefghijklmnopqrstuvwxyz",
            paper={"title": "A paper", "doi": "10.1/safe"},
        ),
    )
    result = LiteratureExtractionTaskDirectory(
        checkpoints=_Store((("task-secret", checkpoint),))
    ).status()
    task = result["tasks"][0]
    assert task["state"] == "cancelled"
    assert task["completed_calls"] == 1
    assert task["resume_token"] is None
    assert task["next_action"] == "restart_extraction"


def test_completed_task_projects_only_concise_persistent_receipt(monkeypatch) -> None:
    checkpoint = _checkpoint(state="completed", stage="completed")
    completion = {
        "schema_version": "literature-extraction-commit-result-v2",
        "status": "completed",
        "paper": {"title": "A paper", "doi": "10.1/safe"},
        "candidate_count": 9,
        "published_item_count": 6,
        "existing_item_count": 1,
        "manual_review_count": 2,
        "visual_evidence_ready": True,
        "table_candidate_count": 3,
        "figure_candidate_count": 4,
        "idempotent": False,
        "extraction_receipt": {"private": "must-not-project"},
        "publication_receipt": {"entity_uids": ["internal"]},
        "dataset_receipt": {
            "schema_version": "dataset-membership-receipt-v1",
            "paper_partition": "validation",
        },
        "search_index": {"status": "refreshed", "document_count": 6},
    }
    monkeypatch.setattr(
        "auto_research.evidence.literature_extraction_task_directory.decode_execution_state",
        lambda _payload: SimpleNamespace(
            job_state=b"job-state", completion_result=completion
        ),
    )
    monkeypatch.setattr(
        "auto_research.evidence.literature_extraction_task_directory._decode_checkpoint_job_state",
        lambda _payload: SimpleNamespace(
            token="opaque_resume_token_abcdefghijklmnopqrstuvwxyz",
            paper={"title": "A paper", "doi": "10.1/safe"},
        ),
    )
    result = LiteratureExtractionTaskDirectory(
        checkpoints=_Store((("task-secret", checkpoint),))
    ).status()
    task = result["tasks"][0]
    assert task["resume_token"] is None
    assert task["next_action"] == "open_search"
    assert task["receipt"] == {
        "schema_version": "literature-extraction-receipt-summary-v1",
        "status": "completed",
        "candidate_count": 9,
        "published_item_count": 6,
        "existing_item_count": 1,
        "manual_review_count": 2,
        "table_candidate_count": 3,
            "figure_candidate_count": 4,
            "visual_evidence_ready": True,
            "visual_stage_status": "ready",
            "search_index": {"status": "refreshed", "document_count": 6},
        "dataset_partition": "validation",
    }
    assert "must-not-project" not in repr(result)
    assert "internal" not in repr(result)


def test_old_unfinished_task_is_visible_ahead_of_recent_terminal_history(monkeypatch):
    store = _Store([(f'closed-{i}', _checkpoint(state='completed', stage='completed'))
                    for i in range(130)] + [('unfinished', _checkpoint(stage='finalizing'))])
    store.iter_task_ids = lambda: iter(store.checkpoints)
    monkeypatch.setattr('auto_research.evidence.literature_extraction_task_directory.decode_execution_state',
        lambda payload: SimpleNamespace(job_state=b'job', completion_result=None))
    monkeypatch.setattr('auto_research.evidence.literature_extraction_task_directory._decode_checkpoint_job_state',
        lambda payload: SimpleNamespace(token='T' * 48, paper={'title':'Synthetic paper'}))
    result = LiteratureExtractionTaskDirectory(checkpoints=store).status(limit=1)
    assert len(result['tasks']) == 1
    assert result['tasks'][0]['state'] == 'paused'
    assert result['tasks'][0]['next_action'] == 'retry_finalization'
    assert result['issues'] == []
