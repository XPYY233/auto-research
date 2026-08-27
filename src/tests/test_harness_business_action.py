from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import time
import unittest

from auto_research.ai.business_actions import BusinessActionError, HarnessBudgetedBusinessAIClient
from auto_research.ai.harness_contract import (
    CORDIS_RUNTIME_PROTOCOL_PIN,
    HARNESS_SDK_PROTOCOL_PIN,
    HarnessDependencyMetadata,
    HarnessDependencySet,
    HarnessError,
)
from auto_research.ai.harness_official_sdk import safe_composition_metadata
from auto_research.ai.prepared_actions import PreparedOutbound
from auto_research.evidence.harness_business_action import harness_business_ports
from auto_research.evidence.harness_federated_backend import sanitize_workspace_documents


def document(uid="item-1", kind="item", **updates):
    value = {
        "source_scope": "official",
        "source_id": "official-v1",
        "entity_type": kind,
        "entity_uid": uid,
        "paper_uid": "paper-1",
        "bundle_uid": "bundle-1",
        "article_title": "Tungsten irradiation",
        "doi": "10.1000/example",
        "meaning": "辐照硬度",
        "value_text": "4.2 GPa",
        "source_page": 5,
        "source_locator": "Table 2",
        "source_excerpt": "The hardness was 4.2 GPa.",
    }
    value.update(updates)
    return value


def conversation_entry(row, ref="R1", bundle_ref="B1"):
    return {
        "ref": ref,
        "source_scope": row["source_scope"],
        "source_id": row["source_id"],
        "entity_type": row["entity_type"],
        "entity_uid": row["entity_uid"],
        "bundle_ref": bundle_ref,
    }


class Session:
    def __init__(self):
        self.documents = [
            document(),
            document("finding-1", "finding", finding_text="硬度上升"),
        ]
        self.fingerprint = "f" * 64

    def status(self):
        return {
            "official_ready": True,
            "official_source": {
                "source_scope": "official",
                "source_id": "official-v1",
                "fingerprint": self.fingerprint,
            },
        }

    def search(self, query="", **_filters):
        return SimpleNamespace(
            hits=tuple(SimpleNamespace(document=row) for row in self.documents)
        )

    def get(self, **identity):
        return next(dict(row) for row in self.documents if row["entity_uid"] == identity["entity_uid"])


class NaturalLanguageSession(Session):
    def __init__(self):
        super().__init__()
        self.queries = []

    def search(self, query="", **_filters):
        self.queries.append(query)
        rows = self.documents if query.casefold() == "irradiation" else ()
        return SimpleNamespace(
            hits=tuple(SimpleNamespace(document=row) for row in rows)
        )


class Workspace:
    def __init__(self):
        self.fingerprint = "w" * 64
        self.documents = [
            document(
                "31",
                source_scope="workspace",
                source_id="workspace",
                entity_uid="31",
                paper_uid="workspace-paper-1",
                bundle_uid="workspace-paper-1",
                article_title="Published local irradiation study",
            )
        ]

    def binding(self):
        return "workspace", self.fingerprint

    def candidates(self, *, query, limit=64):
        return tuple(dict(row) for row in self.documents[:limit])

    def get(self, *, entity_type, entity_uid):
        return next(
            dict(row)
            for row in self.documents
            if row["entity_type"] == entity_type and row["entity_uid"] == entity_uid
        )


class ResearchMemory:
    def __init__(self, items):
        self.items = items
        self.calls = 0

    def approved_items(self):
        self.calls += 1
        return tuple(self.items)


class RawClient:
    def __init__(self):
        self.calls = 0
        self.last_task = None

    def request_tool_message(self, messages, tools, **kwargs):
        self.calls += 1
        self.last_task = kwargs.get("task")
        return {"role": "assistant", "content": "ok"}


class Runtime:
    def __init__(self, selected=False, fail=False):
        self.selected = selected
        self.fail = fail

    def dependency_metadata(self):
        return HarnessDependencySet(
            HarnessDependencyMetadata.from_pin(HARNESS_SDK_PROTOCOL_PIN),
            HarnessDependencyMetadata.from_pin(CORDIS_RUNTIME_PROTOCOL_PIN),
            "2.12.0",
        )

    def composition_metadata(self):
        return safe_composition_metadata()

    def execute(self, *, job, model, tools, prompt=None):
        if self.fail:
            raise RuntimeError("provider body must not escape")
        model.request_tool_message(
            [{"role": "user", "content": "bounded"}],
            [{
                "type": "function",
                "function": {
                    "name": "mcp__auto_research__evidence_detail",
                    "description": "detail",
                    "parameters": {"type": "object"},
                    "strict": True,
                },
            }],
            task=job.task,
            max_tokens=1000,
            temperature=0.1,
        )
        if self.selected:
            return {
                "schema_version": "selected-evidence-harness-model-v1",
                "answer": "当前证据显示硬度为4.2 GPa。",
                "limitations": ["仅限当前官方证据。"],
            }
        search = tools.call(
            "exact_search",
            {"query": "硬度", "entity_types": ["item", "finding"], "limit": 10},
        )
        refs = [row["ref"] for row in search]
        tools.call("citation_verify", {"refs": refs})
        recommendations = tools.call("recommend_papers", {"question": "硬度", "limit": 3})
        return {
            "schema_version": "librarian-harness-result-v1",
            "answer": "官方证据显示辐照后硬度上升。",
            "report": {
                "direct_conclusion": "硬度上升。",
                "evidence_matrix": [],
                "related_evidence": ["同论文结论支持该趋势。"],
                "database_gaps": "缺少更多温度条件。",
                "suggested_followups": ["硬度"],
            },
            "citations": [{"ref": ref} for ref in refs],
            "recommended_articles": recommendations,
            "comparison_bundle_uids": ["bundle-1"],
        }


