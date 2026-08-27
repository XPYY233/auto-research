from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path

import pytest

from auto_research.evidence import table_structure as contract
from auto_research.evidence.db import EvidenceDB, now
from auto_research.evidence.table_structure_service import (
    TableStructureServiceError,
    WorkspaceTableStructureService,
)
from auto_research.evidence.table_structure_store import TableStructureStore


def _candidate(
    entity_uid: str,
    *,
    status: str = "candidate",
    reasons: tuple[str, ...] = (),
) -> contract.TableStructureCandidate:
    identity = contract.PublicTableIdentity("workspace", "workspace", entity_uid)
    rows = (("Sample", "Value"), ("W", "300"))
    cells = (
        contract.TableStructureCell(0, 0, "Sample", (10.0, 10.0, 50.0, 30.0)),
        contract.TableStructureCell(0, 1, "Value", (50.0, 10.0, 90.0, 30.0)),
        contract.TableStructureCell(1, 0, "W", (10.0, 30.0, 50.0, 50.0)),
        contract.TableStructureCell(1, 1, "300", (50.0, 30.0, 90.0, 50.0)),
    )
    bbox = (8.0, 8.0, 92.0, 52.0)
    fingerprint = contract._content_fingerprint(
        identity=identity,
        page=2,
        bbox=bbox,
        status=status,
        reason_codes=reasons,
        rows=rows,
        cells=cells,
    )
    return contract.TableStructureCandidate(
        identity=identity,
        page=2,
        bbox=bbox,
        status=status,
        reason_codes=reasons,
        rows=rows,
        cells=cells,
        content_fingerprint=fingerprint,
    )


