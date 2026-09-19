from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from .deepseek_extraction import (
    VERDICTS,
    _apply_document_mode_guard,
    _coverage_gap_messages,
    _coverage_quantity_anchors,
    _deduplicate,
    _deduplicate_findings,
    _evidence_check,
    _validated_candidates,
    _validated_findings,
    _verification_batches,
    _verification_messages,
)
from .literature_extraction_job import (
    FrozenModelCall,
    LiteratureExtractionJobError,
    LiteratureStageContext,
    PlannedLiteratureStage,
    _canonical_bytes,
    _plain,
)
from .literature_extraction_budget import (
    COVERAGE_GAP_MAX_TOKENS_PER_CALL,
    MAX_EXTRACTED_RECORDS_PER_RESPONSE,
    MAX_THIRD_REVIEW_CALLS_PER_TASK,
    MAX_VERIFICATION_BATCHES_PER_BRANCH_BLOCK,
    THIRD_REVIEW_BATCH_SIZE,
    THIRD_REVIEW_MAX_TOKENS_PER_CALL,
    VERIFICATION_MAX_TOKENS_PER_CALL,
)
from .quality_pipeline import (
    DEFAULT_THRESHOLD,
    _apply_third_review_payloads,
    _make_record,
    _pair_candidates,
    _score_data_pair,
    _score_finding_pair,
    _third_review_messages,
)


from .literature_visual_review import plan_visual_review, compare_visual_review

BRANCHES = ("a", "b")


def _assert_extraction_payload_bounds(payload: object) -> None:
    if not isinstance(payload, Mapping):
        return
    data = payload.get("data")
    findings = payload.get("findings", [])
    if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
        return
    if not isinstance(findings, Sequence) or isinstance(findings, (str, bytes)):
        return
    if len(data) + len(findings) > MAX_EXTRACTED_RECORDS_PER_RESPONSE:
        raise ValueError("extraction response exceeds the reviewed record limit")


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _call_parts(call_id: str) -> tuple[str, int, int]:
    parts = call_id.split("-")
    if len(parts) != 5 or parts[1] != "chunk" or parts[3] != "focus":
        raise LiteratureExtractionJobError("literature_stage_invalid", "抽取调用标识无效")
    try:
        branch, chunk_index, pass_index = parts[0], int(parts[2]), int(parts[4])
    except (TypeError, ValueError) as exc:
        raise LiteratureExtractionJobError("literature_stage_invalid", "抽取调用标识无效") from exc
    if branch not in BRANCHES or chunk_index < 1 or pass_index < 1:
        raise LiteratureExtractionJobError("literature_stage_invalid", "抽取调用标识无效")
    return branch, chunk_index, pass_index


def _chunk(context: LiteratureStageContext, index: int) -> list[dict[str, Any]]:
    if not 1 <= index <= len(context.chunks):
        raise LiteratureExtractionJobError("literature_stage_invalid", "抽取页块超出范围")
    return [_plain(page) for page in context.chunks[index - 1]]


def _empty_branch_state(context: LiteratureStageContext) -> dict[str, Any]:
    return {
        branch: {
            "chunks": [
                {"candidates": [], "findings": [], "rejected": [], "finding_rejected": [],
                 "pending_tasks": []}
                for _ in context.chunks
            ]
        }
        for branch in BRANCHES
    }


def _latest(context: LiteratureStageContext, stage: str) -> dict[str, Any]:
    matches = [_plain(item.result) for item in context.prior_outputs if item.stage == stage]
    if len(matches) != 1 or not isinstance(matches[0], dict):
        raise LiteratureExtractionJobError("literature_stage_invalid", "抽取阶段缺少可信前序结果")
    return matches[0]


def _normalized_source(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _anchor_is_covered(anchor: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]) -> bool:
    page = int(anchor.get("source_page") or 0)
    anchor_text = _normalized_source(anchor.get("source_excerpt"))
    if not anchor_text:
        return False
    for candidate in candidates:
        if int(candidate.get("source_page") or 0) != page:
            continue
        excerpt = _normalized_source(candidate.get("source_excerpt"))
        if excerpt and (excerpt in anchor_text or anchor_text in excerpt):
            return True
    return False