class InvalidOutputRuntime(Runtime):
    def execute(self, *, job, model, tools, prompt=None):
        model.request_tool_message(
            [{"role": "user", "content": "bounded"}],
            [],
            task=job.task,
            max_tokens=1_000,
            temperature=0.0,
        )
        raise HarnessError("harness_output_invalid")


def action(draft, scope):
    now = int(time.time())
    task = draft.call_plan[0].task if scope == "librarian" else "extraction"
    task_models = (
        ((task, "deepseek-v4-flash"),)
        if scope == "librarian"
        else (("extraction", "deepseek-v4-pro"),)
    )
    return PreparedOutbound(
        action_id="action-1",
        session_digest="session-digest-1",
        scope=scope,
        provider_id="deepseek",
        runtime_revision=1,
        credential_generation=1,
        runtime_activation="connection_verified",
        runtime_task_models=(
            ("analysis", "deepseek-v4-pro"),
            ("extraction", "deepseek-v4-pro"),
            ("librarian_planning", "deepseek-v4-flash"),
            ("librarian_synthesis", "deepseek-v4-pro"),
        ),
        task=task,
        task_models=task_models,
        models=tuple(sorted({model for _task, model in task_models})),
        executor_id="librarian_business_executor" if scope == "librarian" else "selected_evidence_chat_executor",
        executor_version="v1",
        estimated_calls=draft.estimated_calls,
        max_calls=draft.max_calls,
        max_tokens=draft.max_tokens,
        outbound={
            "payload": draft.outbound,
            "call_plan": [call.canonical_dict() for call in draft.call_plan],
        },
        outbound_digest="a" * 64,
        manifest_digest="b" * 64,
        units=draft.content_units,
        byte_count=1000,
        issued_at=now,
        expires_at=now + 300,
    )


