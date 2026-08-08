from __future__ import annotations

import inspect
import sys
import unittest
from http import HTTPStatus
from pathlib import Path


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = DESKTOP_ROOT.parents[1]
for path in (PROJECT_ROOT / "src", DESKTOP_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from desktop_server import DesktopEvidenceHandler, create_desktop_server  # noqa: E402
from personal_import_api import PersonalImportAPI  # noqa: E402


IMPORT_ID = "personal_import_0123456789abcdef"
PERSONAL_POST_PATHS = (
    "/api/desktop/personal-imports/preview",
    "/api/desktop/personal-imports/search-refresh",
    f"/api/desktop/personal-imports/{IMPORT_ID}/draft",
    f"/api/desktop/personal-imports/{IMPORT_ID}/confirm",
)


class _PersonalAPI:
    def __init__(self) -> None:
        self.post_calls: list[str] = []
        self.get_calls: list[str] = []

    @staticmethod
    def is_post_route(path: str) -> bool:
        return PersonalImportAPI.is_post_route(path)

    def handle_post(self, handler) -> bool:
        self.post_calls.append(handler.path)
        handler.json_response({"ok": True})
        return True

    def handle_get(self, handler) -> bool:
        self.get_calls.append(handler.path)
        handler.json_response({"state": "ready"})
        return True


def _post_handler(path: str, *, read_only: bool, authorized: bool = True):
    handler = object.__new__(DesktopEvidenceHandler)
    handler.path = path
    handler.read_only = read_only
    handler.package_api = None
    handler.personal_import_api = _PersonalAPI()
    handler.responses = []
    handler.authorization_paths = []

    def authorize(candidate: str) -> bool:
        handler.authorization_paths.append(candidate)
        return authorized

    handler._authorize_post = authorize
    handler.json_response = lambda payload, status=HTTPStatus.OK: handler.responses.append(
        (payload, status)
    )
    return handler


class PersonalImportRouteTests(unittest.TestCase):
    def test_personal_routes_run_only_after_existing_desktop_authorization(self) -> None:
        for path in PERSONAL_POST_PATHS:
            with self.subTest(path=path):
                handler = _post_handler(path, read_only=False)
                handler.do_POST()
                self.assertEqual(handler.authorization_paths, [path])
                self.assertEqual(handler.personal_import_api.post_calls, [path])
                self.assertEqual(handler.responses[0][1], HTTPStatus.OK)

                denied = _post_handler(path, read_only=False, authorized=False)
                denied.do_POST()
                self.assertEqual(denied.authorization_paths, [path])
                self.assertEqual(denied.personal_import_api.post_calls, [])

    def test_read_only_personal_mutations_are_fixed_403_without_service_call(self) -> None:
        for path in PERSONAL_POST_PATHS:
            with self.subTest(path=path):
                handler = _post_handler(path, read_only=True)
                handler.do_POST()
                self.assertEqual(handler.authorization_paths, [path])
                self.assertEqual(handler.personal_import_api.post_calls, [])
                payload, status = handler.responses[0]
                self.assertEqual(status, HTTPStatus.FORBIDDEN)
                self.assertEqual(payload["code"], "read_only")

    def test_recognized_personal_route_with_query_returns_fixed_400(self) -> None:
        class _NoCallService:
            def preview(self, _selection_id):
                raise AssertionError("query route must not call personal service")

        handler = _post_handler(
            "/api/desktop/personal-imports/preview?unsafe=1",
            read_only=False,
        )
        handler.personal_import_api = PersonalImportAPI(_NoCallService())  # type: ignore[arg-type]

        handler.do_POST()

        payload, status = handler.responses[0]
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(payload["code"], "personal_request_invalid")

    def test_server_composition_has_explicit_personal_api_injection(self) -> None:
        signature = inspect.signature(create_desktop_server)
        self.assertIn("personal_import_api", signature.parameters)
        source = inspect.getsource(create_desktop_server)
        self.assertIn('"personal_import_api": personal_import_api', source)


if __name__ == "__main__":
    unittest.main()