def _coverage_gap_reasons(
    chunk: Sequence[Mapping[str, Any]], chunk_state: Mapping[str, Any]
) -> tuple[str, ...]:
    """Conservatively decide whether another paid recall pass is justified."""

    reasons: list[str] = []
    pending = chunk_state.get("pending_tasks") or []
    if pending:
        reasons.append("pending_task")
    if chunk_state.get("rejected") or chunk_state.get("finding_rejected"):
        reasons.append("uncertain_rejected_output")
    candidates = list(chunk_state.get("candidates") or [])
    findings = list(chunk_state.get("findings") or [])
    anchors = _coverage_quantity_anchors([_plain(page) for page in chunk])
    if any(not _anchor_is_covered(anchor, candidates) for anchor in anchors):
        reasons.append("uncovered_quantity_anchor")
    page_anchor_counts: dict[int, int] = {}
    for anchor in anchors:
        page = int(anchor["source_page"])
        page_anchor_counts[page] = page_anchor_counts.get(page, 0) + 1
    if any(count >= 40 for count in page_anchor_counts.values()):
        reasons.append("quantity_anchor_limit_reached")
    if not candidates and not findings and any(
        _normalized_source(page.get("text")) for page in chunk
    ):
        reasons.append("uncertain_empty_inventory")
    return tuple(dict.fromkeys(reasons))


def _has_chinese_semantics(candidate: Mapping[str, Any]) -> bool:
    return bool(
        re.search(r"[\u4e00-\u9fff]", str(candidate.get("meaning") or ""))
        and re.search(
            r"[\u4e00-\u9fff]", str(candidate.get("context_explanation") or "")
        )
    )


def _requires_human_localization(record: Mapping[str, Any]) -> bool:
    return any(
        isinstance(candidate, Mapping) and not _has_chinese_semantics(candidate)
        for candidate in (record.get("candidate"), record.get("alternate"))
        if candidate is not None
    )


