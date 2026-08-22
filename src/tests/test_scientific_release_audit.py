from __future__ import annotations

import copy
import json
import unittest

from auto_research.evidence.scientific_release_audit import (
    CANDIDATE_SCHEMA,
    DIMENSIONS,
    GOLD_SCHEMA,
    REQUEST_SCHEMA,
    ScientificReleaseAuditError,
    audit_scientific_release,
)


def values_for(entity_type: str, *, populated: bool = True) -> dict[str, object]:
    values = {dimension: None for dimension in DIMENSIONS}
    if not populated:
        return values
    locator = {"page": 3, "label": "source", "bbox": [0.1, 0.2, 0.8, 0.9]}
    screenshot = {
        "present": True,
        "content_sha256": "a" * 64,
        "width": 1200,
        "height": 800,
    }
    values["source_excerpt"] = "audited source excerpt"
    values["locator"] = locator
    if entity_type == "item":
        values.update(
            {
                "numeric_value": "4.2",
                "unit": "GPa",
                "physical_meaning": "irradiation hardness",
                "experimental_conditions": {"temperature": "300 K"},
            }
        )
    elif entity_type == "table":
        values.update(
            {
                "table_discovery": True,
                "table_structure": {"columns": ["sample", "hardness"], "rows": 4},
                "table_screenshot": screenshot,
            }
        )
    elif entity_type == "figure":
        values.update(
            {
                "figure_discovery": True,
                "figure_caption": "Hardness after irradiation",
                "figure_page": 3,
                "figure_screenshot": screenshot,
            }
        )
    elif entity_type == "finding":
        values["conclusion"] = "Hardness increased after irradiation."
    return values


def gold_record(paper: str, uid: str, entity_type: str, *, populated: bool = True):
    values = values_for(entity_type, populated=populated)
    return {
        "schema_version": GOLD_SCHEMA,
        "paper_uid": paper,
        "entity_uid": uid,
        "entity_type": entity_type,
        "split": "test",
        "human_reviewed": True,
        "judgments": {
            dimension: {"adjudicated": True, "value": values[dimension]}
            for dimension in DIMENSIONS
        },
    }


def candidate_record(paper: str, uid: str, entity_type: str, *, populated: bool = True):
    return {
        "schema_version": CANDIDATE_SCHEMA,
        "paper_uid": paper,
        "entity_uid": uid,
        "entity_type": entity_type,
        "split": "test",
        "values": values_for(entity_type, populated=populated),
    }


def complete_records(*, populated: bool = True):
    kinds = ("item", "table", "figure", "finding")
    gold = [gold_record(f"paper-{index}", f"{kind}-{index}", kind, populated=populated) for index, kind in enumerate(kinds, 1)]
    candidates = [candidate_record(f"paper-{index}", f"{kind}-{index}", kind, populated=populated) for index, kind in enumerate(kinds, 1)]
    return gold, candidates


def request(gold, candidates, *, coverage=1.0, f1=1.0):
    return {
        "schema_version": REQUEST_SCHEMA,
        "gold_records": gold,
        "candidate_records": candidates,
        "minimum_paper_coverage": coverage,
        "minimum_dimension_f1": f1,
    }


