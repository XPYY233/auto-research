from __future__ import annotations

import unittest

from auto_research.evidence.search_experience import (
    SearchSourceIdentity,
    attach_source_identity,
    route_search,
    search_capabilities,
)


class SearchExperienceTests(unittest.TestCase):
    def test_short_keywords_use_precise_search(self):
        route = route_search("高熵合金 中子辐照 硬度")
        self.assertEqual(route.execution_mode, "exact")
        self.assertEqual(route.reason_code, "short_keyword_query")
        self.assertEqual(route.effective_domains, ("literature",))

    def test_explanation_uses_librarian(self):
        route = route_search("为什么中子辐照后硬度升高？")
        self.assertEqual(route.execution_mode, "librarian")
        self.assertEqual(route.reason_code, "synthesis_requested")
        self.assertIn("为什么", route.synthesis_signals)

    def test_synthesis_wins_over_exact_identifier(self):
        route = route_search("比较 DOI: 10.1234/example.1 和其他论文的硬度趋势")
        self.assertEqual(route.execution_mode, "librarian")
        self.assertIn("doi", route.exact_signals)
        self.assertIn("比较", route.synthesis_signals)

    def test_exact_identifier_uses_precise_search(self):
        route = route_search("打开样品编号 HEA-2026-07")
        self.assertEqual(route.execution_mode, "exact")
        self.assertEqual(route.reason_code, "exact_lookup_requested")
        self.assertIn("identifier", route.exact_signals)

    def test_user_mode_override_is_respected(self):
        route = route_search(
            "为什么硬度升高？",
            requested_mode="precise",
        )
        self.assertEqual(route.execution_mode, "exact")
        self.assertEqual(route.reason_code, "user_selected_mode")
        self.assertIn("为什么", route.synthesis_signals)

    def test_missing_personal_store_is_not_silently_replaced(self):
        route = route_search("HEA-2026-07", requested_scope="personal")
        self.assertEqual(route.effective_domains, ())
        self.assertTrue(route.partial_scope)
        self.assertEqual(
            route.notices,
            ("personal_store_unavailable", "no_requested_source_available"),
        )

    def test_all_scope_reports_partial_availability(self):
        route = route_search("硬度", requested_scope="all")
        self.assertEqual(route.effective_domains, ("literature",))
        self.assertTrue(route.partial_scope)
        self.assertEqual(route.notices, ("personal_store_unavailable",))

    def test_all_scope_uses_both_available_sources_in_stable_order(self):
        route = route_search(
            "硬度",
            requested_scope="all",
            available_domains=("personal", "literature"),
        )
        self.assertEqual(route.effective_domains, ("literature", "personal"))
        self.assertFalse(route.partial_scope)

    def test_public_contract_contains_override_and_source_state(self):
        payload = route_search(
            "硬度",
            source_scope="official",
            source_id="official-2026-08",
            entity_uid="paper:10.1234/example:item:hardness",
        ).as_dict()
        self.assertEqual(payload["schema_version"], "search-route-v1")
        self.assertTrue(payload["user_can_override_mode"])
        self.assertEqual(payload["effective_domains"], ["literature"])
        self.assertEqual(payload["source_scope"], "official")
        self.assertEqual(payload["source_id"], "official-2026-08")
        self.assertEqual(payload["entity_uid"], "paper:10.1234/example:item:hardness")

    def test_precise_alias_and_browse_filter_modes_are_supported(self):
        self.assertEqual(
            route_search("硬度", requested_mode="precise").execution_mode,
            "exact",
        )
        self.assertEqual(route_search("硬度", requested_mode="browse").execution_mode, "browse")
        self.assertEqual(route_search("硬度", requested_mode="filter").execution_mode, "filter")

    def test_source_identity_keeps_four_evidence_types_and_allows_empty_compatibility(self):
        result = attach_source_identity({"entity_type": "item", "meaning": "硬度"})
        self.assertIsNone(result["source_scope"])
        self.assertIsNone(result["source_id"])
        self.assertIsNone(result["entity_uid"])

        official = attach_source_identity(
            {"entity_type": "figure", "caption": "Figure 2"},
            SearchSourceIdentity("official", "official-2026-08", "figure:abc"),
        )
        self.assertEqual(official["source_scope"], "official")
        with self.assertRaises(ValueError):
            attach_source_identity({"entity_type": "recommendation"})

    def test_capabilities_do_not_claim_personal_store_before_import_exists(self):
        payload = search_capabilities(personal_available=False)
        scopes = {row["id"]: row for row in payload["scopes"]}
        self.assertEqual(payload["default_scope"], "literature")
        self.assertFalse(scopes["personal"]["available"])
        self.assertFalse(scopes["all"]["available"])

    def test_invalid_or_empty_requests_are_rejected(self):
        with self.assertRaises(ValueError):
            route_search("   ")
        with self.assertRaises(ValueError):
            route_search("硬度", requested_scope="unknown")
        with self.assertRaises(ValueError):
            route_search("硬度", available_domains=("private-cloud",))
        with self.assertRaises(ValueError):
            route_search("硬度", source_scope="shared")


if __name__ == "__main__":
    unittest.main()
