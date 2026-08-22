from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from auto_research.ai.deepseek import DeepSeekSettings
from auto_research.evidence.agent_runtime import (
    LibrarianAgentRuntime,
    _annotate_article_coverage,
    _bounded_json_list,
    _fallback_recall_queries,
)
from auto_research.evidence.db import EvidenceDB, now
from auto_research.evidence.librarian_reasoning import (
    build_evidence_bundles,
    build_article_recommendations,
    build_query_analysis,
    build_research_report,
    reason_candidates,
    report_references,
)
from auto_research.evidence.search_index import EvidenceSearchIndex, plan_query
from auto_research.evidence.public_dto import public_evidence_dto
from auto_research.evidence.visual_evidence import review_visual_asset
import json


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


class FakeOrphanReferenceClient(FakePlannedClient):
    def request_json(self, messages, **kwargs):
        self.json_calls += 1
        if self.json_calls == 1:
            return {"queries": ["高熵合金 中子辐照 硬度"], "focus": "检索硬度"}
        return {"answer": "发现硬度证据[R1]，另有不存在的证据[R999]。", "selected_refs": ["R1", "R999"]}


class FakeInventedNumberClient(FakePlannedClient):
    def request_json(self, messages, **kwargs):
        self.json_calls += 1
        if self.json_calls == 1:
            return {"queries": ["高熵合金 中子辐照 硬度"], "focus": "检索硬度"}
        return {"answer": "辐照后硬度达到99.9 GPa[R1]。", "selected_refs": ["R1"]}


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

    def test_temperature_unit_does_not_create_a_false_carbon_alias(self):
        index = EvidenceSearchIndex(self.db)
        index.rebuild()
        self.assertEqual(index.search("碳", entity_types={"item"}).total, 0)

    def test_model_context_keeps_all_available_evidence_types(self):
        values = [
            {"entity_type": entity_type, "ref": f"R{index}", "context": "x" * 2_000}
            for index, entity_type in enumerate(("item", "item", "finding", "table", "figure"), start=1)
        ]
        encoded = _bounded_json_list(values, 12_000)
        decoded = json.loads(encoded)
        self.assertEqual({row["entity_type"] for row in decoded}, {"item", "finding", "table", "figure"})

    def test_agent_recall_decomposes_multi_constraint_question(self):
        queries = _fallback_recall_queries("高熵合金在中子辐照后，硬度和缺陷结构有哪些变化？")
        self.assertIn("高熵合金 硬度", queries)
        self.assertIn("中子辐照 硬度", queries)
        self.assertTrue(any("位错环" in query for query in queries))

    def test_deterministic_query_analysis_separates_hard_constraints_and_soft_aliases(self):
        analysis = build_query_analysis(
            "CoCrFeMnNi高熵合金在300°C、1 dpa中子辐照后，硬度和缺陷结构有哪些变化？"
        )
        constraints = analysis.constraints
        self.assertIn("高熵合金", constraints["material"])
        self.assertIn("CoCrFeMnNi", constraints["material"])
        self.assertEqual(constraints["temperature"], ("300°C",))
        self.assertEqual(constraints["dose"], ("1 dpa",))
        self.assertIn("硬度", constraints["property"])
        self.assertIn("缺陷结构", constraints["property"])
        self.assertIn("high entropy alloy", analysis.soft_expansions["material"])
        self.assertNotIn("high entropy alloy", constraints["material"])

    def test_short_material_abbreviations_require_token_boundaries(self):
        heat = build_query_analysis("heat-treated steel after neutron irradiation hardness")
        wheat = build_query_analysis("wheat alloy hardness")
        hea = build_query_analysis("HEA after neutron irradiation hardness")
        self.assertNotIn("高熵合金", heat.constraints["material"])
        self.assertNotIn("高熵合金", wheat.constraints["material"])
        self.assertIn("钢", heat.constraints["material"])
        self.assertIn("高熵合金", hea.constraints["material"])

    def test_power_unit_w_is_not_a_tungsten_material_constraint(self):
        power = build_query_analysis("材料在300 W激光功率下的硬度")
        tungsten = build_query_analysis("W在离子辐照后的硬度")
        self.assertNotIn("钨及钨合金", power.constraints["material"])
        self.assertNotIn("W", power.constraints["material"])
        self.assertIn("W", tungsten.constraints["material"])

    def test_model_number_cannot_cross_inject_temperature_and_dose_fields(self):
        temperature = build_query_analysis(
            "高熵合金在300°C下的硬度",
            model_payload={"constraints": {"dose": ["300 dpa"]}},
        )
        dose = build_query_analysis(
            "高熵合金在1 dpa后的硬度",
            model_payload={"constraints": {"temperature": ["1°C"]}},
        )
        self.assertEqual(temperature.constraints["dose"], ())
        self.assertEqual(dose.constraints["temperature"], ())

    def test_model_particle_token_cannot_become_a_material_hard_condition(self):
        nickel_ion = build_query_analysis(
            "高熵合金经5e16 Ni ions/cm2辐照后的硬度",
            model_payload={"constraints": {"material": ["Ni"]}},
        )
        helium_ion = build_query_analysis(
            "纯镍经He离子辐照后的硬度",
            model_payload={"constraints": {"material": ["He"]}},
        )
        self.assertNotIn("Ni", nickel_ion.constraints["material"])
        self.assertNotIn("He", helium_ion.constraints["material"])

    def test_energy_unit_is_not_misread_as_kelvin(self):
        analysis = build_query_analysis("300 keV Ni离子辐照后的硬度")
        self.assertEqual(analysis.constraints["temperature"], ())
        self.assertIn("Ni离子", analysis.constraints["particle"])

    def test_common_scientific_fluence_notations_are_hard_conditions(self):
        for notation in (
            "1e15 ions/cm2", "2E16 ions cm^-2", "5×10¹⁶ cm⁻²",
            "5×10^16 He ions/cm2", "5e16 He+/cm2",
            "5e16 helium ions/cm2", "5e16 Ni ions/cm2",
        ):
            with self.subTest(notation=notation):
                analysis = build_query_analysis(f"高熵合金在{notation}离子辐照后的硬度")
                self.assertEqual(len(analysis.constraints["dose"]), 1)

    def test_fluence_particle_units_create_separate_hard_beam_constraints(self):
        neutron = build_query_analysis("高熵合金在1e15 n/cm2辐照后的硬度")
        helium = build_query_analysis("高熵合金在5e16 He+/cm2辐照后的硬度")
        nickel = build_query_analysis("高熵合金在5e16 Ni ions/cm2辐照后的硬度")
        self.assertIn("中子辐照", neutron.constraints["irradiation"])
        self.assertIn("中子", neutron.constraints["particle"])
        self.assertIn("氦离子辐照", helium.constraints["irradiation"])
        self.assertIn("氦离子", helium.constraints["particle"])
        self.assertIn("离子辐照", nickel.constraints["irradiation"])
        self.assertIn("Ni离子", nickel.constraints["particle"])
        rows = reason_candidates([{
            "ref": "R1", "entity_type": "item", "entity_id": 1,
            "paper_id": 1, "title": "硬度", "value": "4.2", "unit": "GPa",
            "context": "CoCrFeMnNi高熵合金；1e15 ions/cm2；离子辐照；辐照后",
        }], neutron)
        self.assertNotEqual(rows[0]["match_class"], "direct")

    def test_evidence_bundles_never_merge_different_e_notation_fluences(self):
        analysis = build_query_analysis("高熵合金离子辐照后的硬度")
        candidates = [
            {
                "ref": "R1", "entity_type": "item", "entity_id": 1, "paper_id": 1,
                "article_title": "Paper", "title": "硬度", "materials": ["CoCrFeMnNi"],
                "context": "CoCrFeMnNi高熵合金；离子辐照；1e15 ions/cm2；辐照后",
            },
            {
                "ref": "R2", "entity_type": "item", "entity_id": 2, "paper_id": 1,
                "article_title": "Paper", "title": "硬度", "materials": ["CoCrFeMnNi"],
                "context": "CoCrFeMnNi高熵合金；离子辐照；2e16 ions/cm2；辐照后",
            },
        ]
        bundles = build_evidence_bundles(candidates, analysis)
        self.assertEqual(len(bundles), 2)
        self.assertNotEqual(candidates[0]["bundle_id"], candidates[1]["bundle_id"])

    def test_equivalent_fluence_notations_remain_direct_evidence(self):
        equivalents = (
            ("1e15 ions/cm2", "1 × 10^15 ions cm^-2"),
            ("5×10¹⁶ cm⁻²", "5e16 cm^-2"),
            ("1e15 cm^-2", "1e19 m^-2"),
            ("高于1e15 ions/cm2", "2e15 ions/cm2"),
            ("不高于1e15 ions/cm2", "5e14 ions/cm2"),
        )
        for requested, reported in equivalents:
            with self.subTest(requested=requested, reported=reported):
                analysis = build_query_analysis(
                    f"高熵合金在{requested}离子辐照后的硬度"
                )
                rows = reason_candidates([{
                    "ref": "R1", "entity_type": "item", "entity_id": 1,
                    "paper_id": 1, "title": "硬度", "value": "4.2", "unit": "GPa",
                    "context": f"CoCrFeMnNi高熵合金；{reported}；离子辐照；辐照后",
                }], analysis)
                self.assertEqual(rows[0]["match_class"], "direct")

    def test_plain_fluence_integer_is_not_misread_as_a_power_of_ten(self):
        analysis = build_query_analysis("高熵合金在1015 ions/cm2离子辐照后的硬度")
        self.assertEqual(analysis.constraints["dose"], ("1015 ions/cm2",))
        rows = reason_candidates([{
            "ref": "R1", "entity_type": "item", "entity_id": 1,
            "paper_id": 1, "title": "硬度", "value": "4.2", "unit": "GPa",
            "context": "CoCrFeMnNi高熵合金；1e15 ions/cm2；离子辐照；辐照后",
        }], analysis)
        self.assertNotEqual(rows[0]["match_class"], "direct")

    def test_followup_inherits_omitted_hard_context_but_replaces_property(self):
        analysis = build_query_analysis(
            "那缺陷结构呢？",
            history=[{"role": "user", "content": "高熵合金在中子辐照后的硬度如何变化？"}],
        )
        self.assertEqual(analysis.constraints["material"], ("高熵合金",))
        self.assertEqual(analysis.constraints["irradiation"], ("中子辐照",))
        self.assertEqual(analysis.constraints["property"], ("缺陷结构",))
        self.assertFalse(analysis.needs_clarification)

    def test_explicit_current_condition_blocks_conflicting_history_condition(self):
        analysis = build_query_analysis(
            "CoCrFeMnNi在300°C离子辐照后的硬度如何变化？",
            model_payload={
                "constraints": {
                    "irradiation": ["中子辐照", "离子辐照"],
                    "temperature": ["300°C"],
                },
            },
            history=[{"role": "user", "content": "高熵合金在中子辐照后的硬度如何变化？"}],
        )
        self.assertEqual(analysis.constraints["irradiation"], ("离子辐照",))
        self.assertEqual(analysis.constraints["particle"], ())
        self.assertEqual(analysis.constraints["temperature"], ("300°C",))

    def test_critical_ambiguity_requests_clarification_before_search(self):
        analysis = build_query_analysis("比较一下它们")
        self.assertTrue(analysis.needs_clarification)
        self.assertTrue(analysis.clarification_question)
        self.assertGreaterEqual(len(analysis.clarification_options), 2)

    def test_reasoning_allows_only_one_missing_hard_condition_as_adjacent(self):
        analysis = build_query_analysis("高熵合金在300°C、1 dpa中子辐照后硬度如何变化？")
        base = {
            "entity_type": "item", "entity_id": 1, "paper_id": 1, "article_title": "Paper",
            "doi": "10.1/reason", "title": "辐照后硬度", "value": "4.2", "unit": "GPa",
            "evidence": "hardness after neutron irradiation was 4.2 GPa", "source_page": 4,
        }
        direct = {**base, "ref": "R1", "context": "CoCrFeMnNi高熵合金；300°C；1 dpa；中子辐照；辐照后"}
        adjacent = {**base, "ref": "R2", "entity_id": 2, "context": "CoCrFeMnNi高熵合金；1 dpa；中子辐照；辐照后"}
        expansion = {**base, "ref": "R3", "entity_id": 3, "context": "CoCrFeMnNi高熵合金；离子辐照；辐照后"}
        rows = reason_candidates([direct, adjacent, expansion], analysis)
        by_ref = {row["ref"]: row for row in rows}
        self.assertEqual(by_ref["R1"]["match_class"], "direct")
        self.assertEqual(by_ref["R2"]["match_class"], "adjacent")
        self.assertEqual(by_ref["R2"]["missing_constraints"][0]["label"], "温度")
        self.assertEqual(by_ref["R3"]["match_class"], "expansion")

    def test_evidence_bundles_do_not_merge_different_materials_or_conditions(self):
        analysis = build_query_analysis("高熵合金辐照后硬度")
        rows = reason_candidates([
            {
                "ref": "R1", "entity_type": "item", "entity_id": 1, "paper_id": 1,
                "article_title": "Paper", "title": "辐照前硬度", "value": "3.1", "unit": "GPa",
                "context": "CoCrFeMnNi高熵合金；300°C；中子辐照；辐照后", "evidence": "3.1 GPa",
            },
            {
                "ref": "R2", "entity_type": "item", "entity_id": 2, "paper_id": 1,
                "article_title": "Paper", "title": "辐照后硬度", "value": "4.6", "unit": "GPa",
                "context": "Al0.3CoCrFeNi高熵合金；500°C；中子辐照；辐照后", "evidence": "4.6 GPa",
            },
        ], analysis)
        bundles = build_evidence_bundles(rows, analysis)
        self.assertEqual(len(bundles), 2)
        self.assertNotEqual(rows[0]["bundle_id"], rows[1]["bundle_id"])

    def test_fractional_ceramic_formulas_and_gold_fluences_form_distinct_bundles(self):
        analysis = build_query_analysis("辐照后硬度")
        candidates = []
        for index, (material, fluence) in enumerate((
            ("(Hf1/3Ta1/3Zr1/3)B2", "1×10^14 Au cm^-2"),
            ("(Hf1/3Ta1/3Ti1/3)B2", "1×10^14 Au cm^-2"),
            ("(Hf1/3Ta1/3Zr1/3)B2", "5×10^14 Au cm^-2"),
        ), 1):
            candidates.append({
                "ref": f"R{index}", "entity_type": "item", "entity_id": index,
                "paper_id": 12, "title": "硬度",
                "context": f"{material}；注量{fluence}；辐照后",
            })
        bundles = build_evidence_bundles(candidates, analysis)
        self.assertEqual(len(bundles), 3)
        self.assertEqual({bundle["material"] for bundle in bundles}, {
            "(Hf1/3Ta1/3Zr1/3)B2", "(Hf1/3Ta1/3Ti1/3)B2",
        })
        self.assertTrue(any("Au" in bundle["conditions"] for bundle in bundles))

    def test_unknown_material_and_condition_records_are_not_merged(self):
        analysis = build_query_analysis("硬度")
        candidates = [
            {"ref": "R1", "entity_type": "item", "entity_id": 1, "paper_id": 1, "title": "硬度"},
            {"ref": "R2", "entity_type": "item", "entity_id": 2, "paper_id": 1, "title": "硬度"},
        ]
        self.assertEqual(len(build_evidence_bundles(candidates, analysis)), 2)

    def test_structured_report_has_five_sections_and_traceable_matrix(self):
        analysis = build_query_analysis("高熵合金在300°C中子辐照后硬度如何变化？")
        rows = reason_candidates([
            {
                "ref": "R1", "entity_type": "item", "entity_id": 1, "paper_id": 1,
                "article_title": "Paper", "doi": "10.1/report", "source_page": 4,
                "title": "辐照后硬度", "value": "4.2", "unit": "GPa",
                "context": "CoCrFeMnNi高熵合金；300°C；中子辐照；辐照后", "evidence": "4.2 GPa",
            },
            {
                "ref": "R2", "entity_type": "item", "entity_id": 2, "paper_id": 1,
                "article_title": "Paper", "doi": "10.1/report", "source_page": 5,
                "title": "辐照后硬度", "value": "4.0", "unit": "GPa",
                "context": "CoCrFeMnNi高熵合金；中子辐照；辐照后", "evidence": "4.0 GPa",
            },
        ], analysis)
        build_evidence_bundles(rows, analysis)
        report = build_research_report(analysis, rows, direct_refs=["R1"], related_refs=["R2"])
        self.assertEqual(report["direct_conclusion"]["status"], "found")
        self.assertEqual(report["evidence_matrix"][0]["refs"], ["R1"])
        self.assertEqual(report["related_evidence"][0]["relaxed_constraints"], ["温度"])
        self.assertTrue(report["database_gaps"])
        self.assertGreaterEqual(len(report["suggested_followups"]), 2)
        self.assertLessEqual(len(report["suggested_followups"]), 3)
        self.assertEqual(report_references(report), {"R1", "R2"})

    def test_article_recommendations_aggregate_existing_four_type_candidates(self):
        candidates = [
            {
                "ref": "R1", "entity_type": "item", "entity_id": 1,
                "paper_id": 11, "article_title": "Direct paper", "doi": "10.1/direct",
                "first_author": "A", "year": 2024, "title": "硬度",
                "match_class": "direct", "constraint_coverage": 1.0,
                "matched_constraints": [{"label": "材料", "matched": ["高熵合金"]}],
                "missing_constraints": [], "search_score": 9.0,
            },
            {
                "ref": "R2", "entity_type": "figure", "entity_id": 2,
                "paper_id": 11, "article_title": "Direct paper", "doi": "10.1/direct",
                "first_author": "A", "year": 2024, "title": "缺陷图",
                "match_class": "direct", "constraint_coverage": 1.0,
                "matched_constraints": [{"label": "材料", "matched": ["高熵合金"]}],
                "missing_constraints": [], "search_score": 8.0,
            },
            {
                "ref": "R3", "entity_type": "finding", "entity_id": 3,
                "paper_id": 12, "article_title": "Adjacent paper", "doi": "10.1/adjacent",
                "title": "硬化趋势", "match_class": "adjacent",
                "constraint_coverage": 0.75, "matched_constraints": [],
                "missing_constraints": [{"label": "温度"}], "search_score": 7.0,
            },
            {
                "ref": "R4", "entity_type": "table", "entity_id": 4,
                "paper_id": 13, "article_title": "Expansion paper", "doi": "10.1/expansion",
                "title": "力学性质表", "match_class": "expansion",
                "constraint_coverage": 0.25, "matched_constraints": [],
                "missing_constraints": [{"label": "材料"}, {"label": "温度"}],
                "search_score": 6.0,
            },
        ]
        recommendations = build_article_recommendations(candidates)
        self.assertEqual([row["paper_id"] for row in recommendations], [11, 12])
        self.assertEqual(recommendations[0]["supporting_refs"], ["R1", "R2"])
        self.assertEqual(recommendations[0]["entity_types"], ["item", "figure"])
        self.assertEqual(recommendations[0]["recommendation_level"], "direct")
        self.assertEqual(recommendations[1]["relaxed_constraints"], ["温度"])
        self.assertNotIn("R4", json.dumps(recommendations, ensure_ascii=False))

        expansion_only = build_article_recommendations([candidates[-1]])
        self.assertEqual(expansion_only[0]["recommendation_level"], "expansion")
        self.assertIn("拓展阅读", expansion_only[0]["why_recommended"])

    def test_article_recommendation_warns_when_only_visual_index_exists(self):
        recommendations = [{"paper_id": 21, "article_title": "Visual-only paper"}]
        annotated = _annotate_article_coverage(
            recommendations,
            {21: {"item": 0, "finding": 0, "table": 3, "figure": 11}},
        )
        self.assertEqual(annotated[0]["textual_evidence_status"], "visual_only")
        self.assertIn("全文证据抽取尚未完成", annotated[0]["coverage_warning"])
        self.assertEqual(
            annotated[0]["database_evidence_counts"],
            {"item": 0, "finding": 0, "table": 3, "figure": 11},
        )

        available = _annotate_article_coverage(
            [{"paper_id": 22}],
            {22: {"item": 1, "finding": 0, "table": 2, "figure": 4}},
        )
        self.assertEqual(available[0]["textual_evidence_status"], "available")
        self.assertEqual(available[0]["coverage_warning"], "")

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
        self.assertEqual(result["planning_model"], "deepseek-v4-flash")
        self.assertEqual(result["model"], "deepseek-v4-pro")
        self.assertTrue(result["answered_at"])
        self.assertTrue(result["evidence_version"])
        self.assertEqual(result["recommended_article_count"], 1)
        self.assertEqual(result["recommended_articles"][0]["paper_id"], self.paper_id)
        self.assertEqual(result["recommended_articles"][0]["supporting_refs"], ["R1"])
        self.assertEqual(result["response_format"], "reasoning-presentation-v2")

    def test_search_v2_public_projection_preserves_four_type_identity(self):
        index = EvidenceSearchIndex(self.db)
        index.rebuild()
        row = index.search("硬度", entity_types={"item"}, limit=1).rows[0]
        public = public_evidence_dto(row)
        self.assertEqual(public["entity_type"], "item")
        self.assertEqual(public["entity_id"], row["item_id"])

    def test_librarian_never_exposes_protocol_when_both_model_stages_fail(self):
        client = FakeBrokenSummaryClient()
        result = LibrarianAgentRuntime(self.db, client=client).run(
            "高熵合金中子辐照后的硬度如何变化？"
        )
        self.assertEqual(result["results"][0]["meaning"], "辐照后硬度")
        self.assertNotIn("DSML", result["answer"])
        self.assertIn("[R1]", result["answer"])
        self.assertEqual(result["summary_mode"], "deterministic_fallback")

    def test_related_evidence_rejects_model_numbers_absent_from_its_reference(self):
        candidates = [{
            "ref": "R1", "entity_type": "item", "entity_id": 1,
            "paper_id": 1, "title": "硬度", "value": "4.2", "unit": "GPa",
            "evidence": "hardness was 4.2 GPa", "match_class": "adjacent",
        }]
        notes = LibrarianAgentRuntime._related_notes(
            {"related_notes": [{"ref": "R1", "summary": "硬度为99.9 GPa"}]},
            candidates,
        )
        self.assertEqual(notes, {})

    def test_scientific_number_validation_uses_complete_normalized_tokens(self):
        plain = [{
            "ref": "R1", "entity_type": "item", "entity_id": 1, "paper_id": 20,
            "source_page": 20, "title": "注量", "value": "1", "unit": "cm^-2",
            "evidence": "fluence was 1 cm^-2", "match_class": "direct",
        }]
        self.assertTrue(LibrarianAgentRuntime._has_unsupported_numbers(
            "注量为1e20 cm^-2[R1]",
            {"R1"},
            plain,
        ))
        scientific = [{
            "ref": "R1", "entity_type": "item", "entity_id": 1, "paper_id": 1,
            "source_page": 4, "title": "注量", "value": "1 × 10^20", "unit": "cm⁻²",
            "evidence": "fluence was 1×10^20 cm⁻²", "match_class": "direct",
        }]
        for notation in ("1e20", "1E+20", "1 × 10^20", "1×10²⁰"):
            with self.subTest(notation=notation):
                self.assertFalse(LibrarianAgentRuntime._has_unsupported_numbers(
                    f"注量为{notation} cm^-2[R1]",
                    {"R1"},
                    scientific,
                ))

    def test_scientific_number_validation_binds_explicit_units(self):
        hardness = [{
            "ref": "R1", "entity_type": "item", "entity_id": 1,
            "title": "硬度", "value": "4.2", "unit": "GPa",
            "evidence": "hardness was 4.2 GPa", "match_class": "direct",
        }]
        self.assertFalse(LibrarianAgentRuntime._has_unsupported_numbers(
            "硬度为4.2 GPa[R1]", {"R1"}, hardness,
        ))
        self.assertTrue(LibrarianAgentRuntime._has_unsupported_numbers(
            "硬度为4.2 eV[R1]", {"R1"}, hardness,
        ))
        self.assertFalse(LibrarianAgentRuntime._has_unsupported_numbers(
            "报告值为4.2[R1]", {"R1"}, hardness,
        ))

        temperature = [{
            "ref": "R1", "entity_type": "item", "entity_id": 2,
            "title": "温度", "value": "573", "unit": "K",
            "evidence": "temperature was 573 K", "match_class": "direct",
        }]
        self.assertTrue(LibrarianAgentRuntime._has_unsupported_numbers(
            "能量为573 keV[R1]", {"R1"}, temperature,
        ))

        for claim in (
            "硬度为4.2 eV.[R1]",
            "**硬度为4.2 eV**[R1]",
            "**4.2 eV**[R1]",
            "*4.2 eV*[R1]",
            "_4.2 eV_[R1]",
            "__4.2 eV__[R1]",
            "$4.2 eV$[R1]",
            r"$4.2\,\mathrm{eV}$[R1]",
            r"\(4.2\,\mathrm{eV}\)[R1]",
            "4.2&nbsp;eV[R1]",
            "4.2\u200beV[R1]",
            "4.2\u200ceV[R1]",
            "4.2\u2060eV[R1]",
            "4.2 ｅＶ[R1]",
            "4.2 ℯV[R1]",
            "𝟒.𝟐 eV[R1]",
            "4.2 GPa and **4.2 eV**[R1]",
            "硬度为4.2 `eV`[R1]",
            "硬度为4.2 J[R1]",
            "硬度为4.2 Sv[R1]",
            "硬度为4.2 mSv[R1]",
            "硬度为4.2 Bq[R1]",
            "硬度为4.2 mol[R1]",
            "硬度为4.2 ions/cm^-2[R1]",
        ):
            with self.subTest(claim=claim):
                self.assertTrue(
                    LibrarianAgentRuntime._has_unsupported_numbers(
                        claim,
                        {"R1"},
                        hardness,
                    )
                )

        fluence = [{
            "ref": "R1", "entity_type": "item", "entity_id": 3,
            "title": "注量", "value": "5 × 10^16", "unit": "cm⁻²",
            "evidence": "fluence was 5×10¹⁶ cm⁻²", "match_class": "direct",
        }]
        self.assertFalse(LibrarianAgentRuntime._has_unsupported_numbers(
            "注量为5e16 cm^-2[R1]", {"R1"}, fluence,
        ))
        self.assertTrue(LibrarianAgentRuntime._has_unsupported_numbers(
            "注量为5e16 m^-2[R1]", {"R1"}, fluence,
        ))

        missing = [{
            "ref": "R1", "entity_type": "item", "entity_id": 4,
            "title": "温度", "value": "300", "unit": "",
            "evidence": "reported value 300", "match_class": "direct",
        }]
        self.assertTrue(LibrarianAgentRuntime._has_unsupported_numbers(
            "温度为300 °C[R1]", {"R1"}, missing,
        ))

        for multiplication in ("·", "⋅", "∙"):
            with self.subTest(multiplication=multiplication):
                self.assertTrue(LibrarianAgentRuntime._has_unsupported_numbers(
                    f"硬度为1{multiplication}10^20 GPa[R1]",
                    {"R1"},
                    [{
                        "ref": "R1", "entity_type": "item", "entity_id": 5,
                        "title": "硬度", "value": "1", "unit": "GPa",
                        "evidence": "1 GPa at 10 K", "match_class": "direct",
                    }],
                ))
                self.assertFalse(LibrarianAgentRuntime._has_unsupported_numbers(
                    f"硬度为1{multiplication}10^20 GPa[R1]",
                    {"R1"},
                    [{
                        "ref": "R1", "entity_type": "item", "entity_id": 6,
                        "title": "硬度", "value": "1e20", "unit": "GPa",
                        "evidence": "1e20 GPa", "match_class": "direct",
                    }],
                ))

    def test_scientific_number_validation_ignores_json_metadata_numbers(self):
        candidates = [{
            "ref": "R1", "entity_type": "figure", "entity_id": 20, "paper_id": 20,
            "source_page": 20, "search_score": 20, "doi": "10.20/example",
            "article_title": "Study 20", "title": "TEM image",
            "caption": "No quantitative result is reported.", "match_class": "direct",
        }]
        self.assertTrue(LibrarianAgentRuntime._has_unsupported_numbers(
            "结果为20 GPa[R1]",
            {"R1"},
            candidates,
        ))

    def test_single_numeric_delta_cannot_compare_different_evidence_bundles(self):
        candidates = [
            {"ref": "R1", "bundle_id": "B1"},
            {"ref": "R2", "bundle_id": "B2"},
        ]
        self.assertTrue(LibrarianAgentRuntime._unsafe_cross_bundle_comparison(
            "相比提高1.1 GPa[R1][R2]",
            {"R1", "R2"},
            candidates,
        ))

    def test_any_quantitative_multi_bundle_statement_is_rejected(self):
        candidates = [
            {"ref": "R1", "bundle_id": "B1"},
            {"ref": "R2", "bundle_id": "B2"},
        ]
        for statement in (
            "提升1.1 GPa[R1][R2]",
            "由4.2 GPa变为5.3 GPa[R1][R2]",
            "4.2 GPa vs 5.3 GPa[R1][R2]",
            "后者更大，为5.3 GPa[R1][R2]",
            "测得4.2 GPa和5.3 GPa[R1][R2]",
            "后者更大[R1][R2]",
            "辐照后硬度提升[R1][R2]",
            "R1 对应样品更硬[R1][R2]",
            "R1 的硬度超过 R2[R1][R2]",
            "R1 的硬度较高[R1][R2]",
            "两个实验分别观察到不同缺陷[R1][R2]",
        ):
            with self.subTest(statement=statement):
                self.assertTrue(LibrarianAgentRuntime._unsafe_cross_bundle_comparison(
                    statement,
                    {"R1", "R2"},
                    candidates,
                ))
        same_bundle = [
            {"ref": "R1", "bundle_id": "B1"},
            {"ref": "R2", "bundle_id": "B1"},
        ]
        self.assertFalse(LibrarianAgentRuntime._unsafe_cross_bundle_comparison(
            "由4.2 GPa变为5.3 GPa[R1][R2]",
            {"R1", "R2"},
            same_bundle,
        ))
        self.assertTrue(LibrarianAgentRuntime._unsafe_cross_bundle_comparison(
            "两项证据的实验条件不同[R1][R2]",
            {"R1", "R2"},
            candidates,
        ))
        self.assertFalse(LibrarianAgentRuntime._unsafe_cross_bundle_comparison(
            "检索到2条证据[R1][R2]",
            {"R1", "R2"},
            candidates,
        ))
        unknown_bundle = [dict(candidate) for candidate in candidates]
        unknown_bundle[1]["bundle_id"] = ""
        self.assertTrue(LibrarianAgentRuntime._unsafe_cross_bundle_comparison(
            "后者更大[R1][R2]",
            {"R1", "R2"},
            unknown_bundle,
        ))
        self.assertFalse(LibrarianAgentRuntime._has_unsupported_numbers(
            "检索到2条证据[R1][R2]",
            {"R1", "R2"},
            candidates,
        ))

    def test_report_payload_preserves_direct_adjacent_and_expansion_boundaries(self):
        analysis = build_query_analysis("高熵合金在300°C中子辐照后的硬度")
        candidates = [
            {
                "ref": "R1", "entity_type": "item", "entity_id": 1, "paper_id": 1,
                "title": "硬度", "value": "4.2", "unit": "GPa",
                "context": "高熵合金；300°C；中子辐照",
                "evidence": "hardness was 4.2 GPa", "match_class": "direct",
                "bundle_id": "B1", "missing_constraints": [],
            },
            {
                "ref": "R2", "entity_type": "item", "entity_id": 2, "paper_id": 1,
                "title": "硬度", "value": "4.0", "unit": "GPa",
                "context": "高熵合金；中子辐照",
                "evidence": "hardness was 4.0 GPa", "match_class": "adjacent",
                "bundle_id": "B2", "missing_constraints": [{"label": "温度"}],
            },
            {
                "ref": "R3", "entity_type": "item", "entity_id": 3, "paper_id": 2,
                "title": "硬度", "value": "3.8", "unit": "GPa",
                "context": "另一材料",
                "evidence": "hardness was 3.8 GPa", "match_class": "expansion",
                "bundle_id": "B3", "missing_constraints": [
                    {"label": "材料"}, {"label": "温度"},
                ],
            },
        ]
        payload = {
            "direct_conclusion": "直接硬度为4.2 GPa[R1]",
            "direct_refs": ["R1", "R2", "R3"],
            "related_refs": ["R1", "R2", "R3"],
            "related_notes": [
                {"ref": "R1", "summary": "错误放入相关区的4.2 GPa"},
                {"ref": "R2", "summary": "仅缺温度，报告4.0 GPa"},
                {"ref": "R3", "summary": "缺多个条件，报告3.8 GPa"},
            ],
        }
        report = LibrarianAgentRuntime(self.db, client=FakePlannedClient())._report_from_payload(
            payload,
            analysis,
            candidates,
        )
        self.assertEqual(report["direct_conclusion"]["refs"], ["R1"])
        self.assertEqual([row["refs"] for row in report["evidence_matrix"]], [["R1"]])
        self.assertEqual([row["refs"] for row in report["related_evidence"]], [["R2"]])
        self.assertEqual(report["related_evidence"][0]["summary"], "仅缺温度，报告4.0 GPa")

    def test_related_note_rejects_quantitative_cross_bundle_synthesis(self):
        candidates = [
            {
                "ref": "R1", "title": "硬度", "value": "4.2", "unit": "GPa",
                "evidence": "4.2 GPa", "match_class": "adjacent", "bundle_id": "B1",
            },
            {
                "ref": "R2", "title": "硬度", "value": "5.3", "unit": "GPa",
                "evidence": "5.3 GPa", "match_class": "adjacent", "bundle_id": "B2",
            },
        ]
        notes = LibrarianAgentRuntime._related_notes(
            {
                "related_notes": [{
                    "refs": ["R1", "R2"],
                    "summary": "由4.2 GPa提升至5.3 GPa",
                }],
            },
            candidates,
        )
        self.assertEqual(notes, {})

    def test_direct_conclusion_rejects_implicit_cross_bundle_comparison(self):
        analysis = build_query_analysis("高熵合金中子辐照后的硬度")
        candidates = [
            {
                "ref": "R1", "entity_type": "item", "entity_id": 1, "paper_id": 1,
                "title": "硬度", "value": "4.2", "unit": "GPa",
                "evidence": "hardness was 4.2 GPa", "match_class": "direct",
                "bundle_id": "B1", "missing_constraints": [],
            },
            {
                "ref": "R2", "entity_type": "item", "entity_id": 2, "paper_id": 2,
                "title": "硬度", "value": "6.0", "unit": "GPa",
                "evidence": "hardness was 6.0 GPa", "match_class": "direct",
                "bundle_id": "B9", "missing_constraints": [],
            },
        ]
        report = LibrarianAgentRuntime(self.db, client=FakePlannedClient())._report_from_payload(
            {
                "direct_conclusion": "后者更大[R1][R2]",
                "direct_refs": ["R1", "R2"],
            },
            analysis,
            candidates,
        )
        self.assertNotIn("后者更大", report["direct_conclusion"]["text"])
        self.assertEqual(report["direct_conclusion"]["refs"], ["R1", "R2"])
        self.assertEqual(
            [row["refs"] for row in report["evidence_matrix"]],
            [["R1"], ["R2"]],
        )

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
        self.assertEqual(sum(1 for row in result["results"] if row["agent_cited"]), 2)
        self.assertEqual(result["cited_count"], 2)
        self.assertEqual({row["agent_match_class"] for row in result["results"]}, {"direct", "adjacent"})
        self.assertEqual(len(result["report"]["related_evidence"]), 1)

    def test_orphan_model_reference_is_removed_from_structured_answer(self):
        result = LibrarianAgentRuntime(self.db, client=FakeOrphanReferenceClient()).run(
            "高熵合金中子辐照后的硬度如何变化？"
        )
        self.assertNotIn("R999", result["answer"])
        self.assertEqual(result["report"]["direct_conclusion"]["refs"], ["R1"])

    def test_model_number_absent_from_cited_evidence_is_removed(self):
        result = LibrarianAgentRuntime(self.db, client=FakeInventedNumberClient()).run(
            "高熵合金中子辐照后的硬度如何变化？"
        )
        self.assertNotIn("99.9", result["answer"])
        self.assertIn("4.2", result["results"][0]["value_text"])

    def test_agent_public_payload_does_not_expose_local_identifiers_or_paths(self):
        result = LibrarianAgentRuntime(self.db, client=FakePlannedClient()).run(
            "高熵合金中子辐照后的硬度如何变化？"
        )
        forbidden = {"image_path", "pdf_path", "zotero_key", "local_article_key", "editor", "edit_note"}
        self.assertFalse(forbidden.intersection(result["results"][0]))
        self.assertFalse(forbidden.intersection(result["recommended_articles"][0]))

    def test_shared_public_projection_removes_local_and_review_metadata(self):
        public = public_evidence_dto({
            "id": 7,
            "display_name": "图表",
            "image_url": "/api/visual-assets/7/image",
            "pdf_url": "/api/papers/1/pdf#page=2",
            "image_path": "/Users/example/private.png",
            "zotero_key": "LOCALKEY",
            "reviewer": "private-user",
            "review_note": "internal note",
        })
        self.assertEqual(public["id"], 7)
        self.assertIn("image_url", public)
        self.assertIn("pdf_url", public)
        self.assertFalse({"image_path", "zotero_key", "reviewer", "review_note"}.intersection(public))

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

    def test_visual_review_changes_index_fingerprint_and_refreshes_title(self):
        stamp = now()
        with self.db.connect() as conn:
            asset_id = conn.execute(
                """INSERT INTO visual_assets(
                paper_id,asset_type,label,display_name,asset_number,caption,page_start,page_end,
                bbox_json,image_path,image_sha256,physical_quantities_json,variables_json,
                materials_json,conditions_text,methods_text,context_explanation,tags_json,
                source_context,review_status,extraction_method,metadata_source,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    self.paper_id, "table", "Table 1", "旧表格标题", 1, "hardness table", 2, 2,
                    "[0,0,1,1]", "/tmp/not-public.png", "hash", '["硬度"]', '{}',
                    '["CoCrFeMnNi"]', "300°C", "纳米压痕", "硬度结果", '["硬度","高熵合金"]',
                    "Table 1 hardness", "draft", "pdf_layout", "deterministic", stamp, stamp,
                ),
            ).lastrowid
        index = EvidenceSearchIndex(self.db)
        index.rebuild()
        self.assertEqual(
            index.search(
                "旧表格标题", entity_types={"table"}, quality_filter="published"
            ).total,
            1,
        )
        before = index.source_fingerprint()
        review_visual_asset(
            self.db, asset_id, {"display_name": "高熵合金辐照硬度表"}, "correction",
            reviewer="test", note="修正标题",
        )
        self.assertNotEqual(before, index.source_fingerprint())
        status = index.ensure_fresh()
        self.assertTrue(status["rebuilt"])
        rows = index.search("高熵合金辐照硬度表", entity_types={"table"}).rows
        self.assertEqual(rows[0]["display_name"], "高熵合金辐照硬度表")
        published = index.search(
            "高熵合金辐照硬度表",
            entity_types={"table"},
            quality_filter="published",
        )
        self.assertEqual(published.total, 1)

    def test_new_visual_manual_review_is_not_published(self):
        stamp = now()
        with self.db.connect() as conn:
            asset_id = conn.execute(
                """INSERT INTO visual_assets(
                paper_id,asset_type,label,display_name,asset_number,caption,page_start,page_end,
                bbox_json,image_path,image_sha256,physical_quantities_json,variables_json,
                materials_json,conditions_text,methods_text,context_explanation,tags_json,
                source_context,review_status,extraction_method,metadata_source,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    self.paper_id, "figure", "Figure 99", "待审核新图片", 99,
                    "candidate", 3, 3, "[0,0,1,1]", "/tmp/not-public-99.png", "hash99",
                    "[]", "{}", "[]", "", "", "", "[]", "candidate context",
                    "draft", "pdf_layout", "deterministic", stamp, stamp,
                ),
            ).lastrowid
            run_id = conn.execute(
                """INSERT INTO quality_pipeline_runs(
                paper_id,status,stage,progress,quality_threshold,summary_json,created_at
                ) VALUES(?,?,?,?,?,?,?)""",
                (self.paper_id, "completed", "manual_review", 100, 85, "{}", stamp),
            ).lastrowid
            conn.execute(
                """INSERT INTO quality_candidates(
                pipeline_run_id,paper_id,entity_type,candidate_key,chosen_source,candidate_json,
                gate_status,gate_reason,published_asset_id,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, self.paper_id, "figure", "new-figure-99", "merged",
                    '{"is_new_asset":true}', "manual_review", "等待人工审核", asset_id,
                    stamp, stamp,
                ),
            )
        index = EvidenceSearchIndex(self.db)
        index.rebuild()
        self.assertEqual(
            index.search(
                "待审核新图片", entity_types={"figure"}, quality_filter="published"
            ).total,
            0,
        )
        self.assertEqual(
            index.search(
                "待审核新图片", entity_types={"figure"}, quality_filter="all"
            ).total,
            1,
        )


if __name__ == "__main__":
    unittest.main()
