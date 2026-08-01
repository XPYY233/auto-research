from __future__ import annotations

import csv
import hashlib
import io
import posixpath
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from .experiment_contract import ColumnMapping, PersonalSourceFile, TabularImportPreview


_XML_FORBIDDEN = (b"<!DOCTYPE", b"<!ENTITY")
_CELL_REF_RE = re.compile(r"^([A-Z]+)", re.I)
_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?$")
_UNIT_HEADER_RE = re.compile(r"^\s*(.*?)\s*(?:\[([^\]]{1,40})\]|\(([^()]{1,40})\))\s*$")

_IDENTIFIER_WORDS = ("id", "编号", "样品号", "样品编号", "批次号", "run id")
_UNCERTAINTY_WORDS = ("error", "uncertainty", "std", "stdev", "sem", "误差", "标准差")
_CONDITION_WORDS = (
    "temperature", "temp", "温度", "dose", "dpa", "fluence", "flux", "剂量",
    "注量", "通量", "time", "duration", "时间", "pressure", "压力", "frequency", "频率",
)
_INDEPENDENT_WORDS = ("strain", "应变", "displacement", "位移", "depth", "深度", "angle", "角度")
_NOTE_WORDS = ("note", "comment", "remark", "备注", "说明")

_MEDIA_TYPES = {
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class UnsafeTabularFileError(ValueError):
    """The selected file cannot be safely previewed under the bounded contract."""


@dataclass(frozen=True)
class PreviewLimits:
    max_file_bytes: int = 20 * 1024 * 1024
    max_zip_entries: int = 1_000
    max_uncompressed_bytes: int = 80 * 1024 * 1024
    max_xml_entry_bytes: int = 16 * 1024 * 1024
    max_sheets: int = 20
    max_scan_rows: int = 100_000
    max_columns: int = 256
    max_sample_rows: int = 5
    max_cell_chars: int = 500

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if int(value) <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True)
class TabularFilePreview:
    source_file: PersonalSourceFile
    detected_format: str
    sheets: tuple[TabularImportPreview, ...]
    warnings: tuple[str, ...] = ()
    formula_cell_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "personal-tabular-file-preview-v1",
            "source_file": self.source_file.as_public_dict(),
            "detected_format": self.detected_format,
            "sheets": [sheet.as_dict() for sheet in self.sheets],
            "warnings": list(self.warnings),
            "formula_cell_count": int(self.formula_cell_count),
        }


def preview_tabular_file(
    path: str | Path,
    *,
    limits: PreviewLimits | None = None,
) -> TabularFilePreview:
    """Create a bounded preview without evaluating formulas or writing data."""

    limits = limits or PreviewLimits()
    source_path = Path(path)
    extension = source_path.suffix.casefold()
    if extension in {".xls", ".xlsm", ".xltm", ".xlam"}:
        raise UnsafeTabularFileError("legacy or macro-enabled Excel files are not supported")
    if extension not in _MEDIA_TYPES:
        raise UnsafeTabularFileError("only CSV, TSV, and XLSX files are supported")
    try:
        size = source_path.stat().st_size
    except OSError as exc:
        raise UnsafeTabularFileError("selected file cannot be read") from exc
    if size > limits.max_file_bytes:
        raise UnsafeTabularFileError("selected file exceeds the preview size limit")
    try:
        raw = source_path.read_bytes()
    except OSError as exc:
        raise UnsafeTabularFileError("selected file cannot be read") from exc
    if len(raw) > limits.max_file_bytes:
        raise UnsafeTabularFileError("selected file exceeds the preview size limit")
    digest = hashlib.sha256(raw).hexdigest()
    source = PersonalSourceFile(
        file_id=f"personal-file-{digest[:24]}",
        original_name=source_path.name,
        media_type=_MEDIA_TYPES[extension],
        sha256=digest,
        size_bytes=len(raw),
    )
    if extension in {".csv", ".tsv"}:
        return _preview_delimited(raw, source, extension, limits)
    return _preview_xlsx(raw, source, limits)


def _decode_delimited(raw: bytes) -> tuple[str, str]:
    if b"\x00" in raw:
        raise UnsafeTabularFileError("text table contains binary null bytes")
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise UnsafeTabularFileError("text table encoding is not supported")


