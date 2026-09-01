from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from auto_research.evidence.table_structure_export import TableStructureExportArtifact
from auto_research.evidence.table_structure_service import (
    TableStructureServiceError,
    WorkspaceTableStructureService,
)

from official_table_structure_service import (
    OfficialTableStructureService,
    OfficialTableStructureServiceError,
)


TABLE_STRUCTURE_PATH = "/api/desktop/table-structures"
TABLE_STRUCTURE_REVIEW_PATH = "/api/desktop/table-structures/reviews"
TABLE_STRUCTURE_CANDIDATE_PATH = "/api/desktop/table-structures/candidates"
TABLE_STRUCTURE_EXPORT_PATH = "/api/desktop/table-structures/export"
MAX_TABLE_STRUCTURE_REQUEST_BYTES = 64 * 1024


class TableStructureHTTPHandler(Protocol):
    path: str
    wfile: Any

    def _content_length(self, maximum: int, *, require_body: bool = False) -> int: ...

    def _read_exact_body(self, length: int) -> bytes: ...

    def json_response(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None: ...

    def send_response(self, status: HTTPStatus) -> None: ...

    def send_header(self, name: str, value: str) -> None: ...

    def end_headers(self) -> None: ...


class TableStructureAPI:
    """Thin desktop adapter; the host owns session, Origin, CSRF and framing."""

    def __init__(
        self,
        service: WorkspaceTableStructureService,
        *,
        official_service: OfficialTableStructureService | None = None,
    ) -> None:
        self.service = service
        self.official_service = official_service

    def handle_get(self, handler: TableStructureHTTPHandler) -> bool:
        parsed = urlparse(handler.path)
        if parsed.path not in {TABLE_STRUCTURE_PATH, TABLE_STRUCTURE_EXPORT_PATH}:
            return False
        try:
            query = parse_qs(
                parsed.query,
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=5,
            )
            if any(len(values) != 1 or values[0] == "" for values in query.values()):
                raise TableStructureServiceError("table_structure_service_invalid")
            if parsed.path == TABLE_STRUCTURE_PATH:
                scope = _query_scope(query)
                flag = query["include_unverified"][0]
                if flag not in {"0", "1"}:
                    raise _invalid_error(scope)
                if scope == "official":
                    payload = self._official().get(
                        query["entity_uid"][0],
                        source_id=query["source_id"][0],
                        include_unverified=flag == "1",
                    )
                else:
                    payload = self.service.get(
                        query["entity_uid"][0], include_unverified=flag == "1"
                    )
            else:
                scope = _query_scope(query, export=True)
                if scope == "official":
                    artifact = self._official().export(
                        query["entity_uid"][0],
                        source_id=query["source_id"][0],
                        format=query["format"][0],
                    )
                else:
                    artifact = self.service.export(
                        query["entity_uid"][0], format=query["format"][0]
                    )
        except (ValueError, TableStructureServiceError, OfficialTableStructureServiceError) as exc:
            error = _public_error(exc)
            handler.json_response(error.public_dict(), _error_status(error))
        except Exception:
            error = TableStructureServiceError("table_structure_service_unavailable")
            handler.json_response(error.public_dict(), _error_status(error))
        else:
            if parsed.path == TABLE_STRUCTURE_EXPORT_PATH:
                self._send_artifact(handler, artifact)
            else:
                handler.json_response(payload)
        return True

    @staticmethod
    def is_post_route(path: str) -> bool:
        return urlparse(path).path in {
            TABLE_STRUCTURE_CANDIDATE_PATH,
            TABLE_STRUCTURE_REVIEW_PATH,
        }

    def handle_post(self, handler: TableStructureHTTPHandler) -> bool:
        parsed = urlparse(handler.path)
        if parsed.path not in {
            TABLE_STRUCTURE_CANDIDATE_PATH,
            TABLE_STRUCTURE_REVIEW_PATH,
        }:
            return False
        try:
            if parsed.query:
                raise TableStructureServiceError("table_structure_service_invalid")
            length = handler._content_length(
                MAX_TABLE_STRUCTURE_REQUEST_BYTES, require_body=True
            )
            body = json.loads(handler._read_exact_body(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise TableStructureServiceError("table_structure_service_invalid")
            if parsed.path == TABLE_STRUCTURE_CANDIDATE_PATH:
                required = {"source_scope", "source_id", "entity_uid", "expected_version"}
                if "manual_rows" in body:
                    required.add("manual_rows")
                if set(body) != required or body.get("source_scope") != "official":
                    raise OfficialTableStructureServiceError(
                        "official_table_structure_invalid"
                    )
                payload = self._official().candidate(
                    body["entity_uid"],
                    source_id=body["source_id"],
                    expected_version=body["expected_version"],
                    manual_rows=body.get("manual_rows"),
                )
            else:
                operation = body.get("operation")
                official = body.get("source_scope") == "official"
                required = {"entity_uid", "expected_version", "operation", "note"}
                if operation == "correct":
                    required.add("rows")
                if official:
                    required.update({"source_scope", "source_id"})
                if operation not in {"approve", "reject", "correct"} or set(body) != required:
                    raise _invalid_error("official" if official else "workspace")
                kwargs = {
                    "entity_uid": body["entity_uid"],
                    "expected_version": body["expected_version"],
                    "operation": operation,
                    "note": body["note"],
                    "rows": body.get("rows"),
                }
                if official:
                    payload = self._official().review(
                        **kwargs,
                        source_id=body["source_id"],
                    )
                else:
                    payload = self.service.review(**kwargs)
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
            TypeError,
            TableStructureServiceError,
            OfficialTableStructureServiceError,
        ) as exc:
            error = _public_error(exc)
            handler.json_response(error.public_dict(), _error_status(error))
        except Exception:
            error = TableStructureServiceError("table_structure_service_unavailable")
            handler.json_response(error.public_dict(), _error_status(error))
        else:
            handler.json_response(payload)
        return True

    def _official(self) -> OfficialTableStructureService:
        if self.official_service is None:
            raise OfficialTableStructureServiceError(
                "official_table_structure_unavailable"
            )
        return self.official_service

    @staticmethod
    def _send_artifact(
        handler: TableStructureHTTPHandler,
        artifact: TableStructureExportArtifact,
    ) -> None:
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", artifact.content_type)
        handler.send_header(
            "Content-Disposition", f'attachment; filename="{artifact.filename}"'
        )
        handler.send_header("Content-Length", str(len(artifact.content)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.end_headers()
        handler.wfile.write(artifact.content)


def _query_scope(
    query: dict[str, list[str]], *, export: bool = False
) -> str:
    workspace = {"entity_uid", "format" if export else "include_unverified"}
    official = workspace | {"source_scope", "source_id"}
    if set(query) == workspace:
        return "workspace"
    if set(query) == official and query["source_scope"] == ["official"]:
        return "official"
    raise _invalid_error(
        "official" if query.get("source_scope") == ["official"] else "workspace"
    )


def _invalid_error(scope: str) -> Exception:
    if scope == "official":
        return OfficialTableStructureServiceError("official_table_structure_invalid")
    return TableStructureServiceError("table_structure_service_invalid")


def _public_error(exc: Exception):
    if isinstance(exc, (TableStructureServiceError, OfficialTableStructureServiceError)):
        return exc
    return TableStructureServiceError("table_structure_service_invalid")


def _error_status(
    error: TableStructureServiceError | OfficialTableStructureServiceError,
) -> HTTPStatus:
    if isinstance(error, OfficialTableStructureServiceError):
        return HTTPStatus(error.http_status)
    return {
        "table_structure_service_invalid": HTTPStatus.BAD_REQUEST,
        "table_structure_service_not_found": HTTPStatus.NOT_FOUND,
        "table_structure_service_pending": HTTPStatus.CONFLICT,
        "table_structure_service_unverified": HTTPStatus.CONFLICT,
        "table_structure_service_version_conflict": HTTPStatus.CONFLICT,
        "table_structure_service_corrupt": HTTPStatus.SERVICE_UNAVAILABLE,
        "table_structure_service_unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
    }[error.code]


__all__ = [
    "MAX_TABLE_STRUCTURE_REQUEST_BYTES",
    "TABLE_STRUCTURE_CANDIDATE_PATH",
    "TABLE_STRUCTURE_EXPORT_PATH",
    "TABLE_STRUCTURE_PATH",
    "TABLE_STRUCTURE_REVIEW_PATH",
    "TableStructureAPI",
]
