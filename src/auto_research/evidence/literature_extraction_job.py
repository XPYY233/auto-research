from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, Sequence

import fitz

from .context_chat import _read_stable_pdf_snapshot
from .deepseek_extraction import (
    BASE_EXTRACTION_FOCUSES,
    _extraction_messages,
    _is_reference_dominant,
    _page_chunks,
)
from .experiment_types import classify_experiment_types, extraction_focuses_for_profile
from .quality_pipeline import ROLE_A, ROLE_B
from .literature_snapshot_blob import (
    LiteratureSnapshotBlobError,
    SealedImmutablePDFBlobStore,
)
from .literature_job_persistence import (
    DecodedLiteratureJobState,
    LiteratureJobPersistenceError,
    decode_job_private_state,
    encode_job_private_state,
)
from .literature_finalizer_port import TrustedAtomicLiteratureFinalizer


SCHEMA_VERSION = "literature-extraction-job-v1"
SUMMARY_SCHEMA_VERSION = "literature-extraction-stage-summary-v1"
STAGE_ORDER = (
    "initial_focus",
    "coverage_gap",
    "coverage_verification",
    "adversarial_branches",
    "third_review",
    "validated",
    "finalized",
)
MODEL_STAGES = frozenset({
    "initial_focus", "coverage_gap", "coverage_verification", "adversarial_branches",
    "third_review",
})
REQUIRED_CALL_STAGES = frozenset({"initial_focus", "coverage_gap"})
LOCAL_CAPABLE_STAGES = frozenset({
    "coverage_verification", "adversarial_branches", "third_review",
})
ALLOWED_TASKS = frozenset({"extraction", "verification", "analysis", "localization"})
MAX_STAGE_CALLS = 512
MAX_STAGE_TOKENS = 8_200_000
MAX_MESSAGE_CHARS = 4_000_000
MAX_STAGE_BYTES = 32_000_000
MAX_STAGE_OUTPUT_BYTES = 32_000_000
MAX_JOB_OUTPUT_BYTES = 96_000_000
MAX_EXECUTION_LEASE_SECONDS = 10 * 60
_SENSITIVE_KEYS = frozenset({
    "api_key", "credential_ref", "endpoint", "pdf_path", "file_path", "local_path",
    "selection_id", "zotero_key", "database_path",
})
_LOCAL_PATH_RE = re.compile(
    r"(?:^|[\s='\"])(?:/Users/|/home/|/private/|/tmp/|/var/|/etc/|/usr/|/root/|"
    r"/Applications/|/Library/|/System/|[A-Za-z]:[\\/]|file:|sqlite:|\\\\)",
    re.IGNORECASE,
)
MAX_JOBS = 16
MAX_SESSION_JOBS = 4
MAX_SNAPSHOT_BYTES = 128 * 1024 * 1024
MAX_SNAPSHOT_STORE_BYTES = 512 * 1024 * 1024
MAX_JOB_TTL_SECONDS = 24 * 60 * 60
DEFAULT_TTL_SECONDS = 30 * 60
DEFAULT_PERSISTENT_TTL_SECONDS = MAX_JOB_TTL_SECONDS


class LiteratureExtractionJobError(Exception):
    def __init__(self, code: str, safe_message: str):
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "literature-extraction-error-v1",
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.code in {
                "literature_stage_busy", "literature_job_store_full", "literature_commit_failed",
                "literature_commit_unavailable",
                "literature_persistence_unavailable",
            },
        }


class Clock(Protocol):
    def now(self) -> float: ...


class SystemClock:
    def now(self) -> float:
        return time.time()


class PaperSource(Protocol):
    def get_paper(self, paper_id: int) -> Mapping[str, Any] | None: ...


AtomicLiteratureFinalizer = TrustedAtomicLiteratureFinalizer


class LiteratureStagePlanner(Protocol):
    """Trusted server-side adapter around the existing prompt and validator functions."""

    def plan_next(
        self, context: "LiteratureStageContext", raw_results: Sequence[Mapping[str, Any]]
    ) -> "PlannedLiteratureStage": ...


def _freeze(value: Any, *, depth: int = 0) -> Any:
    if depth > 10:
        raise LiteratureExtractionJobError("literature_stage_invalid", "抽取阶段内容层级过深")
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item, depth=depth + 1) for key, item in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze(item, depth=depth + 1) for item in value)
    raise LiteratureExtractionJobError("literature_stage_invalid", "抽取阶段包含不支持的数据")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain(item) for item in value]
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _validate_intermediate(value: Any, *, depth: int = 0) -> None:
    if depth > 10:
        raise LiteratureExtractionJobError("literature_stage_invalid", "抽取阶段内容层级过深")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold() in _SENSITIVE_KEYS:
                raise LiteratureExtractionJobError("literature_stage_invalid", "抽取阶段包含受保护字段")
            _validate_intermediate(item, depth=depth + 1)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _validate_intermediate(item, depth=depth + 1)
        return
    if isinstance(value, str) and _LOCAL_PATH_RE.search(value):
        raise LiteratureExtractionJobError("literature_stage_invalid", "抽取阶段包含本机位置")


@dataclass(frozen=True)
class FrozenPDFContext:
    pdf_sha256: str
    pages: tuple[Mapping[str, Any], ...]
    page_count: int
    content_fingerprint: str


