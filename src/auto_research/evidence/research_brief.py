from __future__ import annotations

import hashlib
import hmac
import html
import json
import math
import re
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "librarian-research-brief-v1"
EVIDENCE_TYPES = ("item", "finding", "table", "figure")
CONSTRAINT_FIELDS = (
    "material",
    "irradiation",
    "particle",
    "temperature",
    "dose",
    "property",
    "state",
)
CONSTRAINT_LABELS = {
    "material": "材料",
    "irradiation": "辐照类型",
    "particle": "粒子",
    "temperature": "温度",
    "dose": "剂量/注量",
    "property": "物理量",
    "state": "样品状态",
}
REPORT_SECTIONS = (
    "direct_conclusion",
    "evidence_matrix",
    "related_evidence",
    "database_gaps",
    "suggested_followups",
)

MAX_QUESTION_CHARS = 2_000
MAX_REPORT_TEXT_CHARS = 4_000
MAX_FIELD_CHARS = 800
MAX_TITLE_CHARS = 500
MAX_EXCERPT_CHARS = 1_000
MAX_DOI_CHARS = 200
MAX_LIST_ITEMS = 32
MAX_REPORT_ROWS = 32
MAX_CITED_EVIDENCE = 64
MAX_REFERENCE_OUTPUT = 128
MAX_STAT_COUNT = 1_000_000

_MARKDOWN_LINK_RE = re.compile(r"!?\[([^\]\r\n]*)\]\(\s*([^\r\n)]*)\s*\)")
_MARKDOWN_REFERENCE_IMAGE_RE = re.compile(
    r"!\[([^\]\r\n]*)\]\s*\[[^\]\r\n]*\]",
    re.IGNORECASE,
)
_MARKDOWN_REFERENCE_DEF_RE = re.compile(
    r"^\s*\[[^\]\r\n]{1,200}\]\s*:\s*\S.*$",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"(?:https?|file|ftp|sftp)://[^\s<>()\[\]{}]+", re.IGNORECASE)
_PROTOCOL_RELATIVE_URL_RE = re.compile(
    r"(?<![:\w])//[A-Za-z0-9.-]+(?::\d+)?(?:/[^\s<>()\[\]{}]*)?",
    re.IGNORECASE,
)
_MAILTO_RE = re.compile(r"\bmailto:[^\s<>()\[\]{}]+", re.IGNORECASE)
_TEL_RE = re.compile(r"\btel:[^\s<>()\[\]{}]+", re.IGNORECASE)
_BARE_WWW_RE = re.compile(r"\bwww\.[A-Za-z0-9.-]+(?:/[^\s<>()\[\]{}]*)?", re.IGNORECASE)
_BARE_EMAIL_RE = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])"
)
_DANGEROUS_URI_RE = re.compile(
    r"\b(?:javascript|data|vbscript|blob|cid):[^\s<>()\[\]{}]+",
    re.IGNORECASE,
)
_RAW_HTML_RE = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
_LOCAL_PATH_RE = re.compile(
    r"(?:"
    r"(?:~/(?:[^\s<>()\[\]{}]+))|"
    r"(?:(?:\.\./)+(?:[^\s<>()\[\]{}]+))|"
    r"(?:/(?:Users|Volumes|home|private|var|tmp|etc|opt|usr|root|bin|sbin|dev|proc|sys|run|srv|mnt|media|Applications|Library)"
    r"(?:/[^\s<>()\[\]{}]+)+)|"
    r"(?:[A-Za-z]:[\\/][^\s<>()\[\]{}]+)|"
    r"(?:\\\\[^\\\s<>()\[\]{}]+\\[^\s<>()\[\]{}]+)"
    r")",
    re.IGNORECASE,
)
_LOCAL_PATH_START_RE = re.compile(
    r"(?:"
    r"~/|"
    r"(?:\.\./)+|"
    r"/(?:Users|Volumes|home|private|var|tmp|etc|opt|usr|root|bin|sbin|dev|proc|sys|run|srv|mnt|media|Applications|Library)(?=/|\s|$)|"
    r"[A-Za-z]:[\\/]|"
    r"\\\\[^\\\s]+\\"
    r")",
    re.IGNORECASE,
)
_SECRET_RE = re.compile(
    r"(?:"
    r"\bsk-[A-Za-z0-9_-]{16,}\b|"
    r"\b(?:api[_ -]?key|access[_ -]?key|secret(?:[_ -]?key)?|token)"
    r"\s*[:=]\s*[A-Za-z0-9_./+=-]{12,}"
    r")",
    re.IGNORECASE,
)
_PROTOCOL_RE = re.compile(
    r"(?:\bDSML\b|<\|\|(?:invoke|tool|function)|\btool_calls\b\s*[:=：])",
    re.IGNORECASE,
)
_CITATION_REF_RE = re.compile(r"\[\s*[Rr][1-9]\d*\s*\]")
_RETRIEVAL_COUNT_RE = re.compile(
    r"(?:检索到|找到|召回|共)\s*[+\-−]?\d+(?:\.\d+)?\s*(?:条|项|个)"
    r"(?:候选|证据|结果|记录)?",
    re.IGNORECASE,
)
_SAFE_CROSS_BUNDLE_OVERVIEW_RE = re.compile(
    r"^(?:检索到|找到|召回|共)\s*[+\-−]?\d+(?:\.\d+)?\s*(?:条|项|个)\s*"
    r"(?:(?:满足全部硬条件的)?(?:直接|公开)?证据|候选|结果|记录)?"
    r"(?:[，,]\s*已按论文、材料和实验条件整理)?[。.!！]?$",
    re.IGNORECASE,
)
_SUPERSCRIPT_TRANSLATION = str.maketrans("⁺⁻⁰¹²³⁴⁵⁶⁷⁸⁹", "+-0123456789")
_SCIENTIFIC_SUPERSCRIPTS = frozenset("⁺⁻⁰¹²³⁴⁵⁶⁷⁸⁹")
_CJK_DISPLAY_PUNCTUATION = frozenset("，。；：！？、（）【】《》“”‘’")
_NUMBER_RE = re.compile(
    r"""
    (?<![A-Za-z0-9_.^*])
    (?<!\^-)(?<!\^\+)(?<!\*\*-)(?<!\*\*\+)
    (?P<coefficient>[+\-−]?(?:\d+(?:\.\d*)?|\.\d+))
    (?:
        \s*[eE]\s*(?P<e_exp>[+\-−]?\d+)
        |
        \s*[×xX*·⋅∙]\s*10\s*(?:\^|\*\*)?\s*
        (?P<times_exp>[+\-−]?\d+|[⁺⁻⁰¹²³⁴⁵⁶⁷⁸⁹]+)
    )?
    (?![A-Za-z0-9_.])
    """,
    re.VERBOSE,
)
_QUANTITY_UNIT_SUFFIX_RE = re.compile(
    r"""
    \s*
    (?:[*_~`]{1,3}\s*)?
    (?P<unit>
        (?:[°º]\s*[Cc]|(?:(?:degrees?|deg)\s*)?[Cc]elsius)
        |(?:at\.?\s*%|wt\.?\s*%|mol\.?\s*%|appm|ppm|%)
        |(?:
            (?:ions?|atoms?|neutrons?|electrons?|photons?)
            \s*(?:/|\s)\s*
            [A-Za-zµμÅΩ]+(?:\s*(?:\^|\*\*)?\s*[+\-−⁺⁻]?\s*(?:\d+|[⁰¹²³⁴⁵⁶⁷⁸⁹]+))?
        )
        |(?:
            [A-Za-zµμÅΩ]+
            (?:[/·⋅∙*][A-Za-z0-9µμÅΩ^+\-−⁺⁻⁰¹²³⁴⁵⁶⁷⁸⁹]+)*
            (?:\s*(?:\^|\*\*)?\s*[+\-−⁺⁻]\s*(?:\d+|[⁰¹²³⁴⁵⁶⁷⁸⁹]+))?
        )
    )
    (?=$|[\s,.!?;:，。！？；：)\]}>*_~`])
    """,
    re.IGNORECASE | re.VERBOSE,
)
_SNAPSHOT_TOKEN_RE = re.compile(r"rb1\.[0-9a-f]{64}\Z")

