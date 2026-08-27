from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol, Sequence


HISTORY_SCHEMA = "evidence-chat-history-v1"
THREAD_SCHEMA = "evidence-chat-thread-v1"
STORE_SCHEMA = "evidence-chat-history-store-v1"
ERROR_SCHEMA = "evidence-chat-history-error-v1"

MAX_THREADS = 20
MAX_MESSAGES = 20
MAX_INPUT_MESSAGES = 100
MAX_MESSAGE_CHARS = 12_000
MAX_TITLE_CHARS = 300
MAX_IDENTITY_CHARS = 256
MAX_ANNOTATION_ITEMS = 20
MAX_ANNOTATION_CHARS = 1_000
DEFAULT_RETENTION_DAYS = 30
MAX_FUTURE_SKEW = timedelta(minutes=5)

ALLOWED_SOURCE_SCOPES = frozenset({"official", "workspace"})
ALLOWED_ENTITY_TYPES = frozenset({"item", "finding", "table", "figure"})
ALLOWED_ROLES = frozenset({"user", "assistant"})
IDENTITY_FIELDS = ("source_scope", "source_id", "entity_type", "entity_uid")

_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}\Z")
_LOCAL_PATH_RE = re.compile(
    r"(?i)(?:^|[\s\"'=:(])(?:"
    r"file:|sqlite:|~[\\/]|[a-z]:[\\/]|\\\\|"
    r"/(?:Users|home|private|var|tmp|etc|usr|root|srv|mnt|media|opt|Applications|Library|System|Volumes)(?:/|\\)"
    r")"
)
_SECRET_RE = re.compile(
    r"(?i)(?:"
    r"(?:api[_ -]?key|credential(?:_ref)?|access[_ -]?token|secret)\s*[:=]\s*[^\s,;]{6,}|"
    r"bearer\s+[a-z0-9._~+/-]{12,}|"
    r"\b(?:sk|ds)-[a-z0-9_-]{12,}"
    r")"
)
_INTERNAL_ID_RE = re.compile(
    r"(?i)\b(?:paper|asset|visual_asset|candidate|reviewer|file|source_file|run|draft|import|database|db|row|internal)_id\s*[:=]"
)
_PDF_BODY_RE = re.compile(r"(?i)(?:%PDF-\d\.\d|\b\d+\s+\d+\s+obj\b|\bstartxref\b|%%EOF)")
_IMAGE_PAYLOAD_RE = re.compile(r"(?i)(?:data:image/|iVBORw0KGgo|/9j/[A-Za-z0-9+/]{12,})")


class EvidenceChatHistoryError(RuntimeError):
    def __init__(self, code: str, safe_message: str, *, http_status: int = 400) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.http_status = int(http_status)

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": ERROR_SCHEMA,
            "code": self.code,
            "message": self.safe_message,
            "http_status": self.http_status,
        }


class EvidenceChatHistoryStore(Protocol):
    storage_label: str

    def load(self) -> object: ...

    def save(self, value: object) -> None: ...

    def clear(self) -> None: ...


@dataclass(frozen=True, slots=True)
class EvidenceChatHistorySnapshot:
    revision: int
    storage: str
    threads: tuple[dict[str, Any], ...]

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": HISTORY_SCHEMA,
            "revision": self.revision,
            "storage": self.storage,
            "threads": copy.deepcopy(list(self.threads)),
        }


def _invalid(message: str) -> EvidenceChatHistoryError:
    return EvidenceChatHistoryError("evidence_chat_history_invalid", message)


def _store_unavailable(message: str = "本机证据对话历史暂时不可用") -> EvidenceChatHistoryError:
    return EvidenceChatHistoryError(
        "evidence_chat_history_store_unavailable",
        message,
        http_status=503,
    )


