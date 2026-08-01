from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from auto_research.evidence.db import EvidenceDB
from auto_research.evidence.research_brief import (
    ResearchBriefError,
    build_research_brief,
    export_research_brief,
    render_research_brief_markdown,
    research_brief_snapshot_from_result,
    sign_research_brief_snapshot,
    verify_research_brief_snapshot,
)
from auto_research.evidence.webapp import EvidenceHandler


GENERATED_AT = "2026-07-30T18:00:00+08:00"


def librarian_snapshot() -> dict:
    return {
        "question": "高熵合金在中子辐照后，硬度和缺陷结构有哪些变化？",
        "report": {
            "schema_version": "research-report-v1",
            "direct_conclusion": {
                "status": "found",
                "text": "硬度与缺陷结构均有公开证据[R1][R2]。",
                "refs": ["R1", "R2"],
            },
            "evidence_matrix": [
                {
                    "refs": ["R1"],
                    "bundle_id": "B1",
                    "material": "CoCrFeMnNi",
                    "conditions": "中子辐照；300°C",
                    "property": "硬度",
                    "result": "4.2 GPa",
                    "article_title": "Irradiated alloy",
                    "doi": "10.1000/example",
                    "source_page": 4,
                    "entity_type": "item",
                }
            ],
            "related_evidence": [
                {
                    "refs": ["R3", "R4"],
                    "summary": "表格与图片给出相关实验背景。",
                    "relaxed_constraints": ["温度"],
                    "article_title": "Irradiated alloy",
                    "bundle_id": "B2",
                }
            ],
            "database_gaps": ["缺少同一条件下的完整前后对照。"],
            "suggested_followups": ["只比较同一证据包内的硬度结果。"],
        },
        "query_analysis": {
            "constraints": {
                "property": {"label": "物理量", "values": ["硬度", "缺陷结构"], "mode": "hard"},
                "material": {"label": "材料", "values": ["高熵合金"], "mode": "hard"},
                "irradiation": {"label": "辐照类型", "values": ["中子辐照"], "mode": "hard"},
            }
        },
        "results": [
            {
                "agent_ref": "R4",
                "agent_cited": True,
                "agent_entity_type": "figure",
                "agent_match_class": "adjacent",
                "agent_bundle_id": "B2",
                "display_name": "缺陷结构图",
                "label": "Figure 2",
                "article_title": "Irradiated alloy",
                "doi": "https://doi.org/10.1000/example",
                "page_start": 7,
                "caption": "Figure 2. Irradiation defects.",
                "source_context": "Dislocation loops were observed after irradiation.",
                "materials": ["CoCrFeMnNi"],
                "conditions_text": "neutron irradiation",
                "physical_quantities": ["loop density"],
                "image_url": "/api/visual-assets/4/image",
                "pdf_url": "/api/papers/1/pdf#page=7",
                "image_path": "/Users/private/figure.png",
            },
            {
                "agent_ref": "R2",
                "agent_cited": True,
                "agent_entity_type": "finding",
                "agent_match_class": "direct",
                "agent_bundle_id": "B1",
                "meaning": "缺陷结构变化",
                "finding_text": "辐照后观察到位错环。",
                "article_title": "Irradiated alloy",
                "doi": "10.1000/example",
                "source_page": 6,
                "source_locator": "Results",
                "source_excerpt": "Dislocation loops were observed after irradiation.",
            },
            {
                "agent_ref": "R1",
                "agent_cited": True,
                "agent_entity_type": "item",
                "agent_match_class": "direct",
                "agent_bundle_id": "B1",
                "meaning": "辐照后硬度",
                "value_text": "4.2",
                "unit": "GPa",
                "context_explanation": "CoCrFeMnNi；中子辐照；300°C",
                "article_title": "Irradiated alloy",
                "doi": "10.1000/example",
                "source_page": 4,
                "source_locator": "Table 2",
                "source_excerpt": "Hardness increased to 4.2 GPa.",
            },
            {
                "agent_ref": "R3",
                "agent_cited": True,
                "agent_entity_type": "table",
                "agent_match_class": "adjacent",
                "agent_bundle_id": "B2",
                "display_name": "硬度结果表",
                "label": "Table 2",
                "article_title": "Irradiated alloy",
                "doi": "10.1000/example",
                "page_start": 4,
                "caption": "Table 2. Hardness results.",
                "source_context": "Hardness values measured by nanoindentation.",
                "methods_text": "nanoindentation",
            },
            {
                "agent_ref": "R88",
                "agent_cited": False,
                "agent_entity_type": "item",
                "meaning": "未被报告引用的候选",
                "source_excerpt": "This must not be exported.",
            },
        ],
        "model": "deepseek-v4-pro",
        "plan_mode": "deepseek_json",
        "summary_mode": "deepseek_json",
        "response_format": "reasoning-presentation-v1",
        "cache_hit": False,
        "answered_at": "2026-07-30T09:59:00+00:00",
        "evidence_version": "sha256:test-evidence-version",
        "clarification_required": False,
        "candidate_count": 58,
        "cited_count": 4,
        "match_counts": {"direct": 20, "adjacent": 18, "expansion": 20},
        "bundle_count": 9,
    }


