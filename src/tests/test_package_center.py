from __future__ import annotations

import hashlib
import json
import threading
import unittest

from auto_research.product.package_center import (
    PackageCenter,
    PackageCenterError,
    PackageExportService,
    PackageJobService,
    MaterializedPayload,
    PackageTransferImportService,
    PayloadPlanCandidate,
    RightsRequirement,
)


SELECTION_TOKEN = "selection_token_1234567890"
DESTINATION_TOKEN = "destination_token_12345678"
CHECKSUM = hashlib.sha256(b"transfer-package").hexdigest()
FINGERPRINT = hashlib.sha256(b"payload").hexdigest()


class _Resolver:
    def __init__(self, values=None):
        self.values = values or {
            SELECTION_TOKEN: object(),
            DESTINATION_TOKEN: object(),
        }
        self.calls = []

    def resolve(self, token):
        self.calls.append(token)
        if token not in self.values:
            raise PackageCenterError("selection_expired", "选择已过期。")
        return self.values[token]


class _Summary:
    def __init__(self, *, kind="personal_experiments", outcome=None, extra=None):
        self.kind = kind
        self.outcome = outcome
        self.extra = extra or {}

    def public_dict(self):
        value = {
            "schema": "package-summary-v1",
            "package_kind": self.kind,
            "package_id": "user-package-demo",
            "package_version": "1.0.0",
            "package_sha256": CHECKSUM,
            "integrity": "sha256-only",
            "confidentiality": "none",
            "trusted_official": False,
        }
        if self.outcome:
            value["outcome"] = self.outcome
        value.update(self.extra)
        return value


class _Planner:
    def __init__(self, *, rights=False):
        self.rights = rights
        self.current_fingerprint = FINGERPRINT
        self.plan_calls = []
        self.materialize_calls = []
        self.cleanup_calls = 0

    def plan(self, *, kind, scope, selection):
        self.plan_calls.append((kind, scope, selection))
        requirements = (
            RightsRequirement("paper_0123456789abcdef0123456789abcdef", "Paper A"),
        ) if self.rights else ()
        return PayloadPlanCandidate(
            package_id="user-package-demo",
            package_version="1.0.0",
            content_fingerprint=FINGERPRINT,
            estimated_bytes=2048,
            item_count=3,
            paper_count=1 if kind == "literature_collection" else 0,
            missing_pdf_count=1 if kind == "literature_collection" else 0,
            rights_requirements=requirements,
            payload={"private": "opaque"},
        )

    def current_content_fingerprint(self, candidate):
        return self.current_fingerprint

    def materialize(self, candidate, *, rights_confirmations):
        self.materialize_calls.append((candidate, rights_confirmations))
        return MaterializedPayload(
            object(),
            lambda: setattr(self, "cleanup_calls", self.cleanup_calls + 1),
        )


class _Activator:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    def activate(self, imported, *, keep_conflicts):
        self.calls.append((imported, keep_conflicts))
        if self.fail:
            raise PackageCenterError(
                "transfer_activation_failed",
                "资料包已安装，但搜索激活待重试。",
                retryable=True,
            )
        return _Summary(outcome="imported", extra={"search_ready": True})


