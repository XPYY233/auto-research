"""Export only human-verified two-dimensional table structures."""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from xml.sax.saxutils import escape as xml_escape

from .table_structure import TableStructureLimits, _normalized_cell
from .spreadsheet_safety import spreadsheet_safe_cell


FORMATS = frozenset({"csv", "xlsx"})
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
_FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}")


class TableStructureExportError(ValueError):
    _MESSAGES = {
        "table_structure_export_invalid": "表格结构导出请求无效。",
        "table_structure_export_unverified": "表格结构尚未通过人工核验，不能导出。",
        "table_structure_export_too_large": "表格结构导出结果超过安全上限。",
    }

    def __init__(self, code: str) -> None:
        if code not in self._MESSAGES:
            code = "table_structure_export_invalid"
        self.code = code
        self.safe_message = self._MESSAGES[code]
        super().__init__(self.safe_message)

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": "table-structure-export-error-v1",
            "code": self.code,
            "message": self.safe_message,
            "retryable": False,
        }


@dataclass(frozen=True)
class TableStructureExportArtifact:
    content: bytes
    content_type: str
    filename: str

    def __post_init__(self) -> None:
        if not self.content or len(self.content) > MAX_ARTIFACT_BYTES:
            raise TableStructureExportError("table_structure_export_too_large")
        if self.content_type not in {
            "text/csv; charset=utf-8",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }:
            raise TableStructureExportError("table_structure_export_invalid")
        if not re.fullmatch(r"verified-table-(workspace|official)\.(csv|xlsx)", self.filename):
            raise TableStructureExportError("table_structure_export_invalid")


def export_verified_table_structure(
    structure: Mapping[str, Any], *, format: str
) -> TableStructureExportArtifact:
    """Create a literal grid export without inferring headers or numeric values."""

    if format not in FORMATS:
        raise TableStructureExportError("table_structure_export_invalid")
    rows, scope = _verified_rows(structure)
    safe_rows = tuple(
        tuple(str(spreadsheet_safe_cell(value)) for value in row) for row in rows
    )
    if format == "csv":
        output = io.StringIO(newline="")
        csv.writer(output, lineterminator="\r\n").writerows(safe_rows)
        content = ("\ufeff" + output.getvalue()).encode("utf-8")
        content_type = "text/csv; charset=utf-8"
    else:
        content = _grid_xlsx(safe_rows)
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return TableStructureExportArtifact(
        content=content,
        content_type=content_type,
        filename=f"verified-table-{scope}.{format}",
    )


def _verified_rows(structure: Mapping[str, Any]) -> tuple[tuple[tuple[str, ...], ...], str]:
    if not isinstance(structure, Mapping):
        raise TableStructureExportError("table_structure_export_invalid")
    required = {
        "schema_version", "source_scope", "source_id", "entity_uid", "entity_type",
        "version", "status", "reason_codes", "rows", "cells", "content_fingerprint",
        "reviewed_at",
    }
    if set(structure) != required or structure.get("schema_version") != "table-structure-version-v1":
        raise TableStructureExportError("table_structure_export_invalid")
    if structure.get("status") != "verified":
        raise TableStructureExportError("table_structure_export_unverified")
    scope = structure.get("source_scope")
    if scope not in {"workspace", "official"} or structure.get("entity_type") != "table":
        raise TableStructureExportError("table_structure_export_invalid")
    if type(structure.get("version")) is not int or int(structure["version"]) < 1:
        raise TableStructureExportError("table_structure_export_invalid")
    if not isinstance(structure.get("reviewed_at"), str) or not structure["reviewed_at"]:
        raise TableStructureExportError("table_structure_export_invalid")
    if not isinstance(structure.get("reason_codes"), list):
        raise TableStructureExportError("table_structure_export_invalid")
    fingerprint = structure.get("content_fingerprint")
    if not isinstance(fingerprint, str) or not _FINGERPRINT_RE.fullmatch(fingerprint):
        raise TableStructureExportError("table_structure_export_invalid")
    raw_rows = structure.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise TableStructureExportError("table_structure_export_invalid")
    limits = TableStructureLimits()
    columns = len(raw_rows[0]) if isinstance(raw_rows[0], list) else 0
    if not columns or len(raw_rows) > limits.max_rows or columns > limits.max_columns:
        raise TableStructureExportError("table_structure_export_invalid")
    if len(raw_rows) * columns > limits.max_cells:
        raise TableStructureExportError("table_structure_export_invalid")
    rows: list[tuple[str, ...]] = []
    total = 0
    try:
        for raw in raw_rows:
            if not isinstance(raw, list) or len(raw) != columns:
                raise TableStructureExportError("table_structure_export_invalid")
            row = tuple(_normalized_cell(value, limits) for value in raw)
            total += sum(len(value) for value in row)
            rows.append(row)
    except Exception as exc:
        if isinstance(exc, TableStructureExportError):
            raise
        raise TableStructureExportError("table_structure_export_invalid") from exc
    if total > limits.max_total_chars:
        raise TableStructureExportError("table_structure_export_invalid")
    return tuple(rows), str(scope)


def _column_name(index: int) -> str:
    label = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        label = chr(65 + remainder) + label
    return label


def _grid_xlsx(rows: Sequence[Sequence[str]]) -> bytes:
    xml_rows: list[str] = []
    for row_number, row in enumerate(rows, start=1):
        cells = []
        for column_number, value in enumerate(row, start=1):
            ref = f"{_column_name(column_number)}{row_number}"
            cells.append(
                f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">'
                f"{xml_escape(value)}</t></is></c>"
            )
        xml_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    dimension = f"A1:{_column_name(len(rows[0]))}{len(rows)}"
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="{dimension}"/><sheetData>{"".join(xml_rows)}</sheetData></worksheet>'
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '</Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>',
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="verified table" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '</Relationships>',
        )
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
    content = output.getvalue()
    if len(content) > MAX_ARTIFACT_BYTES:
        raise TableStructureExportError("table_structure_export_too_large")
    return content


__all__ = [
    "TableStructureExportArtifact",
    "TableStructureExportError",
    "export_verified_table_structure",
]
