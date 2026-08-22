"""Independent, DTO-only scientific accuracy audit for release decisions.

This module deliberately knows nothing about the application database, model
runtime, extraction pipeline, or software test results.  A caller must supply
an explicitly human-adjudicated gold set and the public candidate records that
are to be measured against it.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence


REQUEST_SCHEMA = "scientific-release-audit-request-v1"
RESULT_SCHEMA = "scientific-release-audit-v1"
GOLD_SCHEMA = "scientific-gold-record-v1"
CANDIDATE_SCHEMA = "scientific-candidate-record-v1"

DIMENSIONS = (
    "numeric_value",
    "unit",
    "physical_meaning",
    "experimental_conditions",
    "table_discovery",
    "table_structure",
    "table_screenshot",
    "figure_discovery",
    "figure_caption",
    "figure_page",
    "figure_screenshot",
    "conclusion",
    "source_excerpt",
    "locator",
)

_ENTITY_TYPES = frozenset({"item", "table", "figure", "finding"})
_SPLITS = frozenset({"train", "validation", "test"})
_REQUEST_FIELDS = frozenset(
    {
        "schema_version",
        "gold_records",
        "candidate_records",
        "minimum_paper_coverage",
        "minimum_dimension_f1",
    }
)
_GOLD_FIELDS = frozenset(
    {
        "schema_version",
        "paper_uid",
        "entity_uid",
        "entity_type",
        "split",
        "human_reviewed",
        "judgments",
    }
)
_CANDIDATE_FIELDS = frozenset(
    {"schema_version", "paper_uid", "entity_uid", "entity_type", "split", "values"}
)
_JUDGMENT_FIELDS = frozenset({"adjudicated", "value"})
_APPLICABLE_TYPES = MappingProxyType(
    {
        "numeric_value": frozenset({"item"}),
        "unit": frozenset({"item"}),
        "physical_meaning": frozenset({"item"}),
        "experimental_conditions": frozenset({"item"}),
        "table_discovery": frozenset({"table"}),
        "table_structure": frozenset({"table"}),
        "table_screenshot": frozenset({"table"}),
        "figure_discovery": frozenset({"figure"}),
        "figure_caption": frozenset({"figure"}),
        "figure_page": frozenset({"figure"}),
        "figure_screenshot": frozenset({"figure"}),
        "conclusion": frozenset({"finding"}),
        "source_excerpt": _ENTITY_TYPES,
        "locator": _ENTITY_TYPES,
    }
)
_BOOLEAN_DIMENSIONS = frozenset({"table_discovery", "figure_discovery"})
_SCREENSHOT_DIMENSIONS = frozenset({"table_screenshot", "figure_screenshot"})
_TEXT_DIMENSIONS = frozenset(
    {"unit", "physical_meaning", "figure_caption", "conclusion", "source_excerpt"}
)
_MAX_RECORDS = 100_000
_MAX_TEXT = 16_000
_MAX_NODES = 10_000
_MAX_DEPTH = 12
_MAX_ERROR_CASES = 500
_SENSITIVE_KEYS = frozenset(
    {
        "path",
        "file_path",
        "source_path",
        "pdf_path",
        "api_key",
        "secret",
        "credential_ref",
        "token",
        "nonce",
        "session_id",
        "db_id",
        "database_id",
        "run_id",
        "draft_id",
        "file_id",
        "import_id",
        "selection_id",
    }
)
_LOCAL_VALUE_RE = re.compile(
    r"(?:^|[\s=:'\"])(?:~[/\\]|/(?:Users|home|private|tmp|var|etc|usr|root|srv|mnt|media|Applications|Library|System)(?:/|\\)|(?:file|sqlite):|[A-Za-z]:[\\/]|\\\\)",
    re.IGNORECASE,
)


_ERRORS = MappingProxyType(
    {
        "scientific_audit_invalid": "科学准确率审计输入无效。",
        "scientific_audit_duplicate_identity": "科学准确率审计包含重复实体身份。",
        "scientific_audit_split_leakage": "同一论文不能跨训练、验证或测试分区。",
        "scientific_audit_cross_paper_identity": "稳定实体身份不能映射到不同论文。",
        "scientific_audit_missing_judgment": "金标准缺少明确的人工判定。",
        "scientific_audit_unsafe_value": "科学准确率审计输入包含不允许公开的信息。",
    }
)


class ScientificReleaseAuditError(ValueError):
    """Stable, path-free validation error."""

    def __init__(self, code: str) -> None:
        if code not in _ERRORS:
            code = "scientific_audit_invalid"
        self.code = code
        self.safe_message = _ERRORS[code]
        super().__init__(self.safe_message)

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": "scientific-release-audit-error-v1",
            "code": self.code,
            "message": self.safe_message,
        }


@dataclass(frozen=True)
class _Record:
    paper_uid: str
    entity_uid: str
    entity_type: str
    split: str
    values: Mapping[str, object]

    @property
    def identity(self) -> tuple[str, str]:
        return self.paper_uid, self.entity_uid


@dataclass
class _Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    adjudicated: int = 0


def _finite_ratio(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScientificReleaseAuditError("scientific_audit_invalid")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ScientificReleaseAuditError("scientific_audit_invalid")
    return result


def _identifier(value: object) -> str:
    if not isinstance(value, str):
        raise ScientificReleaseAuditError("scientific_audit_invalid")
    result = value.strip()
    if not result or len(result) > 256 or _LOCAL_VALUE_RE.search(result):
        raise ScientificReleaseAuditError("scientific_audit_unsafe_value")
    return result


def _safe_value(value: object, *, depth: int = 0, nodes: list[int] | None = None) -> object:
    if nodes is None:
        nodes = [0]
    nodes[0] += 1
    if nodes[0] > _MAX_NODES or depth > _MAX_DEPTH:
        raise ScientificReleaseAuditError("scientific_audit_invalid")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise ScientificReleaseAuditError("scientific_audit_invalid")
        return value
    if isinstance(value, str):
        normalized = " ".join(value.split())
        if len(normalized) > _MAX_TEXT:
            raise ScientificReleaseAuditError("scientific_audit_invalid")
        if _LOCAL_VALUE_RE.search(normalized):
            raise ScientificReleaseAuditError("scientific_audit_unsafe_value")
        return normalized
    if isinstance(value, Mapping):
        output: dict[str, object] = {}
        if any(not isinstance(key, str) for key in value):
            raise ScientificReleaseAuditError("scientific_audit_invalid")
        for key in sorted(value):
            if len(key) > 256:
                raise ScientificReleaseAuditError("scientific_audit_invalid")
            if key.casefold() in _SENSITIVE_KEYS:
                raise ScientificReleaseAuditError("scientific_audit_unsafe_value")
            output[key] = _safe_value(value[key], depth=depth + 1, nodes=nodes)
        return output
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_safe_value(item, depth=depth + 1, nodes=nodes) for item in value]
    raise ScientificReleaseAuditError("scientific_audit_invalid")


def _validate_dimension_value(dimension: str, value: object) -> object:
    safe = _safe_value(value)
    if dimension in _BOOLEAN_DIMENSIONS and safe is not None and not isinstance(safe, bool):
        raise ScientificReleaseAuditError("scientific_audit_invalid")
    if dimension in _TEXT_DIMENSIONS and safe is not None and not isinstance(safe, str):
        raise ScientificReleaseAuditError("scientific_audit_invalid")
    if dimension == "figure_page" and safe is not None and (
        isinstance(safe, bool) or not isinstance(safe, int) or safe < 1
    ):
        raise ScientificReleaseAuditError("scientific_audit_invalid")
    if dimension in _SCREENSHOT_DIMENSIONS and safe is not None:
        if not isinstance(safe, Mapping) or set(safe) != {
            "present", "content_sha256", "width", "height"
        }:
            raise ScientificReleaseAuditError("scientific_audit_invalid")
        present = safe["present"]
        digest = safe["content_sha256"]
        width, height = safe["width"], safe["height"]
        if not isinstance(present, bool):
            raise ScientificReleaseAuditError("scientific_audit_invalid")
        if present:
            if (
                not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                or isinstance(width, bool)
                or not isinstance(width, int)
                or width < 1
                or isinstance(height, bool)
                or not isinstance(height, int)
                or height < 1
            ):
                raise ScientificReleaseAuditError("scientific_audit_invalid")
        elif digest is not None or width is not None or height is not None:
            raise ScientificReleaseAuditError("scientific_audit_invalid")
    if dimension == "locator" and safe is not None:
        if not isinstance(safe, Mapping) or set(safe) != {"page", "label", "bbox"}:
            raise ScientificReleaseAuditError("scientific_audit_invalid")
        page, label, bbox = safe["page"], safe["label"], safe["bbox"]
        if page is not None and (isinstance(page, bool) or not isinstance(page, int) or page < 1):
            raise ScientificReleaseAuditError("scientific_audit_invalid")
        if label is not None and not isinstance(label, str):
            raise ScientificReleaseAuditError("scientific_audit_invalid")
        if bbox is not None:
            if not isinstance(bbox, list) or len(bbox) != 4:
                raise ScientificReleaseAuditError("scientific_audit_invalid")
            if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in bbox):
                raise ScientificReleaseAuditError("scientific_audit_invalid")
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                raise ScientificReleaseAuditError("scientific_audit_invalid")
    return safe


def _parse_records(values: object, *, gold: bool) -> list[_Record]:
    if not isinstance(values, list) or len(values) > _MAX_RECORDS:
        raise ScientificReleaseAuditError("scientific_audit_invalid")
    records: list[_Record] = []
    identities: set[tuple[str, str]] = set()
    global_entities: dict[str, str] = {}
    paper_splits: dict[str, str] = {}
    expected_fields = _GOLD_FIELDS if gold else _CANDIDATE_FIELDS
    expected_schema = GOLD_SCHEMA if gold else CANDIDATE_SCHEMA
    for raw in values:
        if not isinstance(raw, Mapping) or set(raw) != expected_fields:
            raise ScientificReleaseAuditError("scientific_audit_invalid")
        if raw.get("schema_version") != expected_schema:
            raise ScientificReleaseAuditError("scientific_audit_invalid")
        paper_uid = _identifier(raw.get("paper_uid"))
        entity_uid = _identifier(raw.get("entity_uid"))
        entity_type = raw.get("entity_type")
        split = raw.get("split")
        if entity_type not in _ENTITY_TYPES or split not in _SPLITS:
            raise ScientificReleaseAuditError("scientific_audit_invalid")
        prior_split = paper_splits.setdefault(paper_uid, str(split))
        if prior_split != split:
            raise ScientificReleaseAuditError("scientific_audit_split_leakage")
        prior_paper = global_entities.setdefault(entity_uid, paper_uid)
        if prior_paper != paper_uid:
            raise ScientificReleaseAuditError("scientific_audit_cross_paper_identity")
        identity = (paper_uid, entity_uid)
        if identity in identities:
            raise ScientificReleaseAuditError("scientific_audit_duplicate_identity")
        identities.add(identity)

        values_by_dimension: dict[str, object] = {}
        if gold:
            if raw.get("human_reviewed") is not True:
                raise ScientificReleaseAuditError("scientific_audit_missing_judgment")
            judgments = raw.get("judgments")
            if not isinstance(judgments, Mapping) or set(judgments) != set(DIMENSIONS):
                raise ScientificReleaseAuditError("scientific_audit_missing_judgment")
            for dimension in DIMENSIONS:
                judgment = judgments[dimension]
                if (
                    not isinstance(judgment, Mapping)
                    or set(judgment) != _JUDGMENT_FIELDS
                    or judgment.get("adjudicated") is not True
                ):
                    raise ScientificReleaseAuditError("scientific_audit_missing_judgment")
                value = _validate_dimension_value(dimension, judgment.get("value"))
                if entity_type not in _APPLICABLE_TYPES[dimension] and value is not None:
                    raise ScientificReleaseAuditError("scientific_audit_invalid")
                values_by_dimension[dimension] = value
        else:
            candidate_values = raw.get("values")
            if not isinstance(candidate_values, Mapping) or set(candidate_values) != set(DIMENSIONS):
                raise ScientificReleaseAuditError("scientific_audit_invalid")
            for dimension in DIMENSIONS:
                value = _validate_dimension_value(dimension, candidate_values[dimension])
                if entity_type not in _APPLICABLE_TYPES[dimension] and value is not None:
                    raise ScientificReleaseAuditError("scientific_audit_invalid")
                values_by_dimension[dimension] = value
        records.append(
            _Record(
                paper_uid,
                entity_uid,
                str(entity_type),
                str(split),
                MappingProxyType(values_by_dimension),
            )
        )
    return records


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _positive(value: object) -> bool:
    return value not in (None, False, "", (), [], {})


def _equal(expected: object, actual: object) -> bool:
    return _canonical(expected) == _canonical(actual)


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def _metric(counts: _Counts) -> dict[str, object]:
    precision = _ratio(counts.tp, counts.tp + counts.fp)
    recall = _ratio(counts.tp, counts.tp + counts.fn)
    f1 = _ratio(2 * counts.tp, 2 * counts.tp + counts.fp + counts.fn)
    return {
        "tp": counts.tp,
        "fp": counts.fp,
        "fn": counts.fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "gold_adjudicated_records": counts.adjudicated,
    }


def _iou(left: Sequence[float], right: Sequence[float]) -> float:
    x1, y1 = max(left[0], right[0]), max(left[1], right[1])
    x2, y2 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = (left[2] - left[0]) * (left[3] - left[1])
    right_area = (right[2] - right[0]) * (right[3] - right[1])
    union = left_area + right_area - intersection
    return 0.0 if union <= 0 else intersection / union


def _thresholds(value: object) -> dict[str, float]:
    if isinstance(value, Mapping):
        if set(value) != set(DIMENSIONS):
            raise ScientificReleaseAuditError("scientific_audit_invalid")
        return {dimension: _finite_ratio(value[dimension], name=dimension) for dimension in DIMENSIONS}
    threshold = _finite_ratio(value, name="minimum_dimension_f1")
    return {dimension: threshold for dimension in DIMENSIONS}


def audit_scientific_release(payload: object) -> dict[str, object]:
    """Return a deterministic scientific-release-audit-v1 public DTO."""

    if not isinstance(payload, Mapping) or set(payload) != _REQUEST_FIELDS:
        raise ScientificReleaseAuditError("scientific_audit_invalid")
    if payload.get("schema_version") != REQUEST_SCHEMA:
        raise ScientificReleaseAuditError("scientific_audit_invalid")
    minimum_coverage = _finite_ratio(
        payload.get("minimum_paper_coverage"), name="minimum_paper_coverage"
    )
    thresholds = _thresholds(payload.get("minimum_dimension_f1"))
    gold = _parse_records(payload.get("gold_records"), gold=True)
    candidates = _parse_records(payload.get("candidate_records"), gold=False)

    gold_entities = {record.entity_uid: record.paper_uid for record in gold}
    gold_splits = {record.paper_uid: record.split for record in gold}
    for record in candidates:
        if record.entity_uid in gold_entities and gold_entities[record.entity_uid] != record.paper_uid:
            raise ScientificReleaseAuditError("scientific_audit_cross_paper_identity")
        if record.paper_uid in gold_splits and gold_splits[record.paper_uid] != record.split:
            raise ScientificReleaseAuditError("scientific_audit_split_leakage")

    gold_by_identity = {record.identity: record for record in gold}
    candidate_by_identity = {record.identity: record for record in candidates}
    counts = {dimension: _Counts() for dimension in DIMENSIONS}
    errors: list[dict[str, str]] = []
    locator_ious: list[float] = []
    locator_page_matches: list[bool] = []

    def add_error(record: _Record, dimension: str, kind: str) -> None:
        errors.append(
            {
                "paper_uid": record.paper_uid,
                "entity_uid": record.entity_uid,
                "entity_type": record.entity_type,
                "dimension": dimension,
                "error": kind,
            }
        )

    all_identities = sorted(set(gold_by_identity) | set(candidate_by_identity))
    for identity in all_identities:
        expected_record = gold_by_identity.get(identity)
        actual_record = candidate_by_identity.get(identity)
        if expected_record is not None and actual_record is not None:
            if expected_record.entity_type != actual_record.entity_type:
                raise ScientificReleaseAuditError("scientific_audit_invalid")
            if expected_record.split != actual_record.split:
                raise ScientificReleaseAuditError("scientific_audit_split_leakage")
        entity_type = (expected_record or actual_record).entity_type  # type: ignore[union-attr]
        for dimension in DIMENSIONS:
            if entity_type not in _APPLICABLE_TYPES[dimension]:
                continue
            dimension_counts = counts[dimension]
            if expected_record is not None:
                dimension_counts.adjudicated += 1
            expected = expected_record.values[dimension] if expected_record is not None else None
            actual = actual_record.values[dimension] if actual_record is not None else None
            expected_positive, actual_positive = _positive(expected), _positive(actual)
            if expected_positive and actual_positive and _equal(expected, actual):
                dimension_counts.tp += 1
            elif expected_positive:
                dimension_counts.fn += 1
                add_error(expected_record, dimension, "false_negative")  # type: ignore[arg-type]
                if actual_positive:
                    dimension_counts.fp += 1
                    add_error(actual_record, dimension, "false_positive")  # type: ignore[arg-type]
            elif actual_positive:
                dimension_counts.fp += 1
                add_error(actual_record, dimension, "false_positive")  # type: ignore[arg-type]

        if expected_record is not None:
            expected_locator = expected_record.values["locator"]
            actual_locator = (
                actual_record.values["locator"] if actual_record is not None else None
            )
            if isinstance(expected_locator, Mapping):
                if expected_locator.get("page") is not None:
                    locator_page_matches.append(
                        isinstance(actual_locator, Mapping)
                        and expected_locator["page"] == actual_locator.get("page")
                    )
                if expected_locator.get("bbox") is not None:
                    locator_ious.append(
                        _iou(expected_locator["bbox"], actual_locator["bbox"])
                        if isinstance(actual_locator, Mapping)
                        and actual_locator.get("bbox") is not None
                        else 0.0
                    )

    metrics = {dimension: _metric(counts[dimension]) for dimension in DIMENSIONS}
    gold_papers = sorted({record.paper_uid for record in gold})
    candidate_papers = sorted({record.paper_uid for record in candidates})
    matched_papers = sorted(set(gold_papers) & set(candidate_papers))
    paper_coverage = _ratio(len(matched_papers), len(gold_papers))
    matched_records = len(set(gold_by_identity) & set(candidate_by_identity))
    record_coverage = _ratio(matched_records, len(gold))

    reasons: list[str] = []
    if not gold:
        reasons.append("gold_empty")
    if paper_coverage is None or paper_coverage < minimum_coverage:
        reasons.append("paper_coverage_below_minimum")
    for dimension in DIMENSIONS:
        metric = metrics[dimension]
        if metric["gold_adjudicated_records"] == 0:
            reasons.append(f"gold_dimension_missing:{dimension}")
        elif metric["f1"] is None:
            reasons.append(f"dimension_f1_unavailable:{dimension}")
        elif metric["f1"] < thresholds[dimension]:
            reasons.append(f"dimension_f1_below_threshold:{dimension}")

    sorted_errors = sorted(
        errors,
        key=lambda row: (
            row["paper_uid"],
            row["entity_uid"],
            row["dimension"],
            row["error"],
        ),
    )
    return {
        "schema_version": RESULT_SCHEMA,
        "scientific_release_ready": not reasons,
        "reason_codes": sorted(reasons),
        "thresholds": {
            "minimum_paper_coverage": minimum_coverage,
            "minimum_dimension_f1": thresholds,
        },
        "coverage": {
            "gold_papers": len(gold_papers),
            "candidate_papers": len(candidate_papers),
            "matched_papers": len(matched_papers),
            "paper_coverage": paper_coverage,
            "gold_records": len(gold),
            "candidate_records": len(candidates),
            "matched_records": matched_records,
            "record_coverage": record_coverage,
        },
        "human_review": {
            "papers": len(gold_papers),
            "records": len(gold),
        },
        "dimensions": metrics,
        "locator_quality": {
            "mean_iou": _ratio(sum(locator_ious), len(locator_ious)),
            "page_consistency": _ratio(
                sum(1 for matched in locator_page_matches if matched),
                len(locator_page_matches),
            ),
            "iou_pairs": len(locator_ious),
            "page_pairs": len(locator_page_matches),
        },
        "error_cases": sorted_errors[:_MAX_ERROR_CASES],
        "error_case_count": len(sorted_errors),
        "error_cases_truncated": len(sorted_errors) > _MAX_ERROR_CASES,
        "claim_boundary": {
            "software_tests_are_scientific_accuracy": False,
            "ai_was_called": False,
            "curve_pixels_were_interpreted": False,
        },
    }


__all__ = [
    "CANDIDATE_SCHEMA",
    "DIMENSIONS",
    "GOLD_SCHEMA",
    "REQUEST_SCHEMA",
    "RESULT_SCHEMA",
    "ScientificReleaseAuditError",
    "audit_scientific_release",
]