def _clean_text(value: object, *, limit: int, field: str, single_line: bool = False) -> str:
    if not isinstance(value, str):
        raise _invalid(f"{field}格式无效")
    text = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if single_line:
        text = " ".join(text.split())
    if not text or len(text) > limit:
        raise _invalid(f"{field}长度无效")
    if _LOCAL_PATH_RE.search(text):
        raise EvidenceChatHistoryError("evidence_chat_history_content_forbidden", "证据对话历史不能包含本机路径")
    if _SECRET_RE.search(text):
        raise EvidenceChatHistoryError("evidence_chat_history_content_forbidden", "证据对话历史不能包含密钥或凭据")
    if _INTERNAL_ID_RE.search(text):
        raise EvidenceChatHistoryError("evidence_chat_history_content_forbidden", "证据对话历史不能包含内部数据库身份")
    if _PDF_BODY_RE.search(text):
        raise EvidenceChatHistoryError("evidence_chat_history_content_forbidden", "证据对话历史不能保存 PDF 正文载荷")
    if _IMAGE_PAYLOAD_RE.search(text):
        raise EvidenceChatHistoryError("evidence_chat_history_content_forbidden", "证据对话历史不能保存图像载荷")
    return text


def _identity_text(value: object, *, field: str) -> str:
    text = _clean_text(value, limit=MAX_IDENTITY_CHARS, field=field, single_line=True)
    if not _IDENTITY_RE.fullmatch(text):
        raise _invalid(f"{field}不是稳定公开身份")
    return text


def normalize_identity(raw: object) -> dict[str, str]:
    if not isinstance(raw, Mapping) or set(raw) != set(IDENTITY_FIELDS):
        raise _invalid("证据身份字段无效")
    scope = raw.get("source_scope")
    entity_type = raw.get("entity_type")
    if scope not in ALLOWED_SOURCE_SCOPES:
        raise _invalid("证据对话历史只允许官方文献或已发布工作区证据")
    if entity_type not in ALLOWED_ENTITY_TYPES:
        raise _invalid("证据类型无效")
    return {
        "source_scope": str(scope),
        "source_id": _identity_text(raw.get("source_id"), field="来源身份"),
        "entity_type": str(entity_type),
        "entity_uid": _identity_text(raw.get("entity_uid"), field="证据身份"),
    }


def derive_thread_uid(identity: Mapping[str, str]) -> str:
    canonical = json.dumps(
        {field: identity[field] for field in IDENTITY_FIELDS},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(b"auto-research:evidence-chat-thread:v1\0" + canonical).hexdigest()
    return f"ech_{digest[:32]}"


def _normalize_annotations(raw: object) -> dict[str, object]:
    if not isinstance(raw, Mapping) or set(raw) - {"pages", "notes", "limitations"}:
        raise _invalid("消息标注字段无效")
    value: dict[str, object] = {}
    if "pages" in raw:
        pages = raw.get("pages")
        if not isinstance(pages, Sequence) or isinstance(pages, (str, bytes)) or len(pages) > MAX_ANNOTATION_ITEMS:
            raise _invalid("消息页码标注无效")
        normalized_pages: list[int] = []
        for page in pages:
            if isinstance(page, bool) or not isinstance(page, int) or page < 1 or page > 100_000:
                raise _invalid("消息页码标注无效")
            if page not in normalized_pages:
                normalized_pages.append(page)
        value["pages"] = normalized_pages
    for field, label in (("notes", "说明"), ("limitations", "限制")):
        if field not in raw:
            continue
        entries = raw.get(field)
        if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)) or len(entries) > MAX_ANNOTATION_ITEMS:
            raise _invalid(f"消息{label}标注无效")
        value[field] = [
            _clean_text(entry, limit=MAX_ANNOTATION_CHARS, field=f"消息{label}")
            for entry in entries
        ]
    return value


def normalize_message(raw: object) -> dict[str, object]:
    if not isinstance(raw, Mapping) or set(raw) - {"role", "content", "annotations"}:
        raise _invalid("消息字段无效")
    if set(raw) not in ({"role", "content"}, {"role", "content", "annotations"}):
        raise _invalid("消息字段无效")
    role = raw.get("role")
    if role not in ALLOWED_ROLES:
        raise _invalid("消息角色无效")
    value: dict[str, object] = {
        "role": role,
        "content": _clean_text(raw.get("content"), limit=MAX_MESSAGE_CHARS, field="消息内容"),
    }
    if "annotations" in raw:
        value["annotations"] = _normalize_annotations(raw.get("annotations"))
    return value