SNAPSHOT_SIGNATURE_FIELDS = (
    "question",
    "report",
    "query_analysis",
    "results",
    "model",
    "plan_mode",
    "summary_mode",
    "candidate_count",
    "cited_count",
    "match_counts",
    "bundle_count",
    "clarification_required",
    "response_format",
    "cache_hit",
    "answered_at",
    "evidence_version",
)


class ResearchBriefError(ValueError):
    """Raised when a Librarian response snapshot cannot form a brief."""


def _normalize_unicode_text(value: Any) -> str:
    """Normalize compatibility forms while preserving exponent superscripts."""

    normalized: list[str] = []
    for character in str(value or ""):
        if unicodedata.category(character) == "Cf":
            normalized.append(" ")
            continue
        if character in _SCIENTIFIC_SUPERSCRIPTS or character in _CJK_DISPLAY_PUNCTUATION:
            normalized.append(character)
            continue
        normalized.append(unicodedata.normalize("NFKC", character))
    return "".join(normalized)


def _normalize_and_unescape_text(value: Any) -> str:
    """Apply bounded entity decoding and compatibility normalization."""

    text = _normalize_unicode_text(value)
    for _ in range(3):
        decoded = _normalize_unicode_text(html.unescape(text))
        if decoded == text:
            break
        text = decoded
    return text


def _safe_text(value: Any, limit: int = MAX_FIELD_CHARS) -> str:
    if value is None:
        return ""
    text = _normalize_and_unescape_text(value).replace("\x00", " ")
    text = " ".join(text.split())
    if _MARKDOWN_REFERENCE_DEF_RE.fullmatch(text):
        return "[链接定义已移除]"
    # Preserve useful link labels but never preserve an active Markdown target.
    text = _MARKDOWN_LINK_RE.sub(lambda match: match.group(1) or "[链接已移除]", text)
    text = _MARKDOWN_REFERENCE_IMAGE_RE.sub(
        lambda match: match.group(1) or "[图片链接已移除]",
        text,
    )
    text = _URL_RE.sub("[链接已移除]", text)
    text = _PROTOCOL_RELATIVE_URL_RE.sub("[链接已移除]", text)
    text = _MAILTO_RE.sub("[链接已移除]", text)
    text = _TEL_RE.sub("[链接已移除]", text)
    text = _BARE_WWW_RE.sub("[链接已移除]", text)
    text = _BARE_EMAIL_RE.sub("[邮箱已移除]", text)
    text = _DANGEROUS_URI_RE.sub("[危险链接已移除]", text)
    # A local path may legally contain spaces. Once a known local prefix is
    # encountered, conservatively drop the rest of this bounded field rather
    # than risk leaking a suffix after a whitespace-delimited regex match.
    path_start = _LOCAL_PATH_START_RE.search(text)
    if path_start:
        text = text[:path_start.start()].rstrip() + " [本地路径已移除]"
    else:
        text = _LOCAL_PATH_RE.sub("[本地路径已移除]", text)
    text = _SECRET_RE.sub("[凭据已移除]", text)
    text = _RAW_HTML_RE.sub("[HTML已移除]", text)
    return text[:limit]


def _contains_internal_protocol(value: Any, *, depth: int = 0) -> bool:
    if depth > 8:
        return False
    if isinstance(value, str):
        return bool(_PROTOCOL_RE.search(_normalize_and_unescape_text(value)))
    if isinstance(value, Mapping):
        return any(
            _contains_internal_protocol(key, depth=depth + 1)
            or _contains_internal_protocol(item, depth=depth + 1)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return any(_contains_internal_protocol(item, depth=depth + 1) for item in value)
    return False


def _safe_multiline(value: Any, limit: int = MAX_REPORT_TEXT_CHARS) -> str:
    if value is None:
        return ""
    lines = [_safe_text(line, limit) for line in str(value).splitlines()]
    return "\n".join(line for line in lines if line)[:limit]


def _safe_doi(value: Any) -> str:
    doi = " ".join(str(value or "").replace("\x00", " ").split())
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.IGNORECASE)
    return _safe_text(doi, MAX_DOI_CHARS)


def _safe_page(value: Any) -> int | str | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 < value <= 100_000 else None
    text = _safe_text(value, 80)
    if not text:
        return None
    if text.isdigit():
        page = int(text)
        return page if 0 < page <= 100_000 else None
    return text


