from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from auto_research.evidence.literature_extraction_recovery_controller import (
    LiteratureExtractionRecoveryController,
    LiteratureRecoveryControllerError,
)


class _Recovery:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.failure = None

    def recover(self, *, task_id: str, job_token: str):
        self.calls.append((task_id, job_token))
        if self.failure:
            raise self.failure
        return {
            "schema_version": "literature-extraction-recovery-result-v1",
            "code": "literature_recovery_completed",
            "status": "completed",
            "stage": "completed",
            "next_action": "open_extraction_receipt",
            "already_completed": False,
            "completion": {
                "schema_version": "literature-extraction-commit-result-v2",
                "status": "completed",
            },
        }


class _Store:
    def __init__(self, checkpoint) -> None:
        self.checkpoint = checkpoint
        self.failure = None

    def list_task_ids(self, *, limit=64):
        assert limit == 128
        if self.failure:
            raise self.failure
        return (self.checkpoint.manifest.task_id,)

    def load(self, task_id):
        assert task_id == self.checkpoint.manifest.task_id
        if self.failure:
            raise self.failure
        return self.checkpoint


class _Papers:
    def __init__(self, path: Path, sha256: str) -> None:
        self.path = path
        self.sha256 = sha256

    def get_paper(self, paper_id):
        return {
            "id": paper_id,
            "pdf_path": str(self.path),
            "pdf_sha256": self.sha256,
        }


class _Directory:
    def __init__(self) -> None:
        self.issues = []

    def status(self, *, limit=16):
        assert limit == 16
        return {
            "schema_version": "literature-extraction-task-directory-v1",
            "tasks": [],
            "startup_recovery": {},
            "issues": list(self.issues),
        }


def _controller(tmp_path: Path, *, state: str = "validated"):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\ntrusted test snapshot\n%%EOF")
    sha256 = hashlib.sha256(pdf.read_bytes()).hexdigest()
    token = "T" * 48
    task_id = "literature_task_abcdefghijklmnop"
    content_fingerprint = "c" * 64
    checkpoint = SimpleNamespace(
        manifest=SimpleNamespace(
            task_id=task_id,
            pdf_snapshot_fingerprint=content_fingerprint,
        ),
        private_payload=b"authenticated-checkpoint",
        state=state,
        stage="validated",
    )
    recovery = _Recovery()
    directory = _Directory()
    controller = LiteratureExtractionRecoveryController(
        recovery=recovery,
        checkpoints=_Store(checkpoint),
        papers=_Papers(pdf, sha256),
        task_directory=directory,
        pdf_fingerprint=lambda paper: hashlib.sha256(
            Path(str(paper["pdf_path"])).read_bytes()
        ).hexdigest(),
    )
    job = SimpleNamespace(
        token=token,
        paper_id=7,
        snapshot={
            "pdf_sha256": sha256,
            "content_fingerprint": content_fingerprint,
        },
    )
    return controller, recovery, directory, token, job


@pytest.mark.parametrize("repair", [False, True])
def test_same_paper_and_pdf_checkpoint_blocks_new_paid_prepare(tmp_path: Path, repair) -> None:
    controller, recovery, _directory, _token, job = _controller(tmp_path)
    with (
        patch(
            "auto_research.evidence.literature_extraction_recovery_controller."
            "decode_execution_state",
            return_value=SimpleNamespace(job_state=b"job"),
        ),
        patch(
            "auto_research.evidence.literature_extraction_recovery_controller."
            "decode_job_private_state",
            return_value=job,
        ),
        pytest.raises(LiteratureRecoveryControllerError) as raised,
    ):
        controller.assert_prepare_allowed({"paper_id": 7, "force_rescan": not repair, **({"repair_visuals": True} if repair else {})})

    assert raised.value.code == "literature_active_task_exists"
    assert raised.value.next_action == "retry_finalization"
    assert recovery.calls == []


def test_recovery_uses_authenticated_task_identity_and_never_accepts_extra_input(
    tmp_path: Path,
) -> None:
    controller, recovery, _directory, token, job = _controller(tmp_path)
    with (
        patch(
            "auto_research.evidence.literature_extraction_recovery_controller."
            "decode_execution_state",
            return_value=SimpleNamespace(job_state=b"job"),
        ),
        patch(
            "auto_research.evidence.literature_extraction_recovery_controller."
            "decode_job_private_state",
            return_value=job,
        ),
    ):
        result = controller.recover_finalization({"resume_token": token})
        with pytest.raises(LiteratureRecoveryControllerError) as raised:
            controller.recover_finalization(
                {"resume_token": token, "provider_id": "deepseek"}
            )

    assert result["completion"]["status"] == "completed"
    assert recovery.calls == [("literature_task_abcdefghijklmnop", token)]
    assert raised.value.code == "literature_recovery_invalid"
    assert token not in repr(raised.value.__dict__)


def test_directory_issue_fails_closed_and_never_exposes_issue_details(
    tmp_path: Path,
) -> None:
    controller, recovery, directory, _token, _job = _controller(tmp_path)
    directory.issues = [
        {"code": "literature_checkpoint_corrupt", "count": 1}
    ]

    with pytest.raises(LiteratureRecoveryControllerError) as raised:
        controller.task_directory()

    assert raised.value.code == "literature_task_directory_corrupt"
    assert raised.value.cause_code == "literature_checkpoint_corrupt"
    assert recovery.calls == []
    assert "/" not in raised.value.safe_message


def test_changed_current_pdf_does_not_match_old_checkpoint_identity(
    tmp_path: Path,
) -> None:
    controller, recovery, _directory, _token, job = _controller(tmp_path)
    job.snapshot["pdf_sha256"] = "d" * 64
    with (
        patch(
            "auto_research.evidence.literature_extraction_recovery_controller."
            "decode_execution_state",
            return_value=SimpleNamespace(job_state=b"job"),
        ),
        patch(
            "auto_research.evidence.literature_extraction_recovery_controller."
            "decode_job_private_state",
            return_value=job,
        ),
    ):
        controller.assert_prepare_allowed({"paper_id": 7, "force_rescan": True})

    assert recovery.calls == []


def test_recovery_failure_keeps_stable_stage_and_retry_action(tmp_path: Path) -> None:
    controller, recovery, _directory, token, job = _controller(tmp_path)

    class _Failure(RuntimeError):
        code = "literature_checkpoint_store_unavailable"
        safe_message = "private implementation text"
        stage = "checkpoint"
        next_action = "retry_finalization"
        retryable = True

    recovery.failure = _Failure("/private/path must not escape")
    with (
        patch(
            "auto_research.evidence.literature_extraction_recovery_controller."
            "decode_execution_state",
            return_value=SimpleNamespace(job_state=b"job"),
        ),
        patch(
            "auto_research.evidence.literature_extraction_recovery_controller."
            "decode_job_private_state",
            return_value=job,
        ),
        pytest.raises(LiteratureRecoveryControllerError) as raised,
    ):
        controller.recover_finalization({"resume_token": token})

    assert raised.value.code == "literature_checkpoint_store_unavailable"
    assert raised.value.stage == "checkpoint"
    assert raised.value.next_action == "retry_finalization"
    assert raised.value.retryable is True
    assert "/private" not in raised.value.safe_message
