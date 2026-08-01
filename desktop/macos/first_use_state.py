from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol

from secure_credentials import CredentialStatus


MAX_ACTIVE_PACKAGE_STATUS_BYTES = 16_384
PACKAGE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
PACKAGE_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")


class FirstUseState(str, Enum):
    NEEDS_EVIDENCE_PACKAGE = "needs_evidence_package"
    OFFLINE_READY = "offline_ready"
    NEEDS_AI_KEY_FOR_AI_ACTION = "needs_ai_key_for_ai_action"
    READY_FOR_AI = "ready_for_ai"


class FirstUseStateError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ActivePackageStatus:
    active: bool
    package_id: str = ""
    package_version: str = ""

    @classmethod
    def inactive(cls) -> "ActivePackageStatus":
        return cls(active=False)


@dataclass(frozen=True)
class FirstUseReadiness:
    state: FirstUseState
    can_search_offline: bool
    can_use_ai: bool
    ai_key_configured: bool
    active_package: ActivePackageStatus
    credential_storage: str

    def public_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "state": self.state.value,
            "can_search_offline": self.can_search_offline,
            "can_use_ai": self.can_use_ai,
            "active_package": self.active_package.active,
            "ai_key_configured": self.ai_key_configured,
            "credential_storage": self.credential_storage,
        }
        if self.active_package.active:
            value["package_id"] = self.active_package.package_id
            value["package_version"] = self.active_package.package_version
        return value


class CredentialStatusProvider(Protocol):
    def status(self) -> CredentialStatus: ...


def parse_active_package_status(value: Mapping[str, Any] | None) -> ActivePackageStatus:
    if value is None:
        return ActivePackageStatus.inactive()
    if not isinstance(value, Mapping):
        raise FirstUseStateError("active_package_status_invalid", "资料包启用状态格式无效")

    package_id = str(value.get("package_id") or "").strip()
    package_version = str(
        value.get("active_version") or value.get("package_version") or ""
    ).strip()
    explicit_active = value.get("active")
    if explicit_active is False and not package_id and not package_version:
        return ActivePackageStatus.inactive()
    if explicit_active not in (None, True, False):
        raise FirstUseStateError("active_package_status_invalid", "资料包启用标记无效")
    if not package_id and not package_version:
        return ActivePackageStatus.inactive()
    if not PACKAGE_ID_RE.fullmatch(package_id) or not PACKAGE_VERSION_RE.fullmatch(package_version):
        raise FirstUseStateError("active_package_status_invalid", "资料包启用身份无效")
    if explicit_active is False:
        raise FirstUseStateError("active_package_status_invalid", "资料包启用状态相互矛盾")
    return ActivePackageStatus(
        active=True,
        package_id=package_id,
        package_version=package_version,
    )


def read_active_package_status(path: Path | str) -> ActivePackageStatus:
    status_path = Path(path).expanduser()
    if status_path.is_symlink():
        raise FirstUseStateError("active_package_status_unsafe", "资料包启用状态文件不安全")
    if not status_path.exists():
        return ActivePackageStatus.inactive()
    if not status_path.is_file():
        raise FirstUseStateError("active_package_status_unsafe", "资料包启用状态文件不安全")
    try:
        if status_path.stat().st_size > MAX_ACTIVE_PACKAGE_STATUS_BYTES:
            raise FirstUseStateError("active_package_status_invalid", "资料包启用状态文件过大")
        value = json.loads(status_path.read_text(encoding="utf-8"))
    except FirstUseStateError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FirstUseStateError("active_package_status_invalid", "无法读取资料包启用状态") from exc
    if not isinstance(value, dict):
        raise FirstUseStateError("active_package_status_invalid", "资料包启用状态必须是对象")
    return parse_active_package_status(value)


def resolve_first_use_state(
    active_package: ActivePackageStatus,
    credential_status: CredentialStatus,
    *,
    ai_action_requested: bool = False,
) -> FirstUseReadiness:
    if not isinstance(ai_action_requested, bool):
        raise FirstUseStateError("first_use_intent_invalid", "首次使用操作意图无效")
    if not active_package.active:
        state = FirstUseState.NEEDS_EVIDENCE_PACKAGE
    elif credential_status.configured:
        state = FirstUseState.READY_FOR_AI
    elif ai_action_requested:
        state = FirstUseState.NEEDS_AI_KEY_FOR_AI_ACTION
    else:
        state = FirstUseState.OFFLINE_READY
    return FirstUseReadiness(
        state=state,
        can_search_offline=active_package.active,
        can_use_ai=active_package.active and credential_status.configured,
        ai_key_configured=credential_status.configured,
        active_package=active_package,
        credential_storage=credential_status.storage,
    )


def evaluate_first_use_state(
    active_package_status_path: Path | str,
    credential_store: CredentialStatusProvider,
    *,
    ai_action_requested: bool = False,
) -> FirstUseReadiness:
    active_package = read_active_package_status(active_package_status_path)
    credential_status = credential_store.status()
    return resolve_first_use_state(
        active_package,
        credential_status,
        ai_action_requested=ai_action_requested,
    )
