from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .prepared_actions import PreparedOutbound
from .provider_registry import CAPABILITY_AGENT, trusted_provider_profile


HARNESS_SESSION_SCHEMA_VERSION = "harness-session-v1"
HARNESS_JOB_SCHEMA_VERSION = "harness-job-v1"
HARNESS_ERROR_SCHEMA_VERSION = "harness-error-v1"
HARNESS_SDK_PROTOCOL_PIN = (
    "deepseek-harness-sdk",
    "0.1.1rc1",
    "2113aec229039da435bc44b275b487216d2b1c308d850521b88cea6ce3c1b762",
    "mac-independent",
    0,
)
CORDIS_RUNTIME_PROTOCOL_PIN = (
    "deepseek-harness-runtime-bin",
    "0.1.1rc1",
    "2707cd666ba49ee0963228873abf7850ca7ec5e782cca61e3603793bace0d1cf",
    "macos-14-arm64",
    55_190_958,
)
PYDANTIC_MINIMUM = (2, 12, 0)
PYDANTIC_MAXIMUM_MAJOR = 3
CORDIS_COMPOSITION_SCHEMA_VERSION = "auto-research-cordis-composition-v1"
CORDIS_PLUGIN_ALLOWLIST = frozenset(
    {"auto-research-domain-tools", "auto-research-model-port"}
)
CORDIS_FORBIDDEN_CAPABILITIES = frozenset(
    {
        "bash",
        "editor",
        "filesystem",
        "pty",
        "subagent",
        "arbitrary_network",
        "credential_access",
        "local_path_access",
    }
)
HARNESS_SCOPES = frozenset({"librarian", "selected_evidence_chat"})
MAX_HARNESS_PUBLIC_BYTES = 1024 * 1024
MAX_HARNESS_VALUE_DEPTH = 12
MAX_HARNESS_VALUE_NODES = 4_000

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,255}$")
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z][A-Za-z0-9.-]*)?(?:\+[A-Za-z0-9.-]+)?$")
_SHA_RE = re.compile(r"^[a-f0-9]{64}$")
_SENSITIVE_KEY_RE = re.compile(
    r"(?:^|_)(?:api_?key|secret|password|credential|nonce|endpoint|base_?url|path)(?:$|_)",
    re.IGNORECASE,
)
_LOCAL_VALUE_RE = re.compile(
    r"(?:^|[\s='\"])(?:~[/\\]|/(?:Users|home|private|tmp|var|etc|usr|root|srv|mnt|media|opt|Applications|Library|System)(?:[/\\]|$)|/[^/\s]+[/\\][^\s]*|[A-Za-z]:[\\/]|\\\\|file:|sqlite:)",
    re.IGNORECASE,
)

_ERRORS = {
    "harness_invalid": ("Harness 请求无效。", False),
    "harness_dependency_mismatch": ("Harness 运行依赖未通过版本核验。", False),
    "harness_scope_unsupported": ("该科研场景不允许使用 Harness。", False),
    "harness_provider_untrusted": ("当前 AI 提供商未通过 Harness 审核。", False),
    "harness_provider_unavailable": ("AI 提供商当前无法完成 Harness 请求。", True),
    "harness_provider_response_invalid": ("AI 提供商返回了 Harness 无法安全使用的结果。", True),
    "harness_private_forbidden": ("私人实验数据不允许进入该 Harness 场景。", False),
    "harness_tool_forbidden": ("Harness 请求了未授权工具。", False),
    "harness_tool_invalid": ("Harness 工具参数或结果无效。", False),
    "harness_output_invalid": ("Harness 返回结果未通过科研完整性检查。", False),
    "harness_budget_exhausted": ("Harness 已用完本次授权预算，未自动继续收费。", False),
    "harness_runtime_unavailable": ("Harness 运行时暂时不可用。", True),
    "harness_runtime_failed": ("Harness 任务未能完成。", True),
    "harness_job_expired": ("Harness 任务已过期。", False),
    "harness_job_replayed": ("Harness 任务已执行，请重新准备。", False),
    "harness_job_store_full": ("Harness 任务过多，请稍后再试。", True),
}


