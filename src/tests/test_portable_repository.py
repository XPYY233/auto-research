from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from auto_research.product import portable_repository as repository_module

from auto_research.product.portable_repository import (
    DATABASE_CONTRACT,
    DATABASE_PATH,
    DISTRIBUTION_SCHEMA_VERSION,
    OfficialEvidenceRepository,
    PortableExportPlan,
    PortableRepositoryError,
    ReleasePolicy,
    audit_portable_repository,
    materialize_portable_repository,
    provenance_for_papers,
    stable_entity_uid,
    stable_paper_uid,
)


class PortableRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="portable-repository-test-")
        self.root = Path(self.temporary.name)
        self.paper = {
            "doi": "https://doi.org/10.1000/TEST.1",
            "title": "A controlled irradiation experiment",
            "year": 2025,
            "first_author": "Wei Chen",
            "corresponding_author": "Li Zhang",
            "material_focus": "W–Ta alloy",
        }
        self.paper_uid = stable_paper_uid(
            doi=self.paper["doi"],
            title=self.paper["title"],
            year=self.paper["year"],
            first_author=self.paper["first_author"],
        )
        self.entity = {
            "paper_uid": self.paper_uid,
            "entity_type": "item",
            "identity_key": "stable-source-item-1",
            "quality_gate_status": "dual_pass",
            "source_kind": "text",
            "review_action": "automatic",
            "payload": {
                "value_text": "5 × 10^16",
                "meaning": "辐照注量",
                "unit": "cm^-2",
                "context_explanation": "W–Ta 合金，室温 He 离子辐照",
                "source_page": 3,
                "source_locator": "Results, paragraph 2",
                "source_excerpt": "irradiated to a fluence of 5 × 10^16 cm^-2",
                "evidence_count": 1,
                "evidence_occurrences": [
                    {
                        "source_page": 3,
                        "source_locator": "Results, paragraph 2",
                        "source_excerpt": "irradiated to a fluence of 5 × 10^16 cm^-2",
                    }
                ],
            },
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def policy(self, *, excerpts: bool = True, maximum: int = 1000) -> ReleasePolicy:
        return ReleasePolicy(
            distribution_scope="internal-preview-only",
            allowed_paper_uids=frozenset({self.paper_uid}),
            allow_structured_evidence=True,
            allow_short_excerpts=excerpts,
            maximum_excerpt_chars=maximum if excerpts else 0,
            maximum_excerpt_chars_per_paper=4000 if excerpts else 0,
            maximum_excerpt_chars_total=8000 if excerpts else 0,
            accepted_dropped_by_reason={"manual_review": 2},
        )

    def plan(self, *, entity: dict | None = None) -> PortableExportPlan:
        return PortableExportPlan(
            papers=(self.paper,),
            entities=(entity or self.entity,),
            dropped_by_reason={"manual_review": 2},
            private_source_sha256="a" * 64,
        )

    def build(self, name: str = "repository"):
        return materialize_portable_repository(
            self.plan(),
            self.root / name,
            package_id="official-fusion-preview",
            package_version="0.1.0-preview.1",
            release_policy=self.policy(),
            provenance=provenance_for_papers(
                (self.paper,), publisher="Auto Research internal preview"
            ),
        )

    def test_stable_identity_normalizes_doi_and_supports_explicit_lineage(self) -> None:
        equivalent = stable_paper_uid(
            doi="doi:10.1000/test.1",
            title="changed title",
            year=2030,
            first_author="Someone Else",
        )
        self.assertEqual(equivalent, self.paper_uid)
        entity_uid = stable_entity_uid(self.paper_uid, "item", "stable-source-item-1")
        explicit = dict(self.paper, paper_uid=self.paper_uid, title="Corrected title")
        plan = PortableExportPlan(
            papers=(explicit,),
            entities=({**self.entity, "entity_uid": entity_uid},),
            dropped_by_reason={"manual_review": 2},
        )
        output = materialize_portable_repository(
            plan,
            self.root / "lineage",
            package_id="official-fusion-preview",
            package_version="0.1.0",
            release_policy=self.policy(),
            provenance=provenance_for_papers(
                (explicit,), publisher="Auto Research internal preview"
            ),
        )
        repo = OfficialEvidenceRepository.open(output.root)
        self.assertEqual(repo.get_entity(entity_uid)["entity_uid"], entity_uid)

    def test_supplied_identity_must_match_canonical_identity(self) -> None:
        wrong_paper = dict(self.paper, paper_uid="paper_" + "f" * 32)
        with self.assertRaises(PortableRepositoryError) as raised:
            materialize_portable_repository(
                PortableExportPlan(papers=(wrong_paper,), entities=()),
                self.root / "wrong-paper-uid",
                package_id="official-fusion-preview",
                package_version="0.1.0",
                release_policy=ReleasePolicy(
                    distribution_scope="internal-preview-only",
                    allowed_paper_uids=frozenset({"paper_" + "f" * 32}),
                    allow_structured_evidence=True,
                ),
                provenance={
                    "schema_version": "auto-research-provenance-v1",
                    "publisher": "test",
                    "sources": [],
                },
            )
        self.assertEqual(raised.exception.code, "paper_identity")

        wrong_entity = dict(self.entity, entity_uid="entity_item_" + "e" * 32)
        with self.assertRaises(PortableRepositoryError) as raised:
            materialize_portable_repository(
                self.plan(entity=wrong_entity),
                self.root / "wrong-entity-uid",
                package_id="official-fusion-preview",
                package_version="0.1.0",
                release_policy=self.policy(),
                provenance=provenance_for_papers(
                    (self.paper,), publisher="Auto Research internal preview"
                ),
            )
        self.assertEqual(raised.exception.code, "entity_identity")

    def test_excerpt_budget_is_enforced_per_paper_and_deduplicates_repeats(self) -> None:
        entity = json.loads(json.dumps(self.entity))
        excerpt = "A" * 900
        entity["payload"]["source_excerpt"] = excerpt
        entity["payload"]["evidence_occurrences"] = [
            {"source_page": 3, "source_excerpt": excerpt},
            {"source_page": 4, "source_excerpt": "B" * 900},
        ]
        policy = ReleasePolicy(
            distribution_scope="internal-preview-only",
            allowed_paper_uids=frozenset({self.paper_uid}),
            allow_structured_evidence=True,
            allow_short_excerpts=True,
            maximum_excerpt_chars=1000,
            maximum_excerpt_chars_per_paper=1700,
            maximum_excerpt_chars_total=1700,
            accepted_dropped_by_reason={"manual_review": 2},
        )
        with self.assertRaises(PortableRepositoryError) as raised:
            materialize_portable_repository(
                self.plan(entity=entity),
                self.root / "excerpt-budget",
                package_id="official-fusion-preview",
                package_version="0.1.0",
                release_policy=policy,
                provenance=provenance_for_papers(
                    (self.paper,), publisher="Auto Research internal preview"
                ),
            )
        self.assertEqual(raised.exception.code, "rights_scope")

    def test_materialized_repository_is_auditable_and_read_only(self) -> None:
        output = self.build()
        audit = audit_portable_repository(
            output.root,
            expected_package_id="official-fusion-preview",
            expected_version="0.1.0-preview.1",
        )
        self.assertEqual(audit.paper_count, 1)
        self.assertEqual(audit.entity_count, 1)
        self.assertEqual(audit.asset_count, 0)
        self.assertEqual(output.private_source_sha256, "a" * 64)
        database = output.root / DATABASE_PATH
        connection = sqlite3.connect(f"{database.as_uri()}?mode=ro&immutable=1", uri=True)
        try:
            metadata = dict(connection.execute("SELECT key,value FROM schema_meta"))
        finally:
            connection.close()
        self.assertEqual(metadata["schema_version"], str(DISTRIBUTION_SCHEMA_VERSION))
        self.assertEqual(metadata["database_contract"], DATABASE_CONTRACT)
        self.assertNotIn("source_snapshot_sha256", metadata)
        self.assertFalse(Path(str(database) + "-wal").exists())
        self.assertFalse(Path(str(database) + "-shm").exists())

    def test_official_repository_returns_only_package_aware_four_type_document(self) -> None:
        output = self.build()
        repo = OfficialEvidenceRepository.open(output.root)
        rows = list(repo.iter_search_documents())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["entity_type"], "item")
        self.assertEqual(row["source_scope"], "official")
        self.assertEqual(row["source_id"], "official-fusion-preview")
        self.assertEqual(row["collection_kind"], "literature_collection")
        self.assertFalse(row["pdf_available"])
        self.assertEqual(row["article_title"], self.paper["title"])
        self.assertEqual(row["meaning"], "辐照注量")
        forbidden = {"paper_id", "item_id", "zotero_key", "pdf_path", "reviewer"}
        self.assertTrue(forbidden.isdisjoint(row))
        detail = repo.get_entity(row["entity_uid"])
        self.assertEqual(detail["collection_kind"], "literature_collection")
        self.assertFalse(detail["pdf_available"])

    def test_official_visual_asset_opens_by_public_entity_identity(self) -> None:
        entity = json.loads(json.dumps(self.entity))
        entity["entity_type"] = "table"
        entity["identity_key"] = "stable-source-table-1"
        entity["source_kind"] = "table"
        entity["payload"] = {
            "display_name": "Table 1",
            "label": "Table 1",
            "context_explanation": "Audited source table screenshot.",
            "page_start": 3,
        }
        entity_uid = stable_entity_uid(
            self.paper_uid, "table", entity["identity_key"]
        )
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d49444154789c6360000000020001e221bc330000000049454e44ae426082"
        )
        source = self.root / "table.png"
        source.write_bytes(png)
        digest = hashlib.sha256(png).hexdigest()
        policy = ReleasePolicy(
            distribution_scope="internal-preview-only",
            allowed_paper_uids=frozenset({self.paper_uid}),
            allow_structured_evidence=True,
            allow_short_excerpts=True,
            maximum_excerpt_chars=1000,
            maximum_excerpt_chars_per_paper=4000,
            maximum_excerpt_chars_total=8000,
            binary_asset_allowlist={
                entity_uid: repository_module.BinaryAssetPermission(
                    digest, "image/png"
                )
            },
            accepted_dropped_by_reason={"manual_review": 2},
        )
        output = materialize_portable_repository(
            self.plan(entity=entity),
            self.root / "visual-lease",
            package_id="official-fusion-preview",
            package_version="0.1.0-preview.1",
            release_policy=policy,
            provenance=provenance_for_papers(
                (self.paper,), publisher="Auto Research internal preview"
            ),
            binary_assets={entity_uid: source},
        )
        repository = OfficialEvidenceRepository.open(output.root)
        lease = repository.open_entity_asset(entity_uid)
        self.assertIsNotNone(lease)
        with lease:
            self.assertEqual(lease.read(), png)
            metadata = lease.public_metadata()
        self.assertEqual(metadata["source_scope"], "official")
        self.assertEqual(metadata["source_id"], "official-fusion-preview")
        self.assertEqual(metadata["entity_uid"], entity_uid)
        self.assertEqual(metadata["media_type"], "image/png")
        self.assertNotIn("path", json.dumps(metadata))
        self.assertNotIn("asset_uid", metadata)
        self.assertIsNone(repository.open_entity_asset("entity_table_" + "f" * 32))

        asset_row = repository.list_entity_assets(entity_uid)[0]
        asset_path = output.root / asset_row["relative_path"]
        hardlink = self.root / "hardlinked-asset.png"
        os.link(asset_path, hardlink)
        try:
            with self.assertRaises(PortableRepositoryError) as linked:
                repository.open_entity_asset(entity_uid)
            self.assertEqual(linked.exception.code, "asset_missing")
        finally:
            hardlink.unlink()
        stable_lease = repository.open_entity_asset(entity_uid)
        asset_path.write_bytes(b"x" * len(png))
        with stable_lease:
            self.assertEqual(stable_lease.read(), png)
        with self.assertRaises(PortableRepositoryError) as changed:
            repository.open_entity_asset(entity_uid)
        self.assertIn(changed.exception.code, {"asset_media", "asset_checksum"})

    def test_same_public_input_produces_same_database_bytes(self) -> None:
        first = self.build("first")
        second = self.build("second")
        self.assertEqual(first.content_fingerprint, second.content_fingerprint)
        self.assertEqual(first.database_sha256, second.database_sha256)
        self.assertEqual(first.database_path.read_bytes(), second.database_path.read_bytes())

    def test_ambiguous_secondary_alias_is_omitted_without_dropping_records(self) -> None:
        shared_alias = "metadata:historical duplicate label"
        first = dict(self.paper, identity_aliases=(shared_alias,))
        second = {
            **self.paper,
            "doi": "10.1000/test.2",
            "title": "A second controlled irradiation experiment",
            "identity_aliases": (shared_alias,),
        }
        first_uid = stable_paper_uid(
            doi=first["doi"],
            title=first["title"],
            year=first["year"],
            first_author=first["first_author"],
        )
        second_uid = stable_paper_uid(
            doi=second["doi"],
            title=second["title"],
            year=second["year"],
            first_author=second["first_author"],
        )
        output = materialize_portable_repository(
            PortableExportPlan(papers=(first, second), entities=()),
            self.root / "ambiguous-alias",
            package_id="official-fusion-preview",
            package_version="0.1.0",
            release_policy=ReleasePolicy(
                distribution_scope="internal-preview-only",
                allowed_paper_uids=frozenset({first_uid, second_uid}),
                allow_structured_evidence=True,
            ),
            provenance=provenance_for_papers(
                (first, second), publisher="Auto Research internal preview"
            ),
        )
        connection = sqlite3.connect(
            f"{output.database_path.as_uri()}?mode=ro&immutable=1", uri=True
        )
        try:
            papers = connection.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
            ambiguous = connection.execute(
                "SELECT COUNT(*) FROM identity_aliases WHERE object_kind='paper' AND alias_hash=?",
                (repository_module._alias_hash("paper", shared_alias),),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(papers, 2)
        self.assertEqual(ambiguous, 0)

    def test_unexplained_sanitizer_drops_block_release(self) -> None:
        policy = ReleasePolicy(
            distribution_scope="internal-preview-only",
            allowed_paper_uids=frozenset({self.paper_uid}),
            allow_structured_evidence=True,
            allow_short_excerpts=True,
            maximum_excerpt_chars=1000,
            maximum_excerpt_chars_per_paper=4000,
            maximum_excerpt_chars_total=8000,
        )
        with self.assertRaises(PortableRepositoryError) as raised:
            materialize_portable_repository(
                self.plan(),
                self.root / "unexplained-drops",
                package_id="official-fusion-preview",
                package_version="0.1.0",
                release_policy=policy,
                provenance=provenance_for_papers(
                    (self.paper,), publisher="Auto Research internal preview"
                ),
            )
        self.assertEqual(raised.exception.code, "release_gate")

    def test_structured_evidence_and_excerpts_are_default_deny(self) -> None:
        denied = ReleasePolicy(
            distribution_scope="internal-preview-only",
            allowed_paper_uids=frozenset({self.paper_uid}),
            accepted_dropped_by_reason={"manual_review": 2},
        )
        with self.assertRaises(PortableRepositoryError) as raised:
            materialize_portable_repository(
                self.plan(),
                self.root / "denied",
                package_id="official-fusion-preview",
                package_version="0.1.0",
                release_policy=denied,
                provenance=provenance_for_papers(
                    (self.paper,), publisher="Auto Research internal preview"
                ),
            )
        self.assertEqual(raised.exception.code, "rights_scope")

        without_excerpt_permission = self.policy(excerpts=False)
        with self.assertRaises(PortableRepositoryError) as raised:
            materialize_portable_repository(
                self.plan(),
                self.root / "denied-excerpt",
                package_id="official-fusion-preview",
                package_version="0.1.0",
                release_policy=without_excerpt_permission,
                provenance=provenance_for_papers(
                    (self.paper,), publisher="Auto Research internal preview"
                ),
            )
        self.assertEqual(raised.exception.code, "rights_scope")

    def test_strict_allowlists_reject_paths_secrets_and_unknown_fields(self) -> None:
        for dangerous in (
            "evidence at /Users/alice/private/paper.pdf",
            r"copied from C:\\Users\\alice\\paper.pdf",
            r"copied from \\\\server\\share\\paper.pdf",
            "token sk-example12345678901234567890",
            "open http://127.0.0.1:8765/private",
        ):
            entity = json.loads(json.dumps(self.entity))
            entity["payload"]["context_explanation"] = dangerous
            with self.assertRaises(PortableRepositoryError):
                materialize_portable_repository(
                    self.plan(entity=entity),
                    self.root / ("unsafe-" + str(abs(hash(dangerous)))),
                    package_id="official-fusion-preview",
                    package_version="0.1.0",
                    release_policy=self.policy(),
                    provenance=provenance_for_papers(
                        (self.paper,), publisher="Auto Research internal preview"
                    ),
                )

        entity = json.loads(json.dumps(self.entity))
        entity["payload"]["review_note"] = "internal"
        with self.assertRaises(PortableRepositoryError) as raised:
            materialize_portable_repository(
                self.plan(entity=entity),
                self.root / "unknown-field",
                package_id="official-fusion-preview",
                package_version="0.1.0",
                release_policy=self.policy(),
                provenance=provenance_for_papers(
                    (self.paper,), publisher="Auto Research internal preview"
                ),
            )
        self.assertEqual(raised.exception.code, "public_fields")

    def test_public_doi_url_is_allowed_but_other_urls_remain_rejected(self) -> None:
        entity = json.loads(json.dumps(self.entity))
        entity["payload"]["context_explanation"] = (
            "The reported condition is discussed at https://doi.org/10.1000/test.1"
        )
        output = materialize_portable_repository(
            self.plan(entity=entity),
            self.root / "doi-url",
            package_id="official-fusion-preview",
            package_version="0.1.0",
            release_policy=self.policy(),
            provenance=provenance_for_papers(
                (self.paper,), publisher="Auto Research internal preview"
            ),
        )
        self.assertEqual(output.entity_count, 1)

        for unsafe_url in (
            "http://doi.org/10.1000/test.1",
            "https://doi.org.evil.example/10.1000/test.1",
            "https://doi.org:443/10.1000/test.1",
            "https://doi.org/10.1000/test.1?redirect=https://evil.example",
            "https://example.org/10.1000/test.1",
        ):
            unsafe = json.loads(json.dumps(self.entity))
            unsafe["payload"]["context_explanation"] = unsafe_url
            with self.assertRaises(PortableRepositoryError) as raised:
                materialize_portable_repository(
                    self.plan(entity=unsafe),
                    self.root / ("unsafe-url-" + str(abs(hash(unsafe_url)))),
                    package_id="official-fusion-preview",
                    package_version="0.1.0",
                    release_policy=self.policy(),
                    provenance=provenance_for_papers(
                        (self.paper,), publisher="Auto Research internal preview"
                    ),
                )
            self.assertEqual(raised.exception.code, "unsafe_value")

    def test_jpeg_dimensions_are_checked_before_binary_distribution(self) -> None:
        safe = self.root / "safe.jpg"
        safe.write_bytes(
            b"\xff\xd8"
            b"\xff\xc0\x00\x11\x08\x00\x64\x00\xc8\x03"
            b"\x01\x11\x00\x02\x11\x00\x03\x11\x00"
            b"\xff\xd9"
        )
        self.assertEqual(repository_module._validate_image_header(safe, "image/jpeg"), (200, 100))

        oversized = self.root / "oversized.jpg"
        oversized.write_bytes(
            b"\xff\xd8"
            b"\xff\xc0\x00\x11\x08\x9c\x40\x9c\x40\x03"
            b"\x01\x11\x00\x02\x11\x00\x03\x11\x00"
            b"\xff\xd9"
        )
        with self.assertRaises(PortableRepositoryError) as raised:
            repository_module._validate_image_header(oversized, "image/jpeg")
        self.assertEqual(raised.exception.code, "asset_media")

        malformed = self.root / "malformed.jpg"
        malformed.write_bytes(b"\xff\xd8\xff\xd9")
        with self.assertRaises(PortableRepositoryError) as raised:
            repository_module._validate_image_header(malformed, "image/jpeg")
        self.assertEqual(raised.exception.code, "asset_media")

    def test_non_publishable_or_ambiguous_entity_is_rejected(self) -> None:
        for field, value, code in (
            ("quality_gate_status", "manual_review", "quality_gate"),
            ("review_action", "ambiguous", "review_action"),
        ):
            entity = dict(self.entity, **{field: value})
            with self.assertRaises(PortableRepositoryError) as raised:
                materialize_portable_repository(
                    self.plan(entity=entity),
                    self.root / f"invalid-{field}",
                    package_id="official-fusion-preview",
                    package_version="0.1.0",
                    release_policy=self.policy(),
                    provenance=provenance_for_papers(
                        (self.paper,), publisher="Auto Research internal preview"
                    ),
                )
            self.assertEqual(raised.exception.code, code)

    def test_audit_rejects_extra_files_and_tampered_metadata(self) -> None:
        output = self.build()
        extra = output.root / "assets" / "nested" / "hidden.txt"
        extra.parent.mkdir(parents=True)
        extra.write_text("not registered", encoding="utf-8")
        with self.assertRaises(PortableRepositoryError) as raised:
            audit_portable_repository(output.root)
        self.assertEqual(raised.exception.code, "audit_tree")

    def test_provenance_must_cover_exact_exported_papers(self) -> None:
        invalid = provenance_for_papers((self.paper,), publisher="Auto Research internal preview")
        invalid["sources"] = []
        with self.assertRaises(PortableRepositoryError) as raised:
            materialize_portable_repository(
                self.plan(),
                self.root / "missing-provenance",
                package_id="official-fusion-preview",
                package_version="0.1.0",
                release_policy=self.policy(),
                provenance=invalid,
            )
        self.assertEqual(raised.exception.code, "provenance")


if __name__ == "__main__":
    unittest.main()
