from __future__ import annotations

from collections import Counter
from typing import Mapping, Protocol

from .literature_checkpoint_runtime import decode_execution_state
from .literature_extraction_checkpoint_workflow import _decode_checkpoint_job_state
from .literature_task_checkpoint import (
    LiteratureTaskCheckpoint,
    LiteratureTaskCheckpointError,
)


TASK_DIRECTORY_SCHEMA_VERSION = "literature-extraction-task-directory-v1"


class LiteratureCheckpointDirectoryStore(Protocol):
    def list_task_ids(self, *, limit: int = 64) -> tuple[str, ...]: ...

    def load(self, task_id: str) -> LiteratureTaskCheckpoint: ...


class LiteratureExtractionTaskDirectory:
    """Project authenticated recovery state without local paths or prompts."""

    def __init__(
        self,
        *,
        checkpoints: LiteratureCheckpointDirectoryStore,
        startup_recovery: Mapping[str, object] | None = None,
    ) -> None:
        if not callable(getattr(checkpoints, "list_task_ids", None)) or not callable(
            getattr(checkpoints, "load", None)
        ):
            raise ValueError("literature task directory composition is invalid")
        self._checkpoints = checkpoints
        self._startup_recovery = dict(startup_recovery or {})

    def status(self, *, limit: int = 16) -> Mapping[str, object]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 32:
            raise ValueError("literature task directory limit is invalid")
        issues: Counter[str] = Counter()
        try:
            task_ids = self._checkpoints.list_task_ids(limit=limit)
        except LiteratureTaskCheckpointError as exc:
            return self._report(tasks=(), issues={exc.code: 1})
        tasks: list[Mapping[str, object]] = []
        for task_id in task_ids:
            try:
                checkpoint = self._checkpoints.load(task_id)
                execution = decode_execution_state(checkpoint.private_payload)
                job = _decode_checkpoint_job_state(execution.job_state)
                tasks.append(
                    self._project(
                        checkpoint,
                        job,
                        completion=execution.completion_result,
                    )
                )
            except LiteratureTaskCheckpointError as exc:
                issues[exc.code] += 1
            except Exception:
                issues["literature_checkpoint_corrupt"] += 1
        return self._report(tasks=tasks, issues=issues)

    def _report(
        self,
        *,
        tasks: tuple[Mapping[str, object], ...] | list[Mapping[str, object]],
        issues: Mapping[str, int],
    ) -> Mapping[str, object]:
        startup = self._startup_recovery
        safe_startup = {
            "recovered": _safe_count(startup.get("recovered")),
            "already_completed": _safe_count(startup.get("already_completed")),
            "skipped_or_blocked": _safe_count(startup.get("skipped_or_blocked")),
        }
        return {
            "schema_version": TASK_DIRECTORY_SCHEMA_VERSION,
            "tasks": [dict(task) for task in tasks],
            "startup_recovery": safe_startup,
            "issues": [
                {"code": code, "count": count}
                for code, count in sorted(issues.items())
            ],
        }

    @staticmethod
    def _project(
        checkpoint: LiteratureTaskCheckpoint,
        job: object,
        *,
        completion: Mapping[str, object] | None,
    ) -> Mapping[str, object]:
        paper = getattr(job, "paper", None)
        token = getattr(job, "token", None)
        if (
            not isinstance(paper, Mapping)
            or not isinstance(token, str)
            or not token
            or len(token) > 256
        ):
            raise LiteratureTaskCheckpointError("literature_checkpoint_corrupt")
        state = checkpoint.state
        next_action = {
            "authorized": "resume_extraction",
            "running": "resume_extraction",
            "paused": "resume_extraction",
            "validated": "retry_finalization",
            "completed": "open_search",
            "failed": "restart_extraction",
            "cancelled": "restart_extraction",
            "outcome_unknown": "review_call_outcome",
        }[state]
        return {
            "resume_token": (
                token if state not in {"completed", "failed", "cancelled"} else None
            ),
            "state": state,
            "stage": checkpoint.stage,
            "paper": {
                "title": str(paper.get("title") or "")[:500],
                "doi": str(paper.get("doi") or "")[:300] or None,
            },
            "completed_calls": sum(
                receipt.state == "succeeded" for receipt in checkpoint.receipts
            ),
            "spent_calls": checkpoint.spent_calls,
            "max_calls": checkpoint.manifest.max_calls,
            "updated_at": checkpoint.updated_at,
            "expires_at": checkpoint.manifest.expires_at,
            "next_action": next_action,
            "receipt": _public_receipt(completion),
        }


def _safe_count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _public_receipt(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    integer_keys = (
        "candidate_count",
        "published_item_count",
        "existing_item_count",
        "manual_review_count",
        "table_candidate_count",
        "figure_candidate_count",
    )
    search_index = value.get("search_index")
    dataset_receipt = value.get("dataset_receipt")
    status = value.get("status")
    visual_ready = value.get("visual_evidence_ready")
    visual_status = value.get("visual_stage_status")
    if visual_status is None and visual_ready is True:
        # Completed checkpoints written before explicit visual-stage status are
        # safe to resume as legacy ready receipts.
        visual_status = "ready"
    if (
        value.get("schema_version") != "literature-extraction-commit-result-v2"
        or status not in {"completed", "saved_index_pending"}
        or not isinstance(visual_ready, bool)
        or visual_status not in {"ready", "not_found"}
        or visual_ready != (visual_status == "ready")
        or any(
            isinstance(value.get(key), bool)
            or not isinstance(value.get(key), int)
            or value.get(key) < 0
            for key in integer_keys
        )
        or not isinstance(search_index, Mapping)
        or search_index.get("status") not in {"refreshed", "pending"}
        or not isinstance(dataset_receipt, Mapping)
        or dataset_receipt.get("schema_version")
        != "dataset-membership-receipt-v1"
        or dataset_receipt.get("paper_partition") not in {
            "train",
            "validation",
            "test",
        }
    ):
        raise LiteratureTaskCheckpointError("literature_checkpoint_corrupt")
    document_count = search_index.get("document_count")
    if document_count is not None and (
        isinstance(document_count, bool)
        or not isinstance(document_count, int)
        or document_count < 0
    ):
        raise LiteratureTaskCheckpointError("literature_checkpoint_corrupt")
    return {
        "schema_version": "literature-extraction-receipt-summary-v1",
        "status": status,
        **{key: value[key] for key in integer_keys},
        "visual_evidence_ready": visual_ready,
        "visual_stage_status": visual_status,
        "search_index": {
            "status": search_index["status"],
            "document_count": document_count,
        },
        "dataset_partition": dataset_receipt["paper_partition"],
    }


__all__ = [
    "LiteratureCheckpointDirectoryStore",
    "LiteratureExtractionTaskDirectory",
    "TASK_DIRECTORY_SCHEMA_VERSION",
]
