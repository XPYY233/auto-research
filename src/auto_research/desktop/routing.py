from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Mapping, Protocol


ALLOWED_HTTP_METHODS = frozenset({"GET", "POST", "PATCH", "DELETE"})
ALLOWED_DESKTOP_MODES = frozenset(
    {"desktop", "maintenance", "search_only_compat"}
)


class Controller(Protocol):
    def __call__(self, request: "RequestContext") -> Any: ...


@dataclass(frozen=True, slots=True)
class DesktopErrorDTO:
    """Stable, path-free public desktop error.

    ``http_status`` is transport metadata and is deliberately omitted from the
    public body.  No exception text, path, key, request body, or internal ID is
    accepted by this DTO.
    """

    code: str
    message: str
    http_status: int
    retryable: bool = False
    stage: str | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,95}", self.code):
            raise ValueError("desktop error code is invalid")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("desktop error message is required")
        if re.search(
            r"(?i)(?:file://|[a-z]:\\|/(?:Users|home|private|var|tmp|etc|opt|Volumes)/)",
            self.message,
        ):
            raise ValueError("desktop error message must not expose a local path")
        if not 400 <= int(self.http_status) <= 599:
            raise ValueError("desktop error status is invalid")
        if self.stage is not None and not re.fullmatch(
            r"[a-z][a-z0-9_]{1,63}", self.stage
        ):
            raise ValueError("desktop error stage is invalid")

    def public_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": bool(self.retryable),
        }
        if self.stage is not None:
            payload["stage"] = self.stage
        return payload


class DesktopFacadeError(RuntimeError):
    def __init__(self, error: DesktopErrorDTO) -> None:
        super().__init__(error.message)
        self.error = error


@dataclass(frozen=True, slots=True)
class RouteSpec:
    """One platform-neutral route declaration.

    Exactly one of ``path`` and ``pattern`` is required.  Patterns are anchored
    regular expressions and should use named groups for path parameters.
    """

    route_id: str
    method: str
    controller: str
    body_cap_bytes: int
    mutation: bool
    csrf_required: bool
    allowed_modes: frozenset[str]
    path: str | None = None
    pattern: str | None = None
    _compiled_pattern: re.Pattern[str] | None = field(
        init=False, repr=False, compare=False, default=None
    )

    def __post_init__(self) -> None:
        method = self.method.upper()
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "allowed_modes", frozenset(self.allowed_modes))
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{2,95}", self.route_id):
            raise ValueError("route id is invalid")
        if method not in ALLOWED_HTTP_METHODS:
            raise ValueError("route method is invalid")
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{2,95}", self.controller):
            raise ValueError("controller name is invalid")
        if (self.path is None) == (self.pattern is None):
            raise ValueError("route requires exactly one path or pattern")
        if self.path is not None:
            if not self.path.startswith("/api/") or "?" in self.path:
                raise ValueError("route path must be an API path without query")
        else:
            assert self.pattern is not None
            if not self.pattern.startswith("^/api/") or not self.pattern.endswith("$"):
                raise ValueError("route pattern must be an anchored API pattern")
            try:
                compiled = re.compile(self.pattern)
            except re.error as exc:
                raise ValueError("route pattern is invalid") from exc
            object.__setattr__(self, "_compiled_pattern", compiled)
        if isinstance(self.body_cap_bytes, bool) or self.body_cap_bytes < 0:
            raise ValueError("route body cap is invalid")
        if method in {"GET", "DELETE"} and self.body_cap_bytes != 0:
            raise ValueError("GET and DELETE routes cannot accept request bodies")
        if method in {"POST", "PATCH"} and self.body_cap_bytes <= 0:
            raise ValueError("POST and PATCH routes require a positive body cap")
        if self.mutation and method == "GET":
            raise ValueError("GET routes cannot be mutations")
        if self.csrf_required and not self.mutation:
            raise ValueError("CSRF is required only for mutations")
        if self.mutation and not self.csrf_required:
            raise ValueError("desktop mutations must require CSRF")
        if not self.allowed_modes or not self.allowed_modes <= ALLOWED_DESKTOP_MODES:
            raise ValueError("route allowed modes are invalid")

    @property
    def locator(self) -> str:
        assert self.path is not None or self.pattern is not None
        return self.path if self.path is not None else str(self.pattern)

    def match_path(self, path: str) -> Mapping[str, str] | None:
        if self.path is not None:
            return MappingProxyType({}) if path == self.path else None
        assert self._compiled_pattern is not None
        matched = self._compiled_pattern.fullmatch(path)
        if matched is None:
            return None
        return MappingProxyType(
            {key: value for key, value in matched.groupdict().items() if value is not None}
        )

    def public_contract(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "method": self.method,
            "path": self.path,
            "pattern": self.pattern,
            "controller": self.controller,
            "body_cap_bytes": self.body_cap_bytes,
            "mutation": self.mutation,
            "csrf_required": self.csrf_required,
            "allowed_modes": sorted(self.allowed_modes),
        }


