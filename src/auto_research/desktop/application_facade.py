from __future__ import annotations

from typing import Any

from .routing import (
    Controller,
    DesktopErrorDTO,
    DesktopFacadeError,
    RequestContext,
    RouteRegistry,
)


class DesktopApplicationFacade:
    """Dispatch protected desktop requests to injected platform-neutral controllers.

    The facade deliberately does not parse HTTP, open files, resolve credentials,
    or own any scientific/product state.  A platform host authenticates and
    parses the request, then supplies a :class:`RequestContext`.
    """

    def __init__(self, registry: RouteRegistry) -> None:
        if not isinstance(registry, RouteRegistry):
            raise TypeError("registry must be a RouteRegistry")
        self.registry = registry
        self._controllers: dict[str, Controller] = {}

    def register_controller(self, name: str, controller: Controller) -> None:
        if not callable(controller):
            raise TypeError("controller must be callable")
        known = {route.controller for route in self.registry.routes}
        if name not in known:
            raise ValueError("controller is not declared by the route registry")
        if name in self._controllers:
            raise ValueError(f"controller already registered: {name}")
        self._controllers[name] = controller

    @property
    def registered_controllers(self) -> tuple[str, ...]:
        return tuple(sorted(self._controllers))

    def dispatch(self, request: RequestContext) -> Any:
        if not isinstance(request, RequestContext):
            raise TypeError("request must be a RequestContext")
        spec, path_params = self.registry.resolve(request.method, request.path)
        if not request.session_authenticated:
            self._fail("desktop_session_required", "无效的桌面会话。", 403)
        if request.mode not in spec.allowed_modes:
            self._fail(
                "desktop_mode_forbidden",
                "当前工作台模式不能使用此功能。",
                403,
            )
        if request.body_size > spec.body_cap_bytes:
            self._fail(
                "desktop_request_too_large",
                "桌面请求超过允许大小。",
                413,
            )
        if spec.csrf_required and not request.csrf_validated:
            self._fail(
                "desktop_csrf_required",
                "桌面写入授权无效。",
                403,
            )
        controller = self._controllers.get(spec.controller)
        if controller is None:
            self._fail(
                "desktop_controller_unavailable",
                "该功能尚未在当前桌面版本中启用。",
                503,
                retryable=True,
            )
        bound = request.bind(spec, path_params)
        try:
            return controller(bound)
        except DesktopFacadeError:
            raise
        except Exception:
            # Never expose controller exceptions, local paths, keys, payloads or
            # database errors through the shared transport contract.
            self._fail(
                "desktop_request_failed",
                "桌面请求未能完成。",
                500,
                retryable=True,
            )

    @staticmethod
    def _fail(
        code: str,
        message: str,
        status: int,
        *,
        retryable: bool = False,
    ) -> None:
        raise DesktopFacadeError(
            DesktopErrorDTO(code, message, status, retryable=retryable)
        )

