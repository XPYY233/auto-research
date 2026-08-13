from __future__ import annotations

import json
import hashlib
import tempfile
import threading
import unittest
from pathlib import Path

from auto_research.ai.business_actions import BusinessActionError, _public_result
from auto_research.ai.deepseek import DeepSeekSettings
from auto_research.ai.prepared_actions import PreparedOutbound
from auto_research.evidence.agent_runtime import LibrarianAgentRuntime
from auto_research.evidence.db import EvidenceDB, now
from auto_research.evidence.librarian_ai_business_action import (
    LibrarianBusinessExecutor,
    librarian_business_ports,
)
from auto_research.evidence.librarian_ai_job import LibrarianAIJobError, LibrarianAIJobStore
from auto_research.evidence.librarian_state import ResearchStateCodec


class _RuntimeClient:
    def __init__(self) -> None:
        self.settings = DeepSeekSettings(api_key="test")

    def request_json(self, *args, **kwargs):
        raise AssertionError("legacy client must not be used by prepared execution")

    def request_tool_message(self, *args, **kwargs):
        raise AssertionError("prepared path has no tool fallback")


class _PreparedClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request_json(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _action(draft, *, session="s" * 64):
    return PreparedOutbound(
        action_id="action", session_digest=session, scope="librarian",
        provider_id="deepseek", runtime_revision=1, credential_generation=1,
        runtime_activation="legacy_compatible",
        runtime_task_models=(("analysis", "deepseek-v4-pro"),
                             ("extraction", "deepseek-v4-pro"),
                             ("librarian_planning", "deepseek-v4-flash"),
                             ("librarian_synthesis", "deepseek-v4-pro")),
        task="librarian_synthesis",
        task_models=(("librarian_planning", "deepseek-v4-flash"),
                     ("librarian_synthesis", "deepseek-v4-pro")),
        models=("deepseek-v4-flash", "deepseek-v4-pro"),
        executor_id="librarian_business_executor", executor_version="v1",
        estimated_calls=1, max_calls=1, max_tokens=draft.max_tokens,
        outbound={"payload": draft.outbound, "call_plan": []},
        outbound_digest="a" * 64, manifest_digest="b" * 64,
        units=draft.content_units, byte_count=100, issued_at=1, expires_at=9999999999,
    )


class LibrarianBusinessActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = EvidenceDB(Path(self.tmp.name) / "test.sqlite")
        paper_id = self.db.upsert_paper(
            title="Tungsten irradiation defects", doi="10.1/prepared-librarian"
        )
        stamp = now()
        with self.db.connect() as conn:
            item_id = conn.execute(
                "INSERT INTO data_items(paper_id,stable_key,origin_type,created_at) VALUES(?,?,?,?)",
                (paper_id, "vacancy", "automatic", stamp),
            ).lastrowid
            conn.execute(
                """INSERT INTO data_versions(
                item_id,version_no,value_text,meaning,unit,article_title,doi,context_explanation,
                source_page,source_locator,source_excerpt,editor,edit_note,review_action,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (item_id, 0, "3.2", "空位形成能", "eV", "Tungsten irradiation defects",
                 "10.1/prepared-librarian", "钨 DFT 辐照缺陷", 4, "Results",
                 "vacancy formation energy is 3.2 eV", "test", "", "automatic", stamp),
            )
        self.runtime = LibrarianAgentRuntime(
            self.db,
            client=_RuntimeClient(),
            state_codec=ResearchStateCodec(secret=b"librarian-business-test-secret-32"),
        )
        self.ports = librarian_business_ports(self.runtime)

    def tearDown(self):
        self.tmp.cleanup()

    def test_local_system_question_has_no_prepared_or_model_action(self):
        request = {"question": "你用什么AI模型？", "conversation_id": "c1"}
        result = self.ports.assembler.local_result(request)
        self.assertEqual(result["search_operations"], 0)
        self.assertEqual(result["tool_calls"], 0)
        with self.assertRaises(BusinessActionError):
            self.ports.assembler.assemble(request)

    def test_local_clarification_stays_out_of_paid_prepared_job(self):
        request = {"question": "比较一下它们哪个更好？", "conversation_id": "c1"}
        result = self.ports.assembler.local_result(request)
        self.assertIsNotNone(result)
        self.assertEqual(result["summary_mode"], "clarification")
        with self.assertRaises(BusinessActionError):
            self.ports.assembler.assemble(request)

    def test_stage_job_token_is_the_only_additional_public_token(self):
        self.assertEqual(_public_result({"job_token": "opaque"})["job_token"], "opaque")
        for key in ("consent_nonce", "credential_token", "raw_token"):
            with self.subTest(key=key), self.assertRaises(BusinessActionError):
                _public_result({key: "unsafe"})

    def test_planner_then_synthesis_are_two_exact_single_calls(self):
        request = {
            "question": "钨的辐照缺陷形成能有哪些证据？",
            "conversation_id": "c1",
            "history": [],
        }
        planner_draft = self.ports.assembler.assemble(request)
        self.assertEqual(len(planner_draft.call_plan), 1)
        self.assertEqual(planner_draft.call_plan[0].task, "librarian_planning")
        planner = _PreparedClient([{
            "queries": ["钨 辐照缺陷 形成能", "W vacancy formation energy"],
            "focus": "钨辐照缺陷形成能",
            "clarification": {"needed": False, "question": "", "options": []},
        }])
        stage = self.ports.executor.execute(
            action=_action(planner_draft), ai_client=planner
        )
        public_stage = self.ports.projector.project(stage)
        self.assertEqual(len(planner.calls), 1)
        self.assertEqual(public_stage["stage"], "synthesis_ready")
        self.assertTrue(public_stage["requires_second_consent"])
        serialized = json.dumps(public_stage, ensure_ascii=False)
        self.assertNotIn("候选证据", serialized)
        self.assertNotIn("planner_handle", serialized)

        synthesis_draft = self.ports.assembler.assemble(
            {"job_token": public_stage["job_token"]}
        )
        self.assertEqual(len(synthesis_draft.call_plan), 1)
        self.assertEqual(synthesis_draft.call_plan[0].task, "librarian_synthesis")
        synthesis = _PreparedClient([{
            "direct_conclusion": "钨空位形成能为3.2 eV [R1]。",
            "direct_refs": ["R1"], "related_refs": [], "related_notes": [],
            "suggested_followups": ["还有哪些钨缺陷形成能？"],
        }])
        result = self.ports.executor.execute(
            action=_action(synthesis_draft), ai_client=synthesis
        )
        result = self.ports.projector.project(result)
        self.assertEqual(len(synthesis.calls), 1)
        self.assertEqual(result["librarian_core_version"], "librarian-v3")
        self.assertEqual(result["retrieval_policy"], "focused")
        self.assertTrue(all(row["source_scope"] == "official" for row in result["results"]))
        with self.assertRaises(BusinessActionError):
            self.ports.executor.execute(action=_action(synthesis_draft), ai_client=synthesis)

    def test_invalid_planner_has_zero_recall(self):
        draft = self.ports.assembler.assemble(
            {"question": "钨的辐照缺陷？", "conversation_id": "c1"}
        )
        self.runtime._run_recall = lambda queries: (_ for _ in ()).throw(
            AssertionError("invalid planner must not recall")
        )
        with self.assertRaises(BusinessActionError):
            self.ports.executor.execute(
                action=_action(draft), ai_client=_PreparedClient([{"queries": "bad"}])
            )

    def test_invalid_synthesis_uses_deterministic_report_without_third_call(self):
        planner = self.ports.assembler.assemble(
            {"question": "钨的辐照缺陷形成能？", "conversation_id": "c1"}
        )
        stage = self.ports.executor.execute(
            action=_action(planner),
            ai_client=_PreparedClient([{
                "queries": ["钨 辐照缺陷 形成能"], "focus": "形成能",
                "clarification": {"needed": False, "question": "", "options": []},
            }]),
        )
        synthesis = self.ports.assembler.assemble({"job_token": stage["job_token"]})
        client = _PreparedClient([ValueError("invalid json")])
        result = self.ports.executor.execute(action=_action(synthesis), ai_client=client)
        result = self.ports.projector.project(result)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(result["summary_mode"], "deterministic_fallback")

    def test_stale_corpus_and_cross_session_fail_before_synthesis_call(self):
        planner = self.ports.assembler.assemble(
            {"question": "钨辐照形成能？", "conversation_id": "c1"}
        )
        stage = self.ports.executor.execute(
            action=_action(planner, session="a" * 64),
            ai_client=_PreparedClient([{
                "queries": ["钨 辐照 形成能"], "focus": "形成能",
                "clarification": {"needed": False, "question": "", "options": []},
            }]),
        )
        synthesis = self.ports.assembler.assemble({"job_token": stage["job_token"]})
        client = _PreparedClient([{}])
        with self.assertRaises(BusinessActionError):
            self.ports.executor.execute(
                action=_action(synthesis, session="b" * 64), ai_client=client
            )
        self.assertEqual(client.calls, [])
        valid = _PreparedClient([{
            "direct_conclusion": "", "direct_refs": [], "related_refs": [],
            "related_notes": [], "suggested_followups": [],
        }])
        result = self.ports.executor.execute(
            action=_action(synthesis, session="a" * 64), ai_client=valid
        )
        self.assertEqual(len(valid.calls), 1)
        self.assertEqual(
            self.ports.projector.project(result)["librarian_core_version"],
            "librarian-v3",
        )

    def test_job_store_ttl_and_capacity_fail_closed(self):
        clock = [100]
        store = LibrarianAIJobStore(clock=lambda: clock[0], ttl_seconds=30, capacity=1)
        values = dict(
            conversation_id="c1", session_digest="pending", question="q", history=(),
            evidence_version="a" * 64, decision=object(), verified_state=None,
            state_token="", state_fingerprint="none", request_fingerprint="b" * 64,
            effective_history=(), anchors_pre_resolved=False,
        )
        context = store.add_planner(**values)
        with self.assertRaises(LibrarianAIJobError):
            store.add_planner(**values)
        clock[0] = 131
        with self.assertRaises(LibrarianAIJobError):
            store.take_planner(context.handle)
        store.add_planner(**values)

    def test_projector_rejects_extra_public_fields(self):
        with self.assertRaises(BusinessActionError):
            self.ports.projector.project(
                {"librarian_core_version": "librarian-v3", "unexpected": "value"}
            )

    def test_projector_failure_releases_job_and_does_not_consume_research_state(self):
        evidence = self.runtime.index.source_fingerprint()
        state, token = self.runtime.state_codec.build(
            evidence_version=evidence, conversation_id="c1", active_topic="钨形成能",
            constraints={"material": {"values": ["W"]}}, source_id=self.runtime.source_id,
            candidates=[{"ref": "R1", "entity_type": "item", "entity_id": 1, "bundle_id": "B1"}],
            bundles=[{"id": "B1", "doi": "10.1/prepared-librarian", "material": "W",
                      "conditions": "", "refs": ["R1"]}], selected_refs={"R1"},
        )
        request = {
            "question": "详细解释R1", "conversation_id": "c1",
            "research_state": state, "state_token": token,
        }
        planner = self.ports.assembler.assemble(request)
        stage = self.ports.executor.execute(
            action=_action(planner),
            ai_client=_PreparedClient([{
                "queries": ["钨 形成能"], "focus": "钨形成能",
                "clarification": {"needed": False, "question": "", "options": []},
            }]),
        )
        synthesis = self.ports.assembler.assemble({"job_token": stage["job_token"]})
        envelope = self.ports.executor.execute(
            action=_action(synthesis),
            ai_client=_PreparedClient([{
                "direct_conclusion": "钨空位形成能为3.2 eV [R1]。", "direct_refs": ["R1"],
                "related_refs": [], "related_notes": [], "suggested_followups": [],
            }]),
        )
        broken = dict(envelope)
        broken["public_result"] = {**envelope["public_result"], "unexpected": True}
        with self.assertRaises(BusinessActionError):
            self.ports.projector.project(broken)
        # The failed projector must not consume the scientific state or occupy
        # the synthesis job; a fresh second-stage action can still be prepared.
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        self.assertNotIn(token_hash, self.runtime.state_codec._consumed)
        with self.assertRaises(BusinessActionError):
            self.ports.assembler.assemble({"job_token": stage["job_token"]})
        recovered = self.ports.projector.recover(
            job_token=stage["job_token"], session_digest="s" * 64
        )
        self.assertEqual(recovered["librarian_core_version"], "librarian-v3")

    def test_same_state_parallel_planners_reserve_before_second_model_call(self):
        evidence = self.runtime.index.source_fingerprint()
        state, token = self.runtime.state_codec.build(
            evidence_version=evidence, conversation_id="c1", active_topic="钨形成能",
            constraints={"material": {"values": ["W"]}}, source_id=self.runtime.source_id,
            candidates=[{"ref": "R1", "entity_type": "item", "entity_id": 1, "bundle_id": "B1"}],
            bundles=[{"id": "B1", "doi": "10.1/prepared-librarian", "material": "W",
                      "conditions": "", "refs": ["R1"]}], selected_refs={"R1"},
        )
        request = {
            "question": "详细解释R1", "conversation_id": "c1",
            "research_state": state, "state_token": token,
        }
        first = self.ports.assembler.assemble(request)
        second = self.ports.assembler.assemble(request)
        entered = threading.Event()
        release = threading.Event()

        class BlockingClient(_PreparedClient):
            def request_json(inner, messages, **kwargs):
                inner.calls.append((messages, kwargs))
                entered.set()
                release.wait(2)
                return {
                    "queries": ["钨 形成能"], "focus": "钨形成能",
                    "clarification": {"needed": False, "question": "", "options": []},
                }

        first_client = BlockingClient([])
        outcome = []
        thread = threading.Thread(
            target=lambda: outcome.append(
                self.ports.executor.execute(action=_action(first), ai_client=first_client)
            )
        )
        thread.start()
        self.assertTrue(entered.wait(1))
        second_client = _PreparedClient([{}])
        with self.assertRaises(BusinessActionError):
            self.ports.executor.execute(action=_action(second), ai_client=second_client)
        self.assertEqual(second_client.calls, [])
        release.set()
        thread.join(2)
        self.assertEqual(len(first_client.calls), 1)
        self.assertEqual(len(outcome), 1)

    def test_zero_candidates_returns_local_v3_result_without_synthesis_job(self):
        draft = self.ports.assembler.assemble(
            {"question": "不存在材料XYZQ的量子泡泡数据？", "conversation_id": "c1"}
        )
        self.runtime._run_recall = lambda queries: 4
        self.runtime._reasoned_candidates = lambda analysis: ([], [])
        stage = self.ports.executor.execute(
            action=_action(draft),
            ai_client=_PreparedClient([{
                "queries": ["XYZQ 量子泡泡"], "focus": "无结果",
                "clarification": {"needed": False, "question": "", "options": []},
            }]),
        )
        result = self.ports.projector.project(stage)
        self.assertEqual(result["summary_mode"], "no_results")
        self.assertEqual(result["candidate_count"], 0)
        self.assertNotIn("job_token", result)

    def test_zero_candidates_consumes_verified_state_only_after_projection(self):
        evidence = self.runtime.index.source_fingerprint()
        state, token = self.runtime.state_codec.build(
            evidence_version=evidence, conversation_id="c1", active_topic="钨形成能",
            constraints={"material": {"values": ["W"]}}, source_id=self.runtime.source_id,
            candidates=[{"ref": "R1", "entity_type": "item", "entity_id": 1, "bundle_id": "B1"}],
            bundles=[{"id": "B1", "doi": "10.1/prepared-librarian", "material": "W",
                      "conditions": "", "refs": ["R1"]}], selected_refs={"R1"},
        )
        request = {
            "question": "继续解释B1中的不存在性质", "conversation_id": "c1",
            "research_state": state, "state_token": token,
        }
        draft = self.ports.assembler.assemble(request)
        self.runtime._reasoned_candidates = lambda analysis: ([], [])
        envelope = self.ports.executor.execute(
            action=_action(draft),
            ai_client=_PreparedClient([{
                "queries": ["钨 不存在性质"], "focus": "无结果",
                "clarification": {"needed": False, "question": "", "options": []},
            }]),
        )
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        self.assertEqual(self.runtime.state_codec._consumed, {})
        result = self.ports.projector.project(envelope)
        self.assertEqual(result["summary_mode"], "no_results")
        self.assertIn(token_hash, self.runtime.state_codec._consumed)
        second = self.ports.assembler.assemble(request)
        second_client = _PreparedClient([{}])
        with self.assertRaises(BusinessActionError):
            self.ports.executor.execute(action=_action(second), ai_client=second_client)
        self.assertEqual(second_client.calls, [])

    def test_planner_provider_failure_releases_state_reservation(self):
        evidence = self.runtime.index.source_fingerprint()
        state, token = self.runtime.state_codec.build(
            evidence_version=evidence, conversation_id="c1", active_topic="钨形成能",
            constraints={"material": {"values": ["W"]}}, source_id=self.runtime.source_id,
            candidates=[{"ref": "R1", "entity_type": "item", "entity_id": 1, "bundle_id": "B1"}],
            bundles=[{"id": "B1", "doi": "10.1/prepared-librarian", "material": "W",
                      "conditions": "", "refs": ["R1"]}], selected_refs={"R1"},
        )
        request = {
            "question": "详细解释R1", "conversation_id": "c1",
            "research_state": state, "state_token": token,
        }
        failed = self.ports.assembler.assemble(request)
        with self.assertRaises(BusinessActionError):
            self.ports.executor.execute(
                action=_action(failed), ai_client=_PreparedClient([RuntimeError("offline")])
            )
        retry = self.ports.assembler.assemble(request)
        retry_client = _PreparedClient([{
            "queries": ["钨 形成能"], "focus": "钨形成能",
            "clarification": {"needed": False, "question": "", "options": []},
        }])
        self.ports.executor.execute(action=_action(retry), ai_client=retry_client)
        self.assertEqual(len(retry_client.calls), 1)

    def test_claimed_job_survives_ttl_until_execution_lease_ends(self):
        clock = [100]
        store = LibrarianAIJobStore(clock=lambda: clock[0], ttl_seconds=30, capacity=2)
        job = store.add_synthesis(
            conversation_id="c1", session_digest="a" * 64, question="q",
            evidence_version="a" * 64, decision=object(), verified_state=None,
            state_token="", state_fingerprint="none", request_fingerprint="b" * 64,
            queries=(), analysis={"focus": "q"}, plan_mode="prepared", search_operations=0,
            collected=(), reasoned=(), bundles=(), review_map=(), synthesis_candidates=(),
            synthesis_messages=({"role": "user", "content": "bounded"},),
        )
        store.claim_for_execute(job.handle, session_digest="a" * 64)
        clock[0] = 131
        self.assertEqual(store.resolve_active(job.handle).handle, job.handle)
        clock[0] = 221
        with self.assertRaises(LibrarianAIJobError):
            store.resolve_active(job.handle)

    def test_completed_state_tombstone_lives_until_research_state_expiry(self):
        clock = [100.0]
        codec = ResearchStateCodec(
            secret=b"completed-state-expiry-test-32b",
            clock=lambda: clock[0],
            ttl_seconds=600,
        )
        runtime = LibrarianAgentRuntime(self.db, client=_RuntimeClient(), state_codec=codec)
        jobs = LibrarianAIJobStore(clock=lambda: clock[0], ttl_seconds=30, capacity=4)
        ports = librarian_business_ports(runtime, jobs=jobs)
        evidence = runtime.index.source_fingerprint()
        state, token = codec.build(
            evidence_version=evidence, conversation_id="c1", active_topic="钨形成能",
            constraints={"material": {"values": ["W"]}}, source_id=runtime.source_id,
            candidates=[{"ref": "R1", "entity_type": "item", "entity_id": 1, "bundle_id": "B1"}],
            bundles=[{"id": "B1", "doi": "10.1/prepared-librarian", "material": "W",
                      "conditions": "", "refs": ["R1"]}], selected_refs={"R1"},
        )
        request = {
            "question": "继续解释B1中的不存在性质", "conversation_id": "c1",
            "research_state": state, "state_token": token,
        }
        draft = ports.assembler.assemble(request)
        runtime._reasoned_candidates = lambda analysis: ([], [])
        envelope = ports.executor.execute(
            action=_action(draft),
            ai_client=_PreparedClient([{
                "queries": ["钨 不存在性质"], "focus": "无结果",
                "clarification": {"needed": False, "question": "", "options": []},
            }]),
        )
        ports.projector.project(envelope)

        clock[0] = 141.0  # prepared/job TTL ended, research state remains valid
        retry = ports.assembler.assemble(request)
        retry_client = _PreparedClient([{}])
        with self.assertRaises(BusinessActionError):
            ports.executor.execute(action=_action(retry), ai_client=retry_client)
        self.assertEqual(retry_client.calls, [])

        clock[0] = 701.0
        with self.assertRaises(BusinessActionError):
            ports.assembler.assemble(request)


if __name__ == "__main__":
    unittest.main()
