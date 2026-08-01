from __future__ import annotations

import json
import re
from http import HTTPStatus
from typing import Any, Protocol
from urllib.parse import urlparse

from package_import_service import PackageImportService, PackageImportServiceError
from package_job_state import PackageJobStateError


PACKAGE_STATUS_PATH = "/api/desktop/evidence-packages"
PACKAGE_IMPORT_PATH = "/api/desktop/evidence-packages/import"
PACKAGE_ROLLBACK_PATH = "/api/desktop/evidence-packages/rollback"
PACKAGE_JOB_PATH_RE = re.compile(r"^/api/desktop/evidence-package-jobs/([A-Za-z0-9_-]{16,128})$")
MAX_PACKAGE_API_REQUEST_BYTES = 8_192


class PackageHTTPHandler(Protocol):
    path: str

    def _content_length(self, maximum: int, *, require_body: bool = False) -> int: ...

    def _read_exact_body(self, length: int) -> bytes: ...

    def json_response(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None: ...


class PackageAPI:
    def __init__(self, service: PackageImportService) -> None:
        self.service = service

    def handle_get(self, handler: PackageHTTPHandler) -> bool:
        parsed = urlparse(handler.path)
        if parsed.query:
            return False
        if parsed.path == PACKAGE_STATUS_PATH:
            handler.json_response(self.service.status().public_dict())
            return True
        match = PACKAGE_JOB_PATH_RE.fullmatch(parsed.path)
        if match:
            try:
                snapshot = self.service.get_job(match.group(1))
            except PackageJobStateError as exc:
                handler.json_response(exc.error.public_dict(), HTTPStatus.NOT_FOUND)
            else:
                handler.json_response(snapshot.public_dict())
            return True
        return False

    def handle_post(self, handler: PackageHTTPHandler) -> bool:
        parsed = urlparse(handler.path)
        if parsed.query or parsed.path not in {PACKAGE_IMPORT_PATH, PACKAGE_ROLLBACK_PATH}:
            return False
        try:
            body = self._read_json(handler)
            if parsed.path == PACKAGE_IMPORT_PATH:
                if set(body) != {"selection_id"}:
                    raise PackageImportServiceError(
                        "package_selection_invalid",
                        "资料包选择请求无效。",
                        retryable=True,
                    )
                snapshot = self.service.start_import(body["selection_id"])
            else:
                if set(body) != {"package_id", "target_version"}:
                    raise PackageImportServiceError(
                        "rollback_failed", "回退请求无效。", retryable=False
                    )
                snapshot = self.service.start_rollback(
                    body["package_id"], body["target_version"]
                )
        except PackageJobStateError as exc:
            status = HTTPStatus.CONFLICT if exc.code == "package_busy" else HTTPStatus.BAD_REQUEST
            handler.json_response(exc.error.public_dict(), status)
        except PackageImportServiceError as exc:
            handler.json_response(exc.public_dict(), HTTPStatus.BAD_REQUEST)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            handler.json_response(
                {
                    "code": "package_request_invalid",
                    "message": "资料包请求格式无效。",
                    "retryable": True,
                },
                HTTPStatus.BAD_REQUEST,
            )
        else:
            handler.json_response(snapshot.public_dict(), HTTPStatus.ACCEPTED)
        return True

    @staticmethod
    def _read_json(handler: PackageHTTPHandler) -> dict[str, Any]:
        length = handler._content_length(
            MAX_PACKAGE_API_REQUEST_BYTES,
            require_body=True,
        )
        value = json.loads(handler._read_exact_body(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("package request must be an object")
        return value
