from __future__ import annotations

import hashlib
import time
import unittest

from auto_research.ai.business_actions import BudgetedBusinessAIClient
from auto_research.ai.harness_contract import (
    CORDIS_RUNTIME_PROTOCOL_PIN,
    HARNESS_SDK_PROTOCOL_PIN,
    HarnessDependencyMetadata,
    HarnessDependencySet,
    HarnessError,
    HarnessEvidenceIdentity,
)
from auto_research.ai.harness_runtime import DeepSeekHarnessAdapter, HarnessOutputProjector
from auto_research.ai.prepared_actions import PreparedOutbound

from src.tests.test_harness_contract import safe_composition
from src.tests.test_harness_tools import Backend, OFFICIAL


MESSAGES = [{"role": "user", "content": "bounded evidence"}]


def dependencies() -> HarnessDependencySet:
    return HarnessDependencySet(
        HarnessDependencyMetadata.from_pin(HARNESS_SDK_PROTOCOL_PIN),
        HarnessDependencyMetadata.from_pin(CORDIS_RUNTIME_PROTOCOL_PIN),
        "2.12.0",
    )


def action(scope: str = "librarian") -> PreparedOutbound:
    task = "librarian_synthesis" if scope == "librarian" else "extraction"
    model = "deepseek-v4-pro"
    call = {
        "method": "json",
        "task": task,
        "messages": MESSAGES,
        "tools": [],
        "options": {"thinking": None, "temperature": 0.1},
        "max_tokens": 1000,
    }
    now = int(time.time())
    return PreparedOutbound(
        action_id="action-1",
        session_digest=hashlib.sha256(b"session").hexdigest(),
        scope=scope,
        provider_id="deepseek",
        runtime_revision=1,
        credential_generation=1,
        runtime_activation="legacy-compatible",
        runtime_task_models=((task, model),),
        task=task,
        task_models=((task, model),),
        models=(model,),
        executor_id="harness-test",
        executor_version="v1",
        estimated_calls=1,
        max_calls=1,
        max_tokens=1000,
        outbound={"call_plan": [call]},
        outbound_digest=hashlib.sha256(b"outbound").hexdigest(),
        manifest_digest=hashlib.sha256(b"manifest").hexdigest(),
        units=(),
        byte_count=100,
        issued_at=now,
        expires_at=now + 300,
    )


class RawClient:
    def __init__(self) -> None:
        self.calls = 0

    def request_json(self, messages, **kwargs):
        self.calls += 1
        return {"ok": True}


class Runtime:
    def __init__(self, *, selected: bool = False, malformed: bool = False) -> None:
        self.selected = selected
        self.malformed = malformed

    def dependency_metadata(self):
        return dependencies()

    def composition_metadata(self):
        return safe_composition()

    def execute(self, *, job, model, tools, prompt=None):
        model.request_json(
            MESSAGES,
            task=job.task,
            max_tokens=1000,
            thinking=None,
            temperature=0.1,
        )
        if self.malformed:
            return {"schema_version": "bad", "path": "/Users/name/secret"}
        if self.selected:
            return {
                "schema_version": "selected-evidence-harness-model-v1",
                "answer": "当前证据说明硬度变化。",
                "limitations": ["仅解释当前证据"],
            }
        tools.call("citation_verify", {"refs": ["R1"]})
        tools.call("recommend_papers", {"question": "硬度", "limit": 3})
        return {
            "schema_version": "librarian-harness-result-v1",
            "answer": "直接结论",
            "report": {
                "direct_conclusion": "有证据支持。",
                "evidence_matrix": [{"ref": "R1"}],
                "related_evidence": [],
                "database_gaps": "尚缺人工金标准。",
                "suggested_followups": ["核对R1条件"],
            },
            "citations": [{"ref": "R1"}],
            "recommended_articles": [
                {"paper_uid": "paper-1", "title": "Paper", "doi": "", "reason": "相关"}
            ],
            "comparison_bundle_uids": ["bundle-1"],
        }


