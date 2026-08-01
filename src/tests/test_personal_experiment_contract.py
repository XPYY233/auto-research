from __future__ import annotations

import unittest
from dataclasses import replace

from auto_research.personal.experiment_contract import (
    ColumnMapping,
    MeasurementSeriesDraft,
    PersonalArtifactDraft,
    PersonalExperimentDraft,
    PersonalSourceFile,
    TabularImportPreview,
    personal_search_document,
)


class PersonalExperimentContractTests(unittest.TestCase):
    def source_file(self) -> PersonalSourceFile:
        return PersonalSourceFile(
            file_id="file-1",
            original_name="nanoindentation.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            sha256="a" * 64,
            size_bytes=2048,
        )

    def preview(self, *, confirmed: bool) -> TabularImportPreview:
        return TabularImportPreview(
            source_file=self.source_file(),
            sheet_name="Sheet1",
            row_count=12,
            columns=(
                ColumnMapping(
                    "dose",
                    "independent",
                    "number",
                    meaning="辐照剂量",
                    unit="dpa",
                    unit_confirmed=confirmed,
                    sample_values=("0", "0.1", "1"),
                ),
                ColumnMapping(
                    "hardness",
                    "dependent",
                    "number",
                    meaning="纳米硬度",
                    unit="GPa",
                    unit_confirmed=confirmed,
                    sample_values=("3.2", "3.8", "4.1"),
                ),
            ),
        )

    def plot_file(self) -> PersonalSourceFile:
        return PersonalSourceFile(
            file_id="plot-file-1",
            original_name="hardness-trend.png",
            media_type="image/png",
            sha256="b" * 64,
            size_bytes=1024,
        )

    def draft(self, *, confirmed_columns: bool = True, state: str = "draft") -> PersonalExperimentDraft:
        return PersonalExperimentDraft(
            draft_id="run-2026-08-01",
            project_name="W-Ta 辐照实验",
            run_name="室温纳米压痕",
            sample_name="W-Ta-03",
            method="纳米压痕",
            preview=self.preview(confirmed=confirmed_columns),
            series=(MeasurementSeriesDraft("series-1", "硬度-剂量", "dose", "hardness"),),
            supporting_files=(self.plot_file(),),
            artifacts=(
                PersonalArtifactDraft(
                    "plot-1",
                    "plot",
                    "硬度趋势图",
                    "plot-file-1",
                    linked_series_ids=("series-1",),
                    user_description="横轴为剂量，纵轴为硬度",
                ),
            ),
            conditions={"temperature": "室温"},
            user_note="同一批次重复测量三次",
            confirmation_state=state,
        )

    def test_numeric_units_require_explicit_confirmation(self):
        preview = self.preview(confirmed=False)
        self.assertEqual(preview.unresolved_columns, ("dose", "hardness"))
        self.assertTrue(all(column.needs_user_confirmation for column in preview.columns))

    def test_ready_draft_links_columns_series_and_plot(self):
        draft = self.draft()
        self.assertTrue(draft.ready_to_confirm)
        self.assertEqual(draft.confirmation_issues(), ())
        self.assertEqual(draft.artifacts[0].digitization_status, "not_requested")

    def test_missing_series_column_blocks_confirmation(self):
        draft = replace(
            self.draft(),
            series=(MeasurementSeriesDraft("series-1", "硬度-剂量", "missing", "hardness"),),
        )
        self.assertIn("missing_x_column:series-1:missing", draft.confirmation_issues())
        self.assertFalse(draft.ready_to_confirm)

    def test_unlinked_artifact_reference_blocks_confirmation(self):
        draft = replace(
            self.draft(),
            artifacts=(
                PersonalArtifactDraft(
                    "plot-1", "plot", "趋势图", "plot-file-1", linked_series_ids=("unknown",)
                ),
            ),
        )
        self.assertIn(
            "unknown_artifact_series:plot-1:unknown",
            draft.confirmation_issues(),
        )

    def test_draft_is_not_searchable_before_user_confirmation(self):
        with self.assertRaises(ValueError):
            personal_search_document(self.draft(state="draft"))

    def test_confirmed_record_builds_private_search_document(self):
        document = personal_search_document(self.draft(state="confirmed"))
        self.assertEqual(document["source_domain"], "personal")
        self.assertEqual(document["source_scope"], "private")
        self.assertEqual(document["record_type"], "experiment_run")
        self.assertEqual(document["entity_uid"], "personal:experiment_run:run-2026-08-01")
        self.assertEqual(document["measurement_meanings"], ["辐照剂量", "纳米硬度"])
        self.assertNotIn("path", document["source_file"])
        self.assertEqual(document["supporting_files"][0]["original_name"], "hardness-trend.png")
        self.assertNotIn("path", document["supporting_files"][0])

    def test_plot_digitization_cannot_be_implied(self):
        with self.assertRaises(ValueError):
            PersonalArtifactDraft(
                "plot-1",
                "plot",
                "趋势图",
                "file-1",
                digitization_status="automatic",
            )

    def test_bad_file_hash_is_rejected(self):
        with self.assertRaises(ValueError):
            PersonalSourceFile("file-1", "data.csv", "text/csv", "not-a-hash", 12)


if __name__ == "__main__":
    unittest.main()
