from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, ContextManager, Mapping, Protocol, Sequence

from .prepared_actions import (
    ContentUnit,
    PreparedActionError,
    PreparedActionService,
    PreparedOutbound,
    SCOPE_BYTE_CAPS,
)
from .activity import ActivityObserver, bind_activity_observer, emit_ai_activity


BUSINESS_ACTION_ERROR_SCHEMA_VERSION = "ai-business-action-error-v1"
BUSINESS_ACTION_SCOPES = frozenset(
    {
        "librarian",
        "selected_evidence_chat",
        "literature_extraction",
        "personal_suggestion",
    }
)
MAX_PUBLIC_RESULT_BYTES = 1024 * 1024
MAX_EXECUTION_RECEIPTS = 256
MAX_DERIVED_LITERATURE_STAGE_BYTES = 32_000_000
_SAFE_EXECUTOR_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_HARNESS_TOOL_NAME_RE = re.compile(
    r"^mcp__auto_research__(?:exact_search|federated_search|evidence_detail|"
    r"evidence_metadata|source_locator|source_view|citation_verify|recommend_papers)$"
)
_SENSITIVE_KEY_RE = re.compile(
    r"(?:^|_)(?:api_?key|secret|password|credential|token|nonce|path|endpoint)(?:$|_)",
    re.IGNORECASE,
)
_PUBLIC_SIGNED_TOKEN_KEYS = frozenset({"state_token", "snapshot_token", "job_token"})
_OPTIONAL_EMPTY_PUBLIC_SIGNED_TOKEN_KEYS = frozenset({"state_token"})
MAX_PUBLIC_SIGNED_TOKEN_LENGTH = 16 * 1024
_LOCAL_VALUE_RE = re.compile(
    r"(?:^|[\s='\"])(?:~[/\\]|/(?:Users|home|private|tmp|var|etc|usr|root|srv|mnt|media|opt|Applications|Library|System)(?:[/\\]|$)|/[^/\s]+[/\\][^\s]*|[A-Za-z]:[\\/]|\\\\|file:|sqlite:)",
    re.IGNORECASE,
)
_ERRORS = {
    "business_action_invalid": ("AI 业务动作无效。", False),
    "business_action_scope_unsupported": ("该 AI 业务场景暂不支持。", False),
    "business_action_prepare_failed": ("AI 业务内容暂时无法准备。", True),
    "business_action_execution_failed": ("AI 业务动作未能完成。", True),
    "business_action_replayed": ("AI 业务动作已执行，请重新准备。", False),
    "business_action_store_full": ("AI 业务执行记录已满，请稍后再试。", True),
    "business_action_result_invalid": ("AI 业务结果不符合安全要求。", False),
}


class BusinessActionError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        cause_code: str = "",
        stage: str = "",
        next_action: str = "",
    ) -> None:
        if code not in _ERRORS:
            raise ValueError("unsupported AI business action error code")
        message, retryable = _ERRORS[code]
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable
        self.cause_code = cause_code if re.fullmatch(r"[a-z][a-z0-9_]{2,95}", cause_code) else ""
        self.stage = stage if re.fullmatch(r"[a-z][a-z0-9_]{2,63}", stage) else ""
        self.next_action = (
            next_action
            if isinstance(next_action, str)
            and re.fullmatch(r"[a-z][a-z0-9_]{2,95}", next_action)
            else ""
        )

    def public_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": BUSINESS_ACTION_ERROR_SCHEMA_VERSION,
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.retryable,
        }
        if self.cause_code:
            result["cause_code"] = self.cause_code
        if self.stage:
            result["stage"] = self.stage
        if self.next_action:
            result["next_action"] = self.next_action
        return result


