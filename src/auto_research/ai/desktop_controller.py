from __future__ import annotations

import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from auto_research.settings.ai_desktop_service import (
    AIDesktopServiceError,
)
from auto_research.settings.ai_runtime_state import AIRuntimeStateError
from .custom_provider import CustomProviderError

from .consent import AIConsentError
from .business_actions import (
    BUSINESS_ACTION_SCOPES,
    BusinessActionError,
    BusinessPreparedActionRegistry,
)
from .prepared_actions import PreparedActionError, PreparedActionService


DESKTOP_AI_HTTP_ERROR_SCHEMA_VERSION = "desktop-ai-http-error-v1"
MAX_SETTINGS_BODY_BYTES = 32 * 1024
MAX_CREDENTIAL_BODY_BYTES = 8 * 1024
MAX_TEST_BODY_BYTES = 4 * 1024
MAX_CONSENT_BODY_BYTES = 4 * 1024
MAX_PROTECTED_ACTION_BODY_BYTES = 4 * 1024
MAX_BUSINESS_PREPARE_BODY_BYTES = 256 * 1024
_PROVIDER_PATH = r"(?P<provider_id>deepseek|openai|custom)"
_BUSINESS_SCOPE_PATH = (
    r"(?P<scope>librarian|selected_evidence_chat|literature_extraction|personal_suggestion)"
)
_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


class DesktopAISettings(Protocol):
    def catalog(self) -> dict[str, object]: ...

    def get(self) -> dict[str, object]: ...

    def patch(self, payload: Mapping[str, Any]) -> dict[str, object]: ...

    def credential_status(self, provider_id: str) -> dict[str, object]: ...

    def credential_save(
        self, provider_id: str, api_key: object
    ) -> dict[str, object]: ...

    def credential_delete(self, provider_id: str) -> dict[str, object]: ...

    def test(
        self, provider_id: str, prepared_action: object, *, session_id: str
    ) -> dict[str, object]: ...

    def custom_provider_get(self) -> dict[str, object]: ...
    def custom_provider_save(self, payload: Mapping[str, Any]) -> dict[str, object]: ...
    def custom_provider_delete(self, expected_revision: object) -> dict[str, object]: ...


class DesktopAIRequestContext(Protocol):
    """Authenticated platform context; session_id is not renderer input."""

    method: str
    path: str
    body_size: int
    payload: Any
    session_id: str
    session_authenticated: bool
    csrf_validated: bool


@dataclass(frozen=True, slots=True)
class DesktopAIHTTPResponse:
    status: int
    body: Mapping[str, Any]

    def __post_init__(self) -> None:
        if isinstance(self.status, bool) or not 100 <= self.status <= 599:
            raise ValueError("HTTP response status is invalid")
        if not isinstance(self.body, Mapping):
            raise ValueError("HTTP response body must be an object")
        object.__setattr__(self, "body", MappingProxyType(dict(self.body)))


@dataclass(frozen=True, slots=True)
class DesktopAIRoute:
    route_id: str
    method: str
    pattern: str
    body_cap_bytes: int
    success_status: int = 200

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{2,95}", self.route_id):
            raise ValueError("AI route id is invalid")
        if self.method not in {"GET", "POST", "PATCH", "DELETE"}:
            raise ValueError("AI route method is invalid")
        if not self.pattern.startswith("^/api/") or not self.pattern.endswith("$"):
            raise ValueError("AI route pattern must be anchored")
        re.compile(self.pattern)
        if self.method in {"GET", "DELETE"} and self.body_cap_bytes != 0:
            raise ValueError("bodyless AI route has a body cap")
        if self.method in {"POST", "PATCH"} and self.body_cap_bytes <= 0:
            raise ValueError("AI mutation route requires a body cap")

    def match(self, method: str, path: str) -> Mapping[str, str] | None:
        if method != self.method:
            return None
        matched = re.fullmatch(self.pattern, path)
        if matched is None:
            return None
        return MappingProxyType(matched.groupdict())

    def public_dict(self) -> dict[str, object]:
        return {
            "route_id": self.route_id,
            "method": self.method,
            "pattern": self.pattern,
            "body_cap_bytes": self.body_cap_bytes,
            "success_status": self.success_status,
        }


