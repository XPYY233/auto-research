from __future__ import annotations

from dataclasses import dataclass
import hashlib

import pytest

from auto_research.product.dataset_bundle import DatasetBundleBuilder
from auto_research.product.dataset_export_service import (
    DatasetExportCandidate,
    DatasetExportService,
)
from auto_research.product.package_center import PackageJobService
from auto_research.product.package_center_models import PackageCenterError


def _bundle_plan(*, unreviewed: bool = False, rights_risk: bool = False):
    paper = {
        "paper_uid": "paper-alpha",
        "source_scope": "official",
        "title": "Paper alpha",
        "doi": "10.1000/alpha",
        "year": 2025,
        "rights_scope": "unknown" if rights_risk else "open-license",
    }
    record = {
        "source_scope": "official",
        "source_id": "official-v2",
        "entity_uid": "item-alpha",
        "entity_type": "item",
        "paper_uid": "paper-alpha",
        "value_text": "42 MPa",
        "meaning": "yield strength",
    }
    if not unreviewed:
        record["quality_gate_status"] = "published"
    return DatasetBundleBuilder().plan(papers=[paper], evidence=[record])


@dataclass
class Source:
    bundle_plan: object
    current: str

    def plan(self, *, include_private: bool):
        assert include_private is False
        return DatasetExportCandidate(self.current, self.bundle_plan)

    def current_source_fingerprint(self, _candidate):
        return self.current


class Destination:
    def __init__(self):
        self.values = []

    def resolve(self, token):
        self.values.append(token)
        return {"opaque": "destination"}


class Publisher:
    def __init__(self):
        self.calls = []

    def __call__(self, plan, destination, **acks):
        self.calls.append((plan, destination, acks))
        return {
            "schema_version": "dataset-bundle-v1",
            "status": "published",
            "archive_sha256": "a" * 64,
            "checksum_code": "a" * 12,
        }


def _service(*, unreviewed=False, rights_risk=False):
    fingerprint = hashlib.sha256(b"source").hexdigest()
    source = Source(_bundle_plan(unreviewed=unreviewed, rights_risk=rights_risk), fingerprint)
    destination = Destination()
    publisher = Publisher()
    jobs = PackageJobService()
    service = DatasetExportService(
        source=source,
        destination_resolver=destination,
        publisher=publisher,
        jobs=jobs,
    )
    return service, source, destination, publisher, jobs


def test_plan_is_path_free_and_discloses_risks():
    service, *_ = _service(unreviewed=True, rights_risk=True)
    result = service.plan(include_private=False)
    assert result["schema_version"] == "dataset-export-plan-v1"
    assert result["unreviewed_ack_required"] is True
    assert result["rights_ack_required"] is True
    assert "/Users/" not in str(result)


def test_start_publishes_through_shared_job_contract():
    service, _source, destination, publisher, jobs = _service()
    plan = service.plan(include_private=False)
    result = service.start(
        plan["plan_token"],
        "destination_token_1234",
        rights_acknowledged=False,
        unreviewed_acknowledged=False,
    )
    assert result["operation"] == "dataset_export"
    assert result["stage"] == "completed"
    assert result["result"]["checksum_code"] == "a" * 12
    assert destination.values == ["destination_token_1234"]
    assert len(publisher.calls) == 1
    assert jobs.get(result["job_id"])["outcome"] == "exported"


def test_unconfirmed_risks_fail_without_resolving_destination():
    service, _source, destination, publisher, _jobs = _service(unreviewed=True, rights_risk=True)
    plan = service.plan(include_private=False)
    result = service.start(
        plan["plan_token"],
        "destination_token_1234",
        rights_acknowledged=False,
        unreviewed_acknowledged=False,
    )
    assert result["stage"] == "failed"
    assert result["error"]["code"] == "dataset_rights_unconfirmed"
    assert destination.values == []
    assert publisher.calls == []


def test_stale_plan_fails_without_output():
    service, source, destination, publisher, _jobs = _service()
    plan = service.plan(include_private=False)
    source.current = hashlib.sha256(b"changed").hexdigest()
    result = service.start(
        plan["plan_token"],
        "destination_token_1234",
        rights_acknowledged=False,
        unreviewed_acknowledged=False,
    )
    assert result["stage"] == "failed"
    assert result["error"]["code"] == "dataset_plan_stale"
    assert destination.values == []
    assert publisher.calls == []


def test_bad_boolean_and_expired_token_are_rejected():
    service, *_ = _service()
    with pytest.raises(PackageCenterError) as invalid:
        service.plan(include_private="yes")
    assert invalid.value.code == "dataset_request_invalid"
    with pytest.raises(PackageCenterError) as expired:
        service.start(
            "missing_plan_token_1234",
            "destination_token_1234",
            rights_acknowledged=False,
            unreviewed_acknowledged=False,
        )
    assert expired.value.code == "dataset_plan_expired"
