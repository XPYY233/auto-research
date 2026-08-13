from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import threading
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence

from auto_research.ai.provider_registry import TASK_IDS
from auto_research.settings.ai_runtime_state import RuntimeActionBinding

from .consent import (
    AI_CONSENT_SCOPES,
    DISCLOSURE_VERSIONS,
    AIConsentError,
    AIConsentService,
    PreparedConsentBinding,
)


PREPARED_ACTION_SCHEMA_VERSION = "server-prepared-ai-action-v1"
PREPARED_ACTION_TTL_SECONDS = 5 * 60
MAX_PREPARED_ACTIONS = 32
MAX_ACTIONS_PER_SESSION = 8
MAX_TOTAL_PREPARED_BYTES = 16 * 1024 * 1024
SCOPE_BYTE_CAPS = MappingProxyType(
    {
        "capability_test": 64 * 1024,
        "personal_suggestion": 256 * 1024,
        "selected_evidence_chat": 1024 * 1024,
        "librarian": 2 * 1024 * 1024,
        "literature_extraction": 4 * 1024 * 1024,
    }
)
MAX_CONTENT_UNITS = 256
MAX_VALUE_DEPTH = 12
MAX_VALUE_NODES = 2_000
_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,255}$")
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_SENSITIVE_KEY_RE = re.compile(
    r"(?:^|_)(?:api_?key|secret|password|credential|token|nonce|path)(?:$|_)",
    re.IGNORECASE,
)
_LOCAL_VALUE_RE = re.compile(
    r"(?:^|[\s='\"])(?:~[/\\]|/(?:Users|private|tmp|var|etc|usr|root|srv|mnt|media|opt|Applications|Library|System)(?:[/\\]|$)|/[^/\s]+[/\\][^\s]*|[A-Za-z]:[\\/]|\\\\|file:|sqlite:)",
    re.IGNORECASE,
)
_ERRORS = {
    "prepared_action_invalid": ("AI 待执行动作无效。", False),
    "prepared_action_expired": ("AI 待执行动作已过期，请重新准备。", False),
    "prepared_action_consumed": ("AI 待执行动作已使用，请重新准备。", False),
    "prepared_action_stale": ("AI 待执行内容或运行设置已变化，请重新准备。", False),
    "prepared_action_store_full": ("AI 待执行动作过多，请稍后再试。", True),
    "prepared_action_state_unavailable": ("AI 待执行动作服务暂时不可用。", True),
}


class PreparedActionError(RuntimeError):
    def __init__(self, code: str) -> None:
        if code not in _ERRORS:
            raise ValueError("unsupported prepared action error code")
        message, retryable = _ERRORS[code]
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": "prepared-action-error-v1",
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.retryable,
        }


class PreparedActionClock(Protocol):
    def now(self) -> int: ...


class SystemPreparedActionClock:
    def now(self) -> int:
        return int(time.time())


class RuntimeActionAuthority(Protocol):
    def action_binding(self) -> RuntimeActionBinding: ...


class ContentSnapshotAuthority(Protocol):
    def fingerprint_for(
        self, *, kind: str, stable_source_identity: str
    ) -> str: ...


@dataclass(frozen=True)
class ContentUnit:
    kind: str
    stable_source_identity: str
    snapshot_fingerprint: str
    length: int
    sha256: str

    def __post_init__(self) -> None:
        if (
            _SAFE_ID_RE.fullmatch(self.kind) is None
            or _SAFE_ID_RE.fullmatch(self.stable_source_identity) is None
            or _HASH_RE.fullmatch(self.snapshot_fingerprint) is None
            or isinstance(self.length, bool)
            or not isinstance(self.length, int)
            or self.length < 0
            or _HASH_RE.fullmatch(self.sha256) is None
        ):
            raise PreparedActionError("prepared_action_invalid")