class ResearchBriefTests(unittest.TestCase):
    def test_exports_only_report_citations_and_preserves_four_types(self):
        brief = build_research_brief(librarian_snapshot(), generated_at=GENERATED_AT)

        self.assertEqual(brief["schema_version"], "librarian-research-brief-v1")
        self.assertEqual([row["ref"] for row in brief["cited_evidence"]], ["R1", "R2", "R3", "R4"])
        self.assertEqual(
            {key: len(value) for key, value in brief["evidence_by_type"].items()},
            {"item": 1, "finding": 1, "table": 1, "figure": 1},
        )
        self.assertEqual(brief["evidence_by_type"]["figure"][0]["doi"], "10.1000/example")
        self.assertNotIn("R88", json.dumps(brief, ensure_ascii=False))
        self.assertEqual(brief["statistics"]["candidate_count"], 58)
        self.assertEqual(brief["statistics"]["exported_cited_count"], 4)
        self.assertEqual(brief["integrity"]["status"], "pass")
        self.assertEqual(brief["integrity"]["duplicate_report_references"], [])

    def test_rejects_orphans_duplicate_results_and_invalid_types(self):
        snapshot = librarian_snapshot()
        snapshot["report"]["related_evidence"].append({
            "refs": ["R5", "R99", "R5"],
            "summary": "额外引用",
            "relaxed_constraints": ["材料"],
        })
        duplicate = copy.deepcopy(snapshot["results"][2])
        duplicate["source_excerpt"] = "A duplicate public result for R1."
        snapshot["results"].append(duplicate)
        snapshot["results"].append({
            "agent_ref": "R5",
            "agent_cited": True,
            "agent_entity_type": "dataset",
        })
        snapshot["cited_count"] = 6

        with self.assertRaisesRegex(ResearchBriefError, "不一致"):
            build_research_brief(snapshot, generated_at=GENERATED_AT)

    def test_bare_gap_reference_cannot_pull_uncited_candidate_into_appendix(self):
        snapshot = librarian_snapshot()
        snapshot["report"]["database_gaps"].append("候选 R88 仍需进一步验证。")
        snapshot["report"]["suggested_followups"].append("以后可继续核对 R88。")

        brief = build_research_brief(snapshot, generated_at=GENERATED_AT)
        serialized = json.dumps(brief, ensure_ascii=False)

        self.assertNotIn('"ref": "R88"', serialized)
        self.assertEqual(brief["integrity"]["referenced_refs"], ["R1", "R2", "R3", "R4"])

    def test_rejects_uncited_clarification_zero_evidence_and_forged_bundle(self):
        uncited = librarian_snapshot()
        uncited["results"][2]["agent_cited"] = False
        with self.assertRaises(ResearchBriefError):
            build_research_brief(uncited, generated_at=GENERATED_AT)

        clarification = librarian_snapshot()
        clarification["clarification_required"] = True
        clarification["report"]["direct_conclusion"]["status"] = "clarification"
        with self.assertRaisesRegex(ResearchBriefError, "补充条件"):
            build_research_brief(clarification, generated_at=GENERATED_AT)

        empty = librarian_snapshot()
        empty["report"]["direct_conclusion"] = {"status": "not_found", "text": "没有证据", "refs": []}
        empty["report"]["evidence_matrix"] = []
        empty["report"]["related_evidence"] = []
        empty["results"] = []
        empty["cited_count"] = 0
        with self.assertRaisesRegex(ResearchBriefError, "至少需要一条"):
            build_research_brief(empty, generated_at=GENERATED_AT)

        forged = librarian_snapshot()
        forged["report"]["evidence_matrix"][0]["bundle_id"] = "B-FORGED"
        forged["report"]["evidence_matrix"][0]["result"] = "999 GPa"
        with self.assertRaises(ResearchBriefError):
            build_research_brief(forged, generated_at=GENERATED_AT)

    def test_not_found_with_real_adjacent_evidence_remains_exportable(self):
        snapshot = librarian_snapshot()
        snapshot["report"]["direct_conclusion"] = {
            "status": "not_found",
            "text": "没有同时满足全部硬条件的直接证据。",
            "refs": [],
        }
        snapshot["report"]["evidence_matrix"] = []
        snapshot["results"][1]["agent_cited"] = False
        snapshot["results"][2]["agent_cited"] = False
        snapshot["cited_count"] = 2

        brief = build_research_brief(snapshot, generated_at=GENERATED_AT)

        self.assertEqual(brief["report"]["direct_conclusion"]["status"], "not_found")
        self.assertEqual(brief["integrity"]["included_refs"], ["R3", "R4"])

    def test_private_fields_urls_and_local_paths_are_not_exported(self):
        snapshot = librarian_snapshot()
        snapshot["results"][2].update({
            "zotero_key": "SECRET-ZOTERO-KEY",
            "local_article_key": "SECRET-LOCAL-KEY",
            "reviewer": "SECRET-REVIEWER",
            "edit_note": "SECRET-INTERNAL-NOTE",
            "pdf_bytes": "SECRET-PDF-BYTES",
            "pdf_url": "/api/papers/1/pdf",
            "image_url": "/api/visual-assets/1/image",
            "source_excerpt": "See file:///Users/private/full.pdf and https://example.org/image.png",
        })

        brief, markdown = export_research_brief(snapshot, generated_at=GENERATED_AT)
        serialized = json.dumps(brief, ensure_ascii=False) + markdown

        for secret in (
            "SECRET-ZOTERO-KEY",
            "SECRET-LOCAL-KEY",
            "SECRET-REVIEWER",
            "SECRET-INTERNAL-NOTE",
            "SECRET-PDF-BYTES",
            "/Users/private/full.pdf",
            "https://example.org/image.png",
            "/api/papers/1/pdf",
            "/api/visual-assets/1/image",
        ):
            self.assertNotIn(secret, serialized)

    def test_protocol_html_credentials_and_extended_paths_are_blocked(self):
        blocked = librarian_snapshot()
        blocked["report"]["database_gaps"] = ["DSML tool_calls: <||invoke"]
        with self.assertRaisesRegex(ResearchBriefError, "工具协议"):
            build_research_brief(blocked, generated_at=GENERATED_AT)

        for disguised_protocol in (
            "ＤＳＭＬ ｔｏｏｌ＿ｃａｌｌｓ： ＜｜｜ｉｎｖｏｋｅ",
            "D&#83;ML",
            "D&amp;#83;ML",
            "ｔｏｏｌ＿ｃａｌｌｓ：",
        ):
            with self.subTest(disguised_protocol=disguised_protocol):
                disguised = librarian_snapshot()
                disguised["report"]["database_gaps"] = [disguised_protocol]
                with self.assertRaisesRegex(ResearchBriefError, "工具协议"):
                    build_research_brief(disguised, generated_at=GENERATED_AT)

        sanitized = librarian_snapshot()
        sanitized["results"][2]["source_excerpt"] = (
            "<img src=x onerror=alert(1)> "
            "javascript:alert(1) /Volumes/Lab/private.pdf ~/secret.txt "
            "../outside.txt \\\\server\\share\\paper.pdf "
            "api_key=abcdefghijklmnop123456"
        )
        brief, markdown = export_research_brief(sanitized, generated_at=GENERATED_AT)
        serialized = json.dumps(brief, ensure_ascii=False) + markdown
        for secret in (
            "<img",
            "javascript:",
            "/Volumes/Lab/private.pdf",
            "~/secret.txt",
            "../outside.txt",
            "\\\\server\\share\\paper.pdf",
            "abcdefghijklmnop123456",
        ):
            self.assertNotIn(secret, serialized)

        link_sanitized = librarian_snapshot()
        link_sanitized["results"][2]["source_excerpt"] = (
            "See //example.org/raw and [paper](https://example.org/paper) "
            "and ![image](//cdn.example.org/figure.png), "
            "mailto:private@example.org, then /usr/local/My Data/private file.pdf"
        )
        brief, markdown = export_research_brief(link_sanitized, generated_at=GENERATED_AT)
        serialized = json.dumps(brief, ensure_ascii=False) + markdown
        for secret in (
            "//example.org/raw",
            "https://example.org/paper",
            "//cdn.example.org/figure.png",
            "mailto:private@example.org",
            "/usr/local/My Data/private file.pdf",
        ):
            self.assertNotIn(secret, serialized)
        self.assertIn("paper", serialized)

        reference_image = librarian_snapshot()
        reference_image["report"]["direct_conclusion"]["text"] = (
            "检索到 2 条证据[R1][R2]。\n"
            "![tracker][p]\n"
            "[p]: &#x2F;&#x2F;evil.example/collect"
        )
        reference_image["results"][2]["source_excerpt"] = (
            "See /root/secret/file.pdf, www.evil.example/a, "
            "private@example.org and tel:+8613800000000."
        )
        _, markdown = export_research_brief(reference_image, generated_at=GENERATED_AT)
        for secret in (
            "evil.example",
            "![tracker][p]",
            "/root/secret/file.pdf",
            "www.evil.example/a",
            "private@example.org",
            "tel:+8613800000000",
        ):
            self.assertNotIn(secret, markdown)

    def test_quantitative_tokens_are_exact_and_scientific_notation_is_normalized(self):
        unsupported = librarian_snapshot()
        unsupported["report"]["evidence_matrix"][0]["result"] = "1e20 GPa"
        unsupported["results"][2]["value_text"] = "1"
        unsupported["results"][2]["article_title"] = "Unrelated experiment 1e20"
        unsupported["results"][2]["source_excerpt"] = "The reported value was 1 GPa."
        with self.assertRaisesRegex(ResearchBriefError, "无法支持的数值"):
            build_research_brief(unsupported, generated_at=GENERATED_AT)

        equivalent = librarian_snapshot()
        equivalent["report"]["evidence_matrix"][0]["result"] = "5E16 cm^-2"
        equivalent["results"][2]["value_text"] = "5 × 10^16"
        equivalent["results"][2]["unit"] = "cm^-2"
        equivalent["results"][2]["source_excerpt"] = (
            "The fluence was 5 × 10^16 cm^-2."
        )
        brief = build_research_brief(equivalent, generated_at=GENERATED_AT)
        self.assertEqual(
            brief["report"]["evidence_matrix"][0]["result"],
            "5E16 cm^-2",
        )

        superscript = librarian_snapshot()
        superscript["report"]["evidence_matrix"][0]["result"] = "5E16 cm⁻²"
        superscript["results"][2]["value_text"] = "5×10¹⁶"
        superscript["results"][2]["unit"] = "cm⁻²"
        superscript["results"][2]["source_excerpt"] = "The fluence was 5×10¹⁶ cm⁻²."
        build_research_brief(superscript, generated_at=GENERATED_AT)

        decimal_equivalent = librarian_snapshot()
        decimal_equivalent["report"]["evidence_matrix"][0]["result"] = "4.20 GPa"
        build_research_brief(decimal_equivalent, generated_at=GENERATED_AT)

    def test_quantitative_claim_units_must_match_the_same_cited_value(self):
        for wrong_unit in ("eV", "keV", "m^-2"):
            with self.subTest(wrong_unit=wrong_unit):
                snapshot = librarian_snapshot()
                snapshot["report"]["evidence_matrix"][0]["result"] = f"4.2 {wrong_unit}"
                with self.assertRaisesRegex(ResearchBriefError, "无法支持的数值"):
                    build_research_brief(snapshot, generated_at=GENERATED_AT)

        temperature = librarian_snapshot()
        temperature["report"]["evidence_matrix"][0]["result"] = "573 K"
        temperature["results"][2]["value_text"] = "573"
        temperature["results"][2]["unit"] = "K"
        temperature["results"][2]["source_excerpt"] = "The temperature was 573 K."
        build_research_brief(temperature, generated_at=GENERATED_AT)
        temperature["report"]["evidence_matrix"][0]["result"] = "573 keV"
        with self.assertRaisesRegex(ResearchBriefError, "无法支持的数值"):
            build_research_brief(temperature, generated_at=GENERATED_AT)

        celsius = librarian_snapshot()
        celsius["report"]["evidence_matrix"][0]["result"] = "300 Celsius"
        celsius["results"][2]["value_text"] = "300"
        celsius["results"][2]["unit"] = "°C"
        celsius["results"][2]["source_excerpt"] = "Tested at 300 °C."
        build_research_brief(celsius, generated_at=GENERATED_AT)

        unitless = librarian_snapshot()
        unitless["report"]["evidence_matrix"][0]["result"] = "4.2"
        build_research_brief(unitless, generated_at=GENERATED_AT)

        missing_unit = librarian_snapshot()
        missing_unit["results"][2]["unit"] = ""
        missing_unit["results"][2]["source_excerpt"] = "Hardness increased to 4.2."
        with self.assertRaisesRegex(ResearchBriefError, "无法支持的数值"):
            build_research_brief(missing_unit, generated_at=GENERATED_AT)

        for result in (
            "4.2 eV.",
            "4.2 eV!",
            "**4.2 eV**",
            "*4.2 eV*",
            "_4.2 eV_",
            "__4.2 eV__",
            "$4.2 eV$",
            r"$4.2\,\mathrm{eV}$",
            r"\(4.2\,\mathrm{eV}\)",
            "4.2&nbsp;eV",
            "4.2\u200beV",
            "4.2\u200ceV",
            "4.2\u2060eV",
            "4.2 ｅＶ",
            "4.2 ℯV",
            "𝟒.𝟐 eV",
            "4.2 `eV`",
            "4.2 J",
            "4.2 Sv",
            "4.2 mSv",
            "4.2 Bq",
            "4.2 mol",
            "4.2 ions/cm^-2",
        ):
            with self.subTest(result=result):
                unknown_or_wrapped = librarian_snapshot()
                unknown_or_wrapped["report"]["evidence_matrix"][0]["result"] = result
                with self.assertRaisesRegex(ResearchBriefError, "无法支持的数值"):
                    build_research_brief(
                        unknown_or_wrapped,
                        generated_at=GENERATED_AT,
                    )

        mixed_markup = librarian_snapshot()
        mixed_markup["report"]["evidence_matrix"][0]["result"] = (
            "4.2 GPa and **4.2 eV**"
        )
        with self.assertRaisesRegex(ResearchBriefError, "无法支持的数值"):
            build_research_brief(mixed_markup, generated_at=GENERATED_AT)

        compatible_gpa = librarian_snapshot()
        compatible_gpa["report"]["evidence_matrix"][0]["result"] = "𝟒.𝟐 ＧＰａ"
        build_research_brief(compatible_gpa, generated_at=GENERATED_AT)

        for multiplication in ("·", "⋅", "∙"):
            with self.subTest(multiplication=multiplication):
                unsupported = librarian_snapshot()
                unsupported["report"]["evidence_matrix"][0]["result"] = (
                    f"1{multiplication}10^20 GPa"
                )
                unsupported["results"][2]["value_text"] = "1"
                unsupported["results"][2]["source_excerpt"] = "Hardness was 1 GPa at 10 K."
                with self.assertRaisesRegex(ResearchBriefError, "无法支持的数值"):
                    build_research_brief(unsupported, generated_at=GENERATED_AT)

                equivalent = librarian_snapshot()
                equivalent["report"]["evidence_matrix"][0]["result"] = (
                    f"1{multiplication}10^20 GPa"
                )
                equivalent["results"][2]["value_text"] = "1e20"
                equivalent["results"][2]["source_excerpt"] = "Hardness was 1e20 GPa."
                build_research_brief(equivalent, generated_at=GENERATED_AT)

    def test_single_quantitative_or_comparative_statement_cannot_span_bundles(self):
        conclusion = librarian_snapshot()
        conclusion["report"]["direct_conclusion"]["text"] = (
            "报告硬度为 4.2 GPa，并同时引用另一直接证据[R1][R2]。"
        )
        conclusion["results"][1]["agent_bundle_id"] = "B2"
        with self.assertRaisesRegex(ResearchBriefError, "evidence bundle"):
            build_research_brief(conclusion, generated_at=GENERATED_AT)

        comparison = librarian_snapshot()
        comparison["report"]["direct_conclusion"]["text"] = (
            "R1 对应的结果高于 R2 对应的结果[R1][R2]。"
        )
        comparison["results"][1]["agent_bundle_id"] = "B2"
        with self.assertRaisesRegex(ResearchBriefError, "evidence bundle"):
            build_research_brief(comparison, generated_at=GENERATED_AT)

        retrieval_count = librarian_snapshot()
        retrieval_count["report"]["direct_conclusion"]["text"] = (
            "检索到 4 条公开证据[R1][R2]。"
        )
        retrieval_count["results"][1]["agent_bundle_id"] = "B2"
        build_research_brief(retrieval_count, generated_at=GENERATED_AT)

        for prose in (
            "R1 对应样品更硬[R1][R2]。",
            "R1 的硬度超过 R2[R1][R2]。",
            "R1 的硬度较高[R1][R2]。",
            "两个实验分别观察到不同缺陷[R1][R2]。",
        ):
            with self.subTest(prose=prose):
                cross_bundle = librarian_snapshot()
                cross_bundle["report"]["direct_conclusion"]["text"] = prose
                cross_bundle["results"][1]["agent_bundle_id"] = "B2"
                with self.assertRaisesRegex(
                    ResearchBriefError,
                    "只允许固定检索概览",
                ):
                    build_research_brief(cross_bundle, generated_at=GENERATED_AT)

    def test_independent_quantitative_matrix_rows_may_use_different_bundles(self):
        matrix = librarian_snapshot()
        matrix["results"][1]["agent_bundle_id"] = "B2"
        matrix["results"][1]["finding_text"] = "缺陷密度为 2 × 10^20 m^-2。"
        matrix["results"][1]["source_excerpt"] = "Defect density was 2e20 m^-2."
        matrix["report"]["evidence_matrix"].append({
            "refs": ["R2"],
            "bundle_id": "B2",
            "material": "CoCrFeMnNi",
            "conditions": "中子辐照；300°C",
            "property": "缺陷密度",
            "result": "2 × 10^20 m^-2",
            "article_title": "Irradiated alloy",
            "doi": "10.1000/example",
            "source_page": 6,
            "entity_type": "finding",
        })
        matrix["report"]["direct_conclusion"]["text"] = (
            "检索到 2 条满足全部硬条件的直接证据，"
            "已按论文、材料和实验条件整理。[R1][R2]"
        )
        brief = build_research_brief(matrix, generated_at=GENERATED_AT)
        self.assertEqual(
            {row["bundle_id"] for row in brief["report"]["evidence_matrix"]},
            {"B1", "B2"},
        )

    def test_related_comparison_cannot_span_bundles(self):
        snapshot = librarian_snapshot()
        snapshot["report"]["related_evidence"][0]["bundle_id"] = ""
        snapshot["report"]["related_evidence"][0]["summary"] = "后者更大。"
        snapshot["results"][0]["agent_bundle_id"] = "B3"

        with self.assertRaisesRegex(ResearchBriefError, "evidence bundle"):
            build_research_brief(snapshot, generated_at=GENERATED_AT)

    def test_hmac_binding_survives_browser_number_roundtrip_and_detects_tampering(self):
        secret = b"x" * 32
        snapshot = librarian_snapshot()
        snapshot["results"][2]["agent_constraint_coverage"] = 1.0
        snapshot["results"][2]["signed_zero"] = -0.0
        token = sign_research_brief_snapshot(snapshot, secret)
        browser_roundtrip = json.loads(json.dumps(snapshot))
        browser_roundtrip["results"][2]["agent_constraint_coverage"] = 1
        browser_roundtrip["results"][2]["signed_zero"] = 0

        self.assertTrue(verify_research_brief_snapshot(browser_roundtrip, token, secret))
        self.assertFalse(verify_research_brief_snapshot(browser_roundtrip, token, b"y" * 32))
        changed_time = copy.deepcopy(browser_roundtrip)
        changed_time["answered_at"] = "2026-07-30T10:00:00+00:00"
        self.assertFalse(verify_research_brief_snapshot(changed_time, token, secret))
        changed_version = copy.deepcopy(browser_roundtrip)
        changed_version["evidence_version"] = "sha256:other"
        self.assertFalse(verify_research_brief_snapshot(changed_version, token, secret))
        browser_roundtrip["report"]["evidence_matrix"][0]["result"] = "999 GPa"
        self.assertFalse(verify_research_brief_snapshot(browser_roundtrip, token, secret))

    def test_hmac_encoding_is_injective_and_tokens_are_strictly_bounded(self):
        secret = b"p" * 32
        numeric = librarian_snapshot()
        numeric["candidate_count"] = 1
        lookalike_object = copy.deepcopy(numeric)
        lookalike_object["candidate_count"] = {"$number": "1"}
        numeric_token = sign_research_brief_snapshot(numeric, secret)
        object_token = sign_research_brief_snapshot(lookalike_object, secret)

        self.assertNotEqual(numeric_token, object_token)
        self.assertFalse(
            verify_research_brief_snapshot(lookalike_object, numeric_token, secret)
        )
        for token in (None, "", "rb1.", "rb1.not-hex", "rb1." + "a" * 65, "x" * 100_000):
            with self.subTest(token=str(token)[:20]):
                self.assertFalse(verify_research_brief_snapshot(numeric, token, secret))

        # A service restart rotates the process-local key, invalidating an old
        # transient export token without touching the scientific database.
        self.assertFalse(
            verify_research_brief_snapshot(numeric, numeric_token, b"q" * 32)
        )

    def test_export_is_pure_and_never_reads_files_databases_or_network(self):
        fail = AssertionError("pure brief export attempted external I/O")
        with (
            patch("urllib.request.urlopen", side_effect=fail),
            patch("socket.create_connection", side_effect=fail),
            patch("sqlite3.connect", side_effect=fail),
            patch.object(Path, "open", side_effect=fail),
        ):
            brief, markdown = export_research_brief(
                librarian_snapshot(), generated_at=GENERATED_AT
            )
        self.assertEqual(brief["statistics"]["exported_cited_count"], 4)
        self.assertIn("# 图书管理员研究简报", markdown)

    def test_result_order_is_stable_with_injected_timestamp(self):
        first_snapshot = librarian_snapshot()
        second_snapshot = librarian_snapshot()
        second_snapshot["results"] = list(reversed(second_snapshot["results"]))

        first = build_research_brief(first_snapshot, generated_at=GENERATED_AT)
        second = build_research_brief(second_snapshot, generated_at=GENERATED_AT)

        self.assertEqual(first, second)
        self.assertEqual(
            render_research_brief_markdown(first),
            render_research_brief_markdown(second),
        )

    def test_markdown_contains_five_sections_appendix_checks_and_limits(self):
        brief, markdown = export_research_brief(librarian_snapshot(), generated_at=GENERATED_AT)

        self.assertIn("# 图书管理员研究简报", markdown)
        self.assertIn("### 1. 直接结论", markdown)
        self.assertIn("### 2. 证据矩阵", markdown)
        self.assertIn("### 3. 相关证据", markdown)
        self.assertIn("### 4. 数据库缺口", markdown)
        self.assertIn("### 5. 建议追问", markdown)
        self.assertIn("## 引用证据附录", markdown)
        self.assertIn("### [R1] item", markdown)
        self.assertIn("### [R4] figure", markdown)
        self.assertIn("- DOI：10.1000/example", markdown)
        self.assertIn("Hardness increased to 4.2 GPa.", markdown)
        self.assertIn("## 模型与召回统计", markdown)
        self.assertIn("## 完整性检查", markdown)
        self.assertIn("- 快照绑定：unverified", markdown)
        self.assertIn("- 原回答时间：2026-07-30T09:59:00+00:00", markdown)
        self.assertIn("- 文章语境：CoCrFeMnNi；中子辐照；300°C", markdown)
        self.assertIn("不从图片或曲线读取数据点", markdown)
        self.assertIn("不新增跨 evidence bundle 的定量比较", markdown)
        self.assertEqual(brief["generated_at"], GENERATED_AT)

    def test_missing_optional_provenance_stays_empty_and_is_reported(self):
        snapshot = librarian_snapshot()
        item = snapshot["results"][2]
        item["doi"] = ""
        item["source_page"] = None
        item["source_excerpt"] = ""

        brief = build_research_brief(snapshot, generated_at=GENERATED_AT)
        exported = brief["cited_evidence"][0]

        self.assertEqual(exported["doi"], "")
        self.assertIsNone(exported["source_page"])
        self.assertEqual(exported["source_excerpt"], "")
        self.assertEqual(
            brief["integrity"]["missing_provenance"][0],
            {"ref": "R1", "missing": ["doi", "source_page", "source_excerpt"]},
        )
        self.assertEqual(brief["integrity"]["status"], "warning")
        markdown = render_research_brief_markdown(brief)
        r1_section = markdown.split("### [R1] item", 1)[1].split("### [R2]", 1)[0]
        self.assertNotIn("- DOI：", r1_section)
        self.assertNotIn("- 页码：", r1_section)
        self.assertIn("来源字段缺失", markdown)

    def test_no_input_and_wrong_shape_raise_clear_errors(self):
        for value in (None, {}, []):
            with self.subTest(value=value):
                with self.assertRaises(ResearchBriefError):
                    build_research_brief(value, generated_at=GENERATED_AT)
        with self.assertRaisesRegex(ResearchBriefError, "question"):
            build_research_brief({"report": {}, "results": []}, generated_at=GENERATED_AT)
        with self.assertRaisesRegex(ResearchBriefError, "report"):
            build_research_brief({"question": "test", "results": []}, generated_at=GENERATED_AT)
        with self.assertRaisesRegex(ResearchBriefError, "results"):
            build_research_brief({
                "question": "test",
                "report": {"schema_version": "research-report-v1"},
                "results": {},
            }, generated_at=GENERATED_AT)

    def test_read_only_http_export_is_downloadable_and_does_not_write_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "brief-http.sqlite"
            db = EvidenceDB(db_path)
            db.init()
            before = hashlib.sha256(db_path.read_bytes()).hexdigest()
            handler = type(
                "ResearchBriefHTTPHandler",
                (EvidenceHandler,),
                {
                    "db": db,
                    "upload_service": None,
                    "read_only": True,
                    "research_brief_signing_key": b"h" * 32,
                    "log_message": lambda self, fmt, *args: None,
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                question = librarian_snapshot()["question"]
                fake_result = {
                    key: value
                    for key, value in librarian_snapshot().items()
                    if key != "question"
                }
                fake_result.update({
                    "answer": "结构化回答",
                    "agent": {"id": "librarian", "name": "图书管理员"},
                    "tool_calls": 1,
                    "recall_queries": ["高熵合金 中子辐照 硬度"],
                })

                class FakeLibrarianRuntime:
                    def __init__(self, _db):
                        pass

                    def run(self, _question, *, history=None):
                        return copy.deepcopy(fake_result)

                chat_request = Request(
                    f"{base}/api/agents/librarian/chat",
                    data=json.dumps({"question": question, "history": []}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch(
                    "auto_research.evidence.webapp.LibrarianAgentRuntime",
                    FakeLibrarianRuntime,
                ):
                    with urlopen(chat_request, timeout=5) as chat_response:
                        chat_result = json.loads(chat_response.read().decode("utf-8"))
                envelope = chat_result["research_brief"]
                self.assertTrue(envelope["eligible"])
                self.assertTrue(envelope["snapshot_token"].startswith("rb1."))
                snapshot = research_brief_snapshot_from_result(question, chat_result)
                request = Request(
                    f"{base}/api/agents/librarian/research-brief.md",
                    data=json.dumps({
                        "snapshot": snapshot,
                        "snapshot_token": envelope["snapshot_token"],
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=5) as response:
                    markdown = response.read().decode("utf-8")
                    self.assertEqual(response.status, 200)
                    self.assertTrue(response.headers["Content-Type"].startswith("text/markdown"))
                    self.assertIn("attachment; filename=", response.headers["Content-Disposition"])
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertIn("# 图书管理员研究简报", markdown)
                self.assertIn("### [R4] figure", markdown)
                self.assertIn("- 快照绑定：server_hmac_verified", markdown)

                for mode in ("clarification", "zero_evidence"):
                    ineligible = copy.deepcopy(fake_result)
                    ineligible["results"] = []
                    ineligible["candidate_count"] = 0
                    ineligible["cited_count"] = 0
                    ineligible["report"]["evidence_matrix"] = []
                    ineligible["report"]["related_evidence"] = []
                    ineligible["report"]["direct_conclusion"] = {
                        "status": "clarification" if mode == "clarification" else "not_found",
                        "text": "请补充条件" if mode == "clarification" else "没有证据",
                        "refs": [],
                    }
                    ineligible["clarification_required"] = mode == "clarification"
                    fake_result = ineligible
                    with patch(
                        "auto_research.evidence.webapp.LibrarianAgentRuntime",
                        FakeLibrarianRuntime,
                    ):
                        with urlopen(chat_request, timeout=5) as ineligible_response:
                            ineligible_body = json.loads(
                                ineligible_response.read().decode("utf-8")
                            )
                    self.assertFalse(ineligible_body["research_brief"]["eligible"])
                    self.assertEqual(ineligible_body["research_brief"]["snapshot_token"], "")
                    self.assertTrue(ineligible_body["research_brief"]["ineligible_reason"])

                invalid = Request(
                    f"{base}/api/agents/librarian/research-brief.md",
                    data=json.dumps({
                        "snapshot": {"question": "missing report"},
                        "snapshot_token": sign_research_brief_snapshot(
                            {"question": "missing report"},
                            handler.research_brief_signing_key,
                        ),
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as invalid_error:
                    urlopen(invalid, timeout=5)
                self.assertEqual(invalid_error.exception.code, 422)
                invalid_error.exception.close()

                tampered = copy.deepcopy(snapshot)
                tampered["report"]["evidence_matrix"][0]["result"] = "999 GPa"
                bad_signature = Request(
                    f"{base}/api/agents/librarian/research-brief.md",
                    data=json.dumps({
                        "snapshot": tampered,
                        "snapshot_token": sign_research_brief_snapshot(
                            snapshot,
                            handler.research_brief_signing_key,
                        ),
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as signature_error:
                    urlopen(bad_signature, timeout=5)
                self.assertEqual(signature_error.exception.code, 403)
                self.assertEqual(
                    json.loads(signature_error.exception.read().decode("utf-8"))["code"],
                    "invalid_snapshot_token",
                )
                signature_error.exception.close()

                non_object = Request(
                    f"{base}/api/agents/librarian/research-brief.md",
                    data=b"[]",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as non_object_error:
                    urlopen(non_object, timeout=5)
                self.assertEqual(non_object_error.exception.code, 400)
                non_object_error.exception.close()

                forbidden = Request(
                    f"{base}/api/current-paper/save-snapshot",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as forbidden_error:
                    urlopen(forbidden, timeout=5)
                self.assertEqual(forbidden_error.exception.code, 403)
                forbidden_error.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
            after = hashlib.sha256(db_path.read_bytes()).hexdigest()
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