def _parse_timestamp(value: object, *, now: datetime) -> tuple[str, datetime]:
    if not isinstance(value, str) or not value or len(value) > 80:
        raise _store_unavailable("本机证据对话历史时间无效")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _store_unavailable("本机证据对话历史时间无效") from exc
    if parsed.tzinfo is None:
        raise _store_unavailable("本机证据对话历史时间无效")
    parsed = parsed.astimezone(timezone.utc)
    if parsed > now + MAX_FUTURE_SKEW:
        raise _store_unavailable("本机证据对话历史时间无效")
    return parsed.isoformat().replace("+00:00", "Z"), parsed


def _normalize_input_thread(raw: object, *, stamp: str) -> dict[str, Any]:
    expected = {*IDENTITY_FIELDS, "title", "messages"}
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise _invalid("证据对话线程字段无效")
    identity = normalize_identity({field: raw.get(field) for field in IDENTITY_FIELDS})
    messages = raw.get("messages")
    if not isinstance(messages, list) or not messages or len(messages) > MAX_INPUT_MESSAGES:
        raise _invalid("证据对话消息列表无效")
    normalized = [normalize_message(message) for message in messages][-MAX_MESSAGES:]
    return {
        "schema_version": THREAD_SCHEMA,
        "thread_uid": derive_thread_uid(identity),
        **identity,
        "title": _clean_text(raw.get("title"), limit=MAX_TITLE_CHARS, field="线程标题", single_line=True),
        "created_at": stamp,
        "updated_at": stamp,
        "messages": normalized,
    }


def _normalize_stored_thread(raw: object, *, now: datetime) -> tuple[dict[str, Any], datetime]:
    expected = {
        "schema_version", "thread_uid", *IDENTITY_FIELDS, "title", "created_at", "updated_at", "messages"
    }
    if not isinstance(raw, Mapping) or set(raw) != expected or raw.get("schema_version") != THREAD_SCHEMA:
        raise _store_unavailable("本机证据对话线程无效")
    try:
        identity = normalize_identity({field: raw.get(field) for field in IDENTITY_FIELDS})
        title = _clean_text(raw.get("title"), limit=MAX_TITLE_CHARS, field="线程标题", single_line=True)
        messages = raw.get("messages")
        if not isinstance(messages, list) or not messages or len(messages) > MAX_MESSAGES:
            raise _invalid("证据对话消息列表无效")
        normalized_messages = [normalize_message(message) for message in messages]
    except EvidenceChatHistoryError as exc:
        raise _store_unavailable("本机证据对话线程无效") from exc
    if raw.get("thread_uid") != derive_thread_uid(identity):
        raise _store_unavailable("本机证据对话线程身份无效")
    created_at, created = _parse_timestamp(raw.get("created_at"), now=now)
    updated_at, updated = _parse_timestamp(raw.get("updated_at"), now=now)
    if created > updated:
        raise _store_unavailable("本机证据对话历史时间无效")
    return ({
        "schema_version": THREAD_SCHEMA,
        "thread_uid": derive_thread_uid(identity),
        **identity,
        "title": title,
        "created_at": created_at,
        "updated_at": updated_at,
        "messages": normalized_messages,
    }, updated)


