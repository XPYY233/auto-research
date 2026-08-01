from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from auto_research.evidence.fact_model import cluster_fact_rows, cluster_qualitative_rows
from auto_research.evidence.six_column import is_reportable_value_text

from .portable_repository import (
    PUBLISHABLE_QUALITY,
    PortableExportPlan,
    PortableRepositoryError,
    _normalise_entity,
    stable_paper_uid,
)


REQUIRED_TABLES = frozenset(
    {
        "papers",
        "data_items",
        "data_versions",
        "visual_assets",
        "visual_asset_reviews",
        "quality_candidates",
        "data_item_visual_links",
    }
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open_snapshot(path: Path) -> sqlite3.Connection:
    raw = path.expanduser()
    if raw.is_symlink() or not raw.is_file():
        raise PortableRepositoryError("source_snapshot", "源证据库必须是普通 SQLite 快照")
    resolved = raw.resolve()
    if any(Path(str(resolved) + suffix).exists() for suffix in ("-wal", "-shm")):
        raise PortableRepositoryError("source_snapshot", "源证据库仍存在 WAL/SHM，不能作为静态快照")
    try:
        connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro&immutable=1", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise PortableRepositoryError("source_snapshot", "源证据库完整性检查失败")
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if not REQUIRED_TABLES.issubset(tables):
            raise PortableRepositoryError("source_schema", "源证据库缺少 v12 公开投影所需表")
        view = connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='view' AND name='v_current_six_column_data'"
        ).fetchone()
        if view is None:
            raise PortableRepositoryError("source_schema", "源证据库缺少当前六列视图")
        return connection
    except PortableRepositoryError:
        try:
            connection.close()
        except (UnboundLocalError, sqlite3.Error):
            pass
        raise
    except sqlite3.DatabaseError as exc:
        try:
            connection.close()
        except (UnboundLocalError, sqlite3.Error):
            pass
        raise PortableRepositoryError("source_snapshot", "无法只读打开源证据库") from exc


def _json_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def _json_dict(value: Any) -> dict[str, str]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(key).strip(): str(item).strip()
        for key, item in parsed.items()
        if str(key).strip() and str(item).strip()
    }


def _paper_rows(connection: sqlite3.Connection) -> tuple[list[dict[str, Any]], dict[int, str]]:
    papers: list[dict[str, Any]] = []
    local_to_uid: dict[int, str] = {}
    for row in connection.execute(
        """SELECT id,doi,title,year,first_author,corresponding_author,material_focus
           FROM papers ORDER BY title,year,doi,id"""
    ):
        source = dict(row)
        uid = stable_paper_uid(
            doi=source.get("doi"),
            title=source.get("title"),
            year=source.get("year"),
            first_author=source.get("first_author"),
        )
        papers.append(
            {
                "paper_uid": uid,
                "doi": source.get("doi") or "",
                "title": source.get("title") or "",
                "year": source.get("year"),
                "first_author": source.get("first_author") or "",
                "corresponding_author": source.get("corresponding_author") or "",
                "material_focus": source.get("material_focus") or "",
                "identity_aliases": (
                    f"metadata:{source.get('title') or ''}|{source.get('year') or ''}|"
                    f"{source.get('first_author') or ''}",
                ),
            }
        )
        local_to_uid[int(source["id"])] = uid
    return papers, local_to_uid


def _item_quality(connection: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    rows = connection.execute(
        """SELECT q.published_item_id,q.gate_status,q.overall_score
           FROM quality_candidates q
           WHERE q.published_item_id IS NOT NULL
             AND q.id=(SELECT q2.id FROM quality_candidates q2
                       WHERE q2.published_item_id=q.published_item_id
                       ORDER BY q2.id DESC LIMIT 1)"""
    )
    return {
        int(row["published_item_id"]): {
            "quality_gate_status": str(row["gate_status"] or ""),
            "quality_score": float(row["overall_score"] or 0.0),
        }
        for row in rows
    }


def _item_source_kinds(connection: sqlite3.Connection) -> dict[int, str]:
    links: dict[int, list[tuple[str, str]]] = {}
    for row in connection.execute(
        """SELECT l.item_id,l.relation_kind,a.asset_type
           FROM data_item_visual_links l
           JOIN visual_assets a ON a.id=l.asset_id
           ORDER BY l.item_id,CASE l.relation_kind WHEN 'primary' THEN 0 ELSE 1 END,a.asset_number"""
    ):
        links.setdefault(int(row["item_id"]), []).append(
            (str(row["relation_kind"]), str(row["asset_type"]))
        )
    output: dict[int, str] = {}
    for item_id, values in links.items():
        primary = next((kind for relation, kind in values if relation == "primary"), "")
        if primary in {"table", "figure"}:
            output[item_id] = primary
        elif any(kind == "figure" for _, kind in values):
            output[item_id] = "text_with_figure"
        elif any(kind == "table" for _, kind in values):
            output[item_id] = "table"
    return output


def _current_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    quality = _item_quality(connection)
    source_kinds = _item_source_kinds(connection)
    rows = [
        dict(row)
        for row in connection.execute(
            """SELECT item_id,paper_id,stable_key,origin_type,version_no,value_text,meaning,unit,
                      article_title,doi,context_explanation,source_page,source_locator,source_excerpt,
                      review_action,original_value_text,original_meaning,original_unit,
                      original_context_explanation,original_source_page,original_source_locator,
                      original_source_excerpt
               FROM v_current_six_column_data ORDER BY item_id"""
        )
    ]
    output: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("review_action") or "automatic") in {"rejected", "ambiguous"}:
            continue
        item_id = int(row["item_id"])
        row.update(quality.get(item_id, {}))
        if not row.get("quality_gate_status"):
            row["quality_gate_status"] = (
                "manual_approved" if row.get("origin_type") == "manual" else "legacy_stable"
            )
        if row["quality_gate_status"] not in PUBLISHABLE_QUALITY:
            continue
        row["source_kind"] = (
            "manual"
            if row.get("origin_type") == "manual"
            else source_kinds.get(item_id, "text")
        )
        output.append(row)
    return output