def _canonical_call_value(value: object) -> object:
    nodes = [0]

    def thaw(item: object, depth: int = 0) -> object:
        nodes[0] += 1
        if depth > 12 or nodes[0] > 2_000:
            raise BusinessActionError("business_action_invalid")
        if isinstance(item, Mapping):
            output = {}
            for key, child in item.items():
                if (
                    not isinstance(key, str)
                    or not key
                    or _SENSITIVE_KEY_RE.search(key)
                ):
                    raise BusinessActionError("business_action_invalid")
                output[key] = thaw(child, depth + 1)
            return output
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            return [thaw(child, depth + 1) for child in item]
        if item is None or isinstance(item, (bool, int)):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise BusinessActionError("business_action_invalid")
            return item
        if isinstance(item, str):
            if _LOCAL_VALUE_RE.search(item):
                raise BusinessActionError("business_action_invalid")
            return item
        raise BusinessActionError("business_action_invalid")

    try:
        encoded = json.dumps(
            thaw(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        normalized = json.loads(encoded.decode("utf-8"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BusinessActionError("business_action_invalid") from exc
    if len(encoded) > 4 * 1024 * 1024:
        raise BusinessActionError("business_action_invalid")
    return normalized


def _freeze_call_value(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_call_value(child) for key, child in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_call_value(child) for child in value)
    return value


@dataclass(frozen=True)
class PreparedBusinessCall:
    method: str
    task: str
    messages: tuple[Mapping[str, Any], ...]
    max_tokens: int
    tools: tuple[Mapping[str, Any], ...] = ()
    options: Mapping[str, Any] = MappingProxyType({})

    def __post_init__(self) -> None:
        if (
            self.method not in {"json", "tool"}
            or not isinstance(self.task, str)
            or not self.task
            or isinstance(self.max_tokens, bool)
            or not isinstance(self.max_tokens, int)
            or self.max_tokens < 1
            or self.method == "json" and self.tools
        ):
            raise BusinessActionError("business_action_invalid")
        messages = _canonical_call_value(list(self.messages))
        tools = _canonical_call_value(list(self.tools))
        options = _canonical_call_value(dict(self.options))
        if not isinstance(messages, list) or not isinstance(tools, list) or not isinstance(options, dict):
            raise BusinessActionError("business_action_invalid")
        if self.method == "json":
            if set(options) != {"thinking", "temperature"}:
                raise BusinessActionError("business_action_invalid")
            if options["thinking"] not in {None, True, False}:
                raise BusinessActionError("business_action_invalid")
        elif set(options) != {"temperature"}:
            raise BusinessActionError("business_action_invalid")
        temperature = options["temperature"]
        if temperature is not None and (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not math.isfinite(float(temperature))
            or not 0 <= float(temperature) <= 1.5
        ):
            raise BusinessActionError("business_action_invalid")
        object.__setattr__(self, "messages", _freeze_call_value(messages))
        object.__setattr__(self, "tools", _freeze_call_value(tools))
        object.__setattr__(self, "options", _freeze_call_value(options))

    def canonical_dict(self) -> dict[str, object]:
        return {
            "method": self.method,
            "task": self.task,
            "messages": _canonical_call_value(self.messages),
            "tools": _canonical_call_value(self.tools),
            "options": _canonical_call_value(self.options),
            "max_tokens": self.max_tokens,
        }


@dataclass(frozen=True)
class BusinessActionDraft:
    """Server-assembled final envelope input; never accepted from a renderer."""

    outbound: Any
    content_units: tuple[ContentUnit, ...]
    estimated_calls: int
    max_calls: int
    max_tokens: int
    call_plan: tuple[PreparedBusinessCall, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.content_units, tuple)
            or not all(isinstance(unit, ContentUnit) for unit in self.content_units)
            or isinstance(self.estimated_calls, bool)
            or not isinstance(self.estimated_calls, int)
            or self.estimated_calls < 1
            or isinstance(self.max_calls, bool)
            or not isinstance(self.max_calls, int)
            or self.max_calls < self.estimated_calls
            or isinstance(self.max_tokens, bool)
            or not isinstance(self.max_tokens, int)
            or self.max_tokens < 1
            or not isinstance(self.call_plan, tuple)
            or not self.call_plan
            or not all(isinstance(call, PreparedBusinessCall) for call in self.call_plan)
            or len(self.call_plan) != self.estimated_calls
            or len(self.call_plan) > self.max_calls
            or sum(call.max_tokens for call in self.call_plan) > self.max_tokens
        ):
            raise BusinessActionError("business_action_invalid")


class BusinessActionAssembler(Protocol):
    """Existing domain service adapter that constructs the final bounded DTO."""

    def assemble(self, request: object) -> BusinessActionDraft: ...


class BusinessActionExecutor(Protocol):
    """Domain executor that may use only the supplied immutable envelope."""

    def execute(
        self, *, action: PreparedOutbound, ai_client: "BudgetedBusinessAIClient"
    ) -> Mapping[str, Any]: ...


class BusinessAIClientFactory(Protocol):
    def acquire_bound(
        self, action: PreparedOutbound, *, max_attempts: int = 1
    ) -> ContextManager[object]: ...


class BusinessReadinessGate(Protocol):
    def require_business_verification(self, scope: str) -> None: ...


class BusinessResultProjector(Protocol):
    def project(self, result: Mapping[str, Any]) -> Mapping[str, Any]: ...


class BusinessActionClock(Protocol):
    def now(self) -> int: ...


class SystemBusinessActionClock:
    def now(self) -> int:
        return int(time.time())


class SafeBusinessModelSettings:
    """Legacy-compatible model labels without credentials or runtime settings."""

    __slots__ = ("__values",)
    _TASK_ATTRIBUTES = MappingProxyType(
        {
            "extraction_model": "extraction",
            "analysis_model": "analysis",
            "librarian_planning_model": "librarian_planning",
            "librarian_synthesis_model": "librarian_synthesis",
        }
    )

    def __init__(self, task_models: Mapping[str, str]) -> None:
        object.__setattr__(
            self,
            "_SafeBusinessModelSettings__values",
            MappingProxyType(dict(task_models)),
        )

    def __getattribute__(self, name: str) -> object:
        task = object.__getattribute__(self, "_TASK_ATTRIBUTES").get(name)
        if task is None:
            raise AttributeError(name)
        values = object.__getattribute__(
            self, "_SafeBusinessModelSettings__values"
        )
        if task not in values:
            raise BusinessActionError("business_action_invalid")
        return values[task]

    def __setattr__(self, name: str, value: object) -> None:
        raise BusinessActionError("business_action_invalid")


class BudgetedBusinessAIClient:
    """Narrow client that enforces the prepared task/call/token closure."""

    __slots__ = (
        "__client",
        "__allowed",
        "__remaining_calls",
        "__remaining_tokens",
        "__settings",
        "__plan",
        "__position",
    )
    _PUBLIC_ATTRIBUTES = frozenset(
        {
            "request_json",
            "request_tool_message",
            "remaining_calls",
            "remaining_tokens",
            "settings",
        }
    )

    def __init__(self, *, client: object, action: PreparedOutbound) -> None:
        object.__setattr__(self, "_BudgetedBusinessAIClient__client", client)
        object.__setattr__(
            self,
            "_BudgetedBusinessAIClient__allowed",
            MappingProxyType(dict(action.task_models)),
        )
        object.__setattr__(
            self, "_BudgetedBusinessAIClient__remaining_calls", action.max_calls
        )
        object.__setattr__(
            self, "_BudgetedBusinessAIClient__remaining_tokens", action.max_tokens
        )
        object.__setattr__(
            self,
            "_BudgetedBusinessAIClient__settings",
            SafeBusinessModelSettings(dict(action.task_models)),
        )
        try:
            plan = tuple(action.outbound["call_plan"])
        except (KeyError, TypeError) as exc:
            raise BusinessActionError("business_action_invalid") from exc
        object.__setattr__(self, "_BudgetedBusinessAIClient__plan", plan)
        object.__setattr__(self, "_BudgetedBusinessAIClient__position", 0)

    def __getattribute__(self, name: str) -> object:
        if name not in object.__getattribute__(self, "_PUBLIC_ATTRIBUTES"):
            raise BusinessActionError("business_action_invalid")
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value: object) -> None:
        raise BusinessActionError("business_action_invalid")

    @property
    def remaining_calls(self) -> int:
        return object.__getattribute__(
            self, "_BudgetedBusinessAIClient__remaining_calls"
        )

    @property
    def remaining_tokens(self) -> int:
        return object.__getattribute__(
            self, "_BudgetedBusinessAIClient__remaining_tokens"
        )

    @property
    def settings(self) -> SafeBusinessModelSettings:
        return object.__getattribute__(
            self, "_BudgetedBusinessAIClient__settings"
        )

    def request_json(
        self,
        messages: list[dict[str, str]],
        *,
        task: str,
        max_tokens: int,
        thinking: bool | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        authorized = object.__getattribute__(self, "_authorize")(
            method="json",
            task=task,
            messages=messages,
            tools=(),
            options={"thinking": thinking, "temperature": temperature},
            max_tokens=max_tokens,
        )
        try:
            client = object.__getattribute__(
                self, "_BudgetedBusinessAIClient__client"
            )
            method = client.request_json
        except AttributeError as exc:
            raise BusinessActionError("business_action_execution_failed") from exc
        emit_ai_activity("provider_request_started")
        result = method(
            authorized.messages,
            task=authorized.task,
            max_tokens=authorized.max_tokens,
            **authorized.options,
        )
        emit_ai_activity("provider_response_received")
        return result

    def request_tool_message(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        task: str,
        max_tokens: int,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        authorized = object.__getattribute__(self, "_authorize")(
            method="tool",
            task=task,
            messages=messages,
            tools=tools,
            options={"temperature": temperature},
            max_tokens=max_tokens,
        )
        try:
            client = object.__getattribute__(
                self, "_BudgetedBusinessAIClient__client"
            )
            method = client.request_tool_message
        except AttributeError as exc:
            raise BusinessActionError("business_action_execution_failed") from exc
        emit_ai_activity("provider_request_started")
        result = method(
            authorized.messages,
            authorized.tools,
            task=authorized.task,
            max_tokens=authorized.max_tokens,
            **authorized.options,
        )
        emit_ai_activity("provider_response_received")
        return result

    def _authorize(
        self,
        *,
        method: str,
        task: object,
        messages: object,
        tools: object,
        options: object,
        max_tokens: object,
    ) -> "AuthorizedCall":
        position = object.__getattribute__(
            self, "_BudgetedBusinessAIClient__position"
        )
        plan = object.__getattribute__(self, "_BudgetedBusinessAIClient__plan")
        candidate = {
            "method": method,
            "task": task,
            "messages": _canonical_call_value(messages),
            "tools": _canonical_call_value(tools),
            "options": _canonical_call_value(options),
            "max_tokens": max_tokens,
        }
        if (
            not isinstance(task, str)
            or task not in object.__getattribute__(
                self, "_BudgetedBusinessAIClient__allowed"
            )
            or isinstance(max_tokens, bool)
            or not isinstance(max_tokens, int)
            or max_tokens < 1
            or object.__getattribute__(
                self, "_BudgetedBusinessAIClient__remaining_calls"
            ) < 1
            or max_tokens > object.__getattribute__(
                self, "_BudgetedBusinessAIClient__remaining_tokens"
            )
            or position >= len(plan)
            or _canonical_call_value(plan[position]) != candidate
        ):
            raise BusinessActionError("business_action_invalid")
        object.__setattr__(
            self,
            "_BudgetedBusinessAIClient__remaining_calls",
            object.__getattribute__(
                self, "_BudgetedBusinessAIClient__remaining_calls"
            ) - 1,
        )
        object.__setattr__(
            self,
            "_BudgetedBusinessAIClient__remaining_tokens",
            object.__getattribute__(
                self, "_BudgetedBusinessAIClient__remaining_tokens"
            ) - max_tokens,
        )
        object.__setattr__(
            self, "_BudgetedBusinessAIClient__position", position + 1
        )
        planned = _canonical_call_value(plan[position])
        return AuthorizedCall(
            method=planned["method"],
            task=planned["task"],
            messages=planned["messages"],
            tools=planned["tools"],
            options=planned["options"],
            max_tokens=planned["max_tokens"],
        )

    def _assert_complete(self) -> None:
        if object.__getattribute__(
            self, "_BudgetedBusinessAIClient__position"
        ) != len(object.__getattribute__(self, "_BudgetedBusinessAIClient__plan")):
            raise BusinessActionError("business_action_invalid")


class LiteratureDerivedBudgetBusinessAIClient:
    """One-authorization cumulative budget for the reviewed literature runner.

    Only the initial stage is byte-for-byte available when consent is issued.
    Later stages are derived by the fixed literature planner from the captured
    PDF and already validated model results.  The reviewed executor must bind
    each newly frozen stage plan before any request.  This client exposes no
    tool surface and never relaxes the consumed action's provider/model/runtime
    binding or aggregate call/token ceiling.
    """

    __slots__ = (
        "__client",
        "__allowed",
        "__minimum_calls",
        "__remaining_calls",
        "__remaining_tokens",
        "__settings",
        "__plan",
        "__position",
        "__used_calls",
        "__stage_fingerprints",
        "__task_completed",
    )
    _PUBLIC_ATTRIBUTES = frozenset(
        {
            "request_json",
            "resume_json_result",
            "bind_derived_plan",
            "finish_task",
            "remaining_calls",
            "remaining_tokens",
            "settings",
        }
    )

    def __init__(self, *, client: object, action: PreparedOutbound) -> None:
        if (
            action.scope != "literature_extraction"
            or action.executor_id != "literature_extraction_executor"
            or action.executor_version != "v1"
            or tuple(task for task, _model in action.task_models)
            != ("analysis", "extraction")
        ):
            raise BusinessActionError("business_action_invalid")
        try:
            payload = action.outbound["payload"]
            initial_plan = tuple(action.outbound["call_plan"])
            initial_fingerprint = payload["stage_fingerprint"]
            unit = action.units[0]
        except (IndexError, KeyError, TypeError) as exc:
            raise BusinessActionError("business_action_invalid") from exc
        if (
            not isinstance(payload, Mapping)
            or not isinstance(payload.get("stage"), str)
            or re.fullmatch(r"[a-z][a-z0-9_]{2,63}", payload.get("stage")) is None
            or not isinstance(initial_fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", initial_fingerprint) is None
            or len(action.units) != 1
            or unit.kind != "literature_extraction_stage"
            or unit.snapshot_fingerprint != initial_fingerprint
            or unit.sha256 != initial_fingerprint
            or not unit.stable_source_identity.startswith("literature-stage:")
            or payload.get("call_count") != len(initial_plan)
            or payload.get("task_max_calls") != action.max_calls
            or payload.get("task_max_tokens") != action.max_tokens
        ):
            raise BusinessActionError("business_action_invalid")
        allowed = MappingProxyType(dict(action.task_models))
        normalized_plan = LiteratureDerivedBudgetBusinessAIClient._validated_plan(
            initial_plan,
            allowed=allowed,
            remaining_calls=action.max_calls,
            remaining_tokens=action.max_tokens,
        )
        if len(normalized_plan) != action.estimated_calls:
            raise BusinessActionError("business_action_invalid")
        object.__setattr__(self, "_LiteratureDerivedBudgetBusinessAIClient__client", client)
        object.__setattr__(self, "_LiteratureDerivedBudgetBusinessAIClient__allowed", allowed)
        object.__setattr__(
            self,
            "_LiteratureDerivedBudgetBusinessAIClient__minimum_calls",
            action.estimated_calls,
        )
        object.__setattr__(
            self, "_LiteratureDerivedBudgetBusinessAIClient__remaining_calls", action.max_calls
        )
        object.__setattr__(
            self, "_LiteratureDerivedBudgetBusinessAIClient__remaining_tokens", action.max_tokens
        )
        object.__setattr__(
            self,
            "_LiteratureDerivedBudgetBusinessAIClient__settings",
            SafeBusinessModelSettings(dict(action.task_models)),
        )
        object.__setattr__(self, "_LiteratureDerivedBudgetBusinessAIClient__plan", normalized_plan)
        object.__setattr__(self, "_LiteratureDerivedBudgetBusinessAIClient__position", 0)
        object.__setattr__(self, "_LiteratureDerivedBudgetBusinessAIClient__used_calls", 0)
        object.__setattr__(
            self,
            "_LiteratureDerivedBudgetBusinessAIClient__stage_fingerprints",
            {initial_fingerprint},
        )
        object.__setattr__(self, "_LiteratureDerivedBudgetBusinessAIClient__task_completed", False)

    def __getattribute__(self, name: str) -> object:
        if name not in object.__getattribute__(self, "_PUBLIC_ATTRIBUTES"):
            raise BusinessActionError("business_action_invalid")
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value: object) -> None:
        raise BusinessActionError("business_action_invalid")

    @property
    def remaining_calls(self) -> int:
        return object.__getattribute__(
            self, "_LiteratureDerivedBudgetBusinessAIClient__remaining_calls"
        )

    @property
    def remaining_tokens(self) -> int:
        return object.__getattribute__(
            self, "_LiteratureDerivedBudgetBusinessAIClient__remaining_tokens"
        )

    @property
    def settings(self) -> SafeBusinessModelSettings:
        return object.__getattribute__(
            self, "_LiteratureDerivedBudgetBusinessAIClient__settings"
        )

    @staticmethod
    def _validated_plan(
        plan: Sequence[object],
        *,
        allowed: Mapping[str, str],
        remaining_calls: int,
        remaining_tokens: int,
    ) -> tuple[dict[str, object], ...]:
        if (
            isinstance(plan, (str, bytes, bytearray))
            or not isinstance(plan, Sequence)
            or not plan
            or len(plan) > remaining_calls
        ):
            raise BusinessActionError("business_action_invalid")
        normalized: list[dict[str, object]] = []
        token_total = 0
        byte_total = 0
        for value in plan:
            raw = (
                value.canonical_dict()
                if isinstance(value, PreparedBusinessCall)
                else _canonical_call_value(value)
            )
            if not isinstance(raw, Mapping) or set(raw) != {
                "method", "task", "messages", "tools", "options", "max_tokens"
            }:
                raise BusinessActionError("business_action_invalid")
            try:
                call = PreparedBusinessCall(
                    method=raw["method"],
                    task=raw["task"],
                    messages=tuple(raw["messages"]),
                    tools=tuple(raw["tools"]),
                    options=raw["options"],
                    max_tokens=raw["max_tokens"],
                )
            except (KeyError, TypeError) as exc:
                raise BusinessActionError("business_action_invalid") from exc
            canonical = call.canonical_dict()
            messages = canonical["messages"]
            if (
                call.method != "json"
                or call.tools
                or call.task not in allowed
                or call.max_tokens > 32_000
                or not isinstance(messages, list)
                or not messages
                or any(
                    not isinstance(message, Mapping)
                    or set(message) != {"role", "content"}
                    or message.get("role") not in {"system", "user", "assistant"}
                    or not isinstance(message.get("content"), str)
                    or not message.get("content")
                    for message in messages
                )
                or canonical != raw
            ):
                raise BusinessActionError("business_action_invalid")
            token_total += call.max_tokens
            byte_total += len(
                json.dumps(
                    canonical,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            )
            normalized.append(canonical)
        if (
            token_total > remaining_tokens
            or byte_total > MAX_DERIVED_LITERATURE_STAGE_BYTES
        ):
            raise BusinessActionError("business_action_invalid")
        return tuple(normalized)

    def bind_derived_plan(
        self,
        calls: Sequence[PreparedBusinessCall],
        *,
        stage_fingerprint: str,
    ) -> None:
        position = object.__getattribute__(
            self, "_LiteratureDerivedBudgetBusinessAIClient__position"
        )
        current_plan = object.__getattribute__(
            self, "_LiteratureDerivedBudgetBusinessAIClient__plan"
        )
        fingerprints = object.__getattribute__(
            self, "_LiteratureDerivedBudgetBusinessAIClient__stage_fingerprints"
        )
        if (
            position != len(current_plan)
            or not isinstance(stage_fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", stage_fingerprint) is None
            or stage_fingerprint in fingerprints
            or object.__getattribute__(
                self, "_LiteratureDerivedBudgetBusinessAIClient__task_completed"
            )
        ):
            raise BusinessActionError("business_action_invalid")
        normalized = object.__getattribute__(self, "_validated_plan")(
            calls,
            allowed=object.__getattribute__(
                self, "_LiteratureDerivedBudgetBusinessAIClient__allowed"
            ),
            remaining_calls=object.__getattribute__(
                self, "_LiteratureDerivedBudgetBusinessAIClient__remaining_calls"
            ),
            remaining_tokens=object.__getattribute__(
                self, "_LiteratureDerivedBudgetBusinessAIClient__remaining_tokens"
            ),
        )
        fingerprints.add(stage_fingerprint)
        object.__setattr__(self, "_LiteratureDerivedBudgetBusinessAIClient__plan", normalized)
        object.__setattr__(self, "_LiteratureDerivedBudgetBusinessAIClient__position", 0)

    def request_json(
        self,
        messages: list[dict[str, Any]],
        *,
        task: str,
        max_tokens: int,
        thinking: bool | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        planned, position, call_limit = object.__getattribute__(
            self, "_consume_json_plan"
        )(
            messages,
            task=task,
            max_tokens=max_tokens,
            thinking=thinking,
            temperature=temperature,
        )
        try:
            client = object.__getattribute__(
                self, "_LiteratureDerivedBudgetBusinessAIClient__client"
            )
            method = client.request_json
        except AttributeError as exc:
            raise BusinessActionError("business_action_execution_failed") from exc
        call_index = position + 1
        emit_ai_activity(
            "provider_request_started", call_index=call_index, call_limit=call_limit
        )
        result = method(
            planned["messages"],
            task=planned["task"],
            max_tokens=planned["max_tokens"],
            **planned["options"],
        )
        emit_ai_activity(
            "provider_response_received", call_index=call_index, call_limit=call_limit
        )
        if not isinstance(result, Mapping):
            raise BusinessActionError("business_action_execution_failed")
        return dict(result)

    def resume_json_result(
        self,
        messages: list[dict[str, Any]],
        *,
        task: str,
        max_tokens: int,
        thinking: bool | None,
        temperature: float | None,
        result: Mapping[str, Any],
        result_digest: str,
    ) -> dict[str, Any]:
        """Consume one sealed successful receipt without repeating a paid call.

        The trusted literature executor may use this only after the checkpoint
        authority has authenticated both the result and its receipt.  The call
        must still match the next prepared plan item exactly and consumes the
        same local call/token budget as a live provider request.
        """

        normalized = _canonical_call_value(dict(result))
        if (
            not isinstance(normalized, dict)
            or not isinstance(result_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", result_digest) is None
            or hashlib.sha256(
                json.dumps(
                    normalized,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
            != result_digest
        ):
            raise BusinessActionError("business_action_invalid")
        object.__getattribute__(self, "_consume_json_plan")(
            messages,
            task=task,
            max_tokens=max_tokens,
            thinking=thinking,
            temperature=temperature,
        )
        return normalized

    def _consume_json_plan(
        self,
        messages: list[dict[str, Any]],
        *,
        task: str,
        max_tokens: int,
        thinking: bool | None,
        temperature: float | None,
    ) -> tuple[dict[str, object], int, int]:
        position = object.__getattribute__(
            self, "_LiteratureDerivedBudgetBusinessAIClient__position"
        )
        plan = object.__getattribute__(
            self, "_LiteratureDerivedBudgetBusinessAIClient__plan"
        )
        candidate = {
            "method": "json",
            "task": task,
            "messages": _canonical_call_value(messages),
            "tools": [],
            "options": _canonical_call_value(
                {"thinking": thinking, "temperature": temperature}
            ),
            "max_tokens": max_tokens,
        }
        if (
            position >= len(plan)
            or plan[position] != candidate
            or object.__getattribute__(
                self, "_LiteratureDerivedBudgetBusinessAIClient__remaining_calls"
            ) < 1
            or isinstance(max_tokens, bool)
            or not isinstance(max_tokens, int)
            or max_tokens < 1
            or max_tokens
            > object.__getattribute__(
                self, "_LiteratureDerivedBudgetBusinessAIClient__remaining_tokens"
            )
        ):
            raise BusinessActionError("business_action_invalid")
        planned = _canonical_call_value(plan[position])
        object.__setattr__(
            self,
            "_LiteratureDerivedBudgetBusinessAIClient__remaining_calls",
            object.__getattribute__(
                self, "_LiteratureDerivedBudgetBusinessAIClient__remaining_calls"
            ) - 1,
        )
        object.__setattr__(
            self,
            "_LiteratureDerivedBudgetBusinessAIClient__remaining_tokens",
            object.__getattribute__(
                self, "_LiteratureDerivedBudgetBusinessAIClient__remaining_tokens"
            ) - max_tokens,
        )
        object.__setattr__(
            self, "_LiteratureDerivedBudgetBusinessAIClient__position", position + 1
        )
        object.__setattr__(
            self,
            "_LiteratureDerivedBudgetBusinessAIClient__used_calls",
            object.__getattribute__(
                self, "_LiteratureDerivedBudgetBusinessAIClient__used_calls"
            ) + 1,
        )
        return planned, position, len(plan)

    def finish_task(self, completion: Mapping[str, Any]) -> None:
        if (
            object.__getattribute__(
                self, "_LiteratureDerivedBudgetBusinessAIClient__position"
            )
            != len(object.__getattribute__(self, "_LiteratureDerivedBudgetBusinessAIClient__plan"))
            or not isinstance(completion, Mapping)
            or completion.get("schema_version")
            != "literature-extraction-commit-result-v2"
            or completion.get("status") not in {"completed", "saved_index_pending"}
        ):
            raise BusinessActionError("business_action_invalid")
        object.__setattr__(
            self, "_LiteratureDerivedBudgetBusinessAIClient__task_completed", True
        )

    def _assert_complete(self) -> None:
        if (
            not object.__getattribute__(
                self, "_LiteratureDerivedBudgetBusinessAIClient__task_completed"
            )
            or object.__getattribute__(
                self, "_LiteratureDerivedBudgetBusinessAIClient__used_calls"
            )
            < object.__getattribute__(
                self, "_LiteratureDerivedBudgetBusinessAIClient__minimum_calls"
            )
            or object.__getattribute__(
                self, "_LiteratureDerivedBudgetBusinessAIClient__position"
            )
            != len(object.__getattribute__(self, "_LiteratureDerivedBudgetBusinessAIClient__plan"))
        ):
            raise BusinessActionError("business_action_invalid")


class HarnessBudgetedBusinessAIClient:
    """Cumulative budget used only by the audited Harness executor.

    Harness derives later model messages from verified tool results, so those
    messages cannot be byte-for-byte frozen before consent.  This client keeps
    the security boundary at the consumed prepared action: fixed provider and
    model binding, fixed task set, maximum calls/tokens, and an exact tool-name
    allowlist.  It never exposes the underlying credential or endpoint.
    """

    __slots__ = (
        "__client",
        "__allowed",
        "__minimum_calls",
        "__remaining_calls",
        "__remaining_tokens",
        "__settings",
        "__used_calls",
    )
    _PUBLIC_ATTRIBUTES = frozenset(
        {
            "__class__",
            "request_tool_message",
            "remaining_calls",
            "remaining_tokens",
            "settings",
        }
    )

    def __init__(self, *, client: object, action: PreparedOutbound) -> None:
        if action.scope not in {"librarian", "selected_evidence_chat"}:
            raise BusinessActionError("business_action_invalid")
        object.__setattr__(self, "_HarnessBudgetedBusinessAIClient__client", client)
        object.__setattr__(
            self,
            "_HarnessBudgetedBusinessAIClient__allowed",
            MappingProxyType(dict(action.task_models)),
        )
        object.__setattr__(
            self,
            "_HarnessBudgetedBusinessAIClient__minimum_calls",
            action.estimated_calls,
        )
        object.__setattr__(
            self,
            "_HarnessBudgetedBusinessAIClient__remaining_calls",
            action.max_calls,
        )
        object.__setattr__(
            self,
            "_HarnessBudgetedBusinessAIClient__remaining_tokens",
            action.max_tokens,
        )
        object.__setattr__(
            self,
            "_HarnessBudgetedBusinessAIClient__settings",
            SafeBusinessModelSettings(dict(action.task_models)),
        )
        object.__setattr__(self, "_HarnessBudgetedBusinessAIClient__used_calls", 0)

    def __getattribute__(self, name: str) -> object:
        if name not in object.__getattribute__(self, "_PUBLIC_ATTRIBUTES"):
            raise BusinessActionError("business_action_invalid")
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value: object) -> None:
        raise BusinessActionError("business_action_invalid")

    @property
    def remaining_calls(self) -> int:
        return object.__getattribute__(
            self, "_HarnessBudgetedBusinessAIClient__remaining_calls"
        )

    @property
    def remaining_tokens(self) -> int:
        return object.__getattribute__(
            self, "_HarnessBudgetedBusinessAIClient__remaining_tokens"
        )

    @property
    def settings(self) -> SafeBusinessModelSettings:
        return object.__getattribute__(
            self, "_HarnessBudgetedBusinessAIClient__settings"
        )

    def request_tool_message(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        task: str,
        max_tokens: int,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        allowed = object.__getattribute__(
            self, "_HarnessBudgetedBusinessAIClient__allowed"
        )
        remaining_calls = object.__getattribute__(
            self, "_HarnessBudgetedBusinessAIClient__remaining_calls"
        )
        remaining_tokens = object.__getattribute__(
            self, "_HarnessBudgetedBusinessAIClient__remaining_tokens"
        )
        normalized_messages = _canonical_call_value(messages)
        normalized_tools = _canonical_call_value(tools)
        if (
            not isinstance(task, str)
            or task not in allowed
            or isinstance(max_tokens, bool)
            or not isinstance(max_tokens, int)
            or max_tokens < 1
            or remaining_calls < 1
            or max_tokens > remaining_tokens
            or not isinstance(normalized_messages, list)
            or not normalized_messages
            or not isinstance(normalized_tools, list)
            or not _harness_tools_allowed(normalized_tools)
            or isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not math.isfinite(float(temperature))
            or not 0 <= float(temperature) <= 1.5
        ):
            raise BusinessActionError("business_action_invalid")
        object.__setattr__(
            self,
            "_HarnessBudgetedBusinessAIClient__remaining_calls",
            remaining_calls - 1,
        )
        object.__setattr__(
            self,
            "_HarnessBudgetedBusinessAIClient__remaining_tokens",
            remaining_tokens - max_tokens,
        )
        object.__setattr__(
            self,
            "_HarnessBudgetedBusinessAIClient__used_calls",
            object.__getattribute__(
                self, "_HarnessBudgetedBusinessAIClient__used_calls"
            )
            + 1,
        )
        try:
            client = object.__getattribute__(
                self, "_HarnessBudgetedBusinessAIClient__client"
            )
            method = client.request_tool_message
        except AttributeError as exc:
            raise BusinessActionError("business_action_execution_failed") from exc
        return method(
            normalized_messages,
            normalized_tools,
            task=task,
            max_tokens=max_tokens,
            temperature=float(temperature),
        )

    def _assert_complete(self) -> None:
        used = object.__getattribute__(
            self, "_HarnessBudgetedBusinessAIClient__used_calls"
        )
        minimum = object.__getattribute__(
            self, "_HarnessBudgetedBusinessAIClient__minimum_calls"
        )
        if used < minimum:
            raise BusinessActionError("business_action_invalid")


def _harness_tools_allowed(tools: list[object]) -> bool:
    for value in tools:
        if not isinstance(value, Mapping) or set(value) != {"type", "function"}:
            return False
        function = value.get("function")
        if (
            value.get("type") != "function"
            or not isinstance(function, Mapping)
            or set(function) - {"name", "description", "parameters", "strict"}
            or _HARNESS_TOOL_NAME_RE.fullmatch(str(function.get("name") or "")) is None
        ):
            return False
    return True


@dataclass(frozen=True)
class AuthorizedCall:
    """Fresh plain copy of one call from the immutable authorized plan."""

    method: str
    task: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    options: dict[str, Any]
    max_tokens: int


@dataclass(frozen=True)
class _Policy:
    scope: str
    task: str
    allowed_tasks: tuple[str, ...]
    executor_id: str
    executor_version: str
    max_calls: int
    max_tokens: int


_POLICIES = MappingProxyType(
    {
        "librarian": _Policy(
            "librarian",
            # The current Librarian owns recall and citation validation
            # locally, then spends one bounded Flash turn on the final report.
            # Keep the prepared-action authority aligned with that real call;
            # otherwise the provider factory silently selects the historical
            # Pro synthesis model even when the assembled plan is one Flash
            # call.
            "librarian_planning",
            ("librarian_planning",),
            "librarian_business_executor",
            "v1",
            1,
            2_400,
        ),
        "selected_evidence_chat": _Policy(
            "selected_evidence_chat",
            "extraction",
            ("extraction",),
            "selected_evidence_chat_executor",
            "v1",
            1,
            2_400,
        ),
        "literature_extraction": _Policy(
            "literature_extraction",
            "extraction",
            ("analysis", "extraction"),
            "literature_extraction_executor",
            "v1",
            512,
            8_200_000,
        ),
        "personal_suggestion": _Policy(
            "personal_suggestion",
            "analysis",
            ("analysis",),
            "personal_suggestion_executor",
            "v1",
            1,
            16_000,
        ),
    }
)


class BusinessPreparedActionRegistry:
    """Prepare and execute four AI business scopes without owning domain logic."""

    def __init__(
        self,
        *,
        prepared_actions: PreparedActionService,
        client_factory: BusinessAIClientFactory,
        assemblers: Mapping[str, BusinessActionAssembler],
        executors: Mapping[str, BusinessActionExecutor],
        projectors: Mapping[str, BusinessResultProjector],
        clock: BusinessActionClock | None = None,
        receipt_capacity: int = MAX_EXECUTION_RECEIPTS,
        readiness_gate: BusinessReadinessGate | None = None,
    ) -> None:
        if (
            set(assemblers) != BUSINESS_ACTION_SCOPES
            or set(executors) != BUSINESS_ACTION_SCOPES
            or set(projectors) != BUSINESS_ACTION_SCOPES
            or isinstance(receipt_capacity, bool)
            or not isinstance(receipt_capacity, int)
            or not 1 <= receipt_capacity <= MAX_EXECUTION_RECEIPTS
        ):
            raise BusinessActionError("business_action_invalid")
        self._prepared = prepared_actions
        self._client_factory = client_factory
        self._assemblers = MappingProxyType(dict(assemblers))
        self._executors = MappingProxyType(dict(executors))
        self._projectors = MappingProxyType(dict(projectors))
        self._clock = clock or SystemBusinessActionClock()
        self._readiness_gate = readiness_gate
        self._receipt_capacity = receipt_capacity
        self._lock = threading.Lock()
        self._executed: dict[str, int] = {}

    def prepare(
        self,
        *,
        scope: str,
        session_id: str,
        request: object,
    ) -> dict[str, object]:
        policy = self._policy(scope)
        local_result = getattr(self._assemblers[scope], "local_result", None)
        if callable(local_result):
            try:
                result = local_result(request)
            except BusinessActionError:
                raise
            except Exception as exc:
                raise BusinessActionError("business_action_prepare_failed") from exc
            if result is not None:
                try:
                    return _public_result(self._projectors[scope].project(result))
                except BusinessActionError:
                    raise
                except Exception as exc:
                    raise BusinessActionError("business_action_result_invalid") from exc
        # Run only a domain-owned, non-mutating local prerequisite check before
        # the provider readiness gate.  This keeps actionable failures such as
        # a missing PDF visible without creating a job or making a model call.
        preflight = getattr(self._assemblers[scope], "preflight", None)
        if callable(preflight):
            try:
                preflight(request)
            except BusinessActionError:
                raise
            except Exception as exc:
                raise BusinessActionError("business_action_prepare_failed") from exc
        self._require_ready(scope, error_code="business_action_prepare_failed")
        try:
            draft = self._assemblers[scope].assemble(request)
        except BusinessActionError:
            raise
        except Exception as exc:
            raise BusinessActionError("business_action_prepare_failed") from exc
        if not isinstance(draft, BusinessActionDraft):
            raise BusinessActionError("business_action_prepare_failed")
        if (
            draft.max_calls > policy.max_calls
            or draft.max_tokens > policy.max_tokens
            or any(call.task not in policy.allowed_tasks for call in draft.call_plan)
        ):
            raise BusinessActionError("business_action_invalid")
        try:
            return self._prepared.prepare(
                session_id=session_id,
                scope=scope,
                task=policy.task,
                outbound={
                    "payload": draft.outbound,
                    "call_plan": [call.canonical_dict() for call in draft.call_plan],
                },
                content_units=draft.content_units,
                allowed_tasks=policy.allowed_tasks,
                executor_id=policy.executor_id,
                executor_version=policy.executor_version,
                estimated_calls=draft.estimated_calls,
                max_calls=draft.max_calls,
                max_tokens=draft.max_tokens,
            )
        except PreparedActionError as exc:
            raise BusinessActionError("business_action_prepare_failed") from exc

    def execute(
        self,
        action: PreparedOutbound,
        activity_callback: ActivityObserver | None = None,
    ) -> dict[str, object]:
        if not isinstance(action, PreparedOutbound):
            raise BusinessActionError("business_action_invalid")
        policy = self._policy(action.scope)
        self._require_ready(action.scope, error_code="business_action_execution_failed")
        self._validate_consumed_action(action, policy)
        with self._lock:
            now = self._now()
            for action_id, expires_at in tuple(self._executed.items()):
                if expires_at <= now:
                    self._executed.pop(action_id, None)
            if now < action.issued_at or now >= action.expires_at:
                raise BusinessActionError("business_action_invalid")
            if action.action_id in self._executed:
                raise BusinessActionError("business_action_replayed")
            if len(self._executed) >= self._receipt_capacity:
                raise BusinessActionError("business_action_store_full")
            # Mark before creating a client or calling a domain port. A failed
            # attempt must not be replayed into a second billable request.
            self._executed[action.action_id] = action.expires_at
        try:
            with bind_activity_observer(activity_callback):
                with self._client_factory.acquire_bound(
                    action, max_attempts=1
                ) as raw_client:
                    emit_ai_activity("provider_acquired")
                    executor = self._executors[action.scope]
                    if getattr(executor, "requires_literature_derived_budget", False) is True:
                        if action.scope != "literature_extraction":
                            raise BusinessActionError("business_action_invalid")
                        client = LiteratureDerivedBudgetBusinessAIClient(
                            client=raw_client,
                            action=action,
                        )
                    elif getattr(executor, "requires_harness_budget", False) is True:
                        client = HarnessBudgetedBusinessAIClient(
                            client=raw_client,
                            action=action,
                        )
                    else:
                        client = BudgetedBusinessAIClient(
                            client=raw_client,
                            action=action,
                        )
                    result = executor.execute(
                        action=action,
                        ai_client=client,
                    )
                    object.__getattribute__(client, "_assert_complete")()
                    emit_ai_activity("result_validating")
                    result = self._projectors[action.scope].project(result)
        except BusinessActionError:
            raise
        except Exception as exc:
            cause_code = getattr(exc, "code", "")
            if cause_code == "ai_provider_outcome_unknown":
                raise BusinessActionError(
                    "business_action_execution_failed",
                    cause_code="ai_provider_outcome_unknown",
                    stage="provider_call",
                    next_action="review_call_outcome",
                ) from exc
            runtime_failure = {
                "ai_runtime_binding_stale": (
                    "runtime_binding",
                    "verify_connection",
                ),
                "ai_provider_not_configured": (
                    "provider_setup",
                    "save_credential",
                ),
                "ai_provider_unavailable": (
                    "provider_call",
                    "retry_same_request",
                ),
                "ai_provider_response_invalid": (
                    "provider_call",
                    "check_provider_configuration",
                ),
                "ai_provider_capability_missing": (
                    "provider_call",
                    "select_supported_model",
                ),
            }.get(cause_code)
            if runtime_failure is not None:
                stage, next_action = runtime_failure
                raise BusinessActionError(
                    "business_action_execution_failed",
                    cause_code=cause_code,
                    stage=stage,
                    next_action=next_action,
                ) from exc
            raise BusinessActionError("business_action_execution_failed") from exc
        return _public_result(result)

    def _now(self) -> int:
        try:
            value = self._clock.now()
        except Exception as exc:
            raise BusinessActionError("business_action_execution_failed") from exc
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise BusinessActionError("business_action_execution_failed")
        return value

    def _require_ready(self, scope: str, *, error_code: str) -> None:
        if self._readiness_gate is None:
            return
        try:
            self._readiness_gate.require_business_verification(scope)
        except Exception as exc:
            runtime_code = getattr(exc, "code", "")
            needs_test = runtime_code == "ai_runtime_verification_required"
            raise BusinessActionError(
                error_code,
                cause_code=(
                    "ai_business_verification_required"
                    if needs_test
                    else "ai_readiness_unavailable"
                ),
                stage="readiness",
                next_action=(
                    "test_business_capability" if needs_test else "retry_readiness"
                ),
            ) from exc

    @staticmethod
    def _policy(scope: object) -> _Policy:
        if not isinstance(scope, str) or scope not in BUSINESS_ACTION_SCOPES:
            raise BusinessActionError("business_action_scope_unsupported")
        return _POLICIES[scope]

    @staticmethod
    def _validate_consumed_action(action: PreparedOutbound, policy: _Policy) -> None:
        if (
            action.scope != policy.scope
            or action.task != policy.task
            or tuple(task for task, _model in action.task_models)
            != tuple(sorted(policy.allowed_tasks))
            or action.executor_id != policy.executor_id
            or action.executor_version != policy.executor_version
            or action.estimated_calls < 1
            or action.estimated_calls > action.max_calls
            or action.max_calls > policy.max_calls
            or action.max_tokens < 1
            or action.max_tokens > policy.max_tokens
            or action.byte_count < 2
            or action.byte_count > SCOPE_BYTE_CAPS[policy.scope]
            or set(action.models) != {
                model for _task, model in action.task_models
            }
            or _SAFE_EXECUTOR_RE.fullmatch(action.executor_id) is None
        ):
            raise BusinessActionError("business_action_invalid")


def _public_result(value: object) -> dict[str, object]:
    nodes = [0]

    def normalize(item: object, depth: int) -> object:
        nodes[0] += 1
        if nodes[0] > 2_000 or depth > 12:
            raise BusinessActionError("business_action_result_invalid")
        if item is None or isinstance(item, (bool, int)):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise BusinessActionError("business_action_result_invalid")
            return item
        if isinstance(item, str):
            if _LOCAL_VALUE_RE.search(item):
                raise BusinessActionError("business_action_result_invalid")
            return item
        if isinstance(item, Mapping):
            output: dict[str, object] = {}
            for key, child in item.items():
                if (
                    not isinstance(key, str)
                    or not key
                    or _SENSITIVE_KEY_RE.search(key)
                    and key not in _PUBLIC_SIGNED_TOKEN_KEYS
                ):
                    raise BusinessActionError("business_action_result_invalid")
                if key in _PUBLIC_SIGNED_TOKEN_KEYS and (
                    not isinstance(child, str)
                    or (
                        child == ""
                        and key not in _OPTIONAL_EMPTY_PUBLIC_SIGNED_TOKEN_KEYS
                    )
                    or len(child) > MAX_PUBLIC_SIGNED_TOKEN_LENGTH
                ):
                    raise BusinessActionError("business_action_result_invalid")
                output[key] = normalize(child, depth + 1)
            return output
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            return [normalize(child, depth + 1) for child in item]
        raise BusinessActionError("business_action_result_invalid")

    if not isinstance(value, Mapping):
        raise BusinessActionError("business_action_result_invalid")
    normalized = normalize(value, 0)
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > MAX_PUBLIC_RESULT_BYTES:
        raise BusinessActionError("business_action_result_invalid")
    return dict(normalized)


__all__ = [
    "BUSINESS_ACTION_SCOPES",
    "AuthorizedCall",
    "BusinessAIClientFactory",
    "BusinessReadinessGate",
    "BusinessActionClock",
    "BusinessActionAssembler",
    "BusinessActionDraft",
    "BusinessActionError",
    "BusinessActionExecutor",
    "BusinessPreparedActionRegistry",
    "BusinessResultProjector",
    "BudgetedBusinessAIClient",
    "HarnessBudgetedBusinessAIClient",
    "LiteratureDerivedBudgetBusinessAIClient",
    "SafeBusinessModelSettings",
    "PreparedBusinessCall",
    "SystemBusinessActionClock",
]
