from __future__ import annotations

from types import SimpleNamespace
import time
import unittest

from auto_research.ai.business_actions import BusinessActionError, HarnessBudgetedBusinessAIClient
from auto_research.ai.harness_contract import (
    CORDIS_RUNTIME_PROTOCOL_PIN,
    HARNESS_SDK_PROTOCOL_PIN,
    HarnessDependencyMetadata,
    HarnessDependencySet,
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


class RawClient:
    def __init__(self):
        self.calls = 0

    def request_tool_message(self, messages, tools, **kwargs):
        self.calls += 1
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
                "schema_version": "selected-evidence-harness-result-v1",
                "answer": "当前证据显示硬度为4.2 GPa。",
                "entity": job.current_entity.public_dict(),
                "related": [],
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


def action(draft, scope):
    now = int(time.time())
    task = "librarian_synthesis" if scope == "librarian" else "extraction"
    task_models = (
        (("librarian_planning", "deepseek-v4-flash"), ("librarian_synthesis", "deepseek-v4-pro"))
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
        self.assertEqual((draft.max_calls, draft.max_tokens), (8, 128_000))
        self.assertEqual(draft.content_units[0].kind, "harness_literature")
        prepared = action(draft, "librarian")
        raw = RawClient()
        client = HarnessBudgetedBusinessAIClient(client=raw, action=prepared)
        internal = ports.librarian.executor.execute(action=prepared, ai_client=client)
        result = ports.librarian.projector.project(internal)
        self.assertEqual(raw.calls, 1)
        self.assertEqual(result["librarian_core_version"], "librarian-v3")
        self.assertEqual(result["report"]["schema_version"], "research-report-v1")
        self.assertEqual({row["source_scope"] for row in result["results"]}, {"official"})
        self.assertTrue(result["recommended_articles"])
        self.assertNotIn("path", str(result).casefold())

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

    def test_selected_uses_stable_official_identity_and_existing_top_level_fields(self):
        session = Session()
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
