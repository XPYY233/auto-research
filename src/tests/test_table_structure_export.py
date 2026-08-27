from __future__ import annotations

import csv
import io
import zipfile

import pytest

from auto_research.evidence.table_structure_export import (
    TableStructureExportError,
    export_verified_table_structure,
)


def _structure(*, status: str = "verified", rows: list[list[str]] | None = None) -> dict:
    values = rows or [["Sample", "Value"], ["W", "300"], ["Ta", "=2+2"]]
    return {
        "schema_version": "table-structure-version-v1",
        "source_scope": "workspace",
        "source_id": "workspace",
        "entity_uid": "7",
        "entity_type": "table",
        "version": 2,
        "status": status,
        "reason_codes": [],
        "rows": values,
        "cells": [],
        "content_fingerprint": "a" * 64,
        "reviewed_at": "2026-08-27T12:00:00+00:00" if status == "verified" else None,
    }


def test_csv_is_literal_grid_and_neutralizes_formulas() -> None:
    artifact = export_verified_table_structure(_structure(), format="csv")
    rows = list(csv.reader(io.StringIO(artifact.content.decode("utf-8-sig"))))
    assert rows == [["Sample", "Value"], ["W", "300"], ["Ta", "'=2+2"]]
    assert artifact.filename == "verified-table-workspace.csv"
    assert artifact.content_type == "text/csv; charset=utf-8"


def test_xlsx_preserves_grid_without_inventing_numeric_cells() -> None:
    artifact = export_verified_table_structure(
        _structure(rows=[["中文 表头", " 10 MPa "], ["A&B", "<raw>"]]),
        format="xlsx",
    )
    with zipfile.ZipFile(io.BytesIO(artifact.content)) as archive:
        xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert 'dimension ref="A1:B2"' in xml
    assert "中文 表头" in xml
    assert " 10 MPa " in xml
    assert "A&amp;B" in xml and "&lt;raw&gt;" in xml
    assert 't="inlineStr"' in xml
    assert "<f>" not in xml


@pytest.mark.parametrize("status", ["candidate", "manual_review", "rejected"])
def test_unverified_structure_never_exports(status: str) -> None:
    with pytest.raises(TableStructureExportError) as caught:
        export_verified_table_structure(_structure(status=status), format="csv")
    assert caught.value.code == "table_structure_export_unverified"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(schema_version="unknown"),
        lambda value: value.update(rows=[["A"], ["B", "C"]]),
        lambda value: value.update(content_fingerprint="bad"),
        lambda value: value.update(source_scope="private"),
        lambda value: value.update(rows=[["saved at /Users/name/a.pdf"]]),
    ],
)
def test_malformed_or_private_structure_fails_closed(mutation) -> None:
    value = _structure()
    mutation(value)
    with pytest.raises(TableStructureExportError) as caught:
        export_verified_table_structure(value, format="csv")
    assert caught.value.code == "table_structure_export_invalid"


def test_public_error_does_not_echo_content() -> None:
    value = _structure(rows=[["api_key=secret"]])
    with pytest.raises(TableStructureExportError) as caught:
        export_verified_table_structure(value, format="csv")
    assert "secret" not in str(caught.value.public_dict())
