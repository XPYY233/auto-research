"""Performance pruning must preserve the legacy scientific merge predicate."""
from __future__ import annotations

import random
import unittest
from unittest.mock import patch

from auto_research.evidence import fact_model as facts


class QualitativeClusterBoundsTests(unittest.TestCase):
    def test_threshold_predicate_matches_original_ratio_and_containment(self):
        rng = random.Random(912)
        pairs = [
            ("", ""), ("硬化", "硬化"), ("abc", "abcdef"),
            ("a" * 250, "b" + "a" * 250),  # autojunk/containment
            ("温度" * 120 + "硬度", "温度" * 120 + "空洞"),
        ]
        alphabet = "abcde温度硬化空洞012345±"
        for _ in range(160):
            left = "".join(rng.choices(alphabet, k=rng.randrange(1, 100)))
            right = left[:rng.randrange(len(left))] + "".join(rng.choices(alphabet, k=rng.randrange(1, 70)))
            pairs.append((left, right))
        for left, right in pairs:
            a, b = facts._compact(left), facts._compact(right)
            for threshold in (0.48, 0.58, 0.68, 0.82):
                with self.subTest(left=left, right=right, threshold=threshold):
                    self.assertEqual(
                        facts._similarity_at_least(a, b, threshold),
                        facts._similarity(left, right) >= threshold,
                    )

    def test_clusters_and_stable_identities_match_unpruned_scores(self):
        rows = [
            {
                "item_id": n + 1, "paper_id": 1 + n // 20,
                "value_text": ("no voids were observed", "hardening increased", "reduced swelling", "no visible changes")[n % 4],
                "meaning": ("空洞观察结果", "硬度变化", "肿胀趋势")[n % 3],
                "source_excerpt": ("irradiation response " * (n % 5 + 1)) + str(n % 7),
                "source_page": 1 + n % 3,
            }
            for n in range(60)
        ]
        actual = facts.cluster_qualitative_rows(rows)
        with patch.object(facts, "_similarity_at_least", side_effect=lambda a, b, t: facts._similarity(a, b) >= t):
            unpruned = facts.cluster_qualitative_rows(rows)
        self.assertEqual(actual, unpruned)

    def test_disjoint_text_never_runs_expensive_alignment(self):
        with patch.object(facts.difflib.SequenceMatcher, "ratio", side_effect=AssertionError("unnecessary alignment")):
            self.assertFalse(facts._similarity_at_least("abc" * 100, "xyz" * 100, 0.48))


if __name__ == "__main__":
    unittest.main()
