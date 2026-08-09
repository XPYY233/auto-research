from __future__ import annotations

import os
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from auto_research.product.internal_preview_builder import (
    build_internal_preview_package,
    initialize_preview_signing_key,
    InternalPreviewBuildReport,
    load_approved_paper_uids,
    load_preview_signing_key,
    main,
    normalize_approved_paper_uids,
    preview_public_key_base64,
)
from auto_research.product.portable_repository import stable_paper_uid


SYNTHETIC_UID = stable_paper_uid(
    doi="10.1000/synthetic",
    title="Synthetic package gate paper",
    year=2026,
    first_author="A Researcher",
)


class InternalPreviewBuilderTests(unittest.TestCase):
    def test_signing_key_is_persistent_private_and_public_key_is_stable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="preview-signing-key-test-") as temporary:
            path = Path(temporary) / "maintainer" / "preview.key"
            first = initialize_preview_signing_key(path)
            second = load_preview_signing_key(path)
            self.assertEqual(
                preview_public_key_base64(first), preview_public_key_base64(second)
            )
            self.assertEqual(len(path.read_bytes()), 32)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            with self.assertRaises(RuntimeError):
                initialize_preview_signing_key(path)

    def test_signing_key_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory(prefix="preview-signing-key-test-") as temporary:
            root = Path(temporary)
            target = root / "target.key"
            target.write_bytes(b"x" * 32)
            link = root / "preview.key"
            link.symlink_to(target)
            with self.assertRaises(RuntimeError):
                load_preview_signing_key(link)

    def test_source_hash_mismatch_cleans_staging_and_leaves_no_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="preview-builder-test-") as temporary:
            root = Path(temporary)
            output = root / "published"
            with patch(
                "auto_research.product.internal_preview_builder.plan_evidence_v12_export",
                return_value=SimpleNamespace(private_source_sha256="b" * 64),
            ):
                with self.assertRaises(RuntimeError):
                    build_internal_preview_package(
                        source_snapshot=root / "source.sqlite",
                        output_directory=output,
                        signing_key_path=root / "missing.key",
                        expected_source_sha256="a" * 64,
                        approved_paper_uids={"paper_0123456789abcdef0123456789abcdef"},
                    )
            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(".published-*")), [])

    def test_approved_paper_uid_file_is_explicit_strict_and_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory(prefix="preview-approved-papers-") as temporary:
            root = Path(temporary)
            uid = SYNTHETIC_UID
            approved = root / "approved.txt"
            approved.write_text(uid + "\n", encoding="utf-8")
            self.assertEqual(load_approved_paper_uids(approved), frozenset({uid}))
            with self.assertRaises(RuntimeError):
                normalize_approved_paper_uids(())
            with self.assertRaises(RuntimeError):
                normalize_approved_paper_uids((uid, uid))
            approved.write_text("not-a-paper-uid\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_approved_paper_uids(approved)

    @staticmethod
    def synthetic_plan(*, dropped=None):
        uid = SYNTHETIC_UID
        return SimpleNamespace(
            private_source_sha256="a" * 64,
            papers=(
                {
                    "paper_uid": uid,
                    "doi": "10.1000/synthetic",
                    "title": "Synthetic package gate paper",
                    "year": 2026,
                    "first_author": "A Researcher",
                },
            ),
            entities=(),
            dropped_by_reason=dropped or {},
        )

    def test_release_gate_rejects_unexplained_drops_and_approval_mismatch(self) -> None:
        uid = SYNTHETIC_UID
        cases = (
            (self.synthetic_plan(dropped={"manual_review": 1}), {uid}, "dropped_by_reason"),
            (self.synthetic_plan(), {"paper_ffffffffffffffffffffffffffffffff"}, "批准论文清单"),
        )
        for index, (plan, approved, message) in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory(
                prefix="preview-release-gate-"
            ) as temporary:
                root = Path(temporary)
                with patch(
                    "auto_research.product.internal_preview_builder.plan_evidence_v12_export",
                    return_value=plan,
                ):
                    with self.assertRaisesRegex(RuntimeError, message):
                        build_internal_preview_package(
                            source_snapshot=root / "synthetic.sqlite",
                            output_directory=root / "published",
                            signing_key_path=root / "missing.key",
                            expected_source_sha256="a" * 64,
                            approved_paper_uids=approved,
                        )

    def test_release_policy_uses_frozen_excerpt_budgets_and_zero_drop_allowance(self) -> None:
        uid = SYNTHETIC_UID
        captured = {}

        class StopAfterPolicy(RuntimeError):
            pass

        def stop_materialization(*args, **kwargs):
            captured["policy"] = kwargs["release_policy"]
            raise StopAfterPolicy

        with tempfile.TemporaryDirectory(prefix="preview-policy-gate-") as temporary:
            root = Path(temporary)
            with patch(
                "auto_research.product.internal_preview_builder.plan_evidence_v12_export",
                return_value=self.synthetic_plan(),
            ), patch(
                "auto_research.product.internal_preview_builder.materialize_portable_repository",
                side_effect=stop_materialization,
            ):
                with self.assertRaises(StopAfterPolicy):
                    build_internal_preview_package(
                        source_snapshot=root / "synthetic.sqlite",
                        output_directory=root / "published",
                        signing_key_path=root / "missing.key",
                        expected_source_sha256="a" * 64,
                        approved_paper_uids={uid},
                    )
        policy = captured["policy"]
        self.assertEqual(policy.allowed_paper_uids, frozenset({uid}))
        self.assertEqual(policy.maximum_excerpt_chars, 1000)
        self.assertEqual(policy.maximum_excerpt_chars_per_paper, 5000)
        self.assertEqual(policy.maximum_excerpt_chars_total, 200_000)
        self.assertEqual(policy.accepted_dropped_by_reason, {})

    def test_public_report_and_cli_are_path_free_and_use_v06_compatibility(self) -> None:
        report = InternalPreviewBuildReport(
            package_id="auto-research-internal-evidence",
            package_version="0.2.0-preview.1",
            signer_key_id="test-key",
            package_path="/private/tmp/secret/internal.aresearch",
            package_size_bytes=1,
            package_sha256="a" * 64,
            manifest_sha256="b" * 64,
            repository_sha256="c" * 64,
            content_fingerprint="d" * 64,
            source_snapshot_sha256="e" * 64,
            paper_count=1,
            entity_count=0,
            item_count=0,
            finding_count=0,
            table_count=0,
            figure_count=0,
            dropped_by_reason={},
            verified_import_seconds=0.1,
        )
        self.assertEqual(report.public_dict()["package_path"], "internal.aresearch")
        with tempfile.TemporaryDirectory(prefix="preview-cli-gate-") as temporary:
            approved = Path(temporary) / "approved.txt"
            approved.write_text(
                SYNTHETIC_UID + "\n", encoding="utf-8"
            )
            output = io.StringIO()
            with patch(
                "auto_research.product.internal_preview_builder.build_internal_preview_package",
                return_value=report,
            ) as builder, redirect_stdout(output):
                self.assertEqual(
                    main(
                        [
                            "--source-snapshot",
                            "synthetic.sqlite",
                            "--output-directory",
                            "published",
                            "--signing-key",
                            "preview.key",
                            "--expected-source-sha256",
                            "a" * 64,
                            "--approved-paper-uids-file",
                            str(approved),
                        ]
                    ),
                    0,
                )
            call = builder.call_args.kwargs
            self.assertEqual(call["app_minimum"], "0.6.0")
            self.assertEqual(call["app_maximum_exclusive"], "1.0.0")
            self.assertEqual(call["package_version"], "0.2.0-preview.1")
            self.assertNotIn("/private/tmp/secret", output.getvalue())


if __name__ == "__main__":
    unittest.main()