class HarnessRuntimeTests(unittest.TestCase):
    def adapter(self, runtime) -> DeepSeekHarnessAdapter:
        return DeepSeekHarnessAdapter(runtime=runtime, backend=Backend())

    def budgeted(self, prepared: PreparedOutbound):
        raw = RawClient()
        return raw, BudgetedBusinessAIClient(client=raw, action=prepared)

    def test_librarian_one_budgeted_call_and_fixed_five_sections(self) -> None:
        prepared = action()
        raw, model = self.budgeted(prepared)
        result = self.adapter(Runtime()).execute_consumed(
            action=prepared,
            session_id="session-1",
            model=model,
            evidence=(OFFICIAL,),
        )
        self.assertEqual(raw.calls, 1)
        self.assertEqual(
            set(result["report"]),
            {
                "direct_conclusion", "evidence_matrix", "related_evidence",
                "database_gaps", "suggested_followups",
            },
        )
        self.assertEqual(result["harness"]["provider_id"], "deepseek")
        self.assertNotIn("endpoint", str(result).casefold())

    def test_one_turn_librarian_preverifies_frozen_seed_without_provider_tools(self) -> None:
        class OneTurnRuntime(Runtime):
            def execute(self, *, model, **kwargs):
                model.request_json(
                    MESSAGES,
                    task=kwargs["job"].task,
                    max_tokens=1000,
                    thinking=None,
                    temperature=0.1,
                )
                return {
                    "schema_version": "librarian-harness-result-v1",
                    "answer": "直接结论",
                    "report": {
                        "direct_conclusion": "有证据支持。",
                        "evidence_matrix": [{"ref": "R1"}],
                        "related_evidence": [],
                        "database_gaps": "尚缺人工金标准。",
                        "suggested_followups": ["核对R1条件"],
                    },
                    "citations": [{"ref": "R1"}],
                    "recommended_articles": [
                        {"paper_uid": "paper-1", "title": "Paper", "doi": "", "reason": "相关"}
                    ],
                    "comparison_bundle_uids": ["bundle-1"],
                }

        prepared = action()
        raw, model = self.budgeted(prepared)
        result = self.adapter(OneTurnRuntime()).execute_consumed(
            action=prepared,
            session_id="session-1",
            model=model,
            evidence=(OFFICIAL,),
            prompt={"question": "bounded"},
        )
        self.assertEqual(raw.calls, 1)
        self.assertEqual(result["citations"], [{"ref": "R1"}])
        self.assertEqual(
            [row["paper_uid"] for row in result["recommended_articles"]],
            ["paper-1"],
        )

    def test_selected_evidence_current_only_and_private_rejected(self) -> None:
        prepared = action("selected_evidence_chat")
        raw, model = self.budgeted(prepared)
        result = self.adapter(Runtime(selected=True)).execute_consumed(
            action=prepared,
            session_id="session-1",
            model=model,
            evidence=(OFFICIAL,),
            current_entity=OFFICIAL,
        )
        self.assertEqual((raw.calls, result["entity"]["entity_uid"]), (1, "item-1"))
        private = HarnessEvidenceIdentity("private", "mine", "item", "item-1")
        _, private_model = self.budgeted(prepared)
        with self.assertRaises(HarnessError) as raised:
            self.adapter(Runtime(selected=True)).execute_consumed(
                action=prepared,
                session_id="session-1",
                model=private_model,
                evidence=(private,),
                current_entity=private,
            )
        self.assertEqual(raised.exception.code, "harness_private_forbidden")

    def test_raw_client_and_mismatched_dependency_fail_closed(self) -> None:
        prepared = action()
        with self.assertRaises(HarnessError) as raised:
            self.adapter(Runtime()).execute_consumed(
                action=prepared,
                session_id="session-1",
                model=RawClient(),
                evidence=(OFFICIAL,),
            )
        self.assertEqual(raised.exception.code, "harness_invalid")

        runtime = Runtime()
        runtime.dependency_metadata = lambda: HarnessDependencySet(
            HarnessDependencyMetadata.from_pin(HARNESS_SDK_PROTOCOL_PIN),
            HarnessDependencyMetadata.from_pin(CORDIS_RUNTIME_PROTOCOL_PIN),
            "3.0.0",
        )
        with self.assertRaises(HarnessError) as raised:
            self.adapter(runtime)
        self.assertEqual(raised.exception.code, "harness_dependency_mismatch")

    def test_malformed_path_output_has_no_legacy_fallback(self) -> None:
        prepared = action()
        raw, model = self.budgeted(prepared)
        with self.assertRaises(HarnessError) as raised:
            self.adapter(Runtime(malformed=True)).execute_consumed(
                action=prepared,
                session_id="session-1",
                model=model,
                evidence=(OFFICIAL,),
            )
        self.assertEqual(raised.exception.code, "harness_output_invalid")
        self.assertEqual(raw.calls, 1)

    def test_model_bundle_echo_does_not_create_comparison_authority(self) -> None:
        class CrossBundle(Runtime):
            def execute(self, **kwargs):
                value = super().execute(**kwargs)
                value["comparison_bundle_uids"] = ["bundle-1", "bundle-2"]
                return value

        prepared = action()
        _, model = self.budgeted(prepared)
        result = self.adapter(CrossBundle()).execute_consumed(
            action=prepared,
            session_id="session-1",
            model=model,
            evidence=(OFFICIAL,),
        )
        self.assertEqual(result["comparison_bundle_uids"], ["bundle-1"])

    def test_librarian_discards_unverified_echoes_but_keeps_verified_authority(self) -> None:
        class Noisy(Runtime):
            def execute(self, **kwargs):
                value = super().execute(**kwargs)
                value["display_note"] = "not authoritative"
                value["report"]["model_note"] = "not authoritative"
                value["citations"].append({"ref": "R999", "title": "invented"})
                value["recommended_articles"].append(
                    {"paper_uid": "paper-invented", "reason": "invented"}
                )
                return value

        prepared = action()
        _, model = self.budgeted(prepared)
        result = self.adapter(Noisy()).execute_consumed(
            action=prepared,
            session_id="session-1",
            model=model,
            evidence=(OFFICIAL,),
        )
        self.assertEqual(result["citations"], [{"ref": "R1"}])
        self.assertEqual([row["paper_uid"] for row in result["recommended_articles"]], ["paper-1"])
        self.assertNotIn("display_note", result)
        self.assertNotIn("model_note", result["report"])

    def test_librarian_rejects_unverified_text_refs_and_actual_cross_bundle_refs(self) -> None:
        class BadText(Runtime):
            def execute(self, **kwargs):
                value = super().execute(**kwargs)
                value["answer"] = "R1 有证据，R999 没有核验。"
                return value

        prepared = action()
        _, model = self.budgeted(prepared)
        with self.assertRaises(HarnessError) as failed:
            self.adapter(BadText()).execute_consumed(
                action=prepared,
                session_id="session-1",
                model=model,
                evidence=(OFFICIAL,),
            )
        self.assertEqual(failed.exception.code, "harness_output_invalid")

        other = HarnessEvidenceIdentity(
            "official", "official-v1", "finding", "finding-2", "bundle-2"
        )

        class CrossBundleBackend(Backend):
            def citation_verify(self, refs):
                identities = (OFFICIAL, other)
                return [
                    identities[index].public_dict() | {"ref": ref}
                    for index, ref in enumerate(refs)
                ]

        class CrossBundle(Runtime):
            def execute(self, *, tools, **kwargs):
                kwargs["model"].request_json(
                    MESSAGES,
                    task=kwargs["job"].task,
                    max_tokens=1000,
                    thinking=None,
                    temperature=0.1,
                )
                tools.call("citation_verify", {"refs": ["R1", "R2"]})
                return {
                    "schema_version": "librarian-harness-result-v1",
                    "answer": "两条来源分别见 R1 与 R2。",
                    "report": {
                        "direct_conclusion": "来源分属两个不兼容包。",
                        "evidence_matrix": [],
                        "related_evidence": [],
                        "database_gaps": "不能合并比较。",
                        "suggested_followups": [],
                    },
                    "citations": [{"ref": "R1"}, {"ref": "R2"}],
                    "recommended_articles": [],
                    "comparison_bundle_uids": [],
                }

        _, model = self.budgeted(prepared)
        result = DeepSeekHarnessAdapter(
            runtime=CrossBundle(), backend=CrossBundleBackend()
        ).execute_consumed(
            action=prepared,
            session_id="session-1",
            model=model,
            evidence=(OFFICIAL, other),
        )
        self.assertEqual(result["comparison_bundle_uids"], [])
        self.assertEqual(result["citations"], [{"ref": "R1"}, {"ref": "R2"}])

        class QuantitativeCrossBundle(CrossBundle):
            def execute(self, **kwargs):
                value = super().execute(**kwargs)
                value["answer"] = "R1 的硬度为 500 HV，高于 R2 的 420 HV。"
                value["report"]["direct_conclusion"] = value["answer"]
                return value

        _, model = self.budgeted(prepared)
        with self.assertRaises(HarnessError) as quantitative:
            DeepSeekHarnessAdapter(
                runtime=QuantitativeCrossBundle(), backend=CrossBundleBackend()
            ).execute_consumed(
                action=prepared,
                session_id="session-1",
                model=model,
                evidence=(OFFICIAL, other),
            )
        self.assertEqual(quantitative.exception.code, "harness_output_invalid")

    def test_missing_bundle_identity_never_grants_comparison_authority(self) -> None:
        missing_bundle = HarnessEvidenceIdentity(
            "official", "official-v1", "finding", "finding-no-bundle", ""
        )

        class MissingBundleBackend(Backend):
            def citation_verify(self, refs):
                return [missing_bundle.public_dict() | {"ref": ref} for ref in refs]

        prepared = action()
        _, model = self.budgeted(prepared)
        result = DeepSeekHarnessAdapter(
            runtime=Runtime(), backend=MissingBundleBackend()
        ).execute_consumed(
            action=prepared,
            session_id="session-1",
            model=model,
            evidence=(missing_bundle,),
        )
        self.assertEqual(result["comparison_bundle_uids"], [])

    def test_cross_bundle_quantitative_gate_rejects_bare_scientific_range_and_ratio(self) -> None:
        for claim in (
            "R1 为 500，R2 为 420。",
            "R1 为 1.2e3，R2 为 9.0e2。",
            "R1 的范围是 1–50，R2 的比例为 2:1。",
            "R1 为 12 W/mK，versus R2 的 9 W/mK。",
        ):
            self.assertTrue(
                HarnessOutputProjector._has_quantitative_comparison(claim),
                claim,
            )

    def test_librarian_accepts_published_workspace_evidence(self) -> None:
        prepared = action()
        raw, model = self.budgeted(prepared)
        workspace = HarnessEvidenceIdentity(
            "workspace", "workspace", "item", "item-1", "bundle-1"
        )

        class WorkspaceBackend(Backend):
            def citation_verify(self, refs):
                return [workspace.public_dict() | {"ref": ref} for ref in refs]

        result = DeepSeekHarnessAdapter(
            runtime=Runtime(), backend=WorkspaceBackend()
        ).execute_consumed(
            action=prepared,
            session_id="session-1",
            model=model,
            evidence=(workspace,),
        )
        self.assertEqual(result["answer"], "直接结论")
        self.assertEqual(raw.calls, 1)


if __name__ == "__main__":
    unittest.main()