class _ReceiptRecorder:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    def record_completed(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("receipt store failed at /private/receipt")
        return {"schema_version": "activity-receipt-v1"}


def _risk_ack(*, paper_rights=None):
    return {
        "unencrypted_ack": True,
        "unauthenticated_source_ack": True,
        "internal_use_only_ack": True,
        "paper_rights": paper_rights or {},
    }


class PackageCenterTests(unittest.TestCase):
    def test_completed_export_records_receipt_without_changing_artifact_result(self):
        recorder = _ReceiptRecorder()
        jobs = PackageJobService(receipt_recorder=recorder)
        service = PackageExportService(
            payload_planner=_Planner(),
            destination_resolver=_Resolver(),
            exporter=lambda *args, **kwargs: _Summary(outcome="exported"),
            jobs=jobs,
        )
        plan = service.plan("personal_experiments", "all", None)
        completed = service.start(plan["plan_token"], _risk_ack(), DESTINATION_TOKEN)
        self.assertEqual(completed["stage"], "completed")
        self.assertEqual(completed["receipt_status"], "stored")
        self.assertEqual(len(recorder.calls), 1)
        self.assertEqual(recorder.calls[0]["operation"].value, "transfer_export")

    def test_receipt_failure_keeps_completed_artifact_and_does_not_repeat_export(self):
        recorder = _ReceiptRecorder(fail=True)
        exports = []
        jobs = PackageJobService(receipt_recorder=recorder)
        service = PackageExportService(
            payload_planner=_Planner(),
            destination_resolver=_Resolver(),
            exporter=lambda *args, **kwargs: (
                exports.append(args) or _Summary(outcome="exported")
            ),
            jobs=jobs,
        )
        plan = service.plan("personal_experiments", "all", None)
        completed = service.start(plan["plan_token"], _risk_ack(), DESTINATION_TOKEN)
        self.assertEqual(completed["stage"], "completed")
        self.assertEqual(completed["receipt_status"], "pending")
        self.assertEqual(len(exports), 1)
        self.assertEqual(len(recorder.calls), 1)
        self.assertEqual(jobs.get(completed["job_id"])["receipt_status"], "pending")

    def test_non_export_completion_does_not_create_activity_receipt(self):
        recorder = _ReceiptRecorder()
        service = PackageTransferImportService(
            selection_resolver=_Resolver(),
            inspector=lambda source: _Summary(),
            importer=lambda *args, **kwargs: _Summary(outcome="imported"),
            activator=_Activator(),
            jobs=PackageJobService(receipt_recorder=recorder),
        )
        completed = service.start(
            SELECTION_TOKEN, checksum_ack=True, expected_sha=CHECKSUM
        )
        self.assertEqual(completed["stage"], "completed")
        self.assertNotIn("receipt_status", completed)
        self.assertEqual(recorder.calls, [])

    def test_inspect_resolves_opaque_token_and_returns_path_free_summary(self):
        resolver = _Resolver()
        center = PackageCenter(
            selection_resolver=resolver,
            inspector=lambda source: _Summary(),
        )
        summary = center.inspect(SELECTION_TOKEN)
        self.assertEqual(summary["schema"], "package-summary-v1")
        self.assertTrue(summary["checksum_ack_required"])
        self.assertEqual(resolver.calls, [SELECTION_TOKEN])
        self.assertNotIn("path", json.dumps(summary, ensure_ascii=False).casefold())

    def test_inspect_rejects_path_leaking_port_result(self):
        center = PackageCenter(
            selection_resolver=_Resolver(),
            inspector=lambda source: _Summary(extra={"install_path": "/Users/demo"}),
        )
        with self.assertRaises(PackageCenterError) as raised:
            center.inspect(SELECTION_TOKEN)
        self.assertEqual(raised.exception.code, "package_result_unsafe")

    def test_plan_supports_selected_filtered_and_all_without_exposing_selection(self):
        planner = _Planner()
        service = PackageExportService(
            payload_planner=planner,
            destination_resolver=_Resolver(),
            exporter=lambda *args, **kwargs: _Summary(outcome="exported"),
            jobs=PackageJobService(),
        )
        selected = service.plan("literature_collection", "selected", ["paper-a", "paper-b"])
        filtered = service.plan(
            "literature_collection", "filtered", {"element": "W", "year": [2020, 2026]}
        )
        all_records = service.plan("personal_experiments", "all", None)
        self.assertEqual(selected["selected_count"], 2)
        self.assertEqual(filtered["scope"], "filtered")
        self.assertEqual(all_records["scope"], "all")
        encoded = json.dumps((selected, filtered, all_records), ensure_ascii=False)
        self.assertNotIn("paper-a", encoded)
        self.assertNotIn("element", encoded)
        self.assertEqual(len(planner.plan_calls), 3)

    def test_plan_rejects_ambiguous_or_oversized_selection(self):
        service = PackageExportService(
            payload_planner=_Planner(),
            destination_resolver=_Resolver(),
            exporter=lambda *args, **kwargs: _Summary(outcome="exported"),
            jobs=PackageJobService(),
        )
        cases = (
            ("selected", []),
            ("selected", ["same", "same"]),
            ("filtered", {}),
            ("all", ["paper-a"]),
        )
        for scope, selection in cases:
            with self.subTest(scope=scope, selection=selection):
                with self.assertRaises(PackageCenterError) as raised:
                    service.plan("literature_collection", scope, selection)
                self.assertEqual(raised.exception.code, "package_selection_invalid")

    def test_export_requires_all_risk_and_per_pdf_rights_acknowledgements(self):
        planner = _Planner(rights=True)
        jobs = PackageJobService()
        service = PackageExportService(
            payload_planner=planner,
            destination_resolver=_Resolver(),
            exporter=lambda *args, **kwargs: _Summary(outcome="exported"),
            jobs=jobs,
        )
        plan = service.plan("literature_collection", "selected", ["paper-a"])
        for confirmations in (
            {},
            _risk_ack(),
            _risk_ack(paper_rights={
                "paper_0123456789abcdef0123456789abcdef": {
                    "allowed": True,
                    "basis": "",
                }
            }),
        ):
            with self.subTest(confirmations=confirmations):
                with self.assertRaises(PackageCenterError):
                    service.start(plan["plan_token"], confirmations, DESTINATION_TOKEN)

        completed = service.start(
            plan["plan_token"],
            _risk_ack(paper_rights={
                "paper_0123456789abcdef0123456789abcdef": {
                    "allowed": True,
                    "basis": "author approved internal sharing",
                }
            }),
            DESTINATION_TOKEN,
        )
        self.assertEqual(completed["stage"], "completed")
        self.assertEqual(completed["outcome"], "exported")
        self.assertEqual(jobs.get(completed["job_id"]), completed)
        self.assertEqual(len(planner.materialize_calls), 1)
        self.assertEqual(planner.cleanup_calls, 1)

    def test_export_rejects_stale_plan_before_destination_or_archive_write(self):
        planner = _Planner()
        destinations = _Resolver()
        export_calls = []
        service = PackageExportService(
            payload_planner=planner,
            destination_resolver=destinations,
            exporter=lambda *args, **kwargs: export_calls.append(args),
            jobs=PackageJobService(),
        )
        plan = service.plan("personal_experiments", "all", None)
        planner.current_fingerprint = hashlib.sha256(b"changed").hexdigest()
        failed = service.start(plan["plan_token"], _risk_ack(), DESTINATION_TOKEN)
        self.assertEqual(failed["stage"], "failed")
        self.assertEqual(failed["error"]["code"], "package_plan_stale")
        self.assertEqual(destinations.calls, [])
        self.assertEqual(export_calls, [])

    def test_export_cleans_materialized_snapshot_after_failure(self):
        planner = _Planner()
        service = PackageExportService(
            payload_planner=planner,
            destination_resolver=_Resolver(),
            exporter=lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("archive failed")
            ),
            jobs=PackageJobService(),
        )
        plan = service.plan("personal_experiments", "all", None)
        failed = service.start(plan["plan_token"], _risk_ack(), DESTINATION_TOKEN)
        self.assertEqual(failed["stage"], "failed")
        self.assertEqual(planner.cleanup_calls, 1)

    def test_oversized_plan_is_visible_but_cannot_start(self):
        planner = _Planner()
        original_plan = planner.plan

        def oversized(**kwargs):
            candidate = original_plan(**kwargs)
            return PayloadPlanCandidate(
                package_id=candidate.package_id,
                package_version=candidate.package_version,
                content_fingerprint=candidate.content_fingerprint,
                estimated_bytes=2 * 1024 * 1024 * 1024 + 1,
                item_count=candidate.item_count,
                exceeds_size_limit=True,
                payload=candidate.payload,
            )

        planner.plan = oversized
        service = PackageExportService(
            payload_planner=planner,
            destination_resolver=_Resolver(),
            exporter=lambda *args, **kwargs: _Summary(outcome="exported"),
            jobs=PackageJobService(),
        )
        plan = service.plan("personal_experiments", "all", None)
        self.assertTrue(plan["exceeds_size_limit"])
        with self.assertRaises(PackageCenterError) as raised:
            service.start(plan["plan_token"], _risk_ack(), DESTINATION_TOKEN)
        self.assertEqual(raised.exception.code, "transfer_size")

    def test_expired_plan_is_rejected(self):
        now = [10.0]
        service = PackageExportService(
            payload_planner=_Planner(),
            destination_resolver=_Resolver(),
            exporter=lambda *args, **kwargs: _Summary(outcome="exported"),
            jobs=PackageJobService(),
            plan_ttl_seconds=30,
            clock=lambda: now[0],
        )
        plan = service.plan("personal_experiments", "all", None)
        now[0] = 41.0
        with self.assertRaises(PackageCenterError) as raised:
            service.start(plan["plan_token"], _risk_ack(), DESTINATION_TOKEN)
        self.assertEqual(raised.exception.code, "package_plan_expired")

    def test_import_checks_out_of_band_hash_and_returns_pollable_job(self):
        resolver = _Resolver()
        calls = []

        def importer(source, **kwargs):
            calls.append(kwargs)
            return _Summary(outcome="imported")

        jobs = PackageJobService()
        service = PackageTransferImportService(
            selection_resolver=resolver,
            inspector=lambda source: _Summary(),
            importer=importer,
            activator=_Activator(),
            jobs=jobs,
        )
        completed = service.start(
            SELECTION_TOKEN,
            checksum_ack=True,
            expected_sha=CHECKSUM,
        )
        self.assertEqual(completed["operation"], "transfer_import")
        self.assertEqual(completed["stage"], "completed")
        self.assertEqual(completed["result"]["outcome"], "imported")
        self.assertEqual(jobs.get(completed["job_id"]), completed)
        self.assertEqual(calls[0]["expected_kind"], "personal_experiments")
        self.assertTrue(calls[0]["checksum_ack"])
        self.assertTrue(calls[0]["require_structured_payload"])

    def test_import_ack_is_mandatory_and_hash_mismatch_is_terminal_job(self):
        service = PackageTransferImportService(
            selection_resolver=_Resolver(),
            inspector=lambda source: _Summary(),
            importer=lambda *args, **kwargs: _Summary(outcome="imported"),
            activator=_Activator(),
            jobs=PackageJobService(),
        )
        with self.assertRaises(PackageCenterError) as no_ack:
            service.start(SELECTION_TOKEN, checksum_ack=False, expected_sha=CHECKSUM)
        self.assertEqual(no_ack.exception.code, "transfer_checksum_ack_required")
        wrong = hashlib.sha256(b"wrong").hexdigest()
        failed = service.start(SELECTION_TOKEN, checksum_ack=True, expected_sha=wrong)
        self.assertEqual(failed["stage"], "failed")
        self.assertEqual(
            failed["error"]["code"], "transfer_package_checksum_mismatch"
        )

    def test_import_selection_and_export_destination_tokens_are_one_time(self):
        import_service = PackageTransferImportService(
            selection_resolver=_Resolver(),
            inspector=lambda source: _Summary(),
            importer=lambda *args, **kwargs: _Summary(outcome="imported"),
            activator=_Activator(),
            jobs=PackageJobService(),
        )
        first = import_service.start(
            SELECTION_TOKEN, checksum_ack=True, expected_sha=CHECKSUM
        )
        second = import_service.start(
            SELECTION_TOKEN, checksum_ack=True, expected_sha=CHECKSUM
        )
        self.assertEqual(first["stage"], "completed")
        self.assertEqual(second["error"]["code"], "package_selection_expired")

        export_service = PackageExportService(
            payload_planner=_Planner(),
            destination_resolver=_Resolver(),
            exporter=lambda *args, **kwargs: _Summary(outcome="exported"),
            jobs=PackageJobService(),
        )
        plan = export_service.plan("personal_experiments", "all", None)
        exported = export_service.start(
            plan["plan_token"], _risk_ack(), DESTINATION_TOKEN
        )
        repeated = export_service.start(
            plan["plan_token"], _risk_ack(), DESTINATION_TOKEN
        )
        self.assertEqual(exported["stage"], "completed")
        self.assertEqual(repeated["error"]["code"], "package_destination_expired")

    def test_single_flight_rejects_second_package_job(self):
        entered = threading.Event()
        release = threading.Event()
        planner = _Planner()
        jobs = PackageJobService()

        def exporter(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(timeout=2))
            return _Summary(outcome="exported")

        export_service = PackageExportService(
            payload_planner=planner,
            destination_resolver=_Resolver(),
            exporter=exporter,
            jobs=jobs,
        )
        import_service = PackageTransferImportService(
            selection_resolver=_Resolver(),
            inspector=lambda source: _Summary(),
            importer=lambda *args, **kwargs: _Summary(outcome="imported"),
            activator=_Activator(),
            jobs=jobs,
        )
        plan = export_service.plan("personal_experiments", "all", None)
        result = {}

        thread = threading.Thread(
            target=lambda: result.setdefault(
                "job",
                export_service.start(
                    plan["plan_token"], _risk_ack(), DESTINATION_TOKEN
                ),
            )
        )
        thread.start()
        self.assertTrue(entered.wait(timeout=2))
        with self.assertRaises(PackageCenterError) as busy:
            import_service.start(
                SELECTION_TOKEN,
                checksum_ack=True,
                expected_sha=CHECKSUM,
            )
        self.assertEqual(busy.exception.code, "package_busy")
        release.set()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result["job"]["stage"], "completed")

    def test_unknown_port_exception_is_redacted(self):
        def unsafe_importer(*args, **kwargs):
            raise RuntimeError("/Users/private/secret.sqlite")

        service = PackageTransferImportService(
            selection_resolver=_Resolver(),
            inspector=lambda source: _Summary(),
            importer=unsafe_importer,
            activator=_Activator(),
            jobs=PackageJobService(),
        )
        failed = service.start(
            SELECTION_TOKEN,
            checksum_ack=True,
            expected_sha=CHECKSUM,
        )
        encoded = json.dumps(failed, ensure_ascii=False)
        self.assertEqual(failed["error"]["code"], "transfer_import_failed")
        self.assertNotIn("/Users", encoded)
        self.assertNotIn("secret.sqlite", encoded)

    def test_background_submitter_returns_queued_jobs_before_heavy_io(self):
        export_callbacks = []
        export_jobs = PackageJobService()
        export_service = PackageExportService(
            payload_planner=_Planner(),
            destination_resolver=_Resolver(),
            exporter=lambda *args, **kwargs: _Summary(outcome="exported"),
            jobs=export_jobs,
            job_submitter=export_callbacks.append,
        )
        plan = export_service.plan("personal_experiments", "all", None)
        queued_export = export_service.start(
            plan["plan_token"], _risk_ack(), DESTINATION_TOKEN
        )
        self.assertEqual(queued_export["stage"], "queued")
        self.assertFalse(queued_export["terminal"])
        self.assertEqual(len(export_callbacks), 1)
        export_callbacks.pop()()
        self.assertEqual(
            export_jobs.get(queued_export["job_id"])["stage"], "completed"
        )

        import_callbacks = []
        import_jobs = PackageJobService()
        import_service = PackageTransferImportService(
            selection_resolver=_Resolver(),
            inspector=lambda source: _Summary(),
            importer=lambda *args, **kwargs: _Summary(outcome="imported"),
            activator=_Activator(),
            jobs=import_jobs,
            job_submitter=import_callbacks.append,
        )
        queued_import = import_service.start(
            SELECTION_TOKEN,
            checksum_ack=True,
            expected_sha=CHECKSUM,
        )
        self.assertEqual(queued_import["stage"], "queued")
        self.assertFalse(queued_import["terminal"])
        self.assertEqual(len(import_callbacks), 1)
        import_callbacks.pop()()
        self.assertEqual(
            import_jobs.get(queued_import["job_id"])["stage"], "completed"
        )

    def test_activation_failure_is_reported_at_activate_and_keeps_install_result(self):
        activator = _Activator(fail=True)
        service = PackageTransferImportService(
            selection_resolver=_Resolver(),
            inspector=lambda source: _Summary(),
            importer=lambda *args, **kwargs: _Summary(outcome="imported"),
            activator=activator,
            jobs=PackageJobService(),
        )
        failed = service.start(
            SELECTION_TOKEN,
            checksum_ack=True,
            expected_sha=CHECKSUM,
        )
        self.assertEqual(failed["stage"], "failed")
        self.assertEqual(failed["error"]["stage"], "activate")
        self.assertEqual(failed["error"]["code"], "transfer_activation_failed")
        self.assertTrue(failed["error"]["retryable"])

    def test_port_declared_safe_error_cannot_smuggle_a_path(self):
        class UnsafeDeclaredError(RuntimeError):
            code = "selection_failed"
            safe_message = "failed at /Users/private/selected.aresearch"

        def resolver(_token):
            raise UnsafeDeclaredError()

        center = PackageCenter(
            selection_resolver=type("Resolver", (), {"resolve": staticmethod(resolver)})(),
            inspector=lambda source: _Summary(),
        )
        with self.assertRaises(PackageCenterError) as raised:
            center.inspect(SELECTION_TOKEN)
        self.assertEqual(raised.exception.code, "package_inspect_failed")
        self.assertNotIn("/Users", raised.exception.safe_message)


if __name__ == "__main__":
    unittest.main()