class ExistingLiteratureStagePlanner:
    """Pure adapter over the existing extraction parsers and adversarial gate."""

    def __init__(self, *, threshold: float = DEFAULT_THRESHOLD) -> None:
        if not 0 <= threshold <= 100:
            raise ValueError("quality threshold is invalid")
        self._threshold = float(threshold)

    def plan_next(
        self,
        context: LiteratureStageContext,
        raw_results: Sequence[Mapping[str, Any]],
    ) -> PlannedLiteratureStage:
        handlers = {
            "initial_focus": self._initial,
            "coverage_gap": self._coverage,
            "coverage_verification": self._verification,
            "adversarial_branches": self._adversarial,
            "third_review": self._third,
        }
        try:
            return handlers[context.stage.name](context, raw_results)
        except LiteratureExtractionJobError:
            raise
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise LiteratureExtractionJobError(
                "literature_stage_invalid", "模型阶段结果未通过既有科学验证"
            ) from exc

    def _initial(self, context: LiteratureStageContext, raw_results) -> PlannedLiteratureStage:
        state = _empty_branch_state(context)
        for call, payload in zip(context.stage.calls, raw_results, strict=True):
            _assert_extraction_payload_bounds(payload)
            branch, chunk_index, pass_index = _call_parts(call.call_id)
            chunk = _chunk(context, chunk_index)
            candidates, rejected = _validated_candidates(
                _plain(payload), {int(page["page"]) for page in chunk}, chunk_index, pass_index
            )
            candidates, mode_rejected = _apply_document_mode_guard(
                candidates, _plain(context.experiment_profile)
            )
            findings, finding_rejected = _validated_findings(
                _plain(payload), chunk, chunk_index, pass_index
            )
            target = state[branch]["chunks"][chunk_index - 1]
            for item in candidates:
                item["extraction_pass"] = pass_index
                item["extraction_focus"] = "combined_recall_checklist"
            target["candidates"].extend(candidates)
            target["rejected"].extend(rejected + mode_rejected)
            target["findings"].extend(findings)
            target["finding_rejected"].extend(finding_rejected)
            pending = payload.get("pending_tasks", [])
            if not isinstance(pending, Sequence) or isinstance(pending, (str, bytes)):
                raise ValueError("pending_tasks must be a list")
            target["pending_tasks"].extend(_plain(item) for item in pending if isinstance(item, Mapping))
        calls: list[FrozenModelCall] = []
        paper = {**_plain(context.paper), "_recognition_profile": _plain(context.experiment_profile)}
        for branch in BRANCHES:
            for chunk_index, chunk in enumerate(context.chunks, start=1):
                chunk_state = state[branch]["chunks"][chunk_index - 1]
                reasons = _coverage_gap_reasons(chunk, chunk_state)
                chunk_state["coverage_gap_reasons"] = list(reasons)
                chunk_state["coverage_incomplete_reasons"] = list(reasons)
                if not reasons:
                    continue
                existing = chunk_state["candidates"]
                calls.append(FrozenModelCall.create(
                    call_id=f"{branch}-chunk-{chunk_index}-focus-{len(context.focuses) + 1}",
                    task="extraction",
                    messages=_coverage_gap_messages(
                        paper, [_plain(page) for page in chunk], existing,
                        learning_guidance=context.learning_guidance,
                    ),
                    max_tokens=COVERAGE_GAP_MAX_TOKENS_PER_CALL,
                    options={"thinking": False, "temperature": 0.1 if branch == "a" else 0.45},
                ))
        if calls:
            return PlannedLiteratureStage("coverage_gap", state, tuple(calls), _fingerprint(state))
        return self._plan_verification(context, state)

    def _coverage(self, context: LiteratureStageContext, raw_results) -> PlannedLiteratureStage:
        state = _latest(context, "initial_focus")
        for call, payload in zip(context.stage.calls, raw_results, strict=True):
            _assert_extraction_payload_bounds(payload)
            branch, chunk_index, pass_index = _call_parts(call.call_id)
            chunk = _chunk(context, chunk_index)
            candidates, rejected = _validated_candidates(
                _plain(payload), {int(page["page"]) for page in chunk}, chunk_index, pass_index
            )
            candidates, mode_rejected = _apply_document_mode_guard(
                candidates, _plain(context.experiment_profile)
            )
            findings, finding_rejected = _validated_findings(
                _plain(payload), chunk, chunk_index, pass_index
            )
            target = state[branch]["chunks"][chunk_index - 1]
            for item in candidates:
                item["extraction_pass"] = pass_index
                item["extraction_focus"] = "coverage_gap_audit"
            target["candidates"].extend(candidates)
            target["rejected"].extend(rejected + mode_rejected)
            target["findings"].extend(findings)
            target["finding_rejected"].extend(finding_rejected)
            pending = payload.get("pending_tasks", [])
            if not isinstance(pending, Sequence) or isinstance(pending, (str, bytes)):
                raise ValueError("pending_tasks must be a list")
            target["pending_tasks"].extend(_plain(item) for item in pending if isinstance(item, Mapping))
        for branch in BRANCHES:
            for chunk_index, chunk_map in enumerate(state[branch]["chunks"], start=1):
                chunk_map["coverage_incomplete_reasons"] = list(
                    _coverage_gap_reasons(_chunk(context, chunk_index), chunk_map)
                )
        return self._plan_verification(context, state)

    def _plan_verification(
        self, context: LiteratureStageContext, state: dict[str, Any]
    ) -> PlannedLiteratureStage:
        calls: list[FrozenModelCall] = []
        batch_map: list[dict[str, Any]] = []
        all_pages = [_plain(page) for page in context.pages]
        for branch in BRANCHES:
            for chunk_index, chunk_map in enumerate(state[branch]["chunks"], start=1):
                chunk = _chunk(context, chunk_index)
                page_by_number = {int(page["page"]): str(page["text"]) for page in chunk}
                local_passed: list[dict[str, Any]] = []
                for item in chunk_map["candidates"]:
                    item["local_evidence"] = _evidence_check(
                        item, page_by_number[int(item["source_page"])]
                    )
                    if item["local_evidence"]["passed"]:
                        local_passed.append(item)
                batches = _verification_batches(local_passed)
                selected = batches[:MAX_VERIFICATION_BATCHES_PER_BRANCH_BLOCK]
                overflow = [
                    item
                    for batch in batches[MAX_VERIFICATION_BATCHES_PER_BRANCH_BLOCK:]
                    for item in batch
                ]
                chunk_map["verification_overflow_candidate_ids"] = [
                    item["candidate_id"] for item in overflow
                ]
                if overflow:
                    chunk_map["pending_tasks"].append({
                        "task_type": "ambiguous_condition",
                        "description": "候选数量超过自动核验预算，剩余记录未完成 AI 核验",
                        "locator": f"PDF pages {chunk[0]['page']}-{chunk[-1]['page']}",
                    })
                for batch_index, batch in enumerate(selected, start=1):
                    calls.append(FrozenModelCall.create(
                        call_id=f"{branch}-chunk-{chunk_index}-verify-{batch_index}",
                        task="verification",
                        messages=_verification_messages(all_pages, batch),
                        max_tokens=VERIFICATION_MAX_TOKENS_PER_CALL,
                        options={"thinking": False, "temperature": 0.0},
                    ))
                    batch_map.append({
                        "branch": branch,
                        "chunk_index": chunk_index,
                        "candidate_ids": [item["candidate_id"] for item in batch],
                    })
        state["verification_batches"] = batch_map
        return PlannedLiteratureStage(
            "coverage_verification", state, tuple(calls), _fingerprint(state)
        )

    def _verification(self, context: LiteratureStageContext, raw_results) -> PlannedLiteratureStage:
        if any(item.stage == "coverage_gap" for item in context.prior_outputs):
            state = _latest(context, "coverage_gap")
        else:
            state = _latest(context, "initial_focus")
        batch_map = state.pop("verification_batches", [])
        if len(batch_map) != len(raw_results):
            raise ValueError("verification batch count mismatch")
        for batch_meta, payload in zip(batch_map, raw_results, strict=True):
            if not isinstance(payload, Mapping) or not isinstance(payload.get("verdicts"), Sequence):
                raise ValueError("verification payload must contain verdicts")
            verdicts = {
                str(item.get("candidate_id")): _plain(item)
                for item in payload["verdicts"]
                if isinstance(item, Mapping) and item.get("verdict") in VERDICTS
            }
            expected = set(batch_meta["candidate_ids"])
            if set(verdicts) != expected:
                raise ValueError("verification payload must cover each candidate exactly once")
            for chunk_map in state[batch_meta["branch"]]["chunks"]:
                for item in chunk_map["candidates"]:
                    if item["candidate_id"] in expected:
                        item["ai_verification"] = verdicts[item["candidate_id"]]
        for branch in BRANCHES:
            verified = []
            findings = []
            overflow_candidates = []
            for chunk_map in state[branch]["chunks"]:
                overflow_ids = set(
                    chunk_map.get("verification_overflow_candidate_ids") or []
                )
                for item in chunk_map["candidates"]:
                    if item["candidate_id"] in overflow_ids:
                        item["ai_verification"] = {
                            "candidate_id": item["candidate_id"],
                            "verdict": "unsupported",
                            "reason": "超出自动核验预算，AI 核验未通过，未发布",
                        }
                        overflow_candidates.append(item)
                    else:
                        item.setdefault("ai_verification", {
                            "candidate_id": item["candidate_id"],
                            "verdict": "unsupported",
                            "reason": item["local_evidence"]["reason"],
                        })
                    if (
                        item["candidate_id"] not in overflow_ids
                        and item["local_evidence"]["passed"]
                        and item["ai_verification"]["verdict"] == "supported"
                    ):
                        verified.append(item)
                findings.extend(chunk_map["findings"])
            state[branch]["verified_candidates"] = _deduplicate(verified)
            state[branch]["qualitative_findings"] = _deduplicate_findings(findings)
            state[branch]["verification_overflow_candidates"] = _deduplicate(
                overflow_candidates
            )
        assets, calls = plan_visual_review(context)
        state["visual_assets"] = assets
        return PlannedLiteratureStage(
            "adversarial_branches", state, calls, _fingerprint(state)
        )

    def _adversarial(self, context: LiteratureStageContext, raw_results) -> PlannedLiteratureStage:
        state = _latest(context, "coverage_verification")
        records = self._compare(state)
        records.extend(compare_visual_review(
            state.get("visual_assets", []), context.stage.calls, raw_results,
            threshold=self._threshold,
        ))
        coverage_incomplete = any(
            chunk_map.get("coverage_incomplete_reasons")
            for branch in BRANCHES
            for chunk_map in state[branch]["chunks"]
        )
        low_records = [
            record for record in records
            if record["gate_status"] == "manual_review"
            and (record["entity_type"] in {"figure", "table"} or not _requires_human_localization(record))
        ]
        pages = {int(page["page"]): str(page["text"]) for page in context.pages}
        review_batches = [
            low_records[start:start + THIRD_REVIEW_BATCH_SIZE]
            for start in range(0, len(low_records), THIRD_REVIEW_BATCH_SIZE)
        ][:MAX_THIRD_REVIEW_CALLS_PER_TASK]
        calls = tuple(
            FrozenModelCall.create(
                call_id=f"third-review-{index + 1}",
                task="verification",
                messages=_third_review_messages(batch, pages),
                max_tokens=THIRD_REVIEW_MAX_TOKENS_PER_CALL,
                options={"thinking": False, "temperature": 0.0},
            )
            for index, batch in enumerate(review_batches)
        )
        result = (
            {
                "records": records,
                "threshold": self._threshold,
                "coverage_incomplete": coverage_incomplete,
            }
            if calls else self._validated_result(
                records, coverage_incomplete=coverage_incomplete
            )
        )
        next_stage = "third_review" if calls else "validated"
        return PlannedLiteratureStage(next_stage, result, calls, _fingerprint(result))

    def _third(self, context: LiteratureStageContext, raw_results) -> PlannedLiteratureStage:
        state = _latest(context, "adversarial_branches")
        records = state["records"]
        low_records = [
            record for record in records
            if record["gate_status"] == "manual_review"
            and (record["entity_type"] in {"figure", "table"} or not _requires_human_localization(record))
        ][:MAX_THIRD_REVIEW_CALLS_PER_TASK * THIRD_REVIEW_BATCH_SIZE]
        _apply_third_review_payloads(
            low_records, [_plain(item) for item in raw_results], self._threshold
        )
        result = self._validated_result(
            records,
            coverage_incomplete=bool(state.get("coverage_incomplete")),
        )
        return PlannedLiteratureStage("validated", result, (), _fingerprint(result))

    def _compare(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for entity_type, field, scorer in (
            ("data", "verified_candidates", _score_data_pair),
            ("finding", "qualitative_findings", _score_finding_pair),
        ):
            left = state["a"][field]
            right = state["b"][field]
            pairs, used_left, used_right = _pair_candidates(left, right, scorer)
            for left_index, right_index, scored in pairs:
                record = _make_record(
                    entity_type, left[left_index], right[right_index],
                    float(scored["score"]), "extractor_a", self._threshold,
                )
                self._enforce_human_localization(record)
                records.append(record)
            for index, candidate in enumerate(left):
                if index not in used_left:
                    record = _make_record(
                        entity_type, candidate, None, 0.0, "extractor_a", self._threshold
                    )
                    self._enforce_human_localization(record)
                    records.append(record)
            for index, candidate in enumerate(right):
                if index not in used_right:
                    record = _make_record(
                        entity_type, candidate, None, 0.0, "extractor_b", self._threshold
                    )
                    self._enforce_human_localization(record)
                    records.append(record)
        for branch in BRANCHES:
            for candidate in state[branch].get(
                "verification_overflow_candidates", []
            ):
                record = _make_record(
                    "data", candidate, None, 0.0, f"extractor_{branch}",
                    self._threshold,
                )
                record["gate_status"] = "manual_review"
                record["gate_reason"] = "超出自动核验预算，AI 核验未通过，未发布"
                records.append(record)
        return records

    @staticmethod
    def _enforce_human_localization(record: dict[str, Any]) -> None:
        if _requires_human_localization(record):
            record["gate_status"] = "manual_review"
            record["gate_reason"] = "中文物理意义或实验条件缺失，AI 核验未通过，未发布"

    def _validated_result(
        self,
        records: list[dict[str, Any]],
        *,
        coverage_incomplete: bool = False,
    ) -> dict[str, Any]:
        allowed = {"dual_pass", "third_pass", "manual_review"}
        if any(record.get("gate_status") not in allowed for record in records):
            raise ValueError("quality result contains an unsupported gate")
        for record in records:
            if record["gate_status"] == "manual_review":
                record["gate_status"] = "ai_unresolved"
        visual_records = [record for record in records if record["entity_type"] in {"figure", "table"}]
        records = [record for record in records if record["entity_type"] in {"data", "finding"}]
        counts = {status: sum(record["gate_status"] == status for record in records) for status in ("dual_pass", "third_pass", "ai_unresolved")}
        return {
            "visual_records": visual_records,
            "review_policy": "automatic-ai-v1",
            "schema_version": "literature-extraction-validated-v1",
            "coverage": {
                "numeric_items": not coverage_incomplete,
                "qualitative_findings": not coverage_incomplete,
                "visual_evidence_ready": False,
                "atomic_commit_ready": False,
            },
            "records": records,
            "summary": {
                "candidate_count": len(records),
                "dual_pass_count": counts["dual_pass"],
                "third_pass_count": counts["third_pass"],
                "manual_review_count": counts["ai_unresolved"],
                "quality_threshold": self._threshold,
            },
        }


__all__ = ["ExistingLiteratureStagePlanner"]
