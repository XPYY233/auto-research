from __future__ import annotations

import hashlib
import time
import unittest
from dataclasses import replace
from types import MappingProxyType

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
    task = "librarian_planning" if scope == "librarian" else "extraction"
    model = "deepseek-v4-flash" if scope == "librarian" else "deepseek-v4-pro"
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


def librarian_prompt(
    evidence: tuple[HarnessEvidenceIdentity, ...] = (OFFICIAL,),
) -> dict[str, object]:
    return {
        "question": "硬度",
        "seed_evidence": tuple(
            identity.public_dict()
            | {"ref": f"R{index}", "bundle_uid": identity.bundle_uid}
            for index, identity in enumerate(evidence, start=1)
        ),
    }


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
            prompt=librarian_prompt(),
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

    def test_librarian_accepts_minimal_model_report_and_rebuilds_local_sections(self) -> None:
        class MinimalReportRuntime(Runtime):
            def execute(self, **kwargs):
                value = super().execute(**kwargs)
                value["report"].pop("evidence_matrix")
                value["report"].pop("related_evidence")
                return value

        prepared = action()
        _raw, model = self.budgeted(prepared)
        result = self.adapter(MinimalReportRuntime()).execute_consumed(
            action=prepared,
            session_id="session-1",
            model=model,
            evidence=(OFFICIAL,),
            prompt=librarian_prompt(),
        )
        self.assertEqual(result["report"]["evidence_matrix"], [])
        self.assertEqual(result["report"]["related_evidence"], [])

    def test_librarian_normalizes_provider_representation_drift(self) -> None:
        class DriftedRuntime(Runtime):
            def execute(self, **kwargs):
                value = super().execute(**kwargs)
                value.pop("schema_version")
                value.pop("comparison_bundle_uids")
                value["report"] = {
                    "direct_conclusion": "R1 支持定性结论。",
                    "database_gaps": ["尚缺独立复核", "尚缺金标准"],
                    "suggested_followups": "继续核对 R1",
                }
                value["answer"] = "R1 支持定性结论。"
                value["citations"] = ["R1"]
                value.pop("recommended_articles")
                return value

        prepared = action()
        _raw, model = self.budgeted(prepared)
        result = self.adapter(DriftedRuntime()).execute_consumed(
            action=prepared,
            session_id="session-1",
            model=model,
            evidence=(OFFICIAL,),
            prompt=librarian_prompt(),
        )
        self.assertEqual(result["citations"], [{"ref": "R1"}])
        self.assertEqual(
            result["report"]["database_gaps"],
            "尚缺独立复核；尚缺金标准",
        )
        self.assertEqual(result["report"]["suggested_followups"], ["继续核对 R1"])

    def test_librarian_recovers_verified_refs_mentioned_in_prose(self) -> None:
        class MentionOnlyRuntime(Runtime):
            def execute(self, **kwargs):
                value = super().execute(**kwargs)
                value["answer"] = "现有证据支持定性结论，见 R1。"
                value["report"]["direct_conclusion"] = value["answer"]
                value["citations"] = []
                return value

        prepared = action()
        _raw, model = self.budgeted(prepared)
        result = self.adapter(MentionOnlyRuntime()).execute_consumed(
            action=prepared,
            session_id="session-1",
            model=model,
            evidence=(OFFICIAL,),
            prompt=librarian_prompt(),
        )
        self.assertEqual(result["citations"], [{"ref": "R1"}])

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
            prompt=librarian_prompt(),
        )
        self.assertEqual(raw.calls, 1)
        self.assertEqual(result["citations"], [{"ref": "R1"}])
        self.assertEqual(
            [row["paper_uid"] for row in result["recommended_articles"]],
            ["paper-1"],
        )

    def test_librarian_seed_identity_mismatch_fails_before_provider_call(self) -> None:
        prepared = action()
        raw, model = self.budgeted(prepared)
        prompt = librarian_prompt()
        prompt["seed_evidence"][0]["entity_uid"] = "entity-model-only"
        with self.assertRaises(HarnessError) as rejected:
            self.adapter(Runtime()).execute_consumed(
                action=prepared,
                session_id="session-1",
                model=model,
                evidence=(OFFICIAL,),
                prompt=prompt,
            )
        self.assertEqual(rejected.exception.code, "harness_output_invalid")
        self.assertEqual(raw.calls, 0)

    def test_retired_librarian_synthesis_job_fails_before_provider_call(self) -> None:
        prepared = replace(
            action(),
            task="librarian_synthesis",
            task_models=(("librarian_synthesis", "deepseek-v4-pro"),),
            runtime_task_models=(("librarian_synthesis", "deepseek-v4-pro"),),
            models=("deepseek-v4-pro",),
        )
        raw, model = self.budgeted(prepared)
        with self.assertRaises(HarnessError) as rejected:
            self.adapter(Runtime()).execute_consumed(
                action=prepared,
                session_id="session-1",
                model=model,
                evidence=(OFFICIAL,),
                prompt=librarian_prompt(),
            )
        self.assertEqual(rejected.exception.code, "harness_scope_unsupported")
        self.assertEqual(raw.calls, 0)

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
                prompt=librarian_prompt(),
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
            prompt=librarian_prompt(),
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
            prompt=librarian_prompt(),
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
                prompt=librarian_prompt(),
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
            prompt=librarian_prompt((OFFICIAL, other)),
        )
        self.assertEqual(result["comparison_bundle_uids"], [])

        self.assertEqual(result["citations"], [{"ref": "R1"}, {"ref": "R2"}])

        class SupportedConditionCrossBundle(CrossBundle):
            def execute(self, **kwargs):
                value = super().execute(**kwargs)
                value["answer"] = (
                    "在 300 °C 离子辐照条件下，两类材料呈现不同的定性演化，"
                    "分别见 R1 与 R2；现有证据不能用于计算跨论文差值。"
                )
                value["report"]["direct_conclusion"] = value["answer"]
                return value

        prompt = librarian_prompt((OFFICIAL, other))
        prompt["question"] = "在300 °C离子辐照条件下，两类材料有哪些可核验差异？"
        for row in prompt["seed_evidence"]:
            row["conditions_text"] = "300 °C，离子辐照"
        _, model = self.budgeted(prepared)
        contextual = DeepSeekHarnessAdapter(
            runtime=SupportedConditionCrossBundle(), backend=CrossBundleBackend()
        ).execute_consumed(
            action=prepared,
            session_id="session-1",
            model=model,
            evidence=(OFFICIAL, other),
            prompt=prompt,
        )
        self.assertIn("300 °C", contextual["answer"])
        self.assertEqual(contextual["comparison_bundle_uids"], [])

        immutable_prompt = dict(prompt)
        immutable_prompt["seed_evidence"] = tuple(
            MappingProxyType(dict(row)) for row in prompt["seed_evidence"]
        )
        _, model = self.budgeted(prepared)
        immutable_contextual = DeepSeekHarnessAdapter(
            runtime=SupportedConditionCrossBundle(), backend=CrossBundleBackend()
        ).execute_consumed(
            action=prepared,
            session_id="session-1",
            model=model,
            evidence=(OFFICIAL, other),
            prompt=immutable_prompt,
        )
        self.assertIn("300 °C", immutable_contextual["answer"])

        class ExactCitedValuesCrossBundle(CrossBundle):
            def execute(self, **kwargs):
                value = super().execute(**kwargs)
                value["answer"] = (
                    "R1 报告硬度为 500 HV。R2 报告硬度为 420 HV。"
                    "这些是逐篇引用值，不计算跨论文差值。"
                )
                value["report"]["direct_conclusion"] = value["answer"]
                return value

        cited_value_prompt = librarian_prompt((OFFICIAL, other))
        cited_value_prompt["seed_evidence"][0]["evidence_text"] = "硬度 500 HV"
        cited_value_prompt["seed_evidence"][1]["evidence_text"] = "硬度 420 HV"
        _, model = self.budgeted(prepared)
        cited_values = DeepSeekHarnessAdapter(
            runtime=ExactCitedValuesCrossBundle(), backend=CrossBundleBackend()
        ).execute_consumed(
            action=prepared,
            session_id="session-1",
            model=model,
            evidence=(OFFICIAL, other),
            prompt=cited_value_prompt,
        )
        self.assertIn("500 HV", cited_values["answer"])
        self.assertEqual(cited_values["comparison_bundle_uids"], [])

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
                prompt=librarian_prompt((OFFICIAL, other)),
            )
        self.assertEqual(quantitative.exception.code, "harness_output_invalid")

        _, model = self.budgeted(prepared)
        with self.assertRaises(HarnessError) as derived_comparison:
            DeepSeekHarnessAdapter(
                runtime=QuantitativeCrossBundle(), backend=CrossBundleBackend()
            ).execute_consumed(
                action=prepared,
                session_id="session-1",
                model=model,
                evidence=(OFFICIAL, other),
                prompt=cited_value_prompt,
            )
        self.assertEqual(
            derived_comparison.exception.code,
            "harness_output_invalid",
        )

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
            prompt=librarian_prompt((missing_bundle,)),
        )
        self.assertEqual(result["comparison_bundle_uids"], [])

        class QuantitativeMissingBundle(Runtime):
            def execute(self, **kwargs):
                value = super().execute(**kwargs)
                value["answer"] = "R1 的硬度为 500。"
                value["report"]["direct_conclusion"] = value["answer"]
                return value

        _, model = self.budgeted(prepared)
        with self.assertRaises(HarnessError) as rejected:
            DeepSeekHarnessAdapter(
                runtime=QuantitativeMissingBundle(), backend=MissingBundleBackend()
            ).execute_consumed(
                action=prepared,
                session_id="session-1",
                model=model,
                evidence=(missing_bundle,),
                prompt=librarian_prompt((missing_bundle,)),
            )
        self.assertEqual(rejected.exception.code, "harness_output_invalid")

    def test_cross_bundle_quantitative_gate_rejects_bare_scientific_range_and_ratio(self) -> None:
        for claim in (
            "R1 为 500，R2 为 420。",
            "R1 为 1.2e3，R2 为 9.0e2。",
            "R1 的范围是 1–50，R2 的比例为 2:1。",
            "R1 为 12%，R2 为 9%。",
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
            prompt=librarian_prompt((workspace,)),
        )
        self.assertEqual(result["answer"], "直接结论")
        self.assertEqual(raw.calls, 1)


if __name__ == "__main__":
    unittest.main()
