from __future__ import annotations

import difflib
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any

from auto_research.ai.deepseek import DeepSeekClient, DeepSeekResponseError, DeepSeekSettings
from auto_research.paths import DATA_DIR

from .db import EvidenceDB, now
from .deepseek_extraction import DeepSeekEvidenceExtractor, _read_pages
from .extraction_benchmark import _maximum_cardinality_edges, score_pair
from .six_column import (
    _ai_stable_key,
    add_qualitative_item,
    import_ai_result_to_six_column,
)
from .visual_evidence import (
    index_visual_evidence,
    link_data_items_to_visuals,
    list_visual_assets,
)


QUALITY_DIR = DATA_DIR / "evidence" / "quality_runs"
DEFAULT_THRESHOLD = 85.0
PUBLISHABLE_STATUSES = {"dual_pass", "third_pass", "manual_approved"}


ROLE_A = (
    "你是对抗式抽取器 A（完整性审计员）。独立阅读全文，优先避免漏掉实验条件、"
    "表格单元格事实、误差和结果；每条候选必须逐字绑定原文页码与短证据。不要参考另一抽取器。"
)
ROLE_B = (
    "你是对抗式抽取器 B（精确性审计员）。独立阅读全文，优先排除单位错配、材料/条件串联、"
    "重复提及和把定性文字当数值的问题；仍须覆盖全部可报告实验数据。不要参考另一抽取器。"
)


