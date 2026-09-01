from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from auto_research.evidence.db import EvidenceDB, now
from auto_research.evidence.workspace_official_table_link import (
    WorkspaceOfficialTableLinkError,
    WorkspaceOfficialTableLinkService,
)


ENTITY_UID = "entity_table_" + "2" * 32
SOURCE_ID = "official-main"
BBOX = [8.0, 8.0, 92.0, 52.0]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _Repository:
    package_id = SOURCE_ID

    def __init__(self, *, pdf_sha: str, image_sha: str) -> None:
        self.pdf_sha = pdf_sha
        self.image_sha = image_sha
        self.documents = [
            {
                "entity_type": "table",
                "entity_uid": ENTITY_UID,
                "paper_uid": "paper-1",
                "doi": "10.1000/exact-table",
                "source_page": 2,
                "table_bbox": list(BBOX),
            }
        ]

    def iter_search_documents(self, *, entity_types):
        assert entity_types == {"table"}
        return iter(self.documents)

    def get_pdf_identity(self, paper_uid: str):
        assert paper_uid == "paper-1"
        return {"sha256": self.pdf_sha}

    def list_entity_assets(self, entity_uid: str):
        assert entity_uid == ENTITY_UID
        return [{"sha256": self.image_sha}]


class _Packages:
    def __init__(self, repository: _Repository | None) -> None:
        self.repository = repository

    def active_repository(self):
        if self.repository is None:
            return None
        return SimpleNamespace(package_id=SOURCE_ID), self.repository


class _Structures:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def get(self, entity_uid: str, **kwargs):
        self.calls.append({"entity_uid": entity_uid, **kwargs})
        return {
            "schema_version": "official-table-structure-version-v1",
            "source_scope": "official",
            "source_id": SOURCE_ID,
            "entity_uid": ENTITY_UID,
            "entity_type": "table",
            "version": 2,
            "status": "verified",
            "reason_codes": ["human_verified"],
            "rows": [["Material", "Hardness"], ["316H", "4.63 GPa"]],
            "cells": [{"secret_path": "/private/source.png"}],
            "content_fingerprint": "f" * 64,
        }


def _setup(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    pdf = tmp_path / "paper.pdf"
    image = tmp_path / "table.png"
    pdf.write_bytes(b"%PDF-1.7\nexact-source\n%%EOF")
    image.write_bytes(b"exact-table-image")
    database = EvidenceDB(tmp_path / "evidence.sqlite")
    database.init()
    paper_id = database.upsert_paper(
        title="Exact table paper",
        doi="https://doi.org/10.1000/exact-table",
        pdf_path=str(pdf),
        pdf_sha256=_sha256(pdf),
        authenticity_status="verified_pdf",
    )
    with database.connect() as connection:
        inserted = connection.execute(
            """INSERT INTO visual_assets(
               paper_id,asset_type,label,asset_number,caption,page_start,page_end,
               bbox_json,image_path,image_sha256,review_status,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                paper_id,
                "table",
                "Table 1",
                1,
                "Exact table",
                2,
                2,
                json.dumps(BBOX),
                str(image),
                _sha256(image),
                "verified",
                now(),
                now(),
            ),
        )
        asset_id = int(inserted.lastrowid)
    repository = _Repository(pdf_sha=_sha256(pdf), image_sha=_sha256(image))
    structures = _Structures()
    service = WorkspaceOfficialTableLinkService(
        database,
        _Packages(repository),
        structures,
    )
    return service, repository, structures, str(asset_id), pdf, image


def test_exact_source_projects_verified_official_grid_without_workspace_write(
    tmp_path: Path,
) -> None:
    service, _repository, structures, entity_uid, _pdf, _image = _setup(tmp_path)
    result = service.get(entity_uid)
    assert result["schema_version"] == "workspace-linked-official-table-structure-v1"
    assert result["linked_workspace_entity_uid"] == entity_uid
    assert result["match_basis"] == [
        "doi",
        "pdf_sha256",
        "source_page",
        "visual_asset_sha256",
    ]
    assert result["structure"]["status"] == "verified"
    assert result["structure"]["rows"][1] == ["316H", "4.63 GPa"]
    assert structures.calls == [
        {
            "entity_uid": ENTITY_UID,
            "source_id": SOURCE_ID,
            "include_unverified": False,
        }
    ]
    serialized = json.dumps(result, ensure_ascii=False).casefold()
    assert str(tmp_path).casefold() not in serialized
    assert "pdf_path" not in serialized
    assert "image_path" not in serialized
    assert "secret_path" not in serialized
    assert "content_fingerprint" not in serialized


@pytest.mark.parametrize(
    "mutation",
    ("doi", "pdf_sha", "page", "image_sha", "no_package"),
)
def test_any_identity_mismatch_fails_closed(tmp_path: Path, mutation: str) -> None:
    service, repository, _structures, entity_uid, _pdf, _image = _setup(tmp_path)
    if mutation == "doi":
        repository.documents[0]["doi"] = "10.1000/other"
    elif mutation == "pdf_sha":
        repository.pdf_sha = "a" * 64
    elif mutation == "page":
        repository.documents[0]["source_page"] = 3
    elif mutation == "image_sha":
        repository.image_sha = "b" * 64
    else:
        service._packages = _Packages(None)  # type: ignore[attr-defined]
    with pytest.raises(WorkspaceOfficialTableLinkError) as missing:
        service.get(entity_uid)
    assert missing.value.code == "workspace_official_table_link_not_found"


def test_changed_workspace_bytes_and_ambiguous_match_are_rejected(tmp_path: Path) -> None:
    service, repository, _structures, entity_uid, _pdf, image = _setup(tmp_path)
    image.write_bytes(b"changed-after-index")
    with pytest.raises(WorkspaceOfficialTableLinkError) as changed:
        service.get(entity_uid)
    assert changed.value.code == "workspace_official_table_link_source_changed"

    service, repository, _structures, entity_uid, _pdf, _image = _setup(
        tmp_path / "second"
    )
    repository.documents.append(dict(repository.documents[0]))
    with pytest.raises(WorkspaceOfficialTableLinkError) as ambiguous:
        service.get(entity_uid)
    assert ambiguous.value.code == "workspace_official_table_link_corrupt"


@pytest.mark.parametrize("entity_uid", ("", "0", "01", "-1", "1.0", "/tmp/1"))
def test_workspace_identity_is_strict_and_path_free(
    tmp_path: Path,
    entity_uid: str,
) -> None:
    service, _repository, _structures, _valid, _pdf, _image = _setup(tmp_path)
    with pytest.raises(WorkspaceOfficialTableLinkError) as invalid:
        service.get(entity_uid)
    assert invalid.value.code == "workspace_official_table_link_invalid"
    assert "/" not in invalid.value.safe_message
