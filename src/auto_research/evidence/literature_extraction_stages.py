from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from .deepseek_extraction import (
    LOCALIZATION_BATCH_SIZE,
    VERDICTS,
    _apply_document_mode_guard,
    _apply_localization_payload,
    _coverage_gap_messages,
    _deduplicate,
    _deduplicate_findings,
    _evidence_check,
    _localization_messages,
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
from .quality_pipeline import (
    DEFAULT_THRESHOLD,
    _apply_third_review_payloads,
    _make_record,
    _pair_candidates,
    _score_data_pair,
    _score_finding_pair,
    _third_review_messages,
)


BRANCHES = ("a", "b")


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
                item["extraction_focus"] = context.focuses[pass_index - 1]
            target["candidates"].extend(candidates)
            target["rejected"].extend(rejected + mode_rejected)
            target["findings"].extend(findings)
            target["finding_rejected"].extend(finding_rejected)
            pending = payload.get("pending_tasks", [])
            if not isinstance(pending, Sequence) or isinstance(pending, (str, bytes)):
                raise ValueError("pending_tasks must be a list")
            target["pending_tasks"].extend(_plain(item) for item in pending if isinstance(item, Mapping))
        calls = []
        paper = {**_plain(context.paper), "_recognition_profile": _plain(context.experiment_profile)}
        for branch in BRANCHES:
            for chunk_index, chunk in enumerate(context.chunks, start=1):
                existing = state[branch]["chunks"][chunk_index - 1]["candidates"]
                calls.append(FrozenModelCall.create(
                    call_id=f"{branch}-chunk-{chunk_index}-focus-{len(context.focuses) + 1}",
                    task="extraction",
                    messages=_coverage_gap_messages(
                        paper, [_plain(page) for page in chunk], existing,
                        learning_guidance=context.learning_guidance,
                    ),
                    max_tokens=12_000,
                    options={"thinking": False, "temperature": 0.1 if branch == "a" else 0.45},
                ))
        return PlannedLiteratureStage("coverage_gap", state, tuple(calls), _fingerprint(state))

    def _coverage(self, context: LiteratureStageContext, raw_results) -> PlannedLiteratureStage:
        state = _latest(context, "initial_focus")
        for call, payload in zip(context.stage.calls, raw_results, strict=True):
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
        calls: list[FrozenModelCall] = []
        batch_map: list[dict[str, Any]] = []
        for branch in BRANCHES:
            for chunk_index, chunk_map in enumerate(state[branch]["chunks"], start=1):
                chunk = _chunk(context, chunk_index)
                page_by_number = {int(page["page"]): str(page["text"]) for page in chunk}
                local_passed = []
                for item in chunk_map["candidates"]:
                    item["local_evidence"] = _evidence_check(
                        item, page_by_number[int(item["source_page"])]
                    )
                    if item["local_evidence"]["passed"]:
                        local_passed.append(item)
                for batch_index, batch in enumerate(_verification_batches(local_passed), start=1):
                    calls.append(FrozenModelCall.create(
                        call_id=f"{branch}-verify-{chunk_index}-{batch_index}",
                        task="verification",
                        messages=_verification_messages(chunk, batch),
                        max_tokens=4_000,
                        options={"thinking": False, "temperature": 0.0},
                    ))
                    batch_map.append({
                        "branch": branch,
                        "chunk_index": chunk_index,
                        "candidate_ids": [item["candidate_id"] for item in batch],
                    })
        state["verification_batches"] = batch_map
        if not calls:
            # Preserve a deterministic verifier boundary without a paid call by
            # advancing through the local-capable stage with an empty plan.
            calls = []
        return PlannedLiteratureStage(
            "coverage_verification", state, tuple(calls), _fingerprint(state)
        )

    def _verification(self, context: LiteratureStageContext, raw_results) -> PlannedLiteratureStage:
        state = _latest(context, "coverage_gap")
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
            candidates = state[batch_meta["branch"]]["chunks"][batch_meta["chunk_index"] - 1]["candidates"]
            for item in candidates:
                if item["candidate_id"] in expected:
                    item["ai_verification"] = verdicts[item["candidate_id"]]
        calls: list[FrozenModelCall] = []
        localization_map: list[dict[str, Any]] = []
        for branch in BRANCHES:
            verified = []
            findings = []
            for chunk_map in state[branch]["chunks"]:
                for item in chunk_map["candidates"]:
                    item.setdefault("ai_verification", {
                        "candidate_id": item["candidate_id"], "verdict": "unsupported",
                        "reason": item["local_evidence"]["reason"],
                    })
                    if item["local_evidence"]["passed"] and item["ai_verification"]["verdict"] == "supported":
                        verified.append(item)
                findings.extend(chunk_map["findings"])
            state[branch]["verified_candidates"] = _deduplicate(verified)
            state[branch]["qualitative_findings"] = _deduplicate_findings(findings)
            needs_translation = [
                item for item in state[branch]["verified_candidates"]
                if not (
                    re.search(r"[\u4e00-\u9fff]", str(item.get("meaning") or ""))
                    and re.search(r"[\u4e00-\u9fff]", str(item.get("context_explanation") or ""))
                )
            ]
            for batch_index, batch in enumerate(
                _verification_batches(needs_translation, LOCALIZATION_BATCH_SIZE), start=1
            ):
                calls.append(FrozenModelCall.create(
                    call_id=f"{branch}-localize-{batch_index}",
                    task="localization",
                    messages=_localization_messages(batch),
                    max_tokens=8_000,
                    options={"thinking": False, "temperature": 0.0},
                ))
                localization_map.append({
                    "branch": branch,
                    "candidate_ids": [item["candidate_id"] for item in batch],
                })
        state["localization_batches"] = localization_map
        return PlannedLiteratureStage(
            "adversarial_branches", state, tuple(calls), _fingerprint(state)
        )

    def _adversarial(self, context: LiteratureStageContext, raw_results) -> PlannedLiteratureStage:
        state = _latest(context, "coverage_verification")
        batch_map = state.pop("localization_batches", [])
        if len(batch_map) != len(raw_results):
            raise ValueError("localization response count mismatch")
        for batch_meta, payload in zip(batch_map, raw_results, strict=True):
            ids = set(batch_meta["candidate_ids"])
            batch = [
                item for item in state[batch_meta["branch"]]["verified_candidates"]
                if item["candidate_id"] in ids
            ]
            _apply_localization_payload(batch, _plain(payload))
        records = self._compare(state)
        low_records = [record for record in records if record["gate_status"] == "manual_review"]
        pages = {int(page["page"]): str(page["text"]) for page in context.pages}
        calls = tuple(
            FrozenModelCall.create(
                call_id=f"third-review-{index + 1}",
                task="verification",
                messages=_third_review_messages(batch, pages),
                max_tokens=5_000,
                options={"thinking": False, "temperature": 0.0},
            )
            for index, batch in enumerate(
                [low_records[start:start + 10] for start in range(0, len(low_records), 10)]
            )
        )
        result = (
            {"records": records, "threshold": self._threshold}
            if calls else self._validated_result(records)
        )
        next_stage = "third_review" if calls else "validated"
        return PlannedLiteratureStage(next_stage, result, calls, _fingerprint(result))

    def _third(self, context: LiteratureStageContext, raw_results) -> PlannedLiteratureStage:
        state = _latest(context, "adversarial_branches")
        records = state["records"]
        low_records = [record for record in records if record["gate_status"] == "manual_review"]
        _apply_third_review_payloads(
            low_records, [_plain(item) for item in raw_results], self._threshold
        )
        result = self._validated_result(records)
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
                records.append(_make_record(
                    entity_type, left[left_index], right[right_index],
                    float(scored["score"]), "extractor_a", self._threshold,
                ))
            for index, candidate in enumerate(left):
                if index not in used_left:
                    records.append(_make_record(
                        entity_type, candidate, None, 0.0, "extractor_a", self._threshold
                    ))
            for index, candidate in enumerate(right):
                if index not in used_right:
                    records.append(_make_record(
                        entity_type, candidate, None, 0.0, "extractor_b", self._threshold
                    ))
        return records

    def _validated_result(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        allowed = {"dual_pass", "third_pass", "manual_review"}
        if any(record.get("gate_status") not in allowed for record in records):
            raise ValueError("quality result contains an unsupported gate")
        counts = {status: sum(record["gate_status"] == status for record in records) for status in allowed}
        return {
            "schema_version": "literature-extraction-validated-v1",
            "coverage": {
                "numeric_items": True,
                "qualitative_findings": True,
                "visual_evidence_ready": False,
                "atomic_commit_ready": False,
            },
            "records": records,
            "summary": {
                "candidate_count": len(records),
                "dual_pass_count": counts["dual_pass"],
                "third_pass_count": counts["third_pass"],
                "manual_review_count": counts["manual_review"],
                "quality_threshold": self._threshold,
            },
        }


__all__ = ["ExistingLiteratureStagePlanner"]
