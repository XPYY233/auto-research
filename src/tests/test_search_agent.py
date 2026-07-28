from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from auto_research.ai.deepseek import DeepSeekSettings
from auto_research.evidence.agent_runtime import LibrarianAgentRuntime
from auto_research.evidence.db import EvidenceDB, now
from auto_research.evidence.search_index import EvidenceSearchIndex, plan_query


class FakeToolClient:
    def __init__(self):
        self.settings = DeepSeekSettings(api_key="test", analysis_model="deepseek-test")
        self.calls = 0

    def request_tool_message(self, messages, tools, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "search_evidence",
                        "arguments": json.dumps({
                            "query": "高熵合金 中子辐照 硬度",
                            "entity_types": ["item", "table", "figure", "finding"],
                            "limit": 8,
                        }, ensure_ascii=False),
                    },
                }],
            }
        return {"role": "assistant", "content": "发现一条可追溯硬度数据[R1]。", "tool_calls": []}


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

    def test_librarian_reuses_four_type_search_and_returns_source_payload(self):
        client = FakeToolClient()
        result = LibrarianAgentRuntime(self.db, client=client).run(
            "高熵合金中子辐照后的硬度如何变化？"
        )
        self.assertEqual(result["agent"]["id"], "librarian")
        self.assertEqual(result["tool_calls"], 1)
        self.assertEqual(result["results"][0]["agent_entity_type"], "item")
        self.assertEqual(result["results"][0]["meaning"], "辐照后硬度")
        self.assertIn("[R1]", result["answer"])

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