class HarnessBusinessActionTests(unittest.TestCase):
    def test_workspace_sanitizer_replaces_internal_ids_with_stable_public_identity(self):
        rows = sanitize_workspace_documents(
            (
                {
                    "entity_type": "item",
                    "entity_id": 31,
                    "paper_id": 7,
                    "stable_key": "published-stable-key",
                    "article_title": "Published local irradiation study",
                    "doi": "10.1000/workspace",
                    "year": 2026,
                    "first_author": "A Researcher",
                    "meaning": "辐照硬度",
                    "value_text": "4.2",
                    "unit": "GPa",
                    "source_page": 5,
                },
            )
        )
        self.assertEqual(len(rows), 1)
        self.assertRegex(rows[0]["paper_uid"], r"^paper_[0-9a-f]{32}$")
        self.assertRegex(rows[0]["entity_uid"], r"^entity_item_[0-9a-f]{32}$")
        self.assertNotEqual(rows[0]["entity_uid"], "31")
        self.assertNotIn("paper_id", rows[0])

    def test_librarian_prepares_official_snapshot_and_projects_v3(self):
        session = Session()
        runtime = Runtime()
        ports = harness_business_ports(session=session, runtime=runtime)
        draft = ports.librarian.assembler.assemble(
            {
                "question": "辐照后硬度如何变化？",
                "conversation_id": "conversation-1",
                "history": [],
            }
        )
        self.assertEqual((draft.max_calls, draft.max_tokens), (1, 2_400))
        self.assertEqual(draft.call_plan[0].task, "librarian_planning")
        seed = draft.outbound["prompt"]["seed_evidence"]
        self.assertTrue(seed)
        self.assertEqual(seed[0]["ref"], "R1")
        self.assertEqual(draft.content_units[0].kind, "harness_literature")
        prepared = action(draft, "librarian")
        raw = RawClient()
        client = HarnessBudgetedBusinessAIClient(client=raw, action=prepared)
        internal = ports.librarian.executor.execute(action=prepared, ai_client=client)
        result = ports.librarian.projector.project(internal)
        self.assertEqual(raw.calls, 1)
        self.assertEqual(raw.last_task, "librarian_planning")
        self.assertEqual(result["librarian_core_version"], "librarian-v3")
        self.assertEqual(result["report"]["schema_version"], "research-report-v1")
        self.assertEqual({row["source_scope"] for row in result["results"]}, {"official"})
        self.assertTrue(result["recommended_articles"])
        jump = result["recommended_articles"][0]["jump_evidence"]
        self.assertEqual(jump["source_scope"], "official")
        self.assertIn(jump["entity_type"], {"item", "finding", "table", "figure"})
        self.assertTrue(jump["entity_uid"])
        self.assertNotIn("path", str(result).casefold())

    def test_librarian_accepts_immutable_seed_after_prepared_action_freeze(self):
        """Prepared actions freeze JSON arrays to tuples before execution."""

        ports = harness_business_ports(session=Session(), runtime=Runtime())
        draft = ports.librarian.assembler.assemble(
            {
                "question": "辐照后硬度如何变化？",
                "conversation_id": "frozen-seed",
                "history": [],
            }
        )
        prepared = action(draft, "librarian")
        outbound = dict(prepared.outbound)
        payload = dict(outbound["payload"])
        prompt = dict(payload["prompt"])
        prompt["seed_evidence"] = tuple(prompt["seed_evidence"])
        payload["prompt"] = prompt
        outbound["payload"] = payload
        prepared = replace(prepared, outbound=outbound)
        raw = RawClient()
        internal = ports.librarian.executor.execute(
            action=prepared,
            ai_client=HarnessBudgetedBusinessAIClient(
                client=raw, action=prepared
            ),
        )
        result = ports.librarian.projector.project(internal)
        self.assertEqual(raw.calls, 1)
        self.assertTrue(result["results"])

    def test_recommendations_are_bound_to_recalled_documents(self):
        session = Session()
        ports = harness_business_ports(session=session, runtime=Runtime())
        draft = ports.librarian.assembler.assemble(
            {"question": "硬度", "conversation_id": "c", "history": []}
        )
        prepared = action(draft, "librarian")
        internal = ports.librarian.executor.execute(
            action=prepared,
            ai_client=HarnessBudgetedBusinessAIClient(
                client=RawClient(), action=prepared
            ),
        )
        internal["raw"]["recommended_articles"] = [
            {
                "paper_uid": "paper-not-recalled",
                "title": "Fabricated article",
                "doi": "10.0000/fabricated",
                "reason": "model-only",
            },
            {
                "paper_uid": "paper-1",
                "title": "Tampered article title",
                "doi": "10.0000/tampered",
                "reason": "matched source",
            },
        ]
        result = ports.librarian.projector.project(internal)
        self.assertEqual(len(result["recommended_articles"]), 1)
        article = result["recommended_articles"][0]
        self.assertEqual(article["article_title"], "Tungsten irradiation")
        self.assertEqual(article["doi"], "10.1000/example")
        self.assertEqual(article["jump_evidence"]["paper_uid"], "paper-1")
        self.assertNotIn("fabricated", repr(result["recommended_articles"]).casefold())
        self.assertNotIn("tampered", repr(result["recommended_articles"]).casefold())

    def test_invalid_librarian_model_output_uses_local_evidence_without_second_call(self):
        ports = harness_business_ports(
            session=Session(), runtime=InvalidOutputRuntime()
        )
        draft = ports.librarian.assembler.assemble(
            {"question": "硬度", "conversation_id": "fallback", "history": []}
        )
        prepared = action(draft, "librarian")
        raw = RawClient()
        internal = ports.librarian.executor.execute(
            action=prepared,
            ai_client=HarnessBudgetedBusinessAIClient(client=raw, action=prepared),
        )
        result = ports.librarian.projector.project(internal)
        self.assertEqual(raw.calls, 1)
        self.assertEqual(
            result["summary_mode"],
            "local_deterministic_after_harness_rejection",
        )
        self.assertEqual(result["agent"]["runtime"], "auto-research-local-fallback")
        self.assertTrue(result["results"])
        self.assertTrue(all(row["agent_cited"] for row in result["results"]))
        self.assertIn("没有采用模型回答", result["answer"])
        self.assertNotIn("provider body", repr(result))

    def test_librarian_combines_official_and_published_workspace_only(self):
        workspace = Workspace()
        ports = harness_business_ports(
            session=Session(), runtime=Runtime(), workspace=workspace
        )
        draft = ports.librarian.assembler.assemble(
            {"question": "硬度", "conversation_id": "c", "history": []}
        )
        self.assertEqual(
            {row["source_scope"] for row in draft.outbound["documents"]},
            {"official", "workspace"},
        )
        self.assertEqual(
            set(draft.outbound["source_binding"]), {"official", "workspace"}
        )
        self.assertNotIn("private", str(draft.outbound).casefold())
        prepared = action(draft, "librarian")
        raw = RawClient()
        client = HarnessBudgetedBusinessAIClient(client=raw, action=prepared)
        internal = ports.librarian.executor.execute(
            action=prepared, ai_client=client
        )
        result = ports.librarian.projector.project(internal)
        self.assertEqual(
            {row["source_scope"] for row in result["results"]},
            {"official", "workspace"},
        )
        self.assertEqual(raw.calls, 1)

    def test_librarian_uses_only_enabled_relevant_revalidated_research_memory(self):
        source = ResearchMemory(
            [
                {
                    "schema_version": "research-memory-item-v1",
                    "memory_uid": "mem-secret-store-id",
                    "title": "辐照硬度结论",
                    "content": "已确认辐照后硬度上升。",
                    "source_refs": [
                        {
                            "source_scope": "official",
                            "source_id": "official-v1",
                            "entity_type": "item",
                            "entity_uid": "item-1",
                            "page": 5,
                            "title": "辐照硬度",
                        }
                    ],
                    "approval": "user_approved",
                    "origin": "assistant_suggested",
                    "created_at": "2026-08-01T00:00:00+00:00",
                    "updated_at": "2026-08-02T00:00:00+00:00",
                }
            ]
        )
        ports = harness_business_ports(
            session=Session(), runtime=Runtime(), research_memory=source
        )
        disabled = ports.librarian.assembler.assemble(
            {"question": "辐照硬度", "conversation_id": "disabled", "history": []}
        )
        self.assertEqual(source.calls, 0)
        self.assertEqual(disabled.outbound["prompt"]["research_memory"], [])
        enabled = ports.librarian.assembler.assemble(
            {
                "question": "辐照硬度",
                "conversation_id": "enabled",
                "history": [],
                "use_research_memory": True,
            }
        )
        self.assertEqual(source.calls, 1)
        context = enabled.outbound["prompt"]["research_memory"]
        self.assertEqual(len(context), 1)
        self.assertEqual(context[0]["source_refs"][0]["entity_uid"], "item-1")
        self.assertNotIn("mem-secret-store-id", repr(enabled.outbound))
        prepared = action(enabled, "librarian")
        result = ports.librarian.projector.project(
            ports.librarian.executor.execute(
                action=prepared,
                ai_client=HarnessBudgetedBusinessAIClient(
                    client=RawClient(), action=prepared
                ),
            )
        )
        self.assertEqual(result["research_memory_count"], 1)

    def test_librarian_drops_memory_when_source_page_no_longer_matches(self):
        source = ResearchMemory(
            [
                {
                    "schema_version": "research-memory-item-v1",
                    "memory_uid": "mem-stale",
                    "title": "辐照硬度",
                    "content": "过期来源页。",
                    "source_refs": [
                        {
                            "source_scope": "official",
                            "source_id": "official-v1",
                            "entity_type": "item",
                            "entity_uid": "item-1",
                            "page": 99,
                            "title": "过期证据",
                        }
                    ],
                    "approval": "user_approved",
                    "origin": "user_created",
                    "created_at": "2026-08-01T00:00:00+00:00",
                    "updated_at": "2026-08-02T00:00:00+00:00",
                }
            ]
        )
        ports = harness_business_ports(
            session=Session(), runtime=Runtime(), research_memory=source
        )
        draft = ports.librarian.assembler.assemble(
            {
                "question": "辐照硬度",
                "conversation_id": "stale",
                "history": [],
                "use_research_memory": True,
            }
        )
        self.assertEqual(draft.outbound["prompt"]["research_memory"], [])

    def test_librarian_memory_is_explicit_opt_in_and_fails_closed_without_store(self):
        ports = harness_business_ports(session=Session(), runtime=Runtime())
        for invalid in (None, 1, "true", []):
            body = {
                "question": "辐照硬度",
                "conversation_id": "invalid-memory-toggle",
                "history": [],
                "use_research_memory": invalid,
            }
            with self.subTest(value=invalid), self.assertRaises(BusinessActionError) as raised:
                ports.librarian.assembler.assemble(body)
            self.assertEqual(raised.exception.code, "business_action_invalid")
        with self.assertRaises(BusinessActionError) as unavailable:
            ports.librarian.assembler.assemble(
                {
                    "question": "辐照硬度",
                    "conversation_id": "memory-store-unavailable",
                    "history": [],
                    "use_research_memory": True,
                }
            )
        self.assertEqual(unavailable.exception.cause_code, "research_memory_store_unavailable")
        self.assertEqual(unavailable.exception.stage, "librarian_memory_context")
        self.assertEqual(unavailable.exception.next_action, "manage_research_memory")

    def test_librarian_filters_expansion_before_model_and_freezes_local_reasoning(self):
        session = Session()
        session.documents = [
            document(
                "direct",
                paper_uid="paper-direct",
                material_focus="W",
                conditions_text="W，300 °C，离子辐照，辐照后",
                meaning="硬度",
                source_excerpt="The W hardness was measured at 300 °C after ion irradiation.",
            ),
            document(
                "adjacent",
                paper_uid="paper-adjacent",
                material_focus="W",
                conditions_text="W，300 °C，离子辐照，辐照后",
                meaning="弹性模量",
                source_excerpt="The elastic modulus was measured under the same conditions.",
            ),
            document(
                "expansion",
                paper_uid="paper-expansion",
                material_focus="Ta",
                conditions_text="Ta，1200 °C，中子辐照，未辐照",
                meaning="热导率",
                source_excerpt="A different material and irradiation condition.",
            ),
        ]
        ports = harness_business_ports(session=session, runtime=Runtime())
        draft = ports.librarian.assembler.assemble(
            {
                "question": "W 在 300 °C 离子辐照后的硬度是多少？",
                "conversation_id": "hard-conditions",
                "history": [],
            }
        )
        self.assertEqual(
            {row["entity_uid"] for row in draft.outbound["documents"]},
            {"direct", "adjacent"},
        )
        seed = draft.outbound["prompt"]["seed_evidence"]
        self.assertEqual(
            {row["match_class"] for row in seed}, {"direct", "adjacent"}
        )
        self.assertTrue(all(row.get("bundle_uid") for row in seed))
        self.assertIn("source_excerpt", seed[0])
        self.assertNotIn(
            "expansion",
            {row["entity_uid"] for row in draft.outbound["documents"]},
        )

    def test_quantitative_cross_bundle_comparison_fails_before_provider(self):
        session = Session()
        session.documents = [
            document("paper-a", paper_uid="paper-a", article_title="Paper A"),
            document("paper-b", paper_uid="paper-b", article_title="Paper B"),
        ]

        class CountingRuntime(Runtime):
            def __init__(self):
                super().__init__()
                self.execute_calls = 0

            def execute(self, **kwargs):
                self.execute_calls += 1
                return super().execute(**kwargs)

        runtime = CountingRuntime()
        ports = harness_business_ports(session=session, runtime=runtime)
        with self.assertRaises(BusinessActionError) as rejected:
            ports.librarian.assembler.assemble(
                {
                    "question": "比较这些论文中硬度的数值差异",
                    "conversation_id": "cross-bundle",
                    "history": [],
                }
            )
        self.assertEqual(rejected.exception.cause_code, "unsupported_comparison")
        self.assertEqual(rejected.exception.stage, "librarian_local_preflight")
        self.assertEqual(runtime.execute_calls, 0)

    def test_local_librarian_intent_returns_without_provider_or_readiness(self):
        ports = harness_business_ports(session=Session(), runtime=Runtime())
        local = ports.librarian.assembler.local_result(
            {
                "question": "你能做什么？",
                "conversation_id": "local-intent",
                "history": [],
            }
        )
        self.assertIsNotNone(local)
        projected = ports.librarian.projector.project(local)
        self.assertEqual(projected["summary_mode"], "local_capability_manifest")
        self.assertEqual(projected["search_operations"], 0)
        self.assertEqual(projected["tool_calls"], [])
        self.assertEqual(projected["intent"]["kind"], "system_capability")

    def test_qualitative_review_freezes_local_review_map_and_one_call_budget(self):
        session = Session()
        session.documents = [
            document(
                "defect",
                paper_uid="paper-defect",
                meaning="缺陷形成",
                finding_text="辐照后形成空位与间隙原子",
            ),
            document(
                "diffusion",
                paper_uid="paper-diffusion",
                meaning="扩散迁移",
                finding_text="辐照影响缺陷迁移与扩散",
            ),
        ]
        ports = harness_business_ports(session=session, runtime=Runtime())
        draft = ports.librarian.assembler.assemble(
            {
                "question": "辐照研究有哪些用途？请做定性综述",
                "conversation_id": "review-map",
                "history": [],
            }
        )
        prompt = draft.outbound["prompt"]
        refs = {row["ref"] for row in prompt["seed_evidence"]}
        self.assertEqual(prompt["intent"]["kind"], "research_review")
        self.assertTrue(prompt["review_map"])
        self.assertTrue(
            all(
                set(theme["representative_refs"]).issubset(refs)
                for theme in prompt["review_map"]
            )
        )
        self.assertEqual((draft.max_calls, draft.max_tokens), (1, 2_400))
        self.assertEqual(draft.call_plan[0].task, "librarian_planning")

    def test_projector_rejects_bundle_tamper_and_drops_unanswerable_followup(self):
        ports = harness_business_ports(session=Session(), runtime=Runtime())
        draft = ports.librarian.assembler.assemble(
            {"question": "硬度", "conversation_id": "projection", "history": []}
        )
        prepared = action(draft, "librarian")
        internal = ports.librarian.executor.execute(
            action=prepared,
            ai_client=HarnessBudgetedBusinessAIClient(
                client=RawClient(), action=prepared
            ),
        )
        internal["raw"]["report"]["suggested_followups"] = ["催化光学电池性能"]
        result = ports.librarian.projector.project(internal)
        self.assertNotIn("催化", repr(result["suggested_actions"]))
        self.assertTrue(all(row["answerable"] for row in result["suggested_actions"]))

        tampered = dict(internal)
        tampered["raw"] = dict(internal["raw"])
        tampered["raw"]["comparison_bundle_uids"] = ["bu-tampered"]
        with self.assertRaises(BusinessActionError):
            ports.librarian.projector.project(tampered)

    def test_private_workspace_candidate_is_rejected_before_action(self):
        workspace = Workspace()
        workspace.documents = [
            document(
                "private-item",
                source_scope="private",
                source_id="private-library",
                paper_uid="private-paper",
            )
        ]
        ports = harness_business_ports(
            session=Session(), runtime=Runtime(), workspace=workspace
        )
        with self.assertRaises(BusinessActionError) as rejected:
            ports.librarian.assembler.assemble(
                {"question": "硬度", "conversation_id": "private", "history": []}
            )
        self.assertEqual(rejected.exception.cause_code, "harness_private_forbidden")
        self.assertEqual(rejected.exception.stage, "librarian_local_preflight")

    def test_librarian_decomposes_natural_question_before_bounded_recall(self):
        session = NaturalLanguageSession()
        ports = harness_business_ports(session=session, runtime=Runtime())
        question = (
            "What traceable evidence describes irradiation damage evolution? "
            "Include citations, related papers, and limitations."
        )
        draft = ports.librarian.assembler.assemble(
            {"question": question, "conversation_id": "natural", "history": []}
        )
        self.assertIn(question, session.queries)
        self.assertIn("irradiation", session.queries)
        self.assertEqual(len(draft.outbound["documents"]), 2)
        self.assertIn("irradiation", draft.outbound["prompt"]["recall_queries"])

    def test_librarian_seed_and_frozen_documents_share_the_same_16_rows(self):
        session = Session()
        session.documents = [
            document(f"item-{index}", value_text=str(index))
            for index in range(1, 25)
        ]
        ports = harness_business_ports(session=session, runtime=Runtime())
        draft = ports.librarian.assembler.assemble(
            {"question": "硬度", "conversation_id": "bounded", "history": []}
        )
        documents = draft.outbound["documents"]
        seed = draft.outbound["prompt"]["seed_evidence"]
        self.assertEqual((len(documents), len(seed)), (16, 16))
        self.assertEqual(
            [row["entity_uid"] for row in documents],
            [row["entity_uid"] for row in seed],
        )

    def test_librarian_empty_recall_exposes_safe_recovery_lineage(self):
        session = NaturalLanguageSession()
        session.documents = []
        ports = harness_business_ports(session=session, runtime=Runtime())
        with self.assertRaises(BusinessActionError) as raised:
            ports.librarian.assembler.assemble(
                {"question": "unknown phenomenon", "conversation_id": "empty", "history": []}
            )
        self.assertEqual(raised.exception.cause_code, "harness_recall_empty")
        self.assertEqual(raised.exception.stage, "harness_prepare")
        self.assertEqual(raised.exception.next_action, "refine_librarian_question")

    def test_librarian_r1_followup_revalidates_current_bundle_and_projects_v3(self):
        session = Session()
        evidence = [
            conversation_entry(session.documents[0], "R1", "B1"),
            conversation_entry(session.documents[1], "R2", "B1"),
        ]
        ports = harness_business_ports(session=session, runtime=Runtime())
        draft = ports.librarian.assembler.assemble(
            {
                "question": "请详细解释 R1",
                "conversation_id": "followup-r1",
                "history": [],
                "conversation_evidence": evidence,
            }
        )
        prompt = draft.outbound["prompt"]
        self.assertEqual(prompt["intent"]["kind"], "followup_ref")
        self.assertEqual([row["ref"] for row in prompt["seed_evidence"]], ["R1", "R2"])
        self.assertEqual([row["id"] for row in prompt["evidence_bundles"]], ["B1"])
        self.assertEqual(prompt["recall_queries"], [])
        prepared = action(draft, "librarian")
        raw = RawClient()
        result = ports.librarian.projector.project(
            ports.librarian.executor.execute(
                action=prepared,
                ai_client=HarnessBudgetedBusinessAIClient(client=raw, action=prepared),
            )
        )
        self.assertEqual(raw.calls, 1)
        self.assertIsNone(result["research_state"])
        self.assertEqual(result["state_token"], "")
        self.assertEqual({row["ref"] for row in result["results"]}, {"R1", "R2"})

    def test_librarian_b1_followup_and_restart_equivalent_request_are_stable(self):
        first_session = Session()
        evidence = [
            conversation_entry(first_session.documents[0], "R7", "B1"),
            conversation_entry(first_session.documents[1], "R9", "B1"),
        ]
        request = {
            "question": "请总结 B1 的共同结论",
            "conversation_id": "restart-equivalent",
            "history": [],
            "conversation_evidence": evidence,
        }
        first = harness_business_ports(
            session=first_session, runtime=Runtime()
        ).librarian.assembler.assemble(request)
        second = harness_business_ports(
            session=Session(), runtime=Runtime()
        ).librarian.assembler.assemble(request)
        self.assertEqual(first.outbound["prompt"], second.outbound["prompt"])
        self.assertEqual(first.outbound["documents"], second.outbound["documents"])
        self.assertEqual(
            [row["ref"] for row in first.outbound["prompt"]["seed_evidence"]],
            ["R7", "R9"],
        )
        self.assertEqual(
            first.outbound["prompt"]["intent"]["kind"], "followup_bundle"
        )
        ports = harness_business_ports(session=first_session, runtime=Runtime())
        prepared = action(first, "librarian")
        result = ports.librarian.projector.project(
            ports.librarian.executor.execute(
                action=prepared,
                ai_client=HarnessBudgetedBusinessAIClient(
                    client=RawClient(), action=prepared
                ),
            )
        )
        self.assertEqual({row["ref"] for row in result["results"]}, {"R7", "R9"})

    def test_conversation_evidence_is_ignored_for_ordinary_recall(self):
        session = Session()
        stale_but_syntactically_valid = conversation_entry(
            session.documents[1], "R9", "B7"
        )
        stale_but_syntactically_valid["source_id"] = "official-old"
        evidence = [stale_but_syntactically_valid]
        ports = harness_business_ports(session=session, runtime=Runtime())
        baseline = ports.librarian.assembler.assemble(
            {"question": "硬度", "conversation_id": "ordinary", "history": []}
        )
        carried = ports.librarian.assembler.assemble(
            {
                "question": "硬度",
                "conversation_id": "ordinary",
                "history": [],
                "conversation_evidence": evidence,
            }
        )
        self.assertEqual(
            baseline.outbound["prompt"]["seed_evidence"],
            carried.outbound["prompt"]["seed_evidence"],
        )
        self.assertEqual(baseline.outbound["documents"], carried.outbound["documents"])

    def test_followup_rejects_stale_forged_private_duplicate_and_unknown_before_model(self):
        class CountingRuntime(Runtime):
            def __init__(self):
                super().__init__()
                self.execute_calls = 0

            def execute(self, **kwargs):
                self.execute_calls += 1
                return super().execute(**kwargs)

        cases = []
        stale_session = Session()
        stale = conversation_entry(stale_session.documents[0])
        stale["source_id"] = "official-old"
        cases.append((stale_session, [stale], "请解释 R1", "librarian_conversation_evidence_stale"))
        forged_session = Session()
        forged = conversation_entry(forged_session.documents[0])
        forged["entity_uid"] = "forged-entity"
        cases.append((forged_session, [forged], "请解释 R1", "librarian_conversation_evidence_stale"))
        duplicate_session = Session()
        duplicate = conversation_entry(duplicate_session.documents[0])
        duplicate_other = conversation_entry(duplicate_session.documents[1])
        cases.append((duplicate_session, [duplicate, duplicate_other], "请解释 R1", "librarian_conversation_evidence_duplicate"))
        unknown_session = Session()
        cases.append((unknown_session, [conversation_entry(unknown_session.documents[0])], "请解释 R2", "librarian_conversation_evidence_unknown_ref"))
        for session, evidence, question, code in cases:
            runtime = CountingRuntime()
            ports = harness_business_ports(session=session, runtime=runtime)
            with self.subTest(code=code), self.assertRaises(BusinessActionError) as raised:
                ports.librarian.assembler.assemble(
                    {
                        "question": question,
                        "conversation_id": "rejected-followup",
                        "history": [],
                        "conversation_evidence": evidence,
                    }
                )
            self.assertEqual(raised.exception.cause_code, code)
            self.assertEqual(runtime.execute_calls, 0)

        private = conversation_entry(Session().documents[0])
        private["source_scope"] = "private"
        with self.assertRaises(BusinessActionError) as rejected:
            harness_business_ports(
                session=Session(), runtime=CountingRuntime()
            ).librarian.assembler.assemble(
                {
                    "question": "请解释 R1",
                    "conversation_id": "private-followup",
                    "history": [],
                    "conversation_evidence": [private],
                }
            )
        self.assertEqual(
            rejected.exception.cause_code,
            "librarian_conversation_evidence_private",
        )

    def test_workspace_followup_revalidates_the_active_published_source(self):
        workspace = Workspace()
        current = workspace.documents[0]
        ports = harness_business_ports(
            session=Session(), runtime=Runtime(), workspace=workspace
        )
        draft = ports.librarian.assembler.assemble(
            {
                "question": "请解释 R1",
                "conversation_id": "workspace-followup",
                "history": [],
                "conversation_evidence": [conversation_entry(current)],
            }
        )
        self.assertEqual(
            draft.outbound["documents"][0]["source_scope"], "workspace"
        )
        self.assertEqual(
            draft.outbound["prompt"]["seed_evidence"][0]["ref"], "R1"
        )

    def test_followup_cross_bundle_quantitative_comparison_is_zero_model(self):
        session = Session()
        session.documents = [
            document(
                "paper-a", paper_uid="paper-a", article_title="Paper A",
                conditions_text="W, 300 K", value_text="4.2 GPa",
            ),
            document(
                "paper-b", paper_uid="paper-b", article_title="Paper B",
                conditions_text="Ta, 900 K", value_text="8.1 GPa",
            ),
        ]
        evidence = [
            conversation_entry(session.documents[0], "R1", "B1"),
            conversation_entry(session.documents[1], "R2", "B2"),
        ]
        ports = harness_business_ports(session=session, runtime=Runtime())
        with self.assertRaises(BusinessActionError) as raised:
            ports.librarian.assembler.assemble(
                {
                    "question": "比较 R1 和 R2 的硬度数值差异",
                    "conversation_id": "cross-bundle-followup",
                    "history": [],
                    "conversation_evidence": evidence,
                }
            )
        self.assertEqual(raised.exception.cause_code, "unsupported_comparison")
        self.assertEqual(raised.exception.stage, "librarian_local_preflight")

    def test_conversation_evidence_rejects_extra_fields_paths_and_empty_bundle(self):
        session = Session()
        valid = conversation_entry(session.documents[0])
        attacks = [
            [{**valid, "source_excerpt": "untrusted body"}],
            [{**valid, "source_id": "/Users/name/library"}],
            [{**valid, "ref": "R0"}],
            [{**valid, "bundle_ref": "B10000"}],
            [valid] * 41,
        ]
        ports = harness_business_ports(session=session, runtime=Runtime())
        for evidence in attacks:
            with self.subTest(evidence=evidence), self.assertRaises(BusinessActionError) as raised:
                ports.librarian.assembler.assemble(
                    {
                        "question": "请解释 R1",
                        "conversation_id": "invalid-contract",
                        "history": [],
                        "conversation_evidence": evidence,
                    }
                )
            self.assertEqual(raised.exception.code, "business_action_invalid")
        with self.assertRaises(BusinessActionError) as empty:
            ports.librarian.assembler.assemble(
                {
                    "question": "请总结 B2",
                    "conversation_id": "empty-bundle",
                    "history": [],
                    "conversation_evidence": [valid],
                }
            )
        self.assertEqual(empty.exception.cause_code, "librarian_conversation_bundle_empty")

    def test_selected_workspace_evidence_uses_same_paper_neighbors(self):
        workspace = Workspace()
        ports = harness_business_ports(
            session=Session(), runtime=Runtime(selected=True), workspace=workspace
        )
        draft = ports.selected_evidence_chat.assembler.assemble(
            {
                "source_scope": "workspace",
                "source_id": "workspace",
                "entity_type": "item",
                "entity_uid": "31",
                "question": "这条数据代表什么？",
                "history": [],
            }
        )
        self.assertEqual(draft.outbound["current_entity"]["source_scope"], "workspace")
        self.assertEqual(draft.outbound["current_entity"]["entity_uid"], "31")
        self.assertEqual((draft.max_calls, draft.max_tokens), (1, 2_400))
        self.assertEqual(
            draft.outbound["prompt"]["execution_mode"],
            "single_turn_frozen_context",
        )

    def test_selected_evidence_source_change_has_recoverable_lineage(self):
        ports = harness_business_ports(session=Session(), runtime=Runtime(selected=True))
        with self.assertRaises(BusinessActionError) as raised:
            ports.selected_evidence_chat.assembler.assemble(
                {
                    "source_scope": "official",
                    "source_id": "official-old",
                    "entity_type": "item",
                    "entity_uid": "item-1",
                    "question": "这条数据代表什么？",
                    "history": [],
                }
            )
        self.assertEqual(
            raised.exception.cause_code, "selected_evidence_source_changed"
        )
        self.assertEqual(raised.exception.stage, "selected_evidence_preflight")
        self.assertEqual(raised.exception.next_action, "refresh_evidence_detail")

    def test_selected_evidence_missing_entity_has_recoverable_lineage(self):
        ports = harness_business_ports(session=Session(), runtime=Runtime(selected=True))
        with self.assertRaises(BusinessActionError) as raised:
            ports.selected_evidence_chat.assembler.assemble(
                {
                    "source_scope": "official",
                    "source_id": "official-v1",
                    "entity_type": "item",
                    "entity_uid": "missing-item",
                    "question": "这条数据代表什么？",
                    "history": [],
                }
            )
        self.assertEqual(raised.exception.cause_code, "selected_evidence_unavailable")
        self.assertEqual(raised.exception.stage, "selected_evidence_preflight")
        self.assertEqual(raised.exception.next_action, "return_to_search_results")

    def test_selected_workspace_unavailable_has_recoverable_lineage(self):
        ports = harness_business_ports(session=Session(), runtime=Runtime(selected=True))
        with self.assertRaises(BusinessActionError) as raised:
            ports.selected_evidence_chat.assembler.assemble(
                {
                    "source_scope": "workspace",
                    "source_id": "workspace",
                    "entity_type": "item",
                    "entity_uid": "31",
                    "question": "这条数据代表什么？",
                    "history": [],
                }
            )
        self.assertEqual(
            raised.exception.cause_code,
            "selected_evidence_workspace_unavailable",
        )
        self.assertEqual(raised.exception.stage, "selected_evidence_preflight")
        self.assertEqual(raised.exception.next_action, "repair_workspace")

    def test_selected_uses_stable_official_identity_and_existing_top_level_fields(self):
        session = Session()
        session.documents.extend(
            (
                document("table-compatible", "table"),
                document("figure-incompatible", "figure", bundle_uid="bundle-2"),
                document(
                    "finding-other-paper",
                    "finding",
                    paper_uid="paper-2",
                    finding_text="其他论文结论",
                ),
            )
        )
        runtime = Runtime(selected=True)
        ports = harness_business_ports(session=session, runtime=runtime)
        draft = ports.selected_evidence_chat.assembler.assemble(
            {
                "source_scope": "official",
                "source_id": "official-v1",
                "entity_type": "item",
                "entity_uid": "item-1",
                "question": "这条数据代表什么？",
                "history": [],
            }
        )
        self.assertEqual(
            {row["entity_uid"] for row in draft.outbound["allowed_neighbors"]},
            {"finding-1", "table-compatible"},
        )
        self.assertNotIn("figure-incompatible", str(draft.outbound))
        self.assertNotIn("finding-other-paper", str(draft.outbound))
        prepared = action(draft, "selected_evidence_chat")
        raw = RawClient()
        client = HarnessBudgetedBusinessAIClient(client=raw, action=prepared)
        result = ports.selected_evidence_chat.projector.project(
            ports.selected_evidence_chat.executor.execute(action=prepared, ai_client=client)
        )
        self.assertEqual(
            set(result),
            {"answer", "evidence_pages", "evidence_notes", "limitations", "context_pages", "entity", "model"},
        )
        self.assertEqual(result["entity"]["id"], "item-1")
        self.assertEqual(raw.calls, 1)

    def test_renderer_cannot_choose_private_provider_model_or_payload(self):
        ports = harness_business_ports(session=Session(), runtime=Runtime())
        attacks = [
            {"question": "Q", "conversation_id": "c", "provider_id": "openai"},
            {"question": "Q", "conversation_id": "c", "model": "anything"},
            {"question": "Q", "conversation_id": "c", "documents": []},
        ]
        for request in attacks:
            with self.subTest(request=request):
                with self.assertRaises(Exception):
                    ports.librarian.assembler.assemble(request)
        with self.assertRaises(Exception):
            ports.selected_evidence_chat.assembler.assemble(
                {
                    "source_scope": "private", "source_id": "mine",
                    "entity_type": "item", "entity_uid": "item-1",
                    "question": "Q", "history": [],
                }
            )

    def test_source_change_makes_snapshot_stale_and_runtime_failure_has_no_fallback(self):
        session = Session()
        ports = harness_business_ports(session=session, runtime=Runtime())
        draft = ports.librarian.assembler.assemble(
            {"question": "硬度", "conversation_id": "c", "history": []}
        )
        before = draft.content_units[0].snapshot_fingerprint
        session.fingerprint = "e" * 64
        self.assertNotEqual(
            before,
            ports.snapshots.fingerprint_for(
                kind=draft.content_units[0].kind,
                stable_source_identity=draft.content_units[0].stable_source_identity,
            ),
        )
        failing = harness_business_ports(session=Session(), runtime=Runtime(fail=True))
        failed_draft = failing.librarian.assembler.assemble(
            {"question": "硬度", "conversation_id": "c", "history": []}
        )
        prepared = action(failed_draft, "librarian")
        raw = RawClient()
        with self.assertRaises(BusinessActionError) as failed:
            failing.librarian.executor.execute(
                action=prepared,
                ai_client=HarnessBudgetedBusinessAIClient(client=raw, action=prepared),
            )
        self.assertEqual(failed.exception.cause_code, "harness_runtime_failed")
        self.assertEqual(failed.exception.stage, "harness_execute")
        self.assertEqual(failed.exception.next_action, "repair_harness_runtime")
        self.assertEqual(raw.calls, 0)

    def test_budget_exhaustion_is_not_reported_as_runtime_failure(self):
        class BudgetRuntime(Runtime):
            def execute(self, **_kwargs):
                raise HarnessError("harness_budget_exhausted")

        ports = harness_business_ports(session=Session(), runtime=BudgetRuntime())
        draft = ports.librarian.assembler.assemble(
            {"question": "硬度", "conversation_id": "c", "history": []}
        )
        prepared = action(draft, "librarian")
        raw = RawClient()
        with self.assertRaises(BusinessActionError) as failed:
            ports.librarian.executor.execute(
                action=prepared,
                ai_client=HarnessBudgetedBusinessAIClient(client=raw, action=prepared),
            )
        self.assertEqual(failed.exception.cause_code, "harness_budget_exhausted")
        self.assertEqual(failed.exception.stage, "harness_execute")
        self.assertEqual(failed.exception.next_action, "refine_librarian_question")
        self.assertEqual(raw.calls, 0)

    def test_unreviewed_runtime_is_rejected_before_prepare(self):
        class UnreviewedRuntime(Runtime):
            def dependency_metadata(self):
                return HarnessDependencySet(
                    HarnessDependencyMetadata.from_pin(HARNESS_SDK_PROTOCOL_PIN),
                    HarnessDependencyMetadata.from_pin(CORDIS_RUNTIME_PROTOCOL_PIN),
                    "3.0.0",
                )

        ports = harness_business_ports(session=Session(), runtime=UnreviewedRuntime())
        with self.assertRaises(BusinessActionError) as rejected:
            ports.librarian.assembler.assemble(
                {"question": "硬度", "conversation_id": "c", "history": []}
            )
        self.assertEqual(rejected.exception.cause_code, "harness_dependency_mismatch")
        self.assertEqual(rejected.exception.stage, "harness_preflight")
        self.assertEqual(rejected.exception.next_action, "repair_harness_runtime")


if __name__ == "__main__":
    unittest.main()