class HarnessError(RuntimeError):
    def __init__(self, code: str) -> None:
        if code not in _ERRORS:
            raise ValueError("unsupported harness error code")
        message, retryable = _ERRORS[code]
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": HARNESS_ERROR_SCHEMA_VERSION,
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.retryable,
        }


def _canonical(value: Any, *, depth: int = 0, nodes: list[int] | None = None) -> Any:
    count = nodes if nodes is not None else [0]
    count[0] += 1
    if depth > MAX_HARNESS_VALUE_DEPTH or count[0] > MAX_HARNESS_VALUE_NODES:
        raise HarnessError("harness_invalid")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise HarnessError("harness_invalid")
        return value
    if isinstance(value, str):
        if len(value) > 100_000 or _LOCAL_VALUE_RE.search(value):
            raise HarnessError("harness_invalid")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or _SENSITIVE_KEY_RE.search(key):
                raise HarnessError("harness_invalid")
            result[key] = _canonical(item, depth=depth + 1, nodes=count)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical(item, depth=depth + 1, nodes=count) for item in value]
    raise HarnessError("harness_invalid")


def canonical_public(value: Any, *, byte_cap: int = MAX_HARNESS_PUBLIC_BYTES) -> Any:
    normalized = _canonical(value)
    try:
        payload = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise HarnessError("harness_invalid") from exc
    if len(payload) > byte_cap:
        raise HarnessError("harness_invalid")
    return normalized


