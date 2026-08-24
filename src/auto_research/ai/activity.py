from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType
from typing import Callable, Iterator, Mapping


AI_ACTIVITY_EVENT_SCHEMA_VERSION = "ai-activity-event-v1"

_EVENTS = MappingProxyType(
    {
        "execution_queued": ("queued", 3, "任务已进入安全执行队列"),
        "execution_started": ("starting", 6, "已启动受控 AI 任务"),
        "provider_acquired": ("provider", 10, "已连接当前 AI 提供商"),
        "harness_dependencies_verified": ("harness", 14, "Harness 依赖与版本已核验"),
        "harness_composition_verified": ("harness", 18, "只读工具策略已核验"),
        "harness_runtime_verified": ("harness", 22, "Harness 运行时已就绪"),
        "provider_request_started": ("model", 32, "正在请求模型"),
        "provider_response_received": ("model", 48, "模型已返回下一步"),
        "harness_tool_started": ("tool", 56, "正在调用只读证据工具"),
        "harness_tool_completed": ("tool", 68, "只读证据工具已返回"),
        "result_validating": ("validation", 86, "正在校验结果与引用"),
        "execution_completed": ("completed", 100, "任务已完成"),
        "execution_failed": ("failed", 100, "任务未完成"),
    }
)

_TOOLS = MappingProxyType(
    {
        "exact_search": "精确检索",
        "federated_search": "联合检索",
        "evidence_detail": "读取证据详情",
        "evidence_metadata": "核对证据元数据",
        "source_locator": "定位原文来源",
        "source_view": "读取受控来源片段",
        "citation_verify": "核验引用",
        "recommend_papers": "查找相关文章",
    }
)

ActivityObserver = Callable[[Mapping[str, object]], None]
_OBSERVER: ContextVar[ActivityObserver | None] = ContextVar(
    "auto_research_ai_activity_observer", default=None
)


def current_activity_observer() -> ActivityObserver | None:
    return _OBSERVER.get()


@contextmanager
def bind_activity_observer(observer: ActivityObserver | None) -> Iterator[None]:
    token = _OBSERVER.set(observer)
    try:
        yield
    finally:
        _OBSERVER.reset(token)


def emit_ai_activity(
    code: str,
    *,
    tool: str = "",
    call_index: int | None = None,
    call_limit: int | None = None,
) -> None:
    observer = current_activity_observer()
    event = safe_ai_activity_event(
        {
            "schema_version": AI_ACTIVITY_EVENT_SCHEMA_VERSION,
            "code": code,
            "tool": tool,
            "call_index": call_index,
            "call_limit": call_limit,
        }
    )
    if observer is None or event is None:
        return
    try:
        observer(event)
    except Exception:
        # Observability must never change the scientific or billing result.
        return


def safe_ai_activity_event(raw: object) -> Mapping[str, object] | None:
    """Rebuild an event from the fixed catalog; never trust display text."""
    if not isinstance(raw, Mapping):
        return None
    code = raw.get("code")
    if raw.get("schema_version") != AI_ACTIVITY_EVENT_SCHEMA_VERSION or code not in _EVENTS:
        return None
    stage, progress, label = _EVENTS[str(code)]
    event: dict[str, object] = {
        "schema_version": AI_ACTIVITY_EVENT_SCHEMA_VERSION,
        "code": str(code),
        "stage": stage,
        "progress": progress,
        "label": label,
    }
    tool = safe_harness_tool(raw.get("tool"))
    if tool:
        event["tool"] = tool
        event["detail"] = _TOOLS[tool]
    call_index, call_limit = raw.get("call_index"), raw.get("call_limit")
    if (
        isinstance(call_index, int)
        and not isinstance(call_index, bool)
        and isinstance(call_limit, int)
        and not isinstance(call_limit, bool)
        and 1 <= call_index <= call_limit <= 512
    ):
        event["call_index"] = call_index
        event["call_limit"] = call_limit
        event["detail"] = f"模型调用 {call_index}/{call_limit}"
    return MappingProxyType(event)


def safe_harness_tool(raw_name: object) -> str:
    if not isinstance(raw_name, str):
        return ""
    name = raw_name.rsplit("__", 1)[-1]
    return name if name in _TOOLS else ""


__all__ = [
    "AI_ACTIVITY_EVENT_SCHEMA_VERSION",
    "ActivityObserver",
    "bind_activity_observer",
    "current_activity_observer",
    "emit_ai_activity",
    "safe_ai_activity_event",
    "safe_harness_tool",
]
