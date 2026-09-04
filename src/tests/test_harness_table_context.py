from __future__ import annotations

import copy
from types import SimpleNamespace
import unittest

from auto_research.ai.business_actions import BusinessActionError
from auto_research.evidence.harness_table_context import HarnessTableContextAuthority, TABLE_CONTEXT_KIND


IDENTITY = {"source_scope": "official", "source_id": "official-v1", "entity_type": "table", "entity_uid": "table-3"}
ROWS = [["Material", "ΔH (GPa)"], ["HEA", "1.07 ± 0.06"], ["316H", "1.01 ± 0.07"]]


class HarnessTableContextTests(unittest.TestCase):
    def setUp(self):
        self.grid = {**IDENTITY, "status": "verified", "version": 2, "rows": copy.deepcopy(ROWS),
                     "review_note": "must never be sent", "path": "/private/not-for-ai"}
        self.calls = []

        def get(uid, **kwargs):
            self.calls.append((uid, kwargs))
            return copy.deepcopy(self.grid)

        self.authority = HarnessTableContextAuthority(official=SimpleNamespace(get=get))

    def test_verified_rows_are_frozen_and_revalidated_separately_from_search_index(self):
        context, unit = self.authority.freeze(IDENTITY)
        self.assertEqual(context["rows"], ROWS)
        self.assertNotIn("path", str(context))
        self.assertNotIn("review_note", str(context))
        self.assertFalse(self.calls[0][1]["include_unverified"])
        self.assertEqual(self.authority.fingerprint_for(kind=unit.kind, stable_source_identity=unit.stable_source_identity), unit.snapshot_fingerprint)
        self.grid["rows"][1][1] = "corrected"
        self.assertEqual(context["rows"], ROWS)  # already-authorized context cannot mutate
        self.assertNotEqual(self.authority.fingerprint_for(kind=unit.kind, stable_source_identity=unit.stable_source_identity), unit.snapshot_fingerprint)

    def test_absent_pending_rejected_never_send_cells_and_approval_changes_binding(self):
        for status in ("candidate", "pending", "rejected"):
            self.grid["status"] = status
            context, unit = self.authority.freeze(IDENTITY)
            self.assertEqual(context["status"], "unavailable")
            self.assertNotIn("rows", context)
            self.grid["status"] = "verified"
            self.assertNotEqual(self.authority.fingerprint_for(kind=unit.kind, stable_source_identity=unit.stable_source_identity), unit.snapshot_fingerprint)

    def test_private_wrong_identity_corruption_and_oversize_fail_before_model(self):
        for patch in ({"entity_uid": "different"}, {"version": True}, {"rows": [["A"], ["1", "2"]]}, {"rows": [["/Users/person/private.csv"]]}, {"rows": [["x" * 24001]]}):
            original = self.grid
            self.grid = {**original, **patch}
            with self.subTest(patch=tuple(patch)):
                with self.assertRaises(BusinessActionError):
                    self.authority.freeze(IDENTITY)
            self.grid = original
        with self.assertRaises(BusinessActionError):
            self.authority.freeze({**IDENTITY, "source_scope": "private"})
        with self.assertRaises(ValueError):
            self.authority.fingerprint_for(kind=TABLE_CONTEXT_KIND, stable_source_identity="unknown")

    def test_workspace_uses_same_public_resolver_including_identity_verified_official_link(self):
        identity = {**IDENTITY, "source_scope": "workspace", "source_id": "workspace", "entity_uid": "workspace-table-public"}
        calls = []

        def get(**request):
            calls.append(request)
            return {**identity, "image_url": "/api/desktop/image", "table_structure": {**self.grid, "origin": "linked_official", "available": True}}

        authority = HarnessTableContextAuthority(workspace=SimpleNamespace(get=get))
        context, _ = authority.freeze(identity)
        self.assertEqual(calls, [identity])
        self.assertEqual(context["entity"], identity)
        self.assertEqual(context["rows"], ROWS)
        self.assertNotIn("image_url", str(context))
