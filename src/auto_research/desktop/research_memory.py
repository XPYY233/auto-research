from __future__ import annotations

import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol


MAX_RESEARCH_MEMORIES = 200
MAX_MEMORY_TITLE_CHARS = 120
MAX_MEMORY_CONTENT_CHARS = 2_000
MAX_SOURCE_REFS = 20
ALLOWED_SOURCE_SCOPES = frozenset({"official", "workspace"})
ALLOWED_ENTITY_TYPES = frozenset({"item", "finding", "table", "figure"})


class ResearchMemoryError(RuntimeError):
    def __init__(self, code: str, message: str, *, http_status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = int(http_status)


class ResearchMemoryStore(Protocol):
    storage_label: str

    def load(self) -> dict[str, Any]: ...

    def save(self, snapshot: dict[str, Any]) -> None: ...


@dataclass(frozen=True, slots=True)
class ResearchMemorySnapshot:
    revision: int
    items: tuple[dict[str, Any], ...]
    storage: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "research-memory-list-v1",
            "revision": self.revision,
            "storage": self.storage,
            "items": [dict(item) for item in self.items],
        }


def _clean_text(value: object, *, limit: int, field: str) -> str:
    if not isinstance(value, str):
        raise ResearchMemoryError("research_memory_invalid", f"{field}格式无效")
    text = " ".join(value.split()).strip()
    if not text or len(text) > limit:
        raise ResearchMemoryError("research_memory_invalid", f"{field}长度无效")
    if re.search(r"(?i)(?:file://|[a-z]:\\|/(?:Users|home|private|var|tmp|etc|opt|Volumes)/)", text):
        raise ResearchMemoryError("research_memory_content_forbidden", "研究记忆不能包含本机路径")
    if re.search(r"(?i)(?:api[_ -]?key|bearer\s+[a-z0-9._-]{12,}|sk-[a-z0-9_-]{12,})", text):
        raise ResearchMemoryError("research_memory_content_forbidden", "研究记忆不能包含凭据")
    return text


def _clean_identity(value: object, *, field: str, limit: int = 256) -> str:
    text = _clean_text(value, limit=limit, field=field)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}", text):
        raise ResearchMemoryError("research_memory_source_unverified", f"{field}不是稳定来源身份")
    return text