@dataclass(frozen=True)
class ImmutablePDFSnapshot:
    """Private immutable PDF bytes captured by the snapshot authority.

    The payload is intentionally excluded from repr/equality so it cannot be
    projected accidentally with the public job/package metadata.  Consumers
    must present the already-bound SHA-256 before receiving the bytes.
    """

    sha256: str
    size: int
    _content: bytes = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            type(self._content) is not bytes
            or not self._content
            or len(self._content) > MAX_SNAPSHOT_BYTES
            or self.size != len(self._content)
            or not isinstance(self.sha256, str)
            or len(self.sha256) != 64
            or not hmac.compare_digest(
                self.sha256, hashlib.sha256(self._content).hexdigest()
            )
        ):
            raise LiteratureExtractionJobError(
                "literature_pdf_invalid", "PDF 快照无效"
            )

    @classmethod
    def create(cls, content: bytes) -> "ImmutablePDFSnapshot":
        if type(content) is not bytes or not content or len(content) > MAX_SNAPSHOT_BYTES:
            raise LiteratureExtractionJobError(
                "literature_pdf_invalid", "PDF 快照无效"
            )
        return cls(
            sha256=hashlib.sha256(content).hexdigest(),
            size=len(content),
            _content=content,
        )

    def verified_bytes(self, expected_sha256: str) -> bytes:
        if (
            not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or not hmac.compare_digest(self.sha256, expected_sha256)
            or self.size != len(self._content)
        ):
            raise LiteratureExtractionJobError(
                "literature_source_stale", "PDF 快照身份不一致"
            )
        return self._content


@dataclass(frozen=True)
class FrozenModelCall:
    call_id: str
    task: str
    messages: tuple[Mapping[str, Any], ...]
    max_tokens: int
    options: Mapping[str, Any]
    call_digest: str

    @classmethod
    def create(
        cls,
        *,
        call_id: str,
        task: str,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int,
        options: Mapping[str, Any] | None = None,
    ) -> "FrozenModelCall":
        if task not in ALLOWED_TASKS or not call_id or len(call_id) > 120:
            raise LiteratureExtractionJobError("literature_stage_invalid", "模型调用计划无效")
        if not isinstance(max_tokens, int) or not 1 <= max_tokens <= 32_000:
            raise LiteratureExtractionJobError("literature_stage_invalid", "模型调用预算无效")
        frozen_messages = _freeze(list(messages))
        frozen_options = _freeze(dict(options or {}))
        payload = {
            "call_id": call_id,
            "task": task,
            "messages": frozen_messages,
            "max_tokens": max_tokens,
            "options": frozen_options,
        }
        if len(_canonical_bytes(payload)) > MAX_MESSAGE_CHARS:
            raise LiteratureExtractionJobError("literature_stage_too_large", "抽取阶段发送内容超出限制")
        return cls(
            call_id=call_id,
            task=task,
            messages=tuple(frozen_messages),
            max_tokens=max_tokens,
            options=frozen_options,
            call_digest=hashlib.sha256(_canonical_bytes(payload)).hexdigest(),
        )


@dataclass(frozen=True)
class FrozenExtractionStage:
    name: str
    calls: tuple[FrozenModelCall, ...]
    input_fingerprint: str
    stage_fingerprint: str
    created_at: float

    @classmethod
    def create(
        cls, name: str, calls: Sequence[FrozenModelCall], input_fingerprint: str, created_at: float
    ) -> "FrozenExtractionStage":
        if name not in MODEL_STAGES:
            raise LiteratureExtractionJobError("literature_stage_invalid", "未知抽取阶段")
        frozen_calls = tuple(calls)
        if name in REQUIRED_CALL_STAGES and not frozen_calls:
            raise LiteratureExtractionJobError("literature_stage_invalid", "模型阶段必须包含调用计划")
        if len(frozen_calls) > MAX_STAGE_CALLS:
            raise LiteratureExtractionJobError("literature_stage_too_large", "抽取阶段调用次数超出限制")
        if sum(call.max_tokens for call in frozen_calls) > MAX_STAGE_TOKENS:
            raise LiteratureExtractionJobError("literature_stage_too_large", "抽取阶段令牌预算超出限制")
        if sum(len(_canonical_bytes({
            "messages": call.messages, "options": call.options,
        })) for call in frozen_calls) > MAX_STAGE_BYTES:
            raise LiteratureExtractionJobError("literature_stage_too_large", "抽取阶段发送内容超出限制")
        ids = [call.call_id for call in frozen_calls]
        if len(ids) != len(set(ids)):
            raise LiteratureExtractionJobError("literature_stage_invalid", "抽取阶段调用标识重复")
        manifest = {
            "name": name,
            "input_fingerprint": input_fingerprint,
            "calls": [call.call_digest for call in frozen_calls],
        }
        return cls(
            name=name,
            calls=frozen_calls,
            input_fingerprint=input_fingerprint,
            stage_fingerprint=hashlib.sha256(_canonical_bytes(manifest)).hexdigest(),
            created_at=created_at,
        )


@dataclass(frozen=True)
class ValidatedLiteraturePackage:
    paper_id: int
    paper: Mapping[str, Any]
    snapshot_fingerprint: str
    pdf_sha256: str
    pdf_snapshot: ImmutablePDFSnapshot = field(repr=False, compare=False)
    experiment_profile: Mapping[str, Any]
    quality_result: Mapping[str, Any]


@dataclass(frozen=True)
class FrozenStageOutput:
    stage: str
    stage_fingerprint: str
    result: Any
    result_fingerprint: str