def _preview_delimited(
    raw: bytes,
    source: PersonalSourceFile,
    extension: str,
    limits: PreviewLimits,
) -> TabularFilePreview:
    text, encoding = _decode_delimited(raw)
    warnings: list[str] = []
    if encoding == "gb18030":
        warnings.append("decoded_as_gb18030")
    delimiter = "\t" if extension == ".tsv" else _detect_delimiter(text)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        rows, truncated, columns_truncated = _bounded_rows(reader, limits)
    except csv.Error as exc:
        raise UnsafeTabularFileError("text table structure exceeds safe CSV limits") from exc
    if truncated:
        warnings.append("row_scan_truncated")
    if columns_truncated:
        warnings.append("column_scan_truncated")
    if any(_formula_like_text(value) for row in rows for value in row):
        warnings.append("formula_like_text_preserved")
    sheet = _rows_to_sheet(source, "Sheet1", rows, limits)
    return TabularFilePreview(
        source_file=source,
        detected_format=extension[1:],
        sheets=(sheet,),
        warnings=tuple(warnings),
    )


def _detect_delimiter(text: str) -> str:
    sample = text[:16_384]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;").delimiter
    except csv.Error:
        return ","


def _formula_like_text(value: str) -> bool:
    cleaned = str(value).lstrip()
    if cleaned.startswith(("=", "@")):
        return True
    return cleaned.startswith(("+", "-")) and not bool(
        _NUMBER_RE.fullmatch(cleaned.replace(",", ""))
    )


def _bounded_rows(
    reader: Iterable[list[str]],
    limits: PreviewLimits,
) -> tuple[list[list[str]], bool, bool]:
    rows: list[list[str]] = []
    truncated = False
    columns_truncated = False
    for index, row in enumerate(reader):
        if index >= limits.max_scan_rows + 1:
            truncated = True
            break
        if len(row) > limits.max_columns:
            columns_truncated = True
        bounded = [str(value)[: limits.max_cell_chars] for value in row[: limits.max_columns]]
        rows.append(bounded)
    return rows, truncated, columns_truncated


def _safe_xml(data: bytes) -> ET.Element:
    upper = data.upper()
    if any(marker in upper for marker in _XML_FORBIDDEN):
        raise UnsafeTabularFileError("spreadsheet XML contains a forbidden document type or entity")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise UnsafeTabularFileError("spreadsheet XML is malformed") from exc


def _read_zip_entry(archive: zipfile.ZipFile, name: str, limits: PreviewLimits) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise UnsafeTabularFileError(f"required XLSX part is missing: {name}") from exc
    if info.flag_bits & 0x1:
        raise UnsafeTabularFileError("encrypted XLSX entries are not supported")
    if info.file_size > limits.max_xml_entry_bytes:
        raise UnsafeTabularFileError("an XLSX XML part exceeds the preview limit")
    data = archive.read(info)
    if len(data) != info.file_size:
        raise UnsafeTabularFileError("an XLSX entry could not be read completely")
    return data


def _validate_xlsx_archive(archive: zipfile.ZipFile, limits: PreviewLimits) -> None:
    infos = archive.infolist()
    if len(infos) > limits.max_zip_entries:
        raise UnsafeTabularFileError("XLSX contains too many ZIP entries")
    if sum(info.file_size for info in infos) > limits.max_uncompressed_bytes:
        raise UnsafeTabularFileError("XLSX uncompressed content exceeds the preview limit")
    names = {info.filename for info in infos}
    if len(names) != len(infos):
        raise UnsafeTabularFileError("XLSX contains duplicate ZIP entry names")
    if any(info.flag_bits & 0x1 for info in infos):
        raise UnsafeTabularFileError("encrypted XLSX files are not supported")
    if "xl/vbaProject.bin" in names or any(name.casefold().endswith("vbaproject.bin") for name in names):
        raise UnsafeTabularFileError("macro-enabled workbooks are not supported")
    if any(name.startswith("xl/externalLinks/") for name in names):
        raise UnsafeTabularFileError("workbooks with external links are not supported")


def _preview_xlsx(
    raw: bytes,
    source: PersonalSourceFile,
    limits: PreviewLimits,
) -> TabularFilePreview:
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise UnsafeTabularFileError("XLSX is not a valid ZIP-based workbook") from exc
    with archive:
        _validate_xlsx_archive(archive, limits)
        workbook = _safe_xml(_read_zip_entry(archive, "xl/workbook.xml", limits))
        relationships = _workbook_relationships(archive, limits)
        shared_strings = _shared_strings(archive, limits)
        sheets = _workbook_sheets(workbook, relationships)
        warnings: list[str] = []
        if len(sheets) > limits.max_sheets:
            warnings.append("sheet_count_truncated")
            sheets = sheets[: limits.max_sheets]
        previews: list[TabularImportPreview] = []
        formula_count = 0
        for sheet_name, target, hidden in sheets:
            xml = _read_zip_entry(archive, target, limits)
            rows, sheet_formulas, sheet_warnings = _xlsx_rows(xml, shared_strings, limits)
            previews.append(_rows_to_sheet(source, sheet_name, rows, limits))
            formula_count += sheet_formulas
            warnings.extend(sheet_warnings)
            if hidden:
                warnings.append(f"hidden_sheet_included:{sheet_name}")
        if formula_count:
            warnings.append("formula_cells_not_evaluated")
        return TabularFilePreview(
            source_file=source,
            detected_format="xlsx",
            sheets=tuple(previews),
            warnings=tuple(dict.fromkeys(warnings)),
            formula_cell_count=formula_count,
        )