@dataclass(frozen=True)
class PreparedOutbound:
    action_id: str
    session_digest: str
    scope: str
    provider_id: str
    runtime_revision: int
    credential_generation: int
    task: str
    task_models: tuple[tuple[str, str], ...]
    models: tuple[str, ...]
    executor_id: str
    executor_version: str
    estimated_calls: int
    max_calls: int
    max_tokens: int
    outbound: Any
    outbound_digest: str
    manifest_digest: str
    units: tuple[ContentUnit, ...]
    byte_count: int
    issued_at: int
    expires_at: int


def _canonical(value: Any, *, depth: int, nodes: list[int]) -> Any:
    nodes[0] += 1
    if nodes[0] > MAX_VALUE_NODES or depth > MAX_VALUE_DEPTH:
        raise PreparedActionError("prepared_action_invalid")
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and _LOCAL_VALUE_RE.search(value):
            raise PreparedActionError("prepared_action_invalid")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PreparedActionError("prepared_action_invalid")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or _SENSITIVE_KEY_RE.search(key):
                raise PreparedActionError("prepared_action_invalid")
            result[key] = _canonical(item, depth=depth + 1, nodes=nodes)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical(item, depth=depth + 1, nodes=nodes) for item in value]
    raise PreparedActionError("prepared_action_invalid")


