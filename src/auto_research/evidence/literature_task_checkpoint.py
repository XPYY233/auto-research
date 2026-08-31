from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping, Protocol


CHECKPOINT_SCHEMA_VERSION = "literature-task-checkpoint-v1"
PUBLIC_STATUS_SCHEMA_VERSION = "literature-task-checkpoint-status-v1"
ERROR_SCHEMA_VERSION = "literature-task-checkpoint-error-v1"

TASK_STATES = frozenset(
    {
        "authorized",
        "running",
        "paused",
        "validated",
        "completed",
        "failed",
        "outcome_unknown",
    }
)
CALL_STATES = frozenset({"planned", "in_flight", "succeeded", "outcome_unknown"})
CALL_TASKS = frozenset({"analysis", "extraction"})
MODEL_STAGES = frozenset(
    {
        "initial_focus",
        "coverage_gap",
        "coverage_verification",
        "adversarial_branches",
        "third_review",
    }
)
STAGES = MODEL_STAGES | frozenset({"validated", "finalizing", "completed"})

MAX_CALLS = 512
MAX_TOTAL_TOKENS = 8_200_000
MAX_CALL_TOKENS = 64_000
MAX_PRIVATE_PAYLOAD_BYTES = 32 * 1024 * 1024
DEFAULT_LEASE_SECONDS = 300
MAX_LEASE_SECONDS = 1_800

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_OPAQUE_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class LiteratureTaskCheckpointError(RuntimeError):
    _MESSAGES = {
        "literature_checkpoint_invalid": "提取任务检查点无效。",
        "literature_checkpoint_not_found": "未找到提取任务检查点。",
        "literature_checkpoint_conflict": "提取任务已在其他操作中更新。",
        "literature_checkpoint_busy": "提取任务正在处理中。",
        "literature_checkpoint_expired": "提取任务检查点已过期。",
        "literature_checkpoint_corrupt": "提取任务检查点无法安全读取。",
        "literature_checkpoint_store_unavailable": "提取任务检查点存储暂时不可用。",
        "literature_checkpoint_budget_exhausted": "提取任务预算不足。",
        "literature_checkpoint_policy_changed": "提取任务使用的旧预算策略已停用，请从当前 PDF 重新开始。",
        "literature_checkpoint_lease_lost": "提取任务执行权已失效。",
        "literature_call_replayed": "该模型调用已经处理。",
        "literature_call_outcome_unknown": "上一次模型调用结果未知，不能自动重试。",
    }

    def __init__(self, code: str) -> None:
        if code not in self._MESSAGES:
            code = "literature_checkpoint_invalid"
        self.code = code
        self.safe_message = self._MESSAGES[code]
        super().__init__(self.safe_message)

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": ERROR_SCHEMA_VERSION,
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.code
            in {
                "literature_checkpoint_conflict",
                "literature_checkpoint_busy",
                "literature_checkpoint_store_unavailable",
            },
        }


class CheckpointSealer(Protocol):
    """Authenticated encryption supplied by a platform security authority."""

    def seal(self, plaintext: bytes, *, associated_data: bytes) -> bytes: ...

    def open(self, ciphertext: bytes, *, associated_data: bytes) -> bytes: ...


class LiteratureCheckpointPersistence(Protocol):
    def create(self, checkpoint: "LiteratureTaskCheckpoint") -> None: ...

    def load(self, task_id: str) -> "LiteratureTaskCheckpoint": ...

    def list_task_ids(self, *, limit: int = 64) -> tuple[str, ...]: ...

    def compare_and_swap(
        self,
        checkpoint: "LiteratureTaskCheckpoint",
        *,
        expected_revision: int,
    ) -> None: ...


@dataclass(frozen=True)
class LiteratureTaskManifest:
    task_id: str
    session_digest: str
    provider_id: str
    runtime_revision: int
    credential_generation: int
    task_models: tuple[tuple[str, str], ...]
    executor_id: str
    executor_version: str
    pdf_snapshot_fingerprint: str
    max_calls: int
    max_tokens: int
    issued_at: int
    expires_at: int

    def __post_init__(self) -> None:
        validate_task_id(self.task_id)
        require_sha256(self.session_digest)
        require_safe_id(self.provider_id)
        require_nonnegative_int(self.runtime_revision)
        require_nonnegative_int(self.credential_generation)
        require_safe_id(self.executor_id)
        require_safe_id(self.executor_version)
        require_sha256(self.pdf_snapshot_fingerprint)
        require_positive_int(self.max_calls, maximum=MAX_CALLS)
        require_positive_int(self.max_tokens, maximum=MAX_TOTAL_TOKENS)
        require_nonnegative_int(self.issued_at)
        require_nonnegative_int(self.expires_at)
        require(self.expires_at > self.issued_at)
        models = dict(self.task_models)
        require(len(models) == len(self.task_models))
        require(set(models) == CALL_TASKS)
        for task, model in self.task_models:
            require(task in CALL_TASKS)
            require_safe_id(model)

    @property
    def model_map(self) -> Mapping[str, str]:
        return dict(self.task_models)


