from __future__ import annotations

import json
import re
from http import HTTPStatus
from typing import Any, Protocol
from urllib.parse import urlparse

from auto_research.product.package_center import (
    PackageCenter,
    PackageExportService,
    PackageJobService,
    PackageTransferImportService,
)
from auto_research.product.package_center_models import PackageCenterError


PACKAGE_CENTER_PATH = "/api/desktop/package-center"
PACKAGE_CENTER_INSPECT_PATH = f"{PACKAGE_CENTER_PATH}/inspect"
PACKAGE_CENTER_EXPORT_PLAN_PATH = f"{PACKAGE_CENTER_PATH}/export-plan"
PACKAGE_CENTER_EXPORT_PATH = f"{PACKAGE_CENTER_PATH}/export"
PACKAGE_CENTER_IMPORT_PATH = f"{PACKAGE_CENTER_PATH}/import"
PACKAGE_CENTER_JOB_PATH_RE = re.compile(
    r"^/api/desktop/package-center/jobs/([A-Za-z0-9_-]{16,128})$"
)
MAX_PACKAGE_CENTER_REQUEST_BYTES = 512 * 1024


class PackageCenterHTTPHandler(Protocol):
    path: str

    def _content_length(self, maximum: int, *, require_body: bool = False) -> int: ...

    def _read_exact_body(self, length: int) -> bytes: ...

    def json_response(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None: ...


class PackageCenterSummaryProvider(Protocol):
    def summary(self) -> dict[str, Any]: ...


class PackageCenterAPI:
    """Path-free HTTP projection over the shared package-center services."""

    def __init__(
        self,
        *,
        summary_provider: PackageCenterSummaryProvider,
        center: PackageCenter,
        export_service: PackageExportService,
        import_service: PackageTransferImportService,
        jobs: PackageJobService,
    ) -> None:
        self._summary_provider = summary_provider
        self._center = center
        self._export_service = export_service
        self._import_service = import_service
        self._jobs = jobs

    @staticmethod
    def is_post_route(path: str) -> bool:
        return urlparse(path).path in {
            PACKAGE_CENTER_INSPECT_PATH,
            PACKAGE_CENTER_EXPORT_PLAN_PATH,
            PACKAGE_CENTER_EXPORT_PATH,
            PACKAGE_CENTER_IMPORT_PATH,
        }

    def handle_get(self, handler: PackageCenterHTTPHandler) -> bool:
        parsed = urlparse(handler.path)
        known = parsed.path == PACKAGE_CENTER_PATH or bool(
            PACKAGE_CENTER_JOB_PATH_RE.fullmatch(parsed.path)
        )
        if parsed.query:
            if known:
                self._respond_error(
                    handler,
                    PackageCenterError(
                        "package_request_invalid", "资料包中心请求参数无效。"
                    ),
                )
                return True
            return False
        try:
            if parsed.path == PACKAGE_CENTER_PATH:
                handler.json_response(self._summary_provider.summary())
                return True
            match = PACKAGE_CENTER_JOB_PATH_RE.fullmatch(parsed.path)
            if match:
                handler.json_response(self._jobs.get(match.group(1)))
                return True
        except PackageCenterError as exc:
            self._respond_error(handler, exc)
            return True
        return False

    def handle_post(self, handler: PackageCenterHTTPHandler) -> bool:
        parsed = urlparse(handler.path)
        if not self.is_post_route(handler.path):
            return False
        if parsed.query:
            self._respond_error(
                handler,
                PackageCenterError(
                    "package_request_invalid", "资料包中心请求参数无效。"
                ),
            )
            return True
        try:
            body = self._read_json(handler)
            if parsed.path == PACKAGE_CENTER_INSPECT_PATH:
                self._require_fields(body, {"selection_token"})
                result = self._center.inspect(body["selection_token"])
                status = HTTPStatus.OK
            elif parsed.path == PACKAGE_CENTER_EXPORT_PLAN_PATH:
                self._require_fields(body, {"kind", "scope", "selection"})
                result = self._export_service.plan(
                    body["kind"], body["scope"], body["selection"]
                )
                status = HTTPStatus.OK
            elif parsed.path == PACKAGE_CENTER_EXPORT_PATH:
                self._require_fields(
                    body,
                    {"plan_token", "rights_confirmations", "destination_token"},
                )
                result = self._export_service.start(
                    body["plan_token"],
                    body["rights_confirmations"],
                    body["destination_token"],
                )
                status = HTTPStatus.ACCEPTED
            else:
                self._require_fields(
                    body,
                    {
                        "selection_token",
                        "checksum_ack",
                        "expected_sha",
                        "keep_conflicts",
                    },
                )
                result = self._import_service.start(
                    body["selection_token"],
                    checksum_ack=body["checksum_ack"],
                    expected_sha=body["expected_sha"],
                    keep_conflicts=body["keep_conflicts"],
                )
                status = HTTPStatus.ACCEPTED
        except PackageCenterError as exc:
            self._respond_error(handler, exc)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._respond_error(
                handler,
                PackageCenterError(
                    "package_request_invalid", "资料包中心请求格式无效。"
                ),
            )
        else:
            handler.json_response(result, status)
        return True

    @staticmethod
    def _require_fields(body: dict[str, Any], fields: set[str]) -> None:
        if set(body) != fields:
            raise PackageCenterError(
                "package_request_invalid", "资料包中心请求字段无效。"
            )

    @staticmethod
    def _read_json(handler: PackageCenterHTTPHandler) -> dict[str, Any]:
        length = handler._content_length(
            MAX_PACKAGE_CENTER_REQUEST_BYTES,
            require_body=True,
        )
        value = json.loads(handler._read_exact_body(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("package-center request must be an object")
        return value

    @staticmethod
    def _respond_error(
        handler: PackageCenterHTTPHandler, error: PackageCenterError
    ) -> None:
        if error.code == "package_job_not_found":
            status = HTTPStatus.NOT_FOUND
        elif error.code in {
            "package_busy",
            "package_plan_stale",
            "package_destination_exists",
        }:
            status = HTTPStatus.CONFLICT
        elif error.code in {"package_size", "package_result_too_large"}:
            status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        elif error.code in {
            "package_inspect_failed",
            "package_export_failed",
            "transfer_import_failed",
            "package_catalog_unavailable",
        }:
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        else:
            status = HTTPStatus.BAD_REQUEST
        handler.json_response(error.public_dict(), status)


__all__ = [
    "MAX_PACKAGE_CENTER_REQUEST_BYTES",
    "PACKAGE_CENTER_EXPORT_PATH",
    "PACKAGE_CENTER_EXPORT_PLAN_PATH",
    "PACKAGE_CENTER_IMPORT_PATH",
    "PACKAGE_CENTER_INSPECT_PATH",
    "PACKAGE_CENTER_PATH",
    "PackageCenterAPI",
]
