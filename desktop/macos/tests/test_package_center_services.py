from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = Path(__file__).resolve().parents[3] / "src"
for path in (DESKTOP_ROOT, SOURCE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from auto_research.product.package_center_models import (  # noqa: E402
    MaterializedPayload,
    PackageCenterError,
    PayloadPlanCandidate,
)
from desktop_product_services import create_desktop_product_services  # noqa: E402
from package_center_services import (  # noqa: E402
    OfficialPackageCenterSummary,
    create_desktop_package_center_services,
)
from package_export_destination_broker import PackageExportDestinationBroker  # noqa: E402
from package_import_service import PackageServiceStatus  # noqa: E402
from package_selection_broker import (  # noqa: E402
    PackageSelectionBroker,
    PackageSelectionSource,
)


@dataclass(frozen=True)
class _Installed:
    package_id: str
    package_version: str
    active: bool

    def public_dict(self):
        return {
            "schema": "installed-official-package-v1",
            "package_id": self.package_id,
            "package_version": self.package_version,
            "content_fingerprint": "a" * 64,
            "installed_at": "2026-08-09T00:00:00Z",
            "active": self.active,
            "audit_status": "ready",
            "error_code": None,
        }


class _OfficialService:
    def status(self):
        return PackageServiceStatus(
            active=True,
            repository_audited=True,
            package_id="auto-research-internal-evidence",
            package_version="0.2.0-preview.1",
            content_fingerprint="a" * 64,
        )


class _Planner:
    def __init__(self) -> None:
        self.fingerprint = "b" * 64

    def plan(self, *, kind, scope, selection):
        return PayloadPlanCandidate(
            package_id="user-literature-collection",
            package_version="2026.08.09",
            content_fingerprint=self.fingerprint,
            estimated_bytes=100,
            item_count=1,
            paper_count=1,
            payload={"kind": kind, "scope": scope, "selection": selection},
        )

    def current_content_fingerprint(self, _candidate):
        return self.fingerprint

    def materialize(self, candidate, *, rights_confirmations):
        return MaterializedPayload(
            {"candidate": candidate, "rights": rights_confirmations},
            lambda: None,
        )


class _Activator:
    def __init__(self) -> None:
        self.calls = []

    def activate(self, imported, *, keep_conflicts):
        self.calls.append((imported, keep_conflicts))
        result = dict(imported)
        result.update(
            {
                "schema": "package-summary-v1",
                "activation_outcome": "activated",
                "search_ready": True,
                "outcome": "imported",
            }
        )
        return result

class PackageCenterServicesTests(unittest.TestCase):
    def test_summary_uses_injected_audited_lister_and_has_no_paths(self) -> None:
        seen = []

        def list_installed(**kwargs):
            seen.append(kwargs)
            return (
                _Installed("auto-research-internal-evidence", "0.2.0-preview.1", True),
                _Installed("auto-research-internal-evidence", "0.1.0-preview.1", False),
            )

        summary = OfficialPackageCenterSummary(
            package_service=_OfficialService(),
            data_root=Path("/private/not-public"),
            current_app_version="0.6.1-preview.1",
            installed_lister=list_installed,
        ).summary()
        self.assertEqual(len(summary["official"]["installed_versions"]), 2)
        self.assertTrue(summary["official"]["current"]["active"])
        self.assertEqual(summary["transfer_policy"]["integrity"], "sha256-only")
        self.assertNotIn("/private/not-public", str(summary))
        self.assertEqual(seen[0]["current_app_version"], "0.6.1-preview.1")

    def test_summary_rejects_path_leaking_injected_catalog(self) -> None:
        class _Unsafe:
            def public_dict(self):
                return {
                    "schema": "installed-official-package-v1",
                    "package_id": "official",
                    "package_version": "1.0.0",
                    "content_fingerprint": "a" * 64,
                    "installed_at": "2026-08-09T00:00:00Z",
                    "active": True,
                    "audit_status": "ready",
                    "error_code": None,
                    "install_path": "/private/unsafe",
                }

        provider = OfficialPackageCenterSummary(
            package_service=_OfficialService(),
            data_root=Path("/private/not-public"),
            current_app_version="0.6.1-preview.1",
            installed_lister=lambda **_kwargs: (_Unsafe(),),
        )
        with self.assertRaises(PackageCenterError) as raised:
            provider.summary()
        self.assertEqual(raised.exception.code, "package_result_unsafe")

    def test_default_official_catalog_helper_handles_empty_app_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mac-package-catalog-empty-") as raw:
            summary = OfficialPackageCenterSummary(
                package_service=_OfficialService(),
                data_root=Path(raw),
                current_app_version="0.6.1-preview.1",
            ).summary()
        self.assertEqual(summary["official"]["installed_versions"], [])

    def test_factory_adapts_opaque_tokens_without_exposing_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mac-package-center-services-") as raw:
            root = Path(raw)
            package_path = root / "incoming.aresearch"
            package_path.write_bytes(b"transfer")
            broker = PackageSelectionBroker(local_volume_probe=lambda _path: True)
            selection = broker.select(PackageSelectionSource.FILE_PICKER, package_path)
            destination = PackageExportDestinationBroker(
                local_volume_probe=lambda _path: True
            )
            destination_snapshot = destination.select(root / "out.aresearch")
            inspected_paths = []
            exported_paths = []
            imported_paths = []

            def inspector(path):
                inspected_paths.append(path)
                return {
                    "schema": "package-summary-v1",
                    "package_kind": "literature_collection",
                    "package_id": "user-literature-collection",
                    "package_version": "2026.08.09",
                    "package_sha256": "c" * 64,
                }

            def exporter(materialized, path, *, unencrypted_ack):
                exported_paths.append(path)
                self.assertTrue(unencrypted_ack)
                return {
                    "schema": "package-summary-v1",
                    "package_kind": "literature_collection",
                    "package_id": "user-literature-collection",
                    "package_version": "2026.08.09",
                    "package_sha256": "d" * 64,
                    "outcome": "exported",
                }

            def importer(
                path,
                *,
                expected_kind,
                expected_package_sha256,
                checksum_ack,
                require_structured_payload,
            ):
                imported_paths.append(path)
                self.assertEqual(expected_kind, "literature_collection")
                self.assertEqual(expected_package_sha256, "c" * 64)
                self.assertTrue(checksum_ack)
                self.assertTrue(require_structured_payload)
                return {
                    "schema": "package-summary-v1",
                    "package_kind": expected_kind,
                    "package_id": "user-literature-collection",
                    "package_version": "2026.08.09",
                    "package_sha256": expected_package_sha256,
                    "outcome": "imported",
                }

            services = create_desktop_package_center_services(
                package_service=_OfficialService(),
                package_broker=broker,
                destination_broker=destination,
                data_root=root / "application-support",
                current_app_version="0.6.1-preview.1",
                payload_planner=_Planner(),
                transfer_inspector=inspector,
                transfer_exporter=exporter,
                transfer_importer=importer,
                transfer_activator=_Activator(),
                installed_lister=lambda **_kwargs: (),
            )
            inspected = services.center.inspect(selection.selection_id)
            self.assertTrue(inspected["checksum_ack_required"])
            plan = services.export_service.plan(
                "literature_collection", "selected", ["paper-1"]
            )
            exported = services.export_service.start(
                plan["plan_token"],
                {
                    "unencrypted_ack": True,
                    "unauthenticated_source_ack": True,
                    "internal_use_only_ack": True,
                    "paper_rights": {},
                },
                destination_snapshot.destination_token,
            )
            self.assertEqual(exported["outcome"], "exported", exported)
            imported = services.import_service.start(
                selection.selection_id,
                checksum_ack=True,
                expected_sha="c" * 64,
                keep_conflicts=False,
            )
            self.assertEqual(imported["outcome"], "imported", imported)
            canonical_package = package_path.resolve()
            canonical_output = root.resolve() / "out.aresearch"
            self.assertEqual(inspected_paths, [canonical_package, canonical_package])
            self.assertEqual(exported_paths, [canonical_output])
            self.assertEqual(imported_paths, [canonical_package])
            self.assertNotIn(str(root), str(imported))

    def test_factory_returns_queued_export_and_job_can_be_polled(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mac-package-center-background-") as raw:
            root = Path(raw)
            callbacks = []
            destination = PackageExportDestinationBroker(
                local_volume_probe=lambda _path: True
            )
            destination_snapshot = destination.select(root / "out.aresearch")
            services = create_desktop_package_center_services(
                package_service=_OfficialService(),
                package_broker=PackageSelectionBroker(
                    local_volume_probe=lambda _path: True
                ),
                destination_broker=destination,
                data_root=root / "application-support",
                current_app_version="0.7.0-preview.1",
                payload_planner=_Planner(),
                transfer_inspector=lambda _path: {},
                transfer_exporter=lambda _payload, _path, **_kwargs: {
                    "schema": "package-summary-v1",
                    "package_kind": "literature_collection",
                    "package_id": "user-literature-collection",
                    "package_version": "2026.08.09",
                    "package_sha256": "d" * 64,
                    "outcome": "exported",
                },
                transfer_importer=lambda *_args, **_kwargs: {},
                transfer_activator=_Activator(),
                job_submitter=callbacks.append,
                installed_lister=lambda **_kwargs: (),
            )
            plan = services.export_service.plan(
                "literature_collection", "selected", ["paper-1"]
            )
            queued = services.export_service.start(
                plan["plan_token"],
                {
                    "unencrypted_ack": True,
                    "unauthenticated_source_ack": True,
                    "internal_use_only_ack": True,
                    "paper_rights": {},
                },
                destination_snapshot.destination_token,
            )
            self.assertEqual(queued["stage"], "queued")
            self.assertFalse(queued["terminal"])
            self.assertEqual(len(callbacks), 1)

            callbacks.pop()()
            completed = services.jobs.get(queued["job_id"])
            self.assertEqual(completed["stage"], "completed")
            self.assertTrue(completed["terminal"])

    def test_desktop_product_composition_accepts_one_package_center_builder(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mac-package-center-composition-") as raw:
            calls = []
            marker = SimpleNamespace(api=object())

            def builder(**kwargs):
                calls.append(kwargs)
                return marker

            services = create_desktop_product_services(
                data_root=Path(raw),
                current_app_version="0.6.1-preview.1",
                package_center_builder=builder,
            )
            self.assertIs(services.package_center, marker)
            self.assertIs(calls[0]["package_broker"], services.package_service.broker)
            self.assertIs(
                calls[0]["destination_broker"],
                services.package_export_destination_broker,
            )


if __name__ == "__main__":
    unittest.main()
