from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from auto_research.product.package_center import PackageKind, RightsConfirmation
from auto_research.product.package_center_payload_adapter import (
    StructuredPackageCenterPayloadAdapter,
)
from auto_research.product.package_transfer_payloads import (
    LiteratureCollectionPayloadPlanner,
    LiteraturePayloadSelection,
    LiteraturePdfCandidate,
    PersonalExperimentsPayloadPlanner,
    PersonalPayloadSelection,
    PersonalTableCandidate,
)
from auto_research.product.portable_repository import stable_paper_uid
from auto_research.product.transfer_package import TransferFileRights


class _LiteratureSource:
    source_id = "literature-local-workspace"

    def __init__(self, pdf: Path):
        paper_uid = stable_paper_uid(
            doi="10.1/demo", title="Demo paper", year=2026, first_author="A"
        )
        self.value = LiteraturePayloadSelection(
            papers=(
                {
                    "paper_uid": paper_uid,
                    "doi": "10.1/demo",
                    "title": "Demo paper",
                    "year": 2026,
                    "first_author": "A",
                },
            ),
            entities=(),
            pdfs=(LiteraturePdfCandidate(paper_uid, pdf, "demo.pdf"),),
        )

    def read_selection(self, selection):
        return self.value


class _PersonalSource:
    source_id = "personal-local-library"

    def __init__(self, table: Path):
        self.value = PersonalPayloadSelection(
            records=(
                {
                    "project_uid": "project_" + "1" * 32,
                    "project_name": "Project",
                    "sample_uid": "sample_" + "2" * 32,
                    "sample_name": "Sample",
                    "material": "W",
                    "run_uid": "run_" + "3" * 32,
                    "run_name": "Run",
                    "method": "TEM",
                    "confirmation_state": "confirmed",
                    "indexable": True,
                    "conditions": {},
                    "measurements": (),
                    "notes": (),
                },
            ),
            tables=(PersonalTableCandidate(table, "data.csv", "run_" + "3" * 32),),
        )

    def read_selection(self, selection):
        return self.value


class PackageCenterPayloadAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="package-center-payload-")
        self.root = Path(self.temporary.name)
        self.pdf = self.root / "demo.pdf"
        self.pdf.write_bytes(b"%PDF-1.4\n%%EOF")
        self.table = self.root / "data.csv"
        self.table.write_text("x,y\n1,2\n", encoding="utf-8")
        self.literature_source = _LiteratureSource(self.pdf)
        self.adapter = StructuredPackageCenterPayloadAdapter(
            literature_planner=LiteratureCollectionPayloadPlanner(
                self.literature_source
            ),
            personal_planner=PersonalExperimentsPayloadPlanner(
                _PersonalSource(self.table)
            ),
            workspace_parent=self.root / "workspace",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_literature_plan_requires_per_pdf_rights_and_cleans_workspace(self):
        candidate = self.adapter.plan(
            kind="literature_collection",
            scope="selected",
            selection=(self.literature_source.value.papers[0]["paper_uid"],),
        )
        self.assertEqual(candidate.paper_count, 1)
        self.assertEqual(len(candidate.rights_requirements), 1)
        materialized = self.adapter.materialize(
            candidate,
            rights_confirmations={
                candidate.rights_requirements[0].paper_uid: RightsConfirmation(
                    True, "group internal permission"
                )
            },
        )
        self.assertTrue(any((self.root / "workspace").iterdir()))
        materialized.close()
        self.assertEqual(list((self.root / "workspace").iterdir()), [])

    def test_prior_non_oa_rights_never_bypass_current_export_confirmation(self):
        original = self.literature_source.value.pdfs[0]
        self.literature_source.value = LiteraturePayloadSelection(
            papers=self.literature_source.value.papers,
            entities=self.literature_source.value.entities,
            pdfs=(
                LiteraturePdfCandidate(
                    original.paper_uid,
                    original.source_path,
                    original.file_name,
                    rights=TransferFileRights(True, "stale prior declaration"),
                ),
            ),
        )
        candidate = self.adapter.plan(
            kind="literature_collection",
            scope="selected",
            selection=(original.paper_uid,),
        )
        self.assertEqual(
            tuple(item.paper_uid for item in candidate.rights_requirements),
            (original.paper_uid,),
        )

    def test_personal_plan_has_stable_fingerprint_and_private_payload(self):
        first = self.adapter.plan(
            kind=PackageKind.PERSONAL_EXPERIMENTS.value,
            scope="all",
            selection=None,
        )
        self.assertEqual(
            self.adapter.current_content_fingerprint(first),
            first.content_fingerprint,
        )
        self.table.write_text("x,y\n1,3\n", encoding="utf-8")
        self.assertNotEqual(
            self.adapter.current_content_fingerprint(first),
            first.content_fingerprint,
        )

    def test_personal_table_run_binding_changes_content_fingerprint(self):
        first = self.adapter.plan(
            kind=PackageKind.PERSONAL_EXPERIMENTS.value,
            scope="all",
            selection=None,
        )
        table = self.adapter._personal._source.value.tables[0]
        self.adapter._personal._source.value = PersonalPayloadSelection(
            records=self.adapter._personal._source.value.records,
            tables=(
                PersonalTableCandidate(
                    table.source_path,
                    table.file_name,
                    "run_" + "4" * 32,
                ),
            ),
        )
        with self.assertRaises(Exception):
            self.adapter.current_content_fingerprint(first)


if __name__ == "__main__":
    unittest.main()