def _workbook_relationships(
    archive: zipfile.ZipFile,
    limits: PreviewLimits,
) -> dict[str, tuple[str, str]]:
    root = _safe_xml(_read_zip_entry(archive, "xl/_rels/workbook.xml.rels", limits))
    output: dict[str, tuple[str, str]] = {}
    for rel in root:
        rel_id = rel.attrib.get("Id", "")
        target = rel.attrib.get("Target", "")
        rel_type = rel.attrib.get("Type", "")
        target_mode = rel.attrib.get("TargetMode", "").casefold()
        if target_mode == "external" or rel_type.casefold().endswith("/externallink"):
            raise UnsafeTabularFileError("workbooks with external relationships are not supported")
        if not rel_id or not target:
            continue
        if target.startswith("/"):
            normalized = posixpath.normpath(target.lstrip("/"))
        else:
            normalized = posixpath.normpath(posixpath.join("xl", target))
        if not normalized.startswith("xl/") or normalized.startswith("xl/../"):
            raise UnsafeTabularFileError("XLSX relationship escapes the workbook directory")
        output[rel_id] = (normalized, rel_type)
    return output


def _workbook_sheets(
    workbook: ET.Element,
    relationships: dict[str, tuple[str, str]],
) -> list[tuple[str, str, bool]]:
    output: list[tuple[str, str, bool]] = []
    for element in workbook.iter():
        if not element.tag.endswith("}sheet"):
            continue
        name = element.attrib.get("name", "Sheet")
        rel_id = next((value for key, value in element.attrib.items() if key.endswith("}id")), "")
        target, rel_type = relationships.get(rel_id, ("", ""))
        if not target or not rel_type.casefold().endswith("/worksheet"):
            continue
        hidden = element.attrib.get("state", "visible").casefold() != "visible"
        output.append((name, target, hidden))
    if not output:
        raise UnsafeTabularFileError("XLSX contains no readable worksheets")
    return output


def _shared_strings(archive: zipfile.ZipFile, limits: PreviewLimits) -> tuple[str, ...]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return ()
    root = _safe_xml(_read_zip_entry(archive, "xl/sharedStrings.xml", limits))
    values: list[str] = []
    for item in root:
        if not item.tag.endswith("}si"):
            continue
        text = "".join((node.text or "") for node in item.iter() if node.tag.endswith("}t"))
        values.append(text[: limits.max_cell_chars])
        if len(values) > limits.max_scan_rows * limits.max_columns:
            raise UnsafeTabularFileError("XLSX shared-string table exceeds the preview limit")
    return tuple(values)


def _column_index(cell_reference: str) -> int | None:
    match = _CELL_REF_RE.match(cell_reference)
    if not match:
        return None
    value = 0
    for char in match.group(1).upper():
        value = value * 26 + ord(char) - 64
    return value - 1


