from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))

from package_job_state import (  # noqa: E402
    PACKAGE_JOB_PROGRESS,
    PACKAGE_JOB_SEQUENCE,
    PackageImportJobCoordinator,
    PackageJobOperation,
    PackageJobStage,
    PackageJobStateError,
    map_package_error,
)


class PackageJobStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="auto-research-package-job-state-test-"
        )
        self.root = Path(self.temporary.name)
        self.next_job_number = 0

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def coordinator(self) -> PackageImportJobCoordinator:
        def job_id_factory() -> str:
            self.next_job_number += 1
            return f"package_job_{self.next_job_number:016d}"

        return PackageImportJobCoordinator(job_id_factory=job_id_factory)

    def test_stage_contract_is_fixed_and_progress_is_monotonic(self) -> None:
        self.assertEqual(
            tuple(stage.value for stage in PACKAGE_JOB_SEQUENCE),
            (
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
            ),
        )
        progress = [PACKAGE_JOB_PROGRESS[stage] for stage in PACKAGE_JOB_SEQUENCE]
        self.assertEqual(progress, sorted(progress))
        self.assertEqual(progress[0], 0)
        self.assertEqual(progress[-1], 100)
        self.assertEqual(PackageJobStage.FAILED.value, "failed")

    def test_full_import_path_advances_one_stage_at_a_time(self) -> None:
        coordinator = self.coordinator()
        current = coordinator.begin_import("selection_0123456789abcdef")
        observed = [current.stage]
        observed_progress = [current.progress]
        for stage in PACKAGE_JOB_SEQUENCE[1:]:
            current = coordinator.advance(current.job_id, stage)
            observed.append(current.stage)
            observed_progress.append(current.progress)

        self.assertEqual(tuple(observed), PACKAGE_JOB_SEQUENCE)
        self.assertEqual(observed_progress, sorted(observed_progress))
        self.assertTrue(current.terminal)
        self.assertEqual(current.progress, 100)
        self.assertIsNone(coordinator.active_job_id)

    def test_regression_skip_and_terminal_mutation_are_rejected(self) -> None:
        coordinator = self.coordinator()
        job = coordinator.begin_import("selection_0123456789abcdef")
        with self.assertRaises(PackageJobStateError) as skipped:
            coordinator.advance(job.job_id, PackageJobStage.VERIFY_ARCHIVE)
        self.assertEqual(skipped.exception.code, "package_job_transition_invalid")

        job = coordinator.advance(job.job_id, PackageJobStage.SNAPSHOT_SOURCE)
        with self.assertRaises(PackageJobStateError):
            coordinator.advance(job.job_id, PackageJobStage.QUEUED)

        for stage in PACKAGE_JOB_SEQUENCE[2:]:
            job = coordinator.advance(job.job_id, stage)
        with self.assertRaises(PackageJobStateError):
            coordinator.advance(job.job_id, PackageJobStage.FAILED)
        with self.assertRaises(PackageJobStateError):
            coordinator.fail(job.job_id, "install_failed")

    def test_only_one_import_can_be_active_and_terminal_releases_lock(self) -> None:
        coordinator = self.coordinator()
        first = coordinator.begin_import("selection_0123456789abcdef")
        with self.assertRaises(PackageJobStateError) as busy:
            coordinator.begin_import("selection_fedcba9876543210")
        self.assertEqual(busy.exception.code, "package_busy")
        self.assertTrue(busy.exception.error.retryable)

        failed = coordinator.fail(first.job_id, "source_changed")
        self.assertEqual(failed.stage, PackageJobStage.FAILED)
        self.assertTrue(failed.terminal)
        second = coordinator.begin_import("selection_fedcba9876543210")
        self.assertNotEqual(first.job_id, second.job_id)

    def test_rollback_uses_the_same_single_operation_lock(self) -> None:
        coordinator = self.coordinator()
        rollback = coordinator.begin_rollback()
        self.assertEqual(rollback.operation, PackageJobOperation.ROLLBACK)
        with self.assertRaises(PackageJobStateError) as busy:
            coordinator.begin_import("selection_0123456789abcdef")
        self.assertEqual(busy.exception.code, "package_busy")
        failed = coordinator.fail(rollback.job_id, "missing_target")
        self.assertEqual(failed.error.code, "rollback_target_missing")
        self.assertIsNone(coordinator.active_job_id)

    def test_selection_id_is_opaque_and_paths_are_rejected_without_io(self) -> None:
        sample = self.root / "sample.aresearch"
        sample.write_bytes(b"not-a-package")
        before = sample.read_bytes()
        coordinator = self.coordinator()

        invalid = (
            str(sample),
            "file:///tmp/sample.aresearch",
            r"C:\\Users\\person\\sample.aresearch",
            "../sample.aresearch",
            "short",
            "selection.with.periods",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(PackageJobStateError) as raised:
                    coordinator.begin_import(value)
                self.assertEqual(raised.exception.code, "package_selection_invalid")
        self.assertEqual(sample.read_bytes(), before)

    def test_public_dto_never_exposes_selection_or_paths(self) -> None:
        coordinator = self.coordinator()
        selection_id = "selection_private_0123456789"
        job = coordinator.begin_import(selection_id)
        public = job.public_dict()
        serialized = json.dumps(public, ensure_ascii=False)
        self.assertNotIn(selection_id, serialized)
        self.assertNotIn("selection_id", serialized)
        self.assertNotIn("path", serialized.lower())
        self.assertEqual(
            set(public),
            {"job_id", "operation", "stage", "progress", "terminal"},
        )

    def test_failure_keeps_progress_and_reports_the_actual_failure_stage(self) -> None:
        coordinator = self.coordinator()
        job = coordinator.begin_import("selection_0123456789abcdef")
        job = coordinator.advance(job.job_id, PackageJobStage.SNAPSHOT_SOURCE)
        job = coordinator.advance(job.job_id, PackageJobStage.VERIFY_ARCHIVE)
        before_progress = job.progress
        failed = coordinator.fail(job.job_id, "unsafe_archive")

        self.assertEqual(failed.stage, PackageJobStage.FAILED)
        self.assertEqual(failed.progress, before_progress)
        self.assertEqual(failed.error.code, "package_unsafe")
        self.assertEqual(failed.error.stage, PackageJobStage.VERIFY_ARCHIVE)
        self.assertFalse(failed.error.retryable)
        self.assertIsNone(coordinator.active_job_id)

    def test_error_mapping_is_stable_and_does_not_echo_internal_details(self) -> None:
        cases = (
            ("package_size", PackageJobStage.SNAPSHOT_SOURCE, "package_too_large", False),
            ("not_package", PackageJobStage.VERIFY_ARCHIVE, "package_invalid_or_corrupt", False),
            ("unsafe_path", PackageJobStage.VERIFY_ARCHIVE, "package_unsafe", False),
            ("untrusted_signer", PackageJobStage.VERIFY_SIGNATURE, "package_untrusted", False),
            ("invalid_signature", PackageJobStage.VERIFY_SIGNATURE, "package_signature_invalid", False),
            ("incompatible_app", PackageJobStage.VERIFY_ARCHIVE, "package_incompatible_app", False),
            ("incompatible_schema", PackageJobStage.VERIFY_ARCHIVE, "package_incompatible_schema", False),
            ("invalid_rights", PackageJobStage.VERIFY_CHECKSUMS, "package_rights_invalid", False),
            ("install_conflict", PackageJobStage.EXTRACT_STAGING, "package_install_conflict", False),
            ("install_failed", PackageJobStage.EXTRACT_STAGING, "package_install_failed", True),
            ("audit_schema", PackageJobStage.AUDIT_REPOSITORY, "repository_audit_failed", False),
            ("invalid_active_state", PackageJobStage.REFRESH_READINESS, "active_state_invalid", False),
            ("missing_target", PackageJobStage.QUEUED, "rollback_target_missing", False),
        )
        for source, stage, expected, retryable in cases:
            with self.subTest(source=source):
                error = map_package_error(source, stage)
                self.assertEqual(error.code, expected)
                self.assertEqual(error.stage, stage)
                self.assertEqual(error.retryable, retryable)
                serialized = json.dumps(error.public_dict(), ensure_ascii=False)
                self.assertNotIn(source, error.message)
                self.assertNotIn("/", serialized)

    def test_public_error_codes_are_idempotent(self) -> None:
        public_codes = (
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
            "rollback_target_missing",
            "rollback_failed",
            "package_busy",
        )
        for code in public_codes:
            with self.subTest(code=code):
                error = map_package_error(code, PackageJobStage.AUDIT_REPOSITORY)
                self.assertEqual(error.code, code)

    def test_unknown_errors_fail_closed_by_stage_and_operation(self) -> None:
        audit = map_package_error("future_audit_code", PackageJobStage.AUDIT_REPOSITORY)
        rollback = map_package_error(
            "future_rollback_code",
            PackageJobStage.ACTIVATE,
            operation=PackageJobOperation.ROLLBACK,
        )
        generic = map_package_error("future_import_code", PackageJobStage.ACTIVATE)
        self.assertEqual(audit.code, "repository_audit_failed")
        self.assertEqual(rollback.code, "rollback_failed")
        self.assertTrue(rollback.retryable)
        self.assertEqual(generic.code, "package_install_failed")

    def test_missing_and_malformed_job_ids_return_stable_errors(self) -> None:
        coordinator = self.coordinator()
        for job_id in ("missing", "/tmp/package-job", "package_job_9999999999999999"):
            with self.subTest(job_id=job_id):
                with self.assertRaises(PackageJobStateError) as raised:
                    coordinator.get(job_id)
                self.assertEqual(raised.exception.code, "package_job_not_found")


if __name__ == "__main__":
    unittest.main()