DESKTOP_AI_ROUTES = (
    DesktopAIRoute(
        "desktop_ai.providers",
        "GET",
        r"^/api/desktop/ai/providers$",
        0,
    ),
    DesktopAIRoute(
        "desktop_ai.settings_get",
        "GET",
        r"^/api/desktop/ai/settings$",
        0,
    ),
    DesktopAIRoute(
        "desktop_ai.settings_patch",
        "PATCH",
        r"^/api/desktop/ai/settings$",
        MAX_SETTINGS_BODY_BYTES,
    ),
    DesktopAIRoute(
        "desktop_ai.custom_provider_get",
        "GET",
        r"^/api/desktop/ai/custom-provider$",
        0,
    ),
    DesktopAIRoute(
        "desktop_ai.custom_provider_save",
        "POST",
        r"^/api/desktop/ai/custom-provider$",
        MAX_SETTINGS_BODY_BYTES,
    ),
    DesktopAIRoute(
        "desktop_ai.custom_provider_delete",
        "DELETE",
        r"^/api/desktop/ai/custom-provider/(?P<revision>[1-9][0-9]{0,8})$",
        0,
    ),
    DesktopAIRoute(
        "desktop_ai.credential_get",
        "GET",
        rf"^/api/desktop/ai/credentials/{_PROVIDER_PATH}$",
        0,
    ),
    DesktopAIRoute(
        "desktop_ai.credential_save",
        "POST",
        rf"^/api/desktop/ai/credentials/{_PROVIDER_PATH}$",
        MAX_CREDENTIAL_BODY_BYTES,
    ),
    DesktopAIRoute(
        "desktop_ai.credential_delete",
        "DELETE",
        rf"^/api/desktop/ai/credentials/{_PROVIDER_PATH}$",
        0,
    ),
    DesktopAIRoute(
        "desktop_ai.provider_test_prepare",
        "POST",
        rf"^/api/desktop/ai/providers/{_PROVIDER_PATH}/test-actions$",
        MAX_TEST_BODY_BYTES,
        201,
    ),
    DesktopAIRoute(
        "desktop_ai.provider_test",
        "POST",
        rf"^/api/desktop/ai/providers/{_PROVIDER_PATH}/test$",
        MAX_TEST_BODY_BYTES,
    ),
    DesktopAIRoute(
        "desktop_ai.consent_issue",
        "POST",
        r"^/api/desktop/ai/consents$",
        MAX_CONSENT_BODY_BYTES,
        201,
    ),
    DesktopAIRoute(
        "desktop_ai.business_prepare",
        "POST",
        rf"^/api/desktop/ai/actions/{_BUSINESS_SCOPE_PATH}/prepare$",
        MAX_BUSINESS_PREPARE_BODY_BYTES,
    ),
    DesktopAIRoute(
        "desktop_ai.business_execute",
        "POST",
        rf"^/api/desktop/ai/actions/{_BUSINESS_SCOPE_PATH}/execute$",
        MAX_PROTECTED_ACTION_BODY_BYTES,
    ),
)


_ERROR_STATUS = {
    "ai_desktop_request_invalid": 400,
    "ai_desktop_provider_untrusted": 400,
    "ai_desktop_credential_invalid": 400,
    "ai_desktop_credential_unavailable": 503,
    "ai_desktop_consent_required": 428,
    "ai_desktop_test_busy": 409,
    "ai_desktop_custom_active": 409,
    "ai_runtime_state_invalid": 400,
    "ai_runtime_revision_conflict": 409,
    "ai_runtime_store_unavailable": 503,
    "ai_runtime_verification_failed": 422,
    "ai_runtime_verification_required": 428,
    "ai_consent_invalid": 403,
    "ai_consent_scope_invalid": 400,
    "ai_consent_action_invalid": 400,
    "ai_consent_expired": 410,
    "ai_consent_replayed": 409,
    "ai_consent_state_unavailable": 503,
    "prepared_action_invalid": 403,
    "prepared_action_expired": 410,
    "prepared_action_consumed": 409,
    "prepared_action_stale": 409,
    "prepared_action_store_full": 429,
    "prepared_action_state_unavailable": 503,
    "ai_desktop_test_cooldown": 429,
    "business_action_invalid": 400,
    "business_action_scope_unsupported": 400,
    "business_action_prepare_failed": 422,
    "business_action_execution_failed": 503,
    "business_action_replayed": 409,
    "business_action_store_full": 429,
    "business_action_result_invalid": 502,
    "custom_provider_invalid": 400,
    "custom_provider_conflict": 409,
    "custom_provider_unavailable": 503,
    "custom_provider_endpoint_unsafe": 400,
}


