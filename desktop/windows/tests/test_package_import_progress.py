from __future__ import annotations

import sys
import unittest
from pathlib import Path


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT))
try:
    import package_import_progress as MODULE
    import package_input as INPUT
finally:
    sys.path.pop(0)


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def opaque_handle() -> INPUT.PackageInputHandle:
    return INPUT.PackageInputHandle(
        handle_id="opaque-handle-1234567890",
        source="file-picker",
        broker_token=object(),
        identity=INPUT.LocalFileIdentity(
            r"C:\private\never-expose.aresearch", 100, 1, 2, 3
        ),
    )


class SuccessfulFakeImporter:
    def __init__(self) -> None:
        self.snapshots = []

    def run(self, handle, progress) -> None:
        self.asserted_handle = handle
        progress.update_bytes(0, 100)
        for index, stage in enumerate(MODULE.ACTIVE_STAGES[1:], start=1):
            progress.update_bytes(min(index * 10, 100))
            progress.advance(stage)
            self.snapshots.append(progress.snapshot().as_public_dict())


class PackageImportProgressTests(unittest.TestCase):
    def test_stable_stages_map_to_five_user_phases(self) -> None:
        expected = (
            "queued",
            "snapshot_source",
            "verify_archive",
            "verify_signature",
            "verify_checksums",
            "extract_staging",
            "audit_repository",
            "activate",
            "refresh_readiness",
            "completed",
        )
        self.assertEqual(tuple(stage.value for stage in MODULE.ACTIVE_STAGES), expected)
        self.assertEqual(
            {value[1] for value in MODULE.UI_PHASES.values()},
            {"检查文件", "验证资料包", "安装", "准备离线搜索", "完成"},
        )
        self.assertTrue(
            {
                "package_too_large",
                "package_invalid_or_corrupt",
                "package_unsafe",
                "package_untrusted",
                "package_signature_invalid",
                "package_incompatible_app",
                "package_incompatible_schema",
                "package_rights_invalid",
                "package_install_conflict",
                "package_install_failed",
                "repository_audit_failed",
                "active_state_invalid",
                "package_busy",
                "package_selection_invalid",
                "package_job_transition_invalid",
            }.issubset(MODULE.ERRORS)
        )

    def test_successful_fake_import_has_separate_monotonic_progress_and_path_free_summary(self) -> None:
        handle = opaque_handle()
        importer = SuccessfulFakeImporter()
        job = MODULE.PackageImportProgressCoordinator().run(handle, importer)
        snapshots = importer.snapshots
        stage_fractions = [item["stage_progress"]["fraction"] for item in snapshots]
        byte_fractions = [item["byte_progress"]["fraction"] for item in snapshots]
        self.assertEqual(stage_fractions, sorted(stage_fractions))
        self.assertEqual(byte_fractions, sorted(byte_fractions))
        final = job.snapshot().as_public_dict()
        self.assertEqual(final["stage"], "completed")
        self.assertEqual(final["ui_phase_label"], "完成")
        self.assertNotIn(r"C:\private", str(final))
        self.assertNotIn("path", str(final).casefold())

    def test_byte_total_and_progress_cannot_move_backwards_or_merge_with_stage_progress(self) -> None:
        job = MODULE.PackageImportProgressJob(opaque_handle())
        job.update_bytes(10, 100)
        with self.assertRaises(MODULE.PackageProgressError):
            job.update_bytes(9)
        with self.assertRaises(MODULE.PackageProgressError):
            job.update_bytes(20, 101)
        public = job.snapshot().as_public_dict()
        self.assertIn("stage_progress", public)
        self.assertIn("byte_progress", public)
        self.assertNotEqual(public["stage_progress"], public["byte_progress"])

    def test_cancel_is_allowed_only_before_activate(self) -> None:
        pre_activate = MODULE.PackageImportProgressJob(opaque_handle())
        for stage in MODULE.ACTIVE_STAGES[1:MODULE.ACTIVATE_INDEX]:
            pre_activate.advance(stage)
        self.assertTrue(pre_activate.request_cancel())
        with self.assertRaises(MODULE.PackageImportCancelled):
            pre_activate.advance(MODULE.PackageJobStage.ACTIVATE)

        activated = MODULE.PackageImportProgressJob(opaque_handle())
        for stage in MODULE.ACTIVE_STAGES[1 : MODULE.ACTIVATE_INDEX + 1]:
            activated.advance(stage)
        self.assertFalse(activated.cancel_allowed)
        self.assertFalse(activated.request_cancel())

    def test_short_wait_threshold_shows_stable_reason_without_path(self) -> None:
        clock = Clock()
        job = MODULE.PackageImportProgressJob(
            opaque_handle(), clock=clock, wait_threshold_seconds=2.0
        )
        job.advance(MODULE.PackageJobStage.SNAPSHOT_SOURCE)
        clock.value = 1.9
        self.assertFalse(job.snapshot().waiting)
        clock.value = 2.1
        snapshot = job.snapshot()
        self.assertTrue(snapshot.waiting)
        self.assertIn("受保护的临时区域", snapshot.waiting_reason)
        self.assertNotIn(r"C:\private", snapshot.waiting_reason)

    def test_arbitrary_importer_exception_is_mapped_without_leaking_path(self) -> None:
        class FailingImporter:
            def run(self, handle, progress):
                progress.advance(MODULE.PackageJobStage.SNAPSHOT_SOURCE)
                raise RuntimeError(r"failed at C:\private\secret.aresearch")

        job = MODULE.PackageImportProgressCoordinator().run(
            opaque_handle(), FailingImporter()
        )
        public = job.snapshot().as_public_dict()
        self.assertEqual(public["stage"], "failed")
        self.assertEqual(public["error"]["code"], "package_import_failed")
        self.assertEqual(public["error"]["stage"], "snapshot_source")
        self.assertNotIn(r"C:\private", str(public))
        self.assertTrue(public["error"]["retryable"])

    def test_activity_gate_rejects_second_import(self) -> None:
        gate = MODULE.PackageImportActivityGate()
        gate.acquire()
        try:
            with self.assertRaises(MODULE.PackageProgressError) as busy:
                gate.acquire()
            self.assertEqual(busy.exception.code, "import_busy")
        finally:
            gate.release()


if __name__ == "__main__":
    unittest.main()