def _canonical_bytes(value: Any, *, byte_cap: int) -> tuple[Any, bytes]:
    normalized = _canonical(value, depth=0, nodes=[0])
    try:
        payload = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PreparedActionError("prepared_action_invalid") from exc
    if len(payload) > byte_cap:
        raise PreparedActionError("prepared_action_invalid")
    return normalized, payload


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _manifest_digest(
    *,
    scope: str,
    binding: RuntimeActionBinding,
    task: str,
    task_models: tuple[tuple[str, str], ...],
    models: tuple[str, ...],
    executor_id: str,
    executor_version: str,
    estimated_calls: int,
    max_calls: int,
    max_tokens: int,
    outbound_digest: str,
    units: tuple[ContentUnit, ...],
) -> str:
    manifest = {
        "domain": "auto-research/server-prepared-ai-action/v1",
        "scope": scope,
        "provider_id": binding.provider_id,
        "runtime_revision": binding.selection_revision,
        "credential_generation": binding.credential_generation,
        "activation": binding.activation,
        "task": task,
        "task_models": dict(task_models),
        "models": list(models),
        "executor_id": executor_id,
        "executor_version": executor_version,
        "call_budget": {"estimated": estimated_calls, "maximum": max_calls},
        "token_budget": max_tokens,
        "outbound_digest": outbound_digest,
        "content_units": [
            {
                "kind": unit.kind,
                "stable_source_identity": unit.stable_source_identity,
                "snapshot_fingerprint": unit.snapshot_fingerprint,
                "length": unit.length,
                "sha256": unit.sha256,
            }
            for unit in units
        ],
    }
    payload = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class PreparedActionService:
    """Bounded process-local authority for immutable server-assembled AI DTOs."""

    def __init__(
        self,
        *,
        runtime_state: RuntimeActionAuthority,
        consents: AIConsentService,
        snapshots: ContentSnapshotAuthority | None = None,
        clock: PreparedActionClock | None = None,
    ) -> None:
        self._runtime = runtime_state
        self._consents = consents
        self._snapshots = snapshots
        self._clock = clock or SystemPreparedActionClock()
        self._lock = threading.RLock()
        self._actions: dict[str, PreparedOutbound] = {}
        self._consumed: dict[str, int] = {}

    def prepare(
        self,
        *,
        session_id: str,
        scope: str,
        task: str,
        outbound: Any,
        content_units: Sequence[ContentUnit] = (),
        allowed_tasks: Sequence[str] | None = None,
        models: Sequence[str] | None = None,
        executor_id: str,
        executor_version: str,
        estimated_calls: int,
        max_calls: int,
        max_tokens: int,
        expected_provider_id: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, object]:
        if _SESSION_RE.fullmatch(session_id) is None or scope not in AI_CONSENT_SCOPES:
            raise PreparedActionError("prepared_action_invalid")
        binding = self._binding()
        if scope != "capability_test" and binding.activation not in {
            "legacy_compatible",
            "connection_verified",
        }:
            raise PreparedActionError("prepared_action_stale")
        if (
            expected_provider_id is not None
            and binding.provider_id != expected_provider_id
        ) or (
            expected_revision is not None
            and binding.selection_revision != expected_revision
        ):
            raise PreparedActionError("prepared_action_stale")
        selected_task_models = self._task_models(
            binding, scope, task, allowed_tasks, models
        )
        selected_models = tuple(sorted(set(dict(selected_task_models).values())))
        if (
            _SAFE_ID_RE.fullmatch(executor_id) is None
            or _SAFE_ID_RE.fullmatch(executor_version) is None
            or isinstance(estimated_calls, bool)
            or not isinstance(estimated_calls, int)
            or estimated_calls < 0
            or isinstance(max_calls, bool)
            or not isinstance(max_calls, int)
            or max_calls < 1
            or estimated_calls > max_calls
            or isinstance(max_tokens, bool)
            or not isinstance(max_tokens, int)
            or max_tokens < 1
            or max_tokens > 10_000_000
        ):
            raise PreparedActionError("prepared_action_invalid")
        units = tuple(content_units)
        if len(units) > MAX_CONTENT_UNITS or not all(
            isinstance(unit, ContentUnit) for unit in units
        ):
            raise PreparedActionError("prepared_action_invalid")
        self._validate_units(units)
        normalized, payload = _canonical_bytes(
            outbound, byte_cap=SCOPE_BYTE_CAPS[scope]
        )
        outbound_digest = hashlib.sha256(payload).hexdigest()
        manifest_digest = _manifest_digest(
            scope=scope,
            binding=binding,
            task=task,
            task_models=selected_task_models,
            models=selected_models,
            executor_id=executor_id,
            executor_version=executor_version,
            estimated_calls=estimated_calls,
            max_calls=max_calls,
            max_tokens=max_tokens,
            outbound_digest=outbound_digest,
            units=units,
        )
        now = self._now()
        action = PreparedOutbound(
            action_id=secrets.token_urlsafe(32),
            session_digest=hashlib.sha256(session_id.encode("utf-8")).hexdigest(),
            scope=scope,
            provider_id=binding.provider_id,
            runtime_revision=binding.selection_revision,
            credential_generation=binding.credential_generation,
            task=task,
            task_models=selected_task_models,
            models=selected_models,
            executor_id=executor_id,
            executor_version=executor_version,
            estimated_calls=estimated_calls,
            max_calls=max_calls,
            max_tokens=max_tokens,
            outbound=_freeze(normalized),
            outbound_digest=outbound_digest,
            manifest_digest=manifest_digest,
            units=units,
            byte_count=len(payload),
            issued_at=now,
            expires_at=now + PREPARED_ACTION_TTL_SECONDS,
        )
        with self._lock:
            self._cleanup_locked(now)
            if len(self._actions) >= MAX_PREPARED_ACTIONS:
                raise PreparedActionError("prepared_action_store_full")
            session_count = sum(
                item.session_digest == action.session_digest
                for item in self._actions.values()
            )
            total_bytes = sum(item.byte_count for item in self._actions.values())
            if (
                session_count >= MAX_ACTIONS_PER_SESSION
                or total_bytes + action.byte_count > MAX_TOTAL_PREPARED_BYTES
            ):
                raise PreparedActionError("prepared_action_store_full")
            self._actions[action.action_id] = action
        return self._public_summary(action)

    def prepare_capability_test(
        self,
        *,
        session_id: str,
        provider_id: str,
        expected_revision: int,
    ) -> dict[str, object]:
        binding = self._binding()
        if (
            binding.provider_id != provider_id
            or binding.selection_revision != expected_revision
        ):
            raise PreparedActionError("prepared_action_stale")
        models = tuple(sorted(set(binding.task_models.values())))
        return self.prepare(
            session_id=session_id,
            scope="capability_test",
            task="capability_test",
            outbound={
                "kind": "capability_test",
                "provider_id": binding.provider_id,
                "runtime_revision": binding.selection_revision,
                "models": list(models),
                "maximum_model_calls": 2 * len(models),
            },
            models=models,
            executor_id="provider_capability_verifier",
            executor_version="v1",
            estimated_calls=2 * len(models),
            max_calls=2 * len(models),
            max_tokens=32 * 2 * len(models),
            expected_provider_id=provider_id,
            expected_revision=expected_revision,
        )

    def binding_for_consent(
        self, *, action_id: str, session_id: str
    ) -> PreparedConsentBinding:
        with self._lock:
            action = self._resolve_locked(action_id, session_id)
            self._validate_current_locked(action)
            return self._consent_binding(action, session_id)

    def issue_consent(self, *, action_id: str, session_id: str) -> dict[str, object]:
        with self._lock:
            action = self._resolve_locked(action_id, session_id)
            self._validate_current_locked(action)
            return self._consents.issue(
                binding=self._consent_binding(action, session_id)
            )

    def consume(
        self, *, action_id: str, consent_nonce: str, session_id: str
    ) -> PreparedOutbound:
        with self._lock:
            action = self._resolve_locked(action_id, session_id)
            self._validate_current_locked(action)
            self._consents.consume(
                nonce=consent_nonce,
                binding=self._consent_binding(action, session_id),
            )
            self._actions.pop(action_id, None)
            self._consumed[action_id] = action.expires_at
            return action

    def _resolve_locked(self, action_id: str, session_id: str) -> PreparedOutbound:
        now = self._now()
        if action_id in self._consumed:
            raise PreparedActionError("prepared_action_consumed")
        action = self._actions.get(action_id)
        if action is None or _SESSION_RE.fullmatch(session_id) is None:
            self._cleanup_locked(now)
            raise PreparedActionError("prepared_action_invalid")
        session_digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        if action.session_digest != session_digest:
            raise PreparedActionError("prepared_action_invalid")
        if now >= action.expires_at or now < action.issued_at:
            self._actions.pop(action_id, None)
            self._cleanup_locked(now)
            raise PreparedActionError("prepared_action_expired")
        self._cleanup_locked(now, preserve_action_id=action_id)
        return action

    def _validate_current_locked(self, action: PreparedOutbound) -> None:
        binding = self._binding()
        if (
            binding.provider_id != action.provider_id
            or binding.selection_revision != action.runtime_revision
            or binding.credential_generation != action.credential_generation
            or binding.activation not in {"legacy_compatible", "connection_verified"}
            and action.scope != "capability_test"
            or self._task_models(
                binding,
                action.scope,
                action.task,
                tuple(task for task, _model in action.task_models),
                action.models,
            )
            != action.task_models
        ):
            raise PreparedActionError("prepared_action_stale")
        self._validate_units(action.units)

    def _validate_units(self, units: tuple[ContentUnit, ...]) -> None:
        if units and self._snapshots is None:
            raise PreparedActionError("prepared_action_state_unavailable")
        for unit in units:
            try:
                current = self._snapshots.fingerprint_for(
                    kind=unit.kind,
                    stable_source_identity=unit.stable_source_identity,
                )
            except Exception as exc:
                raise PreparedActionError("prepared_action_state_unavailable") from exc
            if current != unit.snapshot_fingerprint:
                raise PreparedActionError("prepared_action_stale")

    @staticmethod
    def _task_models(
        binding: RuntimeActionBinding,
        scope: str,
        task: str,
        allowed_tasks: Sequence[str] | None,
        models: Sequence[str] | None,
    ) -> tuple[tuple[str, str], ...]:
        if scope == "capability_test":
            if task != "capability_test":
                raise PreparedActionError("prepared_action_invalid")
            expected = tuple(sorted(set(binding.task_models.values())))
            if models is not None and tuple(models) != expected:
                raise PreparedActionError("prepared_action_stale")
            expected_mapping = tuple(
                (f"capability_test:{index}", model)
                for index, model in enumerate(expected)
            )
            expected_keys = tuple(item for item, _model in expected_mapping)
            if allowed_tasks is not None and tuple(allowed_tasks) != expected_keys:
                raise PreparedActionError("prepared_action_invalid")
            return expected_mapping
        if task not in TASK_IDS:
            raise PreparedActionError("prepared_action_invalid")
        tasks = (task,) if allowed_tasks is None else tuple(allowed_tasks)
        if (
            not tasks
            or task not in tasks
            or len(set(tasks)) != len(tasks)
            or any(item not in TASK_IDS for item in tasks)
        ):
            raise PreparedActionError("prepared_action_invalid")
        expected_mapping = tuple(
            sorted((item, binding.task_models[item]) for item in tasks)
        )
        expected_models = tuple(sorted(set(model for _item, model in expected_mapping)))
        if models is not None and tuple(models) != expected_models:
            raise PreparedActionError("prepared_action_stale")
        return expected_mapping

    def _binding(self) -> RuntimeActionBinding:
        try:
            binding = self._runtime.action_binding()
        except Exception as exc:
            raise PreparedActionError("prepared_action_state_unavailable") from exc
        if not isinstance(binding, RuntimeActionBinding):
            raise PreparedActionError("prepared_action_state_unavailable")
        return binding

    def _now(self) -> int:
        try:
            value = self._clock.now()
        except Exception as exc:
            raise PreparedActionError("prepared_action_state_unavailable") from exc
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PreparedActionError("prepared_action_state_unavailable")
        return value

    @staticmethod
    def _consent_binding(
        action: PreparedOutbound, session_id: str
    ) -> PreparedConsentBinding:
        return PreparedConsentBinding(
            action.action_id,
            session_id,
            action.provider_id,
            action.runtime_revision,
            action.credential_generation,
            action.scope,
            DISCLOSURE_VERSIONS[action.scope],
            action.manifest_digest,
            action.expires_at,
        )

    @staticmethod
    def _public_summary(action: PreparedOutbound) -> dict[str, object]:
        counts: dict[str, int] = {}
        for unit in action.units:
            counts[unit.kind] = counts.get(unit.kind, 0) + 1
        return {
            "schema_version": PREPARED_ACTION_SCHEMA_VERSION,
            "action_id": action.action_id,
            "scope": action.scope,
            "provider_id": action.provider_id,
            "display": "AI 能力测试" if action.scope == "capability_test" else "AI 请求",
            "task": action.task,
            "allowed_tasks": (
                ["capability_test"]
                if action.scope == "capability_test"
                else [task for task, _model in action.task_models]
            ),
            "model": action.models[0] if len(action.models) == 1 else list(action.models),
            "unit_counts": counts,
            "byte_count": action.byte_count,
            "outbound_unit_count": len(action.units),
            "estimated_calls": action.estimated_calls,
            "maximum_calls": action.max_calls,
            "maximum_tokens": action.max_tokens,
            "disclosure_version": DISCLOSURE_VERSIONS[action.scope],
            "expires_at": action.expires_at,
        }

    def _cleanup_locked(self, now: int, preserve_action_id: str | None = None) -> None:
        for action_id, action in tuple(self._actions.items()):
            if action_id != preserve_action_id and now >= action.expires_at:
                self._actions.pop(action_id, None)
        for action_id, expires_at in tuple(self._consumed.items()):
            if now >= expires_at:
                self._consumed.pop(action_id, None)


__all__ = [
    "ContentSnapshotAuthority",
    "ContentUnit",
    "PreparedActionClock",
    "PreparedActionError",
    "PreparedActionService",
    "PreparedOutbound",
    "RuntimeActionAuthority",
    "SystemPreparedActionClock",
    "SCOPE_BYTE_CAPS",
]
