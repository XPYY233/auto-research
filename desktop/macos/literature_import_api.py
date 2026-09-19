"""Protected HTTP adapter for PDF import; no legacy Web business dispatch."""
from __future__ import annotations

from http import HTTPStatus
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from auto_research.desktop.application_facade import DesktopApplicationFacade
from auto_research.desktop.literature_import import LiteratureImportController
from auto_research.desktop.route_catalog import build_default_route_registry
from auto_research.desktop.routing import DesktopFacadeError, RequestContext
from auto_research.evidence.uploads import UploadService

UPLOAD_PATH = '/api/uploads/pdf'


class ImportHTTPHandler(Protocol):
    path: str
    close_connection: bool

    def _content_length(self, maximum: int, *, require_body: bool) -> int: ...
    def _read_exact_body(self, length: int) -> bytes: ...
    def _has_session(self, *, require_origin: bool = False) -> bool: ...
    def _csrf_valid(self) -> bool: ...
    def json_response(self, payload: Any, status: int = 200) -> None: ...


class LiteratureImportAPI:
    def __init__(self, service: UploadService) -> None:
        registry = build_default_route_registry()
        self.facade = DesktopApplicationFacade(registry)
        self.facade.register_controller('workspace.upload_pdf', LiteratureImportController(service))
        self.route, _ = registry.resolve('POST', UPLOAD_PATH)

    def handle_post(self, handler: ImportHTTPHandler) -> None:
        # The host already enforces session, Origin, media type, CSRF and mode.
        # Reconfirm capabilities in the shared envelope before the write.
        try:
            parsed = urlparse(handler.path)
            length = handler._content_length(self.route.body_cap_bytes, require_body=True)
            payload = handler._read_exact_body(length)
            request = RequestContext(
                request_id='literature-import', method='POST', path=parsed.path,
                mode='desktop', body_size=length, payload=payload,
                query=parse_qs(parsed.query),
                session_authenticated=handler._has_session(require_origin=True),
                csrf_validated=handler._csrf_valid(),
            )
            result = self.facade.dispatch(request)
        except ValueError:
            # The unread body must not become the next keep-alive request.
            handler.close_connection = True
            handler.json_response({'error': '上传文件为空、过大或请求格式无效。',
                                   'code': 'literature_import_invalid'}, HTTPStatus.BAD_REQUEST)
        except DesktopFacadeError as exc:
            handler.json_response(exc.error.public_dict(), exc.error.http_status)
        else:
            status = HTTPStatus.CREATED if result['outcome'] == 'accepted' else HTTPStatus.OK
            handler.json_response(result, status)