class DesktopAIController:
    """Thin HTTP-shaped dispatcher over shared AI settings and consent services."""

    def __init__(
        self,
        *,
        settings: DesktopAISettings,
        prepared_actions: PreparedActionService,
        business_actions: BusinessPreparedActionRegistry | None = None,
    ) -> None:
        self._settings = settings
        self._prepared = prepared_actions
        self._business = business_actions

    def __call__(self, request: DesktopAIRequestContext) -> DesktopAIHTTPResponse:
        try:
            return self._dispatch(request)
        except _RequestTooLarge:
            return _error_response(
                413,
                "desktop_ai_request_too_large",
                "AI 设置请求超过允许大小。",
                False,
            )
        except (
            AIDesktopServiceError,
            AIRuntimeStateError,
            AIConsentError,
            PreparedActionError,
            BusinessActionError,
            CustomProviderError,
        ) as exc:
            return _known_error(exc)
        except Exception:
            return _error_response(
                500,
                "desktop_ai_request_failed",
                "AI 设置请求未能完成。",
                True,
            )

    def _dispatch(self, request: DesktopAIRequestContext) -> DesktopAIHTTPResponse:
        method = _request_method(request)
        path = _request_path(request)
        matches = [
            (route, parameters)
            for route in DESKTOP_AI_ROUTES
            if (parameters := route.match(method, path)) is not None
        ]
        if not matches:
            allowed = any(route.match(route.method, path) is not None for route in DESKTOP_AI_ROUTES)
            return _error_response(
                405 if allowed else 404,
                "desktop_ai_method_not_allowed" if allowed else "desktop_ai_endpoint_not_found",
                "该 AI 接口不支持当前请求方式。" if allowed else "AI 接口不存在。",
                False,
            )
        route, parameters = matches[0]
        if getattr(request, "session_authenticated", None) is not True:
            return _error_response(
                403,
                "desktop_ai_session_required",
                "无效的桌面会话。",
                False,
            )
        if route.method in {"POST", "PATCH", "DELETE"} and getattr(
            request, "csrf_validated", None
        ) is not True:
            return _error_response(
                403,
                "desktop_ai_csrf_required",
                "桌面写入授权无效。",
                False,
            )
        payload = _body(request, route)
        provider_id = parameters.get("provider_id")

        if route.route_id == "desktop_ai.providers":
            result = self._settings.catalog()
        elif route.route_id == "desktop_ai.settings_get":
            result = self._settings.get()
        elif route.route_id == "desktop_ai.settings_patch":
            result = self._settings.patch(payload)
        elif route.route_id == "desktop_ai.custom_provider_get":
            result = self._settings.custom_provider_get()
        elif route.route_id == "desktop_ai.custom_provider_save":
            result = self._settings.custom_provider_save(payload)
        elif route.route_id == "desktop_ai.custom_provider_delete":
            result = self._settings.custom_provider_delete(int(parameters["revision"]))
        elif route.route_id == "desktop_ai.credential_get":
            result = self._settings.credential_status(str(provider_id))
        elif route.route_id == "desktop_ai.credential_save":
            _exact_keys(payload, {"api_key"})
            result = self._settings.credential_save(
                str(provider_id), payload.get("api_key")
            )
        elif route.route_id == "desktop_ai.credential_delete":
            result = self._settings.credential_delete(str(provider_id))
        elif route.route_id == "desktop_ai.provider_test_prepare":
            if set(payload) not in ({"expected_revision"}, {"expected_revision", "scope"}):
                raise AIDesktopServiceError("ai_desktop_request_invalid")
            result = self._prepared.prepare_capability_test(
                session_id=_session_id(request),
                provider_id=str(provider_id),
                expected_revision=payload.get("expected_revision"),
                business_scope=payload.get("scope"),
            )
        elif route.route_id == "desktop_ai.provider_test":
            _exact_keys(payload, {"action_id", "consent_nonce"})
            action = self._prepared.consume(
                action_id=payload.get("action_id"),
                consent_nonce=payload.get("consent_nonce"),
                session_id=_session_id(request),
            )
            result = self._settings.test(
                str(provider_id),
                action,
                session_id=_session_id(request),
            )
        elif route.route_id == "desktop_ai.consent_issue":
            _exact_keys(payload, {"action_id"})
            result = self._prepared.issue_consent(
                action_id=payload.get("action_id"),
                session_id=_session_id(request),
            )
        elif route.route_id == "desktop_ai.business_prepare":
            if self._business is None:
                return _error_response(
                    503,
                    "desktop_ai_business_unavailable",
                    "该 AI 功能尚未在当前桌面版本中启用。",
                    True,
                )
            scope = str(parameters.get("scope") or "")
            if scope not in BUSINESS_ACTION_SCOPES:
                raise BusinessActionError("business_action_scope_unsupported")
            result = self._business.prepare(
                scope=scope,
                session_id=_session_id(request),
                request=payload,
            )
        elif route.route_id == "desktop_ai.business_execute":
            if self._business is None:
                return _error_response(
                    503,
                    "desktop_ai_business_unavailable",
                    "该 AI 功能尚未在当前桌面版本中启用。",
                    True,
                )
            _exact_keys(payload, {"action_id", "consent_nonce"})
            scope = str(parameters.get("scope") or "")
            action = self._prepared.consume(
                action_id=payload.get("action_id"),
                consent_nonce=payload.get("consent_nonce"),
                session_id=_session_id(request),
            )
            if getattr(action, "scope", None) != scope:
                raise BusinessActionError("business_action_invalid")
            result = self._business.execute(action)
        else:  # pragma: no cover - route table and dispatch are reviewed together
            raise RuntimeError("unhandled AI route")
        if not isinstance(result, Mapping):
            raise RuntimeError("AI service returned a non-object")
        return DesktopAIHTTPResponse(route.success_status, result)

    @staticmethod
    def route_contract() -> tuple[dict[str, object], ...]:
        return tuple(route.public_dict() for route in DESKTOP_AI_ROUTES)


