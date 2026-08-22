from __future__ import annotations

import ipaddress
import re
import socket
import threading
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit

from .provider_registry import (
    CAPABILITY_AGENT,
    CAPABILITY_STRUCTURED_JSON,
    CAPABILITY_TOOL_CALLING,
    OPENAI_CHAT_COMPLETIONS_PROTOCOL,
    ProviderCapabilities,
    RUNTIME_ACTIVATION_CONNECTION_REQUIRED,
    TASK_IDS,
    TrustedProviderProfile,
    clear_custom_provider_profile,
    install_custom_provider_profile,
)


CUSTOM_PROVIDER_ID = "custom"
CUSTOM_PROVIDER_SCHEMA_VERSION = "custom-ai-provider-v1"
_DISPLAY_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,80}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_STORED_KEYS = frozenset(
    {"schema_version", "revision", "display_name", "chat_endpoint", "task_models", "resolved_ips"}
)
_MUTATION_LOCK = threading.RLock()


class CustomProviderError(RuntimeError):
    _MESSAGES = {
        "custom_provider_invalid": ("自定义 AI 提供商配置无效。", False),
        "custom_provider_conflict": ("自定义 AI 提供商配置已更新，请重新加载。", True),
        "custom_provider_unavailable": ("自定义 AI 提供商配置暂时不可用。", True),
        "custom_provider_endpoint_unsafe": ("自定义 AI 提供商地址未通过安全检查。", False),
    }

    def __init__(self, code: str) -> None:
        if code not in self._MESSAGES:
            raise ValueError("unsupported custom provider error")
        message, retryable = self._MESSAGES[code]
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": "custom-ai-provider-error-v1",
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.retryable,
        }


class AtomicCustomProviderStore(Protocol):
    def read(self) -> Mapping[str, Any] | None: ...
    def revision(self) -> int: ...
    def compare_and_swap(self, *, expected_revision: int, value: Mapping[str, Any] | None) -> bool: ...


class HostResolver(Protocol):
    def resolve(self, hostname: str) -> tuple[str, ...]: ...


class SystemHostResolver:
    def resolve(self, hostname: str) -> tuple[str, ...]:
        values = {
            str(row[4][0])
            for row in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        }
        return tuple(sorted(values))


def _safe_ips(values: object) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)) or not values or len(values) > 16:
        raise CustomProviderError("custom_provider_endpoint_unsafe")
    result: list[str] = []
    for value in values:
        try:
            address = ipaddress.ip_address(str(value))
        except ValueError as exc:
            raise CustomProviderError("custom_provider_endpoint_unsafe") from exc
        if not address.is_global or any(
            (
                address.is_private,
                address.is_loopback,
                address.is_link_local,
                address.is_multicast,
                address.is_reserved,
                address.is_unspecified,
            )
        ):
            raise CustomProviderError("custom_provider_endpoint_unsafe")
        result.append(address.compressed)
    return tuple(sorted(set(result)))


def _endpoint(value: object) -> tuple[str, str]:
    if not isinstance(value, str) or len(value) > 2048 or value != value.strip():
        raise CustomProviderError("custom_provider_endpoint_unsafe")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise CustomProviderError("custom_provider_endpoint_unsafe") from exc
    try:
        ipaddress.ip_address(parsed.hostname or "")
    except ValueError:
        pass
    else:
        raise CustomProviderError("custom_provider_endpoint_unsafe")
    hostname = (parsed.hostname or "").casefold()
    if (
        parsed.scheme != "https"
        or not hostname
        or hostname == "localhost"
        or hostname.endswith(".local")
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or not parsed.path.endswith("/chat/completions")
        or "//" in parsed.path
    ):
        raise CustomProviderError("custom_provider_endpoint_unsafe")
    return value, hostname


@dataclass(frozen=True)
class CustomProviderRecord:
    revision: int
    display_name: str
    chat_endpoint: str
    task_models: Mapping[str, str]
    resolved_ips: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise CustomProviderError("custom_provider_invalid")
        if (
            not isinstance(self.display_name, str)
            or _DISPLAY_RE.fullmatch(self.display_name) is None
            or "://" in self.display_name
            or "/" in self.display_name
            or "\\" in self.display_name
        ):
            raise CustomProviderError("custom_provider_invalid")
        endpoint, _ = _endpoint(self.chat_endpoint)
        if not isinstance(self.task_models, Mapping) or set(self.task_models) != set(TASK_IDS):
            raise CustomProviderError("custom_provider_invalid")
        models = {task: str(self.task_models[task]) for task in TASK_IDS}
        if any(_MODEL_RE.fullmatch(model) is None for model in models.values()):
            raise CustomProviderError("custom_provider_invalid")
        object.__setattr__(self, "chat_endpoint", endpoint)
        object.__setattr__(self, "task_models", MappingProxyType(models))
        object.__setattr__(self, "resolved_ips", _safe_ips(self.resolved_ips))

    def stored_dict(self) -> dict[str, object]:
        return {
            "schema_version": CUSTOM_PROVIDER_SCHEMA_VERSION,
            "revision": self.revision,
            "display_name": self.display_name,
            "chat_endpoint": self.chat_endpoint,
            "task_models": dict(self.task_models),
            "resolved_ips": list(self.resolved_ips),
        }

    def profile(self) -> TrustedProviderProfile:
        options = {task: (model,) for task, model in self.task_models.items()}
        return TrustedProviderProfile(
            provider_id=CUSTOM_PROVIDER_ID,
            display_name=self.display_name,
            protocol=OPENAI_CHAT_COMPLETIONS_PROTOCOL,
            chat_endpoint=self.chat_endpoint,
            capabilities=ProviderCapabilities(True, True, True, False),
            default_task_models=self.task_models,
            allowed_task_models=options,
            model_validation="connection-test-required",
            runtime_activation=RUNTIME_ACTIVATION_CONNECTION_REQUIRED,
            provider_kind="custom",
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": CUSTOM_PROVIDER_SCHEMA_VERSION,
            "provider_id": CUSTOM_PROVIDER_ID,
            "revision": self.revision,
            "display_name": self.display_name,
            "task_models": dict(self.task_models),
            "configured": True,
        }