class ScientificReleaseAuditTests(unittest.TestCase):
    def test_perfect_human_gold_is_ready_and_separate_from_software_tests(self):
        gold, candidates = complete_records()
        result = audit_scientific_release(request(gold, candidates))
        self.assertTrue(result["scientific_release_ready"])
        self.assertEqual(result["reason_codes"], [])
        self.assertEqual(result["human_review"], {"papers": 4, "records": 4})
        self.assertEqual(result["coverage"]["paper_coverage"], 1.0)
        self.assertEqual(result["coverage"]["record_coverage"], 1.0)
        for dimension in DIMENSIONS:
            self.assertEqual(result["dimensions"][dimension]["f1"], 1.0)
        self.assertEqual(result["locator_quality"]["mean_iou"], 1.0)
        self.assertEqual(result["locator_quality"]["page_consistency"], 1.0)
        self.assertFalse(result["claim_boundary"]["software_tests_are_scientific_accuracy"])
        self.assertFalse(result["claim_boundary"]["ai_was_called"])

    def test_empty_gold_never_passes_and_zero_denominators_are_null(self):
        result = audit_scientific_release(request([], [], coverage=0.0, f1=0.0))
        self.assertFalse(result["scientific_release_ready"])
        self.assertIn("gold_empty", result["reason_codes"])
        self.assertIsNone(result["coverage"]["paper_coverage"])
        self.assertIsNone(result["dimensions"]["numeric_value"]["precision"])
        self.assertIsNone(result["dimensions"]["numeric_value"]["recall"])
        self.assertIsNone(result["dimensions"]["numeric_value"]["f1"])
        self.assertIsNone(result["locator_quality"]["mean_iou"])

    def test_only_software_test_fields_are_rejected(self):
        payload = request([], [])
        payload["software_tests_passed"] = 999
        with self.assertRaises(ScientificReleaseAuditError) as caught:
            audit_scientific_release(payload)
        self.assertEqual(caught.exception.code, "scientific_audit_invalid")

    def test_duplicate_identity_and_cross_split_paper_fail_closed(self):
        gold, candidates = complete_records()
        with self.assertRaises(ScientificReleaseAuditError) as duplicate:
            audit_scientific_release(request(gold + [copy.deepcopy(gold[0])], candidates))
        self.assertEqual(duplicate.exception.code, "scientific_audit_duplicate_identity")

        split_leak = copy.deepcopy(gold)
        split_leak[1]["paper_uid"] = split_leak[0]["paper_uid"]
        split_leak[1]["split"] = "train"
        with self.assertRaises(ScientificReleaseAuditError) as leakage:
            audit_scientific_release(request(split_leak, candidates))
        self.assertEqual(leakage.exception.code, "scientific_audit_split_leakage")

    def test_same_entity_uid_cannot_match_across_papers(self):
        gold = [gold_record("paper-a", "stable-item", "item")]
        candidate = [candidate_record("paper-b", "stable-item", "item")]
        with self.assertRaises(ScientificReleaseAuditError) as caught:
            audit_scientific_release(request(gold, candidate))
        self.assertEqual(caught.exception.code, "scientific_audit_cross_paper_identity")

    def test_missing_human_judgment_and_unknown_record_field_fail_closed(self):
        gold, candidates = complete_records()
        missing = copy.deepcopy(gold)
        del missing[0]["judgments"]["unit"]
        with self.assertRaises(ScientificReleaseAuditError) as caught:
            audit_scientific_release(request(missing, candidates))
        self.assertEqual(caught.exception.code, "scientific_audit_missing_judgment")

        unknown = copy.deepcopy(gold)
        unknown[0]["tests_passed"] = True
        with self.assertRaises(ScientificReleaseAuditError) as caught:
            audit_scientific_release(request(unknown, candidates))
        self.assertEqual(caught.exception.code, "scientific_audit_invalid")

    def test_paths_keys_and_internal_ids_never_enter_results_or_errors(self):
        gold, candidates = complete_records()
        unsafe = copy.deepcopy(candidates)
        unsafe[0]["values"]["source_excerpt"] = "saved at /Users/name/paper.pdf"
        with self.assertRaises(ScientificReleaseAuditError) as caught:
            audit_scientific_release(request(gold, unsafe))
        public = caught.exception.public_dict()
        self.assertEqual(public["code"], "scientific_audit_unsafe_value")
        rendered = json.dumps(public, ensure_ascii=False).casefold()
        self.assertNotIn("/users", rendered)
        self.assertNotIn("paper.pdf", rendered)

        secret = copy.deepcopy(candidates)
        secret[1]["values"]["table_structure"] = {"api_key": "do-not-leak"}
        with self.assertRaises(ScientificReleaseAuditError) as caught:
            audit_scientific_release(request(gold, secret))
        self.assertNotIn("do-not-leak", json.dumps(caught.exception.public_dict()))

    def test_human_adjudicated_but_all_negative_dimensions_are_not_fake_perfect(self):
        gold, candidates = complete_records(populated=False)
        result = audit_scientific_release(request(gold, candidates, coverage=1.0, f1=0.0))
        self.assertFalse(result["scientific_release_ready"])
        self.assertIsNone(result["dimensions"]["table_discovery"]["f1"])
        self.assertIn("dimension_f1_unavailable:table_discovery", result["reason_codes"])

    def test_missing_candidate_is_zero_f1_and_zero_locator_quality(self):
        gold, _candidates = complete_records()
        result = audit_scientific_release(request(gold, [], coverage=0.0, f1=0.0))
        self.assertEqual(result["dimensions"]["numeric_value"]["f1"], 0.0)
        self.assertEqual(result["dimensions"]["locator"]["f1"], 0.0)
        self.assertEqual(result["locator_quality"]["mean_iou"], 0.0)
        self.assertEqual(result["locator_quality"]["page_consistency"], 0.0)

    def test_wrong_value_counts_false_positive_and_negative_and_is_reproducible(self):
        gold, candidates = complete_records()
        candidates[0]["values"]["numeric_value"] = "9.9"
        candidates[1]["values"]["table_screenshot"]["content_sha256"] = "b" * 64
        candidates[2]["values"]["figure_caption"] = None
        first = audit_scientific_release(request(gold, candidates, coverage=1.0, f1=0.8))
        second = audit_scientific_release(
            request(list(reversed(gold)), list(reversed(candidates)), coverage=1.0, f1=0.8)
        )
        self.assertEqual(first, second)
        self.assertEqual(first["dimensions"]["numeric_value"]["fp"], 1)
        self.assertEqual(first["dimensions"]["numeric_value"]["fn"], 1)
        self.assertEqual(first["dimensions"]["table_screenshot"]["fp"], 1)
        self.assertEqual(first["dimensions"]["table_screenshot"]["fn"], 1)
        self.assertEqual(first["dimensions"]["figure_caption"]["fn"], 1)
        self.assertFalse(first["scientific_release_ready"])
        self.assertEqual(
            first["error_cases"],
            sorted(
                first["error_cases"],
                key=lambda row: (row["paper_uid"], row["entity_uid"], row["dimension"], row["error"]),
            ),
        )
        rendered = json.dumps(first, ensure_ascii=False).casefold()
        for forbidden in ("api_key", "credential_ref", "file_path", "run_id"):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