@dataclass(frozen=True)
class LiteratureStageContext:
    stage: FrozenExtractionStage
    paper: Mapping[str, Any]
    pages: tuple[Mapping[str, Any], ...]
    chunks: tuple[tuple[Mapping[str, Any], ...], ...]
    focuses: tuple[str, ...]
    learning_guidance: str
    experiment_profile: Mapping[str, Any]
    prior_outputs: tuple[FrozenStageOutput, ...]
    snapshot_fingerprint: str


@dataclass(frozen=True)
class PlannedLiteratureStage:
    next_stage: str
    validated_result: Any
    calls: tuple[FrozenModelCall, ...]
    input_fingerprint: str


@dataclass
class _Job:
    token: str
    session_digest: str
    paper_id: int
    paper: Mapping[str, Any]
    snapshot_handle: str
    snapshot: FrozenPDFContext
    experiment_profile: Mapping[str, Any]
    chunks: tuple[tuple[Mapping[str, Any], ...], ...]
    focuses: tuple[str, ...]
    learning_guidance: str
    stage: FrozenExtractionStage
    issued_at: float
    expires_at: float
    claimed: bool = False
    claim_expires_at: float | None = None
    status: str = "prepared"
    stage_outputs: tuple[FrozenStageOutput, ...] = ()
    validated_package: ValidatedLiteraturePackage | None = None
    validated_quality_result: Any | None = None


def _job_from_decoded_state(state: DecodedLiteratureJobState) -> _Job:
    """Rebuild trusted runtime objects from a validated persistence DTO.

    The persistence module intentionally knows nothing about this module.  This
    one-way adapter keeps the dependency graph acyclic while ensuring every
    restored call and stage is revalidated by the same domain constructors used
    for newly created jobs.
    """

    snapshot = FrozenPDFContext(
        pdf_sha256=str(state.snapshot["pdf_sha256"]),
        pages=tuple(_freeze(state.snapshot["pages"])),
        page_count=int(state.snapshot["page_count"]),
        content_fingerprint=str(state.snapshot["content_fingerprint"]),
    )
    calls = tuple(
        FrozenModelCall.create(
            call_id=str(call["call_id"]),
            task=str(call["task"]),
            messages=call["messages"],
            max_tokens=int(call["max_tokens"]),
            options=call["options"],
        )
        for call in state.stage["calls"]
    )
    stage = FrozenExtractionStage.create(
        str(state.stage["name"]),
        calls,
        str(state.stage["input_fingerprint"]),
        float(state.stage["created_at"]),
    )
    if not hmac.compare_digest(stage.stage_fingerprint, str(state.stage["stage_fingerprint"])):
        raise LiteratureExtractionJobError(
            "literature_job_state_invalid", "抽取任务恢复状态无效"
        )
    outputs = tuple(
        FrozenStageOutput(
            stage=str(item["stage"]),
            stage_fingerprint=str(item["stage_fingerprint"]),
            result=_freeze(item["result"]),
            result_fingerprint=str(item["result_fingerprint"]),
        )
        for item in state.stage_outputs
    )
    return _Job(
        token=state.token,
        session_digest=state.session_digest,
        paper_id=state.paper_id,
        paper=_freeze(state.paper),
        snapshot_handle=state.snapshot_ref,
        snapshot=snapshot,
        experiment_profile=_freeze(state.experiment_profile),
        chunks=tuple(tuple(_freeze(page) for page in chunk) for chunk in state.chunks),
        focuses=tuple(state.focuses),
        learning_guidance=state.learning_guidance,
        stage=stage,
        issued_at=state.issued_at,
        expires_at=state.expires_at,
        status=state.resume_status,
        stage_outputs=outputs,
        validated_quality_result=_freeze(state.validated_quality_result),
    )


@dataclass(frozen=True)
class _SnapshotRecord:
    source_path: str | None
    snapshot: ImmutablePDFSnapshot | None
    context: FrozenPDFContext
    snapshot_ref: str | None = None
    size: int = 0


