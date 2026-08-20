from __future__ import annotations

import csv
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from auto_research.evidence.db import EvidenceDB
from auto_research.evidence.evidence_export import (
    EXPORT_FIELDS,
    EvidenceExportError,
    EvidenceExportService,
    WorkspaceEvidenceProjectionResolver,
)


class _Resolver:
    def __init__(self, documents=None, error: Exception | None = None) -> None:
        self.documents = documents or {}
        self.error = error
        self.calls: list[dict[str, str]] = []

    def get(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.error is not None:
            raise self.error
        key = (
            kwargs["source_scope"],
            kwargs["source_id"],
            kwargs["entity_type"],
            kwargs["entity_uid"],
        )
        if key not in self.documents:
            raise KeyError("private implementation detail")
        return self.documents[key]


def _workspace_document(entity_type: str, entity_id: int) -> dict[str, object]:
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "paper_id": 91,
        "display_name": f"{entity_type} title",
        "caption": "caption",
        "article_title": "article",
        "source_page": 7,
        "source_excerpt": "source evidence",
        "variables": {"x": "temperature", "y": "hardness"},
    }


def _federated_document(scope: str, source_id: str, entity_type: str, uid: str):
    return {
        "schema_version": "evidence-search-document-v1",
        "source_scope": scope,
        "source_id": source_id,
        "entity_type": entity_type,
        "entity_uid": uid,
        "display_title": f"{entity_type} title",
        "meaning_text": "meaning",
        "context_text": "context",
        "source_excerpt": "source evidence",
        "conditions": {"temperature": "300 K"},
        "tags": ["hardness", "irradiation"],
    }