def _setup(
    tmp_path: Path,
    *,
    asset_type: str = "table",
    save_candidate: bool = True,
) -> tuple[EvidenceDB, WorkspaceTableStructureService, str]:
    database = EvidenceDB(tmp_path / "evidence.sqlite")
    database.init()
    paper_id = database.upsert_paper(
        title="Table service paper",
        doi="10.1000/table-service",
        authenticity_status="verified_pdf",
    )
    with database.connect() as connection:
        inserted = connection.execute(
            """INSERT INTO visual_assets(
               paper_id,asset_type,label,asset_number,caption,page_start,page_end,bbox_json,
               image_path,image_sha256,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                paper_id,
                asset_type,
                "Table 1" if asset_type == "table" else "Figure 1",
                1,
                "Measured values",
                2,
                2,
                "[8,8,92,52]",
                "/temporary-test/asset.png",
                "test-hash",
                now(),
                now(),
            ),
        )
        asset_id = int(inserted.lastrowid)
    entity_uid = str(asset_id)
    if save_candidate and asset_type == "table":
        TableStructureStore(database).save_candidate(
            visual_asset_id=asset_id,
            candidate=_candidate(entity_uid),
            expected_version=0,
        )
    return database, WorkspaceTableStructureService(database), entity_uid


def _assert_safe(value: object) -> None:
    serialized = json.dumps(value, ensure_ascii=False).casefold()
    for forbidden in (
        "visual_asset_id",
        "asset_id",
        "paper_id",
        "reviewer",
        '"note"',
        "image_path",
        "/temporary-test/",
        "api_key",
        "credential",
    ):
        assert forbidden not in serialized


def test_pending_is_hidden_by_default_but_visible_for_review(tmp_path: Path) -> None:
    _database, service, entity_uid = _setup(tmp_path)
    with pytest.raises(TableStructureServiceError) as pending:
        service.get(entity_uid)
    assert pending.value.code == "table_structure_service_pending"
    visible = service.get(entity_uid, include_unverified=True)
    assert visible["status"] == "candidate"
    assert visible["entity_uid"] == entity_uid
    _assert_safe(visible)


def test_approve_exposes_verified_version_without_review_metadata(tmp_path: Path) -> None:
    _database, service, entity_uid = _setup(tmp_path)
    approved = service.review(
        entity_uid,
        expected_version=1,
        operation="approve",
        note="checked against the source",
    )
    assert approved["version"] == 2
    assert approved["status"] == "verified"
    assert service.get(entity_uid) == approved
    _assert_safe(approved)


def test_correct_changes_only_grid_text_and_synchronizes_cells(tmp_path: Path) -> None:
    database, service, entity_uid = _setup(tmp_path)
    before = service.get(entity_uid, include_unverified=True)
    corrected = service.review(
        entity_uid,
        expected_version=1,
        operation="correct",
        rows=[["Sample", "Value"], ["W", "=2+2"]],
    )
    assert corrected["status"] == "verified"
    assert corrected["rows"] == [["Sample", "Value"], ["W", "=2+2"]]
    assert [cell["raw_text"] for cell in corrected["cells"]] == [
        "Sample",
        "Value",
        "W",
        "=2+2",
    ]
    assert [cell["bbox"] for cell in corrected["cells"]] == [
        cell["bbox"] for cell in before["cells"]
    ]
    assert corrected["reason_codes"] == before["reason_codes"]
    assert corrected["content_fingerprint"] != before["content_fingerprint"]
    with database.connect() as connection:
        stored = [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT candidate_json FROM table_structure_versions ORDER BY version_no"
            )
        ]
    assert stored[1]["page"] == stored[0]["page"]
    assert stored[1]["bbox"] == stored[0]["bbox"]
    assert stored[1]["source_scope"] == stored[0]["source_scope"]
    assert stored[1]["source_id"] == stored[0]["source_id"]
    assert stored[1]["entity_uid"] == stored[0]["entity_uid"]
    assert [cell["bbox"] for cell in stored[1]["cells"]] == [
        cell["bbox"] for cell in stored[0]["cells"]
    ]


@pytest.mark.parametrize(
    "rows",
    (
        [["one column"], ["W"]],
        [["Sample", "Value"]],
        [["Sample", "Value"], ["W", 300]],
        [["Sample", "Value"], ["W", "/Users/name/result.csv"]],
    ),
)
def test_correct_rejects_shape_type_and_private_content(tmp_path: Path, rows: object) -> None:
    _database, service, entity_uid = _setup(tmp_path)
    with pytest.raises(TableStructureServiceError) as invalid:
        service.review(
            entity_uid,
            expected_version=1,
            operation="correct",
            rows=rows,
        )
    assert invalid.value.code == "table_structure_service_invalid"
    assert service.get(entity_uid, include_unverified=True)["version"] == 1


def test_reject_is_append_only_and_hides_default_read(tmp_path: Path) -> None:
    _database, service, entity_uid = _setup(tmp_path)
    rejected = service.review(entity_uid, 1, "reject")
    assert rejected["version"] == 2
    assert rejected["status"] == "rejected"
    with pytest.raises(TableStructureServiceError) as missing:
        service.get(entity_uid)
    assert missing.value.code == "table_structure_service_not_found"
    assert service.get(entity_uid, include_unverified=True) == rejected


def test_stale_expected_version_preserves_cas_conflict(tmp_path: Path) -> None:
    _database, service, entity_uid = _setup(tmp_path)
    service.review(entity_uid, 1, "approve")
    with pytest.raises(TableStructureServiceError) as conflict:
        service.review(entity_uid, 1, "reject")
    assert conflict.value.code == "table_structure_service_version_conflict"
    assert conflict.value.public_dict()["retryable"] is True


def test_csv_and_xlsx_export_only_verified_literal_rows(tmp_path: Path) -> None:
    _database, service, entity_uid = _setup(tmp_path)
    with pytest.raises(TableStructureServiceError) as unverified:
        service.export(entity_uid, "csv")
    assert unverified.value.code == "table_structure_service_unverified"
    service.review(
        entity_uid,
        1,
        "correct",
        rows=[["Sample", "Value"], ["W", "=2+2"]],
    )
    csv_artifact = service.export(entity_uid, "csv")
    assert list(
        csv.reader(io.StringIO(csv_artifact.content.decode("utf-8-sig")))
    ) == [["Sample", "Value"], ["W", "'=2+2"]]
    assert csv_artifact.filename == "verified-table-workspace.csv"

    xlsx_artifact = service.export(entity_uid, "xlsx")
    with zipfile.ZipFile(io.BytesIO(xlsx_artifact.content)) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "'=2+2" in sheet
    assert "<f>" not in sheet


@pytest.mark.parametrize(
    "entity_uid",
    ("", "0", "01", "-1", "+1", "1.0", "１", "9" * 20, "/tmp/1"),
)
def test_invalid_workspace_identity_is_rejected(tmp_path: Path, entity_uid: str) -> None:
    _database, service, _valid = _setup(tmp_path)
    with pytest.raises(TableStructureServiceError) as invalid:
        service.get(entity_uid, include_unverified=True)
    assert invalid.value.code == "table_structure_service_invalid"


def test_non_table_and_unknown_identity_are_not_disclosed(tmp_path: Path) -> None:
    _database, service, figure_uid = _setup(tmp_path, asset_type="figure")
    with pytest.raises(TableStructureServiceError) as figure:
        service.get(figure_uid, include_unverified=True)
    assert figure.value.code == "table_structure_service_not_found"
    with pytest.raises(TableStructureServiceError) as unknown:
        service.get("999999", include_unverified=True)
    assert unknown.value.code == "table_structure_service_not_found"


def test_extra_fields_and_renderer_controlled_review_fields_are_rejected(tmp_path: Path) -> None:
    _database, service, entity_uid = _setup(tmp_path)
    with pytest.raises(TableStructureServiceError) as extra:
        service.review(
            entity_uid,
            1,
            "approve",
            reviewer="attacker",
            status="verified",
            fingerprint="forged",
        )
    assert extra.value.code == "table_structure_service_invalid"
    with pytest.raises(TableStructureServiceError) as rows_for_approve:
        service.review(entity_uid, 1, "approve", rows=[["forged"]])
    assert rows_for_approve.value.code == "table_structure_service_invalid"
    with pytest.raises(TableStructureServiceError) as extra_get:
        service.get(entity_uid, include_unverified=True, path="/tmp/db")
    assert extra_get.value.code == "table_structure_service_invalid"
    _assert_safe(extra.value.public_dict())


def test_corrupt_storage_maps_to_safe_service_error(tmp_path: Path) -> None:
    database, service, entity_uid = _setup(tmp_path)
    with database.connect() as connection:
        connection.execute(
            "UPDATE table_structure_versions SET candidate_json='{}'"
        )
    with pytest.raises(TableStructureServiceError) as corrupt:
        service.get(entity_uid, include_unverified=True)
    assert corrupt.value.code == "table_structure_service_corrupt"
    _assert_safe(corrupt.value.public_dict())


def test_note_is_internal_and_private_reference_is_rejected(tmp_path: Path) -> None:
    _database, service, entity_uid = _setup(tmp_path)
    with pytest.raises(TableStructureServiceError) as invalid:
        service.review(
            entity_uid,
            1,
            "approve",
            note="saved at /Users/name/private-review.txt",
        )
    assert invalid.value.code == "table_structure_service_invalid"
    assert "private-review" not in json.dumps(invalid.value.public_dict())