class LiteraturePDFSnapshotAuthority:
    """Owns local path and binary bytes outside the public/job state machine."""

    def __init__(
        self,
        *,
        max_total_bytes: int = MAX_SNAPSHOT_STORE_BYTES,
        blob_store: SealedImmutablePDFBlobStore | None = None,
    ) -> None:
        if not MAX_SNAPSHOT_BYTES <= max_total_bytes <= 2 * 1024 * 1024 * 1024:
            raise ValueError("snapshot authority capacity is invalid")
        self._max_total_bytes = max_total_bytes
        self._blob_store = blob_store
        self._records: dict[str, _SnapshotRecord] = {}
        self._lock = threading.RLock()

    @property
    def persistent(self) -> bool:
        return self._blob_store is not None

    def capture(self, pdf_path: str, *, max_pages: int | None = None) -> tuple[str, FrozenPDFContext]:
        raw, context = _capture_pdf_snapshot(pdf_path, max_pages=max_pages)
        if len(raw) > MAX_SNAPSHOT_BYTES:
            raise LiteratureExtractionJobError("literature_pdf_too_large", "PDF 超出抽取快照限制")
        snapshot = ImmutablePDFSnapshot.create(raw)
        if not hmac.compare_digest(snapshot.sha256, context.pdf_sha256):
            raise LiteratureExtractionJobError("literature_pdf_invalid", "PDF 快照身份不一致")
        if self._blob_store is not None:
            try:
                handle = self._blob_store.put(raw, expected_sha256=context.pdf_sha256)
            except LiteratureSnapshotBlobError as exc:
                raise _snapshot_job_error(exc) from None
            record = _SnapshotRecord(None, None, context, handle, snapshot.size)
        else:
            handle = secrets.token_urlsafe(32)
            record = _SnapshotRecord(pdf_path, snapshot, context, None, snapshot.size)
        with self._lock:
            stored_bytes = sum(item.size for item in self._records.values())
            if stored_bytes + snapshot.size > self._max_total_bytes:
                if self._blob_store is not None:
                    self._blob_store.delete(handle)
                raise LiteratureExtractionJobError("literature_job_store_full", "抽取快照空间不足，请稍后重试")
            self._records[handle] = record
        return handle, context

    def assert_fresh(self, handle: str) -> None:
        with self._lock:
            record = self._records.get(handle)
        if record is None:
            raise LiteratureExtractionJobError("literature_job_expired", "抽取快照已过期")
        if record.snapshot_ref is not None:
            self._read_persisted(record)
        else:
            current = _read_stable_pdf_snapshot(record.source_path or "")
            if not hmac.compare_digest(record.context.pdf_sha256, current.sha256):
                raise LiteratureExtractionJobError("literature_source_stale", "原始 PDF 已发生变化")

    def snapshot_for_finalization(
        self, handle: str, *, expected_sha256: str
    ) -> ImmutablePDFSnapshot:
        """Return the private captured snapshot, never the source path."""

        with self._lock:
            record = self._records.get(handle)
        if record is None:
            raise LiteratureExtractionJobError("literature_job_expired", "抽取快照已过期")
        if not hmac.compare_digest(record.context.pdf_sha256, expected_sha256):
            raise LiteratureExtractionJobError("literature_source_stale", "PDF 快照身份不一致")
        if record.snapshot_ref is not None:
            return ImmutablePDFSnapshot.create(self._read_persisted(record))
        if record.snapshot is None:
            raise LiteratureExtractionJobError("literature_job_expired", "抽取快照已过期")
        record.snapshot.verified_bytes(expected_sha256)
        return record.snapshot

    def restore_persisted(self, handle: str, context: FrozenPDFContext) -> None:
        if self._blob_store is None:
            raise LiteratureExtractionJobError(
                "literature_persistence_unavailable", "抽取快照持久化不可用"
            )
        provisional = _SnapshotRecord(None, None, context, handle)
        raw = self._read_persisted(provisional)
        record = _SnapshotRecord(None, None, context, handle, len(raw))
        with self._lock:
            if handle in self._records:
                raise LiteratureExtractionJobError("literature_job_duplicate", "抽取任务已恢复")
            if sum(item.size for item in self._records.values()) + record.size > self._max_total_bytes:
                raise LiteratureExtractionJobError("literature_job_store_full", "抽取快照空间不足，请稍后重试")
            self._records[handle] = record

    def _read_persisted(self, record: _SnapshotRecord) -> bytes:
        if self._blob_store is None or record.snapshot_ref is None:
            raise LiteratureExtractionJobError("literature_job_expired", "抽取快照已过期")
        try:
            return self._blob_store.get(
                record.snapshot_ref, expected_sha256=record.context.pdf_sha256
            )
        except LiteratureSnapshotBlobError as exc:
            raise _snapshot_job_error(exc) from None

    def release(self, handle: str) -> None:
        with self._lock:
            record = self._records.pop(handle, None)
        if record is not None and record.snapshot_ref is not None and self._blob_store is not None:
            try:
                self._blob_store.delete(record.snapshot_ref)
            except LiteratureSnapshotBlobError:
                pass


def _snapshot_job_error(error: LiteratureSnapshotBlobError) -> LiteratureExtractionJobError:
    code = (
        "literature_source_stale"
        if error.code in {"literature_snapshot_corrupt", "literature_snapshot_not_found"}
        else "literature_persistence_unavailable"
    )
    message = "PDF 快照身份不一致" if code == "literature_source_stale" else "抽取快照持久化不可用"
    return LiteratureExtractionJobError(code, message)


def _capture_pdf_snapshot(
    pdf_path: str, *, max_pages: int | None = None
) -> tuple[bytes, FrozenPDFContext]:
    """Capture bytes and page text from the same verified file descriptor read."""
    raw = _read_stable_pdf_snapshot(pdf_path)
    pages: list[Mapping[str, Any]] = []
    try:
        with fitz.open(stream=raw.content, filetype="pdf") as document:
            page_count = len(document)
            if max_pages is not None and page_count > max_pages:
                raise LiteratureExtractionJobError(
                    "literature_pdf_page_limit_exceeded",
                    f"PDF 共 {page_count} 页，当前完整提取最多支持 {max_pages} 页；未创建截断任务",
                )
            limit = page_count
            for index in range(limit):
                text = document[index].get_text("text").strip()
                if _is_reference_dominant(text):
                    continue
                pages.append(MappingProxyType({"page": index + 1, "text": text}))
    except LiteratureExtractionJobError:
        raise
    except Exception as exc:
        raise LiteratureExtractionJobError("literature_pdf_invalid", "PDF 无法安全解析") from exc
    if not pages:
        raise LiteratureExtractionJobError("literature_pdf_empty", "PDF 没有可用于抽取的正文")
    fingerprint = hashlib.sha256(_canonical_bytes({
        "pdf_sha256": raw.sha256,
        "page_count": page_count,
        "pages": pages,
    })).hexdigest()
    return raw.content, FrozenPDFContext(raw.sha256, tuple(pages), page_count, fingerprint)


