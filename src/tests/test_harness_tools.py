from __future__ import annotations

import hashlib
import unittest

from auto_research.ai.harness_contract import (
    HarnessError,
    HarnessEvidenceIdentity,
    HarnessJobV1,
    HarnessSessionV1,
)
from auto_research.ai.harness_tools import HarnessToolGateway
from auto_research.ai.harness_tools import TOOL_SCHEMAS


OFFICIAL = HarnessEvidenceIdentity(
    "official", "official-v1", "item", "item-1", "bundle-1"
)


def job(scope: str = "librarian") -> HarnessJobV1:
    session = HarnessSessionV1("session-1", "deepseek", 1, 1, scope, "action-1", 10, 100)
    return HarnessJobV1(
        "job-1",
        session,
        "librarian_synthesis" if scope == "librarian" else "extraction",
        "deepseek-v4-pro",
        1,
        1000,
        (OFFICIAL,),
        None if scope == "librarian" else OFFICIAL,
        (),
        hashlib.sha256(b"outbound").hexdigest(),
    )


class Backend:
    def exact_search(self, request):
        return [OFFICIAL.public_dict() | {"meaning": "硬度"}]

    federated_search = exact_search

    def evidence_detail(self, request):
        return OFFICIAL.public_dict() | {"meaning": "硬度"}

    evidence_metadata = evidence_detail
    source_locator = evidence_detail
    source_view = evidence_detail

    def citation_verify(self, refs):
        return [OFFICIAL.public_dict() | {"ref": ref} for ref in refs]

    def recommend_papers(self, request):
        return [{"paper_uid": "paper-1", "title": "Paper"}]


class HarnessToolTests(unittest.TestCase):
    def test_only_fixed_domain_tools_are_visible(self) -> None:
        catalog = HarnessToolGateway(backend=Backend(), job=job()).public_catalog()
        names = {item["name"] for item in catalog["tools"]}
        self.assertEqual(
            names,
            {
                "exact_search", "federated_search", "evidence_detail",
                "evidence_metadata", "source_locator", "source_view",
                "citation_verify", "recommend_papers",
            },
        )
        self.assertEqual(catalog["generic_capabilities"], [])
        for item in catalog["tools"]:
            self.assertEqual(item["input_schema"]["additionalProperties"], False)
        federated = next(
            item for item in catalog["tools"] if item["name"] == "federated_search"
        )
        self.assertEqual(
            federated["input_schema"]["properties"]["source_scope"]["enum"],
            ["official", "workspace"],
        )
        with self.assertRaises(TypeError):
            TOOL_SCHEMAS["exact_search"]["description"] = "changed"

    def test_librarian_rejects_private_scope_and_unknown_tool(self) -> None:
        gateway = HarnessToolGateway(backend=Backend(), job=job())
        with self.assertRaises(HarnessError) as raised:
            gateway.call(
                "federated_search",
                {"query": "W", "source_scope": "private", "entity_types": ["item"], "limit": 10},
            )
        self.assertEqual(raised.exception.code, "harness_private_forbidden")
        with self.assertRaises(HarnessError) as raised:
            gateway.call("bash", {})
        self.assertEqual(raised.exception.code, "harness_tool_forbidden")

    def test_selected_evidence_is_current_entity_only_and_no_search(self) -> None:
        gateway = HarnessToolGateway(backend=Backend(), job=job("selected_evidence_chat"))
        self.assertEqual(
            {item["name"] for item in gateway.public_catalog()["tools"]},
            {"evidence_detail", "evidence_metadata", "source_locator", "source_view"},
        )
        detail = gateway.call("evidence_detail", {
            "source_scope": "official", "source_id": "official-v1",
            "entity_type": "item", "entity_uid": "item-1",
        })
        self.assertEqual(detail["entity_uid"], "item-1")
        with self.assertRaises(HarnessError):
            gateway.call("exact_search", {"query": "W", "entity_types": ["item"], "limit": 10})

    def test_source_view_requires_protected_permission(self) -> None:
        arguments = {
            "source_scope": "official", "source_id": "official-v1",
            "entity_type": "item", "entity_uid": "item-1",
        }
        with self.assertRaises(HarnessError) as raised:
            HarnessToolGateway(backend=Backend(), job=job()).call("source_view", arguments)
        self.assertEqual(raised.exception.code, "harness_tool_forbidden")
        result = HarnessToolGateway(
            backend=Backend(), job=job(), allow_source_view=True
        ).call("source_view", arguments)
        self.assertEqual(result["entity_uid"], "item-1")

    def test_tool_result_with_internal_id_fails_closed(self) -> None:
        class UnsafeBackend(Backend):
            def exact_search(self, request):
                return [OFFICIAL.public_dict() | {"paper_id": 7}]

        gateway = HarnessToolGateway(backend=UnsafeBackend(), job=job())
        with self.assertRaises(HarnessError) as raised:
            gateway.call(
                "exact_search",
                {"query": "W", "entity_types": ["item"], "limit": 10},
            )
        self.assertEqual(raised.exception.code, "harness_tool_invalid")

    def test_verified_reference_bundle_authority_is_application_owned(self) -> None:
        gateway = HarnessToolGateway(backend=Backend(), job=job())
        gateway.call("citation_verify", {"refs": ["R1"]})
        self.assertEqual(gateway.verified_refs, frozenset({"R1"}))
        self.assertEqual(dict(gateway.verified_ref_bundles), {"R1": "bundle-1"})
        with self.assertRaises(TypeError):
            gateway.verified_ref_bundles["R1"] = "bundle-2"


if __name__ == "__main__":
    unittest.main()