def normalize_source_ref(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ResearchMemoryError("research_memory_source_unverified", "研究记忆缺少已核验来源")
    if set(raw) - {
        "source_scope",
        "source_id",
        "entity_type",
        "entity_uid",
        "paper_uid",
        "doi",
        "page",
        "title",
    }:
        raise ResearchMemoryError("research_memory_source_unverified", "研究记忆来源字段无效")
    scope = raw.get("source_scope")
    entity_type = raw.get("entity_type")
    if scope not in ALLOWED_SOURCE_SCOPES or entity_type not in ALLOWED_ENTITY_TYPES:
        raise ResearchMemoryError("research_memory_source_unverified", "研究记忆只能引用已发布的公开文献证据")
    page = raw.get("page")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1 or page > 100_000:
        raise ResearchMemoryError("research_memory_source_unverified", "研究记忆来源页无效")
    value: dict[str, Any] = {
        "source_scope": scope,
        "source_id": _clean_identity(raw.get("source_id"), field="来源库"),
        "entity_type": entity_type,
        "entity_uid": _clean_identity(raw.get("entity_uid"), field="证据身份"),
        "page": page,
        "title": _clean_text(raw.get("title"), limit=500, field="来源标题"),
    }
    for field in ("paper_uid", "doi"):
        if raw.get(field):
            value[field] = _clean_identity(raw[field], field=field)
    return value


def normalize_memory_item(raw: object, *, now: str | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ResearchMemoryError("research_memory_invalid", "研究记忆必须是对象")
    if raw.get("user_approved") is not True:
        raise ResearchMemoryError("research_memory_approval_required", "只有你明确确认的内容才能保存为研究记忆")
    refs = raw.get("source_refs")
    if not isinstance(refs, list) or not refs or len(refs) > MAX_SOURCE_REFS:
        raise ResearchMemoryError("research_memory_source_unverified", "研究记忆必须包含有界的已核验来源")
    stamp = now or datetime.now(timezone.utc).isoformat()
    memory_uid = raw.get("memory_uid")
    if memory_uid is not None:
        memory_uid = _clean_identity(memory_uid, field="记忆身份", limit=96)
    return {
        "schema_version": "research-memory-item-v1",
        "memory_uid": memory_uid or f"mem_{secrets.token_urlsafe(18)}",
        "title": _clean_text(raw.get("title"), limit=MAX_MEMORY_TITLE_CHARS, field="记忆标题"),
        "content": _clean_text(raw.get("content"), limit=MAX_MEMORY_CONTENT_CHARS, field="记忆内容"),
        "source_refs": [normalize_source_ref(ref) for ref in refs],
        "approval": "user_approved",
        "origin": "assistant_suggested" if raw.get("origin") == "assistant_suggested" else "user_created",
        "created_at": stamp,
        "updated_at": stamp,
    }


def normalize_stored_memory_item(raw: object) -> dict[str, Any]:
    """Validate an authenticated snapshot before projecting it to the UI."""

    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "memory_uid",
        "title",
        "content",
        "source_refs",
        "approval",
        "origin",
        "created_at",
        "updated_at",
    }:
        raise ResearchMemoryError(
            "research_memory_store_unavailable",
            "本机研究记忆条目无效",
            http_status=503,
        )
    if raw.get("schema_version") != "research-memory-item-v1" or raw.get("approval") != "user_approved":
        raise ResearchMemoryError(
            "research_memory_store_unavailable",
            "本机研究记忆条目版本无效",
            http_status=503,
        )
    timestamps: dict[str, str] = {}
    for field in ("created_at", "updated_at"):
        stamp = _clean_text(raw.get(field), limit=80, field=field)
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ResearchMemoryError(
                "research_memory_store_unavailable",
                "本机研究记忆时间无效",
                http_status=503,
            ) from exc
        if parsed.tzinfo is None:
            raise ResearchMemoryError(
                "research_memory_store_unavailable",
                "本机研究记忆时间无效",
                http_status=503,
            )
        timestamps[field] = stamp
    try:
        value = normalize_memory_item(
            {
                "memory_uid": raw.get("memory_uid"),
                "title": raw.get("title"),
                "content": raw.get("content"),
                "source_refs": raw.get("source_refs"),
                "origin": raw.get("origin"),
                "user_approved": True,
            },
            now=timestamps["updated_at"],
        )
    except ResearchMemoryError as exc:
        raise ResearchMemoryError(
            "research_memory_store_unavailable",
            "本机研究记忆条目无效",
            http_status=503,
        ) from exc
    value["created_at"] = timestamps["created_at"]
    value["updated_at"] = timestamps["updated_at"]
    return value


class ResearchMemoryService:
    """Own user-approved research memory independently from chat history."""

    def __init__(self, store: ResearchMemoryStore) -> None:
        self.store = store
        self._lock = threading.RLock()

    def _load_locked(self) -> tuple[int, list[dict[str, Any]]]:
        try:
            snapshot = self.store.load()
        except ResearchMemoryError:
            raise
        except Exception as exc:
            raise ResearchMemoryError(
                "research_memory_store_unavailable",
                "本机研究记忆暂时不可用",
                http_status=503,
            ) from exc
        revision = snapshot.get("revision", 0)
        items = snapshot.get("items", [])
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0 or not isinstance(items, list):
            raise ResearchMemoryError("research_memory_store_unavailable", "本机研究记忆状态无效", http_status=503)
        if len(items) > MAX_RESEARCH_MEMORIES:
            raise ResearchMemoryError("research_memory_store_unavailable", "本机研究记忆超过安全上限", http_status=503)
        try:
            normalized_items = [normalize_stored_memory_item(item) for item in items]
        except ResearchMemoryError:
            raise
        return revision, normalized_items

    def get(self) -> ResearchMemorySnapshot:
        with self._lock:
            revision, items = self._load_locked()
            return ResearchMemorySnapshot(revision, tuple(items), self.store.storage_label)

    def mutate(self, body: object) -> ResearchMemorySnapshot:
        if not isinstance(body, dict):
            raise ResearchMemoryError("research_memory_invalid", "研究记忆请求必须是对象")
        action = body.get("action")
        expected = body.get("expected_revision")
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
            raise ResearchMemoryError("research_memory_invalid", "研究记忆版本无效")
        with self._lock:
            revision, items = self._load_locked()
            if expected != revision:
                raise ResearchMemoryError("research_memory_revision_conflict", "研究记忆已在其他操作中更新，请刷新后重试", http_status=409)
            next_items = list(items)
            now = datetime.now(timezone.utc).isoformat()
            if action == "create":
                if set(body) != {"action", "expected_revision", "item"}:
                    raise ResearchMemoryError("research_memory_invalid", "新增研究记忆字段无效")
                item = normalize_memory_item(body.get("item"), now=now)
                if len(next_items) >= MAX_RESEARCH_MEMORIES:
                    raise ResearchMemoryError("research_memory_limit_reached", "研究记忆已达到上限")
                next_items.insert(0, item)
            elif action == "update":
                if set(body) != {"action", "expected_revision", "item"}:
                    raise ResearchMemoryError("research_memory_invalid", "编辑研究记忆字段无效")
                candidate = normalize_memory_item(body.get("item"), now=now)
                index = next((index for index, item in enumerate(next_items) if item.get("memory_uid") == candidate["memory_uid"]), -1)
                if index < 0:
                    raise ResearchMemoryError("research_memory_not_found", "研究记忆不存在", http_status=404)
                candidate["created_at"] = next_items[index].get("created_at", now)
                next_items[index] = candidate
            elif action == "delete":
                if set(body) != {"action", "expected_revision", "memory_uid"}:
                    raise ResearchMemoryError("research_memory_invalid", "删除研究记忆字段无效")
                uid = _clean_identity(body.get("memory_uid"), field="记忆身份", limit=96)
                reduced = [item for item in next_items if item.get("memory_uid") != uid]
                if len(reduced) == len(next_items):
                    raise ResearchMemoryError("research_memory_not_found", "研究记忆不存在", http_status=404)
                next_items = reduced
            elif action == "clear":
                if set(body) != {"action", "expected_revision", "confirm_clear"} or body.get("confirm_clear") is not True:
                    raise ResearchMemoryError("research_memory_invalid", "清空研究记忆需要再次确认")
                next_items = []
            else:
                raise ResearchMemoryError("research_memory_invalid", "研究记忆操作无效")
            snapshot = {"revision": revision + 1, "items": next_items}
            try:
                self.store.save(snapshot)
            except ResearchMemoryError:
                raise
            except Exception as exc:
                raise ResearchMemoryError("research_memory_store_unavailable", "本机研究记忆暂时不可用", http_status=503) from exc
            return ResearchMemorySnapshot(revision + 1, tuple(next_items), self.store.storage_label)
