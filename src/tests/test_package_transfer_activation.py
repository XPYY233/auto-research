from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from auto_research.evidence.federated_search_session import FederatedSearchSession
from auto_research.personal.private_repository import PrivateExperimentRepository
from auto_research.personal.transfer_merge import PersonalTransferMergeService
from auto_research.product.package_transfer_activation import (
    PackageTransferActivationService,
)
from auto_research.product.package_transfer_payloads import (
    LiteratureCollectionPayloadPlanner,
    LiteraturePayloadSelection,
    LiteraturePdfCandidate,
    PersonalExperimentsPayloadPlanner,
    PersonalPayloadSelection,
    PersonalTableCandidate,
    PayloadSelection,
    audit_transfer_payload_tree,
    materialize_payload_candidate,
    open_transferred_literature_repository,
    read_personal_transfer_snapshot,
)
from auto_research.product.portable_repository import stable_entity_uid, stable_paper_uid
from auto_research.product.transfer_package import (
    TransferFileRights,
    TransferPackageError,
    export_transfer_package,
    import_transfer_package,
)


class _LiteratureSource:
    source_id = "literature-activation-source"

    def __init__(self, payload):
        self.payload = payload

    def read_selection(self, selection):
        return self.payload


class _UnusedPersonalMerger:
    def prepare(self, *args, **kwargs):  # pragma: no cover - literature test only
        raise AssertionError("personal merger must not be called")


class _PersonalSource:
    source_id = "personal-activation-source"

    def __init__(self, payload):
        self.payload = payload

    def read_selection(self, selection):
        return self.payload