def _xlsx_rows(
    xml: bytes,
    shared_strings: tuple[str, ...],
    limits: PreviewLimits,
) -> tuple[list[list[str]], int, list[str]]:
    upper = xml.upper()
    if any(marker in upper for marker in _XML_FORBIDDEN):
        raise UnsafeTabularFileError("worksheet XML contains a forbidden document type or entity")
    rows: list[list[str]] = []
    formula_count = 0
    warnings: list[str] = []
    try:
        iterator = ET.iterparse(io.BytesIO(xml), events=("end",))
        for _, element in iterator:
            if not element.tag.endswith("}row"):
                continue
            if len(rows) >= limits.max_scan_rows + 1:
                warnings.append("row_scan_truncated")
                element.clear()
                break
            values: dict[int, str] = {}
            fallback_index = 0
            for cell in element:
                if not cell.tag.endswith("}c"):
                    continue
                index = _column_index(cell.attrib.get("r", ""))
                if index is None:
                    index = fallback_index
                fallback_index = index + 1
                if index >= limits.max_columns:
                    warnings.append("column_scan_truncated")
                    continue
                formula = next((node for node in cell if node.tag.endswith("}f")), None)
                value_node = next((node for node in cell if node.tag.endswith("}v")), None)
                inline_nodes = [node for node in cell.iter() if node.tag.endswith("}t")]
                cell_type = cell.attrib.get("t", "")
                raw_value = value_node.text if value_node is not None and value_node.text is not None else ""
                if formula is not None:
                    formula_count += 1
                    value = raw_value or "[公式，未计算]"
                elif cell_type == "s":
                    try:
                        value = shared_strings[int(raw_value)]
                    except (ValueError, IndexError):
                        value = ""
                        warnings.append("invalid_shared_string_reference")
                elif cell_type == "inlineStr":
                    value = "".join(node.text or "" for node in inline_nodes)
                elif cell_type == "b":
                    value = "TRUE" if raw_value == "1" else "FALSE"
                elif cell_type == "e":
                    value = raw_value or "[Excel错误]"
                    warnings.append("excel_error_cell_present")
                else:
                    value = raw_value
                values[index] = str(value)[: limits.max_cell_chars]
            if values:
                width = min(max(values) + 1, limits.max_columns)
                rows.append([values.get(index, "") for index in range(width)])
            else:
                rows.append([])
            element.clear()
    except ET.ParseError as exc:
        raise UnsafeTabularFileError("worksheet XML is malformed") from exc
    if b"<mergeCells" in xml or b":mergeCells" in xml:
        warnings.append("merged_cells_not_expanded")
    return rows, formula_count, warnings


def _rows_to_sheet(
    source: PersonalSourceFile,
    sheet_name: str,
    rows: list[list[str]],
    limits: PreviewLimits,
) -> TabularImportPreview:
    nonempty = [row for row in rows if any(str(value).strip() for value in row)]
    if not nonempty:
        return TabularImportPreview(source, sheet_name, 0, ())
    header = nonempty[0][: limits.max_columns]
    data_rows = nonempty[1:]
    width = min(max([len(header), *(len(row) for row in data_rows)]), limits.max_columns)
    names = _unique_headers(header, width)
    columns: list[ColumnMapping] = []
    for index, name in enumerate(names):
        samples = tuple(
            str(row[index]).strip()[: limits.max_cell_chars]
            for row in data_rows
            if index < len(row) and str(row[index]).strip()
        )[: limits.max_sample_rows]
        meaning, unit = _header_suggestions(name)
        data_type = _infer_data_type(samples)
        columns.append(
            ColumnMapping(
                source_name=name,
                role=_suggest_role(meaning, data_type),
                data_type=data_type,
                meaning=meaning,
                meaning_confirmed=False,
                unit=unit,
                unit_confirmed=False,
                sample_values=samples,
            )
        )
    return TabularImportPreview(
        source_file=source,
        sheet_name=sheet_name,
        row_count=len(data_rows),
        columns=tuple(columns),
    )


def _unique_headers(header: list[str], width: int) -> list[str]:
    output: list[str] = []
    counts: dict[str, int] = {}
    for index in range(width):
        base = " ".join(str(header[index] if index < len(header) else "").split())
        base = base or f"Column {index + 1}"
        key = base.casefold()
        counts[key] = counts.get(key, 0) + 1
        output.append(base if counts[key] == 1 else f"{base} ({counts[key]})")
    return output


def _header_suggestions(header: str) -> tuple[str, str | None]:
    match = _UNIT_HEADER_RE.match(header)
    if not match:
        return header, None
    meaning = " ".join(match.group(1).split()) or header
    unit = next((value for value in match.groups()[1:] if value), None)
    return meaning, unit.strip() if unit else None


def _infer_data_type(samples: tuple[str, ...]) -> str:
    if not samples:
        return "unknown"
    lowered = [value.casefold() for value in samples]
    if all(value in {"true", "false", "yes", "no", "是", "否"} for value in lowered):
        return "boolean"
    if all(_NUMBER_RE.fullmatch(value.replace(",", "")) for value in samples):
        return "number"
    if all(re.fullmatch(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T].*)?", value) for value in samples):
        return "datetime"
    return "text"


def _suggest_role(meaning: str, data_type: str) -> str:
    lowered = meaning.casefold()
    if any(word in lowered for word in _IDENTIFIER_WORDS):
        return "identifier"
    if any(word in lowered for word in _UNCERTAINTY_WORDS):
        return "uncertainty"
    if any(word in lowered for word in _NOTE_WORDS):
        return "note"
    if any(word in lowered for word in _CONDITION_WORDS):
        return "condition"
    if any(word in lowered for word in _INDEPENDENT_WORDS):
        return "independent"
    return "dependent" if data_type == "number" else "note"
