from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from auto_research.evidence.db import EvidenceDB, now
from auto_research.evidence.search_index import EvidenceSearchIndex
from auto_research.evidence.workspace_evidence_resolver import (
    WorkspaceEvidenceResolverError,
    WorkspacePublicEvidenceResolver,
)
from auto_research.evidence.workspace_official_table_link import (
    WorkspaceOfficialTableLinkService,
)
from auto_research.evidence.workspace_public_identity import workspace_public_identity


OFFICIAL_SOURCE = "official-main"
OFFICIAL_TABLE_UID = "entity_table_" + "2" * 32


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _OfficialRepository:
    package_id = OFFICIAL_SOURCE

    def __init__(self, *, pdf_sha: str, image_sha: str) -> None:
        self.pdf_sha = pdf_sha
        self.image_sha = image_sha
        self.documents = [
            {
                "entity_type": "table",
                "entity_uid": OFFICIAL_TABLE_UID,
                "paper_uid": "paper_official_table",
                "doi": "10.1000/workspace-table",
                "source_page": 3,
            }
        ]

    def iter_search_documents(self, *, entity_types):
        assert entity_types == {"table"}
        return iter(self.documents)

    def get_pdf_identity(self, paper_uid: str):
        assert paper_uid == "paper_official_table"
        return {"sha256": self.pdf_sha}

    def list_entity_assets(self, entity_uid: str):
        assert entity_uid == OFFICIAL_TABLE_UID
        return [{"sha256": self.image_sha}]


class _Packages:
    def __init__(self, repository: _OfficialRepository) -> None:
        self.repository = repository

    def active_repository(self):
        return SimpleNamespace(package_id=OFFICIAL_SOURCE), self.repository


class _OfficialStructures:
    def get(self, entity_uid: str, **kwargs):
        assert entity_uid == OFFICIAL_TABLE_UID
        assert kwargs == {
            "source_id": OFFICIAL_SOURCE,
            "include_unverified": False,
        }
        return {
            "schema_version": "official-table-structure-version-v1",
            "source_scope": "official",
            "source_id": OFFICIAL_SOURCE,
            "entity_uid": OFFICIAL_TABLE_UID,
            "entity_type": "table",
            "version": 4,
            "status": "verified",
            "reason_codes": ["human_verified"],
            "rows": [
                ["Temperature", "Hardness"],
                ["300 K", "4.63 GPa"],
            ],
            "content_fingerprint": "f" * 64,
            "reviewer": "must-not-leak",
        }


class _PendingWorkspaceStructures:
    def get(self, *_args, **_kwargs):
        error = RuntimeError("pending")
        error.code = "table_structure_service_pending"  # type: ignore[attr-defined]
        raise error


