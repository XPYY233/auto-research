"""Real files across the shared exporter and Mac destination/receipt adapters.

No App, native dialog, network, model, production database or user key is used.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import zipfile

import pyarrow.parquet as parquet
import pytest

from auto_research.product.activity_receipts import ActivityReceiptService
from auto_research.product.dataset_bundle import DatasetBundleBuilder
from auto_research.product.dataset_export_service import DatasetExportCandidate, DatasetExportService
from auto_research.product.package_center import PackageJobService
from package_center_services import _DatasetArchivePublisher, _PackageDestinationResolver
from package_export_destination_broker import PackageExportDestinationBroker
from secure_activity_receipts import SecureActivityReceiptStore
from secure_history import StaticHistoryKeyProvider


class Source:
    def __init__(self):
        papers, records = [], []
        for index in range(123):
            uid = f"paper-{index}"
            papers.append({"paper_uid": uid, "source_scope": "official", "title": f"合成材料研究 {index}",
                           "doi": f"10.1000/synthetic-{index}", "year": 2025, "rights_scope": "unknown"})
            for kind in ("item", "finding", "table", "figure"):
                records.append({"source_scope": "official", "source_id": "synthetic-official",
                                "entity_uid": f"{kind}-{index}", "entity_type": kind, "paper_uid": uid,
                                "quality_gate_status": "dual_pass", "source_page": 3,
                                "value_text": "1.07 ± 0.06 GPa", "meaning": "辐照硬化增量",
                                "caption": "合成核验示例", "finding_text": "仅用于隔离软件验收。"})
        self.plan_value = DatasetBundleBuilder().plan(papers=papers, evidence=records)
        self.fingerprint = hashlib.sha256(b"synthetic-source-v1").hexdigest()

    def plan(self, *, include_private):
        assert include_private is False
        return DatasetExportCandidate(self.fingerprint, self.plan_value)

    def current_source_fingerprint(self, _candidate):
        return self.fingerprint


def services(root: Path, source: Source):
    receipts = ActivityReceiptService(SecureActivityReceiptStore(
        root / "state" / "receipts.enc", StaticHistoryKeyProvider(b"\x52" * 32),
    ))
    # The OS volume probe/native dialog are separate UI acceptance gates.
    broker = PackageExportDestinationBroker(local_volume_probe=lambda _: True)
    jobs = PackageJobService(receipt_recorder=receipts)
    exporter = DatasetExportService(source=source, destination_resolver=_PackageDestinationResolver(broker),
                                    publisher=_DatasetArchivePublisher(), jobs=jobs)
    return exporter, broker, receipts


def test_real_archive_rows_checksums_and_receipt_survive_service_restart(tmp_path):
    source = Source()
    exporter, broker, receipts = services(tmp_path, source)
    plan = exporter.plan(include_private=False)
    assert len(plan["rights_risks"]) > 100
    target = tmp_path / "科研数据集.zip"
    destination = broker.select(target).public_dict()
    job = exporter.start(plan["plan_token"], destination["destination_token"],
                         rights_acknowledged=True, unreviewed_acknowledged=False)
    assert job["stage"] == "completed", job
    assert job["receipt_status"] == "stored"
    result = job["result"]
    assert result["record_count"] == 492
    assert result["entity_counts"] == {kind: 123 for kind in ("item", "finding", "table", "figure")}
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    assert result["archive_sha256"] == digest
    assert result["checksum_code"] == digest[:12]
    assert result["archive_size"] == target.stat().st_size
    with zipfile.ZipFile(target) as archive:
        assert set(archive.namelist()) == {"DATA_CARD.md", "dataset.jsonl", "dataset.parquet", "manifest.json"}
        manifest = json.loads(archive.read("manifest.json"))
        rows = [json.loads(line) for line in archive.read("dataset.jsonl").splitlines()]
        columns = parquet.read_table(io.BytesIO(archive.read("dataset.parquet"))).to_pylist()
        assert len(rows) == len(columns) == 492
        for row, column in zip(rows, columns):
            assert row["paper"] == json.loads(column["paper_json"])
            assert row["evidence"] == json.loads(column["evidence_json"])
            assert (row["paper_uid"], row["split"]) == (column["paper_uid"], column["split"])
        assert manifest["content_fingerprint"] == plan["content_fingerprint"]
        assert manifest["binary_assets_included"] is False
        for file in manifest["files"]:
            payload = archive.read(file["name"])
            assert hashlib.sha256(payload).hexdigest() == file["sha256"]
            assert len(payload) == file["size"]
        assert len({row["split"] for row in rows}) == 3
        assert all(len({row["split"] for row in rows if row["paper_uid"] == uid}) == 1
                   for uid in {row["paper_uid"] for row in rows})
    stored = receipts.get()
    assert len(stored["receipts"]) == 1
    assert stored["receipts"][0]["summary"]["checksum_code"] == digest[:12]
    _, _, reopened = services(tmp_path, source)
    assert reopened.get() == stored
    assert digest[:12].encode() not in (tmp_path / "state" / "receipts.enc").read_bytes()
    assert str(tmp_path) not in json.dumps([plan, destination, job, stored])
    assert not list(tmp_path.glob(".dataset-*"))


@pytest.mark.parametrize("timing", ["before_generation", "during_generation"])
def test_destination_created_after_selection_is_preserved_without_success_receipt(tmp_path, monkeypatch, timing):
    exporter, broker, receipts = services(tmp_path, Source())
    plan = exporter.plan(include_private=False)
    target = tmp_path / "existing.zip"
    destination = broker.select(target)
    if timing == "before_generation":
        target.write_bytes(b"user-owned file")
    else:
        writer = exporter._publisher._builder._parquet_writer
        original = writer.write

        def concurrent_write(records, destination):
            result = original(records, destination)
            target.write_bytes(b"user-owned file")
            return result

        monkeypatch.setattr(writer, "write", concurrent_write)
    job = exporter.start(plan["plan_token"], destination.destination_token,
                         rights_acknowledged=True, unreviewed_acknowledged=False)
    assert job["stage"] == "failed"
    expected = "package_destination_exists" if timing == "before_generation" else "dataset_bundle_destination_exists"
    assert job["error"]["code"] == expected
    assert target.read_bytes() == b"user-owned file"
    assert receipts.get()["receipts"] == []
    assert not list(tmp_path.glob(".dataset-*"))
