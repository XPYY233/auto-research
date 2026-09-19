from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = Path(__file__).resolve().parents[3] / "src"
for path in (DESKTOP_ROOT, SOURCE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from auto_research.evidence.federated_search_session import (  # noqa: E402
    FederatedSearchSession,
    SearchSourceRegistration,
)
from auto_research.personal.experiment_contract import (  # noqa: E402
    ColumnMapping,
    MeasurementSeriesDraft,
    PersonalExperimentDraft,
    PersonalSourceFile,
    TabularImportPreview,
)
from auto_research.personal.private_repository import (  # noqa: E402
    PrivateExperimentRepository,
    PrivateProject,
    PrivateSample,
)
from auto_research.product.runtime_api import (  # noqa: E402
    TransferPackageKind,
    list_installed_transfer_packages,
    open_transferred_literature_repository,
)
from auto_research.product.operation_history import OperationHistoryService  # noqa: E402
from auto_research.product.package_job_contract import PackageOperation  # noqa: E402
from package_center_runtime import (  # noqa: E402
    DesktopPackageCenterRuntimeBuilder,
    NoAutomaticLiteratureLicenseVerifier,
    SnapshotRegisteredPdfResolver,
    create_workspace_snapshot,
)
from desktop_product_services import create_desktop_product_services  # noqa: E402
from package_export_destination_broker import PackageExportDestinationBroker  # noqa: E402
from package_import_service import PackageServiceStatus  # noqa: E402
from package_selection_broker import PackageSelectionBroker, PackageSelectionSource  # noqa: E402


class _OperationHistoryStore:
    storage_label = "test-operation-history-aes-256-gcm"

    def __init__(self) -> None:
        self.value = None

    def load(self):
        return copy.deepcopy(self.value)

    def save(self, value):
        self.value = copy.deepcopy(value)

    def clear(self):
        self.value = None


def _build_v12_database(path: Path, pdf: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE papers (
              id INTEGER PRIMARY KEY, doi TEXT, title TEXT, year INTEGER,
              first_author TEXT, corresponding_author TEXT, material_focus TEXT,
              pdf_path TEXT
            );
            CREATE TABLE data_items (
              id INTEGER PRIMARY KEY, paper_id INTEGER, stable_key TEXT, origin_type TEXT
            );
            CREATE TABLE data_versions (
              id INTEGER PRIMARY KEY, item_id INTEGER, version_no INTEGER,
              value_text TEXT, meaning TEXT, unit TEXT, article_title TEXT, doi TEXT,
              context_explanation TEXT, source_page INTEGER, source_locator TEXT,
              source_excerpt TEXT, review_action TEXT
            );
            CREATE VIEW v_current_six_column_data AS
              SELECT i.id item_id,i.paper_id,i.stable_key,i.origin_type,v.version_no,
                     v.value_text,v.meaning,v.unit,v.article_title,v.doi,v.context_explanation,
                     v.source_page,v.source_locator,v.source_excerpt,v.review_action,
                     v.value_text original_value_text,v.meaning original_meaning,
                     v.unit original_unit,v.context_explanation original_context_explanation,
                     v.source_page original_source_page,v.source_locator original_source_locator,
                     v.source_excerpt original_source_excerpt
              FROM data_items i JOIN data_versions v ON v.item_id=i.id
              WHERE v.version_no=(SELECT MAX(v2.version_no) FROM data_versions v2 WHERE v2.item_id=i.id);
            CREATE TABLE visual_assets (
              id INTEGER PRIMARY KEY, paper_id INTEGER, asset_type TEXT, label TEXT,
              asset_number INTEGER, display_name TEXT, caption TEXT, page_start INTEGER,
              page_end INTEGER, physical_quantities_json TEXT, variables_json TEXT,
              materials_json TEXT, conditions_text TEXT, methods_text TEXT,
              context_explanation TEXT, tags_json TEXT, source_context TEXT,
              review_status TEXT
            );
            CREATE TABLE visual_asset_reviews (
              id INTEGER PRIMARY KEY, asset_id INTEGER, version_no INTEGER,
              review_action TEXT, fields_json TEXT
            );
            CREATE TABLE quality_candidates (
              id INTEGER PRIMARY KEY, published_item_id INTEGER, published_asset_id INTEGER,
              gate_status TEXT, overall_score REAL, candidate_json TEXT
            );
            CREATE TABLE data_item_visual_links (
              item_id INTEGER, asset_id INTEGER, relation_kind TEXT
            );
            """
        )
        connection.executemany(
            "INSERT INTO papers VALUES (?,?,?,?,?,?,?,?)",
            (
                (
                    1,
                    "10.1000/source.1",
                    "Tungsten irradiation",
                    2024,
                    "A. Li",
                    "B. Wu",
                    "W",
                    str(pdf),
                ),
                (
                    2,
                    "10.1000/source.2",
                    "SiC irradiation",
                    2025,
                    "C. Xu",
                    "D. Sun",
                    "SiC",
                    None,
                ),
            ),
        )
        connection.executemany(
            "INSERT INTO data_items VALUES (?,?,?,?)",
            ((1, 1, "dose", "automatic"), (2, 2, "strength", "automatic")),
        )
        connection.executemany(
            "INSERT INTO data_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                (
                    1,
                    1,
                    0,
                    "5",
                    "辐照剂量",
                    "dpa",
                    "Tungsten irradiation",
                    "10.1000/source.1",
                    "300 K",
                    2,
                    "Table 1",
                    "dose was 5 dpa",
                    "automatic",
                ),
                (
                    2,
                    2,
                    0,
                    "420",
                    "弯曲强度",
                    "MPa",
                    "SiC irradiation",
                    "10.1000/source.2",
                    "neutron irradiated",
                    3,
                    "Table 2",
                    "strength was 420 MPa",
                    "automatic",
                ),
            ),
        )
        connection.executemany(
            "INSERT INTO quality_candidates VALUES (?,?,?,?,?,?)",
            (
                (1, 1, None, "dual_pass", 98.0, "{}"),
                (2, 2, None, "dual_pass", 97.0, "{}"),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _confirmed_repository(root: Path) -> PrivateExperimentRepository:
    repository = PrivateExperimentRepository(root)
    repository.add_project(PrivateProject("project-1", "W-Ta 项目"))
    repository.add_sample(PrivateSample("sample-1", "project-1", "W-Ta-01", "W-Ta"))
    content = b"dose,hardness\n0,3.2\n1,4.0\n"
    selected = root.parent / "measurements.csv"
    selected.write_bytes(content)
    source_file = PersonalSourceFile(
        "file-primary",
        "measurements.csv",
        "text/csv",
        hashlib.sha256(content).hexdigest(),
        len(content),
    )
    repository.register_source_file(source_file, selected)
    preview = TabularImportPreview(
        source_file=source_file,
        sheet_name="Sheet1",
        row_count=2,
        columns=(
            ColumnMapping(
                "dose", "independent", "number", True, "辐照剂量", True, "dpa", True
            ),
            ColumnMapping(
                "hardness", "dependent", "number", True, "纳米硬度", True, "GPa", True
            ),
        ),
    )
    draft = PersonalExperimentDraft(
        "run-local-1",
        "W-Ta 项目",
        "室温纳米压痕",
        "W-Ta-01",
        "纳米压痕",
        preview,
        series=(
            MeasurementSeriesDraft(
                "series-local-1", "硬度-剂量", "dose", "hardness"
            ),
        ),
        conditions={"temperature": "300 K"},
        user_note="已核验",
        confirmation_state="draft",
    )
    saved = repository.save_experiment(
        draft, project_id="project-1", sample_id="sample-1"
    )
    repository.save_experiment(
        replace(draft, confirmation_state="confirmed"),
        project_id="project-1",
        sample_id="sample-1",
        expected_revision=saved.revision,
    )
    return repository


class _OfficialService:
    def status(self):
        return PackageServiceStatus(active=False, repository_audited=False)


class _StaticSearchSource:
    def __init__(self, *, scope: str, source_id: str, entity_uid: str) -> None:
        self._scope = scope
        self._source_id = source_id
        self._entity_uid = entity_uid

    def iter_search_documents(self):
        yield {
            "schema_version": "evidence-search-document-v1",
            "entity_type": "item",
            "source_scope": self._scope,
            "source_id": self._source_id,
            "entity_uid": self._entity_uid,
            "display_title": "保留的旧搜索结果",
            "meaning_text": "硬度",
            "context_text": "恢复失败前已可用",
            "source_excerpt": "preserved evidence",
        }


def _preserved_search_session() -> FederatedSearchSession:
    official_source = _StaticSearchSource(
        scope="official", source_id="official-baseline", entity_uid="official-item-1"
    )
    private_source = _StaticSearchSource(
        scope="private", source_id="personal-baseline", entity_uid="personal-item-1"
    )
    return FederatedSearchSession(
        official=SearchSourceRegistration.official(
            official_source,
            source_id="official-baseline",
            fingerprint="official-fingerprint-1",
        ),
        private=SearchSourceRegistration.private(
            private_source,
            source_id="personal-baseline",
            fingerprint="personal-fingerprint-1",
        ),
    )


class PackageCenterRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="mac-package-runtime-")
        self.root = Path(self.temporary.name)
        self.pdf = self.root / "paper.pdf"
        self.pdf.write_bytes(b"%PDF-1.7\n%%EOF\n")
        self.database = self.root / "workspace.sqlite"
        _build_v12_database(self.database, self.pdf)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _runtime(
        self,
        name: str,
        *,
        repository: PrivateExperimentRepository,
        session: FederatedSearchSession | None = None,
        operation_history: OperationHistoryService | None = None,
    ):
        data_root = self.root / name
        broker = PackageSelectionBroker(local_volume_probe=lambda _path: True)
        destination = PackageExportDestinationBroker(
            local_volume_probe=lambda _path: True
        )
        search = session or FederatedSearchSession()
        services = DesktopPackageCenterRuntimeBuilder(
            workspace_database=self.database,
            workspace_root=self.root,
            private_repository=repository,
            search_session=search,
            operation_history=operation_history,
        )(
            package_service=_OfficialService(),
            package_broker=broker,
            destination_broker=destination,
            data_root=data_root,
            current_app_version="0.7.0-preview.1",
        )
        return services, broker, destination, search, data_root

    def test_runtime_builder_forwards_operation_history_to_jobs_and_api(self) -> None:
        history = OperationHistoryService(_OperationHistoryStore())
        services, _, _, _, _ = self._runtime(
            "operation-history-runtime",
            repository=PrivateExperimentRepository(self.root / "history-private"),
            operation_history=history,
        )
        self.assertIs(services.operation_history, history)
        job_id = services.jobs._begin(PackageOperation.TRANSFER_IMPORT)
        snapshot = history.get()
        self.assertEqual(snapshot["operations"][0]["state"], "queued")
        self.assertNotIn(job_id, json.dumps(snapshot, ensure_ascii=False))

    @staticmethod
    def _rights(plan: dict) -> dict:
        return {
            "unencrypted_ack": True,
            "unauthenticated_source_ack": True,
            "internal_use_only_ack": True,
            "paper_rights": {
                item["paper_uid"]: {
                    "allowed": True,
                    "basis": "测试中逐篇确认具有组内分享权限",
                }
                for item in plan.get("rights_requirements", [])
            },
        }

    def _wait_job(self, services, job: dict, *, timeout: float = 3.0) -> dict:
        deadline = time.monotonic() + timeout
        current = job
        while not current["terminal"] and time.monotonic() < deadline:
            time.sleep(0.005)
            current = services.jobs.get(job["job_id"])
        self.assertTrue(current["terminal"], current)
        return current

    def _export(self, services, destination_broker, *, kind, scope, selection, path):
        plan = services.export_service.plan(kind, scope, selection)
        selected = destination_broker.select(path)
        job = services.export_service.start(
            plan["plan_token"],
            self._rights(plan),
            selected.destination_token,
        )
        job = self._wait_job(services, job)
        self.assertEqual(job["stage"], "completed", job)
        self.assertTrue(path.is_file())
        self.assertTrue(path.with_name(path.name + ".sha256").is_file())
        return plan, job["result"]

    def test_wal_workspace_is_snapshotted_and_pdf_license_is_not_inferred(self) -> None:
        snapshot = create_workspace_snapshot(
            self.database, snapshot_root=self.root / "snapshot-root"
        )
        self.assertTrue(snapshot.is_file())
        self.assertFalse(Path(str(snapshot) + "-wal").exists())
        resolver = SnapshotRegisteredPdfResolver(snapshot, workspace_root=self.root)
        probe = __import__(
            "auto_research.product.package_payload_sources",
            fromlist=["EvidenceV12LiteraturePayloadSource"],
        ).EvidenceV12LiteraturePayloadSource(
            snapshot,
            pdf_resolver=resolver,
            license_verifier=NoAutomaticLiteratureLicenseVerifier(),
        )
        paper_uid = probe.resolve_selection_ids(("1",))[0]
        candidate = probe.read_selection(
            __import__(
                "auto_research.product.package_transfer_payloads",
                fromlist=["PayloadSelection"],
            ).PayloadSelection("selected", selected_ids=(paper_uid,))
        ).pdfs[0]
        self.assertEqual(candidate.source_path, self.pdf)
        self.assertFalse(candidate.license_verified)
        self.assertIsNone(candidate.license_id)

    def test_production_composition_enables_package_center_with_one_private_repository(self) -> None:
        services = create_desktop_product_services(
            data_root=self.root / "application-support",
            current_app_version="0.7.0-preview.1",
            workspace_database=self.database,
            workspace_root=self.root,
        )
        self.assertIsNotNone(services.package_center)
        self.assertIs(
            services.personal_import_service._repository_value,
            services.personal_repository,
        )
        summary = services.package_center.summary_provider.summary()
        self.assertEqual(
            summary["export_sources"]["literature"]["mode"],
            "per_plan_snapshot",
        )
        self.assertEqual(
            summary["export_sources"]["literature"]["refresh"],
            "every_plan_and_export",
        )
        self.assertNotIn(str(self.root), json.dumps(summary, ensure_ascii=False))

    def test_corrupt_installed_transfer_tree_degrades_without_blocking_old_search(self) -> None:
        data_root = self.root / "corrupt-restore-runtime"
        corrupt = (
            data_root
            / "user-transfer-packages"
            / "literature_collection"
            / "corrupt-collection"
            / "1.0.0"
        )
        corrupt.mkdir(parents=True)
        (corrupt / "unexpected.bin").write_bytes(b"invalid")
        session = _preserved_search_session()
        before = session.status()

        services, _, _, after_session, _ = self._runtime(
            "corrupt-restore-runtime",
            repository=PrivateExperimentRepository(self.root / "corrupt-private"),
            session=session,
        )

        recovery = services.summary_provider.summary()["export_sources"]["recovery"]
        self.assertEqual(recovery["state"], "degraded")
        self.assertEqual(recovery["error"]["code"], "transfer_restore_failed")
        self.assertTrue(recovery["retryable"])
        self.assertNotIn(str(self.root), json.dumps(recovery, ensure_ascii=False))
        self.assertEqual(after_session.status(), before)
        self.assertEqual(after_session.search("硬度").total, 2)

    def test_restore_activation_failure_is_reported_and_keeps_old_sources(self) -> None:
        session = _preserved_search_session()
        before = session.status()
        with patch(
            "package_center_runtime.list_installed_transfer_packages",
            return_value=(object(),),
        ), patch(
            "package_center_runtime.PackageTransferActivationService.activate",
            side_effect=RuntimeError("/private/hidden/transfer.sqlite"),
        ):
            services, _, _, after_session, _ = self._runtime(
                "activation-failure-runtime",
                repository=PrivateExperimentRepository(
                    self.root / "activation-failure-private"
                ),
                session=session,
            )

        recovery = services.summary_provider.summary()["export_sources"]["recovery"]
        self.assertEqual(recovery["state"], "degraded")
        self.assertEqual(recovery["failed_count"], 1)
        self.assertNotIn("/private", json.dumps(recovery, ensure_ascii=False))
        self.assertEqual(after_session.status(), before)
        self.assertEqual(after_session.search("硬度").total, 2)

    def test_literature_selected_filtered_all_import_search_and_restart_restore(self) -> None:
        repository = PrivateExperimentRepository(self.root / "sender-private")
        services, broker, destination, search, data_root = self._runtime(
            "sender-runtime", repository=repository
        )
        selected_path = self.root / "selected.aresearch"
        selected_plan, selected_result = self._export(
            services,
            destination,
            kind="literature_collection",
            scope="selected",
            selection=[1],
            path=selected_path,
        )
        self.assertEqual(selected_plan["paper_count"], 1)
        self.assertEqual(len(selected_plan["rights_requirements"]), 1)
        filtered_path = self.root / "filtered.aresearch"
        filtered_plan, filtered_result = self._export(
            services,
            destination,
            kind="literature_collection",
            scope="filtered",
            selection={"paper_ids": [2], "query": "SiC"},
            path=filtered_path,
        )
        self.assertEqual(filtered_plan["paper_count"], 1)
        all_plan = services.export_service.plan("literature_collection", "all", None)
        self.assertEqual(all_plan["paper_count"], 2)

        selection = broker.select(PackageSelectionSource.FILE_PICKER, selected_path)
        imported = services.import_service.start(
            selection.selection_id,
            checksum_ack=True,
            expected_sha=selected_result["package_sha256"],
            keep_conflicts=False,
        )
        imported = self._wait_job(services, imported)
        self.assertEqual(imported["stage"], "completed", imported)
        self.assertTrue(imported["result"]["search_ready"])
        self.assertGreater(search.search("辐照剂量").total, 0)

        second_selection = broker.select(
            PackageSelectionSource.FILE_PICKER, filtered_path
        )
        second_import = services.import_service.start(
            second_selection.selection_id,
            checksum_ack=True,
            expected_sha=filtered_result["package_sha256"],
            keep_conflicts=False,
        )
        second_import = self._wait_job(services, second_import)
        self.assertEqual(second_import["stage"], "completed", second_import)
        self.assertGreater(search.search("弯曲强度").total, 0)

        installed = list_installed_transfer_packages(
            data_root, kind=TransferPackageKind.LITERATURE_COLLECTION
        )
        self.assertEqual(len(installed), 2)
        transferred = open_transferred_literature_repository(
            next(
                item.install_path
                for item in installed
                if item.package_sha256 == selected_result["package_sha256"]
            ),
            next(
                item.manifest
                for item in installed
                if item.package_sha256 == selected_result["package_sha256"]
            ),
        )
        with transferred.open_pdf(
            selected_plan["rights_requirements"][0]["paper_uid"]
        ) as pdf_lease:
            self.assertEqual(pdf_lease.read(5), b"%PDF-")

        restarted = FederatedSearchSession()
        self._runtime(
            "sender-runtime",
            repository=repository,
            session=restarted,
        )
        self.assertGreater(restarted.search("辐照剂量").total, 0)
        self.assertGreater(restarted.search("弯曲强度").total, 0)

        before = restarted.search("").total
        invalid = self.root / "invalid.aresearch"
        invalid.write_bytes(b"not a package")
        invalid_selection = PackageSelectionBroker(
            local_volume_probe=lambda _path: True
        )
        # A failed import through another runtime must not disturb the restored engine.
        failed_services, failed_broker, _, failed_search, _ = self._runtime(
            "sender-runtime",
            repository=repository,
            session=restarted,
        )
        token = failed_broker.select(PackageSelectionSource.FILE_PICKER, invalid)
        failed = failed_services.import_service.start(
            token.selection_id,
            checksum_ack=True,
            expected_sha="0" * 64,
            keep_conflicts=False,
        )
        failed = self._wait_job(failed_services, failed)
        self.assertEqual(failed["stage"], "failed")
        self.assertEqual(failed_search.search("").total, before)

    def test_each_plan_uses_fresh_v12_snapshot_and_old_all_plan_becomes_stale(self) -> None:
        services, _, destination, _, _ = self._runtime(
            "fresh-plan-runtime",
            repository=PrivateExperimentRepository(self.root / "fresh-plan-private"),
        )
        old_plan = services.export_service.plan(
            "literature_collection", "all", None
        )
        self.assertEqual(old_plan["paper_count"], 2)
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "INSERT INTO papers VALUES (?,?,?,?,?,?,?,?)",
                (
                    3,
                    "10.1000/source.3",
                    "ODS steel irradiation",
                    2026,
                    "E. Zhao",
                    "F. Ma",
                    "ODS steel",
                    None,
                ),
            )
            connection.execute(
                "INSERT INTO data_items VALUES (3,3,'swelling','automatic')"
            )
            connection.execute(
                "INSERT INTO data_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    3,
                    3,
                    0,
                    "1.2",
                    "肿胀率",
                    "%",
                    "ODS steel irradiation",
                    "10.1000/source.3",
                    "573 K",
                    4,
                    "Fig. 3",
                    "swelling was 1.2 percent",
                    "automatic",
                ),
            )
            connection.execute(
                "INSERT INTO quality_candidates VALUES (3,3,NULL,'dual_pass',99.0,'{}')"
            )
            connection.commit()
        finally:
            connection.close()

        new_plan = services.export_service.plan(
            "literature_collection", "all", None
        )
        self.assertEqual(new_plan["paper_count"], 3)
        output = self.root / "stale-plan.aresearch"
        destination_token = destination.select(output).destination_token
        stale = services.export_service.start(
            old_plan["plan_token"],
            self._rights(old_plan),
            destination_token,
        )
        stale = self._wait_job(services, stale)
        self.assertEqual(stale["stage"], "failed")
        self.assertEqual(stale["error"]["code"], "package_plan_stale")
        self.assertFalse(output.exists())

    def test_personal_export_import_merges_same_repository_and_refreshes_search(self) -> None:
        sender_repository = _confirmed_repository(self.root / "private-sender")
        sender, _, destination, _, _ = self._runtime(
            "personal-sender-runtime", repository=sender_repository
        )
        package_path = self.root / "personal.aresearch"
        plan, result = self._export(
            sender,
            destination,
            kind="personal_experiments",
            scope="all",
            selection=None,
            path=package_path,
        )
        self.assertEqual(plan["paper_count"], 0)
        self.assertEqual(plan["item_count"], 1)

        receiver_repository = PrivateExperimentRepository(self.root / "private-receiver")
        receiver, receiver_broker, _, receiver_search, _ = self._runtime(
            "personal-receiver-runtime", repository=receiver_repository
        )
        selected = receiver_broker.select(
            PackageSelectionSource.FILE_PICKER, package_path
        )
        imported = receiver.import_service.start(
            selected.selection_id,
            checksum_ack=True,
            expected_sha=result["package_sha256"],
            keep_conflicts=False,
        )
        imported = self._wait_job(receiver, imported)
        self.assertEqual(imported["stage"], "completed", imported)
        self.assertTrue(imported["result"]["search_ready"])
        self.assertEqual(len(receiver_repository.list_personal_search_documents()), 1)
        self.assertGreater(receiver_search.search("纳米硬度").total, 0)

    def test_both_user_packages_restore_encrypted_receipts_and_idempotent_search(self) -> None:
        """Real archive -> distinct receiver -> restart, without production state."""
        import fitz
        from secure_history import StaticHistoryKeyProvider
        from secure_operation_history import SecureOperationHistoryStore

        document = fitz.open()
        document.new_page().insert_text((72, 72), "Synthetic sharing acceptance only.")
        document.save(self.pdf)
        document.close()

        for kind, query in (("literature_collection", "辐照剂量"),
                            ("personal_experiments", "纳米硬度")):
            with self.subTest(kind=kind):
                sender, _, destination, _, _ = self._runtime(
                    kind + "-sender", repository=_confirmed_repository(self.root / (kind + "-source")),
                )
                path = self.root / (kind + ".aresearch")
                _, exported = self._export(
                    sender, destination, kind=kind,
                    scope="selected" if kind == "literature_collection" else "all",
                    selection=[1] if kind == "literature_collection" else None, path=path,
                )
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(digest, exported["package_sha256"])
                self.assertIn(digest, path.with_name(path.name + ".sha256").read_text())
                history_path = self.root / (kind + "-history.enc")

                def history():
                    return OperationHistoryService(SecureOperationHistoryStore(
                        history_path, StaticHistoryKeyProvider(b"\x53" * 32),
                    ))

                receiver_root = self.root / (kind + "-receiver")

                def receiver_graph():
                    # Exercise the same full composition as the launcher: private
                    # search restoration belongs to PersonalImportAPI, not the
                    # package-only builder that restores literature collections.
                    # Foundation/native-volume detection is a separate installed
                    # App gate; the archive, stores, merge and search remain real.
                    with patch("package_import_service.PackageSelectionBroker",
                               side_effect=lambda: PackageSelectionBroker(
                                   local_volume_probe=lambda _: True)):
                        return create_desktop_product_services(
                            data_root=receiver_root, current_app_version="1.2.0",
                            workspace_database=self.database, workspace_root=self.root,
                            operation_history=history(),
                        )

                graph = receiver_graph()
                receiver = graph.package_center
                broker = graph.package_service.broker
                search = graph.federated_search_service.session
                selected = broker.select(PackageSelectionSource.FILE_PICKER, path)
                result = self._wait_job(receiver, receiver.import_service.start(
                    selected.selection_id, checksum_ack=True, expected_sha=digest, keep_conflicts=False,
                ))
                self.assertEqual(result["stage"], "completed", result)
                self.assertTrue(result["result"]["search_ready"])
                count = search.search(query).total
                self.assertGreater(count, 0)
                stored = history().get()
                self.assertEqual(stored["operations"][0]["state"], "completed")
                self.assertNotIn(str(self.root), json.dumps(stored))
                self.assertNotIn(b"completed", history_path.read_bytes())

                reopened = receiver_graph()
                restarted = reopened.package_center
                new_broker = reopened.package_service.broker
                restored_search = reopened.federated_search_service.session
                self.assertEqual(restored_search.search(query).total, count)
                self.assertEqual(history().get(), stored)
                # A second selection is mandatory; replay never reuses an expired picker token.
                selected = new_broker.select(PackageSelectionSource.FILE_PICKER, path)
                repeated = self._wait_job(restarted, restarted.import_service.start(
                    selected.selection_id, checksum_ack=True, expected_sha=digest, keep_conflicts=False,
                ))
                self.assertEqual(repeated["stage"], "completed", repeated)
                self.assertEqual(restored_search.search(query).total, count)


if __name__ == "__main__":
    unittest.main()
