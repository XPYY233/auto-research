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
                tasks.append(self._project(checkpoint, job))
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
    def _project(checkpoint: LiteratureTaskCheckpoint, job: object) -> Mapping[str, object]:
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
            "outcome_unknown": "review_call_outcome",
        }[state]
        return {
            "resume_token": token if state not in {"completed", "failed"} else None,
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
        }


def _safe_count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


__all__ = [
    "LiteratureCheckpointDirectoryStore",
    "LiteratureExtractionTaskDirectory",
    "TASK_DIRECTORY_SCHEMA_VERSION",
]