class ProfiledDeepSeekClient:
    """Give two independent DeepSeek branches distinct audit roles."""

    def __init__(self, client: DeepSeekClient, instruction: str, temperature: float):
        self.client = client
        self.settings = client.settings
        self.instruction = instruction
        self.temperature = temperature

    def request_json(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        enriched = [dict(message) for message in messages]
        if enriched and enriched[0].get("role") == "system":
            enriched[0]["content"] = f"{self.instruction}\n\n{enriched[0].get('content', '')}"
        else:
            enriched.insert(0, {"role": "system", "content": self.instruction})
        kwargs.setdefault("temperature", self.temperature)
        return self.client.request_json(enriched, **kwargs)


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _similarity(left: Any, right: Any) -> float:
    a, b = _clean_text(left), _clean_text(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _candidate_key(entity_type: str, candidate: dict[str, Any]) -> str:
    identity = "|".join(
        str(candidate.get(field) or "")
        for field in (
            "asset_id", "value_text", "finding_text", "meaning", "unit",
            "source_page", "source_locator", "source_excerpt",
        )
    )
    return f"{entity_type}_{hashlib.sha1(identity.encode('utf-8')).hexdigest()[:20]}"


def _field_completeness(candidate: dict[str, Any], fields: tuple[str, ...]) -> float:
    present = sum(bool(str(candidate.get(field) or "").strip()) for field in fields)
    return round(100.0 * present / len(fields), 2) if fields else 100.0


def _candidate_strength(candidate: dict[str, Any]) -> float:
    local = bool((candidate.get("local_evidence") or {}).get("passed"))
    supported = (candidate.get("ai_verification") or {}).get("verdict") == "supported"
    factuality = 100.0 if local and supported else 75.0 if local else 35.0
    evidence = 0.0
    if candidate.get("source_page"):
        evidence += 25
    if candidate.get("source_excerpt"):
        evidence += 45
    if candidate.get("source_locator"):
        evidence += 15
    if candidate.get("source_precision") in {"exact_text", "exact_table"}:
        evidence += 15
    completeness = _field_completeness(
        candidate,
        ("value_text", "meaning", "context_explanation", "source_page", "source_excerpt"),
    )
    return round(0.45 * factuality + 0.30 * min(evidence, 100) + 0.25 * completeness, 2)


def _score_data_pair(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any] | None:
    return score_pair(left, right)


def _score_finding_pair(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any] | None:
    page_equal = int(left.get("source_page") or 0) == int(right.get("source_page") or 0)
    finding = _similarity(left.get("finding_text"), right.get("finding_text"))
    meaning = _similarity(left.get("meaning"), right.get("meaning"))
    excerpt = _similarity(left.get("source_excerpt"), right.get("source_excerpt"))
    score = 0.45 * finding + 0.20 * meaning + 0.25 * excerpt + 0.10 * float(page_equal)
    if score < 0.68:
        return None
    return {"score": round(score, 4), "status": "exact" if score >= 0.82 else "partial"}


def _pair_candidates(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    scorer,
) -> tuple[list[tuple[int, int, dict[str, Any]]], set[int], set[int]]:
    edges: list[tuple[float, int, int, dict[str, Any]]] = []
    for left_index, left_item in enumerate(left):
        for right_index, right_item in enumerate(right):
            scored = scorer(left_item, right_item)
            if scored:
                edges.append((float(scored["score"]), left_index, right_index, scored))
    pairs = _maximum_cardinality_edges(edges)
    return pairs, {item[0] for item in pairs}, {item[1] for item in pairs}


def _make_record(
    entity_type: str,
    primary: dict[str, Any],
    alternate: dict[str, Any] | None,
    agreement: float,
    chosen_source: str,
    threshold: float,
) -> dict[str, Any]:
    primary_strength = _candidate_strength(primary)
    alternate_strength = _candidate_strength(alternate) if alternate else 0.0
    if alternate and alternate_strength > primary_strength:
        primary, alternate = alternate, primary
        chosen_source = "extractor_b" if chosen_source == "extractor_a" else "extractor_a"
        primary_strength, alternate_strength = alternate_strength, primary_strength
    factuality = 100.0 if (
        (primary.get("local_evidence") or {}).get("passed")
        and (primary.get("ai_verification") or {}).get("verdict") == "supported"
    ) else 70.0
    evidence = min(100.0, 35.0 + (25.0 if primary.get("source_page") else 0.0)
                   + (25.0 if primary.get("source_excerpt") else 0.0)
                   + (15.0 if primary.get("source_locator") else 0.0))
    completeness = _field_completeness(
        primary,
        ("value_text" if entity_type == "data" else "finding_text", "meaning",
         "context_explanation", "source_page", "source_excerpt"),
    )
    agreement_score = round(agreement * 100.0, 2)
    overall = round(
        0.35 * factuality + 0.25 * evidence + 0.20 * completeness + 0.20 * agreement_score,
        2,
    )
    dual_supported = bool(alternate) and all(
        (item.get("local_evidence") or {}).get("passed")
        and (item.get("ai_verification") or {}).get("verdict") == "supported"
        for item in (primary, alternate)
    )
    status = "dual_pass" if dual_supported and overall >= threshold else "manual_review"
    reason = (
        "两路独立抽取一致，且证据与字段完整度达到自动发布阈值"
        if status == "dual_pass"
        else "两路结果未同时达到自动发布阈值，进入第三次独立复核"
    )
    return {
        "entity_type": entity_type,
        "candidate_key": _candidate_key(entity_type, primary),
        "chosen_source": chosen_source,
        "candidate": primary,
        "alternate": alternate,
        "agreement_score": agreement_score,
        "factuality_score": factuality,
        "completeness_score": completeness,
        "evidence_score": evidence,
        "overall_score": overall,
        "gate_status": status,
        "gate_reason": reason,
    }


def _visual_prompt(assets: list[dict[str, Any]]) -> list[dict[str, str]]:
    compact = [
        {
            "asset_id": int(asset["id"]),
            "asset_type": asset["asset_type"],
            "label": asset["label"],
            "page": asset["page_start"],
            "caption": asset.get("caption") or "",
            "nearby_text": str(asset.get("source_context") or "")[:3500],
        }
        for asset in assets
    ]
    return [
        {
            "role": "system",
            "content": (
                "你只依据图表编号、图注和相邻正文，独立生成可检索的科学语义元数据。"
                "你没有看到图片像素，不得声称进行了视觉识别，不得从曲线猜测数据点。"
                "返回 JSON：{visuals:[{asset_id,display_name,physical_quantities,variables,materials,"
                "conditions_text,methods_text,context_explanation,tags}]}。"
            ),
        },
        {"role": "user", "content": _json(compact)},
    ]


def _extract_visual_semantics(client: ProfiledDeepSeekClient,
                              assets: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    for start in range(0, len(assets), 16):
        batch = assets[start:start + 16]
        payload = client.request_json(
            _visual_prompt(batch), task="analysis", max_tokens=6000, thinking=False
        )
        for item in payload.get("visuals", []):
            if not isinstance(item, dict):
                continue
            try:
                asset_id = int(item.get("asset_id"))
            except (TypeError, ValueError):
                continue
            if asset_id not in {int(asset["id"]) for asset in batch}:
                continue
            output[asset_id] = item
    return output


def _visual_text(item: dict[str, Any]) -> str:
    return " ".join(
        str(item.get(field) or "")
        for field in (
            "display_name", "physical_quantities", "variables", "materials",
            "conditions_text", "methods_text", "context_explanation", "tags",
        )
    )


def _visual_local_score(asset: dict[str, Any]) -> float:
    path = Path(str(asset.get("image_path") or ""))
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[3] / path
    score = 0.0
    if path.is_file() and path.stat().st_size >= 1000:
        score += 45
    if asset.get("image_sha256"):
        score += 15
    if asset.get("label") and asset.get("caption"):
        score += 25
    if asset.get("page_start") and asset.get("bbox_json"):
        score += 15
    return min(score, 100.0)


def _make_visual_record(asset: dict[str, Any], left: dict[str, Any] | None,
                        right: dict[str, Any] | None, *, is_new: bool,
                        threshold: float) -> dict[str, Any]:
    left = dict(left or {})
    right = dict(right or {})
    agreement = _similarity(_visual_text(left), _visual_text(right)) if left and right else 0.0
    left_complete = _field_completeness(
        left, ("display_name", "context_explanation", "tags")
    )
    right_complete = _field_completeness(
        right, ("display_name", "context_explanation", "tags")
    )
    base = {
        "asset_id": int(asset["id"]),
        "asset_type": asset["asset_type"],
        "label": asset["label"],
        "page_start": asset["page_start"],
        "caption": asset.get("caption") or "",
        "is_new_asset": bool(is_new),
    }
    primary, alternate, source = ({**base, **left}, {**base, **right}, "extractor_a")
    if right_complete > left_complete:
        primary, alternate, source = ({**base, **right}, {**base, **left}, "extractor_b")
    local_score = _visual_local_score(asset)
    completeness = max(left_complete, right_complete)
    agreement_score = round(agreement * 100, 2)
    overall = round(0.45 * local_score + 0.25 * completeness + 0.30 * agreement_score, 2)
    dual_pass = bool(left and right) and local_score >= 90 and overall >= threshold
    candidate = primary
    return {
        "entity_type": asset["asset_type"],
        "candidate_key": f"{asset['asset_type']}_asset_{int(asset['id'])}",
        "chosen_source": source,
        "candidate": candidate,
        "alternate": alternate if left or right else None,
        "agreement_score": agreement_score,
        "factuality_score": local_score,
        "completeness_score": completeness,
        "evidence_score": local_score,
        "overall_score": overall,
        "gate_status": "dual_pass" if dual_pass else "manual_review",
        "gate_reason": (
            "本地截图完整，且两路 DeepSeek 图注语义一致"
            if dual_pass else "截图或两路语义一致性未达到自动发布阈值，进入第三次复核"
        ),
        "asset_id": int(asset["id"]),
    }


def _third_review_messages(records: list[dict[str, Any]], pages: dict[int, str]) -> list[dict[str, str]]:
    payload = []
    for record in records:
        candidate = record["candidate"]
        page = int(candidate.get("source_page") or candidate.get("page_start") or 0)
        payload.append({
            "candidate_key": record["candidate_key"],
            "entity_type": record["entity_type"],
            "candidate": candidate,
            "alternate": record.get("alternate"),
            "current_scores": {
                "agreement": record["agreement_score"],
                "factuality": record["factuality_score"],
                "completeness": record["completeness_score"],
                "evidence": record["evidence_score"],
                "overall": record["overall_score"],
            },
            "source_page_text": str(pages.get(page, ""))[:7000],
        })
    return [
        {
            "role": "system",
            "content": (
                "你是第三位独立质量裁判。逐项核对候选与原文，只能批准原文明确支持且字段关系完整的候选。"
                "表格/图片对象只能依据图注和相邻文字评价语义，不能假装看到图片像素。"
                "返回 JSON：{verdicts:[{candidate_key,approved,preferred_source,"
                "factuality_score,completeness_score,evidence_score,overall_score,reason}]}。"
                "所有分数为0至100；overall_score>=85且approved=true才可自动发布。"
            ),
        },
        {"role": "user", "content": _json(payload)},
    ]


def _apply_third_review(client: DeepSeekClient, records: list[dict[str, Any]],
                        pages: dict[int, str], threshold: float) -> None:
    for start in range(0, len(records), 10):
        batch = records[start:start + 10]
        try:
            payload = client.request_json(
                _third_review_messages(batch, pages), task="verification",
                max_tokens=5000, thinking=False, temperature=0.0,
            )
        except DeepSeekResponseError as exc:
            for record in batch:
                record["gate_reason"] = f"第三次复核未完成，转人工审核：{exc}"
            continue
        verdicts = {
            str(item.get("candidate_key")): item
            for item in payload.get("verdicts", []) if isinstance(item, dict)
        }
        for record in batch:
            verdict = verdicts.get(record["candidate_key"])
            record["third_review"] = verdict
            if not verdict:
                record["gate_reason"] = "第三次复核未返回该候选，转人工审核"
                continue
            preferred = str(verdict.get("preferred_source") or "")
            if preferred == "extractor_b" and record.get("alternate"):
                record["candidate"], record["alternate"] = record["alternate"], record["candidate"]
                record["chosen_source"] = "extractor_b"
            for field in ("factuality_score", "completeness_score", "evidence_score", "overall_score"):
                try:
                    record[field] = min(max(float(verdict.get(field, record[field])), 0), 100)
                except (TypeError, ValueError):
                    pass
            if bool(verdict.get("approved")) and record["overall_score"] >= threshold:
                record["gate_status"] = "third_pass"
                record["gate_reason"] = str(verdict.get("reason") or "第三次独立复核通过")
            else:
                record["gate_status"] = "manual_review"
                record["gate_reason"] = str(verdict.get("reason") or "第三次复核后仍低于阈值")


def _measurement_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "category": "six_column_evidence",
        "parameter": candidate["meaning"],
        "value_raw": candidate["value_text"],
        "value_num": None,
        "uncertainty_num": None,
        "unit_raw": candidate.get("unit") or None,
        "condition_text": candidate["context_explanation"],
        "measurement_method": None,
        "evidence_type": candidate.get("evidence_type") or "measured",
        "source_precision": candidate.get("source_precision") or "exact_text",
        "page_number": int(candidate["source_page"]),
        "locator": candidate.get("source_locator"),
        "excerpt": candidate["source_excerpt"],
    }


def _apply_visual_semantics(db: EvidenceDB, candidate: dict[str, Any]) -> int:
    asset_id = int(candidate["asset_id"])
    display_name = str(candidate.get("display_name") or candidate.get("label") or "").strip()
    with db.connect() as conn:
        conn.execute(
            """UPDATE visual_assets SET display_name=?,physical_quantities_json=?,variables_json=?,
               materials_json=?,conditions_text=?,methods_text=?,context_explanation=?,tags_json=?,
               metadata_source='deepseek',updated_at=? WHERE id=?""",
            (
                display_name,
                _json(candidate.get("physical_quantities") or []),
                _json(candidate.get("variables") or {}),
                _json(candidate.get("materials") or []),
                str(candidate.get("conditions_text") or ""),
                str(candidate.get("methods_text") or ""),
                str(candidate.get("context_explanation") or ""),
                _json(candidate.get("tags") or []),
                now(), asset_id,
            ),
        )
    return asset_id


def _publish_candidate(db: EvidenceDB, paper_id: int, entity_type: str,
                       candidate: dict[str, Any]) -> tuple[int | None, int | None]:
    if entity_type == "data":
        measurement = _measurement_payload(candidate)
        import_ai_result_to_six_column(
            db, paper_id,
            {"materials": [], "experiments": [], "measurements": [measurement], "pending_tasks": []},
        )
        stable_key = _ai_stable_key(measurement)
        with db.connect() as conn:
            row = conn.execute(
                "SELECT id FROM data_items WHERE paper_id=? AND stable_key=?",
                (paper_id, stable_key),
            ).fetchone()
        return (int(row["id"]) if row else None), None
    if entity_type == "finding":
        item = add_qualitative_item(db, paper_id, candidate, editor="DeepSeek quality gate")
        return int(item["item_id"]), None
    return None, _apply_visual_semantics(db, candidate)


def latest_quality_run(db: EvidenceDB, paper_id: int) -> dict[str, Any] | None:
    db.init()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM quality_pipeline_runs WHERE paper_id=? ORDER BY id DESC LIMIT 1",
            (paper_id,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        counts = conn.execute(
            """SELECT gate_status,COUNT(*) count FROM quality_candidates
               WHERE pipeline_run_id=? GROUP BY gate_status""",
            (int(row["id"]),),
        ).fetchall()
    try:
        result["summary"] = json.loads(str(result.pop("summary_json") or "{}"))
    except json.JSONDecodeError:
        result["summary"] = {}
    result["gate_counts"] = {item["gate_status"]: int(item["count"]) for item in counts}
    return result


def list_quality_candidates(db: EvidenceDB, paper_id: int, *, status: str | None = None,
                            limit: int = 500) -> list[dict[str, Any]]:
    db.init()
    sql = "SELECT * FROM quality_candidates WHERE paper_id=?"
    params: list[Any] = [paper_id]
    if status:
        sql += " AND gate_status=?"
        params.append(status)
    sql += " ORDER BY CASE gate_status WHEN 'manual_review' THEN 0 ELSE 1 END,overall_score,id LIMIT ?"
    params.append(min(max(limit, 1), 2000))
    with db.connect() as conn:
        rows = [dict(row) for row in conn.execute(sql, params)]
    for row in rows:
        for source, target in (("candidate_json", "candidate"), ("alternate_json", "alternate"),
                               ("third_review_json", "third_review")):
            try:
                row[target] = json.loads(str(row.pop(source) or "null"))
            except json.JSONDecodeError:
                row[target] = None
    return rows


def quality_for_items(db: EvidenceDB, item_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not item_ids:
        return {}
    db.init()
    placeholders = ",".join("?" for _ in item_ids)
    with db.connect() as conn:
        rows = conn.execute(
            f"""SELECT q.* FROM quality_candidates q
                WHERE q.published_item_id IN ({placeholders})
                  AND q.id=(SELECT q2.id FROM quality_candidates q2
                            WHERE q2.published_item_id=q.published_item_id ORDER BY q2.id DESC LIMIT 1)""",
            item_ids,
        ).fetchall()
    return {
        int(row["published_item_id"]): {
            "quality_gate_status": row["gate_status"],
            "quality_score": float(row["overall_score"]),
            "quality_candidate_id": int(row["id"]),
        }
        for row in rows
    }


def quality_for_assets(db: EvidenceDB, asset_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not asset_ids:
        return {}
    db.init()
    placeholders = ",".join("?" for _ in asset_ids)
    with db.connect() as conn:
        rows = conn.execute(
            f"""SELECT q.* FROM quality_candidates q
                WHERE q.published_asset_id IN ({placeholders})
                  AND q.id=(SELECT q2.id FROM quality_candidates q2
                            WHERE q2.published_asset_id=q.published_asset_id ORDER BY q2.id DESC LIMIT 1)""",
            asset_ids,
        ).fetchall()
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        try:
            candidate = json.loads(str(row["candidate_json"] or "{}"))
        except json.JSONDecodeError:
            candidate = {}
        is_new = bool(candidate.get("is_new_asset"))
        gate_status = str(row["gate_status"])
        visible_status = (
            "legacy_stable"
            if not is_new and gate_status not in PUBLISHABLE_STATUSES
            else gate_status
        )
        result[int(row["published_asset_id"])] = {
            "quality_gate_status": visible_status,
            "quality_candidate_status": gate_status,
            "quality_score": float(row["overall_score"]),
            "quality_candidate_id": int(row["id"]),
            "quality_is_new_asset": is_new,
        }
    return result


def review_quality_candidate(db: EvidenceDB, candidate_id: int, *, decision: str,
                             fields: dict[str, Any] | None = None,
                             reviewer: str = "本地研究者", note: str = "") -> dict[str, Any]:
    if decision not in {"approve", "reject"}:
        raise ValueError("quality decision must be approve or reject")
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM quality_candidates WHERE id=?", (candidate_id,)).fetchone()
    if not row:
        raise KeyError(f"quality candidate not found: {candidate_id}")
    if str(row["gate_status"]) != "manual_review":
        raise ValueError("only candidates awaiting manual review can be approved or rejected")
    candidate = json.loads(str(row["candidate_json"]))
    if fields:
        for key, value in fields.items():
            if key in candidate:
                candidate[key] = value
    item_id = row["published_item_id"]
    asset_id = row["published_asset_id"]
    if decision == "approve":
        item_id, applied_asset_id = _publish_candidate(
            db, int(row["paper_id"]), str(row["entity_type"]), candidate
        )
        asset_id = applied_asset_id or asset_id
        gate_status = "manual_approved"
    else:
        gate_status = "rejected"
    with db.connect() as conn:
        conn.execute(
            """UPDATE quality_candidates SET candidate_json=?,gate_status=?,gate_reason=?,
               published_item_id=?,published_asset_id=?,reviewer=?,review_note=?,updated_at=? WHERE id=?""",
            (
                _json(candidate), gate_status,
                "人工审核通过" if decision == "approve" else "人工审核不采用",
                item_id, asset_id, reviewer, note, now(), candidate_id,
            ),
        )
    return next(item for item in list_quality_candidates(db, int(row["paper_id"]))
                if int(item["id"]) == candidate_id)


class AdversarialQualityPipeline:
    def __init__(self, db: EvidenceDB, settings: DeepSeekSettings | None = None,
                 output_dir: Path | None = None):
        self.db = db
        self.settings = settings or DeepSeekSettings.from_env()
        self.output_dir = Path(output_dir or QUALITY_DIR)
        self.db.init()

    def _update(self, run_id: int, *, stage: str, progress: int,
                summary: dict[str, Any] | None = None) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """UPDATE quality_pipeline_runs SET stage=?,progress=?,summary_json=? WHERE id=?""",
                (stage, progress, _json(summary or {}), run_id),
            )

    def _branch(self, paper_id: int, role: str, temperature: float,
                max_pages: int | None, chunk_pages: int,
                assets: list[dict[str, Any]], progress_callback=None) -> dict[str, Any]:
        profiled = ProfiledDeepSeekClient(
            DeepSeekClient(self.settings), role, temperature
        )
        extraction = DeepSeekEvidenceExtractor(self.db, client=profiled).run(
            paper_id,
            commit=False,
            max_pages=max_pages,
            chunk_pages=chunk_pages,
            merge_existing=True,
            parallel_focuses=True,
            prepare_visuals=False,
            enrich_visuals=False,
            update_processing_job=False,
            prompt_instruction=role,
            progress_callback=progress_callback,
        )
        if progress_callback:
            progress_callback({
                "run_id": extraction["run_id"], "stage": "visual_semantics", "progress": 94,
            })
        extraction["visual_semantics"] = _extract_visual_semantics(profiled, assets)
        if progress_callback:
            progress_callback({
                "run_id": extraction["run_id"], "stage": "completed", "progress": 100,
            })
        return extraction

    def run(self, paper_id: int, *, max_pages: int | None = None,
            chunk_pages: int = 2, threshold: float = DEFAULT_THRESHOLD) -> dict[str, Any]:
        paper = self.db.get_paper(paper_id)
        if not paper or not paper.get("pdf_path") or not Path(paper["pdf_path"]).is_file():
            raise FileNotFoundError("当前文章没有可读取的本地 PDF")
        if not self.settings.api_key:
            raise ValueError("DeepSeek 尚未配置，无法运行对抗式质量检测")
        with self.db.connect() as conn:
            cur = conn.execute(
                """INSERT INTO quality_pipeline_runs(
                   paper_id,status,stage,progress,quality_threshold,created_at
                   ) VALUES(?,'running','visual_index',2,?,?)""",
                (paper_id, threshold, now()),
            )
            pipeline_run_id = int(cur.lastrowid)
        try:
            before_ids = {int(asset["id"]) for asset in list_visual_assets(self.db, paper_id=paper_id)}
            visual_summary = index_visual_evidence(self.db, paper_id)
            assets = list_visual_assets(self.db, paper_id=paper_id)
            new_asset_ids = {int(asset["id"]) for asset in assets} - before_ids
            self._update(pipeline_run_id, stage="dual_extraction", progress=10)
            branch_progress = {"completeness": 0.0, "precision": 0.0}
            branch_lock = Lock()

            def report_branch(name: str, event: dict[str, Any]) -> None:
                with branch_lock:
                    branch_progress[name] = min(max(float(event.get("progress") or 0), 0), 100)
                    aggregate = 10 + round(0.44 * sum(branch_progress.values()) / 2)
                    run_column = (
                        "extractor_a_run_id" if name == "completeness" else "extractor_b_run_id"
                    )
                    with self.db.connect() as conn:
                        conn.execute(
                            f"""UPDATE quality_pipeline_runs SET {run_column}=?,stage='dual_extraction',
                               progress=?,summary_json=? WHERE id=?""",
                            (
                                event.get("run_id"), aggregate,
                                _json({"branch_progress": dict(branch_progress)}), pipeline_run_id,
                            ),
                        )

            with ThreadPoolExecutor(max_workers=2) as executor:
                future_a = executor.submit(
                    self._branch, paper_id, ROLE_A, 0.1, max_pages, chunk_pages, assets,
                    lambda event: report_branch("completeness", event),
                )
                future_b = executor.submit(
                    self._branch, paper_id, ROLE_B, 0.45, max_pages, chunk_pages, assets,
                    lambda event: report_branch("precision", event),
                )
                branch_a = future_a.result()
                branch_b = future_b.result()
            with self.db.connect() as conn:
                conn.execute(
                    """UPDATE quality_pipeline_runs SET extractor_a_run_id=?,extractor_b_run_id=?,
                       stage='adversarial_compare',progress=58 WHERE id=?""",
                    (branch_a["run_id"], branch_b["run_id"], pipeline_run_id),
                )

            records: list[dict[str, Any]] = []
            data_a = [dict(item) for item in branch_a.get("verified_candidates", [])]
            data_b = [dict(item) for item in branch_b.get("verified_candidates", [])]
            pairs, used_a, used_b = _pair_candidates(data_a, data_b, _score_data_pair)
            for left_index, right_index, scored in pairs:
                records.append(_make_record(
                    "data", data_a[left_index], data_b[right_index],
                    float(scored["score"]), "extractor_a", threshold,
                ))
            for index, candidate in enumerate(data_a):
                if index not in used_a:
                    records.append(_make_record("data", candidate, None, 0.0, "extractor_a", threshold))
            for index, candidate in enumerate(data_b):
                if index not in used_b:
                    records.append(_make_record("data", candidate, None, 0.0, "extractor_b", threshold))

            findings_a = [dict(item) for item in branch_a.get("qualitative_findings", [])]
            findings_b = [dict(item) for item in branch_b.get("qualitative_findings", [])]
            pairs, used_a, used_b = _pair_candidates(findings_a, findings_b, _score_finding_pair)
            for left_index, right_index, scored in pairs:
                records.append(_make_record(
                    "finding", findings_a[left_index], findings_b[right_index],
                    float(scored["score"]), "extractor_a", threshold,
                ))
            for index, candidate in enumerate(findings_a):
                if index not in used_a:
                    records.append(_make_record("finding", candidate, None, 0.0, "extractor_a", threshold))
            for index, candidate in enumerate(findings_b):
                if index not in used_b:
                    records.append(_make_record("finding", candidate, None, 0.0, "extractor_b", threshold))

            visual_a = branch_a.get("visual_semantics", {})
            visual_b = branch_b.get("visual_semantics", {})
            for asset in assets:
                records.append(_make_visual_record(
                    asset,
                    visual_a.get(int(asset["id"])),
                    visual_b.get(int(asset["id"])),
                    is_new=int(asset["id"]) in new_asset_ids,
                    threshold=threshold,
                ))

            self._update(pipeline_run_id, stage="third_review", progress=70)
            pages = {
                int(page["page"]): str(page.get("text") or "")
                for page in _read_pages(Path(paper["pdf_path"]), max_pages=max_pages)
            }
            low_records = [record for record in records if record["gate_status"] == "manual_review"]
            final_client = DeepSeekClient(self.settings)
            _apply_third_review(final_client, low_records, pages, threshold)

            self._update(pipeline_run_id, stage="publish", progress=86)
            published_items = 0
            published_visuals = 0
            manual_count = 0
            candidate_ids: list[int] = []
            for record in records:
                asset_id = record.get("asset_id")
                with self.db.connect() as conn:
                    cur = conn.execute(
                        """INSERT INTO quality_candidates(
                           pipeline_run_id,paper_id,entity_type,candidate_key,chosen_source,
                           candidate_json,alternate_json,agreement_score,factuality_score,
                           completeness_score,evidence_score,overall_score,gate_status,gate_reason,
                           third_review_json,published_asset_id,created_at,updated_at
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            pipeline_run_id, paper_id, record["entity_type"], record["candidate_key"],
                            record["chosen_source"], _json(record["candidate"]),
                            _json(record["alternate"]) if record.get("alternate") else None,
                            record["agreement_score"], record["factuality_score"],
                            record["completeness_score"], record["evidence_score"],
                            record["overall_score"], record["gate_status"], record["gate_reason"],
                            _json(record.get("third_review")) if record.get("third_review") else None,
                            asset_id, now(), now(),
                        ),
                    )
                    candidate_id = int(cur.lastrowid)
                    candidate_ids.append(candidate_id)
                if record["gate_status"] in PUBLISHABLE_STATUSES:
                    item_id, applied_asset_id = _publish_candidate(
                        self.db, paper_id, record["entity_type"], record["candidate"]
                    )
                    with self.db.connect() as conn:
                        conn.execute(
                            "UPDATE quality_candidates SET published_item_id=?,published_asset_id=?,updated_at=? WHERE id=?",
                            (item_id, applied_asset_id or asset_id, now(), candidate_id),
                        )
                    published_items += int(item_id is not None)
                    published_visuals += int((applied_asset_id or asset_id) is not None and record["entity_type"] in {"table", "figure"})
                else:
                    manual_count += 1
            link_summary = link_data_items_to_visuals(self.db, paper_id)
            gate_counts: dict[str, int] = {}
            for record in records:
                gate_counts[record["gate_status"]] = gate_counts.get(record["gate_status"], 0) + 1
            summary = {
                "candidate_count": len(records),
                "dual_pass_count": gate_counts.get("dual_pass", 0),
                "third_pass_count": gate_counts.get("third_pass", 0),
                "manual_review_count": manual_count,
                "published_item_count": published_items,
                "published_visual_count": published_visuals,
                "table_count": int(visual_summary.get("table_count", 0)),
                "figure_count": int(visual_summary.get("figure_count", 0)),
                "quality_threshold": threshold,
                "gate_counts": gate_counts,
                "links": link_summary,
            }
            result = {
                "pipeline_run_id": pipeline_run_id,
                "paper": {"id": paper_id, "title": paper["title"], "doi": paper.get("doi")},
                "extractor_a_run_id": branch_a["run_id"],
                "extractor_b_run_id": branch_b["run_id"],
                "summary": summary,
                "candidate_ids": candidate_ids,
                "created_at": now(),
            }
            self.output_dir.mkdir(parents=True, exist_ok=True)
            output_path = self.output_dir / f"paper_{paper_id:03d}_quality_{pipeline_run_id:04d}.json"
            output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            with self.db.connect() as conn:
                conn.execute(
                    """UPDATE quality_pipeline_runs SET status='completed',stage='completed',progress=100,
                       summary_json=?,output_path=?,finished_at=? WHERE id=?""",
                    (_json(summary), str(output_path), now(), pipeline_run_id),
                )
            result["output_path"] = str(output_path)
            return result
        except BaseException as exc:
            with self.db.connect() as conn:
                conn.execute(
                    """UPDATE quality_pipeline_runs SET status='failed',stage='failed',progress=100,
                       error_message=?,finished_at=? WHERE id=?""",
                    (str(exc)[:1200], now(), pipeline_run_id),
                )
            raise
