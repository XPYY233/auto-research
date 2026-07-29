from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from auto_research.ai.deepseek import DeepSeekSettings
from auto_research.evidence.agent_runtime import LibrarianAgentRuntime, _fallback_recall_queries
from auto_research.evidence.db import EvidenceDB, now
from auto_research.evidence.search_index import EvidenceSearchIndex, plan_query


class FakePlannedClient:
    def __init__(self):
        self.settings = DeepSeekSettings(api_key="test", analysis_model="deepseek-test")
        self.json_calls = 0

    def request_json(self, messages, **kwargs):
        self.json_calls += 1
        if self.json_calls == 1:
            return {
                "queries": ["高熵合金 中子辐照 硬度", "高熵合金 硬度"],
                "focus": "查找高熵合金中子辐照后的硬度",
            }
        return {"answer": "发现一条可追溯硬度数据[R1]。", "selected_refs": ["R1"]}

    def request_tool_message(self, messages, tools, **kwargs):
        raise AssertionError("JSON summary should succeed")

class FakeBrokenSummaryClient(FakePlannedClient):
    def request_json(self, messages, **kwargs):
        raise ValueError("invalid JSON")

    def request_tool_message(self, messages, tools, **kwargs):
        return {
            "role": "assistant",
            "content": '<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="search_evidence"></｜｜DSML｜｜invoke>',
            "tool_calls": [],
        }


class SearchAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = EvidenceDB(Path(self.tmp.name) / "evidence.sqlite")
        self.paper_id = self.db.upsert_paper(
            title="Neutron irradiated high entropy alloy", doi="10.1/search-agent",
            first_author="Test Author",
        )
        stamp = now()
        with self.db.connect() as conn:
            item = conn.execute(
                "INSERT INTO data_items(paper_id,stable_key,origin_type,created_at) VALUES(?,?,?,?)",
                (self.paper_id, "hardness", "automatic", stamp),
            ).lastrowid
            conn.execute(
                """INSERT INTO data_versions(
                item_id,version_no,value_text,meaning,unit,article_title,doi,context_explanation,
                source_page,source_locator,source_excerpt,editor,edit_note,review_action,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item, 0, "4.2", "辐照后硬度", "GPa",
                    "Neutron irradiated high entropy alloy", "10.1/search-agent",
                    "CoCrFeMnNi高熵合金；中子辐照；300°C", 4, "Table 2",
                    "hardness increased to 4.2 GPa", "test", "", "automatic", stamp,
                ),
            )

    def tearDown(self):
        self.tmp.cleanup()

    def test_natural_language_query_uses_same_terms_as_keywords(self):
        sentence = plan_query("高熵合金在中子辐照后硬度如何变化")
        keywords = plan_query("高熵合金 中子辐照 硬度")
        self.assertEqual(sentence, keywords)

    def test_single_chinese_element_and_short_property_are_searchable(self):
        self.assertIn("钨", plan_query("钨"))
        index = EvidenceSearchIndex(self.db)
        index.rebuild()
        rows = index.search("硬度", entity_types={"item"}).rows
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["meaning"], "辐照后硬度")

    def test_agent_recall_decomposes_multi_constraint_question(self):
        queries = _fallback_recall_queries("高熵合金在中子辐照后，硬度和缺陷结构有哪些变化？")
        self.assertIn("高熵合金 硬度", queries)
        self.assertIn("中子辐照 硬度", queries)
        self.assertTrue(any("位错环" in query for query in queries))

    def test_librarian_reuses_four_type_search_and_returns_source_payload(self):
        client = FakePlannedClient()
        result = LibrarianAgentRuntime(self.db, client=client).run(
            "高熵合金中子辐照后的硬度如何变化？"
        )
        self.assertEqual(result["agent"]["id"], "librarian")
        self.assertEqual(result["tool_calls"], len(result["recall_queries"]))
        self.assertGreater(result["search_operations"], result["tool_calls"])
        self.assertEqual(result["results"][0]["agent_entity_type"], "item")
        self.assertEqual(result["results"][0]["meaning"], "辐照后硬度")
        self.assertTrue(result["results"][0]["agent_cited"])
        self.assertIn("[R1]", result["answer"])
        self.assertEqual(result["scope"], {"paper_ids": [], "mode": "all"})
        self.assertEqual(result["summary_mode"], "deepseek_json")

    def test_librarian_never_exposes_protocol_when_both_model_stages_fail(self):
        client = FakeBrokenSummaryClient()
        result = LibrarianAgentRuntime(self.db, client=client).run(
            "高熵合金中子辐照后的硬度如何变化？"
        )
        self.assertEqual(result["results"][0]["meaning"], "辐照后硬度")
        self.assertNotIn("DSML", result["answer"])
        self.assertIn("[R1]", result["answer"])
        self.assertEqual(result["summary_mode"], "deterministic_fallback")

    def test_librarian_keeps_uncited_recall_candidates_visible(self):
        stamp = now()
        with self.db.connect() as conn:
            item = conn.execute(
                "INSERT INTO data_items(paper_id,stable_key,origin_type,created_at) VALUES(?,?,?,?)",
                (self.paper_id, "hardening", "automatic", stamp),
            ).lastrowid
            conn.execute(
                """INSERT INTO data_versions(
                item_id,version_no,value_text,meaning,unit,article_title,doi,context_explanation,
                source_page,source_locator,source_excerpt,editor,edit_note,review_action,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item, 0, "1.1", "辐照硬化增量", "GPa",
                    "Neutron irradiated high entropy alloy", "10.1/search-agent",
                    "CoCrFeMnNi高熵合金；中子辐照；300°C", 5, "Results",
                    "irradiation hardening was 1.1 GPa", "test", "", "automatic", stamp,
                ),
            )
        result = LibrarianAgentRuntime(self.db, client=FakePlannedClient()).run(
            "高熵合金中子辐照后的硬度如何变化？"
        )
        self.assertGreaterEqual(len(result["results"]), 2)
        self.assertEqual(sum(1 for row in result["results"] if row["agent_cited"]), 1)
        self.assertEqual(result["cited_count"], 1)

    def test_identical_question_reuses_stable_cached_response(self):
        client = FakePlannedClient()
        runtime = LibrarianAgentRuntime(self.db, client=client)
        first = runtime.run("高熵合金中子辐照后的硬度如何变化？")
        calls_after_first = client.json_calls
        second = runtime.run("高熵合金中子辐照后的硬度如何变化？")

        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(client.json_calls, calls_after_first)
        self.assertEqual(second["answer"], first["answer"])
        self.assertEqual(second["results"], first["results"])

    def test_librarian_forces_fresh_search_instead_of_reusing_history_refs(self):
        result = LibrarianAgentRuntime(self.db, client=FakePlannedClient()).run(
            "高熵合金中子辐照后的硬度如何变化？",
            history=[{"role": "assistant", "content": "旧答案[R99]"}],
        )
        self.assertEqual(len(result["results"]), 1)
        self.assertIn("[R1]", result["answer"])
        self.assertNotIn("R99", result["answer"])

    def test_changed_paper_refreshes_incrementally(self):
        index = EvidenceSearchIndex(self.db)
        index.rebuild()
        stamp = now()
        with self.db.connect() as conn:
            item = conn.execute(
                "INSERT INTO data_items(paper_id,stable_key,origin_type,created_at) VALUES(?,?,?,?)",
                (self.paper_id, "dose", "automatic", stamp),
            ).lastrowid
            conn.execute(
                """INSERT INTO data_versions(
                item_id,version_no,value_text,meaning,unit,article_title,doi,context_explanation,
                source_page,source_locator,source_excerpt,editor,edit_note,review_action,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item, 0, "1", "辐照剂量", "dpa",
                    "Neutron irradiated high entropy alloy", "10.1/search-agent",
                    "CoCrFeMnNi高熵合金；中子辐照", 3, "Methods",
                    "irradiated to 1 dpa", "test", "", "automatic", stamp,
                ),
            )
        status = index.ensure_fresh()
        self.assertTrue(status["incremental"])
        self.assertEqual(status["papers"], [self.paper_id])
        self.assertEqual(index.search("辐照剂量", entity_types={"item"}).total, 1)


if __name__ == "__main__":
    unittest.main()
