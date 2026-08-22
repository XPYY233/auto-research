from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from .db import EvidenceDB
from .federated_search import _LOCAL_REFERENCE_RE
from .quality_pipeline import quality_candidate_snapshot, review_quality_candidate
from auto_research.product.portable_repository import stable_paper_uid


SCHEMA_VERSION = "review-queue-v1"
RESULT_SCHEMA_VERSION = "review-result-v1"
WORKSPACE_SOURCE_ID = "workspace"
MAX_QUEUE_ITEMS = 500
MAX_TOKEN_RECORDS = 1024
MAX_TEXT = 2000
MAX_EXCERPT = 800
MAX_LIST_ITEMS = 32
_ENTITY_MAP = {"data": "item", "finding": "finding", "table": "table", "figure": "figure"}
_TOKEN_RE = re.compile(r"rq_[A-Za-z0-9_-]{32,96}")

_PUBLIC_FIELDS = {
    "data": frozenset({
        "value_text", "meaning", "unit", "context_explanation", "source_page",
        "source_locator", "source_excerpt", "evidence_type", "source_precision",
    }),
    "finding": frozenset({
        "finding_text", "value_text", "meaning", "context_explanation", "source_page",
        "source_locator", "source_excerpt",
    }),
    "table": frozenset({
        "display_name", "label", "caption", "context_explanation", "conditions_text",
        "methods_text", "source_page", "page_start", "page_end", "source_locator",
        "source_excerpt", "physical_quantities", "materials", "variables", "tags",
    }),
    "figure": frozenset({
        "display_name", "label", "caption", "context_explanation", "conditions_text",
        "methods_text", "source_page", "page_start", "page_end", "source_locator",
        "source_excerpt", "physical_quantities", "materials", "variables", "tags",
    }),
}


class ReviewQueueError(RuntimeError):
    _MESSAGES = {
        "review_queue_invalid": "审核请求无效。",
        "review_queue_not_found": "没有找到待审核候选。",
        "review_token_invalid": "审核凭证无效。",
        "review_token_expired": "审核凭证已过期，请刷新候选列表。",
        "review_candidate_changed": "候选内容已变化，请刷新后重新审核。",
        "review_conflict": "该候选已经处理，不能重复执行不同操作。",
        "review_queue_unavailable": "审核服务暂时不可用。",
    }

    def __init__(self, code: str) -> None:
        if code not in self._MESSAGES:
            raise ValueError("unsupported review queue error")
        self.code = code
        self.safe_message = self._MESSAGES[code]
        super().__init__(self.safe_message)

    def public_dict(self) -> dict[str, Any]:
        retryable = self.code in {
            "review_token_expired", "review_candidate_changed", "review_queue_unavailable"
        }
        return {
            "schema_version": "review-error-v1",
            "code": self.code,
            "message": self.safe_message,
            "retryable": retryable,
            "stage": "review_queue",
            "next_action": "refresh_queue" if retryable else "none",
        }


class SearchIndexRefresher(Protocol):
    def refresh_papers(self, paper_ids: tuple[int, ...]) -> Mapping[str, Any]: ...


@dataclass
class _TokenRecord:
    candidate_id: int
    paper_id: int
    paper_uid: str
    entity_type: str
    snapshot: str
    expires_at: float
    active: bool = False
    operation_digest: str | None = None
    result: dict[str, Any] | None = None


def _paper_uid(row: Mapping[str, Any]) -> str:
    return stable_paper_uid(
        doi=row.get("doi"),
        title=row.get("title"),
        year=row.get("year"),
        first_author=row.get("first_author"),
    )


def _bounded(value: Any, *, excerpt: bool = False) -> Any:
    limit = MAX_EXCERPT if excerpt else MAX_TEXT
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        clean = re.sub(r"\s+", " ", value).strip()
        if _LOCAL_REFERENCE_RE.search(clean):
            raise ReviewQueueError("review_queue_unavailable")
        return clean[:limit]
    if isinstance(value, list):
        return [_bounded(item) for item in value[:MAX_LIST_ITEMS]]
    if isinstance(value, dict):
        return {
            str(key)[:120]: _bounded(item)
            for key, item in list(value.items())[:MAX_LIST_ITEMS]
            if isinstance(key, str)
        }
    return str(value)[:limit]


