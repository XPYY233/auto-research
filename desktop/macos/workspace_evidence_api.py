from __future__ import annotations

from http import HTTPStatus
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from auto_research.evidence.workspace_evidence_resolver import (
    WorkspaceEvidenceResolverError,
)


WORKSPACE_EVIDENCE_PATH = "/api/desktop/workspace-evidence"
WORKSPACE_EVIDENCE_IMAGE_PATH = f"{WORKSPACE_EVIDENCE_PATH}/image"
WORKSPACE_EVIDENCE_PDF_PATH = f"{WORKSPACE_EVIDENCE_PATH}/pdf"
_QUERY_FIELDS = frozenset(
    {"source_scope", "source_id", "entity_type", "entity_uid"}
)
_STREAM_BYTES = 1024 * 1024


class WorkspaceEvidenceHTTPHandler(Protocol):
    path: str
    wfile: Any

    def json_response(
        self, payload: Any, status: HTTPStatus = HTTPStatus.OK
    ) -> None: ...

    def send_response(self, status: HTTPStatus) -> None: ...

    def send_header(self, name: str, value: str) -> None: ...

    def end_headers(self) -> None: ...


class WorkspaceEvidenceResolver(Protocol):
    def get(self, **request: object) -> dict[str, Any]: ...

    def open_image(self, **request: object) -> Any: ...

    def open_pdf(self, **request: object) -> Any: ...


class WorkspaceEvidenceAPI:
    """Thin authenticated desktop adapter over the shared opaque resolver."""

    def __init__(self, resolver: WorkspaceEvidenceResolver) -> None:
        if not all(
            callable(getattr(resolver, name, None))
            for name in ("get", "open_image", "open_pdf")
        ):
            raise TypeError("workspace evidence resolver is invalid")
        self.resolver = resolver

    def handle_get(self, handler: WorkspaceEvidenceHTTPHandler) -> bool:
        parsed = urlparse(handler.path)
        if parsed.path not in {
            WORKSPACE_EVIDENCE_PATH,
            WORKSPACE_EVIDENCE_IMAGE_PATH,
            WORKSPACE_EVIDENCE_PDF_PATH,
        }:
            return False
        try:
            request = _query(parsed.query)
            if parsed.path == WORKSPACE_EVIDENCE_PATH:
                handler.json_response(self.resolver.get(**request))
            else:
                lease = (
                    self.resolver.open_image(**request)
                    if parsed.path == WORKSPACE_EVIDENCE_IMAGE_PATH
                    else self.resolver.open_pdf(**request)
                )
                _serve_binary(handler, lease)
        except WorkspaceEvidenceResolverError as exc:
            handler.json_response(exc.public_dict(), HTTPStatus(exc.http_status))
        except Exception:
            error = WorkspaceEvidenceResolverError("workspace_evidence_unavailable")
            handler.json_response(error.public_dict(), HTTPStatus(error.http_status))
        return True


def _query(raw_query: str) -> dict[str, str]:
    try:
        query = parse_qs(
            raw_query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=len(_QUERY_FIELDS),
        )
    except ValueError:
        raise WorkspaceEvidenceResolverError("workspace_evidence_invalid") from None
    if set(query) != _QUERY_FIELDS or any(
        len(values) != 1 or not values[0] for values in query.values()
    ):
        raise WorkspaceEvidenceResolverError("workspace_evidence_invalid")
    return {key: query[key][0] for key in sorted(_QUERY_FIELDS)}


def _serve_binary(handler: WorkspaceEvidenceHTTPHandler, lease: Any) -> None:
    try:
        metadata = lease.public_metadata()
        size = int(metadata["size_bytes"])
        media_type = str(metadata["media_type"])
        if size < 5 or media_type not in {
            "application/pdf",
            "image/png",
            "image/jpeg",
        }:
            raise ValueError("workspace evidence lease is invalid")
        extension = {
            "application/pdf": "pdf",
            "image/png": "png",
            "image/jpeg": "jpg",
        }[media_type]
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", media_type)
        handler.send_header("Content-Length", str(size))
        handler.send_header(
            "Content-Disposition", f'inline; filename="workspace-evidence.{extension}"'
        )
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.end_headers()
        with lease:
            while True:
                block = lease.read(_STREAM_BYTES)
                if not block:
                    break
                handler.wfile.write(block)
    except (KeyError, TypeError, ValueError):
        try:
            lease.close()
        except Exception:
            pass
        raise WorkspaceEvidenceResolverError("workspace_evidence_changed") from None


__all__ = [
    "WORKSPACE_EVIDENCE_IMAGE_PATH",
    "WORKSPACE_EVIDENCE_PATH",
    "WORKSPACE_EVIDENCE_PDF_PATH",
    "WorkspaceEvidenceAPI",
]
