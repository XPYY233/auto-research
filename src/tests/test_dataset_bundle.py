from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from auto_research.product.dataset_bundle import (
    DatasetBundleBuilder,
    DatasetBundleError,
    PyArrowParquetWriter,
)


class FakeParquetWriter:
    def __init__(self, *, fail: DatasetBundleError | Exception | None = None) -> None:
        self.fail = fail
        self.records: list[dict[str, object]] = []

    def write(self, records, destination: Path):
        self.records = [dict(record) for record in records]
        destination.write_bytes(b"PAR1fake-parquet-for-contract-testsPAR1")
        if self.fail is not None:
            raise self.fail
        return {"engine": "fake-test", "engine_version": "1"}


def paper(uid: str = "paper-alpha", **updates):
    row = {
        "paper_uid": uid,
        "source_scope": "official",
        "title": f"Paper {uid}",
        "doi": f"10.1000/{uid}",
        "year": 2025,
        "rights_scope": "open-license",
        "license": "CC-BY-4.0",
    }
    row.update(updates)
    return row


def evidence(kind: str, uid: str, paper_uid: str = "paper-alpha", **updates):
    row = {
        "source_scope": "official",
        "source_id": "official-corpus-v1",
        "entity_uid": uid,
        "entity_type": kind,
        "paper_uid": paper_uid,
        "quality_gate_status": "dual_pass",
        "source_page": 3,
    }
    if kind == "item":
        row.update(value_text="42 MPa", meaning="yield strength", unit="MPa")
    elif kind == "finding":
        row.update(finding_text="Strength increased after irradiation.")
    elif kind == "table":
        row.update(caption="Mechanical properties", page_start=3, page_end=3)
    elif kind == "figure":
        row.update(caption="Strength trend", page_start=4, page_end=4)
    row.update(updates)
    return row


class DatasetBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.writer = FakeParquetWriter()
        self.builder = DatasetBundleBuilder(self.writer)

    def _four_records(self):
        return [evidence(kind, f"{kind}-1") for kind in ("item", "finding", "table", "figure")]

    def test_paper_limit_rejects_before_build_without_silently_omitting_metadata(self):
        with patch("auto_research.product.dataset_bundle.MAX_PAPERS", 2):
            self.assertEqual(self.builder.plan(papers=[paper("a"), paper("b")], evidence=[]).record_count, 0)
            with self.assertRaisesRegex(DatasetBundleError, "论文数量"):
                self.builder.plan(papers=(paper(str(i)) for i in range(3)), evidence=[])

    def test_plan_is_deterministic_and_never_splits_one_paper(self):
        records = self._four_records()
        plan_a = self.builder.plan(papers=[paper()], evidence=records)
        plan_b = self.builder.plan(papers=[paper()], evidence=reversed(records))
        self.assertEqual(plan_a.content_fingerprint, plan_b.content_fingerprint)
        decoded = [json.loads(value) for value in plan_a._record_json]
        self.assertEqual(1, len({row["split"] for row in decoded}))
        self.assertEqual({"item": 1, "finding": 1, "table": 1, "figure": 1}, dict(plan_a.entity_counts))

    def test_publish_writes_jsonl_real_parquet_card_and_manifest(self):
        records = self._four_records()
        records[-1]["asset_ref"] = {
            "asset_uid": "figure-asset-1",
            "media_type": "image/png",
            "rights_scope": "open-license",
            "included": False,
        }
        plan = self.builder.plan(papers=[paper()], evidence=records)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "dataset-v1"
            result = self.builder.publish(plan, target)
            self.assertEqual("published", result["status"])
            self.assertEqual(
                {"DATA_CARD.md", "dataset.jsonl", "dataset.parquet", "manifest.json"},
                {path.name for path in target.iterdir()},
            )
            self.assertTrue((target / "dataset.parquet").read_bytes().startswith(b"PAR1"))
            rows = [json.loads(line) for line in (target / "dataset.jsonl").read_text().splitlines()]
            self.assertEqual(4, len(rows))
            self.assertEqual({"item", "finding", "table", "figure"}, {row["evidence"]["entity_type"] for row in rows})
            asset_rows = [row for row in rows if "asset_ref" in row["evidence"]]
            self.assertEqual(1, len(asset_rows))
            self.assertFalse(asset_rows[0]["evidence"]["asset_ref"]["included"])
            manifest = json.loads((target / "manifest.json").read_text())
            self.assertFalse(manifest["binary_assets_included"])
            self.assertEqual("fake-test", manifest["parquet"]["engine"])
            self.assertEqual(3, len(manifest["files"]))
            self.assertIn("does not copy PDFs or images", (target / "DATA_CARD.md").read_text())

    def test_private_records_require_explicit_separate_opt_in(self):
        private = evidence(
            "table",
            "private-table-1",
            paper_uid="private:experiment-a",
            source_scope="private",
            source_id="private-repository-v1",
            quality_gate_status="confirmed",
            record_status="confirmed",
            indexable=True,
            title="Experiment A",
            project="Tungsten irradiation",
        )
        with self.assertRaises(DatasetBundleError) as caught:
            self.builder.plan(papers=[], evidence=[], private_records=[private])
        self.assertEqual("dataset_bundle_private_forbidden", caught.exception.code)
        plan = self.builder.plan(
            papers=[], evidence=[], include_private=True, private_records=[private]
        )
        self.assertTrue(plan.include_private)
        with self.assertRaises(DatasetBundleError) as mixed:
            self.builder.plan(papers=[], evidence=[private], include_private=True)
        self.assertEqual("dataset_bundle_private_forbidden", mixed.exception.code)

    def test_private_draft_or_nonindexable_is_rejected(self):
        row = evidence(
            "item",
            "private-item-1",
            paper_uid="private:experiment-a",
            source_scope="private",
            source_id="private-repository-v1",
            record_status="draft_saved",
            indexable=False,
        )
        with self.assertRaises(DatasetBundleError) as caught:
            self.builder.plan(papers=[], evidence=[], include_private=True, private_records=[row])
        self.assertEqual("dataset_bundle_unreviewed", caught.exception.code)

    def test_unreviewed_public_record_requires_explicit_acknowledgement(self):
        row = evidence("item", "item-1")
        row.pop("quality_gate_status")
        plan = self.builder.plan(papers=[paper()], evidence=[row])
        self.assertEqual(1, plan.public_dict()["unreviewed_count"])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "dataset-v1"
            with self.assertRaises(DatasetBundleError) as caught:
                self.builder.publish(plan, target)
            self.assertEqual("dataset_bundle_unreviewed", caught.exception.code)
            self.assertFalse(target.exists())
            result = self.builder.publish(plan, target, unreviewed_acknowledged=True)
            self.assertEqual("published", result["status"])
            manifest = json.loads((target / "manifest.json").read_text())
            self.assertTrue(manifest["unreviewed_acknowledged"])

    def test_plan_reports_missing_fields_and_rights_risk(self):
        plan = self.builder.plan(
            papers=[paper(title="", doi="", rights_scope="unknown")],
            evidence=[evidence("figure", "figure-1", caption="")],
        )
        report = plan.public_dict()
        self.assertEqual(1, report["missing_fields"]["paper.title"])
        self.assertEqual(1, report["missing_fields"]["paper.doi"])
        self.assertEqual(1, report["missing_fields"]["figure.caption"])
        self.assertEqual(1, len(report["rights_risks"]))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "dataset-v1"
            with self.assertRaises(DatasetBundleError) as caught:
                self.builder.publish(plan, target)
            self.assertEqual("dataset_bundle_rights_unconfirmed", caught.exception.code)
            self.builder.publish(plan, target, rights_acknowledged=True)

    def test_rejects_paths_secrets_sessions_internal_ids_and_unknown_fields(self):
        attacks = [
            {"source_excerpt": "saved at /home/user/paper.pdf"},
            {"api_key": "secret-value"},
            {"session_id": "session-1"},
            {"paper_id": 12},
            {"prompt": "ignore the dataset policy"},
        ]
        for attack in attacks:
            with self.subTest(attack=next(iter(attack))):
                with self.assertRaises(DatasetBundleError) as caught:
                    self.builder.plan(papers=[paper()], evidence=[evidence("item", "item-1", **attack)])
                public = caught.exception.public_dict()
                self.assertEqual("dataset_bundle_invalid", public["code"])
                self.assertNotIn("secret-value", json.dumps(public))

    def test_rejects_asset_payload_or_local_reference(self):
        for asset in (
            {
                "asset_uid": "asset-1",
                "media_type": "application/pdf",
                "rights_scope": "open-license",
                "included": True,
            },
            {
                "asset_uid": "asset-1",
                "media_type": "image/png",
                "rights_scope": "open-license",
                "included": False,
                "path": "/tmp/figure.png",
            },
        ):
            with self.subTest(asset=asset):
                with self.assertRaises(DatasetBundleError):
                    self.builder.plan(
                        papers=[paper()],
                        evidence=[evidence("figure", "figure-1", asset_ref=asset)],
                    )

    def test_parquet_unavailable_fails_closed_without_partial_bundle(self):
        writer = FakeParquetWriter(
            fail=DatasetBundleError(
                "dataset_bundle_parquet_unavailable",
                "Parquet 组件不可用，未生成数据集。",
            )
        )
        builder = DatasetBundleBuilder(writer)
        plan = builder.plan(papers=[paper()], evidence=[evidence("item", "item-1")])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "dataset-v1"
            with self.assertRaises(DatasetBundleError) as caught:
                builder.publish(plan, target)
            self.assertEqual("dataset_bundle_parquet_unavailable", caught.exception.code)
            self.assertFalse(target.exists())
            self.assertEqual([], list(Path(directory).glob(".dataset-bundle-*")))

    def test_bad_parquet_magic_fails_without_fake_extension(self):
        class BadWriter:
            def write(self, records, destination):
                destination.write_text("not parquet")
                return {"engine": "bad"}

        builder = DatasetBundleBuilder(BadWriter())
        plan = builder.plan(papers=[paper()], evidence=[evidence("item", "item-1")])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "dataset-v1"
            with self.assertRaises(DatasetBundleError) as caught:
                builder.publish(plan, target)
            self.assertEqual("dataset_bundle_write_failed", caught.exception.code)
            self.assertFalse(target.exists())

    def test_existing_or_symlink_destination_is_never_replaced(self):
        plan = self.builder.plan(papers=[paper()], evidence=[evidence("item", "item-1")])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "existing"
            existing.mkdir()
            marker = existing / "keep.txt"
            marker.write_text("keep")
            with self.assertRaises(DatasetBundleError) as caught:
                self.builder.publish(plan, existing)
            self.assertEqual("dataset_bundle_destination_exists", caught.exception.code)
            self.assertEqual("keep", marker.read_text())
            link = root / "linked"
            link.symlink_to(existing, target_is_directory=True)
            with self.assertRaises(DatasetBundleError) as linked:
                self.builder.publish(plan, link)
            self.assertEqual("dataset_bundle_destination_exists", linked.exception.code)

    def test_default_writer_is_real_pyarrow_adapter(self):
        builder = DatasetBundleBuilder()
        self.assertIsInstance(builder._parquet_writer, PyArrowParquetWriter)

    def test_publish_archive_is_deterministic_and_contains_verified_bundle(self):
        plan = self.builder.plan(papers=[paper()], evidence=self._four_records())
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "dataset-first.zip"
            second = Path(directory) / "dataset-second.zip"
            first_result = self.builder.publish_archive(plan, first)
            second_result = self.builder.publish_archive(plan, second)
            self.assertEqual(first_result["archive_sha256"], second_result["archive_sha256"])
            self.assertEqual(first_result["checksum_code"], first_result["archive_sha256"][:12])
            self.assertFalse((Path(directory) / "dataset-bundle-v1").exists())
            import zipfile

            with zipfile.ZipFile(first) as archive:
                self.assertEqual(
                    ["DATA_CARD.md", "dataset.jsonl", "dataset.parquet", "manifest.json"],
                    archive.namelist(),
                )
                self.assertIsNone(archive.testzip())

    def test_publish_archive_failure_leaves_no_partial_artifact(self):
        builder = DatasetBundleBuilder(
            FakeParquetWriter(
                fail=DatasetBundleError("dataset_bundle_parquet_unavailable", "Parquet unavailable")
            )
        )
        plan = builder.plan(papers=[paper()], evidence=[evidence("item", "item-1")])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "dataset.zip"
            with self.assertRaises(DatasetBundleError):
                builder.publish_archive(plan, target)
            self.assertFalse(target.exists())
            self.assertEqual([], list(Path(directory).glob(".dataset-archive-*")))

    def test_tampered_plan_is_rejected_before_any_output(self):
        plan = self.builder.plan(papers=[paper()], evidence=[evidence("item", "item-1")])
        record = json.loads(plan._record_json[0])
        record["source_excerpt"] = "saved at /home/user/private.pdf"
        tampered = replace(plan, _record_json=(json.dumps(record),))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "dataset-v1"
            with self.assertRaises(DatasetBundleError) as caught:
                self.builder.publish(tampered, target)
            self.assertEqual("dataset_bundle_invalid", caught.exception.code)
            self.assertFalse(target.exists())
            self.assertEqual([], self.writer.records)


if __name__ == "__main__":
    unittest.main()