def _safe_count(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return min(max(number, 0), MAX_STAT_COUNT)


def _safe_string_list(value: Any, *, limit: int = MAX_LIST_ITEMS, chars: int = MAX_FIELD_CHARS) -> list[str]:
    if isinstance(value, str):
        values: Sequence[Any] = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = value
    else:
        return []
    cleaned: list[str] = []
    for item in values:
        text = _safe_text(item, chars)
        if text and text not in cleaned:
            cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned


def _safe_ref(value: Any) -> str:
    match = re.fullmatch(r"\s*[Rr]([1-9]\d*)\s*", str(value or ""))
    return f"R{int(match.group(1))}" if match else ""


def _ref_sort_key(ref: str) -> tuple[int, str]:
    match = re.fullmatch(r"R([1-9]\d*)", ref)
    return (int(match.group(1)), ref) if match else (MAX_STAT_COUNT + 1, ref)


def _safe_refs(value: Any, *, limit: int = MAX_REFERENCE_OUTPUT) -> list[str]:
    values = value if isinstance(value, list) else [value]
    refs = {_safe_ref(item) for item in values}
    refs.discard("")
    return sorted(refs, key=_ref_sort_key)[:limit]


def _clean_report(snapshot_report: Mapping[str, Any]) -> dict[str, Any]:
    conclusion = snapshot_report.get("direct_conclusion")
    conclusion = conclusion if isinstance(conclusion, Mapping) else {}
    clean_conclusion = {
        "status": _safe_text(conclusion.get("status"), 80),
        "text": _safe_multiline(conclusion.get("text")),
        "refs": _safe_refs(conclusion.get("refs") or []),
    }

    matrix: list[dict[str, Any]] = []
    raw_matrix = snapshot_report.get("evidence_matrix")
    if isinstance(raw_matrix, list):
        for row in raw_matrix[:MAX_REPORT_ROWS]:
            if not isinstance(row, Mapping):
                continue
            entity_type = _safe_text(row.get("entity_type"), 30).lower()
            clean = {
                "refs": _safe_refs(row.get("refs") or []),
                "bundle_id": _safe_text(row.get("bundle_id"), 100),
                "material": _safe_text(row.get("material")),
                "conditions": _safe_text(row.get("conditions")),
                "property": _safe_text(row.get("property")),
                "result": _safe_text(row.get("result")),
                "article_title": _safe_text(row.get("article_title"), MAX_TITLE_CHARS),
                "doi": _safe_doi(row.get("doi")),
                "source_page": _safe_page(row.get("source_page")),
                "entity_type": entity_type,
            }
            matrix.append(clean)
    matrix.sort(key=lambda row: (_ref_sort_key(row["refs"][0]) if row["refs"] else (MAX_STAT_COUNT + 1, ""), json.dumps(row, ensure_ascii=False, sort_keys=True)))

    related: list[dict[str, Any]] = []
    raw_related = snapshot_report.get("related_evidence")
    if isinstance(raw_related, list):
        for row in raw_related[:MAX_REPORT_ROWS]:
            if not isinstance(row, Mapping):
                continue
            related.append({
                "refs": _safe_refs(row.get("refs") or []),
                "summary": _safe_text(row.get("summary"), MAX_REPORT_TEXT_CHARS),
                "relaxed_constraints": _safe_string_list(row.get("relaxed_constraints")),
                "article_title": _safe_text(row.get("article_title"), MAX_TITLE_CHARS),
                "bundle_id": _safe_text(row.get("bundle_id"), 100),
            })
    related.sort(key=lambda row: (_ref_sort_key(row["refs"][0]) if row["refs"] else (MAX_STAT_COUNT + 1, ""), json.dumps(row, ensure_ascii=False, sort_keys=True)))

    return {
        "schema_version": "research-report-v1",
        "direct_conclusion": clean_conclusion,
        "evidence_matrix": matrix,
        "related_evidence": related,
        "database_gaps": _safe_string_list(
            snapshot_report.get("database_gaps"), limit=MAX_LIST_ITEMS, chars=MAX_REPORT_TEXT_CHARS
        ),
        "suggested_followups": _safe_string_list(
            snapshot_report.get("suggested_followups"), limit=MAX_LIST_ITEMS, chars=MAX_REPORT_TEXT_CHARS
        ),
    }


def _report_reference_occurrences(report: Mapping[str, Any]) -> list[str]:
    """Return only machine-readable references owned by report sections.

    Human prose may mention an R number while describing a gap or a suggested
    follow-up. Those mentions are not evidence citations and must never cause a
    result card to enter the exported appendix.
    """

    occurrences: list[str] = []
    conclusion = report.get("direct_conclusion")
    if isinstance(conclusion, Mapping):
        occurrences.extend(_safe_refs(conclusion.get("refs") or []))
    for section in ("evidence_matrix", "related_evidence"):
        rows = report.get(section)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            occurrences.extend(_safe_refs(row.get("refs") or []))
    return occurrences


def _report_duplicate_references(report: Mapping[str, Any]) -> list[str]:
    """Find duplicate refs inside one machine-readable ``refs`` list.

    Reusing one evidence card in the conclusion and the evidence matrix is
    expected. Only a duplicate inside the same structured list is malformed.
    """

    duplicates: set[str] = set()

    def inspect(value: Any) -> None:
        values = value if isinstance(value, list) else [value]
        refs = [_safe_ref(item) for item in values[:MAX_REFERENCE_OUTPUT]]
        refs = [ref for ref in refs if ref]
        duplicates.update(ref for ref in set(refs) if refs.count(ref) > 1)

    conclusion = report.get("direct_conclusion")
    if isinstance(conclusion, Mapping):
        inspect(conclusion.get("refs") or [])
    for section in ("evidence_matrix", "related_evidence"):
        rows = report.get(section)
        if not isinstance(rows, list):
            continue
        for row in rows[:MAX_REPORT_ROWS]:
            if isinstance(row, Mapping):
                inspect(row.get("refs") or [])
    return sorted(duplicates, key=_ref_sort_key)[:MAX_REFERENCE_OUTPUT]


def _hard_conditions(query_analysis: Any) -> list[dict[str, Any]]:
    if not isinstance(query_analysis, Mapping):
        return []
    constraints = query_analysis.get("constraints")
    if not isinstance(constraints, Mapping):
        return []
    output: list[dict[str, Any]] = []
    for field in CONSTRAINT_FIELDS:
        raw = constraints.get(field)
        if isinstance(raw, Mapping):
            values = _safe_string_list(raw.get("values"))
            mode = _safe_text(raw.get("mode"), 30).lower()
            label = _safe_text(raw.get("label"), 80) or CONSTRAINT_LABELS[field]
            if mode and mode != "hard":
                continue
        else:
            values = _safe_string_list(raw)
            label = CONSTRAINT_LABELS[field]
        if values:
            output.append({"field": field, "label": label, "values": values, "mode": "hard"})
    return output


def _evidence_type(row: Mapping[str, Any]) -> str:
    return _safe_text(row.get("agent_entity_type") or row.get("entity_type"), 30).lower()


def _clean_evidence(row: Mapping[str, Any], ref: str, entity_type: str) -> dict[str, Any]:
    source_page = row.get("source_page")
    if source_page in (None, ""):
        source_page = row.get("original_source_page")
    if source_page in (None, ""):
        source_page = row.get("page_start")
    source_locator = row.get("source_locator") or row.get("original_source_locator")
    source_excerpt = row.get("source_excerpt") or row.get("original_source_excerpt")
    if not source_excerpt and entity_type in {"table", "figure"}:
        source_excerpt = row.get("source_context")
    evidence: dict[str, Any] = {
        "ref": ref,
        "entity_type": entity_type,
        "article_title": _safe_text(row.get("article_title"), MAX_TITLE_CHARS),
        "doi": _safe_doi(row.get("doi")),
        "source_page": _safe_page(source_page),
        "source_locator": _safe_text(source_locator, 300),
        "source_excerpt": _safe_text(source_excerpt, MAX_EXCERPT_CHARS),
        "match_class": _safe_text(row.get("agent_match_class"), 30).lower(),
        "bundle_id": _safe_text(row.get("agent_bundle_id"), 100),
    }
    if entity_type == "item":
        evidence.update({
            "meaning": _safe_text(row.get("meaning")),
            "value_text": _safe_text(row.get("value_text")),
            "unit": _safe_text(row.get("unit"), 120),
            "context_explanation": _safe_text(row.get("context_explanation"), MAX_REPORT_TEXT_CHARS),
            "evidence_type": _safe_text(row.get("evidence_type"), 40),
        })
    elif entity_type == "finding":
        evidence.update({
            "meaning": _safe_text(row.get("meaning")),
            "finding_text": _safe_text(row.get("finding_text") or row.get("value_text"), MAX_REPORT_TEXT_CHARS),
            "context_explanation": _safe_text(row.get("context_explanation"), MAX_REPORT_TEXT_CHARS),
            "evidence_type": _safe_text(row.get("evidence_type"), 40),
        })
    else:
        evidence.update({
            "label": _safe_text(row.get("label"), 200),
            "display_name": _safe_text(row.get("display_name"), MAX_TITLE_CHARS),
            "caption": _safe_text(row.get("caption"), MAX_REPORT_TEXT_CHARS),
            "materials": _safe_string_list(row.get("materials")),
            "conditions_text": _safe_text(row.get("conditions_text"), MAX_REPORT_TEXT_CHARS),
            "methods_text": _safe_text(row.get("methods_text"), MAX_REPORT_TEXT_CHARS),
            "physical_quantities": _safe_string_list(row.get("physical_quantities")),
            "variables": _safe_string_list(row.get("variables")),
            "tags": _safe_string_list(row.get("tags")),
        })
    return evidence


def _canonical_evidence_key(row: Mapping[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def research_brief_snapshot_from_result(
    question: str,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the exact public snapshot signed by the HTTP service.

    This is intentionally a field projection, not a database lookup. The same
    projection is reconstructed by the browser before export.
    """

    if not isinstance(result, Mapping):
        raise ResearchBriefError("图书管理员响应必须是对象")
    return {
        "question": str(question or "").strip(),
        "report": result.get("report"),
        "query_analysis": result.get("query_analysis"),
        "results": result.get("results"),
        "model": result.get("model") or "",
        "plan_mode": result.get("plan_mode") or "",
        "summary_mode": result.get("summary_mode") or "",
        "candidate_count": result.get("candidate_count", 0),
        "cited_count": result.get("cited_count", 0),
        "match_counts": result.get("match_counts"),
        "bundle_count": result.get("bundle_count", 0),
        "clarification_required": bool(result.get("clarification_required")),
        "response_format": result.get("response_format") or "",
        "cache_hit": bool(result.get("cache_hit")),
        "answered_at": result.get("answered_at") or "",
        "evidence_version": result.get("evidence_version") or "",
    }


def _signature_material(snapshot: Mapping[str, Any]) -> bytes:
    if not isinstance(snapshot, Mapping):
        raise ResearchBriefError("研究简报签名快照必须是对象")
    payload = {
        field: _canonical_signature_value(snapshot.get(field))
        for field in SNAPSHOT_SIGNATURE_FIELDS
    }
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ResearchBriefError("研究简报快照包含不可签名字段") from exc
    return encoded.encode("utf-8")


def _canonical_signature_value(value: Any, *, depth: int = 0) -> Any:
    """Return an injective, JSON-serializable HMAC representation.

    Every JSON type has an explicit tag.  This prevents a caller-supplied
    object such as ``{"$number": "1"}`` from colliding with the number ``1``
    while retaining the intended browser equivalence of ``1``/``1.0`` and
    ``0``/``-0.0``.
    """

    if depth > 12:
        raise ResearchBriefError("研究简报签名快照嵌套过深")
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, int):
        return ["number", _canonical_decimal_text(Decimal(value))]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ResearchBriefError("研究简报签名快照包含非有限数值")
        return ["number", _canonical_decimal_text(Decimal(str(value)))]
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ResearchBriefError("研究简报签名对象的键必须是字符串")
        return [
            "object",
            [
                [key, _canonical_signature_value(value[key], depth=depth + 1)]
                for key in sorted(value)
            ],
        ]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return ["array", [
            _canonical_signature_value(item, depth=depth + 1) for item in value
        ]]
    raise ResearchBriefError("研究简报快照包含不可签名字段")


def _canonical_decimal_text(value: Decimal) -> str:
    """Encode a finite decimal as coefficient and base-10 exponent.

    Manual trailing-zero removal avoids Decimal context rounding and gives the
    same representation to mathematically equal JSON integers and floats.
    """

    if not value.is_finite():
        raise ResearchBriefError("研究简报签名快照包含非有限数值")
    if value.is_zero():
        return "0e0"
    sign, digits_tuple, exponent = value.as_tuple()
    digits = list(digits_tuple)
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    coefficient = "".join(str(digit) for digit in digits)
    return f"{'-' if sign else ''}{coefficient}e{exponent}"


def sign_research_brief_snapshot(snapshot: Mapping[str, Any], secret: bytes) -> str:
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise ResearchBriefError("研究简报签名密钥无效")
    digest = hmac.new(secret, _signature_material(snapshot), hashlib.sha256).hexdigest()
    return f"rb1.{digest}"


def verify_research_brief_snapshot(
    snapshot: Mapping[str, Any],
    token: Any,
    secret: bytes,
) -> bool:
    supplied = str(token or "")
    if not _SNAPSHOT_TOKEN_RE.fullmatch(supplied):
        return False
    expected = sign_research_brief_snapshot(snapshot, secret)
    return hmac.compare_digest(expected, supplied)


def _canonical_numeric_match(match: re.Match[str]) -> str:
    coefficient_text = match.group("coefficient").replace("−", "-")
    try:
        value = Decimal(coefficient_text)
        exponent_text = match.group("e_exp") or match.group("times_exp")
        if exponent_text is not None:
            exponent = int(exponent_text.translate(_SUPERSCRIPT_TRANSLATION).replace("−", "-"))
            # Scientific evidence does not need unbounded exponents.  Keep an
            # absurd value as an exact raw token rather than exercising a large
            # Decimal scale operation or silently dropping the claim.
            if abs(exponent) > 100_000:
                return "raw:" + re.sub(r"\s+", "", match.group(0)).casefold()
            value = value.scaleb(exponent)
        return _canonical_decimal_text(value)
    except (InvalidOperation, ValueError, OverflowError):
        return "raw:" + re.sub(r"\s+", "", match.group(0)).casefold()


def _normalize_quantitative_display_text(value: Any) -> str:
    """Remove bounded display markup without changing the scientific content."""

    text = _normalize_and_unescape_text(value).replace("\x00", " ")
    text = text.replace("\u00a0", " ")
    text = re.sub(
        r"\\(?:mathrm|text|operatorname)\s*\{([^{}\r\n]{1,80})\}",
        r"\1",
        text,
    )
    text = re.sub(r"\^\s*\{\s*([+\-−]?\d+)\s*\}", r"^\1", text)
    text = re.sub(r"\\(?:times|cdot|cdotp)\b", "×", text)
    text = re.sub(r"\\[,;:!]\s*", " ", text)
    text = text.replace(r"\(", " ").replace(r"\)", " ")
    text = text.replace(r"\[", " ").replace(r"\]", " ")
    # Preserve exponent notation before removing remaining Markdown emphasis.
    text = re.sub(r"(?<=10)\s*\*\*\s*(?=[+\-−]?\d)", "^", text)
    text = re.sub(r"[*_~`$]", " ", text)
    text = text.replace("{", " ").replace("}", " ")
    return " ".join(text.split())


def _quantitative_values(
    value: Any,
    *,
    ignore_retrieval_counts: bool = False,
) -> set[str]:
    """Extract complete numeric tokens, never JSON-line substrings.

    Structured R citations are removed first so ``[R1]`` is not mistaken for
    a scientific value.  Decimal canonicalization makes ``1e20`` equivalent to
    ``1 × 10^20`` while keeping it distinct from the standalone value ``1``.
    """

    text = _CITATION_REF_RE.sub(" ", _normalize_quantitative_display_text(value))
    if ignore_retrieval_counts:
        text = _RETRIEVAL_COUNT_RE.sub(" ", text)
    return {_canonical_numeric_match(match) for match in _NUMBER_RE.finditer(text)}


def _structured_text_refs(value: Any) -> set[str]:
    return {
        _safe_ref(match.group(0).strip("[] "))
        for match in _CITATION_REF_RE.finditer(str(value or ""))
    } - {""}


def _is_safe_cross_bundle_overview(value: Any) -> bool:
    text = _CITATION_REF_RE.sub(" ", _normalize_quantitative_display_text(value))
    text = " ".join(text.split())
    text = re.sub(r"\s+([。.!！])$", r"\1", text)
    return bool(_SAFE_CROSS_BUNDLE_OVERVIEW_RE.fullmatch(text))


def _canonical_quantity_unit(value: Any) -> str:
    """Normalize an explicit unit token; unknown unit-like tokens fail closed."""

    raw = _normalize_and_unescape_text(value).strip()
    raw = raw.strip("*_~`")
    raw = raw.rstrip(".,!?;:，。！？；：")
    raw = " ".join(raw.split())
    if not raw or len(raw) > 80:
        return ""
    if not re.search(r"[A-Za-zµμÅΩ°º%]", raw):
        return ""
    if raw.casefold() in {
        "a", "an", "and", "or", "the", "is", "are", "was", "were", "be",
        "at", "by", "for", "from", "in", "of", "on", "than", "to", "with",
    }:
        return ""
    unit = (
        raw
        .translate(_SUPERSCRIPT_TRANSLATION)
        .replace("−", "-")
        .replace("µ", "μ")
    )
    compact = re.sub(r"\s+", "", unit).casefold()
    if compact in {"°c", "ºc", "celsius", "degreecelsius", "degreescelsius", "degcelsius"}:
        return "degc"
    aliases = {
        "at.%": "at%",
        "wt.%": "wt%",
        "mol.%": "mol%",
        "angstrom": "å",
        "um": "μm",
        "ug": "μg",
        "us": "μs",
        "un": "μn",
        "ua": "μa",
    }
    compact = aliases.get(compact, compact)
    compact = re.sub(
        r"^(ions?|atoms?|neutrons?|electrons?|photons?)(?=[a-zμåω])",
        r"\1/",
        compact,
    )
    # Normalize cm⁻², cm−2 and cm^-2 without changing the base unit.
    compact = re.sub(r"([a-zåμω]+)([+\-]\d+)$", r"\1^\2", compact)
    return compact


def _quantitative_mentions(
    value: Any,
    *,
    ignore_retrieval_counts: bool = False,
) -> tuple[tuple[str, str], ...]:
    """Return ``(number, unit)`` mentions; an empty unit means dimensionless/unspecified."""

    text = _CITATION_REF_RE.sub(
        " ",
        _normalize_quantitative_display_text(value),
    )
    if ignore_retrieval_counts:
        text = _RETRIEVAL_COUNT_RE.sub(" ", text)
    mentions: list[tuple[str, str]] = []
    for match in _NUMBER_RE.finditer(text):
        suffix = _QUANTITY_UNIT_SUFFIX_RE.match(text, match.end())
        unit = _canonical_quantity_unit(suffix.group("unit")) if suffix else ""
        mentions.append((_canonical_numeric_match(match), unit))
    return tuple(mentions)


def _has_unsupported_quantitative_claim(
    claim: Any,
    evidence_values: Sequence[Any],
    *,
    structured_value_units: Sequence[tuple[Any, Any]] = (),
    ignore_retrieval_counts: bool = False,
) -> bool:
    """Require an explicit claim unit to match the same value and normalized unit."""

    claimed = _quantitative_mentions(
        claim,
        ignore_retrieval_counts=ignore_retrieval_counts,
    )
    if not claimed:
        return False
    supported_numbers: set[str] = set()
    supported_quantities: set[tuple[str, str]] = set()
    for value in evidence_values:
        for number, unit in _quantitative_mentions(value):
            supported_numbers.add(number)
            if unit:
                supported_quantities.add((number, unit))
    for raw_value, raw_unit in structured_value_units:
        unit = _canonical_quantity_unit(raw_unit)
        for number in _quantitative_values(raw_value):
            supported_numbers.add(number)
            if unit:
                supported_quantities.add((number, unit))
    return any(
        (number, unit) not in supported_quantities if unit else number not in supported_numbers
        for number, unit in claimed
    )


def _evidence_quantitative_values(row: Mapping[str, Any]) -> set[str]:
    entity_type = _safe_text(row.get("entity_type"), 30).lower()
    common_fields = ("source_excerpt",)
    if entity_type == "item":
        fields = common_fields + ("value_text", "unit", "context_explanation")
    elif entity_type == "finding":
        fields = common_fields + ("finding_text", "context_explanation")
    else:
        fields = common_fields + (
            "caption",
            "conditions_text",
            "methods_text",
            "physical_quantities",
            "variables",
            "tags",
        )
    output: set[str] = set()
    for field in fields:
        raw = row.get(field)
        values = raw if isinstance(raw, list) else [raw]
        for value in values:
            output.update(_quantitative_values(value))
    return output


def _evidence_quantitative_inputs(
    row: Mapping[str, Any],
) -> tuple[list[Any], list[tuple[Any, Any]]]:
    entity_type = _safe_text(row.get("entity_type"), 30).lower()
    common_fields = ("source_excerpt",)
    if entity_type == "item":
        fields = common_fields + ("value_text", "context_explanation")
        pairs = [(row.get("value_text"), row.get("unit"))]
    elif entity_type == "finding":
        fields = common_fields + ("finding_text", "context_explanation")
        pairs = []
    else:
        fields = common_fields + (
            "caption",
            "conditions_text",
            "methods_text",
            "physical_quantities",
            "variables",
            "tags",
        )
        pairs = []
    values: list[Any] = []
    for field in fields:
        raw = row.get(field)
        values.extend(raw if isinstance(raw, list) else [raw])
    return values, pairs


def _assert_quantitative_claim_supported(
    claim: Any,
    evidence_rows: Sequence[Mapping[str, Any]],
    *,
    label: str,
    ignore_retrieval_counts: bool = False,
) -> set[str]:
    claimed = _quantitative_values(
        claim,
        ignore_retrieval_counts=ignore_retrieval_counts,
    )
    if not claimed:
        return set()
    evidence_values: list[Any] = []
    structured_pairs: list[tuple[Any, Any]] = []
    for evidence in evidence_rows:
        values, pairs = _evidence_quantitative_inputs(evidence)
        evidence_values.extend(values)
        structured_pairs.extend(pairs)
    if _has_unsupported_quantitative_claim(
        claim,
        evidence_values,
        structured_value_units=structured_pairs,
        ignore_retrieval_counts=ignore_retrieval_counts,
    ):
        raise ResearchBriefError(f"{label}含有引用记录无法支持的数值")
    return claimed


def _assert_report_evidence_consistency(
    report: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
) -> None:
    by_ref = {str(row.get("ref")): row for row in selected}

    conclusion = report.get("direct_conclusion")
    conclusion = conclusion if isinstance(conclusion, Mapping) else {}
    conclusion_refs = _safe_refs(conclusion.get("refs") or [])
    undeclared_conclusion_refs = _structured_text_refs(conclusion.get("text")) - set(conclusion_refs)
    if undeclared_conclusion_refs:
        raise ResearchBriefError("直接结论正文含未在 refs 声明的引用")
    conclusion_evidence: list[Mapping[str, Any]] = []
    for ref in conclusion_refs:
        evidence = by_ref.get(ref)
        if not evidence or evidence.get("match_class") != "direct":
            raise ResearchBriefError(f"直接结论引用 {ref} 未绑定 direct 证据")
        conclusion_evidence.append(evidence)
    conclusion_numbers = _assert_quantitative_claim_supported(
        conclusion.get("text"),
        conclusion_evidence,
        label="直接结论",
        ignore_retrieval_counts=True,
    )
    conclusion_bundles = {
        _safe_text(evidence.get("bundle_id"), 100)
        for evidence in conclusion_evidence
    }
    conclusion_crosses_bundles = (
        bool(conclusion_bundles)
        and ("" in conclusion_bundles or len(conclusion_bundles) != 1)
    )
    if conclusion_crosses_bundles and not _is_safe_cross_bundle_overview(
        conclusion.get("text")
    ):
        raise ResearchBriefError("跨 evidence bundle 的直接结论只允许固定检索概览")

    matrix = report.get("evidence_matrix")
    if isinstance(matrix, list):
        for row in matrix:
            if not isinstance(row, Mapping):
                continue
            refs = _safe_refs(row.get("refs") or [])
            undeclared_related_refs = _structured_text_refs(row.get("summary")) - set(refs)
            if undeclared_related_refs:
                raise ResearchBriefError("相关证据正文含未在 refs 声明的引用")
            evidence_rows = [by_ref.get(ref) for ref in refs]
            if not refs or any(evidence is None for evidence in evidence_rows):
                raise ResearchBriefError("证据矩阵存在无法解析的结构化引用")
            expected_type = _safe_text(row.get("entity_type"), 30).lower()
            expected_bundle = _safe_text(row.get("bundle_id"), 100)
            bundles = {
                _safe_text(evidence.get("bundle_id"), 100)
                for evidence in evidence_rows
                if isinstance(evidence, Mapping)
            }
            if "" in bundles or len(bundles) != 1:
                raise ResearchBriefError("同一证据矩阵行必须绑定一个非空 evidence bundle")
            if not expected_bundle or bundles != {expected_bundle}:
                raise ResearchBriefError("证据矩阵的 bundle 与引用证据不一致")
            for evidence in evidence_rows:
                if not isinstance(evidence, Mapping):
                    raise ResearchBriefError("证据矩阵存在无法解析的引用记录")
                if evidence.get("match_class") != "direct":
                    raise ResearchBriefError("证据矩阵只能收录 direct 证据")
                if expected_type and evidence.get("entity_type") != expected_type:
                    raise ResearchBriefError("证据矩阵的证据类型与引用记录不一致")
                report_doi = _safe_doi(row.get("doi"))
                evidence_doi = _safe_doi(evidence.get("doi"))
                if report_doi and evidence_doi and report_doi.casefold() != evidence_doi.casefold():
                    raise ResearchBriefError("证据矩阵的 DOI 与引用记录不一致")

            _assert_quantitative_claim_supported(
                row.get("result"),
                [evidence for evidence in evidence_rows if isinstance(evidence, Mapping)],
                label="证据矩阵",
            )

    related = report.get("related_evidence")
    if isinstance(related, list):
        for row in related:
            if not isinstance(row, Mapping):
                continue
            refs = _safe_refs(row.get("refs") or [])
            expected_bundle = _safe_text(row.get("bundle_id"), 100)
            evidence_rows: list[Mapping[str, Any]] = []
            for ref in refs:
                evidence = by_ref.get(ref)
                if not evidence or evidence.get("match_class") != "adjacent":
                    raise ResearchBriefError(f"相关证据引用 {ref} 未绑定 adjacent 证据")
                evidence_rows.append(evidence)
                evidence_bundle = _safe_text(evidence.get("bundle_id"), 100)
                if expected_bundle and expected_bundle != evidence_bundle:
                    raise ResearchBriefError("相关证据的 bundle 与引用记录不一致")
            related_bundles = {
                _safe_text(evidence.get("bundle_id"), 100)
                for evidence in evidence_rows
            }
            related_numbers = _assert_quantitative_claim_supported(
                row.get("summary"),
                evidence_rows,
                label="相关证据",
            )
            related_crosses_bundles = (
                bool(related_bundles)
                and ("" in related_bundles or len(related_bundles) != 1)
            )
            if related_crosses_bundles and not _is_safe_cross_bundle_overview(
                row.get("summary")
            ):
                raise ResearchBriefError("跨 evidence bundle 的相关说明只允许固定检索概览")


def _generated_at(value: str | datetime | None) -> str:
    if value is None:
        value = datetime.now(timezone.utc).replace(microsecond=0)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    text = _safe_text(value, 100)
    if not text:
        raise ResearchBriefError("generated_at 不能为空")
    return text


def build_research_brief(
    snapshot: Mapping[str, Any] | None,
    *,
    generated_at: str | datetime | None = None,
    snapshot_binding: str = "unverified",
) -> dict[str, Any]:
    """Build a bounded, deterministic brief from one already-public Librarian response.

    The function is deliberately pure apart from the default timestamp. It does
    not open a database, read a PDF, or invoke a model. Pass ``generated_at`` to
    make repeated exports byte-for-byte deterministic.
    """

    if not isinstance(snapshot, Mapping) or not snapshot:
        raise ResearchBriefError("需要一次非空的图书管理员公开响应快照")
    if _contains_internal_protocol(snapshot):
        raise ResearchBriefError("图书管理员响应含内部工具协议文本，不能导出")
    question = _safe_text(snapshot.get("question"), MAX_QUESTION_CHARS)
    if not question:
        raise ResearchBriefError("快照缺少研究问题 question")
    raw_report = snapshot.get("report")
    if not isinstance(raw_report, Mapping):
        raise ResearchBriefError("快照缺少结构化五段报告 report")
    if raw_report.get("schema_version") != "research-report-v1":
        raise ResearchBriefError("研究简报只接受 research-report-v1 报告")
    raw_results = snapshot.get("results")
    if raw_results is None:
        raw_results = []
    if not isinstance(raw_results, list):
        raise ResearchBriefError("快照字段 results 必须是列表")
    answered_at = _safe_text(snapshot.get("answered_at"), 100)
    evidence_version = _safe_text(snapshot.get("evidence_version"), 200)
    if not answered_at or not evidence_version:
        raise ResearchBriefError("研究简报快照缺少原回答时间或证据版本")

    report = _clean_report(raw_report)
    conclusion = report.get("direct_conclusion")
    conclusion = conclusion if isinstance(conclusion, Mapping) else {}
    query_analysis = snapshot.get("query_analysis")
    query_analysis = query_analysis if isinstance(query_analysis, Mapping) else {}
    if (
        bool(snapshot.get("clarification_required"))
        or bool(query_analysis.get("needs_clarification"))
        or str(conclusion.get("status") or "").casefold() == "clarification"
    ):
        raise ResearchBriefError("需要补充条件的回答不能导出研究简报")

    occurrences = _report_reference_occurrences(report)
    occurrence_counts = {ref: occurrences.count(ref) for ref in set(occurrences)}
    referenced_refs = sorted(occurrence_counts, key=_ref_sort_key)[:MAX_REFERENCE_OUTPUT]
    if not referenced_refs:
        raise ResearchBriefError("研究简报至少需要一条报告实际引用的公开证据")

    rows_by_ref: dict[str, list[dict[str, Any]]] = {}
    invalid_types: list[dict[str, str]] = []
    uncited_result_refs: set[str] = set()
    public_cited_refs: set[str] = set()
    for raw_row in raw_results:
        if not isinstance(raw_row, Mapping):
            continue
        ref = _safe_ref(raw_row.get("agent_ref") or raw_row.get("ref"))
        if not ref:
            continue
        if raw_row.get("agent_cited") is True:
            public_cited_refs.add(ref)
        if ref not in occurrence_counts:
            continue
        if raw_row.get("agent_cited") is not True:
            uncited_result_refs.add(ref)
            continue
        entity_type = _evidence_type(raw_row)
        if entity_type not in EVIDENCE_TYPES:
            invalid_types.append({"ref": ref, "entity_type": entity_type})
            continue
        rows_by_ref.setdefault(ref, []).append(_clean_evidence(raw_row, ref, entity_type))

    duplicate_result_refs = sorted(
        (ref for ref, rows in rows_by_ref.items() if len(rows) > 1), key=_ref_sort_key
    )[:MAX_REFERENCE_OUTPUT]
    selected: list[dict[str, Any]] = []
    for ref in sorted(rows_by_ref, key=_ref_sort_key):
        candidates = sorted(rows_by_ref[ref], key=_canonical_evidence_key)
        selected.append(candidates[0])
    evidence_truncated = len(selected) > MAX_CITED_EVIDENCE
    selected = selected[:MAX_CITED_EVIDENCE]
    included_refs = [row["ref"] for row in selected]
    included_set = set(included_refs)
    invalid_ref_set = {row["ref"] for row in invalid_types}
    orphan_refs = [
        ref for ref in referenced_refs
        if ref not in rows_by_ref and ref not in invalid_ref_set
    ]
    omitted_refs = [ref for ref in referenced_refs if ref in rows_by_ref and ref not in included_set]

    evidence_by_type = {
        entity_type: [row for row in selected if row["entity_type"] == entity_type]
        for entity_type in EVIDENCE_TYPES
    }
    missing_provenance: list[dict[str, Any]] = []
    for row in selected:
        missing = [
            field for field in ("article_title", "doi", "source_page", "source_excerpt")
            if row.get(field) in (None, "")
        ]
        if missing:
            missing_provenance.append({"ref": row["ref"], "missing": missing})

    missing_sections = [section for section in REPORT_SECTIONS if section not in raw_report]
    declared_cited_count = _safe_count(snapshot.get("cited_count"))
    duplicate_report_refs = _report_duplicate_references(raw_report)
    invalid_types = sorted(
        invalid_types,
        key=lambda item: (_ref_sort_key(item["ref"]), item["entity_type"]),
    )[:MAX_REFERENCE_OUTPUT]
    extra_cited_refs = sorted(public_cited_refs - set(referenced_refs), key=_ref_sort_key)
    missing_cited_refs = sorted(set(referenced_refs) - public_cited_refs, key=_ref_sort_key)
    critical_issues = bool(
        orphan_refs
        or invalid_types
        or duplicate_report_refs
        or duplicate_result_refs
        or omitted_refs
        or missing_sections
        or uncited_result_refs
        or extra_cited_refs
        or missing_cited_refs
        or evidence_truncated
        or declared_cited_count != len(referenced_refs)
        or len(selected) != len(referenced_refs)
    )
    if critical_issues:
        raise ResearchBriefError("图书管理员回答与引用证据不一致，不能生成稳定研究简报")
    _assert_report_evidence_consistency(report, selected)
    integrity_issues = bool(missing_provenance)

    match_counts = snapshot.get("match_counts")
    match_counts = match_counts if isinstance(match_counts, Mapping) else {}
    brief = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _generated_at(generated_at),
        "answered_at": answered_at,
        "evidence_version": evidence_version,
        "question": question,
        "hard_conditions": _hard_conditions(snapshot.get("query_analysis")),
        "report": report,
        "cited_evidence": selected,
        "evidence_by_type": evidence_by_type,
        "statistics": {
            "model": _safe_text(snapshot.get("model"), 200),
            "plan_mode": _safe_text(snapshot.get("plan_mode"), 100),
            "summary_mode": _safe_text(snapshot.get("summary_mode"), 100),
            "response_format": _safe_text(snapshot.get("response_format"), 100),
            "cache_hit": bool(snapshot.get("cache_hit")),
            "candidate_count": _safe_count(snapshot.get("candidate_count")),
            "declared_cited_count": declared_cited_count,
            "report_reference_count": len(referenced_refs),
            "exported_cited_count": len(selected),
            "match_counts": {
                key: _safe_count(match_counts.get(key))
                for key in ("direct", "adjacent", "expansion")
            },
            "bundle_count": _safe_count(snapshot.get("bundle_count")),
        },
        "integrity": {
            "status": "warning" if integrity_issues else "pass",
            "referenced_refs": referenced_refs,
            "included_refs": included_refs,
            "orphan_references": orphan_refs,
            "duplicate_report_references": duplicate_report_refs,
            "duplicate_result_references": duplicate_result_refs,
            "invalid_type_references": invalid_types,
            "omitted_references": omitted_refs,
            "missing_report_sections": missing_sections,
            "declared_cited_count_matches_report": declared_cited_count == len(referenced_refs),
            "missing_provenance": missing_provenance,
            "evidence_truncated": evidence_truncated,
            "snapshot_binding": _safe_text(snapshot_binding, 80) or "unverified",
        },
        "limitations": [
            "本简报只重放一次已由当前服务进程签名绑定的图书管理员公开响应，不重新检索数据库或调用模型。",
            "证据附录只收录五段报告实际引用且可在公开 results 中解析的 R# 记录。",
            "缺失的 DOI、论文题目、页码或原文片段保持为空，不推测补全。",
            "本简报不从图片或曲线读取数据点，也不把视觉估算写成精确数值。",
            "本简报不新增跨 evidence bundle 的定量比较；任何比较应回到同一兼容实验条件核验。",
            "服务端签名与内部一致性检查只能证明本文件绑定于该次公开响应，不能替代人工科学复核。",
        ],
    }
    return brief


def _md_cell(value: Any) -> str:
    return _safe_text(value).replace("|", "／") or "—"


def _md_ref_list(value: Any) -> str:
    return " ".join(f"[{ref}]" for ref in _safe_refs(value))


def _evidence_heading(row: Mapping[str, Any]) -> str:
    title = (
        row.get("meaning")
        or row.get("display_name")
        or row.get("label")
        or row.get("article_title")
        or ""
    )
    return f"### [{row.get('ref')}] {row.get('entity_type')}" + (f" · {title}" if title else "")


def render_research_brief_markdown(brief: Mapping[str, Any]) -> str:
    """Render a brief produced by :func:`build_research_brief` as Markdown."""

    if not isinstance(brief, Mapping) or brief.get("schema_version") != SCHEMA_VERSION:
        raise ResearchBriefError(f"Markdown 仅接受 {SCHEMA_VERSION} 结构")
    lines = [
        "# 图书管理员研究简报",
        "",
        f"- 格式：`{SCHEMA_VERSION}`",
        f"- 生成时间：{_safe_text(brief.get('generated_at'), 100)}",
    ]
    if brief.get("answered_at"):
        lines.append(f"- 原回答时间：{_safe_text(brief.get('answered_at'), 100)}")
    if brief.get("evidence_version"):
        lines.append(f"- 证据版本：{_safe_text(brief.get('evidence_version'), 200)}")
    lines.extend([
        "",
        "## 研究问题",
        "",
        _safe_multiline(brief.get("question"), MAX_QUESTION_CHARS),
        "",
        "## 硬条件",
        "",
    ])
    conditions = brief.get("hard_conditions") if isinstance(brief.get("hard_conditions"), list) else []
    if conditions:
        for condition in conditions:
            if isinstance(condition, Mapping):
                values = "、".join(_safe_string_list(condition.get("values")))
                lines.append(f"- {_safe_text(condition.get('label'), 80)}：{values}")
    else:
        lines.append("- 快照未提供硬条件。")

    report = brief.get("report") if isinstance(brief.get("report"), Mapping) else {}
    conclusion = report.get("direct_conclusion") if isinstance(report.get("direct_conclusion"), Mapping) else {}
    lines.extend(["", "## 五段报告", "", "### 1. 直接结论", ""])
    lines.append(_safe_multiline(conclusion.get("text")) or "快照未提供直接结论文本。")

    lines.extend(["", "### 2. 证据矩阵", ""])
    matrix = report.get("evidence_matrix") if isinstance(report.get("evidence_matrix"), list) else []
    if matrix:
        lines.extend([
            "| 引用 | 材料 | 条件 | 性质 | 结果 | 论文 | DOI | 页码 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ])
        for row in matrix:
            if not isinstance(row, Mapping):
                continue
            lines.append("| " + " | ".join([
                _md_ref_list(row.get("refs")),
                _md_cell(row.get("material")),
                _md_cell(row.get("conditions")),
                _md_cell(row.get("property")),
                _md_cell(row.get("result")),
                _md_cell(row.get("article_title")),
                _md_cell(row.get("doi")),
                _md_cell(row.get("source_page")),
            ]) + " |")
    else:
        lines.append("无可导出的直接证据矩阵。")

    lines.extend(["", "### 3. 相关证据", ""])
    related = report.get("related_evidence") if isinstance(report.get("related_evidence"), list) else []
    if related:
        for row in related:
            if not isinstance(row, Mapping):
                continue
            refs = _md_ref_list(row.get("refs"))
            relaxed = "、".join(_safe_string_list(row.get("relaxed_constraints")))
            suffix = f"；放宽条件：{relaxed}" if relaxed else ""
            lines.append(f"- {refs} {_safe_text(row.get('summary'), MAX_REPORT_TEXT_CHARS)}{suffix}".rstrip())
    else:
        lines.append("- 无可导出的相关证据。")

    lines.extend(["", "### 4. 数据库缺口", ""])
    gaps = _safe_string_list(report.get("database_gaps"), chars=MAX_REPORT_TEXT_CHARS)
    lines.extend(f"- {value}" for value in gaps)
    if not gaps:
        lines.append("- 快照未提供数据库缺口说明。")

    lines.extend(["", "### 5. 建议追问", ""])
    followups = _safe_string_list(report.get("suggested_followups"), chars=MAX_REPORT_TEXT_CHARS)
    lines.extend(f"- {value}" for value in followups)
    if not followups:
        lines.append("- 快照未提供建议追问。")

    lines.extend(["", "## 引用证据附录", ""])
    evidence = brief.get("cited_evidence") if isinstance(brief.get("cited_evidence"), list) else []
    if not evidence:
        lines.append("五段报告没有可解析的已引用公开证据。")
    for row in evidence:
        if not isinstance(row, Mapping):
            continue
        lines.extend([_evidence_heading(row), ""])
        for label, field in (
            ("论文", "article_title"),
            ("DOI", "doi"),
            ("页码", "source_page"),
            ("定位", "source_locator"),
            ("召回分级", "match_class"),
            ("证据包", "bundle_id"),
        ):
            if row.get(field) not in (None, ""):
                lines.append(f"- {label}：{_safe_text(row.get(field), MAX_TITLE_CHARS)}")
        if row.get("entity_type") == "item":
            value = " ".join(
                part for part in (_safe_text(row.get("value_text")), _safe_text(row.get("unit"), 120)) if part
            )
            if row.get("meaning"):
                lines.append(f"- 具体意义：{_safe_text(row.get('meaning'))}")
            if value:
                lines.append(f"- 数值：{value}")
            if row.get("evidence_type"):
                lines.append(f"- 物理证据类型：{_safe_text(row.get('evidence_type'), 40)}")
            if row.get("context_explanation"):
                lines.append(
                    f"- 文章语境：{_safe_text(row.get('context_explanation'), MAX_REPORT_TEXT_CHARS)}"
                )
        elif row.get("entity_type") == "finding" and row.get("finding_text"):
            lines.append(f"- 实验结论：{_safe_text(row.get('finding_text'), MAX_REPORT_TEXT_CHARS)}")
            if row.get("evidence_type"):
                lines.append(f"- 物理证据类型：{_safe_text(row.get('evidence_type'), 40)}")
            if row.get("context_explanation"):
                lines.append(
                    f"- 文章语境：{_safe_text(row.get('context_explanation'), MAX_REPORT_TEXT_CHARS)}"
                )
        elif row.get("caption"):
            lines.append(f"- 图注：{_safe_text(row.get('caption'), MAX_REPORT_TEXT_CHARS)}")
        if row.get("source_excerpt"):
            lines.extend(["", f"> {_safe_text(row.get('source_excerpt'), MAX_EXCERPT_CHARS)}"])
        lines.append("")

    statistics = brief.get("statistics") if isinstance(brief.get("statistics"), Mapping) else {}
    match_counts = statistics.get("match_counts") if isinstance(statistics.get("match_counts"), Mapping) else {}
    lines.extend([
        "## 模型与召回统计",
        "",
        f"- 模型：{_safe_text(statistics.get('model'), 200) or '快照未提供'}",
        f"- 规划模式：{_safe_text(statistics.get('plan_mode'), 100) or '快照未提供'}",
        f"- 汇总模式：{_safe_text(statistics.get('summary_mode'), 100) or '快照未提供'}",
        f"- 响应格式：{_safe_text(statistics.get('response_format'), 100) or '快照未提供'}",
        f"- 缓存复用：{'是' if statistics.get('cache_hit') else '否'}",
        f"- 候选：{_safe_count(statistics.get('candidate_count'))}",
        f"- 报告唯一引用：{_safe_count(statistics.get('report_reference_count'))}",
        f"- 附录收录：{_safe_count(statistics.get('exported_cited_count'))}",
        f"- 召回分级：direct {_safe_count(match_counts.get('direct'))} / adjacent {_safe_count(match_counts.get('adjacent'))} / expansion {_safe_count(match_counts.get('expansion'))}",
        f"- 证据包：{_safe_count(statistics.get('bundle_count'))}",
    ])

    integrity = brief.get("integrity") if isinstance(brief.get("integrity"), Mapping) else {}
    lines.extend(["", "## 完整性检查", "", f"- 状态：{_safe_text(integrity.get('status'), 30)}"])
    checks = (
        ("孤儿引用", "orphan_references"),
        ("报告内重复引用", "duplicate_report_references"),
        ("结果中重复引用", "duplicate_result_references"),
        ("无效证据类型", "invalid_type_references"),
        ("因上限未收录", "omitted_references"),
        ("缺失报告段落", "missing_report_sections"),
        ("来源字段缺失", "missing_provenance"),
    )
    for label, field in checks:
        value = integrity.get(field)
        if value:
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
            lines.append(f"- {label}：{rendered}")
        else:
            lines.append(f"- {label}：无")
    lines.append(
        "- 声明引用数与报告一致："
        + ("是" if integrity.get("declared_cited_count_matches_report") else "否")
    )
    lines.append(
        "- 证据是否因上限截断："
        + ("是" if integrity.get("evidence_truncated") else "否")
    )
    lines.append(
        f"- 快照绑定：{_safe_text(integrity.get('snapshot_binding'), 80) or '未提供'}"
    )

    lines.extend(["", "## 明确限制", ""])
    limitations = _safe_string_list(brief.get("limitations"), chars=MAX_REPORT_TEXT_CHARS)
    lines.extend(f"- {value}" for value in limitations)
    return "\n".join(lines).rstrip() + "\n"


def export_research_brief(
    snapshot: Mapping[str, Any] | None,
    *,
    generated_at: str | datetime | None = None,
    snapshot_binding: str = "unverified",
) -> tuple[dict[str, Any], str]:
    """Return the structured brief and its Markdown rendering together."""

    brief = build_research_brief(
        snapshot,
        generated_at=generated_at,
        snapshot_binding=snapshot_binding,
    )
    return brief, render_research_brief_markdown(brief)


# A concise alias for callers that already hold the structured brief.
research_brief_markdown = render_research_brief_markdown
