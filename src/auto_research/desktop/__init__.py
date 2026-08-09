"""Platform-neutral desktop application contracts.

This package intentionally contains no HTTP server, filesystem picker, secure
credential store, scientific database, or product algorithm.  macOS and
Windows hosts adapt their protected transports to these small contracts.
"""

from .application_facade import DesktopApplicationFacade
from .route_catalog import DEFAULT_DESKTOP_ROUTES, build_default_route_registry
from .routing import (
    Controller,
    DesktopErrorDTO,
    DesktopFacadeError,
    RequestContext,
    RouteRegistry,
    RouteSpec,
)

__all__ = [
    "Controller",
    "DEFAULT_DESKTOP_ROUTES",
    "DesktopApplicationFacade",
    "DesktopErrorDTO",
    "DesktopFacadeError",
    "RequestContext",
    "RouteRegistry",
    "RouteSpec",
    "build_default_route_registry",
]
