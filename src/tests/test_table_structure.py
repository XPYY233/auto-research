from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace

import fitz
import pytest

from auto_research.evidence import table_structure as subject


IDENTITY = subject.PublicTableIdentity(
    source_scope="official",
    source_id="official-package-v2",
    entity_uid="table:paper-001:t1",
)


def _table_pdf(*, second: bool = False) -> bytes:
    document = fitz.open()
    page = document.new_page(width=600, height=800)

    def draw_table(x0: float, y0: float, values: list[list[str]]) -> None:
        widths = (120, 160)
        row_height = 42
        xs = (x0, x0 + widths[0], x0 + sum(widths))
        ys = tuple(y0 + row_height * index for index in range(len(values) + 1))
        for x in xs:
            page.draw_line((x, ys[0]), (x, ys[-1]))
        for y in ys:
            page.draw_line((xs[0], y), (xs[-1], y))
        for row_index, row in enumerate(values):
            for column_index, value in enumerate(row):
                if value:
                    page.insert_text(
                        (xs[column_index] + 8, ys[row_index] + 27),
                        value,
                        fontsize=10,
                        fontname="china-s" if any(ord(char) > 127 for char in value) else "helv",
                    )

    draw_table(72, 100, [["样品 A", "10  MPa"], ["B", ""]])
    if second:
        draw_table(72, 300, [["C", "20"], ["D", "30"]])
    payload = document.tobytes(garbage=4, deflate=True)
    document.close()
    return payload


def _extract(pdf: bytes | None = None, **kwargs: object) -> subject.TableStructureCandidate:
    return subject.extract_table_structure_candidate(
        pdf or _table_pdf(),
        page=1,
        table_bbox=kwargs.pop("table_bbox", (70, 98, 354, 186)),
        identity=kwargs.pop("identity", IDENTITY),
        **kwargs,
    )


def test_regular_table_preserves_strings_empty_cells_and_never_verifies() -> None:
    candidate = _extract()
    public = candidate.public_dict()

    assert public["schema_version"] == "table-structure-candidate-v1"
    assert public["status"] in {"candidate", "manual_review"}
    assert public["status"] != "verified"
    assert public["entity_type"] == "table"
    assert public["rows"][0][0] == "样品 A"
    # PyMuPDF owns PDF text tokenization; this module preserves its returned value.
    assert public["rows"][0][1] == "10 MPa"
    assert public["rows"][1][1] == ""
    assert public["row_count"] == 2
    assert public["column_count"] == 2
    assert len(public["cells"]) == 4
    assert "value_num" not in json.dumps(public, ensure_ascii=False)
    assert "item" not in public


def test_content_fingerprint_is_deterministic() -> None:
    pdf = _table_pdf()
    first = _extract(pdf).public_dict()
    second = _extract(pdf).public_dict()
    assert first == second
    assert len(first["content_fingerprint"]) == 64


def test_multiple_matching_tables_fail_closed() -> None:
    with pytest.raises(subject.TableStructureError) as caught:
        _extract(_table_pdf(second=True), table_bbox=(65, 90, 360, 390))
    assert caught.value.code == "table_structure_ambiguous"


@dataclass
class _FakeRow:
    cells: list[tuple[float, float, float, float] | None]


class _FakeTable:
    def __init__(
        self,
        values: list[list[str | None]],
        *,
        bbox: tuple[float, float, float, float] = (70, 98, 354, 186),
        cell_boxes: list[list[tuple[float, float, float, float] | None]] | None = None,
    ) -> None:
        self.bbox = bbox
        self.row_count = len(values)
        self.col_count = len(values[0]) if values else 0
        self._values = values
        if cell_boxes is None:
            cell_boxes = []
            height = (bbox[3] - bbox[1]) / max(1, self.row_count)
            width = (bbox[2] - bbox[0]) / max(1, self.col_count)
            for row in range(self.row_count):
                cell_boxes.append(
                    [
                        (
                            bbox[0] + column * width,
                            bbox[1] + row * height,
                            bbox[0] + (column + 1) * width,
                            bbox[1] + (row + 1) * height,
                        )
                        for column in range(self.col_count)
                    ]
                )
        self.rows = [_FakeRow(row) for row in cell_boxes]
        self.header = SimpleNamespace(external=False)

    def extract(self) -> list[list[str | None]]:
        return self._values


