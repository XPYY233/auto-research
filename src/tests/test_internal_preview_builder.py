from __future__ import annotations

import hashlib
import os
import io
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from auto_research.evidence.official_table_structure_review import (
    OfficialTableSource,
    official_table_structure_content_fingerprint,
)
from auto_research.product.evidence_package import verify_evidence_package
from auto_research.product.internal_preview_builder import (
    build_internal_preview_package,
    initialize_preview_signing_key,
    InternalPreviewBuildReport,
    load_approved_paper_uids,
    load_preview_signing_key,
    main,
    normalize_approved_paper_uids,
    preview_public_key_base64,
    _budget_official_excerpts,
)
from auto_research.product.official_package_store import (
    import_official_evidence_package,
    open_active_official_repository,
)
from auto_research.product.official_table_structures import (
    OFFICIAL_TABLE_STRUCTURES_PATH,
)
from auto_research.product.portable_repository import (
    PortableExportPlan,
    stable_entity_uid,
    stable_paper_uid,
)
from auto_research.product.trusted_publishers import (
    TrustedPublisher,
    TrustedPublisherPolicy,
)


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
            captured["plan"] = args[0]
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
            ), patch(
                "auto_research.product.internal_preview_builder.build_official_table_structures_document"
            ) as table_structures_builder:
                with self.assertRaises(StopAfterPolicy):
                    build_internal_preview_package(
                        source_snapshot=root / "synthetic.sqlite",
                        output_directory=root / "published",
                        signing_key_path=root / "missing.key",
                        expected_source_sha256="a" * 64,
                        approved_paper_uids={uid},
                    )
        table_structures_builder.assert_not_called()
        self.assertEqual(captured["plan"].table_structures, ())
        policy = captured["policy"]
        self.assertEqual(policy.allowed_paper_uids, frozenset({uid}))
        self.assertEqual(policy.maximum_excerpt_chars, 1000)
        self.assertEqual(policy.maximum_excerpt_chars_per_paper, 5000)
        self.assertEqual(policy.maximum_excerpt_chars_total, 200_000)
        self.assertEqual(policy.accepted_dropped_by_reason, {})

    def test_realistic_long_context_is_trimmed_without_changing_scientific_fields(self) -> None:
        uid = SYNTHETIC_UID
        structures = ({"stable_marker": "verified-grid"},)
        plan = PortableExportPlan(
            papers=self.synthetic_plan().papers,
            entities=(
                {
                    "paper_uid": uid,
                    "entity_type": "item",
                    "identity_key": "item-1",
                    "payload": {
                        "value_text": "300",
                        "unit": "K",
                        "source_context": "x" * 5000,
                        "occurrences": [
                            {"source_excerpt": "y" * 900},
                            {"source_excerpt": "z" * 900},
                        ],
                    },
                },
            ),
            private_source_sha256="a" * 64,
            table_structures=structures,
        )
        budgeted = _budget_official_excerpts(plan)
        payload = budgeted.entities[0]["payload"]
        self.assertEqual(payload["value_text"], "300")
        self.assertEqual(payload["unit"], "K")
        self.assertEqual(len(payload["source_context"]), 1000)
        self.assertEqual(len(payload["occurrences"][0]["source_excerpt"]), 900)
        self.assertEqual(budgeted.table_structures, structures)

    def test_real_signed_archive_preserves_verified_table_structure(self) -> None:
        package_id = "auto-research-internal-evidence"
        package_version = "1.2.0-test.1"
        signer_key_id = "test-official-table-key"
        entity_identity = "stable-source-table-grid-3"
        entity_uid = stable_entity_uid(SYNTHETIC_UID, "table", entity_identity)
        rows = (("Material", "Hardness"), ("Alloy A", "4.63"))
        reasons = ("manual_transcription",)
        with tempfile.TemporaryDirectory(prefix="preview-table-sidecar-") as temporary:
            root = Path(temporary)
            pdf = root / "paper.pdf"
            pdf_bytes = b"%PDF-1.4\nsynthetic official table source\n"
            pdf.write_bytes(pdf_bytes)
            pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
            source = OfficialTableSource(
                package_id,
                SYNTHETIC_UID,
                entity_uid,
                pdf_sha256,
                3,
                (70.0, 90.0, 350.0, 190.0),
            )
            record = {
                "schema_version": "official-table-structure-version-v1",
                "source_scope": "official",
                "source_id": package_id,
                "paper_uid": SYNTHETIC_UID,
                "entity_uid": entity_uid,
                "entity_type": "table",
                "source_pdf_sha256": pdf_sha256,
                "version": 2,
                "status": "verified",
                "page": 3,
                "bbox": list(source.table_bbox or ()),
                "reason_codes": list(reasons),
                "rows": [list(row) for row in rows],
                "cells": [],
                "content_fingerprint": official_table_structure_content_fingerprint(
                    source, reasons, rows, ()
                ),
                "reviewed_at": "2026-09-02T00:00:00+00:00",
            }
            plan = PortableExportPlan(
                papers=self.synthetic_plan().papers,
                entities=(
                    {
                        "paper_uid": SYNTHETIC_UID,
                        "entity_type": "table",
                        "identity_key": entity_identity,
                        "quality_gate_status": "manual_approved",
                        "source_kind": "table",
                        "review_action": "manual",
                        "payload": {
                            "display_name": "Table 3",
                            "label": "Table 3",
                            "caption": "Verified synthetic table",
                            "page_start": 3,
                        },
                    },
                ),
                private_source_sha256="a" * 64,
                table_structures=(record,),
            )
            key_path = root / "signing.key"
            signing_key = initialize_preview_signing_key(key_path)
            publisher = TrustedPublisher(
                key_id=signer_key_id,
                display_name="Test official table publisher",
                manifest_publisher_name="Auto Research internal preview",
                public_key_base64=preview_public_key_base64(signing_key),
                channel="test-official-table",
                allowed_package_ids=(package_id,),
                required_rights_redistribution="internal-group-restricted",
            )
            policy = TrustedPublisherPolicy(
                channel=publisher.channel,
                publishers=(publisher,),
            )
            published = root / "published"
            with patch(
                "auto_research.product.internal_preview_builder.plan_evidence_v12_export",
                return_value=plan,
            ), patch(
                "auto_research.product.internal_preview_builder.assert_trusted_package_identity",
                return_value=publisher,
            ), patch(
                "auto_research.product.internal_preview_builder.trusted_public_keys",
                return_value=policy.public_keys(),
            ), patch(
                "auto_research.product.official_package_store.trusted_publisher_policy",
                return_value=policy,
            ):
                report = build_internal_preview_package(
                    source_snapshot=root / "synthetic.sqlite",
                    output_directory=published,
                    signing_key_path=key_path,
                    expected_source_sha256="a" * 64,
                    approved_paper_uids={SYNTHETIC_UID},
                    package_id=package_id,
                    package_version=package_version,
                    signer_key_id=signer_key_id,
                    current_app_version="1.2.0",
                    app_minimum="1.1.0",
                    app_maximum_exclusive="2.0.0",
                    paper_pdf_paths={SYNTHETIC_UID: pdf},
                )
            package = published / report.package_path
            verified = verify_evidence_package(
                package,
                trusted_public_keys=policy.public_keys(),
                current_app_version="1.2.0",
            )
            self.assertIn(OFFICIAL_TABLE_STRUCTURES_PATH, verified.payload_members)
            with zipfile.ZipFile(package) as archive:
                sidecar = archive.read(OFFICIAL_TABLE_STRUCTURES_PATH)
            self.assertEqual(
                verified.checksums[OFFICIAL_TABLE_STRUCTURES_PATH]["sha256"],
                hashlib.sha256(sidecar).hexdigest(),
            )

            installed_root = root / "installed"
            import_official_evidence_package(
                package,
                data_root=installed_root,
                current_app_version="1.2.0",
                publisher_policy=policy,
            )
            _active, repository = open_active_official_repository(
                data_root=installed_root,
                current_app_version="1.2.0",
                publisher_policy=policy,
            )
            structure = repository.get_table_structure(entity_uid)
            self.assertEqual(structure["status"], "verified")
            self.assertEqual(structure["rows"], [list(row) for row in rows])

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

    def test_cli_loads_official_v2_exclusions_without_mutating_source_scope(self) -> None:
        report = InternalPreviewBuildReport(
            package_id="auto-research-internal-evidence",
            package_version="1.1.0",
            signer_key_id="test-key",
            package_path="auto-research-internal-evidence-1.1.0.aresearch",
            package_size_bytes=1,
            package_sha256="a" * 64,
            manifest_sha256="b" * 64,
            repository_sha256="c" * 64,
            content_fingerprint="d" * 64,
            source_snapshot_sha256="e" * 64,
            paper_count=59,
            entity_count=0,
            item_count=0,
            finding_count=0,
            table_count=0,
            figure_count=0,
            dropped_by_reason={},
            verified_import_seconds=0.1,
        )
        with tempfile.TemporaryDirectory(prefix="preview-cli-exclusions-") as temporary:
            root = Path(temporary)
            approved = root / "approved.txt"
            approved.write_text(SYNTHETIC_UID + "\n", encoding="utf-8")
            exclusions = root / "exclusions.json"
            exclusions.write_text(
                '{"schema_version":"official-package-v2-exclusions-v1",'
                '"excluded_papers":[{"doi":"10.2172/6065200",'
                '"reason":"source_pdf_unavailable"}]}\n',
                encoding="utf-8",
            )
            with patch(
                "auto_research.product.internal_preview_builder.build_internal_preview_package",
                return_value=report,
            ) as builder, redirect_stdout(io.StringIO()):
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
                            "--official-package-v2-exclusions",
                            str(exclusions),
                        ]
                    ),
                    0,
                )
            self.assertEqual(
                builder.call_args.kwargs["excluded_dois"],
                {"10.2172/6065200": "source_pdf_unavailable"},
            )


if __name__ == "__main__":
    unittest.main()