class EvidenceChatHistoryService:
    """Validate selected-evidence chat history above an encrypted platform store."""

    def __init__(
        self,
        store: EvidenceChatHistoryStore,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ) -> None:
        if isinstance(retention_days, bool) or not isinstance(retention_days, int) or retention_days < 1 or retention_days > 365:
            raise ValueError("retention_days must be between 1 and 365")
        self.store = store
        self._clock = clock
        self._retention = timedelta(days=retention_days)
        self._lock = threading.RLock()

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise _store_unavailable("本机证据对话历史时钟不可用")
        return value.astimezone(timezone.utc)

    def _storage_label(self) -> str:
        label = getattr(self.store, "storage_label", None)
        if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", label):
            raise _store_unavailable("本机证据对话历史存储不可用")
        return label

    def _save_locked(self, revision: int, threads: list[dict[str, Any]]) -> None:
        snapshot = {"schema_version": STORE_SCHEMA, "revision": revision, "threads": threads}
        try:
            self.store.save(copy.deepcopy(snapshot))
        except Exception as exc:
            raise _store_unavailable() from exc

    def _load_locked(self) -> tuple[int, list[dict[str, Any]]]:
        try:
            raw = self.store.load()
        except Exception as exc:
            raise _store_unavailable() from exc
        if raw is None:
            return 0, []
        if not isinstance(raw, Mapping) or set(raw) != {"schema_version", "revision", "threads"}:
            raise _store_unavailable("本机证据对话历史状态无效")
        if raw.get("schema_version") != STORE_SCHEMA:
            raise _store_unavailable("本机证据对话历史版本无效")
        revision = raw.get("revision")
        threads = raw.get("threads")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0 or not isinstance(threads, list):
            raise _store_unavailable("本机证据对话历史状态无效")
        if len(threads) > 100:
            raise _store_unavailable("本机证据对话历史超过安全上限")
        now = self._now()
        cutoff = now - self._retention
        retained: list[tuple[datetime, dict[str, Any]]] = []
        seen: set[str] = set()
        for raw_thread in threads:
            thread, updated = _normalize_stored_thread(raw_thread, now=now)
            uid = thread["thread_uid"]
            if uid in seen:
                raise _store_unavailable("本机证据对话线程身份重复")
            seen.add(uid)
            if updated >= cutoff:
                retained.append((updated, thread))
        retained.sort(key=lambda value: (-value[0].timestamp(), value[1]["thread_uid"]))
        normalized = [thread for _updated, thread in retained[:MAX_THREADS]]
        if normalized != threads:
            revision += 1
            self._save_locked(revision, normalized)
        return revision, normalized

    def _snapshot(self, revision: int, threads: list[dict[str, Any]]) -> EvidenceChatHistorySnapshot:
        return EvidenceChatHistorySnapshot(revision, self._storage_label(), tuple(copy.deepcopy(threads)))

    def get(self) -> dict[str, object]:
        with self._lock:
            revision, threads = self._load_locked()
            return self._snapshot(revision, threads).public_dict()

    def mutate(self, body: object) -> dict[str, object]:
        if not isinstance(body, Mapping):
            raise _invalid("证据对话历史请求必须是对象")
        operation = body.get("operation")
        expected_revision = body.get("expected_revision")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise _invalid("证据对话历史版本无效")
        with self._lock:
            revision, threads = self._load_locked()
            if expected_revision != revision:
                raise EvidenceChatHistoryError(
                    "evidence_chat_history_revision_conflict",
                    "证据对话历史已更新，请刷新后重试",
                    http_status=409,
                )
            next_threads = list(threads)
            if operation == "upsert":
                if set(body) != {"operation", "expected_revision", "thread"}:
                    raise _invalid("保存证据对话线程字段无效")
                stamp = self._now().isoformat().replace("+00:00", "Z")
                candidate = _normalize_input_thread(body.get("thread"), stamp=stamp)
                existing = next(
                    (thread for thread in next_threads if thread["thread_uid"] == candidate["thread_uid"]),
                    None,
                )
                if existing is not None:
                    candidate["created_at"] = existing["created_at"]
                next_threads = [
                    thread for thread in next_threads if thread["thread_uid"] != candidate["thread_uid"]
                ]
                next_threads.insert(0, candidate)
                next_threads = next_threads[:MAX_THREADS]
            elif operation == "delete":
                if set(body) != {"operation", "expected_revision", "identity"}:
                    raise _invalid("删除证据对话线程字段无效")
                identity = normalize_identity(body.get("identity"))
                uid = derive_thread_uid(identity)
                reduced = [thread for thread in next_threads if thread["thread_uid"] != uid]
                if len(reduced) == len(next_threads):
                    raise EvidenceChatHistoryError(
                        "evidence_chat_history_not_found",
                        "证据对话线程不存在",
                        http_status=404,
                    )
                next_threads = reduced
            elif operation == "clear":
                if set(body) != {"operation", "expected_revision", "confirm_clear"} or body.get("confirm_clear") is not True:
                    raise _invalid("清空证据对话历史需要再次确认")
                next_threads = []
            else:
                raise _invalid("证据对话历史操作无效")
            next_revision = revision + 1
            self._save_locked(next_revision, next_threads)
            return self._snapshot(next_revision, next_threads).public_dict()
