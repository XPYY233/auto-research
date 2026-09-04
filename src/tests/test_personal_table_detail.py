from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from auto_research.personal.experiment_contract import (
    ColumnMapping,
    MeasurementSeriesDraft,
    PersonalExperimentDraft,
    PersonalSourceFile,
    TabularImportPreview,
)
from auto_research.personal.private_repository import (
    PrivateExperimentRepository,
    PrivateProject,
    PrivateSample,
)
from auto_research.personal.search_source import PrivateRepositorySearchSource
from auto_research.personal.table_detail import PersonalTableDetailService, PersonalTableError
from auto_research.personal.tabular_preview import UnsafeTabularFileError, read_tabular_snapshot


class PersonalTableDetailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="personal-table-detail-")
        self.root = Path(self.temporary.name)
        self.repository = PrivateExperimentRepository(self.root / "library")
        self.repository.add_project(PrivateProject("project-1", "中子辐照"))
        self.repository.add_sample(PrivateSample("sample-1", "project-1", "W-01", "W"))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _save_table(
        self,
        *,
        run_id: str,
        name: str,
        content: bytes,
        media_type: str,
        sheet_name: str = "Sheet1",
        row_count: int = 3,
        confirmed: bool = True,
    ) -> tuple[PersonalTableDetailService, str, str, PersonalSourceFile]:
        suffix = {"text/csv": ".csv", "text/tab-separated-values": ".tsv"}.get(
            media_type, ".xlsx"
        )
        selected = self.root / f"{run_id}{suffix}"
        selected.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        source = PersonalSourceFile(
            file_id=f"file-{run_id}",
            original_name=selected.name,
            media_type=media_type,
            sha256=digest,
            size_bytes=len(content),
        )
        self.repository.register_source_file(source, selected)
        preview = TabularImportPreview(
            source_file=source,
            sheet_name=sheet_name,
            row_count=row_count,
            columns=(
                ColumnMapping(
                    "剂量",
                    "independent",
                    "number",
                    role_confirmed=True,
                    meaning="辐照剂量",
                    meaning_confirmed=True,
                    unit="dpa",
                    unit_confirmed=True,
                ),
                ColumnMapping(
                    "硬度",
                    "dependent",
                    "number",
                    role_confirmed=True,
                    meaning="纳米硬度",
                    meaning_confirmed=True,
                    unit="GPa",
                    unit_confirmed=True,
                ),
                ColumnMapping(
                    "备注",
                    "ignore",
                    "text",
                    role_confirmed=True,
                    meaning="实验备注",
                    meaning_confirmed=True,
                    unit=None,
                    unit_confirmed=True,
                ),
            ),
        )
        draft = PersonalExperimentDraft(
            draft_id=run_id,
            project_name="中子辐照",
            run_name=name,
            sample_name="W-01",
            method="纳米压痕",
            preview=preview,
            series=(MeasurementSeriesDraft(f"series-{run_id}", "硬度曲线", "剂量", "硬度"),),
            conditions={"温度": "300 K"},
            confirmation_state="draft",
        )
        saved = self.repository.save_experiment(
            draft, project_id="project-1", sample_id="sample-1"
        )
        if confirmed:
            self.repository.save_experiment(
                replace(draft, confirmation_state="confirmed"),
                project_id="project-1",
                sample_id="sample-1",
                expected_revision=saved.revision,
            )
        source_adapter = PrivateRepositorySearchSource(self.repository)
        table = next(
            (
                value
                for value in source_adapter.list_documents()
                if value.entity_type == "table" and name in value.display_title
            ),
            None,
        )
        return (
            PersonalTableDetailService(self.repository),
            source_adapter.source_id,
            table.entity_uid if table is not None else "private:table:missing",
            source,
        )

    def test_confirmed_csv_is_paginated_with_real_rows_and_path_free_metadata(self) -> None:
        long_value = "长" * 800
        service, source_id, entity_uid, _source = self._save_table(
            run_id="run-csv",
            name="真实三行数据",
            content=(f"剂量,硬度,备注\n0,3.2,中文\n1,,\n2,4.1,{long_value}\n").encode(),
            media_type="text/csv",
        )
        first = service.get_page(
            source_id=source_id, entity_uid=entity_uid, page=1, page_size=2
        ).public_dict()
        second = service.get_page(
            source_id=source_id, entity_uid=entity_uid, page=2, page_size=2
        ).public_dict()
        self.assertEqual(first["schema_version"], "personal-table-page-v1")
        self.assertEqual(first["rows"][0], {"剂量": "0", "硬度": "3.2", "备注": "中文"})
        self.assertEqual(first["rows"][1]["硬度"], "")
        self.assertEqual(first["columns"][2]["role"], "ignore")
        self.assertEqual(first["total"], 3)
        self.assertTrue(first["has_next"])
        self.assertEqual(len(second["rows"][0]["备注"]), 500)
        self.assertFalse(second["has_next"])
        self.assertEqual(first["conditions"], {"温度": "300 K"})
        self.assertEqual(first["series"][0]["name"], "硬度曲线")
        encoded = json.dumps(first, ensure_ascii=False)
        for forbidden in (str(self.root), "file_id", "run_id", "project_id", "sample_id", "sha256"):
            self.assertNotIn(forbidden, encoded)
        unsafe_page = replace(
            service.get_page(source_id=source_id, entity_uid=entity_uid),
            title="saved at /home/user/private.csv",
        )
        with self.assertRaises(PersonalTableError) as raised:
            unsafe_page.public_dict()
        self.assertEqual(raised.exception.code, "personal_table_invalid")

    def test_tsv_and_xlsx_use_the_same_bounded_snapshot_reader(self) -> None:
        tsv = "剂量\t硬度\t备注\n0\t3.2\t甲\n1\t4.0\t乙\n2\t4.1\t丙\n".encode()
        service, source_id, entity_uid, _ = self._save_table(
            run_id="run-tsv",
            name="TSV",
            content=tsv,
            media_type="text/tab-separated-values",
        )
        self.assertEqual(
            service.get_page(source_id=source_id, entity_uid=entity_uid).rows[2]["备注"], "丙"
        )

        xlsx = _xlsx_bytes()
        service, source_id, entity_uid, _ = self._save_table(
            run_id="run-xlsx",
            name="XLSX",
            content=xlsx,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            sheet_name="数据",
        )
        page = service.get_page(source_id=source_id, entity_uid=entity_uid)
        self.assertEqual(page.rows[1]["硬度"], "4.0")

    def test_wrong_identity_and_draft_are_not_visible(self) -> None:
        service, source_id, entity_uid, _ = self._save_table(
            run_id="run-visible",
            name="可见",
            content=b"\xe5\x89\x82\xe9\x87\x8f,\xe7\xa1\xac\xe5\xba\xa6,\xe5\xa4\x87\xe6\xb3\xa8\n0,3.2,a\n1,4.0,b\n2,4.1,c\n",
            media_type="text/csv",
        )
        for bad_source, bad_entity in (("private-wrong", entity_uid), (source_id, "private:table:wrong")):
            with self.assertRaisesRegex(PersonalTableError, "找不到"):
                service.get_page(source_id=bad_source, entity_uid=bad_entity)
            with self.assertRaisesRegex(PersonalTableError, "找不到"):
                service.get_series(source_id=bad_source, entity_uid=bad_entity)
        draft_service, draft_source, draft_entity, _ = self._save_table(
            run_id="run-draft",
            name="草稿",
            content=b"\xe5\x89\x82\xe9\x87\x8f,\xe7\xa1\xac\xe5\xba\xa6,\xe5\xa4\x87\xe6\xb3\xa8\n0,3.2,a\n1,4.0,b\n2,4.1,c\n",
            media_type="text/csv",
            confirmed=False,
        )
        with self.assertRaises(PersonalTableError) as raised:
            draft_service.get_page(source_id=draft_source, entity_uid=draft_entity)
        self.assertEqual(raised.exception.code, "personal_table_not_found")
        with self.assertRaises(PersonalTableError) as plot_error:
            draft_service.get_series(source_id=draft_source, entity_uid=draft_entity)
        self.assertEqual(plot_error.exception.code, "personal_table_not_found")

    def test_changed_file_and_symlink_fail_closed(self) -> None:
        service, source_id, entity_uid, source = self._save_table(
            run_id="run-change",
            name="变化",
            content="剂量,硬度,备注\n0,3.2,a\n1,4.0,b\n2,4.1,c\n".encode(),
            media_type="text/csv",
        )
        stored = self.repository.private_path_for_file(source.file_id)
        stored.write_bytes(b"x" * source.size_bytes)
        os.utime(stored, ns=(stored.stat().st_atime_ns, stored.stat().st_mtime_ns))
        with self.assertRaises(PersonalTableError) as raised:
            service.get_page(source_id=source_id, entity_uid=entity_uid)
        self.assertEqual(raised.exception.code, "personal_table_changed")

    def test_change_during_same_descriptor_read_is_detected(self) -> None:
        service, source_id, entity_uid, source = self._save_table(
            run_id="run-race",
            name="读取竞态",
            content="剂量,硬度,备注\n0,3.2,a\n1,4.0,b\n2,4.1,c\n".encode(),
            media_type="text/csv",
        )
        stored = self.repository.private_path_for_file(source.file_id)
        real_read = os.read
        changed = False

        def mutate_after_read(fd: int, size: int) -> bytes:
            nonlocal changed
            data = real_read(fd, size)
            if data and not changed:
                changed = True
                stored.write_bytes(b"z" * source.size_bytes)
            return data

        with patch("auto_research.personal.table_detail.os.read", side_effect=mutate_after_read):
            with self.assertRaises(PersonalTableError) as raised:
                service.get_page(source_id=source_id, entity_uid=entity_uid)
        self.assertEqual(raised.exception.code, "personal_table_changed")

        stored.unlink()
        target = self.root / "target.csv"
        target.write_bytes(b"safe")
        stored.symlink_to(target)
        with self.assertRaises(PersonalTableError) as raised:
            service.get_page(source_id=source_id, entity_uid=entity_uid)
        self.assertEqual(raised.exception.code, "personal_table_changed")

    def test_xlsx_casefold_conflicts_and_xml_entities_are_rejected(self) -> None:
        with self.assertRaises(UnsafeTabularFileError):
            read_tabular_snapshot(
                _xlsx_bytes(extra={"XL/WORKBOOK.XML": b"duplicate"}),
                original_name="unsafe.xlsx",
            )
        with self.assertRaises(UnsafeTabularFileError):
            read_tabular_snapshot(
                _xlsx_bytes(sheet_prefix='<!DOCTYPE x [<!ENTITY leak "unsafe">]>'),
                original_name="unsafe.xlsx",
            )


def _xlsx_bytes(
    *,
    extra: dict[str, bytes] | None = None,
    sheet_prefix: str = "",
) -> bytes:
    import io

    output = io.BytesIO()
    workbook = """<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <sheets><sheet name="数据" sheetId="1" r:id="rId1"/></sheets></workbook>"""
    rels = """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
    </Relationships>"""
    rows = [
        ("剂量", "硬度", "备注"),
        ("0", "3.2", "甲"),
        ("1", "4.0", "乙"),
        ("2", "4.1", "丙"),
    ]
    body = "".join(
        f'<row r="{row_index}">'
        + "".join(
            f'<c r="{chr(65 + column_index)}{row_index}" t="inlineStr"><is><t>{value}</t></is></c>'
            for column_index, value in enumerate(row)
        )
        + "</row>"
        for row_index, row in enumerate(rows, start=1)
    )
    sheet = sheet_prefix + (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{body}</sheetData></worksheet>"
    )
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    return output.getvalue()


if __name__ == "__main__":
    unittest.main()
