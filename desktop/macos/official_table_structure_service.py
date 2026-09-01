"""macOS coordinator for immutable official table-structure review."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol, Sequence

from auto_research.evidence.official_table_structure_review import (
    OfficialTableSource,
    OfficialTableStructureReviewError,
    OfficialTableStructureReviewService,
    OfficialTableStructureReviewStore,
    official_table_structure_content_fingerprint,
)
from auto_research.evidence.table_structure import TableStructureCell, _validated_bbox
from auto_research.evidence.table_structure_export import (
    TableStructureExportArtifact,
    TableStructureExportError,
    export_verified_table_structure,
)


_PAPER_UID_RE = re.compile(r"^paper_[0-9a-f]{32}$")
_ENTITY_UID_RE = re.compile(r"^entity_table_[0-9a-f]{32}$")
_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PackageImportAuthority(Protocol):
    def active_repository(self) -> tuple[object, object] | None: ...


class OfficialTableStructureServiceError(ValueError):
    """Stable renderer-safe coordinator failure."""

    _MESSAGES = {
        "official_table_structure_invalid": "官方表格结构请求无效。",
        "official_table_structure_not_found": "未找到对应的官方表格结构。",
        "official_table_structure_pending": "官方表格结构仍待人工核验。",
        "official_table_structure_version_conflict": "表格结构版本已变化，请刷新后重试。",
        "official_table_structure_corrupt": "官方表格结构记录损坏，已停止处理。",
        "official_table_structure_unavailable": "官方表格结构服务暂时不可用。",
        "official_table_structure_source_changed": "当前官方资料包已变化，请重新打开表格。",
        "official_table_structure_pdf_changed": "官方论文原文校验失败。",
    }

    def __init__(self, code: str) -> None:
        if code not in self._MESSAGES:
            code = "official_table_structure_unavailable"
        self.code = code
        self.safe_message = self._MESSAGES[code]
        self.http_status = {
            "official_table_structure_invalid": 400,
            "official_table_structure_not_found": 404,
            "official_table_structure_pending": 409,
            "official_table_structure_version_conflict": 409,
            "official_table_structure_corrupt": 409,
            "official_table_structure_source_changed": 409,
            "official_table_structure_pdf_changed": 409,
            "official_table_structure_unavailable": 503,
        }[code]
        super().__init__(self.safe_message)

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": "official-table-structure-service-error-v1",
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.code
            in {
                "official_table_structure_version_conflict",
                "official_table_structure_source_changed",
                "official_table_structure_pdf_changed",
                "official_table_structure_unavailable",
            },
        }


@dataclass(frozen=True)
class _ResolvedSource:
    repository: object
    source: OfficialTableSource


class OfficialTableStructureService:
    """Coordinate active-package identity, local review and safe export."""

    def __init__(
        self,
        package_service: PackageImportAuthority,
        review_store: OfficialTableStructureReviewStore,
        *,
        review_service: OfficialTableStructureReviewService | None = None,
    ) -> None:
        if not callable(getattr(package_service, "active_repository", None)):
            raise OfficialTableStructureServiceError(
                "official_table_structure_invalid"
            )
        self._packages = package_service
        self._review = review_service or OfficialTableStructureReviewService(
            review_store
        )

    def get(
        self,
        entity_uid: str,
        *,
        source_id: str,
        include_unverified: bool = False,
    ) -> dict[str, object]:
        try:
            resolved = self._resolve(entity_uid, source_id)
            try:
                packaged = resolved.repository.get_table_structure(entity_uid)
            except KeyError:
                packaged = None
            if packaged is not None:
                return self._verified_package_structure(packaged, resolved.source)
            value = self._review.get(
                entity_uid,
                include_unverified=include_unverified,
            )
            self._assert_local_fresh(value, resolved.source)
            return value
        except Exception as exc:
            raise _service_error(exc) from None

    def candidate(
        self,
        entity_uid: str,
        *,
        source_id: str,
        expected_version: int = 0,
        manual_rows: Sequence[Sequence[str]] | None = None,
        table_bbox: Sequence[float] | None = None,
    ) -> dict[str, object]:
        try:
            resolved = self._resolve(entity_uid, source_id)
            try:
                resolved.repository.get_table_structure(entity_uid)
            except KeyError:
                pass
            else:
                raise OfficialTableStructureServiceError(
                    "official_table_structure_version_conflict"
                )
            source = resolved.source
            if table_bbox is not None:
                try:
                    supplied_bbox = _validated_bbox(table_bbox)
                except Exception as exc:
                    raise OfficialTableStructureServiceError(
                        "official_table_structure_invalid"
                    ) from exc
                if source.table_bbox is not None and supplied_bbox != source.table_bbox:
                    raise OfficialTableStructureServiceError(
                        "official_table_structure_invalid"
                    )
                if source.table_bbox is None:
                    source = replace(source, table_bbox=supplied_bbox)
            # A transcription without an audited source region cannot ever be
            # approved safely.  The renderer is deliberately unable to supply
            # a bbox; only package evidence or a maintainer-side audited input
            # may establish this immutable locator before any candidate write.
            if source.table_bbox is None:
                raise OfficialTableStructureServiceError(
                    "official_table_structure_invalid"
                )
            lease = resolved.repository.open_pdf(source.paper_uid)
            if lease is None:
                raise OfficialTableStructureServiceError(
                    "official_table_structure_pdf_changed"
                )
            try:
                value = self._review.candidate(
                    source=source,
                    pdf_lease=lease,
                    expected_version=expected_version,
                    manual_rows=manual_rows,
                )
            finally:
                close = getattr(lease, "close", None)
                if callable(close):
                    close()
            self._assert_local_fresh(value, resolved.source)
            return value
        except Exception as exc:
            raise _service_error(exc) from None

    def review(
        self,
        entity_uid: str,
        *,
        source_id: str,
        expected_version: int,
        operation: str,
        rows: Sequence[Sequence[str]] | None = None,
        note: str = "",
    ) -> dict[str, object]:
        try:
            resolved = self._resolve(entity_uid, source_id)
            try:
                resolved.repository.get_table_structure(entity_uid)
            except KeyError:
                pass
            else:
                raise OfficialTableStructureServiceError(
                    "official_table_structure_version_conflict"
                )
            current = self._review.get(entity_uid, include_unverified=True)
            self._assert_local_fresh(current, resolved.source)
            value = self._review.review(
                entity_uid,
                expected_version=expected_version,
                operation=operation,
                rows=rows,
                note=note,
            )
            self._assert_local_fresh(value, resolved.source)
            return value
        except Exception as exc:
            raise _service_error(exc) from None

    def export(
        self,
        entity_uid: str,
        *,
        source_id: str,
        format: str,
    ) -> TableStructureExportArtifact:
        try:
            value = self.get(
                entity_uid,
                source_id=source_id,
                include_unverified=False,
            )
            return export_verified_table_structure(
                _export_projection(value),
                format=format,
            )
        except Exception as exc:
            raise _service_error(exc) from None

    def _resolve(self, entity_uid: str, source_id: str) -> _ResolvedSource:
        if (
            type(entity_uid) is not str
            or not _ENTITY_UID_RE.fullmatch(entity_uid)
            or type(source_id) is not str
            or not _SOURCE_ID_RE.fullmatch(source_id)
        ):
            raise OfficialTableStructureServiceError(
                "official_table_structure_invalid"
            )
        active_pair = self._packages.active_repository()
        if active_pair is None:
            raise OfficialTableStructureServiceError(
                "official_table_structure_unavailable"
            )
        active, repository = active_pair
        if (
            getattr(active, "package_id", None) != source_id
            or getattr(repository, "package_id", None) != source_id
        ):
            raise OfficialTableStructureServiceError(
                "official_table_structure_source_changed"
            )
        try:
            entity = repository.get_entity(entity_uid)
        except KeyError as exc:
            raise OfficialTableStructureServiceError(
                "official_table_structure_not_found"
            ) from exc
        if (
            not isinstance(entity, Mapping)
            or entity.get("source_scope") != "official"
            or entity.get("source_id") != source_id
            or entity.get("entity_type") != "table"
            or entity.get("entity_uid") != entity_uid
        ):
            raise OfficialTableStructureServiceError(
                "official_table_structure_source_changed"
            )
        paper_uid = str(entity.get("paper_uid") or "")
        if not _PAPER_UID_RE.fullmatch(paper_uid):
            raise OfficialTableStructureServiceError(
                "official_table_structure_corrupt"
            )
        page = _entity_page(entity)
        bbox = _entity_bbox(entity)
        try:
            pdf_identity = repository.get_pdf_identity(paper_uid)
        except KeyError as exc:
            raise OfficialTableStructureServiceError(
                "official_table_structure_pdf_changed"
            ) from exc
        if (
            not isinstance(pdf_identity, Mapping)
            or pdf_identity.get("schema_version") != "official-pdf-identity-v1"
            or pdf_identity.get("paper_uid") != paper_uid
            or not _SHA256_RE.fullmatch(str(pdf_identity.get("sha256") or ""))
            or pdf_identity.get("media_type") != "application/pdf"
            or type(pdf_identity.get("size_bytes")) is not int
            or int(pdf_identity["size_bytes"]) <= 0
        ):
            raise OfficialTableStructureServiceError(
                "official_table_structure_pdf_changed"
            )
        return _ResolvedSource(
            repository,
            OfficialTableSource(
                source_id,
                paper_uid,
                entity_uid,
                str(pdf_identity["sha256"]),
                page,
                bbox,
            ),
        )

    @staticmethod
    def _verified_package_structure(
        value: object,
        source: OfficialTableSource,
    ) -> dict[str, object]:
        fields = {
            "schema_version",
            "source_scope",
            "source_id",
            "entity_uid",
            "entity_type",
            "version",
            "status",
            "page",
            "bbox",
            "reason_codes",
            "rows",
            "cells",
            "content_fingerprint",
            "reviewed_at",
        }
        if (
            not isinstance(value, Mapping)
            or set(value) != fields
            or value.get("schema_version") != "official-table-structure-version-v1"
            or value.get("source_scope") != "official"
            or value.get("source_id") != source.source_id
            or value.get("entity_uid") != source.entity_uid
            or value.get("entity_type") != "table"
            or value.get("status") != "verified"
            or value.get("page") != source.page
        ):
            raise OfficialTableStructureServiceError(
                "official_table_structure_source_changed"
            )
        if source.table_bbox is not None and tuple(value.get("bbox") or ()) != tuple(
            source.table_bbox
        ):
            raise OfficialTableStructureServiceError(
                "official_table_structure_source_changed"
            )
        return {key: value[key] for key in fields}

    @staticmethod
    def _assert_local_fresh(
        value: Mapping[str, Any],
        current: OfficialTableSource,
    ) -> None:
        try:
            if (
                value.get("source_scope") != "official"
                or value.get("source_id") != current.source_id
                or value.get("entity_uid") != current.entity_uid
                or value.get("entity_type") != "table"
                or value.get("page") != current.page
            ):
                raise ValueError
            bbox = tuple(value["bbox"]) if value.get("bbox") is not None else None
            if current.table_bbox is not None and bbox != current.table_bbox:
                raise ValueError
            source = OfficialTableSource(
                current.source_id,
                current.paper_uid,
                current.entity_uid,
                current.source_pdf_sha256,
                current.page,
                bbox,
            )
            rows = tuple(tuple(str(cell) for cell in row) for row in value["rows"])
            reasons = tuple(str(item) for item in value["reason_codes"])
            cells = tuple(_cell(item) for item in value["cells"])
            expected = official_table_structure_content_fingerprint(
                source,
                reasons,
                rows,
                cells,
            )
        except Exception as exc:
            raise OfficialTableStructureServiceError(
                "official_table_structure_corrupt"
            ) from exc
        if value.get("content_fingerprint") != expected:
            raise OfficialTableStructureServiceError(
                "official_table_structure_source_changed"
            )


def _entity_page(entity: Mapping[str, Any]) -> int:
    values: set[int] = set()
    for key in ("page", "page_start", "source_page"):
        value = entity.get(key)
        if value is None:
            continue
        if type(value) is not int or value <= 0:
            raise OfficialTableStructureServiceError(
                "official_table_structure_corrupt"
            )
        values.add(value)
    if len(values) != 1 or next(iter(values)) <= 0:
        raise OfficialTableStructureServiceError("official_table_structure_corrupt")
    return next(iter(values))


def _entity_bbox(
    entity: Mapping[str, Any],
) -> tuple[float, float, float, float] | None:
    values = [entity[key] for key in ("table_bbox", "bbox") if entity.get(key) is not None]
    if not values:
        return None
    try:
        normalized = [_validated_bbox(value) for value in values]
    except Exception as exc:
        raise OfficialTableStructureServiceError(
            "official_table_structure_corrupt"
        ) from exc
    if any(value != normalized[0] for value in normalized[1:]):
        raise OfficialTableStructureServiceError(
            "official_table_structure_corrupt"
        )
    return normalized[0]


def _cell(value: object) -> TableStructureCell:
    if not isinstance(value, Mapping) or set(value) != {
        "row_index",
        "column_index",
        "raw_text",
        "bbox",
    }:
        raise ValueError
    bbox = tuple(value["bbox"]) if value["bbox"] is not None else None
    return TableStructureCell(
        int(value["row_index"]),
        int(value["column_index"]),
        str(value["raw_text"]),
        bbox,
    )


def _export_projection(value: Mapping[str, Any]) -> dict[str, object]:
    required = {
        "source_scope",
        "source_id",
        "entity_uid",
        "entity_type",
        "version",
        "status",
        "reason_codes",
        "rows",
        "cells",
        "content_fingerprint",
        "reviewed_at",
    }
    if not required.issubset(value):
        raise OfficialTableStructureServiceError("official_table_structure_corrupt")
    return {
        "schema_version": "table-structure-version-v1",
        **{key: value[key] for key in required - {"source_scope"}},
        "source_scope": "official",
    }


def _service_error(exc: Exception) -> OfficialTableStructureServiceError:
    if isinstance(exc, OfficialTableStructureServiceError):
        return exc
    if isinstance(exc, OfficialTableStructureReviewError):
        suffix = exc.code.removeprefix("official_table_review_")
        mapped = {
            "invalid": "invalid",
            "not_found": "not_found",
            "pending": "pending",
            "version_conflict": "version_conflict",
            "corrupt": "corrupt",
            "unavailable": "unavailable",
            "pdf_changed": "pdf_changed",
        }.get(suffix, "unavailable")
        return OfficialTableStructureServiceError(
            f"official_table_structure_{mapped}"
        )
    if isinstance(exc, TableStructureExportError):
        if exc.code == "table_structure_export_unverified":
            return OfficialTableStructureServiceError(
                "official_table_structure_pending"
            )
        return OfficialTableStructureServiceError(
            "official_table_structure_invalid"
        )
    if isinstance(exc, KeyError):
        return OfficialTableStructureServiceError(
            "official_table_structure_not_found"
        )
    return OfficialTableStructureServiceError(
        "official_table_structure_unavailable"
    )


__all__ = [
    "OfficialTableStructureService",
    "OfficialTableStructureServiceError",
]