def canonical_digest(value: Any) -> str:
    normalized = canonical_public(value)
    return hashlib.sha256(
        json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class HarnessDependencyMetadata:
    distribution: str
    version: str
    wheel_sha256: str
    platform_tag: str
    size_bytes: int = 0

    def __post_init__(self) -> None:
        if (
            _ID_RE.fullmatch(self.distribution) is None
            or _VERSION_RE.fullmatch(self.version) is None
            or _SHA_RE.fullmatch(self.wheel_sha256) is None
            or _ID_RE.fullmatch(self.platform_tag) is None
            or isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise HarnessError("harness_dependency_mismatch")

    @classmethod
    def from_pin(
        cls, pin: tuple[str, str, str, str, int]
    ) -> "HarnessDependencyMetadata":
        return cls(*pin)


@dataclass(frozen=True)
class HarnessDependencySet:
    sdk: HarnessDependencyMetadata
    runtime: HarnessDependencyMetadata
    pydantic_version: str

    def verify_production_protocols(self) -> None:
        if (
            self.sdk != HarnessDependencyMetadata.from_pin(HARNESS_SDK_PROTOCOL_PIN)
            or self.runtime
            != HarnessDependencyMetadata.from_pin(CORDIS_RUNTIME_PROTOCOL_PIN)
            or self.sdk.version != self.runtime.version
            or not _pydantic_version_allowed(self.pydantic_version)
        ):
            raise HarnessError("harness_dependency_mismatch")


def _pydantic_version_allowed(value: str) -> bool:
    if not isinstance(value, str) or _VERSION_RE.fullmatch(value) is None:
        return False
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", value)
    if match is None:
        return False
    if match.end() != len(value):
        return False
    version = tuple(int(part) for part in match.groups())
    return PYDANTIC_MINIMUM <= version and version[0] < PYDANTIC_MAXIMUM_MAJOR


def verify_cordis_composition(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version", "plugins", "capabilities"
    }:
        raise HarnessError("harness_dependency_mismatch")
    plugins = value.get("plugins")
    capabilities = value.get("capabilities")
    if (
        value.get("schema_version") != CORDIS_COMPOSITION_SCHEMA_VERSION
        or not isinstance(plugins, Sequence)
        or isinstance(plugins, (str, bytes, bytearray))
        or any(not isinstance(plugin, str) or _ID_RE.fullmatch(plugin) is None for plugin in plugins)
        or set(plugins) != CORDIS_PLUGIN_ALLOWLIST
        or len(plugins) != len(CORDIS_PLUGIN_ALLOWLIST)
        or not isinstance(capabilities, Mapping)
        or set(capabilities) != CORDIS_FORBIDDEN_CAPABILITIES
        or any(value is not False for value in capabilities.values())
    ):
        raise HarnessError("harness_dependency_mismatch")


@dataclass(frozen=True)
class HarnessEvidenceIdentity:
    source_scope: str
    source_id: str
    entity_type: str
    entity_uid: str
    bundle_uid: str = ""

    def __post_init__(self) -> None:
        if (
            self.source_scope not in {"workspace", "official", "private"}
            or self.entity_type not in {"item", "finding", "table", "figure"}
            or any(
                _ID_RE.fullmatch(value) is None
                for value in (self.source_id, self.entity_uid)
            )
            or (self.bundle_uid and _ID_RE.fullmatch(self.bundle_uid) is None)
        ):
            raise HarnessError("harness_invalid")

    def public_dict(self) -> dict[str, str]:
        value = {
            "source_scope": self.source_scope,
            "source_id": self.source_id,
            "entity_type": self.entity_type,
            "entity_uid": self.entity_uid,
        }
        if self.bundle_uid:
            value["bundle_uid"] = self.bundle_uid
        return value


@dataclass(frozen=True)
class HarnessSessionV1:
    session_id: str
    provider_id: str
    runtime_revision: int
    credential_generation: int
    scope: str
    action_id: str
    issued_at: int
    expires_at: int

    def __post_init__(self) -> None:
        try:
            profile = trusted_provider_profile(self.provider_id)
        except Exception as exc:
            raise HarnessError("harness_provider_untrusted") from exc
        if (
            _ID_RE.fullmatch(self.session_id) is None
            or _ID_RE.fullmatch(self.action_id) is None
            or self.scope not in HARNESS_SCOPES
            or not profile.capabilities.supports(CAPABILITY_AGENT)
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in (self.runtime_revision, self.credential_generation, self.issued_at)
            )
            or not isinstance(self.expires_at, int)
            or self.expires_at <= self.issued_at
        ):
            raise HarnessError("harness_invalid")

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": HARNESS_SESSION_SCHEMA_VERSION,
            "provider_id": self.provider_id,
            "scope": self.scope,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class HarnessJobV1:
    job_id: str
    session: HarnessSessionV1
    task: str
    model: str
    max_calls: int
    max_tokens: int
    evidence: tuple[HarnessEvidenceIdentity, ...]
    current_entity: HarnessEvidenceIdentity | None
    allowed_neighbors: tuple[HarnessEvidenceIdentity, ...]
    outbound_digest: str
    state: str = "prepared"

    def __post_init__(self) -> None:
        action_models = trusted_provider_profile(self.session.provider_id).allowed_task_models
        if (
            _ID_RE.fullmatch(self.job_id) is None
            or self.task not in action_models
            or self.model not in action_models[self.task]
            or isinstance(self.max_calls, bool)
            or not isinstance(self.max_calls, int)
            or not 1 <= self.max_calls <= 8
            or isinstance(self.max_tokens, bool)
            or not isinstance(self.max_tokens, int)
            or not 1 <= self.max_tokens <= 128_000
            or len(self.evidence) > 256
            or len(self.allowed_neighbors) > 32
            or _SHA_RE.fullmatch(self.outbound_digest) is None
            or self.state not in {"prepared", "running", "completed", "failed"}
        ):
            raise HarnessError("harness_invalid")
        if self.session.scope == "librarian" and any(
            item.source_scope == "private" for item in self.evidence
        ):
            raise HarnessError("harness_private_forbidden")
        allowed_tasks = {
            # Search V2 may prepare either a fast, locally seeded planning turn
            # or the historical two-stage synthesis turn.  Both remain inside
            # the pinned Librarian scope and provider registry.
            "librarian": frozenset({"librarian_planning", "librarian_synthesis"}),
            "selected_evidence_chat": frozenset({"extraction"}),
        }[self.session.scope]
        if self.task not in allowed_tasks:
            raise HarnessError("harness_scope_unsupported")
        if self.session.scope == "selected_evidence_chat":
            if self.current_entity is None or self.current_entity.source_scope == "private":
                raise HarnessError("harness_private_forbidden")
            allowed = {self.current_entity, *self.allowed_neighbors}
            if any(item not in allowed for item in self.evidence):
                raise HarnessError("harness_invalid")

    def with_state(self, state: str) -> "HarnessJobV1":
        return HarnessJobV1(
            self.job_id,
            self.session,
            self.task,
            self.model,
            self.max_calls,
            self.max_tokens,
            self.evidence,
            self.current_entity,
            self.allowed_neighbors,
            self.outbound_digest,
            state,
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": HARNESS_JOB_SCHEMA_VERSION,
            "scope": self.session.scope,
            "provider_id": self.session.provider_id,
            "task": self.task,
            "model": self.model,
            "state": self.state,
            "evidence_count": len(self.evidence),
            "max_calls": self.max_calls,
            "max_tokens": self.max_tokens,
            "expires_at": self.session.expires_at,
        }


def job_from_prepared_action(
    action: PreparedOutbound,
    *,
    session_id: str,
    evidence: Sequence[HarnessEvidenceIdentity],
    current_entity: HarnessEvidenceIdentity | None = None,
    allowed_neighbors: Sequence[HarnessEvidenceIdentity] = (),
) -> HarnessJobV1:
    if not isinstance(action, PreparedOutbound) or action.scope not in HARNESS_SCOPES:
        raise HarnessError("harness_scope_unsupported")
    models = dict(action.task_models)
    model = models.get(action.task)
    if not model:
        raise HarnessError("harness_invalid")
    session = HarnessSessionV1(
        session_id=session_id,
        provider_id=action.provider_id,
        runtime_revision=action.runtime_revision,
        credential_generation=action.credential_generation,
        scope=action.scope,
        action_id=action.action_id,
        issued_at=action.issued_at,
        expires_at=action.expires_at,
    )
    return HarnessJobV1(
        job_id=f"harness-{secrets.token_urlsafe(24)}",
        session=session,
        task=action.task,
        model=model,
        max_calls=action.max_calls,
        max_tokens=action.max_tokens,
        evidence=tuple(evidence),
        current_entity=current_entity,
        allowed_neighbors=tuple(allowed_neighbors),
        outbound_digest=action.outbound_digest,
    )


def frozen_public_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    normalized = canonical_public(value)
    if not isinstance(normalized, dict):
        raise HarnessError("harness_invalid")
    return MappingProxyType(normalized)


__all__ = [
    "CORDIS_RUNTIME_PROTOCOL_PIN",
    "CORDIS_COMPOSITION_SCHEMA_VERSION",
    "CORDIS_FORBIDDEN_CAPABILITIES",
    "CORDIS_PLUGIN_ALLOWLIST",
    "HARNESS_ERROR_SCHEMA_VERSION",
    "HARNESS_JOB_SCHEMA_VERSION",
    "HARNESS_SCOPES",
    "HARNESS_SDK_PROTOCOL_PIN",
    "HARNESS_SESSION_SCHEMA_VERSION",
    "HarnessDependencyMetadata",
    "HarnessDependencySet",
    "HarnessError",
    "HarnessEvidenceIdentity",
    "HarnessJobV1",
    "HarnessSessionV1",
    "canonical_digest",
    "canonical_public",
    "frozen_public_mapping",
    "job_from_prepared_action",
    "verify_cordis_composition",
]
