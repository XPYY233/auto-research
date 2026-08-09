from __future__ import annotations

import sys
import unittest
from pathlib import Path


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT))
try:
    import package_import_bridge as BRIDGE
    import package_import_service as SERVICE
    import package_input as INPUT
finally:
    sys.path.pop(0)


class FakeService:
    def __init__(self) -> None:
        self.readiness = SERVICE.OfflineReadiness(
            False, "official_package_required", "请先导入官方资料包。"
        )

    def refresh_startup_readiness(self):
        return self.readiness

    def run(self, handle, progress):
        for stage in list(__import__("package_import_progress").ACTIVE_STAGES)[1:]:
            progress.advance(stage)
        self.readiness = SERVICE.OfflineReadiness(
            True,
            "offline_ready",
            "可以离线搜索。",
            {
                "package_id": "official",
                "package_version": "1.0.0",
                "content_fingerprint": "b" * 64,
            },
        )


def opaque_handle():
    return INPUT.PackageInputHandle(
        handle_id="opaque-handle-1234567890",
        source="drag-drop",
        broker_token=object(),
        identity=INPUT.LocalFileIdentity(r"C:\secret\input.aresearch", 1, 2, 3, 4),
    )


class PackageImportBridgeTests(unittest.TestCase):
    def test_bridge_returns_only_public_job_and_readiness(self) -> None:
        service = FakeService()
        bridge = BRIDGE.PackageImportBridgeAdapter(service)
        self.assertFalse(bridge.startup_readiness()["offline_ready"])
        result = bridge.import_package(opaque_handle())
        self.assertEqual(result["job"]["stage"], "completed")
        self.assertTrue(result["readiness"]["offline_ready"])
        self.assertNotIn(r"C:\secret", str(result))
        self.assertEqual(set(result), {"job", "readiness"})

    def test_registered_selection_runs_as_pollable_shared_ui_job(self) -> None:
        service = FakeService()
        scheduled = []
        bridge = BRIDGE.PackageImportBridgeAdapter(
            service,
            scheduler=scheduled.append,
            job_id_factory=lambda: "package-job-0123456789abcdef",
        )
        handle = opaque_handle()
        selection = bridge.register_selection(handle)
        started = bridge.start_import(selection["selection_id"])
        self.assertEqual(started["stage"], "queued")
        self.assertFalse(started["terminal"])
        scheduled[0]()
        completed = bridge.get_job(started["job_id"])
        self.assertEqual(completed["stage"], "completed")
        self.assertEqual(completed["progress"], 100)
        self.assertTrue(completed["terminal"])
        with self.assertRaises(BRIDGE.PackageBridgeError):
            bridge.start_import(selection["selection_id"])


if __name__ == "__main__":
    unittest.main()