class CustomProviderService:
    def __init__(self, store: AtomicCustomProviderStore, *, resolver: HostResolver | None = None) -> None:
        self._store = store
        self._resolver = resolver or SystemHostResolver()
        record = self._read()
        if record is not None:
            self._activate(record)
        else:
            # The registry is process-global because every runtime client must
            # resolve the same reviewed recipient.  A newly composed service
            # with no configured record must not inherit a stale profile from
            # an earlier test or replaced application composition.
            clear_custom_provider_profile()

    def _read(self) -> CustomProviderRecord | None:
        try:
            value = self._store.read()
        except Exception as exc:
            raise CustomProviderError("custom_provider_unavailable") from exc
        if value is None:
            return None
        if not isinstance(value, Mapping) or set(value) != _STORED_KEYS or value.get("schema_version") != CUSTOM_PROVIDER_SCHEMA_VERSION:
            raise CustomProviderError("custom_provider_unavailable")
        return CustomProviderRecord(
            value.get("revision"), value.get("display_name"), value.get("chat_endpoint"),
            value.get("task_models"), tuple(value.get("resolved_ips") or ()),
        )

    def _activate(self, record: CustomProviderRecord) -> None:
        expected = record.resolved_ips
        endpoint = record.chat_endpoint
        _, hostname = _endpoint(endpoint)

        def guard(candidate: str) -> bool:
            if candidate != endpoint:
                return False
            try:
                return _safe_ips(self._resolver.resolve(hostname)) == expected
            except Exception:
                return False

        install_custom_provider_profile(record.profile(), endpoint_guard=guard)

    def get(self) -> dict[str, object]:
        record = self._read()
        return record.public_dict() if record else {
            "schema_version": CUSTOM_PROVIDER_SCHEMA_VERSION,
            "provider_id": CUSTOM_PROVIDER_ID,
            "revision": self._revision(),
            "configured": False,
        }

    def _revision(self) -> int:
        try:
            value = self._store.revision()
        except Exception as exc:
            raise CustomProviderError("custom_provider_unavailable") from exc
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CustomProviderError("custom_provider_unavailable")
        return value

    def save(self, payload: Mapping[str, Any]) -> dict[str, object]:
        if not isinstance(payload, Mapping) or set(payload) != {"display_name", "chat_endpoint", "task_models", "expected_revision"}:
            raise CustomProviderError("custom_provider_invalid")
        expected = payload.get("expected_revision")
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
            raise CustomProviderError("custom_provider_invalid")
        endpoint, hostname = _endpoint(payload.get("chat_endpoint"))
        try:
            ips = _safe_ips(self._resolver.resolve(hostname))
        except CustomProviderError:
            raise
        except Exception as exc:
            raise CustomProviderError("custom_provider_endpoint_unsafe") from exc
        record = CustomProviderRecord(
            expected + 1, payload.get("display_name"), endpoint, payload.get("task_models"), ips
        )
        with _MUTATION_LOCK:
            try:
                swapped = self._store.compare_and_swap(expected_revision=expected, value=record.stored_dict())
            except Exception as exc:
                raise CustomProviderError("custom_provider_unavailable") from exc
            if swapped is not True:
                raise CustomProviderError("custom_provider_conflict")
            self._activate(record)
        return record.public_dict()

    def delete(self, *, expected_revision: object) -> dict[str, object]:
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise CustomProviderError("custom_provider_invalid")
        with _MUTATION_LOCK:
            try:
                swapped = self._store.compare_and_swap(expected_revision=expected_revision, value=None)
            except Exception as exc:
                raise CustomProviderError("custom_provider_unavailable") from exc
            if swapped is not True:
                raise CustomProviderError("custom_provider_conflict")
            clear_custom_provider_profile()
        return {
            "schema_version": CUSTOM_PROVIDER_SCHEMA_VERSION,
            "provider_id": CUSTOM_PROVIDER_ID,
            "revision": expected_revision + 1,
            "configured": False,
        }


__all__ = [
    "AtomicCustomProviderStore", "CUSTOM_PROVIDER_ID", "CustomProviderError",
    "CustomProviderRecord", "CustomProviderService", "HostResolver", "SystemHostResolver",
]