class ConsentProtectedAction:
    """Consume consent for a business action without duplicating platform logic."""

    def __init__(self, prepared_actions: PreparedActionService) -> None:
        self._prepared = prepared_actions

    def consume(
        self,
        *,
        context: DesktopAIRequestContext,
        body: Any,
    ) -> object:
        if getattr(context, "session_authenticated", None) is not True:
            raise AIConsentError("ai_consent_invalid")
        try:
            payload = _mapping_body(body, MAX_PROTECTED_ACTION_BODY_BYTES)
        except (AIDesktopServiceError, _RequestTooLarge) as exc:
            raise AIConsentError("ai_consent_action_invalid") from exc
        if set(payload) != {"action_id", "consent_nonce"}:
            raise AIConsentError("ai_consent_invalid")
        action_id = payload.get("action_id")
        nonce = payload.get("consent_nonce")
        if (
            not isinstance(action_id, str)
            or not action_id
            or not isinstance(nonce, str)
            or not nonce
        ):
            raise AIConsentError("ai_consent_invalid")
        return self._prepared.consume(
            action_id=action_id,
            consent_nonce=nonce,
            session_id=_session_id(context),
        )


def _request_method(request: Any) -> str:
    value = getattr(request, "method", None)
    if not isinstance(value, str):
        raise ValueError("request method is unavailable")
    return value.upper()


def _request_path(request: Any) -> str:
    value = getattr(request, "path", None)
    if not isinstance(value, str) or not value.startswith("/api/") or "?" in value:
        raise ValueError("request path is unavailable")
    return value


def _session_id(request: Any) -> str:
    value = getattr(request, "session_id", None)
    if not isinstance(value, str) or _SESSION_RE.fullmatch(value) is None:
        raise AIConsentError("ai_consent_invalid")
    return value


def _body(request: Any, route: DesktopAIRoute) -> Mapping[str, Any]:
    body_size = getattr(request, "body_size", None)
    if isinstance(body_size, bool) or not isinstance(body_size, int) or body_size < 0:
        raise AIDesktopServiceError("ai_desktop_request_invalid")
    payload = getattr(request, "payload", None)
    if route.body_cap_bytes == 0:
        if body_size != 0 or payload not in (None, {}):
            raise AIDesktopServiceError("ai_desktop_request_invalid")
        return MappingProxyType({})
    if body_size > route.body_cap_bytes:
        raise _RequestTooLarge
    return _mapping_body(payload, route.body_cap_bytes)


def _mapping_body(payload: Any, cap: int) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise AIDesktopServiceError("ai_desktop_request_invalid")
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AIDesktopServiceError("ai_desktop_request_invalid") from exc
    if len(encoded) > cap:
        raise _RequestTooLarge
    return MappingProxyType(dict(payload))


def _exact_keys(payload: Mapping[str, Any], keys: set[str]) -> None:
    if set(payload) != keys:
        raise AIDesktopServiceError("ai_desktop_request_invalid")


class _RequestTooLarge(Exception):
    pass


def _known_error(
    error: AIDesktopServiceError
    | AIRuntimeStateError
    | AIConsentError
    | PreparedActionError
    | BusinessActionError,
) -> DesktopAIHTTPResponse:
    status = _ERROR_STATUS.get(error.code, 500)
    response = _error_response(
        status,
        error.code if status != 500 else "desktop_ai_request_failed",
        error.safe_message if status != 500 else "AI 设置请求未能完成。",
        error.retryable if status != 500 else True,
    )
    if status != 500 and isinstance(error, BusinessActionError):
        body = dict(response.body)
        for key in ("cause_code", "stage", "next_action"):
            value = getattr(error, key, "")
            if value:
                body[key] = value
        response = DesktopAIHTTPResponse(status, body)
    return response


def _error_response(
    status: int,
    code: str,
    message: str,
    retryable: bool,
) -> DesktopAIHTTPResponse:
    return DesktopAIHTTPResponse(
        status,
        {
            "schema_version": DESKTOP_AI_HTTP_ERROR_SCHEMA_VERSION,
            "code": code,
            "message": message,
            "retryable": retryable,
        },
    )


__all__ = [
    "DESKTOP_AI_ROUTES",
    "ConsentProtectedAction",
    "DesktopAIController",
    "DesktopAIHTTPResponse",
    "DesktopAIRequestContext",
    "DesktopAIRoute",
    "DesktopAISettings",
]