@dataclass(frozen=True)
class LiteratureCallReceipt:
    ordinal: int
    stage: str
    task: str
    model: str
    call_digest: str
    max_tokens: int
    state: str = "planned"
    result_digest: str | None = None
    started_at: int | None = None
    finished_at: int | None = None

    def __post_init__(self) -> None:
        require_positive_int(self.ordinal, maximum=MAX_CALLS)
        require(self.stage in MODEL_STAGES)
        require(self.task in CALL_TASKS)
        require_safe_id(self.model)
        require_sha256(self.call_digest)
        require_positive_int(self.max_tokens, maximum=MAX_CALL_TOKENS)
        require(self.state in CALL_STATES)
        if self.state == "planned":
            require(self.started_at is None and self.finished_at is None)
            require(self.result_digest is None)
        elif self.state == "in_flight":
            require_nonnegative_int(self.started_at)
            require(self.finished_at is None and self.result_digest is None)
        elif self.state == "succeeded":
            require_nonnegative_int(self.started_at)
            require_nonnegative_int(self.finished_at)
            require(self.finished_at >= self.started_at)
            require_sha256(self.result_digest)
        else:
            require_nonnegative_int(self.started_at)
            require_nonnegative_int(self.finished_at)
            require(self.finished_at >= self.started_at)
            require(self.result_digest is None)


@dataclass(frozen=True)
class LiteratureTaskCheckpoint:
    """Recoverable mutable domain state, never immutable PDF snapshot bytes.

    private_payload is sealed at rest and capped at 32 MiB. It may contain
    validated intermediate domain state and an opaque reference to a separately
    stored immutable PDF snapshot. A later blob authority must write that large
    snapshot once; checkpoint CAS updates must not copy the PDF bytes.
    """

    manifest: LiteratureTaskManifest
    revision: int
    state: str
    stage: str
    receipts: tuple[LiteratureCallReceipt, ...]
    spent_calls: int
    spent_tokens: int
    updated_at: int
    private_payload: bytes = field(repr=False)
    lease_owner_digest: str | None = None
    lease_expires_at: int | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        require_nonnegative_int(self.revision)
        require(self.state in TASK_STATES)
        require(self.stage in STAGES)
        require_nonnegative_int(self.spent_calls)
        require_nonnegative_int(self.spent_tokens)
        require_nonnegative_int(self.updated_at)
        require(isinstance(self.private_payload, bytes))
        require(len(self.private_payload) <= MAX_PRIVATE_PAYLOAD_BYTES)
        ordinals = tuple(receipt.ordinal for receipt in self.receipts)
        require(ordinals == tuple(range(1, len(ordinals) + 1)))
        require(len(self.receipts) <= self.manifest.max_calls)
        charged = tuple(
            receipt
            for receipt in self.receipts
            if receipt.state in {"in_flight", "succeeded", "outcome_unknown"}
        )
        require(self.spent_calls == len(charged))
        require(self.spent_tokens == sum(receipt.max_tokens for receipt in charged))
        require(self.spent_calls <= self.manifest.max_calls)
        require(self.spent_tokens <= self.manifest.max_tokens)
        for receipt in self.receipts:
            require(receipt.model == self.manifest.model_map[receipt.task])
        unresolved = tuple(
            receipt for receipt in self.receipts if receipt.state in {"in_flight", "outcome_unknown"}
        )
        require(len(unresolved) <= 1)
        if any(receipt.state == "outcome_unknown" for receipt in self.receipts):
            require(self.state == "outcome_unknown")
            require(self.reason_code == "literature_call_outcome_unknown")
        if self.lease_owner_digest is None:
            require(self.lease_expires_at is None)
        else:
            require_sha256(self.lease_owner_digest)
            require_nonnegative_int(self.lease_expires_at)
        if self.reason_code is not None:
            require_safe_id(self.reason_code)

    def public_status(self) -> dict[str, object]:
        return {
            "schema_version": PUBLIC_STATUS_SCHEMA_VERSION,
            "task_id": self.manifest.task_id,
            "revision": self.revision,
            "state": self.state,
            "stage": self.stage,
            "completed_calls": sum(receipt.state == "succeeded" for receipt in self.receipts),
            "spent_calls": self.spent_calls,
            "max_calls": self.manifest.max_calls,
            "spent_tokens": self.spent_tokens,
            "max_tokens": self.manifest.max_tokens,
            "expires_at": self.manifest.expires_at,
            "reason_code": self.reason_code,
        }


def require(condition: bool) -> None:
    if not condition:
        raise LiteratureTaskCheckpointError("literature_checkpoint_invalid")


def require_nonnegative_int(value: object) -> None:
    require(isinstance(value, int) and not isinstance(value, bool) and value >= 0)


def require_positive_int(value: object, *, maximum: int) -> None:
    require(isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= maximum)


def require_safe_id(value: object) -> None:
    require(isinstance(value, str) and bool(_SAFE_ID_RE.fullmatch(value)))


def require_sha256(value: object) -> None:
    require(isinstance(value, str) and bool(_SHA256_RE.fullmatch(value)))


def validate_task_id(task_id: object) -> None:
    require(isinstance(task_id, str) and bool(_OPAQUE_TASK_ID_RE.fullmatch(task_id)))


__all__ = [
    "CALL_TASKS",
    "CHECKPOINT_SCHEMA_VERSION",
    "CheckpointSealer",
    "DEFAULT_LEASE_SECONDS",
    "LiteratureCallReceipt",
    "LiteratureCheckpointPersistence",
    "LiteratureTaskCheckpoint",
    "LiteratureTaskCheckpointError",
    "LiteratureTaskManifest",
    "MAX_LEASE_SECONDS",
    "MAX_PRIVATE_PAYLOAD_BYTES",
    "MODEL_STAGES",
    "STAGES",
]
