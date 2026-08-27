from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


PRIVATE_STATE_SCHEMA_VERSION = "literature-extraction-private-state-v1"
MAX_PRIVATE_STATE_BYTES = 32 * 1024 * 1024
MAX_JOB_TTL_SECONDS = 24 * 60 * 60
_MODEL_STAGES = (
    "initial_focus",
    "coverage_gap",
    "coverage_verification",
    "adversarial_branches",
    "third_review",
)
_STAGE_ORDER = _MODEL_STAGES + ("validated", "finalized")
_ALLOWED_TASKS = frozenset({"extraction", "verification", "analysis", "localization"})
_SNAPSHOT_REF_RE = re.compile(r"^pdfsnap_[A-Za-z0-9_-]{32,96}$")
_JOB_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_LOCAL_PATH_RE = re.compile(
    r"(?:^|[\s='\"])(?:/Users/|/home/|/private/|/tmp/|/var/|/etc/|/usr/|/root/|"
    r"/Applications/|/Library/|/System/|[A-Za-z]:[\\/]|file:|sqlite:|\\\\)",
    re.IGNORECASE,
)
_SENSITIVE_KEYS = frozenset(
    {
        "api_key", "credential_ref", "endpoint", "pdf_path", "file_path",
        "local_path", "selection_id", "zotero_key", "database_path",
    }
)


class LiteratureJobPersistenceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.safe_message = message
        super().__init__(message)


@dataclass(frozen=True)
class DecodedLiteratureJobState:
    token: str
    session_digest: str
    paper_id: int
    paper: Mapping[str, Any]
    snapshot_ref: str
    snapshot: Mapping[str, Any]
    experiment_profile: Mapping[str, Any]
    chunks: tuple[tuple[Mapping[str, Any], ...], ...]
    focuses: tuple[str, ...]
    learning_guidance: str
    stage: Mapping[str, Any]
    issued_at: float
    expires_at: float
    resume_status: str
    stage_outputs: tuple[Mapping[str, Any], ...]
    validated_quality_result: Any | None


def encode_job_private_state(job: object) -> bytes:
    quality = (
        job.validated_package.quality_result
        if job.validated_package is not None
        else job.validated_quality_result
    )
    state = {
        "schema_version": PRIVATE_STATE_SCHEMA_VERSION,
        "job_token": job.token,
        "session_digest": job.session_digest,
        "paper_id": job.paper_id,
        "paper": _plain(job.paper),
        "snapshot_ref": job.snapshot_handle,
        "snapshot": {
            "pdf_sha256": job.snapshot.pdf_sha256,
            "pages": _plain(job.snapshot.pages),
            "page_count": job.snapshot.page_count,
            "content_fingerprint": job.snapshot.content_fingerprint,
        },
        "experiment_profile": _plain(job.experiment_profile),
        "chunks": _plain(job.chunks),
        "focuses": list(job.focuses),
        "learning_guidance": job.learning_guidance,
        "stage": _stage_state(job.stage),
        "issued_at": job.issued_at,
        "expires_at": job.expires_at,
        "resume_status": "validated" if job.status == "validated" else "prepared",
        "stage_outputs": [
            {
                "stage": item.stage,
                "stage_fingerprint": item.stage_fingerprint,
                "result": _plain(item.result),
                "result_fingerprint": item.result_fingerprint,
            }
            for item in job.stage_outputs
        ],
        "validated_quality_result": _plain(quality),
    }
    state["state_fingerprint"] = _digest(state)
    _validate_private_value(state)
    payload = _canonical_bytes(state)
    if len(payload) > MAX_PRIVATE_STATE_BYTES:
        raise LiteratureJobPersistenceError(
            "literature_job_state_too_large", "抽取任务恢复状态超出限制"
        )
    return payload


def decode_job_private_state(payload: bytes) -> DecodedLiteratureJobState:
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_PRIVATE_STATE_BYTES:
        _invalid()
    try:
        value = json.loads(payload.decode("utf-8"))
        return _decode_state(value)
    except LiteratureJobPersistenceError:
        raise
    except Exception:
        _invalid()