@dataclass(frozen=True, slots=True)
class RequestContext:
    """An authenticated, already-parsed request handed to the shared facade."""

    request_id: str
    method: str
    path: str
    mode: str
    body_size: int = 0
    payload: Any = None
    query: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    session_authenticated: bool = True
    csrf_validated: bool = False
    route_id: str | None = None
    path_params: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        method = self.method.upper()
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "query", _freeze_multi_mapping(self.query))
        object.__setattr__(self, "path_params", MappingProxyType(dict(self.path_params)))
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise ValueError("request id is required")
        if method not in ALLOWED_HTTP_METHODS:
            raise ValueError("request method is invalid")
        if not self.path.startswith("/api/") or "?" in self.path:
            raise ValueError("request path is invalid")
        if self.mode not in ALLOWED_DESKTOP_MODES:
            raise ValueError("request mode is invalid")
        if isinstance(self.body_size, bool) or self.body_size < 0:
            raise ValueError("request body size is invalid")

    def bind(self, spec: RouteSpec, path_params: Mapping[str, str]) -> "RequestContext":
        return replace(self, route_id=spec.route_id, path_params=path_params)


def _freeze_multi_mapping(
    values: Mapping[str, tuple[str, ...]],
) -> Mapping[str, tuple[str, ...]]:
    frozen: dict[str, tuple[str, ...]] = {}
    for key, entries in values.items():
        if not isinstance(key, str):
            raise ValueError("query key is invalid")
        if isinstance(entries, str):
            frozen[key] = (entries,)
        else:
            frozen[key] = tuple(str(entry) for entry in entries)
    return MappingProxyType(frozen)


class RouteRegistry:
    """Validated route catalog with deterministic exact-before-pattern matching."""

    def __init__(self, routes: tuple[RouteSpec, ...] | list[RouteSpec] = ()) -> None:
        self._routes: list[RouteSpec] = []
        self._ids: set[str] = set()
        self._locations: set[tuple[str, str]] = set()
        for route in routes:
            self.add(route)

    def add(self, route: RouteSpec) -> None:
        if not isinstance(route, RouteSpec):
            raise TypeError("route must be a RouteSpec")
        location = (route.method, route.locator)
        if route.route_id in self._ids:
            raise ValueError(f"duplicate route id: {route.route_id}")
        if location in self._locations:
            raise ValueError(f"duplicate route: {route.method} {route.locator}")
        self._routes.append(route)
        self._ids.add(route.route_id)
        self._locations.add(location)

    @property
    def routes(self) -> tuple[RouteSpec, ...]:
        return tuple(self._routes)

    def resolve(self, method: str, path: str) -> tuple[RouteSpec, Mapping[str, str]]:
        normalized = method.upper()
        exact: list[tuple[RouteSpec, Mapping[str, str]]] = []
        patterns: list[tuple[RouteSpec, Mapping[str, str]]] = []
        for route in self._routes:
            if route.method != normalized:
                continue
            parameters = route.match_path(path)
            if parameters is None:
                continue
            target = exact if route.path is not None else patterns
            target.append((route, parameters))
        matches = exact or patterns
        if not matches:
            if self.allowed_methods(path):
                raise DesktopFacadeError(
                    DesktopErrorDTO(
                        "desktop_method_not_allowed",
                        "该桌面接口不支持当前请求方式。",
                        405,
                    )
                )
            raise DesktopFacadeError(
                DesktopErrorDTO(
                    "desktop_endpoint_not_found",
                    "桌面接口不存在。",
                    404,
                )
            )
        if len(matches) != 1:
            raise DesktopFacadeError(
                DesktopErrorDTO(
                    "desktop_route_ambiguous",
                    "桌面路由配置冲突。",
                    500,
                )
            )
        return matches[0]

    def allowed_methods(self, path: str) -> tuple[str, ...]:
        methods = {
            route.method for route in self._routes if route.match_path(path) is not None
        }
        return tuple(sorted(methods))

    def golden_contract(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            route.public_contract()
            for route in sorted(self._routes, key=lambda item: item.route_id)
        )