def _public_occurrences(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        occurrence = {
            "source_page": raw.get("page"),
            "source_locator": str(raw.get("locator") or ""),
            "source_excerpt": str(raw.get("excerpt") or ""),
        }
        output.append({key: item for key, item in occurrence.items() if item not in (None, "")})
    return output


def _cluster_entities(
    rows: list[dict[str, Any]], local_to_uid: Mapping[int, str]
) -> list[dict[str, Any]]:
    stable_keys = {int(row["item_id"]): str(row["stable_key"]) for row in rows}
    reportable = [row for row in rows if is_reportable_value_text(row.get("value_text"))]
    nonreportable = [row for row in rows if not is_reportable_value_text(row.get("value_text"))]
    facts = cluster_fact_rows(reportable)
    findings = cluster_qualitative_rows(nonreportable)
    entities: list[dict[str, Any]] = []
    for entity_type, records in (("item", facts), ("finding", findings)):
        for row in records:
            action = str(row.get("review_action") or "automatic")
            quality = str(row.get("quality_gate_status") or "legacy_stable")
            if action in {"rejected", "ambiguous"} or quality not in PUBLISHABLE_QUALITY:
                continue
            member_key = "fact_member_ids" if entity_type == "item" else "finding_member_ids"
            member_ids = [int(value) for value in row.get(member_key) or [row["item_id"]]]
            aliases = sorted({stable_keys[item_id] for item_id in member_ids if item_id in stable_keys})
            if not aliases:
                continue
            occurrences = _public_occurrences(row.get("evidence_occurrences"))
            payload = {
                ("value_text" if entity_type == "item" else "finding_text"): str(
                    row.get("value_text") or row.get("finding_text") or ""
                ),
                "meaning": str(row.get("meaning") or ""),
                "context_explanation": str(row.get("context_explanation") or ""),
                "source_page": row.get("source_page"),
                "source_locator": str(row.get("source_locator") or ""),
                "source_excerpt": str(row.get("source_excerpt") or ""),
                "evidence_occurrences": occurrences,
                "evidence_count": len(occurrences),
            }
            if entity_type == "item":
                payload["unit"] = str(row.get("unit") or "")
            entities.append(
                {
                    "paper_uid": local_to_uid[int(row["paper_id"])],
                    "entity_type": entity_type,
                    "identity_key": f"stable-key:{aliases[0]}",
                    "identity_aliases": tuple(f"stable-key:{key}" for key in aliases),
                    "quality_gate_status": quality,
                    "source_kind": str(row.get("source_kind") or "text"),
                    "review_action": action,
                    "payload": payload,
                }
            )
    return entities


def _visual_quality(connection: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    rows = connection.execute(
        """SELECT q.published_asset_id,q.gate_status,q.overall_score,q.candidate_json
           FROM quality_candidates q
           WHERE q.published_asset_id IS NOT NULL
             AND q.id=(SELECT q2.id FROM quality_candidates q2
                       WHERE q2.published_asset_id=q.published_asset_id
                       ORDER BY q2.id DESC LIMIT 1)"""
    )
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        try:
            candidate = json.loads(str(row["candidate_json"] or "{}"))
        except json.JSONDecodeError:
            candidate = {}
        gate = str(row["gate_status"] or "")
        is_new = bool(candidate.get("is_new_asset")) if isinstance(candidate, dict) else False
        result[int(row["published_asset_id"])] = {
            "quality_gate_status": gate if is_new or gate in PUBLISHABLE_QUALITY else "legacy_stable",
            "quality_is_new_asset": is_new,
        }
    return result


def _visual_entities(
    connection: sqlite3.Connection, local_to_uid: Mapping[int, str]
) -> list[dict[str, Any]]:
    quality = _visual_quality(connection)
    rows = connection.execute(
        """SELECT a.id,a.paper_id,a.asset_type,a.label,a.asset_number,a.display_name,a.caption,
                  a.page_start,a.page_end,a.physical_quantities_json,a.variables_json,
                  a.materials_json,a.conditions_text,a.methods_text,a.context_explanation,
                  a.tags_json,a.source_context,a.review_status,
                  vr.review_action,vr.fields_json
           FROM visual_assets a
           LEFT JOIN visual_asset_reviews vr ON vr.asset_id=a.id
             AND vr.version_no=(SELECT MAX(vr2.version_no) FROM visual_asset_reviews vr2
                                WHERE vr2.asset_id=a.id)
           ORDER BY a.paper_id,a.asset_type,a.asset_number,a.id"""
    )
    output: list[dict[str, Any]] = []
    editable = {
        "display_name",
        "physical_quantities",
        "variables",
        "materials",
        "conditions_text",
        "methods_text",
        "context_explanation",
        "tags",
    }
    for source_row in rows:
        row = dict(source_row)
        action = str(row.get("review_action") or "automatic")
        if action in {"rejected", "ambiguous"} or str(row.get("review_status") or "") == "ambiguous":
            continue
        asset_id = int(row["id"])
        quality_row = quality.get(asset_id, {})
        quality_status = str(quality_row.get("quality_gate_status") or "legacy_stable")
        if quality_row.get("quality_is_new_asset") and quality_status not in PUBLISHABLE_QUALITY:
            continue
        if quality_status not in PUBLISHABLE_QUALITY:
            quality_status = "legacy_stable"
        payload: dict[str, Any] = {
            "label": str(row.get("label") or ""),
            "display_name": str(row.get("display_name") or row.get("label") or ""),
            "caption": str(row.get("caption") or ""),
            "page_start": row.get("page_start"),
            "page_end": row.get("page_end"),
            "physical_quantities": _json_list(row.get("physical_quantities_json")),
            "variables": _json_dict(row.get("variables_json")),
            "materials": _json_list(row.get("materials_json")),
            "conditions_text": str(row.get("conditions_text") or ""),
            "methods_text": str(row.get("methods_text") or ""),
            "context_explanation": str(row.get("context_explanation") or ""),
            "tags": _json_list(row.get("tags_json")),
            "source_context": str(row.get("source_context") or ""),
        }
        if row.get("fields_json"):
            try:
                reviewed = json.loads(str(row["fields_json"]))
            except json.JSONDecodeError:
                reviewed = {}
            if isinstance(reviewed, dict):
                for key in editable:
                    if key in reviewed:
                        payload[key] = reviewed[key]
        logical = f"visual:{row['asset_type']}:{row['label']}:{int(row['asset_number'])}"
        output.append(
            {
                "paper_uid": local_to_uid[int(row["paper_id"])],
                "entity_type": str(row["asset_type"]),
                "identity_key": logical,
                "identity_aliases": (logical,),
                "quality_gate_status": quality_status,
                "source_kind": str(row["asset_type"]),
                "review_action": action if action in {"confirmation", "correction"} else "automatic",
                "payload": payload,
            }
        )
    return output


def plan_evidence_v12_export(snapshot: Path | str) -> PortableExportPlan:
    """Create a sanitized four-type plan from an audited immutable v12 snapshot.

    The snapshot hash is returned only in the private build report. It is never
    copied into the distribution database, rights document or provenance file.
    """

    source_path = Path(snapshot).expanduser()
    before = _sha256_file(source_path)
    connection = _open_snapshot(source_path)
    dropped: Counter[str] = Counter()
    try:
        papers, local_to_uid = _paper_rows(connection)
        rows = _current_rows(connection)
        candidates = _cluster_entities(rows, local_to_uid)
        candidates.extend(_visual_entities(connection, local_to_uid))
    finally:
        connection.close()
    after = _sha256_file(source_path)
    if before != after:
        raise PortableRepositoryError("source_changed", "源证据库在生成公开计划时发生变化")

    entities: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            normalised, _ = _normalise_entity(candidate)
        except PortableRepositoryError as exc:
            dropped[f"sanitizer:{exc.code}"] += 1
            continue
        uid = str(normalised["entity_uid"])
        if uid in seen:
            dropped["identity_collision"] += 1
            continue
        seen.add(uid)
        entities.append(candidate)
    return PortableExportPlan(
        papers=tuple(papers),
        entities=tuple(entities),
        dropped_by_reason=dict(sorted(dropped.items())),
        private_source_sha256=before,
    )
