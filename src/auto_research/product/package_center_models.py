"""Frozen DTOs and boundary validation for the shared package center."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Protocol, Sequence


OPAQUE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_SELECTION_ITEMS = 10_000
MAX_FILTER_BYTES = 64 * 1024
MAX_PUBLIC_DTO_BYTES = 2 * 1024 * 1024
MAX_TRANSFER_BYTES = 2 * 1024 * 1024 * 1024


class PackageKind(str, Enum):
    LITERATURE_COLLECTION = "literature_collection"
    PERSONAL_EXPERIMENTS = "personal_experiments"


class PackageScope(str, Enum):
    SELECTED = "selected"
    FILTERED = "filtered"
    ALL = "all"


class PackageCenterError(RuntimeError):
    """Stable, path-free error returned by the shared orchestration layer."""

    def __init__(
        self,
        code: str,
        safe_message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(safe_message)
        self.code = str(code)
        self.safe_message = str(safe_message)
        self.retryable = bool(retryable)

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema": "package-center-error-v1",
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class RightsRequirement:
    paper_uid: str
    title: str
    reason: str = "该 PDF 需要逐篇确认课题组内部分享权限"

    def public_dict(self) -> dict[str, str]:
        return {
            "paper_uid": self.paper_uid,
            "title": self.title,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RightsConfirmation:
    allowed: bool
    basis: str


@dataclass(frozen=True)
class PayloadPlanCandidate:
    """Internal plan whose opaque payload is never serialized."""

    package_id: str
    package_version: str
    content_fingerprint: str
    estimated_bytes: int
    item_count: int
    paper_count: int = 0
    missing_pdf_count: int = 0
    rights_requirements: tuple[RightsRequirement, ...] = ()
    exceeds_size_limit: bool = False
    payload: Any = None


@dataclass
class MaterializedPayload:
    """Private export value with an explicit, idempotent cleanup owner."""

    export_value: Any
    cleanup_callback: Callable[[], None]
    _closed: bool = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.cleanup_callback()


class SelectionResolver(Protocol):
    def resolve(self, selection_token: str) -> Any: ...


class DestinationResolver(Protocol):
    def resolve(self, destination_token: str) -> Any: ...


class PayloadPlanner(Protocol):
    def plan(
        self,
        *,
        kind: str,
        scope: str,
        selection: Any,
    ) -> PayloadPlanCandidate: ...

    def current_content_fingerprint(self, candidate: PayloadPlanCandidate) -> str: ...

    def materialize(
        self,
        candidate: PayloadPlanCandidate,
        *,
        rights_confirmations: Mapping[str, RightsConfirmation],
    ) -> MaterializedPayload: ...


class TransferInspector(Protocol):
    def __call__(self, source: Any) -> Any: ...


class TransferExporter(Protocol):
    def __call__(self, plan: Any, destination: Any, *, unencrypted_ack: bool) -> Any: ...


class TransferImporter(Protocol):
    def __call__(
        self,
        source: Any,
        *,
        expected_kind: str,
        expected_package_sha256: str,
        checksum_ack: bool,
        require_structured_payload: bool,
    ) -> Any: ...


class TransferActivator(Protocol):
    def activate(self, imported: Any, *, keep_conflicts: bool) -> Any: ...


def normalize_token(value: Any, *, label: str) -> str:
    token = str(value or "")
    if not OPAQUE_TOKEN_RE.fullmatch(token):
        raise PackageCenterError(
            f"{label}_invalid",
            "操作令牌无效或已经过期，请重新选择文件。",
        )
    return token


def normalize_kind(value: Any) -> PackageKind:
    try:
        return PackageKind(str(value))
    except ValueError as exc:
        raise PackageCenterError(
            "package_kind_invalid", "请选择论文集合包或私人实验包。"
        ) from exc


def normalize_scope(value: Any) -> PackageScope:
    try:
        return PackageScope(str(value))
    except ValueError as exc:
        raise PackageCenterError(
            "package_scope_invalid", "导出范围必须是已选论文、当前筛选结果或全部。"
        ) from exc


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PackageCenterError(
            "package_selection_invalid", "导出选择内容格式无效。"
        ) from exc


def normalize_selection(scope: PackageScope, selection: Any) -> tuple[Any, str, int]:
    if scope is PackageScope.ALL:
        if selection not in (None, (), [], {}):
            raise PackageCenterError(
                "package_selection_invalid", "导出全部内容时不能附带局部选择。"
            )
        normalized: Any = None
        count = 0
    elif scope is PackageScope.SELECTED:
        if isinstance(selection, (str, bytes)) or not isinstance(selection, Sequence):
            raise PackageCenterError(
                "package_selection_invalid", "请至少选择一篇论文或一组实验记录。"
            )
        values = tuple(str(item).strip() for item in selection)
        if (
            not values
            or len(values) > MAX_SELECTION_ITEMS
            or any(not value or len(value) > 256 for value in values)
            or len(set(values)) != len(values)
        ):
            raise PackageCenterError(
                "package_selection_invalid", "选择项为空、重复或超过数量上限。"
            )
        normalized = values
        count = len(values)
    else:
        if not isinstance(selection, Mapping) or not selection:
            raise PackageCenterError(
                "package_selection_invalid", "当前筛选结果缺少有效筛选条件。"
            )
        encoded = canonical_json(selection)
        if len(encoded) > MAX_FILTER_BYTES:
            raise PackageCenterError(
                "package_selection_too_large", "筛选条件超过安全上限。"
            )
        normalized = json.loads(encoded.decode("utf-8"))
        count = 0
    digest = hashlib.sha256(canonical_json(normalized)).hexdigest()
    return normalized, digest, count


_FORBIDDEN_PUBLIC_KEYS = {
    "path",
    "package_path",
    "install_path",
    "source_path",
    "destination_path",
    "local_path",
    "file_path",
}


def assert_path_free(value: Any, *, depth: int = 0) -> None:
    if depth > 12:
        raise PackageCenterError("package_result_invalid", "资料包结果层级异常。")
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in _FORBIDDEN_PUBLIC_KEYS:
                raise PackageCenterError(
                    "package_result_unsafe", "资料包结果包含不应公开的本机信息。"
                )
            assert_path_free(child, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        for child in value:
            assert_path_free(child, depth=depth + 1)
    elif isinstance(value, str):
        lowered = value.casefold()
        if (
            lowered.startswith(("/", "\\\\"))
            or "file://" in lowered
            or any(
                marker in lowered
                for marker in ("/users/", "/private/", "/home/", "/volumes/")
            )
            or re.search(r"(?:^|\s)[a-z]:[\\/]", lowered)
        ):
            raise PackageCenterError(
                "package_result_unsafe", "资料包结果包含不应公开的本机信息。"
            )


def public_result(value: Any, *, expected_schema: str = "package-summary-v1") -> dict[str, Any]:
    if hasattr(value, "public_dict"):
        raw = value.public_dict()
    elif isinstance(value, Mapping):
        raw = dict(value)
    else:
        raise PackageCenterError("package_result_invalid", "资料包服务返回了无效结果。")
    if raw.get("schema") != expected_schema:
        raise PackageCenterError("package_result_invalid", "资料包结果版本不受支持。")
    assert_path_free(raw)
    if len(canonical_json(raw)) > MAX_PUBLIC_DTO_BYTES:
        raise PackageCenterError("package_result_too_large", "资料包结果超过安全上限。")
    return raw


def safe_port_error(
    exc: Exception, *, fallback_code: str, fallback_message: str
) -> PackageCenterError:
    if isinstance(exc, PackageCenterError):
        return exc
    code = getattr(exc, "code", None)
    message = getattr(exc, "safe_message", None)
    if isinstance(code, str) and code and isinstance(message, str) and message:
        try:
            assert_path_free({"code": code, "message": message})
        except PackageCenterError:
            return PackageCenterError(fallback_code, fallback_message)
        else:
            return PackageCenterError(
                code,
                message,
                retryable=bool(getattr(exc, "retryable", False)),
            )
    return PackageCenterError(fallback_code, fallback_message)


@dataclass(frozen=True)
class PackageExportPlan:
    token: str
    kind: PackageKind
    scope: PackageScope
    selection_fingerprint: str
    candidate: PayloadPlanCandidate
    created_at: float
    expires_at: float
    selected_count: int

    def public_dict(self, *, now: float) -> dict[str, Any]:
        value = {
            "schema": "package-plan-v1",
            "plan_token": self.token,
            "package_kind": self.kind.value,
            "scope": self.scope.value,
            "package_id": self.candidate.package_id,
            "package_version": self.candidate.package_version,
            "integrity": "sha256-only",
            "confidentiality": "none",
            "trusted_official": False,
            "estimated_bytes": self.candidate.estimated_bytes,
            "exceeds_size_limit": self.candidate.exceeds_size_limit,
            "item_count": self.candidate.item_count,
            "paper_count": self.candidate.paper_count,
            "selected_count": self.selected_count,
            "missing_pdf_count": self.candidate.missing_pdf_count,
            "content_fingerprint": self.candidate.content_fingerprint,
            "rights_requirements": [
                requirement.public_dict()
                for requirement in self.candidate.rights_requirements
            ],
            "expires_in_seconds": max(0, int(self.expires_at - now)),
            "risk_ack_required": {
                "unencrypted_ack": True,
                "unauthenticated_source_ack": True,
                "internal_use_only_ack": True,
            },
            "warning": "用户资料包未加密、不能证明来源，仅限课题组内部传递。",
        }
        assert_path_free(value)
        if len(canonical_json(value)) > MAX_PUBLIC_DTO_BYTES:
            raise PackageCenterError("package_plan_too_large", "资料包计划超过安全上限。")
        return value


__all__ = [
    "DestinationResolver",
    "MaterializedPayload",
    "PackageCenterError",
    "PackageExportPlan",
    "PackageKind",
    "PackageScope",
    "PayloadPlanCandidate",
    "PayloadPlanner",
    "RightsConfirmation",
    "RightsRequirement",
    "SelectionResolver",
    "TransferExporter",
    "TransferImporter",
    "TransferActivator",
    "TransferInspector",
]