class EvidenceExportTests(unittest.TestCase):
    def setUp(self) -> None:
        workspace_documents = {}
        federated_documents = {}
        for position, entity_type in enumerate(
            ("item", "finding", "table", "figure"), start=1
        ):
            workspace_documents[
                ("workspace", "workspace", entity_type, str(position))
            ] = _workspace_document(entity_type, position)
            federated_documents[
                ("official", "official-v1", entity_type, f"official-{entity_type}")
            ] = _federated_document(
                "official", "official-v1", entity_type, f"official-{entity_type}"
            )
        self.workspace = _Resolver(workspace_documents)
        self.federated = _Resolver(federated_documents)
        self.service = EvidenceExportService(
            workspace_resolver=self.workspace,
            federated_resolver=self.federated,
        )

    def test_all_four_types_resolve_by_authoritative_identity(self) -> None:
        for position, entity_type in enumerate(
            ("item", "finding", "table", "figure"), start=1
        ):
            with self.subTest(scope="workspace", entity_type=entity_type):
                artifact = self.service.export(
                    source_scope="workspace",
                    source_id="workspace",
                    entity_type=entity_type,
                    entity_uid=str(position),
                    format="csv",
                )
                row = next(csv.DictReader(io.StringIO(artifact.content.decode("utf-8-sig"))))
                self.assertEqual(row["source_scope"], "workspace")
                self.assertEqual(row["entity_type"], entity_type)
                self.assertEqual(row["entity_uid"], str(position))
            with self.subTest(scope="official", entity_type=entity_type):
                artifact = self.service.export(
                    source_scope="official",
                    source_id="official-v1",
                    entity_type=entity_type,
                    entity_uid=f"official-{entity_type}",
                    format="csv",
                )
                row = next(csv.DictReader(io.StringIO(artifact.content.decode("utf-8-sig"))))
                self.assertEqual(row["source_scope"], "official")
                self.assertEqual(row["entity_type"], entity_type)

    def test_csv_and_xlsx_neutralize_formulas_and_exclude_binary_fields(self) -> None:
        document = _federated_document(
            "private", "private-v1", "figure", "private-figure"
        )
        document.update(
            {
                "display_title": "=2+2",
                "caption": "+SUM(A1:A2)",
                "image_url": "/api/visual-assets/1/image",
            }
        )
        resolver = _Resolver(
            {("private", "private-v1", "figure", "private-figure"): document}
        )
        service = EvidenceExportService(
            workspace_resolver=self.workspace,
            federated_resolver=resolver,
        )
        csv_artifact = service.export(
            source_scope="private",
            source_id="private-v1",
            entity_type="figure",
            entity_uid="private-figure",
            format="csv",
        )
        self.assertTrue(csv_artifact.content.startswith(b"\xef\xbb\xbf"))
        row = next(csv.DictReader(io.StringIO(csv_artifact.content.decode("utf-8-sig"))))
        self.assertEqual(row["display_title"], "'=2+2")
        self.assertEqual(row["caption"], "'+SUM(A1:A2)")
        self.assertNotIn("image_url", row)

        xlsx_artifact = service.export(
            source_scope="private",
            source_id="private-v1",
            entity_type="figure",
            entity_uid="private-figure",
            format="xlsx",
        )
        self.assertTrue(xlsx_artifact.content.startswith(b"PK"))
        with zipfile.ZipFile(io.BytesIO(xlsx_artifact.content)) as workbook:
            worksheet = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("'=2+2", worksheet)
        self.assertIn("'+SUM(A1:A2)", worksheet)
        self.assertNotIn("image_url", worksheet)
        self.assertEqual(tuple(row), EXPORT_FIELDS)

    def test_invalid_identity_missing_document_and_resolver_failure_are_safe(self) -> None:
        invalid_requests = (
            {"source_scope": "workspace", "source_id": "official-v1", "entity_type": "item", "entity_uid": "1", "format": "csv"},
            {"source_scope": "workspace", "source_id": "workspace", "entity_type": "other", "entity_uid": "1", "format": "csv"},
            {"source_scope": "workspace", "source_id": "workspace", "entity_type": "item", "entity_uid": "01", "format": "csv"},
            {"source_scope": "official", "source_id": "/Users/name/db", "entity_type": "item", "entity_uid": "one", "format": "csv"},
        )
        for request in invalid_requests:
            with self.subTest(request=request):
                with self.assertRaises(EvidenceExportError) as raised:
                    self.service.export(**request)
                self.assertEqual(raised.exception.code, "evidence_export_invalid")

        with self.assertRaises(EvidenceExportError) as missing:
            self.service.export(
                source_scope="private",
                source_id="private-v1",
                entity_type="item",
                entity_uid="missing",
                format="csv",
            )
        self.assertEqual(missing.exception.code, "evidence_export_not_found")

        failed = EvidenceExportService(
            workspace_resolver=self.workspace,
            federated_resolver=_Resolver(error=RuntimeError("/tmp/secret.sqlite")),
        )
        with self.assertRaises(EvidenceExportError) as unavailable:
            failed.export(
                source_scope="official",
                source_id="official-v1",
                entity_type="item",
                entity_uid="official-item",
                format="csv",
            )
        public = unavailable.exception.public_dict()
        self.assertEqual(public["code"], "evidence_export_unavailable")
        self.assertNotIn("tmp", json.dumps(public))

    def test_private_fields_and_embedded_local_paths_fail_closed(self) -> None:
        unsafe = _federated_document("official", "official-v1", "item", "unsafe")
        unsafe["source_excerpt"] = "saved at /home/user/result.json"
        unsafe["file_id"] = "internal"
        service = EvidenceExportService(
            workspace_resolver=self.workspace,
            federated_resolver=_Resolver(
                {("official", "official-v1", "item", "unsafe"): unsafe}
            ),
        )
        with self.assertRaises(EvidenceExportError) as raised:
            service.export(
                source_scope="official",
                source_id="official-v1",
                entity_type="item",
                entity_uid="unsafe",
                format="csv",
            )
        self.assertEqual(raised.exception.code, "evidence_export_unavailable")

    def test_workspace_projection_resolver_is_select_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workspace.sqlite"
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    "CREATE TABLE search_index_documents ("
                    "entity_type TEXT NOT NULL, entity_id INTEGER NOT NULL, "
                    "payload_json TEXT NOT NULL, PRIMARY KEY(entity_type, entity_id))"
                )
                connection.execute(
                    "INSERT INTO search_index_documents VALUES (?,?,?)",
                    ("item", 7, json.dumps(_workspace_document("item", 7))),
                )
                connection.commit()
            finally:
                connection.close()
            before = path.read_bytes()
            resolver = WorkspaceEvidenceProjectionResolver(EvidenceDB(path))
            result = resolver.get(
                source_scope="workspace",
                source_id="workspace",
                entity_type="item",
                entity_uid="7",
            )
            self.assertEqual(result["entity_id"], 7)
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