def _workspace(tmp_path: Path):
    pdf_bytes = b"%PDF-1.7\nworkspace-table\n%%EOF\n"
    image_bytes = b"\x89PNG\r\n\x1a\nworkspace-table-image"
    pdf_path = tmp_path / "paper.pdf"
    image_path = tmp_path / "table.png"
    pdf_path.write_bytes(pdf_bytes)
    image_path.write_bytes(image_bytes)
    database = EvidenceDB(tmp_path / "workspace.sqlite")
    database.init()
    paper_id = database.upsert_paper(
        title="Workspace Table Paper",
        doi="10.1000/workspace-table",
        year=2026,
        first_author="A. Researcher",
        pdf_path=str(pdf_path),
        pdf_sha256=_sha(pdf_bytes),
        authenticity_status="verified_pdf",
    )
    with database.connect() as connection:
        inserted = connection.execute(
            """INSERT INTO visual_assets(
               paper_id,asset_type,label,display_name,asset_number,caption,
               page_start,page_end,bbox_json,image_path,image_sha256,
               review_status,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                paper_id,
                "table",
                "Table 3",
                "Irradiation hardness",
                3,
                "Hardness after irradiation",
                3,
                3,
                json.dumps([10.0, 20.0, 200.0, 100.0]),
                str(image_path),
                _sha(image_bytes),
                "verified",
                now(),
                now(),
            ),
        )
        asset_id = int(inserted.lastrowid)
        payload = {
            "id": asset_id,
            "paper_id": paper_id,
            "entity_type": "table",
            "asset_type": "table",
            "asset_number": 3,
            "label": "Table 3",
            "display_name": "Irradiation hardness",
            "caption": "Hardness after irradiation",
            "article_title": "Workspace Table Paper",
            "doi": "10.1000/workspace-table",
            "year": 2026,
            "first_author": "A. Researcher",
            "page_start": 3,
            "page_end": 3,
            "source_page": 3,
            "source_excerpt": "Table 3 reports irradiation hardness.",
            "quality_gate_status": "manual_approved",
            "image_path": str(image_path),
            "pdf_path": str(pdf_path),
            "image_sha256": _sha(image_bytes),
            "pdf_sha256": _sha(pdf_bytes),
            "image_url": f"/api/visual-assets/{asset_id}/image",
            "pdf_url": f"/api/papers/{paper_id}/pdf#page=3",
        }
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        connection.execute(
            """INSERT INTO search_index_documents(
               entity_type,entity_id,paper_id,article_title,display_title,
               meaning_text,context_text,evidence_text,metadata_text,
               quality_gate_status,source_kind,review_action,source_page,
               payload_json,content_hash,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "table",
                asset_id,
                paper_id,
                payload["article_title"],
                payload["display_name"],
                "hardness",
                "irradiation",
                payload["source_excerpt"],
                payload["caption"],
                "manual_approved",
                "table",
                "confirmation",
                3,
                text,
                hashlib.sha256(text.encode("utf-8")).hexdigest(),
                now(),
            ),
        )
    fingerprint = EvidenceSearchIndex(database).source_fingerprint()
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO search_index_state(key,value) VALUES('source_fingerprint',?)",
            (fingerprint,),
        )
    identity = workspace_public_identity(payload)
    repository = _OfficialRepository(
        pdf_sha=_sha(pdf_bytes), image_sha=_sha(image_bytes)
    )
    linked = WorkspaceOfficialTableLinkService(
        database,
        _Packages(repository),
        _OfficialStructures(),
    )
    resolver = WorkspacePublicEvidenceResolver(
        database,
        table_structures=_PendingWorkspaceStructures(),
        linked_official_tables=linked,
    )
    request = {
        "source_scope": "workspace",
        "source_id": "workspace",
        "entity_type": "table",
        "entity_uid": identity["entity_uid"],
    }
    return database, resolver, request, asset_id, paper_id, pdf_path, image_path


def test_workspace_table_resolves_opaque_detail_and_exact_official_grid(
    tmp_path: Path,
) -> None:
    _database, resolver, request, asset_id, paper_id, _pdf, _image = _workspace(
        tmp_path
    )
    detail = resolver.get(**request)

    assert detail["schema_version"] == "workspace-evidence-detail-v1"
    assert detail["entity_uid"] == request["entity_uid"]
    assert detail["label"] == "Table 3"
    assert detail["table_structure"]["origin"] == "linked_official"
    assert detail["table_structure"]["rows"][1] == ["300 K", "4.63 GPa"]
    assert detail["image_url"].startswith(
        "/api/desktop/workspace-evidence/image?source_scope=workspace"
    )
    assert detail["pdf_url"].endswith("#page=3")
    serialized = json.dumps(detail, ensure_ascii=False).casefold()
    for forbidden in (
        str(tmp_path).casefold(),
        '"entity_id"',
        '"asset_id"',
        '"paper_id"',
        '"image_sha256"',
        '"pdf_sha256"',
        f"/visual-assets/{asset_id}/",
        f"/papers/{paper_id}/",
        "reviewer",
        "content_fingerprint",
    ):
        assert forbidden not in serialized


