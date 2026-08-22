from __future__ import annotations

from auto_research.product.dataset_bundle import DatasetBundleBuilder
from auto_research.product.dataset_bundle_sources import (
    portable_export_dataset_rows,
    private_selection_dataset_rows,
)
from auto_research.product.package_transfer_payloads import PersonalPayloadSelection
from auto_research.product.portable_repository import PortableExportPlan


def test_portable_rows_are_path_free_and_keep_four_types():
    plan = PortableExportPlan(
        papers=(
            {
                "paper_uid": "paper_1234567890abcdef1234567890abcdef",
                "title": "Paper",
                "doi": "10.1000/paper",
                "year": 2025,
                "first_author": "A",
                "corresponding_author": "B",
                "material_focus": "W",
            },
        ),
        entities=tuple(
            {
                "paper_uid": "paper_1234567890abcdef1234567890abcdef",
                "entity_type": kind,
                "identity_key": f"stable:{kind}",
                "quality_gate_status": "published",
                "payload": (
                    {"value_text": "42", "meaning": "strength"}
                    if kind == "item"
                    else {"finding_text": "stable"}
                    if kind == "finding"
                    else {"caption": kind, "page_start": 2, "page_end": 2}
                ),
            }
            for kind in ("item", "finding", "table", "figure")
        ),
    )
    papers, evidence = portable_export_dataset_rows(
        plan,
        source_scope="workspace",
        source_id="workspace-v12",
    )
    assert len(papers) == 1
    assert {row["entity_type"] for row in evidence} == {"item", "finding", "table", "figure"}
    assert all("path" not in str(row).casefold() for row in (*papers, *evidence))
    assert all(row["asset_ref"]["included"] is False for row in evidence if row["entity_type"] in {"table", "figure"})
    bundle = DatasetBundleBuilder().plan(papers=papers, evidence=evidence)
    assert bundle.record_count == 4


def test_private_selection_only_projects_confirmed_structured_table():
    selection = PersonalPayloadSelection(
        records=(
            {
                "run_uid": "run_1234567890abcdef1234567890abcdef",
                "run_name": "Run 1",
                "project_name": "Project",
                "sample_name": "Sample",
                "material": "W",
                "method": "tensile",
                "confirmation_state": "confirmed",
                "indexable": True,
                "conditions": {"temperature": "300 K"},
                "sheet_name": "Sheet1",
                "columns": [{"source_name": "x", "meaning": "time", "unit": "s"}],
                "series": [{"series_uid": "series_1", "name": "x"}],
            },
        ),
        tables=(),
    )
    rows = private_selection_dataset_rows(selection)
    assert rows[0]["record_status"] == "confirmed"
    assert rows[0]["indexable"] is True
    assert "path" not in str(rows[0]).casefold()
    plan = DatasetBundleBuilder().plan(
        papers=[],
        evidence=[],
        include_private=True,
        private_records=rows,
    )
    assert plan.record_count == 1