class PackageTransferActivationTests(unittest.TestCase):
    def test_literature_activation_adds_private_search_without_official_state(self):
        with tempfile.TemporaryDirectory(prefix="transfer-activation-") as temporary:
            root = Path(temporary)
            paper = {
                "doi": "10.1000/activation.1",
                "title": "Synthetic activation paper",
                "year": 2026,
                "first_author": "A Researcher",
            }
            paper_uid = stable_paper_uid(**paper)
            paper["paper_uid"] = paper_uid
            entity = {
                "paper_uid": paper_uid,
                "entity_type": "item",
                "identity_key": "activation-temperature",
                "entity_uid": stable_entity_uid(
                    paper_uid, "item", "activation-temperature"
                ),
                "quality_gate_status": "dual_pass",
                "source_kind": "text",
                "review_action": "automatic",
                "payload": {
                    "value_text": "300",
                    "meaning": "激活测试温度",
                    "unit": "K",
                    "source_page": 1,
                    "source_excerpt": "tested at 300 K",
                },
            }
            pdf = root / "activation.pdf"
            pdf.write_bytes(b"%PDF-1.4\nactivation\n%%EOF\n")
            payload = LiteraturePayloadSelection(
                papers=(paper,),
                entities=(entity,),
                pdfs=(
                    LiteraturePdfCandidate(
                        paper_uid,
                        pdf,
                        "activation.pdf",
                        rights=TransferFileRights(True, "current internal permission"),
                        rights_confirmed_for_export=True,
                    ),
                ),
            )
            candidate = LiteratureCollectionPayloadPlanner(
                _LiteratureSource(payload)
            ).plan(PayloadSelection("selected", selected_ids=(paper_uid,)))
            plan = materialize_payload_candidate(
                candidate,
                root / "workspace",
                package_id="user-literature-activation",
                package_version="1.0.0",
                created_at="2026-08-09T00:00:00+00:00",
            )
            package = root / "activation.aresearch"
            exported = export_transfer_package(plan, package, unencrypted_ack=True)
            imported = import_transfer_package(
                package,
                destination_root=root / "recipient",
                expected_kind="literature_collection",
                expected_package_sha256=exported.package_sha256,
                checksum_ack=True,
                require_structured_payload=True,
            )
            session = FederatedSearchSession()
            service = PackageTransferActivationService(
                search_session=session,
                personal_merger=_UnusedPersonalMerger(),
            )
            result = service.activate(imported, keep_conflicts=False)
            self.assertTrue(result.public_dict()["search_ready"])
            self.assertEqual(session.search("激活测试温度").total, 1)
            self.assertFalse(session.status()["official_ready"])
            self.assertTrue(session.status()["private_ready"])

            reopened = open_transferred_literature_repository(
                imported.install_path,
                imported.manifest,
            )
            installed_pdf = reopened.resolve_pdf(paper_uid)
            self.assertIsNotNone(installed_pdf)
            self.assertNotEqual(installed_pdf, pdf)
            installed_pdf.write_bytes(installed_pdf.read_bytes() + b"tamper")
            with self.assertRaises(TransferPackageError) as changed:
                reopened.resolve_pdf(paper_uid)
            self.assertEqual(changed.exception.code, "transfer_payload_changed")

    def test_personal_activation_merges_then_refreshes_search_atomically(self):
        with tempfile.TemporaryDirectory(prefix="personal-transfer-activation-") as temporary:
            root = Path(temporary)
            table = root / "hardness.csv"
            table.write_text("dose,hardness\n0,3.2\n1,4.0\n", encoding="utf-8")
            record = {
                "project_uid": "project_" + "1" * 32,
                "project_name": "W-Ta 项目",
                "sample_uid": "sample_" + "2" * 32,
                "sample_name": "W-Ta-01",
                "material": "W-Ta",
                "run_uid": "run_" + "3" * 32,
                "run_name": "室温纳米压痕",
                "method": "纳米压痕",
                "confirmation_state": "confirmed",
                "indexable": True,
                "conditions": {"temperature": "300 K"},
                "sheet_name": "Sheet1",
                "row_count": 2,
                "columns": [
                    {"source_name": "dose", "role": "independent", "data_type": "number", "meaning": "辐照剂量", "unit": "dpa"},
                    {"source_name": "hardness", "role": "dependent", "data_type": "number", "meaning": "纳米硬度", "unit": "GPa"},
                ],
                "series": [
                    {"series_uid": "measurement_" + "6" * 32, "name": "硬度-剂量", "x_column": "dose", "y_column": "hardness", "uncertainty_column": "", "description": "已核验序列"}
                ],
                "measurements": [
                    {"measurement_uid": "measurement_" + "4" * 32, "name": "硬度-剂量", "meaning": "纳米硬度", "unit": "GPa", "x_name": "dose", "y_name": "hardness", "uncertainty_name": ""}
                ],
                "notes": [
                    {"note_uid": "note_" + "5" * 32, "text": "已核验"}
                ],
            }
            payload = PersonalPayloadSelection(
                records=(record,),
                tables=(PersonalTableCandidate(table, table.name, record["run_uid"]),),
            )
            candidate = PersonalExperimentsPayloadPlanner(
                _PersonalSource(payload)
            ).plan(PayloadSelection("all"))
            plan = materialize_payload_candidate(
                candidate,
                root / "workspace",
                package_id="user-personal-activation",
                package_version="1.0.0",
                created_at="2026-08-09T00:00:00+00:00",
            )
            package = root / "personal.aresearch"
            exported = export_transfer_package(plan, package, unencrypted_ack=True)
            imported = import_transfer_package(
                package,
                destination_root=root / "recipient",
                expected_kind="personal_experiments",
                expected_package_sha256=exported.package_sha256,
                checksum_ack=True,
                require_structured_payload=True,
            )
            repository = PrivateExperimentRepository(root / "private-library")
            merger = PersonalTransferMergeService(
                repository,
                payload_auditor=audit_transfer_payload_tree,
                snapshot_reader=read_personal_transfer_snapshot,
            )
            session = FederatedSearchSession()
            service = PackageTransferActivationService(
                search_session=session,
                personal_merger=merger,
            )

            result = service.activate(imported, keep_conflicts=False)

            self.assertTrue(result.public_dict()["search_ready"])
            self.assertGreaterEqual(session.search("纳米硬度").total, 1)
            self.assertEqual(result.activation_outcome, "imported")
            with repository.connect() as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM transfer_imports").fetchone()[0],
                    1,
                )


if __name__ == "__main__":
    unittest.main()
