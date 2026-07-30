from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable

from .db import EvidenceDB, now
from .six_column import (
    ELEMENT_SEARCH_ALIASES,
    list_current_facts,
    list_qualitative_findings,
)
from .visual_evidence import list_visual_assets


ENTITY_TYPES = {"item", "table", "figure", "finding"}
QUALITY_PASSED = {"dual_pass", "third_pass", "manual_approved"}
QUALITY_FILTERS = {"all", "quality_passed", "dual_pass", "third_pass", "manual_approved", "legacy_stable"}
INDEX_FORMAT_VERSION = "3"

# This is intentionally a small query lexicon, not a second scientific data
# model. It only separates common Chinese natural-language questions into
# phrases that already occur in the evidence index.
DOMAIN_TERMS = (
    "高熵合金", "中熵合金", "难熔合金", "钨合金", "聚变堆", "辐照实验",
    "中子辐照", "离子辐照", "电子辐照", "氦离子", "氢离子", "重离子",
    "辐照温度", "辐照剂量", "辐照注量", "辐照通量", "损伤剂量",
    "纳米硬度", "显微硬度", "硬度", "拉伸强度", "屈服强度", "断裂韧性",
    "空洞尺寸", "空洞密度", "空洞", "气泡", "位错环", "位错密度",
    "晶格肿胀", "体积肿胀", "元素偏聚", "相变", "相稳定", "微观结构",
    "透射电镜", "扫描电镜", "纳米压痕", "原始表格", "论文图片",
    "随温度变化", "随剂量变化", "辐照后", "辐照前", "未观察到",
)
STOP_PHRASES = (
    "请帮我", "帮我", "请问", "哪些文章", "哪篇文章", "有哪些", "有什么",
    "如何变化", "有什么变化", "具体情况", "相关研究", "相关数据", "实验数据",
    "文章中", "论文中", "数据库中", "能否", "可以", "我想找", "查找",
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _join(row: dict[str, Any], fields: Iterable[str]) -> str:
    return " ".join(_text(row.get(field)).strip() for field in fields if _text(row.get(field)).strip())


def _alias_text(text: str) -> str:
    lowered = text.casefold()
    aliases: list[str] = []
    seen_groups: set[tuple[str, ...]] = set()
    for values in ELEMENT_SEARCH_ALIASES.values():
        group = tuple(str(value) for value in values)
        if group in seen_groups:
            continue
        seen_groups.add(group)
        matched = False
        for value in group:
            if any("\u3400" <= char <= "\u9fff" for char in value):
                matched = value in text
            elif len(value) == 1 and value.isalpha():
                # A bare element symbol is too ambiguous in a whole-document
                # index: C, V and W also occur in °C, volts and watts.  Chinese
                # element names and full English names still expand to their
                # symbols, while an actual formula remains searchable as-is.
                matched = False
            elif len(value) == 2 and value.isalpha():
                matched = bool(re.search(rf"(?<![a-z]){re.escape(value)}(?=$|[^a-z]|[A-Z0-9])", text))
            else:
                matched = value.casefold() in lowered
            if matched:
                break
        if matched:
            aliases.extend(group)
    return " ".join(dict.fromkeys(aliases))


def plan_query(query: str) -> list[str]:
    """Turn a sentence into bounded evidence-search phrases without an API call."""

    cleaned = str(query or "").strip()
    if not cleaned:
        return []
    lowered = cleaned.casefold()
    terms: list[str] = []
    for key in ELEMENT_SEARCH_ALIASES:
        if len(key) == 1 and "\u3400" <= key <= "\u9fff" and key in cleaned:
            terms.append(key)
    for term in DOMAIN_TERMS:
        if term.casefold() in lowered:
            terms.append(term)
            lowered = lowered.replace(term.casefold(), " ")
    for phrase in STOP_PHRASES:
        lowered = lowered.replace(phrase, " ")
    for token in re.findall(r"[A-Za-z][A-Za-z0-9+_.\-/]*|\d+(?:\.\d+)?(?:°C|K|MeV|keV|dpa)?", cleaned):
        if len(token) > 1 or any(char.isdigit() for char in token):
            terms.append(token)
    for chunk in re.findall(r"[\u3400-\u9fff]{2,12}", lowered):
        for part in re.split(r"(?:在|的|后|前|与|和|及|对|中|下|为|是|了)", chunk):
            if 2 <= len(part) <= 8:
                terms.append(part)
    return list(dict.fromkeys(term.casefold() for term in terms if term.strip()))[:12]


def _fts_expression(terms: list[str]) -> str:
    escaped = [term.replace('"', '""') for term in terms]
    return " OR ".join(f'"{term}"' for term in escaped)


@dataclass(frozen=True)
class SearchPage:
    rows: list[dict[str, Any]]
    total: int
    limit: int
    offset: int
    elapsed_ms: float
    terms: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "total": self.total,
            "limit": self.limit,
            "offset": self.offset,
            "elapsed_ms": self.elapsed_ms,
            "query_terms": self.terms,
        }