def capture_pdf_snapshot(pdf_path: str, *, max_pages: int | None = None) -> FrozenPDFContext:
    """Public helper returns immutable derived context, never binary bytes or paths."""

    return _capture_pdf_snapshot(pdf_path, max_pages=max_pages)[1]


class LiteratureExtractionJobStore:
    """Process-local staged authority; it never writes scientific state."""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        ttl_seconds: int | None = None,
        max_jobs: int = MAX_JOBS,
        max_session_jobs: int = MAX_SESSION_JOBS,
        session_key: bytes | None = None,
        snapshots: LiteraturePDFSnapshotAuthority | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        effective_ttl = (
            DEFAULT_PERSISTENT_TTL_SECONDS
            if ttl_seconds is None and snapshots is not None and snapshots.persistent
            else DEFAULT_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        )
        self._ttl = effective_ttl
        self._max_jobs = max_jobs
        self._max_session_jobs = max_session_jobs
        self._key = session_key or secrets.token_bytes(32)
        self._snapshots = snapshots or LiteraturePDFSnapshotAuthority()
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.RLock()
        if not 1 <= effective_ttl <= MAX_JOB_TTL_SECONDS:
            raise ValueError("ttl_seconds must be between 1 and 86400")
        if not 1 <= max_jobs <= 128 or not 1 <= max_session_jobs <= max_jobs:
            raise ValueError("job capacity limits are invalid")

    def _session_digest(self, session_id: str) -> str:
        if not isinstance(session_id, str) or not session_id or len(session_id) > 256:
            raise LiteratureExtractionJobError("literature_session_invalid", "抽取会话无效")
        return hmac.new(self._key, session_id.encode("utf-8"), hashlib.sha256).hexdigest()

    def _cleanup(self, now_value: float) -> None:
        for token, job in list(self._jobs.items()):
            claim_expired = job.claimed and (job.claim_expires_at or 0) <= now_value
            if job.expires_at <= now_value and (not job.claimed or claim_expired):
                self._jobs.pop(token, None)
                self._snapshots.release(job.snapshot_handle)

    def create(
        self,
        source: PaperSource,
        *,
        paper_id: int,
        session_id: str,
        max_pages: int | None = None,
        chunk_pages: int = 2,
        learning_guidance: str = "",
    ) -> dict[str, Any]:
        if not isinstance(paper_id, int) or paper_id < 1 or not 1 <= chunk_pages <= 20:
            raise LiteratureExtractionJobError("literature_request_invalid", "文献抽取请求无效")
        if len(learning_guidance) > 64_000:
            raise LiteratureExtractionJobError("literature_request_invalid", "抽取规则超出限制")
        paper_raw = source.get_paper(paper_id)
        if not paper_raw or not paper_raw.get("pdf_path"):
            raise LiteratureExtractionJobError("literature_pdf_missing", "当前文献没有可读取的 PDF")
        source_path = str(paper_raw["pdf_path"])
        snapshot_handle, snapshot = self._snapshots.capture(source_path, max_pages=max_pages)
        public_paper = MappingProxyType({
            "title": str(paper_raw.get("title") or "")[:500],
            "doi": str(paper_raw.get("doi") or "")[:300] or None,
        })
        page_list = [_plain(page) for page in snapshot.pages]
        profile = _freeze(classify_experiment_types(dict(public_paper), pages=page_list))
        focuses = tuple(extraction_focuses_for_profile(_plain(profile)) or BASE_EXTRACTION_FOCUSES)
        chunks = tuple(tuple(chunk) for chunk in _page_chunks(page_list, chunk_pages))
        extraction_paper = {**dict(public_paper), "_recognition_profile": _plain(profile)}
        calls: list[FrozenModelCall] = []
        for branch, instruction in (("a", ROLE_A), ("b", ROLE_B)):
            for chunk_index, chunk in enumerate(chunks, start=1):
                for focus_index, focus in enumerate(focuses, start=1):
                    messages = _extraction_messages(
                        extraction_paper, list(chunk), focus, learning_guidance
                    )
                    messages[0]["content"] = f"{instruction}\n\n{messages[0]['content']}"
                    calls.append(FrozenModelCall.create(
                        call_id=f"{branch}-chunk-{chunk_index}-focus-{focus_index}",
                        task="extraction",
                        messages=messages,
                        max_tokens=16_000,
                        options={"thinking": False, "temperature": 0.1 if branch == "a" else 0.45},
                    ))
        now_value = self._clock.now()
        stage = FrozenExtractionStage.create(
            "initial_focus", calls, snapshot.content_fingerprint, now_value
        )
        session_digest = self._session_digest(session_id)
        try:
            with self._lock:
                self._cleanup(now_value)
                if len(self._jobs) >= self._max_jobs:
                    raise LiteratureExtractionJobError("literature_job_store_full", "抽取任务繁忙，请稍后重试")
                if sum(job.session_digest == session_digest for job in self._jobs.values()) >= self._max_session_jobs:
                    raise LiteratureExtractionJobError("literature_job_store_full", "当前会话抽取任务过多")
                token = secrets.token_urlsafe(32)
                self._jobs[token] = _Job(
                    token=token,
                    session_digest=session_digest,
                    paper_id=paper_id,
                    paper=public_paper,
                    snapshot_handle=snapshot_handle,
                    snapshot=snapshot,
                    experiment_profile=profile,
                    chunks=chunks,
                    focuses=focuses,
                    learning_guidance=learning_guidance,
                    stage=stage,
                    issued_at=now_value,
                    expires_at=now_value + self._ttl,
                )
        except Exception:
            self._snapshots.release(snapshot_handle)
            raise
        return self.summary(token, session_id=session_id)

    def summary(self, job_token: str, *, session_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._get(job_token, session_id)
            pending_calls = job.stage.calls if job.status not in {"validated", "finalized"} else ()
            return {
                "schema_version": SUMMARY_SCHEMA_VERSION,
                "job_token": job.token,
                "stage": job.status if job.status in {"validated", "finalized"} else job.stage.name,
                "paper": dict(job.paper),
                "call_count": len(pending_calls),
                "max_token_budget": sum(call.max_tokens for call in pending_calls),
                "sending_scope": {
                    "pdf_page_count": len(job.snapshot.pages),
                    "page_block_count": len(job.chunks),
                    "branch_count": 2,
                    "focus_count": len(job.focuses),
                },
                "possible_charges": bool(pending_calls),
                "requires_confirmation": bool(pending_calls),
                "expires_at": job.expires_at,
                "transient": True,
                "persistence_allowed": False,
            }

    def export_private_state(self, job_token: str, *, session_id: str) -> bytes:
        """Return deterministic private recovery state, never PDF bytes or paths."""

        if not self._snapshots.persistent:
            raise LiteratureExtractionJobError(
                "literature_persistence_unavailable", "抽取任务持久化不可用"
            )
        with self._lock:
            job = self._get(job_token, session_id)
            try:
                return encode_job_private_state(job)
            except LiteratureJobPersistenceError as exc:
                raise LiteratureExtractionJobError(exc.code, exc.safe_message) from exc

    def restore_private_state(
        self,
        payload: bytes,
        *,
        session_id: str,
        allow_authenticated_session_rebind: bool = False,
    ) -> dict[str, Any]:
        """Restore a paused/prepared job after authenticating its sealed PDF blob."""

        if not self._snapshots.persistent:
            raise LiteratureExtractionJobError(
                "literature_persistence_unavailable", "抽取任务持久化不可用"
            )
        if not isinstance(allow_authenticated_session_rebind, bool):
            raise LiteratureExtractionJobError(
                "literature_job_state_invalid", "抽取任务恢复状态无效"
            )
        try:
            decoded = decode_job_private_state(payload)
            job = _job_from_decoded_state(decoded)
        except LiteratureJobPersistenceError as exc:
            raise LiteratureExtractionJobError(exc.code, exc.safe_message) from exc
        expected_session = self._session_digest(session_id)
        if not hmac.compare_digest(job.session_digest, expected_session):
            if not allow_authenticated_session_rebind:
                raise LiteratureExtractionJobError(
                    "literature_job_invalid", "抽取任务不属于当前会话"
                )
            # This switch is intentionally private to the authenticated
            # checkpoint recovery path. A new desktop process has a new
            # session and JobStore HMAC key; the sealed checkpoint, not an HTTP
            # caller, authorizes rebinding the restored job to that process.
            job.session_digest = expected_session
        now_value = self._clock.now()
        if job.expires_at <= now_value:
            raise LiteratureExtractionJobError("literature_job_expired", "抽取任务已过期")
        with self._lock:
            self._cleanup(now_value)
            if job.token in self._jobs:
                raise LiteratureExtractionJobError("literature_job_duplicate", "抽取任务已恢复")
            if len(self._jobs) >= self._max_jobs:
                raise LiteratureExtractionJobError("literature_job_store_full", "抽取任务繁忙，请稍后重试")
            if sum(item.session_digest == expected_session for item in self._jobs.values()) >= self._max_session_jobs:
                raise LiteratureExtractionJobError("literature_job_store_full", "当前会话抽取任务过多")
            self._snapshots.restore_persisted(job.snapshot_handle, job.snapshot)
            self._jobs[job.token] = job
        return self.summary(job.token, session_id=session_id)

    def _get(self, job_token: str, session_id: str) -> _Job:
        now_value = self._clock.now()
        self._cleanup(now_value)
        job = self._jobs.get(job_token)
        if not job or job.expires_at <= now_value:
            raise LiteratureExtractionJobError("literature_job_expired", "抽取任务已过期")
        if not hmac.compare_digest(job.session_digest, self._session_digest(session_id)):
            raise LiteratureExtractionJobError("literature_job_invalid", "抽取任务不属于当前会话")
        return job

    def claim_stage(self, job_token: str, *, session_id: str) -> FrozenExtractionStage:
        with self._lock:
            job = self._get(job_token, session_id)
            if job.claimed:
                raise LiteratureExtractionJobError("literature_stage_busy", "抽取阶段正在执行")
            job.claimed = True
            job.claim_expires_at = self._clock.now() + MAX_EXECUTION_LEASE_SECONDS
            job.status = "executing"
            return job.stage

    def peek_stage(self, job_token: str, *, session_id: str) -> FrozenExtractionStage:
        with self._lock:
            job = self._get(job_token, session_id)
            if job.claimed or job.status != "prepared":
                raise LiteratureExtractionJobError("literature_stage_busy", "抽取阶段正在执行")
            return job.stage

    def fail_stage(self, job_token: str, *, session_id: str) -> None:
        with self._lock:
            job = self._get(job_token, session_id)
            job.claimed = False
            job.claim_expires_at = None
            job.status = "prepared"

    def complete_stage(
        self,
        job_token: str,
        *,
        session_id: str,
        completed_stage_fingerprint: str,
        raw_results: Sequence[Mapping[str, Any]],
        planner: LiteratureStagePlanner,
    ) -> dict[str, Any]:
        """Validate one stage and freeze its successor without exposing a renderer plan seam."""
        with self._lock:
            job = self._get(job_token, session_id)
            if not job.claimed or not hmac.compare_digest(
                job.stage.stage_fingerprint, completed_stage_fingerprint
            ):
                raise LiteratureExtractionJobError("literature_stage_stale", "抽取阶段状态已变化")
        try:
            if len(raw_results) != len(job.stage.calls):
                raise LiteratureExtractionJobError(
                    "literature_stage_invalid", "模型返回数量与冻结计划不一致"
                )
            _validate_intermediate(raw_results)
            if len(_canonical_bytes(raw_results)) > MAX_STAGE_OUTPUT_BYTES:
                raise LiteratureExtractionJobError(
                    "literature_stage_too_large", "模型阶段返回内容超出限制"
                )
            context = LiteratureStageContext(
                stage=job.stage,
                paper=job.paper,
                pages=job.snapshot.pages,
                chunks=job.chunks,
                focuses=job.focuses,
                learning_guidance=job.learning_guidance,
                experiment_profile=job.experiment_profile,
                prior_outputs=job.stage_outputs,
                snapshot_fingerprint=job.snapshot.content_fingerprint,
            )
            planned = planner.plan_next(context, tuple(_freeze(item) for item in raw_results))
        except LiteratureExtractionJobError:
            self.fail_stage(job_token, session_id=session_id)
            raise
        except Exception as exc:
            self.fail_stage(job_token, session_id=session_id)
            raise LiteratureExtractionJobError(
                "literature_stage_invalid", "模型阶段结果未通过科学验证"
            ) from exc
        try:
            with self._lock:
                job = self._get(job_token, session_id)
                if not job.claimed or not hmac.compare_digest(
                    job.stage.stage_fingerprint, completed_stage_fingerprint
                ):
                    raise LiteratureExtractionJobError("literature_stage_stale", "抽取阶段状态已变化")
                current_index = STAGE_ORDER.index(job.stage.name)
                valid_next = (
                    current_index + 1 < len(STAGE_ORDER)
                    and STAGE_ORDER[current_index + 1] == planned.next_stage
                ) or (
                    job.stage.name == "adversarial_branches"
                    and planned.next_stage == "validated"
                    and not planned.calls
                )
                if not valid_next:
                    raise LiteratureExtractionJobError("literature_stage_invalid", "抽取阶段顺序无效")
                frozen_result = _freeze(planned.validated_result)
                _validate_intermediate(frozen_result)
                result_bytes = _canonical_bytes(frozen_result)
                if len(result_bytes) > MAX_STAGE_OUTPUT_BYTES:
                    raise LiteratureExtractionJobError(
                        "literature_stage_too_large", "抽取阶段中间结果超出限制"
                    )
                if sum(len(_canonical_bytes(item.result)) for item in job.stage_outputs) + len(result_bytes) > MAX_JOB_OUTPUT_BYTES:
                    raise LiteratureExtractionJobError(
                        "literature_stage_too_large", "抽取任务中间结果总量超出限制"
                    )
                output = FrozenStageOutput(
                    stage=job.stage.name,
                    stage_fingerprint=job.stage.stage_fingerprint,
                    result=frozen_result,
                    result_fingerprint=hashlib.sha256(result_bytes).hexdigest(),
                )
                job.stage_outputs = (*job.stage_outputs, output)
                if planned.next_stage == "validated":
                    if planned.calls:
                        raise LiteratureExtractionJobError(
                            "literature_stage_invalid", "已验证阶段不能包含模型调用"
                        )
                    job.validated_quality_result = frozen_result
                    if not self._snapshots.persistent:
                        job.validated_package = self._validated_package(job, frozen_result)
                    job.claimed = False
                    job.claim_expires_at = None
                    job.status = "validated"
                    return self.summary(job_token, session_id=session_id)
                next_frozen = FrozenExtractionStage.create(
                    planned.next_stage, planned.calls, planned.input_fingerprint, self._clock.now()
                )
                job.stage = next_frozen
                job.claimed = False
                job.claim_expires_at = None
                job.status = "prepared"
                return self.summary(job_token, session_id=session_id)
        except Exception:
            self.fail_stage(job_token, session_id=session_id)
            raise

    def advance_local_stage(
        self,
        job_token: str,
        *,
        session_id: str,
        planner: LiteratureStagePlanner,
    ) -> dict[str, Any]:
        stage = self.claim_stage(job_token, session_id=session_id)
        if stage.name not in LOCAL_CAPABLE_STAGES or stage.calls:
            self.fail_stage(job_token, session_id=session_id)
            raise LiteratureExtractionJobError("literature_stage_invalid", "当前阶段不是本地阶段")
        return self.complete_stage(
            job_token,
            session_id=session_id,
            completed_stage_fingerprint=stage.stage_fingerprint,
            raw_results=(),
            planner=planner,
        )

    def mark_validated(
        self,
        job_token: str,
        *,
        session_id: str,
        completed_stage_fingerprint: str,
        quality_result: Mapping[str, Any],
    ) -> None:
        with self._lock:
            job = self._get(job_token, session_id)
            if job.stage.name != "third_review" or not job.claimed:
                raise LiteratureExtractionJobError("literature_stage_invalid", "抽取结果尚未完成质量验证")
            if not hmac.compare_digest(job.stage.stage_fingerprint, completed_stage_fingerprint):
                raise LiteratureExtractionJobError("literature_stage_stale", "抽取阶段状态已变化")
            frozen_quality = _freeze(quality_result)
            _validate_intermediate(frozen_quality)
            job.validated_quality_result = frozen_quality
            if not self._snapshots.persistent:
                job.validated_package = self._validated_package(job, frozen_quality)
            job.claimed = False
            job.claim_expires_at = None
            job.status = "validated"

    def assert_source_fresh(self, job_token: str, *, session_id: str) -> None:
        """Authorization-time freshness check; stages themselves use only captured bytes."""

        with self._lock:
            job = self._get(job_token, session_id)
            snapshot_handle = job.snapshot_handle
        self._snapshots.assert_fresh(snapshot_handle)

    def stage_fingerprint(self, job_token: str) -> str:
        """Snapshot-authority hook; possession never grants execution or summary access."""

        with self._lock:
            self._cleanup(self._clock.now())
            job = self._jobs.get(job_token)
            if not job or job.status != "prepared" or job.claimed:
                raise LiteratureExtractionJobError("literature_job_expired", "抽取阶段已变化")
            return job.stage.stage_fingerprint

    def finalize(
        self,
        job_token: str,
        *,
        session_id: str,
        finalizer: AtomicLiteratureFinalizer,
    ) -> Mapping[str, Any]:
        with self._lock:
            job = self._get(job_token, session_id)
            quality_result = (
                job.validated_package.quality_result
                if job.validated_package is not None
                else job.validated_quality_result
            )
            if job.claimed or job.status != "validated" or quality_result is None:
                raise LiteratureExtractionJobError("literature_not_validated", "抽取结果尚未通过全部质量门")
            if not isinstance(finalizer, TrustedAtomicLiteratureFinalizer):
                raise LiteratureExtractionJobError(
                    "literature_commit_unavailable", "当前未安装受信原子保存组件"
                )
            job.claimed = True
            job.claim_expires_at = self._clock.now() + MAX_EXECUTION_LEASE_SECONDS
            snapshot_handle = job.snapshot_handle
        try:
            self._snapshots.assert_fresh(snapshot_handle)
            package = self._validated_package(job, quality_result)
            if job.validated_package is not None and package.pdf_snapshot is not job.validated_package.pdf_snapshot:
                raise LiteratureExtractionJobError(
                    "literature_source_stale", "抽取 PDF 快照已脱离受控来源"
                )
            result = finalizer.finalize(package)
            _validate_intermediate(result)
        except LiteratureExtractionJobError:
            with self._lock:
                current = self._jobs.get(job_token)
                if current is job:
                    current.claimed = False
                    current.claim_expires_at = None
            raise
        except Exception as exc:
            with self._lock:
                current = self._jobs.get(job_token)
                if current is job:
                    current.claimed = False
                    current.claim_expires_at = None
            raise LiteratureExtractionJobError(
                "literature_commit_failed", "抽取结果未能原子保存，未发布任何新科学记录"
            ) from exc
        # Publication is idempotent, but the durable checkpoint still has to
        # record completion after this method returns.  Keep the validated job
        # and sealed PDF alive until that acknowledgement succeeds; deleting
        # them here would make a crash between the DB commit and checkpoint CAS
        # impossible to recover without repeating or losing task state.
        with self._lock:
            current = self._jobs.get(job_token)
            if current is job:
                current.claimed = False
                current.claim_expires_at = None
        return MappingProxyType(dict(_plain(result)))

    def acknowledge_finalized(self, job_token: str, *, session_id: str) -> None:
        """Release a published task only after its durable completion receipt."""

        with self._lock:
            job = self._get(job_token, session_id)
            quality_result = (
                job.validated_package.quality_result
                if job.validated_package is not None
                else job.validated_quality_result
            )
            if job.claimed or job.status != "validated" or quality_result is None:
                raise LiteratureExtractionJobError(
                    "literature_not_validated", "抽取结果尚未通过全部质量门"
                )
            removed = self._jobs.pop(job_token, None)
        if removed is not None:
            self._snapshots.release(removed.snapshot_handle)

    def _validated_package(self, job: _Job, quality_result: Any) -> ValidatedLiteraturePackage:
        return ValidatedLiteraturePackage(
            paper_id=job.paper_id,
            paper=job.paper,
            snapshot_fingerprint=job.snapshot.content_fingerprint,
            pdf_sha256=job.snapshot.pdf_sha256,
            pdf_snapshot=self._snapshots.snapshot_for_finalization(
                job.snapshot_handle,
                expected_sha256=job.snapshot.pdf_sha256,
            ),
            experiment_profile=job.experiment_profile,
            quality_result=quality_result,
        )

    def cancel(self, job_token: str, *, session_id: str) -> None:
        with self._lock:
            job = self._get(job_token, session_id)
            if job.claimed:
                raise LiteratureExtractionJobError("literature_stage_busy", "抽取阶段正在执行")
            self._jobs.pop(job_token, None)
        self._snapshots.release(job.snapshot_handle)