def test_workspace_binary_leases_use_opaque_identity_and_verified_descriptor(
    tmp_path: Path,
) -> None:
    _database, resolver, request, _asset, _paper, _pdf, _image = _workspace(tmp_path)
    image = resolver.open_image(**request)
    assert image.read().startswith(b"\x89PNG")
    image_metadata = image.public_metadata()
    image.close()
    pdf = resolver.open_pdf(**request)
    assert pdf.read().startswith(b"%PDF-")
    pdf_metadata = pdf.public_metadata()
    pdf.close()
    for metadata in (image_metadata, pdf_metadata):
        assert metadata["entity_uid"] == request["entity_uid"]
        encoded = json.dumps(metadata).casefold()
        assert "path" not in encoded
        assert "sha" not in encoded
        assert '"asset_id"' not in encoded
        assert '"paper_id"' not in encoded


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_scope": "official"},
        {"source_id": "other"},
        {"entity_type": "figure"},
        {"entity_uid": "3"},
        {"entity_uid": "entity_table_" + "z" * 32},
    ],
)
def test_malformed_scope_type_or_identity_fails_before_resolution(
    tmp_path: Path, overrides: dict[str, str]
) -> None:
    _database, resolver, request, *_rest = _workspace(tmp_path)
    request.update(overrides)
    with pytest.raises(WorkspaceEvidenceResolverError) as caught:
        resolver.get(**request)
    assert caught.value.code == "workspace_evidence_invalid"


def test_unknown_and_changed_identity_fail_closed(tmp_path: Path) -> None:
    database, resolver, request, *_rest = _workspace(tmp_path)
    unknown = dict(request, entity_uid="entity_table_" + "0" * 32)
    with pytest.raises(WorkspaceEvidenceResolverError) as missing:
        resolver.get(**unknown)
    assert missing.value.code == "workspace_evidence_not_found"

    database.upsert_paper(
        title="Changed after indexing",
        doi="10.1000/workspace-table",
        year=2026,
        first_author="A. Researcher",
    )
    with pytest.raises(WorkspaceEvidenceResolverError) as changed:
        resolver.get(**request)
    assert changed.value.code == "workspace_evidence_changed"


def test_duplicate_recomputed_identity_fails_closed(tmp_path: Path) -> None:
    database, resolver, request, asset_id, paper_id, *_rest = _workspace(tmp_path)
    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM search_index_documents WHERE entity_type='table'"
        ).fetchone()
        payload = json.loads(row["payload_json"])
        payload["id"] = asset_id + 1
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        connection.execute(
            """INSERT INTO search_index_documents(
               entity_type,entity_id,paper_id,article_title,display_title,
               meaning_text,context_text,evidence_text,metadata_text,
               quality_gate_status,source_kind,review_action,source_page,
               payload_json,content_hash,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "table",
                asset_id + 1,
                paper_id,
                row["article_title"],
                row["display_title"],
                row["meaning_text"],
                row["context_text"],
                row["evidence_text"],
                row["metadata_text"],
                row["quality_gate_status"],
                row["source_kind"],
                row["review_action"],
                row["source_page"],
                text,
                hashlib.sha256(text.encode("utf-8")).hexdigest(),
                now(),
            ),
        )
    with pytest.raises(WorkspaceEvidenceResolverError) as duplicate:
        resolver.get(**request)
    assert duplicate.value.code == "workspace_evidence_changed"


def test_changed_workspace_visual_invalidates_exact_link_and_binary_lease(
    tmp_path: Path,
) -> None:
    _database, resolver, request, *_ids, image_path = _workspace(tmp_path)
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nchanged-workspace-image")
    with pytest.raises(WorkspaceEvidenceResolverError) as changed:
        resolver.get(**request)
    assert changed.value.code == "workspace_evidence_changed"
    with pytest.raises(WorkspaceEvidenceResolverError) as binary_changed:
        resolver.open_image(**request)
    assert binary_changed.value.code == "workspace_evidence_changed"