def _decode_state(value: Any) -> DecodedLiteratureJobState:
    expected = {
        "schema_version", "job_token", "session_digest", "paper_id", "paper",
        "snapshot_ref", "snapshot", "experiment_profile", "chunks", "focuses",
        "learning_guidance", "stage", "issued_at", "expires_at", "resume_status",
        "stage_outputs", "validated_quality_result", "state_fingerprint",
    }
    _exact_keys(value, expected)
    if value["schema_version"] != PRIVATE_STATE_SCHEMA_VERSION:
        _invalid()
    fingerprint = value["state_fingerprint"]
    unsigned = {key: item for key, item in value.items() if key != "state_fingerprint"}
    if not _valid_sha256(fingerprint) or not hmac.compare_digest(
        fingerprint, _digest(unsigned)
    ):
        _invalid()
    _validate_private_value(value)
    token, session_digest, snapshot_ref = (
        value["job_token"], value["session_digest"], value["snapshot_ref"]
    )
    if (
        not isinstance(token, str)
        or _JOB_TOKEN_RE.fullmatch(token) is None
        or not _valid_sha256(session_digest)
        or not isinstance(snapshot_ref, str)
        or _SNAPSHOT_REF_RE.fullmatch(snapshot_ref) is None
    ):
        _invalid()
    paper_id = value["paper_id"]
    if isinstance(paper_id, bool) or not isinstance(paper_id, int) or paper_id < 1:
        _invalid()
    paper = value["paper"]
    _exact_keys(paper, {"title", "doi"})
    if (
        not isinstance(paper["title"], str)
        or len(paper["title"]) > 500
        or not (
            paper["doi"] is None
            or isinstance(paper["doi"], str) and len(paper["doi"]) <= 300
        )
    ):
        _invalid()
    snapshot = _validate_snapshot(value["snapshot"])
    chunks = _validate_chunks(value["chunks"], snapshot["pages"])
    focuses = value["focuses"]
    if (
        not isinstance(focuses, list)
        or not focuses
        or len(focuses) > 128
        or any(not isinstance(item, str) or not item or len(item) > 1_000 for item in focuses)
    ):
        _invalid()
    guidance = value["learning_guidance"]
    if not isinstance(guidance, str) or len(guidance) > 64_000:
        _invalid()
    stage = _validate_stage(value["stage"])
    outputs = _validate_outputs(value["stage_outputs"], stage["name"])
    if not outputs and not hmac.compare_digest(
        stage["input_fingerprint"], snapshot["content_fingerprint"]
    ):
        _invalid()
    status, quality = value["resume_status"], value["validated_quality_result"]
    if status not in {"prepared", "validated"}:
        _invalid()
    if status == "validated":
        if stage["name"] not in {"adversarial_branches", "third_review"} or not isinstance(
            quality, Mapping
        ):
            _invalid()
    elif quality is not None:
        _invalid()
    issued_at, expires_at = value["issued_at"], value["expires_at"]
    if (
        not _valid_time(issued_at)
        or not _valid_time(expires_at)
        or expires_at <= issued_at
        or expires_at - issued_at > MAX_JOB_TTL_SECONDS
    ):
        _invalid()
    profile = value["experiment_profile"]
    if not isinstance(profile, Mapping):
        _invalid()
    return DecodedLiteratureJobState(
        token=token,
        session_digest=session_digest,
        paper_id=paper_id,
        paper=_plain(paper),
        snapshot_ref=snapshot_ref,
        snapshot=_plain(snapshot),
        experiment_profile=_plain(profile),
        chunks=chunks,
        focuses=tuple(focuses),
        learning_guidance=guidance,
        stage=_plain(stage),
        issued_at=float(issued_at),
        expires_at=float(expires_at),
        resume_status=status,
        stage_outputs=tuple(_plain(item) for item in outputs),
        validated_quality_result=_plain(quality),
    )


def _stage_state(stage: object) -> dict[str, Any]:
    return {
        "name": stage.name,
        "input_fingerprint": stage.input_fingerprint,
        "stage_fingerprint": stage.stage_fingerprint,
        "created_at": stage.created_at,
        "calls": [
            {
                "call_id": call.call_id,
                "task": call.task,
                "messages": _plain(call.messages),
                "max_tokens": call.max_tokens,
                "options": _plain(call.options),
                "call_digest": call.call_digest,
            }
            for call in stage.calls
        ],
    }


def _validate_snapshot(value: Any) -> Mapping[str, Any]:
    _exact_keys(value, {"pdf_sha256", "pages", "page_count", "content_fingerprint"})
    pdf_sha256, pages, page_count = value["pdf_sha256"], value["pages"], value["page_count"]
    if (
        not _valid_sha256(pdf_sha256)
        or isinstance(page_count, bool)
        or not isinstance(page_count, int)
        or page_count < 1
        or not isinstance(pages, list)
        or not pages
        or len(pages) > page_count
    ):
        _invalid()
    numbers = []
    for page in pages:
        _exact_keys(page, {"page", "text"})
        if (
            isinstance(page["page"], bool)
            or not isinstance(page["page"], int)
            or not 1 <= page["page"] <= page_count
            or not isinstance(page["text"], str)
        ):
            _invalid()
        numbers.append(page["page"])
    if numbers != sorted(set(numbers)):
        _invalid()
    expected = _digest(
        {"pdf_sha256": pdf_sha256, "page_count": page_count, "pages": pages}
    )
    if not _valid_sha256(value["content_fingerprint"]) or not hmac.compare_digest(
        expected, value["content_fingerprint"]
    ):
        _invalid()
    return value


