from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from auto_research.personal.tabular_preview import (
    PreviewLimits,
    UnsafeTabularFileError,
    preview_tabular_file,
)


CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
</Types>"""

WORKBOOK = """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Measurements" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""

WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>"""

SHARED_STRINGS = """<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="4" uniqueCount="4">
  <si><t>Dose (dpa)</t></si><si><t>Hardness [GPa]</t></si>
  <si><t>0</t></si><si><t>3.2</t></si>
</sst>"""

SHEET = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>
    <row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2" t="s"><v>3</v></c></row>
    <row r="3"><c r="A3"><v>1</v></c><c r="B3"><f>2+2</f><v>4.0</v></c></row>
  </sheetData>
</worksheet>"""


def write_xlsx(
    path: Path,
    *,
    sheet_xml: str = SHEET,
    relationships: str = WORKBOOK_RELS,
    extra_entries: dict[str, bytes] | None = None,
) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("xl/workbook.xml", WORKBOOK)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/sharedStrings.xml", SHARED_STRINGS)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        for name, value in (extra_entries or {}).items():
            archive.writestr(name, value)


class PersonalTabularPreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_csv_preview_suggests_meaning_unit_and_role_but_requires_confirmation(self):
        path = self.root / "measurements.csv"
        path.write_text(
            "Dose (dpa),Hardness [GPa],Sample ID,Note\n"
            "0,3.2,W-01,before irradiation\n"
            "1,4.0,W-01,after irradiation\n",
            encoding="utf-8",
        )
        preview = preview_tabular_file(path)
        sheet = preview.sheets[0]
        columns = {column.source_name: column for column in sheet.columns}
        self.assertEqual(sheet.row_count, 2)
        self.assertEqual(columns["Dose (dpa)"].meaning, "Dose")
        self.assertEqual(columns["Dose (dpa)"].unit, "dpa")
        self.assertEqual(columns["Dose (dpa)"].role, "condition")
        self.assertFalse(columns["Dose (dpa)"].meaning_confirmed)
        self.assertFalse(columns["Dose (dpa)"].unit_confirmed)
        self.assertTrue(columns["Dose (dpa)"].needs_user_confirmation)
        self.assertEqual(columns["Sample ID"].role, "identifier")

    def test_csv_formula_like_text_is_preserved_and_never_executed(self):
        path = self.root / "notes.csv"
        path.write_text("name,note\nsample-1,=HYPERLINK(\"https://example.invalid\")\n", encoding="utf-8")
        preview = preview_tabular_file(path)
        self.assertIn("formula_like_text_preserved", preview.warnings)
        self.assertEqual(
            preview.sheets[0].columns[1].sample_values,
            ('=HYPERLINK("https://example.invalid")',),
        )

    def test_negative_measurement_is_not_misreported_as_formula_text(self):
        path = self.root / "negative.csv"
        path.write_text("energy (eV)\n-7.27\n", encoding="utf-8")
        preview = preview_tabular_file(path)
        self.assertNotIn("formula_like_text_preserved", preview.warnings)
        self.assertEqual(preview.sheets[0].columns[0].data_type, "number")

    def test_gb18030_and_tsv_are_read_without_external_dependencies(self):
        path = self.root / "测量.tsv"
        path.write_bytes("温度 (°C)\t硬度 (GPa)\n300\t4.2\n".encode("gb18030"))
        preview = preview_tabular_file(path)
        self.assertEqual(preview.detected_format, "tsv")
        self.assertIn("decoded_as_gb18030", preview.warnings)
        self.assertEqual(preview.sheets[0].columns[0].unit, "°C")

    def test_duplicate_and_blank_headers_get_stable_unique_names(self):
        path = self.root / "duplicate.csv"
        path.write_text("value,value,\n1,2,3\n", encoding="utf-8")
        columns = preview_tabular_file(path).sheets[0].columns
        self.assertEqual([column.source_name for column in columns], ["value", "value (2)", "Column 3"])

    def test_row_scan_is_bounded_and_reported(self):
        path = self.root / "large.csv"
        path.write_text("x,y\n1,2\n3,4\n5,6\n", encoding="utf-8")
        preview = preview_tabular_file(path, limits=PreviewLimits(max_scan_rows=2))
        self.assertEqual(preview.sheets[0].row_count, 2)
        self.assertIn("row_scan_truncated", preview.warnings)

    def test_xlsx_shared_strings_and_cached_formula_are_previewed_without_evaluation(self):
        path = self.root / "measurements.xlsx"
        write_xlsx(path)
        preview = preview_tabular_file(path)
        self.assertEqual(preview.detected_format, "xlsx")
        self.assertEqual(preview.formula_cell_count, 1)
        self.assertIn("formula_cells_not_evaluated", preview.warnings)
        self.assertEqual(preview.sheets[0].row_count, 2)
        hardness = preview.sheets[0].columns[1]
        self.assertEqual(hardness.meaning, "Hardness")
        self.assertEqual(hardness.unit, "GPa")
        self.assertEqual(hardness.sample_values, ("3.2", "4.0"))

    def test_preview_payload_never_contains_the_local_parent_path(self):
        path = self.root / "private.csv"
        path.write_text("x\n1\n", encoding="utf-8")
        encoded = json.dumps(preview_tabular_file(path).as_dict(), ensure_ascii=False)
        self.assertNotIn(str(self.root), encoded)
        self.assertIn("private.csv", encoded)

    def test_legacy_macro_external_and_xml_entity_inputs_are_rejected(self):
        old = self.root / "legacy.xls"
        old.write_bytes(b"old excel")
        with self.assertRaises(UnsafeTabularFileError):
            preview_tabular_file(old)

        macro = self.root / "macro.xlsx"
        write_xlsx(macro, extra_entries={"xl/vbaProject.bin": b"macro"})
        with self.assertRaises(UnsafeTabularFileError):
            preview_tabular_file(macro)

        external = self.root / "external.xlsx"
        external_rels = WORKBOOK_RELS.replace(
            "</Relationships>",
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/externalLink" Target="https://example.invalid" TargetMode="External"/></Relationships>',
        )
        write_xlsx(external, relationships=external_rels)
        with self.assertRaises(UnsafeTabularFileError):
            preview_tabular_file(external)

        entity = self.root / "entity.xlsx"
        unsafe_sheet = '<!DOCTYPE x [<!ENTITY boom "unsafe">]>' + SHEET
        write_xlsx(entity, sheet_xml=unsafe_sheet)
        with self.assertRaises(UnsafeTabularFileError):
            preview_tabular_file(entity)

    def test_file_and_zip_limits_fail_closed(self):
        path = self.root / "small.csv"
        path.write_text("x\n1\n", encoding="utf-8")
        with self.assertRaises(UnsafeTabularFileError):
            preview_tabular_file(path, limits=PreviewLimits(max_file_bytes=2))

        workbook = self.root / "entries.xlsx"
        write_xlsx(workbook)
        with self.assertRaises(UnsafeTabularFileError):
            preview_tabular_file(workbook, limits=PreviewLimits(max_zip_entries=2))


if __name__ == "__main__":
    unittest.main()
