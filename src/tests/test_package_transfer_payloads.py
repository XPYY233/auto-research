from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from auto_research.product.package_transfer_payloads import (
    LiteratureCollectionPayloadPlanner,
    LiteraturePayloadSelection,
    LiteraturePdfCandidate,
    PayloadSelection,
    PersonalExperimentsPayloadPlanner,
    PersonalPayloadSelection,
    PersonalTableCandidate,
    audit_personal_transfer_snapshot,
    audit_transfer_payload_tree,
    materialize_payload_candidate,
    open_transferred_literature_repository,
)
from auto_research.product.portable_repository import stable_entity_uid, stable_paper_uid
from auto_research.product.transfer_package import (
    TransferFileRights,
    TransferPackageError,
    export_transfer_package,
    import_transfer_package,
    verify_transfer_package,
)


def uid(prefix: str, digit: str) -> str:
    return prefix + "_" + digit * 32


class LiteratureSource:
    source_id = "literature-team-library"

    def __init__(self, selection: LiteraturePayloadSelection) -> None:
        self.selection = selection
        self.calls: list[PayloadSelection] = []

    def read_selection(self, selection: PayloadSelection) -> LiteraturePayloadSelection:
        self.calls.append(selection)
        return self.selection


class ResolvingLiteratureSource(LiteratureSource):
    def __init__(self, selection, mapping):
        super().__init__(selection)
        self.mapping = mapping

    def resolve_selection_ids(self, selected_ids):
        return tuple(self.mapping[item] for item in selected_ids)


class PersonalSource:
    source_id = "personal-team-experiments"

    def __init__(self, selection: PersonalPayloadSelection) -> None:
        self.selection = selection
        self.calls: list[PayloadSelection] = []

    def read_selection(self, selection: PayloadSelection) -> PersonalPayloadSelection:
        self.calls.append(selection)
        return self.selection


class PackageTransferPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="transfer-payload-")
        self.root = Path(self.temporary.name)
        self.paper = {
            "doi": "10.1000/payload.1",
            "title": "Synthetic irradiation payload",
            "year": 2026,
            "first_author": "A Researcher",
        }
        self.paper_uid = stable_paper_uid(
            doi=self.paper["doi"],
            title=self.paper["title"],
            year=self.paper["year"],
            first_author=self.paper["first_author"],
        )
        self.paper["paper_uid"] = self.paper_uid
        self.entity = {
            "paper_uid": self.paper_uid,
            "entity_type": "item",
            "identity_key": "temperature-1",
            "entity_uid": stable_entity_uid(self.paper_uid, "item", "temperature-1"),
            "quality_gate_status": "dual_pass",
            "source_kind": "text",
            "review_action": "automatic",
            "payload": {
                "value_text": "300",
                "meaning": "实验温度",
                "unit": "K",
                "source_page": 2,
                "source_excerpt": "tested at 300 K",
            },
        }
        self.pdf = self.root / "paper.pdf"
        self.pdf.write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n")
        self.csv = self.root / "measurements.csv"
        self.csv.write_text("temperature,hardness\n300,2.4\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def literature_payload(self) -> LiteraturePayloadSelection:
        return LiteraturePayloadSelection(
            papers=(self.paper,),
            entities=(self.entity,),
            pdfs=(
                LiteraturePdfCandidate(
                    self.paper_uid,
                    self.pdf,
                    "paper.pdf",
                    license_id="cc-by-4.0",
                    license_verified=True,
                ),
            ),
        )

    def personal_record(self) -> dict:
        return {
            "project_uid": uid("project", "1"),
            "project_name": "辐照硬度实验",
            "sample_uid": uid("sample", "2"),
            "sample_name": "W-1",
            "material": "W",
            "run_uid": uid("run", "3"),
            "run_name": "300 K 纳米压痕",
            "method": "nanoindentation",
            "confirmation_state": "confirmed",
            "indexable": True,
            "conditions": {"temperature": "300 K", "dose": "1 dpa"},
            "measurements": [
                {
                    "measurement_uid": uid("measurement", "4"),
                    "name": "hardness-series",
                    "meaning": "辐照后硬度",
                    "unit": "GPa",
                    "x_name": "dose",
                    "y_name": "hardness",
                    "uncertainty_name": "",
                }
            ],
            "notes": [{"note_uid": uid("note", "5"), "text": "仅包含已核验记录"}],
        }

    def test_literature_materialization_contains_audited_repository_and_private_projection(self) -> None:
        source = LiteratureSource(self.literature_payload())
        candidate = LiteratureCollectionPayloadPlanner(source).plan(
            PayloadSelection("selected", selected_ids=(self.paper_uid,))
        )
        self.assertEqual(candidate.optional_file_count, 1)
        self.assertEqual(candidate.missing_pdf_count, 0)
        plan = materialize_payload_candidate(
            candidate,
            self.root / "literature-workspace",
            package_id="user-literature-payload",
            package_version="1.0.0",
            created_at="2026-08-09T00:00:00+00:00",
        )
        self.assertEqual(
            {entry.role for entry in plan.files},
            {"structured_repository", "repository_rights", "repository_provenance", "paper_pdf"},
        )
        package = self.root / "literature.aresearch"
        exported = export_transfer_package(plan, package, unencrypted_ack=True)
        imported = import_transfer_package(
            package,
            destination_root=self.root / "recipient",
            expected_kind="literature_collection",
            expected_package_sha256=exported.package_sha256,
            checksum_ack=True,
        )
        manifest = json.loads((imported.install_path / "manifest.json").read_text())
        audit = audit_transfer_payload_tree(imported.install_path, manifest)
        self.assertEqual(audit["record_count"], 1)
        repository = open_transferred_literature_repository(
            imported.install_path,
            manifest,
        )
        documents = list(repository.iter_search_documents())
        self.assertEqual(documents[0]["source_scope"], "private")
        self.assertTrue(documents[0]["source_id"].startswith("literature-"))
        self.assertNotEqual(documents[0]["source_scope"], "official")

    def test_selected_local_ids_are_resolved_before_stable_identity_check(self) -> None:
        source = ResolvingLiteratureSource(
            self.literature_payload(),
            {"42": self.paper_uid},
        )
        candidate = LiteratureCollectionPayloadPlanner(source).plan(
            PayloadSelection("selected", selected_ids=("42",))
        )
        self.assertEqual(candidate.selection.selected_ids, (self.paper_uid,))
        self.assertEqual(source.calls[0].selected_ids, (self.paper_uid,))

    def test_literature_pdf_accounting_and_rights_are_default_deny(self) -> None:
        papers = []
        for index in range(3):
            paper = {
                "doi": f"10.1000/payload.{index + 10}",
                "title": f"Synthetic payload {index}",
                "year": 2026,
                "first_author": "A Researcher",
            }
            paper["paper_uid"] = stable_paper_uid(**paper)
            papers.append(paper)
        missing = LiteraturePdfCandidate(papers[0]["paper_uid"], None, "missing.pdf")
        restricted = LiteraturePdfCandidate(
            papers[1]["paper_uid"], self.pdf, "restricted.pdf"
        )
        allowed = LiteraturePdfCandidate(
            papers[2]["paper_uid"],
            self.pdf,
            "allowed.pdf",
            rights=TransferFileRights(True, "author-confirmed internal transfer"),
            rights_confirmed_for_export=True,
        )
        payload = LiteraturePayloadSelection(
            tuple(papers), (), (missing, restricted, allowed)
        )
        candidate = LiteratureCollectionPayloadPlanner(LiteratureSource(payload)).plan(
            PayloadSelection("all")
        )
        self.assertEqual(candidate.missing_pdf_count, 1)
        # A prior source-side declaration is not authority for this export;
        # both non-OA PDFs require a fresh per-paper confirmation.
        self.assertEqual(candidate.rights_required_count, 2)
        self.assertEqual(candidate.optional_file_count, 0)
        self.assertIn("需要逐篇确认", " ".join(candidate.warnings))

    def test_selection_modes_are_forwarded_to_read_only_port(self) -> None:
        source = LiteratureSource(self.literature_payload())
        planner = LiteratureCollectionPayloadPlanner(source)
        planner.plan(PayloadSelection("filtered", filter_token="filter-reviewed"))
        self.assertEqual(source.calls[0].mode.value, "filtered")
        with self.assertRaises(TransferPackageError):
            PayloadSelection("selected")

    def test_selected_scope_rejects_source_returning_other_identity(self) -> None:
        planner = LiteratureCollectionPayloadPlanner(
            LiteratureSource(self.literature_payload())
        )
        with self.assertRaises(TransferPackageError) as raised:
            planner.plan(
                PayloadSelection(
                    "selected", selected_ids=("paper_" + "f" * 32,)
                )
            )
        self.assertEqual(raised.exception.code, "transfer_selection_mismatch")

    def test_personal_materialization_contains_sanitized_snapshot_and_raw_table(self) -> None:
        payload = PersonalPayloadSelection(
            records=(self.personal_record(),),
            tables=(
                PersonalTableCandidate(
                    self.csv, "measurements.csv", "run_" + "3" * 32
                ),
            ),
        )
        candidate = PersonalExperimentsPayloadPlanner(PersonalSource(payload)).plan(
            PayloadSelection("all")
        )
        plan = materialize_payload_candidate(
            candidate,
            self.root / "personal-workspace",
            package_id="user-personal-payload",
            package_version="1.0.0",
            created_at="2026-08-09T00:00:00+00:00",
        )
        self.assertEqual({entry.role for entry in plan.files}, {"structured_snapshot", "table"})
        snapshot = next(entry.source_path for entry in plan.files if entry.role == "structured_snapshot")
        audit = audit_personal_transfer_snapshot(snapshot, expected_source_id="personal-team-experiments")
        self.assertEqual(audit["run_count"], 1)
        with sqlite3.connect(snapshot) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
            self.assertNotIn("drafts", tables)
            self.assertNotIn("ai_suggestions", tables)
            blob = snapshot.read_bytes().lower()
            self.assertNotIn(b"/users/", blob)
            self.assertNotIn(b"file_id", blob)
        package = self.root / "personal.aresearch"
        exported = export_transfer_package(plan, package, unencrypted_ack=True)
        imported = import_transfer_package(
            package,
            destination_root=self.root / "recipient-personal",
            expected_kind="personal_experiments",
            expected_package_sha256=exported.package_sha256,
            checksum_ack=True,
        )
        self.assertTrue((imported.install_path / "personal/structured/personal_transfer.sqlite").is_file())
        self.assertFalse((self.root / "recipient-personal/official-packages").exists())
        self.assertFalse((self.root / "recipient-personal/personal_experiments.sqlite").exists())

    def test_personal_draft_and_tampered_schema_are_rejected(self) -> None:
        record = self.personal_record()
        record["confirmation_state"] = "draft"
        source = PersonalSource(PersonalPayloadSelection(records=(record,)))
        with self.assertRaises(TransferPackageError) as raised:
            PersonalExperimentsPayloadPlanner(source).plan(PayloadSelection("all"))
        self.assertEqual(raised.exception.code, "transfer_payload_unconfirmed")

        valid = PersonalExperimentsPayloadPlanner(
            PersonalSource(
                PersonalPayloadSelection(
                    records=(self.personal_record(),),
                    tables=(
                        PersonalTableCandidate(
                            self.csv, "measurements.csv", uid("run", "3")
                        ),
                    ),
                )
            )
        ).plan(PayloadSelection("all"))
        plan = materialize_payload_candidate(
            valid,
            self.root / "tamper-workspace",
            package_id="user-personal-tamper",
            package_version="1.0.0",
        )
        snapshot = next(entry.source_path for entry in plan.files if entry.role == "structured_snapshot")
        with sqlite3.connect(snapshot) as connection:
            connection.execute("PRAGMA application_id=0")
        with self.assertRaises(TransferPackageError) as raised:
            audit_personal_transfer_snapshot(snapshot)
        self.assertEqual(raised.exception.code, "transfer_payload_schema")

    def test_personal_snapshot_rejects_orphan_and_cross_project_identities(self) -> None:
        valid = PersonalExperimentsPayloadPlanner(
            PersonalSource(
                PersonalPayloadSelection(
                    records=(self.personal_record(),),
                    tables=(
                        PersonalTableCandidate(
                            self.csv, "measurements.csv", uid("run", "3")
                        ),
                    ),
                )
            )
        ).plan(PayloadSelection("all"))
        plan = materialize_payload_candidate(
            valid,
            self.root / "identity-workspace",
            package_id="user-personal-identity",
            package_version="1.0.0",
        )
        snapshot = next(
            entry.source_path for entry in plan.files if entry.role == "structured_snapshot"
        )
        with sqlite3.connect(snapshot) as connection:
            connection.execute(
                "INSERT INTO projects VALUES (?,?)",
                (uid("project", "9"), "Unused project"),
            )
        with self.assertRaises(TransferPackageError) as raised:
            audit_personal_transfer_snapshot(snapshot)
        self.assertEqual(raised.exception.code, "transfer_payload_identity")

    def test_personal_snapshot_rejects_same_columns_without_constraints(self) -> None:
        snapshot = self.root / "weak-schema.sqlite"
        with sqlite3.connect(snapshot) as connection:
            connection.executescript(
                """
                PRAGMA application_id=1095914576;
                PRAGMA user_version=1;
                CREATE TABLE transfer_meta(key TEXT,value TEXT);
                CREATE TABLE projects(project_uid TEXT,name TEXT);
                CREATE TABLE samples(sample_uid TEXT,project_uid TEXT,name TEXT,material TEXT);
                CREATE TABLE runs(run_uid TEXT,project_uid TEXT,sample_uid TEXT,name TEXT,method TEXT,confirmation_state TEXT,indexable INTEGER);
                CREATE TABLE conditions(run_uid TEXT,name TEXT,value TEXT);
                CREATE TABLE measurements(measurement_uid TEXT,run_uid TEXT,name TEXT,meaning TEXT,unit TEXT,x_name TEXT,y_name TEXT,uncertainty_name TEXT);
                CREATE TABLE notes(note_uid TEXT,run_uid TEXT,text TEXT);
                """
            )
        with self.assertRaises(TransferPackageError) as raised:
            audit_personal_transfer_snapshot(snapshot)
        self.assertEqual(raised.exception.code, "transfer_payload_schema")

    def test_export_file_names_reject_windows_paths_and_xlsx_macros(self) -> None:
        path_payload = PersonalPayloadSelection(
            records=(self.personal_record(),),
            tables=(
                PersonalTableCandidate(
                    self.csv,
                    r"C:\Users\Alice\secret.csv",
                    uid("run", "3"),
                ),
            ),
        )
        candidate = PersonalExperimentsPayloadPlanner(
            PersonalSource(path_payload)
        ).plan(PayloadSelection("all"))
        with self.assertRaises(TransferPackageError) as path_error:
            materialize_payload_candidate(
                candidate,
                self.root / "path-name-workspace",
                package_id="user-personal-path-name",
                package_version="1.0.0",
            )
        self.assertEqual(path_error.exception.code, "transfer_file_name_invalid")

        workbook = self.root / "macro.xlsx"
        with zipfile.ZipFile(workbook, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("xl/vbaProject.bin", b"macro")
        macro_payload = PersonalPayloadSelection(
            records=(self.personal_record(),),
            tables=(
                PersonalTableCandidate(workbook, "macro.xlsx", uid("run", "3")),
            ),
        )
        candidate = PersonalExperimentsPayloadPlanner(
            PersonalSource(macro_payload)
        ).plan(PayloadSelection("all"))
        with self.assertRaises(TransferPackageError) as macro_error:
            materialize_payload_candidate(
                candidate,
                self.root / "macro-workspace",
                package_id="user-personal-macro",
                package_version="1.0.0",
            )
        self.assertEqual(macro_error.exception.code, "transfer_personal_file_invalid")

        unsafe_members = (
            (
                "external.xlsx",
                "xl/_rels/workbook.xml.rels",
                '<Relationships><Relationship TargetMode="External" Target="https://example.invalid"/></Relationships>',
            ),
            (
                "entity.xlsx",
                "xl/workbook.xml",
                '<!DOCTYPE x [<!ENTITY leak SYSTEM "file:///etc/passwd">]><x/>',
            ),
        )
        for file_name, member, content in unsafe_members:
            with self.subTest(file_name=file_name):
                workbook = self.root / file_name
                with zipfile.ZipFile(workbook, "w") as archive:
                    archive.writestr("[Content_Types].xml", "<Types/>")
                    archive.writestr(member, content)
                payload = PersonalPayloadSelection(
                    records=(self.personal_record(),),
                    tables=(
                        PersonalTableCandidate(
                            workbook, file_name, uid("run", "3")
                        ),
                    ),
                )
                candidate = PersonalExperimentsPayloadPlanner(
                    PersonalSource(payload)
                ).plan(PayloadSelection("all"))
                with self.assertRaises(TransferPackageError) as raised:
                    materialize_payload_candidate(
                        candidate,
                        self.root / f"{file_name}-workspace",
                        package_id=f"user-personal-{file_name.split('.')[0]}",
                        package_version="1.0.0",
                    )
                self.assertIn(
                    raised.exception.code,
                    {"transfer_personal_file_invalid", "transfer_sensitive_content"},
                )

        collision = self.root / "collision.xlsx"
        with zipfile.ZipFile(collision, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("XL/workbook.xml", "<workbook/>")
            archive.writestr("xl/WORKBOOK.xml", "<workbook/>")
        collision_payload = PersonalPayloadSelection(
            records=(self.personal_record(),),
            tables=(
                PersonalTableCandidate(
                    collision, "collision.xlsx", uid("run", "3")
                ),
            ),
        )
        candidate = PersonalExperimentsPayloadPlanner(
            PersonalSource(collision_payload)
        ).plan(PayloadSelection("all"))
        with self.assertRaises(TransferPackageError) as collision_error:
            materialize_payload_candidate(
                candidate,
                self.root / "collision-workspace",
                package_id="user-personal-collision",
                package_version="1.0.0",
            )
        self.assertEqual(
            collision_error.exception.code, "transfer_personal_file_invalid"
        )

    def test_personal_snapshot_rejects_sensitive_values_before_sqlite_materialization(self) -> None:
        record = self.personal_record()
        record["notes"] = [
            {"note_uid": uid("note", "5"), "text": "source file:///Users/alice/private.csv"}
        ]
        source = PersonalSource(PersonalPayloadSelection(records=(record,)))
        with self.assertRaises(TransferPackageError) as raised:
            PersonalExperimentsPayloadPlanner(source).plan(PayloadSelection("all"))
        self.assertEqual(raised.exception.code, "transfer_sensitive_content")

    def test_low_level_plan_can_require_structured_payload(self) -> None:
        from auto_research.product.transfer_package import TransferFileSpec, plan_transfer_package

        with self.assertRaises(TransferPackageError) as raised:
            plan_transfer_package(
                kind="personal_experiments",
                package_id="user-personal-raw-only",
                package_version="1.0.0",
                files=(TransferFileSpec(self.csv, "personal/tables/data.csv", "table", "text/csv"),),
                require_structured_payload=True,
            )
        self.assertEqual(raised.exception.code, "transfer_payload_required")

        legacy_plan = plan_transfer_package(
            kind="personal_experiments",
            package_id="user-personal-legacy-raw",
            package_version="1.0.0",
            files=(
                TransferFileSpec(
                    self.csv, "personal/tables/data.csv", "table", "text/csv"
                ),
            ),
        )
        package = self.root / "legacy-raw.aresearch"
        exported = export_transfer_package(legacy_plan, package, unencrypted_ack=True)
        with self.assertRaises(TransferPackageError) as strict_import:
            import_transfer_package(
                package,
                destination_root=self.root / "strict-recipient",
                expected_kind="personal_experiments",
                expected_package_sha256=exported.package_sha256,
                checksum_ack=True,
                require_structured_payload=True,
            )
        self.assertEqual(strict_import.exception.code, "transfer_payload_required")


if __name__ == "__main__":
    unittest.main()