def _candidate_public(entity_type: str, raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ReviewQueueError("review_queue_unavailable")
    result: dict[str, Any] = {}
    for key in _PUBLIC_FIELDS[entity_type]:
        if key not in raw:
            continue
        result[key] = _bounded(raw[key], excerpt=key == "source_excerpt")
    return result


def _load_json(raw: Any) -> Any:
    try:
        return json.loads(str(raw or "null"))
    except json.JSONDecodeError as exc:
        raise ReviewQueueError("review_queue_unavailable") from exc


class ReviewQueueService:
    """Path-free manual-review authority over schema-v12 quality candidates."""

    def __init__(
        self,
        database: EvidenceDB,
        *,
        search_index: SearchIndexRefresher | None = None,
        clock: Callable[[], float] = time.time,
        token_ttl_seconds: int = 10 * 60,
        capacity: int = MAX_TOKEN_RECORDS,
        fault_injector: Callable[[], None] | None = None,
    ) -> None:
        if token_ttl_seconds < 30 or token_ttl_seconds > 3600:
            raise ValueError("review token ttl is invalid")
        if capacity < 1 or capacity > MAX_TOKEN_RECORDS:
            raise ValueError("review token capacity is invalid")
        self._db = database
        self._search_index = search_index
        self._clock = clock
        self._ttl = token_ttl_seconds
        self._capacity = capacity
        self._fault_injector = fault_injector
        self._lock = threading.RLock()
        self._records: dict[str, _TokenRecord] = {}
        self._candidate_tokens: dict[int, str] = {}

    def list(self, *, paper_uid: str | None = None) -> dict[str, Any]:
        if paper_uid is not None and (
            not isinstance(paper_uid, str)
            or not re.fullmatch(r"paper_[0-9a-f]{32}", paper_uid)
        ):
            raise ReviewQueueError("review_queue_invalid")
        self._db.init()
        with self._db.connect() as connection:
            rows = connection.execute(
                """SELECT q.*,p.title paper_title,p.doi paper_doi,p.year paper_year,
                          p.first_author paper_first_author
                   FROM quality_candidates q JOIN papers p ON p.id=q.paper_id
                   WHERE q.gate_status='manual_review'
                   ORDER BY p.title COLLATE NOCASE,q.overall_score,q.entity_type,q.id LIMIT ?""",
                (MAX_QUEUE_ITEMS,),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            uid = _paper_uid({
                "doi": row["paper_doi"],
                "title": row["paper_title"],
                "year": row["paper_year"],
                "first_author": row["paper_first_author"],
            })
            if paper_uid is not None and uid != paper_uid:
                continue
            items.append(self._project_row(row, uid))
        return {
            "schema_version": SCHEMA_VERSION,
            "source_scope": "workspace",
            "source_id": WORKSPACE_SOURCE_ID,
            "paper_uid": paper_uid,
            "total": len(items),
            "items": items,
        }

    def review(
        self,
        *,
        review_token: str,
        action: str,
        fields: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(review_token, str) or _TOKEN_RE.fullmatch(review_token) is None:
            raise ReviewQueueError("review_token_invalid")
        if action not in {"approve", "reject", "correct"}:
            raise ReviewQueueError("review_queue_invalid")
        if action == "correct":
            corrections = self._validate_corrections(review_token, fields)
        elif fields not in (None, {}):
            raise ReviewQueueError("review_queue_invalid")
        else:
            corrections = None
        digest = hashlib.sha256(
            json.dumps(
                {"action": action, "fields": corrections},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        with self._lock:
            record = self._records.get(review_token)
            if record is None:
                self._cleanup_locked()
                raise ReviewQueueError("review_token_invalid")
            if record.expires_at <= self._clock():
                self._remove_locked(review_token, record)
                raise ReviewQueueError("review_token_expired")
            self._cleanup_locked()
            if record.result is not None:
                if record.operation_digest != digest:
                    raise ReviewQueueError("review_conflict")
                return self._retry_index_if_needed(record)
            if record.active:
                raise ReviewQueueError("review_conflict")
            record.active = True
            record.operation_digest = digest
        try:
            reviewed = review_quality_candidate(
                self._db,
                record.candidate_id,
                decision="approve" if action in {"approve", "correct"} else "reject",
                fields=dict(corrections or {}),
                reviewer="本地研究者",
                note="桌面审核队列确认",
                expected_snapshot=record.snapshot,
                _fault_injector=self._fault_injector,
            )
            result = self._result(record, reviewed)
        except ValueError as exc:
            with self._lock:
                record.active = False
                record.operation_digest = None
            if "changed" in str(exc) or "awaiting manual review" in str(exc):
                raise ReviewQueueError("review_candidate_changed") from exc
            raise ReviewQueueError("review_queue_invalid") from exc
        except KeyError as exc:
            with self._lock:
                record.active = False
                record.operation_digest = None
            raise ReviewQueueError("review_candidate_changed") from exc
        except ReviewQueueError:
            with self._lock:
                record.active = False
                record.operation_digest = None
            raise
        except Exception as exc:
            with self._lock:
                record.active = False
                record.operation_digest = None
            raise ReviewQueueError("review_queue_unavailable") from exc
        with self._lock:
            record.active = False
            record.result = result
        return dict(result)

    def _project_row(self, row: Any, paper_uid: str) -> dict[str, Any]:
        candidate_id = int(row["id"])
        snapshot = quality_candidate_snapshot(row)
        with self._lock:
            self._cleanup_locked()
            token = self._candidate_tokens.get(candidate_id)
            record = self._records.get(token or "")
            if record is None or record.snapshot != snapshot:
                if len(self._records) >= self._capacity:
                    raise ReviewQueueError("review_queue_unavailable")
                token = "rq_" + secrets.token_urlsafe(32)
                record = _TokenRecord(
                    candidate_id=candidate_id,
                    paper_id=int(row["paper_id"]),
                    paper_uid=paper_uid,
                    entity_type=str(row["entity_type"]),
                    snapshot=snapshot,
                    expires_at=self._clock() + self._ttl,
                )
                self._records[token] = record
                self._candidate_tokens[candidate_id] = token
        entity_type = str(row["entity_type"])
        return {
            "review_token": token,
            "expires_at": record.expires_at,
            "source_scope": "workspace",
            "source_id": WORKSPACE_SOURCE_ID,
            "paper_uid": paper_uid,
            "paper": {
                "title": _bounded(row["paper_title"]),
                "doi": _bounded(row["paper_doi"]),
            },
            "entity_type": _ENTITY_MAP[entity_type],
            "candidate": _candidate_public(entity_type, _load_json(row["candidate_json"])),
            "alternate": _candidate_public(entity_type, _load_json(row["alternate_json"])),
            "conflict_reason": _bounded(row["gate_reason"]),
            "scores": {
                "agreement": float(row["agreement_score"]),
                "factuality": float(row["factuality_score"]),
                "completeness": float(row["completeness_score"]),
                "evidence": float(row["evidence_score"]),
                "overall": float(row["overall_score"]),
            },
            "allowed_operations": {
                "approve": {"available": True},
                "reject": {"available": True},
                "correct": {"available": True},
                "merge": {"available": False, "reason": "domain_primitive_unavailable"},
                "split": {"available": False, "reason": "domain_primitive_unavailable"},
            },
        }

    def _validate_corrections(
        self, review_token: str, fields: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        if not isinstance(fields, Mapping) or not fields:
            raise ReviewQueueError("review_queue_invalid")
        with self._lock:
            record = self._records.get(review_token)
            if record is None:
                raise ReviewQueueError("review_token_invalid")
            allowed = _PUBLIC_FIELDS[record.entity_type]
        if set(fields) - allowed:
            raise ReviewQueueError("review_queue_invalid")
        encoded = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 32 * 1024:
            raise ReviewQueueError("review_queue_invalid")
        clean: dict[str, Any] = {}
        for key, value in fields.items():
            if isinstance(value, str) and len(value) > MAX_TEXT:
                raise ReviewQueueError("review_queue_invalid")
            if isinstance(value, list) and len(value) > MAX_LIST_ITEMS:
                raise ReviewQueueError("review_queue_invalid")
            if isinstance(value, dict) and len(value) > MAX_LIST_ITEMS:
                raise ReviewQueueError("review_queue_invalid")
            clean[str(key)] = _sanitize_correction_value(value)
        return clean

    def _result(self, record: _TokenRecord, reviewed: Mapping[str, Any]) -> dict[str, Any]:
        refreshed = False
        if self._search_index is not None:
            try:
                self._search_index.refresh_papers((record.paper_id,))
                refreshed = True
            except Exception:
                refreshed = False
        status = "saved" if refreshed else "saved_index_pending"
        return {
            "schema_version": RESULT_SCHEMA_VERSION,
            "code": "review_saved" if refreshed else "review_saved_index_pending",
            "status": status,
            "stage": "complete" if refreshed else "index_refresh",
            "next_action": "none" if refreshed else "retry_same_request",
            "source_scope": "workspace",
            "source_id": WORKSPACE_SOURCE_ID,
            "paper_uid": record.paper_uid,
            "entity_type": _ENTITY_MAP[record.entity_type],
            "review_status": str(reviewed.get("gate_status") or ""),
        }

    def _retry_index_if_needed(self, record: _TokenRecord) -> dict[str, Any]:
        assert record.result is not None
        if record.result.get("status") != "saved_index_pending" or self._search_index is None:
            return dict(record.result)
        try:
            self._search_index.refresh_papers((record.paper_id,))
        except Exception:
            return dict(record.result)
        record.result = {
            **record.result,
            "code": "review_saved",
            "status": "saved",
            "stage": "complete",
            "next_action": "none",
        }
        return dict(record.result)

    def _cleanup_locked(self) -> None:
        current = self._clock()
        for token, record in list(self._records.items()):
            if not record.active and record.expires_at <= current:
                self._remove_locked(token, record)

    def _remove_locked(self, token: str, record: _TokenRecord) -> None:
        self._records.pop(token, None)
        if self._candidate_tokens.get(record.candidate_id) == token:
            self._candidate_tokens.pop(record.candidate_id, None)


def _sanitize_correction_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if _LOCAL_REFERENCE_RE.search(value):
            raise ReviewQueueError("review_queue_invalid")
        return value
    if isinstance(value, list):
        return [_sanitize_correction_value(item) for item in value]
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_correction_value(item)
            for key, item in value.items()
            if isinstance(key, str)
        }
    raise ReviewQueueError("review_queue_invalid")