def _patch_tables(monkeypatch: pytest.MonkeyPatch, *tables: _FakeTable) -> None:
    monkeypatch.setattr(subject, "_find_tables", lambda _page: tuple(tables))


def test_parser_preserves_whitespace_returned_by_pymupdf(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_tables(monkeypatch, _FakeTable([["中文 名称", "  10  MPa  "]]))
    public = _extract().public_dict()
    assert public["rows"] == [["中文 名称", "  10  MPa  "]]
    assert public["cells"][1]["raw_text"] == "  10  MPa  "


def test_merged_or_missing_geometry_requires_manual_review(monkeypatch: pytest.MonkeyPatch) -> None:
    table = _FakeTable(
        [["Header", "Value"], ["A", "1"]],
        cell_boxes=[
            [(70, 98, 212, 142), None],
            [(70, 142, 212, 186), (212, 142, 354, 186)],
        ],
    )
    _patch_tables(monkeypatch, table)
    public = _extract().public_dict()
    assert public["status"] == "manual_review"
    assert "merged_or_missing_cell_geometry" in public["reason_codes"]


def test_possible_cross_page_table_requires_manual_review(monkeypatch: pytest.MonkeyPatch) -> None:
    table = _FakeTable([["A", "B"]], bbox=(70, 754, 354, 799))
    _patch_tables(monkeypatch, table)
    public = _extract(table_bbox=(70, 750, 354, 800)).public_dict()
    assert public["status"] == "manual_review"
    assert "possible_cross_page_table" in public["reason_codes"]


def test_bbox_outside_page_fails_closed() -> None:
    with pytest.raises(subject.TableStructureError) as caught:
        _extract(table_bbox=(70, 98, 700, 186))
    assert caught.value.code == "table_structure_geometry_conflict"


def test_empty_table_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_tables(monkeypatch, _FakeTable([["", None]]))
    with pytest.raises(subject.TableStructureError) as caught:
        _extract()
    assert caught.value.code == "table_structure_not_found"


@pytest.mark.parametrize(
    "limits",
    [
        subject.TableStructureLimits(max_rows=1),
        subject.TableStructureLimits(max_columns=1),
        subject.TableStructureLimits(max_cells=3),
        subject.TableStructureLimits(max_cell_chars=3),
        subject.TableStructureLimits(max_total_chars=5),
    ],
)
def test_all_structural_and_text_limits_fail_closed(
    monkeypatch: pytest.MonkeyPatch, limits: subject.TableStructureLimits
) -> None:
    _patch_tables(monkeypatch, _FakeTable([["Header", "Value"], ["A", "1234"]]))
    with pytest.raises(subject.TableStructureError) as caught:
        _extract(limits=limits)
    assert caught.value.code == "table_structure_limit_exceeded"


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "saved at /Users/name/a.pdf",
        "db=sqlite:/tmp/a.db",
        "api_key=secret",
    ],
)
def test_private_content_is_rejected_without_echo(
    monkeypatch: pytest.MonkeyPatch, unsafe_text: str
) -> None:
    _patch_tables(monkeypatch, _FakeTable([["Header", unsafe_text]]))
    with pytest.raises(subject.TableStructureError) as caught:
        _extract()
    public = caught.value.public_dict()
    assert caught.value.code == "table_structure_unsafe_content"
    assert unsafe_text not in json.dumps(public, ensure_ascii=False)


def test_public_candidate_has_no_private_field_names(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_tables(monkeypatch, _FakeTable([["Header", "Value"], ["A", "1"]]))
    encoded = json.dumps(_extract().public_dict(), ensure_ascii=False, sort_keys=True)
    for forbidden in (
        '"path"',
        '"file_id"',
        '"paper_id"',
        '"candidate_id"',
        '"api_key"',
        '"credential_ref"',
        '"session_id"',
        '"pdf_bytes"',
    ):
        assert forbidden not in encoded


def test_invalid_or_unparseable_geometry_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    table = _FakeTable([["A", "B"]])
    table.col_count = 3
    _patch_tables(monkeypatch, table)
    with pytest.raises(subject.TableStructureError) as caught:
        _extract()
    assert caught.value.code == "table_structure_geometry_conflict"


def test_identity_rejects_paths_and_private_scope() -> None:
    with pytest.raises(subject.TableStructureError):
        subject.PublicTableIdentity("official", "file:/tmp/a", "table:1")
    with pytest.raises(subject.TableStructureError):
        subject.PublicTableIdentity("private", "private-source", "table:1")
