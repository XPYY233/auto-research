from __future__ import annotations

from http import HTTPStatus
from typing import Any, Mapping, Protocol
from urllib.parse import parse_qs, urlparse

from auto_research.evidence.evidence_export import (
    EvidenceExportArtifact,
    EvidenceExportError,
    EvidenceExportService,
    FederatedEvidenceResolver,
)


EVIDENCE_EXPORT_PATH = "/api/desktop/evidence-export"
_QUERY_FIELDS = frozenset(
    {"source_scope", "source_id", "entity_type", "entity_uid", "format"}
)


class EvidenceExportHTTPHandler(Protocol):
    path: str
    wfile: Any

    def json_response(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None: ...
    def send_response(self, status: HTTPStatus) -> None: ...
    def send_header(self, name: str, value: str) -> None: ...
    def end_headers(self) -> None: ...


class _UnavailableWorkspaceResolver:
    """Fail closed until the shared workspace controller is injected."""

    def get(self, **_identity: Any) -> Mapping[str, Any]:
        raise EvidenceExportError("evidence_export_unavailable")


def windows_evidence_export_service(federated_session: Any) -> EvidenceExportService:
    return EvidenceExportService(
        workspace_resolver=_UnavailableWorkspaceResolver(),
        federated_resolver=FederatedEvidenceResolver(federated_session),
    )


class WindowsEvidenceExportBridge:
    """Thin attachment response over the shared single-evidence exporter."""

    def __init__(self, service: EvidenceExportService) -> None:
        self.service = service

    def handle_get(self, handler: EvidenceExportHTTPHandler) -> bool:
        parsed = urlparse(handler.path)
        if parsed.path != EVIDENCE_EXPORT_PATH:
            return False
        try:
            query = parse_qs(
                parsed.query,
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=len(_QUERY_FIELDS),
            )
            if set(query) != _QUERY_FIELDS or any(
                len(values) != 1 or values[0] == "" for values in query.values()
            ):
                raise EvidenceExportError("evidence_export_invalid")
            artifact = self.service.export(
                source_scope=query["source_scope"][0],
                source_id=query["source_id"][0],
                entity_type=query["entity_type"][0],
                entity_uid=query["entity_uid"][0],
                format=query["format"][0],
            )
        except (ValueError, EvidenceExportError) as exc:
            error = exc if isinstance(exc, EvidenceExportError) else EvidenceExportError(
                "evidence_export_invalid"
            )
            handler.json_response(error.public_dict(), _error_status(error))
        except Exception:
            error = EvidenceExportError("evidence_export_unavailable")
            handler.json_response(error.public_dict(), _error_status(error))
        else:
            self._send_artifact(handler, artifact)
        return True

    @staticmethod
    def _send_artifact(
        handler: EvidenceExportHTTPHandler,
        artifact: EvidenceExportArtifact,
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


def _error_status(error: EvidenceExportError) -> HTTPStatus:
    return {
        "evidence_export_invalid": HTTPStatus.BAD_REQUEST,
        "evidence_export_not_found": HTTPStatus.NOT_FOUND,
        "evidence_export_unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
    }[error.code]


__all__ = [
    "WindowsEvidenceExportBridge",
    "windows_evidence_export_service",
]
