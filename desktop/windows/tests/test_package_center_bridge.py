from __future__ import annotations

import sys
import unittest
from pathlib import Path


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT))
try:
    import package_center_bridge as MODULE
finally:
    sys.path.pop(0)


class _Summary:
    def summary(self):
        return {"schema": "package-center-status-v1", "capabilities": {}}


class _Center:
    def inspect(self, token):
        return {"schema": "package-summary-v1", "selection": token}


class _Export:
    def plan(self, kind, scope, selection):
        return {"schema": "package-plan-v1", "kind": kind, "scope": scope, "selection": selection}

    def start(self, plan_token, confirmations, destination_token):
        return {
            "schema": "package-job-v1",
            "job_id": "job_export_0123456789",
            "stage": "queued",
            "terminal": False,
            "plan_token": plan_token,
            "rights": confirmations,
            "destination_token": destination_token,
        }


class _Import:
    def start(self, token, **kwargs):
        return {
            "schema": "package-job-v1",
            "job_id": "job_import_0123456789",
            "stage": "queued",
            "terminal": False,
            "selection_token": token,
            **kwargs,
        }


class _Jobs:
    def get(self, job_id):
        return {
            "schema": "package-job-v1",
            "job_id": job_id,
            "stage": "completed",
            "terminal": True,
        }


class PackageCenterBridgeTests(unittest.TestCase):
    def test_bridge_does_not_import_publisher_side_runtime_api(self):
        source = (WINDOWS_ROOT / "package_center_bridge.py").read_text(encoding="utf-8")
        self.assertNotIn("auto_research.product.runtime_api", source)
        self.assertNotIn("evidence_v12_export", source)

    def adapter(self):
        return MODULE.WindowsPackageCenterBridgeAdapter(
            summary_provider=_Summary(),
            center=_Center(),
            export_service=_Export(),
            import_service=_Import(),
            jobs=_Jobs(),
        )

    def test_adapter_only_forwards_shared_services_and_queued_jobs(self):
        adapter = self.adapter()
        self.assertEqual(adapter.status()["schema"], "package-center-status-v1")
        self.assertEqual(adapter.inspect("selection_0123456789")["selection"], "selection_0123456789")
        plan = adapter.plan_export("literature_collection", "all", None)
        self.assertEqual(plan["schema"], "package-plan-v1")
        export = adapter.start_export(
            "plan_token_0123456789",
            {
                "unencrypted_ack": True,
                "unauthenticated_source_ack": True,
                "internal_use_only_ack": True,
                "paper_rights": {},
            },
            "destination_0123456789",
        )
        self.assertEqual(export["stage"], "queued")
        imported = adapter.start_import(
            "selection_0123456789",
            checksum_ack=True,
            expected_sha="a" * 64,
            keep_conflicts=True,
        )
        self.assertEqual(imported["stage"], "queued")
        self.assertTrue(adapter.get_job(imported["job_id"])["terminal"])

    def test_unavailable_runtime_fails_closed_with_path_free_code(self):
        unavailable = MODULE.UnavailablePackageCenterBridge()
        with self.assertRaises(Exception) as raised:
            unavailable.status()
        payload, status = MODULE.WindowsPackageCenterBridgeAdapter.public_error(
            raised.exception
        )
        self.assertEqual(payload["code"], "package_center_unavailable")
        self.assertEqual(int(status), 503)
        self.assertNotIn("path", str(payload).casefold())


if __name__ == "__main__":
    unittest.main()
