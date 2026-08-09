from __future__ import annotations

import unittest

from auto_research.product.package_job_contract import (
    PackageJobContractError,
    PackageJobError,
    PackageJobStage,
    PackageOperation,
    advance_package_job,
    begin_package_job,
    fail_package_job,
    package_job_stages,
)


class PackageJobContractTests(unittest.TestCase):
    def test_official_import_preserves_existing_desktop_stage_contract(self) -> None:
        self.assertEqual(
            [stage.value for stage, _ in package_job_stages("official_import")],
            [
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
            ],
        )

    def test_each_operation_advances_monotonically_to_completion(self) -> None:
        for operation in PackageOperation:
            with self.subTest(operation=operation.value):
                progress = begin_package_job(operation)
                values = [progress.progress]
                stages = package_job_stages(operation)
                for stage, _ in stages[1:]:
                    progress = advance_package_job(
                        progress,
                        stage,
                        outcome="done" if stage is PackageJobStage.COMPLETED else None,
                    )
                    values.append(progress.progress)
                self.assertEqual(values, sorted(values))
                self.assertEqual(progress.outcome, "done")
                self.assertTrue(progress.terminal)
                self.assertEqual(progress.public_dict()["schema"], "package-job-v1")

    def test_job_rejects_skipped_or_terminal_transitions(self) -> None:
        progress = begin_package_job(PackageOperation.TRANSFER_IMPORT)
        with self.assertRaises(PackageJobContractError):
            advance_package_job(progress, PackageJobStage.VERIFY_CHECKSUMS)
        for stage, _ in package_job_stages(PackageOperation.TRANSFER_IMPORT)[1:]:
            progress = advance_package_job(progress, stage)
        with self.assertRaises(PackageJobContractError):
            advance_package_job(progress, PackageJobStage.COMPLETED)

    def test_failure_keeps_last_progress_and_uses_path_free_dto(self) -> None:
        progress = begin_package_job(PackageOperation.TRANSFER_EXPORT)
        progress = advance_package_job(progress, PackageJobStage.PLAN)
        error = PackageJobError(
            code="transfer_pdf_rights_required",
            safe_message="每篇 PDF 都必须明确通过传输权利检查",
            stage=PackageJobStage.PLAN,
            retryable=False,
        )
        failed = fail_package_job(progress, error)
        self.assertEqual(failed.progress, 10)
        self.assertEqual(
            failed.public_dict()["error"],
            {
                "schema": "package-job-error-v1",
                "code": "transfer_pdf_rights_required",
                "message": "每篇 PDF 都必须明确通过传输权利检查",
                "stage": "plan",
                "retryable": False,
            },
        )
        self.assertNotIn("path", str(failed.public_dict()).casefold())


if __name__ == "__main__":
    unittest.main()