class EvidenceSearchIndex:
    """Disposable FTS projection over the existing four public evidence types."""

    def __init__(self, db: EvidenceDB):
        self.db = db

    def ensure_schema(self) -> None:
        self.db.init()
        with self.db.connect() as conn:
            try:
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS search_index_fts USING fts5("
                    "display_title,meaning_text,context_text,evidence_text,metadata_text,"
                    "content='search_index_documents',content_rowid='id',tokenize='trigram')"
                )
            except Exception:
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS search_index_fts USING fts5("
                    "display_title,meaning_text,context_text,evidence_text,metadata_text,"
                    "content='search_index_documents',content_rowid='id')"
                )

    def source_fingerprint(self) -> str:
        self.db.init()
        tables = (
            ("data_versions", "id"),
            ("visual_assets", "updated_at"),
            ("visual_asset_reviews", "id"),
            ("quality_candidates", "updated_at"),
            ("data_item_visual_links", "created_at"),
            ("papers", "updated_at"),
        )
        parts: list[str] = []
        with self.db.connect() as conn:
            existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table, marker in tables:
                if table not in existing:
                    continue
                count, maximum = conn.execute(
                    f"SELECT COUNT(*),COALESCE(MAX({marker}), '') FROM {table}"
                ).fetchone()
                parts.append(f"{table}:{count}:{maximum}")
        parts.append(f"index_format:{INDEX_FORMAT_VERSION}")
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    def paper_fingerprints(self) -> dict[int, str]:
        """Return cheap per-paper change markers for incremental index refresh."""

        self.db.init()
        with self.db.connect() as conn:
            paper_ids = [int(row[0]) for row in conn.execute("SELECT id FROM papers ORDER BY id")]
            existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            output: dict[int, str] = {}
            for paper_id in paper_ids:
                parts = [f"paper:{paper_id}", f"format:{INDEX_FORMAT_VERSION}"]
                row = conn.execute(
                    "SELECT updated_at FROM papers WHERE id=?", (paper_id,)
                ).fetchone()
                parts.append(str(row[0] if row else ""))
                count, maximum = conn.execute(
                    "SELECT COUNT(*),COALESCE(MAX(v.id),0) FROM data_versions v "
                    "JOIN data_items i ON i.id=v.item_id WHERE i.paper_id=?", (paper_id,),
                ).fetchone()
                parts.append(f"versions:{count}:{maximum}")
                if "visual_assets" in existing:
                    count, maximum = conn.execute(
                        "SELECT COUNT(*),COALESCE(MAX(updated_at),'') FROM visual_assets WHERE paper_id=?",
                        (paper_id,),
                    ).fetchone()
                    parts.append(f"visuals:{count}:{maximum}")
                if "visual_asset_reviews" in existing:
                    count, maximum = conn.execute(
                        "SELECT COUNT(*),COALESCE(MAX(v.id),0) FROM visual_asset_reviews v "
                        "JOIN visual_assets a ON a.id=v.asset_id WHERE a.paper_id=?", (paper_id,),
                    ).fetchone()
                    parts.append(f"visual_versions:{count}:{maximum}")
                if "quality_candidates" in existing:
                    count, maximum, updated_at = conn.execute(
                        "SELECT COUNT(*),COALESCE(MAX(id),0),COALESCE(MAX(updated_at),'') "
                        "FROM quality_candidates WHERE paper_id=?",
                        (paper_id,),
                    ).fetchone()
                    parts.append(f"quality:{count}:{maximum}:{updated_at}")
                if "data_item_visual_links" in existing:
                    count, maximum = conn.execute(
                        "SELECT COUNT(*),COALESCE(MAX(l.created_at),'') FROM data_item_visual_links l "
                        "JOIN data_items i ON i.id=l.item_id WHERE i.paper_id=?",
                        (paper_id,),
                    ).fetchone()
                    parts.append(f"visual_links:{count}:{maximum}")
                output[paper_id] = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
        return output

    def _document(self, entity_type: str, row: dict[str, Any]) -> dict[str, Any]:
        if entity_type in {"item", "finding"}:
            entity_id = int(row["item_id"])
            title = row.get("meaning") or row.get("finding_text") or row.get("value_text") or "实验数据"
            meaning = _join(row, ("meaning", "finding_text", "value_text", "unit"))
            context = _join(row, ("context_explanation", "search_text"))
            evidence = _join(row, ("source_excerpt", "source_locator"))
            metadata = _join(row, ("article_title", "doi", "first_author", "corresponding_author"))
            source_kind = str(row.get("source_kind") or "text")
        else:
            entity_id = int(row["id"])
            title = row.get("display_name") or row.get("label") or ("原始表格" if entity_type == "table" else "论文图片")
            meaning = _join(row, ("display_name", "physical_quantities", "variables", "caption"))
            context = _join(row, ("context_explanation", "conditions_text", "methods_text", "materials"))
            evidence = _join(row, ("source_context", "caption", "label"))
            metadata = _join(row, ("tags", "article_title", "doi", "first_author", "corresponding_author"))
            source_kind = entity_type
        aliases = _alias_text(" ".join((meaning, context, evidence, metadata)))
        metadata = f"{metadata} {aliases}".strip()
        payload = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "paper_id": int(row["paper_id"]),
            "article_title": str(row.get("article_title") or ""),
            "display_title": str(title),
            "meaning_text": meaning,
            "context_text": context,
            "evidence_text": evidence,
            "metadata_text": metadata,
            "quality_gate_status": str(row.get("quality_gate_status") or "legacy_stable"),
            "source_kind": source_kind,
            "review_action": str(row.get("review_action") or "automatic"),
            "source_page": row.get("source_page") or row.get("page_start"),
            "payload_json": payload,
            "content_hash": digest,
            "updated_at": now(),
        }

    def rebuild(self) -> dict[str, Any]:
        self.ensure_schema()
        started = time.perf_counter()
        documents: list[dict[str, Any]] = []
        documents.extend(self._document("item", row) for row in list_current_facts(self.db))
        documents.extend(self._document("finding", row) for row in list_qualitative_findings(self.db))
        for row in list_visual_assets(self.db):
            documents.append(self._document(str(row["asset_type"]), row))
        columns = (
            "entity_type", "entity_id", "paper_id", "article_title", "display_title",
            "meaning_text", "context_text", "evidence_text", "metadata_text",
            "quality_gate_status", "source_kind", "review_action", "source_page",
            "payload_json", "content_hash", "updated_at",
        )
        fingerprint = self.source_fingerprint()
        paper_fingerprints = self.paper_fingerprints()
        with self.db.connect() as conn:
            conn.execute("DELETE FROM search_index_documents")
            conn.executemany(
                f"INSERT INTO search_index_documents({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                [tuple(document[column] for column in columns) for document in documents],
            )
            conn.execute("INSERT INTO search_index_fts(search_index_fts) VALUES('rebuild')")
            conn.execute(
                "INSERT INTO search_index_state(key,value) VALUES('source_fingerprint',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (fingerprint,),
            )
            conn.execute(
                "INSERT INTO search_index_state(key,value) VALUES('rebuilt_at',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (now(),),
            )
            conn.execute("DELETE FROM search_index_state WHERE key LIKE 'paper:%'")
            conn.executemany(
                "INSERT INTO search_index_state(key,value) VALUES(?,?)",
                [(f"paper:{paper_id}", value) for paper_id, value in paper_fingerprints.items()],
            )
        counts = {entity: 0 for entity in ENTITY_TYPES}
        for document in documents:
            counts[document["entity_type"]] += 1
        return {
            "documents": len(documents),
            "counts": counts,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "fingerprint": fingerprint,
        }

    def refresh_papers(self, paper_ids: Iterable[int]) -> dict[str, Any]:
        """Refresh only changed papers while preserving stable entity identities."""

        self.ensure_schema()
        selected = sorted({int(value) for value in paper_ids})
        started = time.perf_counter()
        current_fingerprints = self.paper_fingerprints()
        global_fingerprint = self.source_fingerprint()
        columns = (
            "entity_type", "entity_id", "paper_id", "article_title", "display_title",
            "meaning_text", "context_text", "evidence_text", "metadata_text",
            "quality_gate_status", "source_kind", "review_action", "source_page",
            "payload_json", "content_hash", "updated_at",
        )
        documents: list[dict[str, Any]] = []
        for paper_id in selected:
            if paper_id not in current_fingerprints:
                continue
            documents.extend(self._document("item", row) for row in list_current_facts(self.db, paper_id))
            documents.extend(self._document("finding", row) for row in list_qualitative_findings(self.db, paper_id))
            for row in list_visual_assets(self.db, paper_id=paper_id):
                documents.append(self._document(str(row["asset_type"]), row))
        with self.db.connect() as conn:
            if selected:
                placeholders = ",".join("?" for _ in selected)
                conn.execute(f"DELETE FROM search_index_documents WHERE paper_id IN ({placeholders})", selected)
            if documents:
                conn.executemany(
                    f"INSERT INTO search_index_documents({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                    [tuple(document[column] for column in columns) for document in documents],
                )
            conn.execute("INSERT INTO search_index_fts(search_index_fts) VALUES('rebuild')")
            for paper_id in selected:
                key = f"paper:{paper_id}"
                if paper_id in current_fingerprints:
                    conn.execute(
                        "INSERT INTO search_index_state(key,value) VALUES(?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, current_fingerprints[paper_id]),
                    )
                else:
                    conn.execute("DELETE FROM search_index_state WHERE key=?", (key,))
            conn.execute(
                "INSERT INTO search_index_state(key,value) VALUES('source_fingerprint',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (global_fingerprint,),
            )
            conn.execute(
                "INSERT INTO search_index_state(key,value) VALUES('rebuilt_at',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (now(),),
            )
        return {
            "papers": selected,
            "documents": len(documents),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        }

    def ensure_fresh(self) -> dict[str, Any]:
        self.ensure_schema()
        fingerprint = self.source_fingerprint()
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT value FROM search_index_state WHERE key='source_fingerprint'"
            ).fetchone()
            count = int(conn.execute("SELECT COUNT(*) FROM search_index_documents").fetchone()[0])
        if count == 0:
            return {"rebuilt": True, **self.rebuild()}
        if not row or row["value"] != fingerprint:
            current = self.paper_fingerprints()
            with self.db.connect() as conn:
                stored = {
                    int(record["key"].split(":", 1)[1]): str(record["value"])
                    for record in conn.execute(
                        "SELECT key,value FROM search_index_state WHERE key LIKE 'paper:%'"
                    )
                }
            changed = sorted(
                paper_id for paper_id in set(current) | set(stored)
                if current.get(paper_id) != stored.get(paper_id)
            )
            if not stored or len(changed) > max(10, len(current) // 2):
                return {"rebuilt": True, **self.rebuild()}
            refreshed = self.refresh_papers(changed)
            return {"rebuilt": True, "incremental": True, **refreshed, "fingerprint": fingerprint}
        return {"rebuilt": False, "documents": count, "fingerprint": fingerprint}

    def search(
        self,
        query: str,
        *,
        entity_types: Iterable[str] | None = None,
        paper_ids: Iterable[int] | None = None,
        quality_filter: str = "all",
        source_filter: str = "all",
        review_filter: str = "all",
        sort: str = "relevance",
        limit: int = 100,
        offset: int = 0,
        refresh: bool = True,
    ) -> SearchPage:
        if refresh:
            self.ensure_fresh()
        started = time.perf_counter()
        types = set(entity_types or ENTITY_TYPES)
        invalid = types - ENTITY_TYPES
        if invalid:
            raise ValueError(f"unsupported search entity type: {', '.join(sorted(invalid))}")
        limit = min(max(int(limit), 1), 500)
        offset = max(int(offset), 0)
        clauses = [f"d.entity_type IN ({','.join('?' for _ in types)})"]
        params: list[Any] = sorted(types)
        selected_papers = sorted({int(paper_id) for paper_id in paper_ids or []})
        if selected_papers:
            clauses.append(f"d.paper_id IN ({','.join('?' for _ in selected_papers)})")
            params.extend(selected_papers)
        if quality_filter not in QUALITY_FILTERS:
            raise ValueError(f"unsupported quality filter: {quality_filter}")
        if quality_filter == "quality_passed":
            clauses.append(f"d.quality_gate_status IN ({','.join('?' for _ in QUALITY_PASSED)})")
            params.extend(sorted(QUALITY_PASSED))
        elif quality_filter != "all":
            clauses.append("d.quality_gate_status=?")
            params.append(quality_filter)
        if source_filter == "figure":
            clauses.append("d.source_kind IN ('figure','text_with_figure')")
        elif source_filter != "all":
            clauses.append("d.source_kind=?")
            params.append(source_filter)
        if review_filter == "reviewed":
            clauses.append("d.review_action IN ('confirmation','correction','manual')")
        elif review_filter == "pending":
            clauses.append("d.review_action='automatic'")
        clauses.append("d.review_action NOT IN ('rejected','ambiguous')")
        where = " AND ".join(clauses)
        terms = plan_query(query)
        if terms:
            # Trigram FTS cannot match one/two-character terms such as “钨” or
            # “硬度”. Long phrases use FTS to reduce the candidate set; short
            # terms add a bounded LIKE branch. Final coverage ranking happens
            # in Python over at most 10k lightweight index records, never over
            # the scientific fact clustering pipeline.
            long_terms = [term for term in terms if len(term) >= 3]
            short_terms = [term for term in terms if len(term) < 3]
            candidate_parts: list[str] = []
            candidate_params: list[Any] = []
            if long_terms:
                candidate_parts.append(
                    "d.id IN (SELECT rowid FROM search_index_fts WHERE search_index_fts MATCH ?)"
                )
                candidate_params.append(_fts_expression(long_terms))
            searchable = (
                "lower(d.display_title||' '||d.meaning_text||' '||d.context_text||' '||"
                "d.evidence_text||' '||d.metadata_text)"
            )
            for term in short_terms:
                candidate_parts.append(f"{searchable} LIKE ?")
                candidate_params.append(f"%{term}%")
            candidate_where = " OR ".join(candidate_parts) or "1=1"
            sql = (
                "SELECT d.*,0.0 rank FROM search_index_documents d "
                f"WHERE ({candidate_where}) AND {where} LIMIT 10000"
            )
            query_params = [*candidate_params, *params]
        else:
            order = "d.article_title,d.source_page,d.id" if sort in {"article", "source_page"} else "d.id DESC"
            sql = f"SELECT d.*,0.0 rank FROM search_index_documents d WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?"
            query_params = params
        with self.db.connect() as conn:
            if terms:
                records = conn.execute(sql, query_params).fetchall()
            else:
                total = int(conn.execute(
                    f"SELECT COUNT(*) FROM search_index_documents d WHERE {where}", query_params
                ).fetchone()[0])
                records = conn.execute(sql, [*query_params, limit, offset]).fetchall()
        if terms:
            ranked: list[tuple[float, Any]] = []
            field_weights = (
                ("display_title", 8.0), ("meaning_text", 7.0), ("context_text", 5.0),
                ("evidence_text", 3.0), ("metadata_text", 1.5),
            )
            minimum = len(terms) if len(terms) <= 2 else max(2, (len(terms) * 2 + 2) // 3)
            for record in records:
                matched = 0
                score = 0.0
                for term in terms:
                    term_score = 0.0
                    for field, weight in field_weights:
                        candidate = str(record[field] or "").casefold()
                        if term in candidate:
                            term_score = max(term_score, weight * (3.0 if term == candidate else 2.0))
                    if term_score:
                        matched += 1
                        score += term_score
                if matched >= minimum:
                    coverage = matched / len(terms)
                    ranked.append((score * (0.55 + 0.45 * coverage), record))
            ranked.sort(key=lambda pair: (-pair[0], int(pair[1]["id"])))
            total = len(ranked)
            selected_records = ranked[offset:offset + limit]
        else:
            selected_records = [(0.0, record) for record in records]
        rows: list[dict[str, Any]] = []
        for score, record in selected_records:
            payload = json.loads(record["payload_json"])
            # The payload originates from one of four existing evidence
            # projections, none of which carries the shared Search V2 type.
            # Add the disposable index identity at the boundary so callers can
            # route the record without guessing from its fields.
            payload["entity_type"] = str(record["entity_type"])
            payload["entity_id"] = int(record["entity_id"])
            payload["search_score"] = round(float(score), 3)
            rows.append(payload)
        if sort in {"article", "source_page"} and terms:
            rows.sort(key=lambda row: (
                str(row.get("article_title") or "").casefold(),
                int(row.get("source_page") or row.get("page_start") or 10**9),
            ))
        return SearchPage(
            rows=rows,
            total=total,
            limit=limit,
            offset=offset,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
            terms=terms,
        )

    def status(self) -> dict[str, Any]:
        fresh = self.ensure_fresh()
        with self.db.connect() as conn:
            counts = {
                row["entity_type"]: int(row["n"])
                for row in conn.execute(
                    "SELECT entity_type,COUNT(*) n FROM search_index_documents GROUP BY entity_type"
                )
            }
            rebuilt = conn.execute(
                "SELECT value FROM search_index_state WHERE key='rebuilt_at'"
            ).fetchone()
        return {**fresh, "counts": counts, "rebuilt_at": rebuilt["value"] if rebuilt else None}

    def get(self, entity_type: str, entity_id: int) -> dict[str, Any]:
        if entity_type not in ENTITY_TYPES:
            raise ValueError("unsupported search entity type")
        self.ensure_fresh()
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM search_index_documents WHERE entity_type=? AND entity_id=?",
                (entity_type, int(entity_id)),
            ).fetchone()
        if not row:
            raise KeyError(f"search entity not found: {entity_type}/{entity_id}")
        payload = json.loads(row["payload_json"])
        payload["entity_type"] = entity_type
        payload["entity_id"] = int(entity_id)
        return payload