def _validate_chunks(value: Any, pages: list[Mapping[str, Any]]) -> tuple:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(chunk, list) for chunk in value)
        or [page for chunk in value for page in chunk] != pages
    ):
        _invalid()
    return tuple(tuple(_plain(page) for page in chunk) for chunk in value)


def _validate_stage(value: Any) -> Mapping[str, Any]:
    _exact_keys(
        value,
        {"name", "input_fingerprint", "stage_fingerprint", "created_at", "calls"},
    )
    name, calls = value["name"], value["calls"]
    if (
        name not in _MODEL_STAGES
        or not _valid_sha256(value["input_fingerprint"])
        or not _valid_sha256(value["stage_fingerprint"])
        or not _valid_time(value["created_at"])
        or not isinstance(calls, list)
        or len(calls) > 512
        or (name in {"initial_focus", "coverage_gap"} and not calls)
    ):
        _invalid()
    digests, call_ids, tokens = [], [], 0
    for call in calls:
        _exact_keys(
            call,
            {"call_id", "task", "messages", "max_tokens", "options", "call_digest"},
        )
        if (
            not isinstance(call["call_id"], str)
            or not call["call_id"]
            or len(call["call_id"]) > 120
            or call["task"] not in _ALLOWED_TASKS
            or not isinstance(call["messages"], list)
            or not isinstance(call["options"], Mapping)
            or isinstance(call["max_tokens"], bool)
            or not isinstance(call["max_tokens"], int)
            or not 1 <= call["max_tokens"] <= 32_000
        ):
            _invalid()
        expected = _digest(
            {
                "call_id": call["call_id"], "task": call["task"],
                "messages": call["messages"], "max_tokens": call["max_tokens"],
                "options": call["options"],
            }
        )
        if not _valid_sha256(call["call_digest"]) or not hmac.compare_digest(
            expected, call["call_digest"]
        ):
            _invalid()
        call_ids.append(call["call_id"])
        digests.append(call["call_digest"])
        tokens += call["max_tokens"]
    if len(call_ids) != len(set(call_ids)) or tokens > 8_200_000:
        _invalid()
    expected_stage = _digest(
        {"name": name, "input_fingerprint": value["input_fingerprint"], "calls": digests}
    )
    if not hmac.compare_digest(expected_stage, value["stage_fingerprint"]):
        _invalid()
    return value


def _validate_outputs(value: Any, current_stage: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        _invalid()
    outputs, indexes = [], []
    for item in value:
        _exact_keys(
            item,
            {"stage", "stage_fingerprint", "result", "result_fingerprint"},
        )
        if (
            item["stage"] not in _MODEL_STAGES
            or not _valid_sha256(item["stage_fingerprint"])
            or not _valid_sha256(item["result_fingerprint"])
            or not hmac.compare_digest(_digest(item["result"]), item["result_fingerprint"])
        ):
            _invalid()
        indexes.append(_STAGE_ORDER.index(item["stage"]))
        outputs.append(item)
    if indexes != sorted(set(indexes)) or any(
        index > _STAGE_ORDER.index(current_stage) for index in indexes
    ):
        _invalid()
    return tuple(outputs)


def _validate_private_value(value: Any, *, depth: int = 0) -> None:
    if depth > 10:
        _invalid()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold() in _SENSITIVE_KEYS:
                _invalid()
            _validate_private_value(item, depth=depth + 1)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _validate_private_value(item, depth=depth + 1)
    elif isinstance(value, str) and _LOCAL_PATH_RE.search(value):
        _invalid()


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain(item) for item in value]
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _plain(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _exact_keys(value: Any, expected: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        _invalid()


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _valid_time(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _invalid() -> None:
    raise LiteratureJobPersistenceError(
        "literature_job_state_invalid", "抽取任务恢复状态无效"
    )


__all__ = [
    "DecodedLiteratureJobState",
    "LiteratureJobPersistenceError",
    "MAX_PRIVATE_STATE_BYTES",
    "PRIVATE_STATE_SCHEMA_VERSION",
    "decode_job_private_state",
    "encode_job_private_state",
]
