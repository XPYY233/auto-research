from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from auto_research.ai.deepseek import DeepSeekClient

from .db import EvidenceDB
from .librarian_reasoning import (
    QueryAnalysis,
    build_evidence_bundles,
    build_query_analysis,
    build_research_report,
    reason_candidates as reason_candidate_rows,
    report_markdown,
    report_references,
    soft_recall_queries,
)
from .public_dto import public_evidence_dto
from .research_brief import (
    _has_unsupported_quantitative_claim,
    _is_safe_cross_bundle_overview,
)
from .search_index import ENTITY_TYPES, EvidenceSearchIndex, plan_query


MAX_AGENT_QUESTION_CHARS = 2_000
MAX_AGENT_HISTORY_MESSAGES = 8
MAX_AGENT_RESULTS = 80
MAX_AGENT_RECALL_QUERIES = 16
MAX_SUMMARY_RESULTS = 48
MAX_RESULTS_PER_TYPE = {"item": 24, "finding": 20, "table": 18, "figure": 18}
AGENT_CACHE_TTL_SECONDS = 3_600
AGENT_CACHE_MAX_ENTRIES = 32
LIBRARIAN_RESPONSE_FORMAT_VERSION = "reasoning-presentation-v1"

_GENERIC_RECALL_TERMS = {"变化", "影响", "结果", "情况", "表现", "关系", "规律", "研究"}
_IRRADIATION_TERMS = {"中子辐照", "离子辐照", "电子辐照", "辐照实验", "氦离子", "氢离子", "重离子"}
_CONCEPT_EXPANSIONS = {
    "缺陷结构": ("位错环", "空洞", "气泡", "位错密度", "微观结构"),
    "缺陷": ("位错环", "空洞", "气泡", "位错密度"),
    "力学性能": ("硬度", "拉伸强度", "屈服强度", "断裂韧性"),
    "肿胀": ("晶格肿胀", "体积肿胀", "空洞"),
}


_DSML_MARKER = "DSML"
_EVIDENCE_NUMBER_FIELDS = (
    "title",
    "value",
    "unit",
    "context",
    "evidence",
    "finding",
    "label",
    "caption",
    "materials",
    "conditions",
    "quantities",
)


def _is_internal_protocol(content: Any) -> bool:
    return _DSML_MARKER in str(content or "")


def _scientific_scalar_values(value: Any, *, depth: int = 0) -> tuple[Any, ...]:
    if depth > 4 or value is None or isinstance(value, bool):
        return ()
    if isinstance(value, dict):
        return tuple(
            scalar
            for item in value.values()
            for scalar in _scientific_scalar_values(item, depth=depth + 1)
        )
    if isinstance(value, (list, tuple, set)):
        return tuple(
            scalar
            for item in value
            for scalar in _scientific_scalar_values(item, depth=depth + 1)
        )
    if isinstance(value, (str, int, float, Decimal)):
        return (value,)
    return ()


def _candidate_quantitative_inputs(
    candidate: dict[str, Any],
) -> tuple[list[Any], list[tuple[Any, Any]]]:
    values: list[Any] = []
    for field in _EVIDENCE_NUMBER_FIELDS:
        values.extend(_scientific_scalar_values(candidate.get(field)))
    structured_value = candidate.get("value")
    if structured_value in (None, ""):
        structured_value = candidate.get("value_text")
    return values, [(structured_value, candidate.get("unit"))]


def _cited_refs(answer: str) -> set[str]:
    refs = {f"R{value}" for value in re.findall(r"\bR(\d+)\b", answer or "")}
    for start, end in re.findall(r"\bR(\d+)\s*[\-–—]\s*R?(\d+)\b", answer or ""):
        first, last = int(start), int(end)
        if first <= last and last - first <= MAX_AGENT_RESULTS:
            refs.update(f"R{value}" for value in range(first, last + 1))
    return refs


def _dedupe_text(values: list[str], limit: int) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(str(value or "").split()).strip()
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        output.append(cleaned[:180])
        if len(output) >= limit:
            break
    return output


def _model_safe_value(value: Any, *, depth: int = 0) -> Any:
    """Bound model context without cutting JSON tokens or exposing huge fields."""

    if depth > 4:
        return str(value)[:240]
    if isinstance(value, str):
        return value[:1_200]
    if isinstance(value, dict):
        return {str(key): _model_safe_value(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_model_safe_value(item, depth=depth + 1) for item in list(value)[:24]]
    return value


def _bounded_json_list(values: list[dict[str, Any]], max_chars: int) -> str:
    # Give every available evidence type a place in the model context before
    # filling the remaining budget.  This avoids a long run of data items
    # starving tables or figures at the end of the prompt.
    type_order = ("item", "finding", "table", "figure")
    by_type = {
        entity_type: [value for value in values if value.get("entity_type") == entity_type]
        for entity_type in type_order
    }
    ordered: list[dict[str, Any]] = []
    seen: set[int] = set()
    while any(by_type.values()):
        for entity_type in type_order:
            if by_type[entity_type]:
                value = by_type[entity_type].pop(0)
                ordered.append(value)
                seen.add(id(value))
    ordered.extend(value for value in values if id(value) not in seen)
    selected: list[dict[str, Any]] = []
    for value in ordered:
        safe = _model_safe_value(value)
        trial = json.dumps([*selected, safe], ensure_ascii=False, default=str)
        if len(trial) > max_chars:
            continue
        selected.append(safe)
    return json.dumps(selected, ensure_ascii=False, default=str)


def _fallback_recall_queries(question: str) -> list[str]:
    """Build broad but bounded search variants when the model planner fails.

    Long natural-language questions are deliberately decomposed.  This avoids
    requiring every concept (for example material + particle + two outcomes)
    to occur in the same indexed record and lets the final model distinguish
    direct evidence from adjacent evidence.
    """

    terms = [term for term in plan_query(question) if term not in _GENERIC_RECALL_TERMS]
    if not terms:
        return [str(question or "").strip()]
    subject = terms[0]
    irradiation = next((term for term in terms if term in _IRRADIATION_TERMS or "辐照" in term), "")
    outcomes = [term for term in terms if term not in {subject, irradiation}]
    queries = [" ".join(terms[:4])]
    if subject and irradiation:
        queries.append(f"{subject} {irradiation}")
    for outcome in outcomes[:4]:
        queries.append(f"{subject} {outcome}")
        if irradiation:
            queries.append(f"{irradiation} {outcome}")
        for expansion in _CONCEPT_EXPANSIONS.get(outcome, ()):
            queries.append(f"{subject} {expansion}")
            if irradiation:
                queries.append(f"{irradiation} {expansion}")
    queries.extend(terms)
    return _dedupe_text(queries, MAX_AGENT_RECALL_QUERIES)


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]]

    def api_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class AgentDefinition:
    agent_id: str
    name: str
    description: str
    system_prompt: str
    tools: tuple[str, ...]
    enabled: bool = True


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate agent tool: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> AgentTool:
        if name not in self._tools:
            raise KeyError(f"agent tool not allowed: {name}")
        return self._tools[name]


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, AgentDefinition] = {}

    def register(self, agent: AgentDefinition) -> None:
        if agent.agent_id in self._agents:
            raise ValueError(f"duplicate agent: {agent.agent_id}")
        self._agents[agent.agent_id] = agent

    def get(self, agent_id: str) -> AgentDefinition:
        agent = self._agents.get(agent_id)
        if not agent or not agent.enabled:
            raise KeyError(f"agent not available: {agent_id}")
        return agent

    def public_list(self) -> list[dict[str, Any]]:
        return [
            {
                "id": agent.agent_id,
                "name": agent.name,
                "description": agent.description,
                "enabled": agent.enabled,
            }
            for agent in self._agents.values() if agent.enabled
        ]


class AgentRateLimiter:
    """Small in-process guardrail for the shared read-only endpoint."""

    def __init__(self, limit: int = 12, window_seconds: int = 60):
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        stamp = time.monotonic()
        with self._lock:
            recent = [value for value in self._events.get(key, []) if stamp - value < self.window_seconds]
            if len(recent) >= self.limit:
                self._events[key] = recent
                return False
            recent.append(stamp)
            self._events[key] = recent
            return True


def _compact_result(entity_type: str, row: dict[str, Any], ref: str) -> dict[str, Any]:
    common = {
        "ref": ref,
        "entity_type": entity_type,
        "entity_id": int(row.get("item_id") or row.get("id") or 0),
        "paper_id": int(row.get("paper_id") or 0),
        "article_title": row.get("article_title"),
        "doi": row.get("doi"),
        "source_page": row.get("source_page") or row.get("page_start"),
        "search_score": row.get("search_score") or 0,
    }
    if entity_type == "item":
        common.update({
            "title": row.get("meaning"), "value": row.get("value_text"), "unit": row.get("unit"),
            "context": row.get("context_explanation"), "evidence": row.get("source_excerpt"),
        })
    elif entity_type == "finding":
        common.update({
            "title": row.get("meaning"), "finding": row.get("finding_text") or row.get("value_text"),
            "context": row.get("context_explanation"), "evidence": row.get("source_excerpt"),
        })
    else:
        common.update({
            "title": row.get("display_name") or row.get("label"), "label": row.get("label"),
            "context": row.get("context_explanation"), "caption": row.get("caption"),
            "materials": row.get("materials"), "conditions": row.get("conditions_text"),
            "quantities": row.get("physical_quantities"),
        })
    return common


class LibrarianAgentRuntime:
    """Read-only DeepSeek agent that composes the existing four search types."""

    _response_cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
    _response_cache_lock = threading.Lock()

    def __init__(self, db: EvidenceDB, client: DeepSeekClient | None = None):
        self.db = db
        self.index = EvidenceSearchIndex(db)
        self.client = client or DeepSeekClient()
        self.tools = ToolRegistry()
        self.agents = AgentRegistry()
        self._collected: list[dict[str, Any]] = []
        self._register_tools()
        self.agents.register(AgentDefinition(
            agent_id="librarian",
            name="图书管理员",
            description="把自然语言问题拆成检索条件，并从数据、表格、图片和实验结论中返回可追溯证据。",
            tools=("search_evidence", "get_evidence_detail"),
            system_prompt=(
                "你是实验文献证据库的图书管理员。把自然语言问题拆成材料、实验类型、条件和物理量，"
                "并从item数据条目、table原始表格、figure论文图片、finding实验结论四类只读记录中查找证据。"
                "必须区分同时满足全部条件的直接证据与仅满足部分条件的相关证据。不得编造数据库外论文、"
                "数值或曲线点，不得把visual interpretation写成直接测量；最终中文回答用[R编号]连接真实记录。"
            ),
        ))

    def _register_tools(self) -> None:
        self.tools.register(AgentTool(
            name="search_evidence",
            description="检索现有证据库的四类结果；可一次检索一种或多种类型。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "精炼后的检索词，包含材料、条件和物理量"},
                    "entity_types": {
                        "type": "array", "items": {"type": "string", "enum": ["item", "table", "figure", "finding"]},
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 12},
                },
                "required": ["query", "entity_types"],
                "additionalProperties": False,
            },
            handler=self._search_tool,
        ))
        self.tools.register(AgentTool(
            name="get_evidence_detail",
            description="读取某个已检索结果的完整结构化详情。",
            parameters={
                "type": "object",
                "properties": {
                    "entity_type": {"type": "string", "enum": ["item", "table", "figure", "finding"]},
                    "entity_id": {"type": "integer"},
                },
                "required": ["entity_type", "entity_id"],
                "additionalProperties": False,
            },
            handler=self._detail_tool,
        ))

    def _search_tool(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()[:500]
        if not query:
            raise ValueError("search query cannot be empty")
        entity_types = {str(value) for value in arguments.get("entity_types") or []}
        if not entity_types or entity_types - ENTITY_TYPES:
            raise ValueError("entity_types must use the four registered evidence types")
        limit = min(max(int(arguments.get("limit") or 8), 1), 12)
        page = self.index.search(query, entity_types=entity_types, limit=limit)
        compact: list[dict[str, Any]] = []
        for row in page.rows:
            entity_type = "item" if "fact_id" in row else "finding" if "finding_id" in row else str(row.get("asset_type"))
            identity = (entity_type, int(row.get("item_id") or row.get("id") or 0))
            if not any((item["entity_type"], item["entity_id"]) == identity for item in self._collected):
                if len(self._collected) >= MAX_AGENT_RESULTS:
                    continue
                if sum(1 for item in self._collected if item["entity_type"] == entity_type) >= MAX_RESULTS_PER_TYPE[entity_type]:
                    continue
                ref = f"R{len(self._collected) + 1}"
                full = {
                    "ref": ref,
                    "entity_type": entity_type,
                    "entity_id": identity[1],
                    "payload": row,
                    "matched_queries": [query],
                }
                self._collected.append(full)
            else:
                full = next(item for item in self._collected if (item["entity_type"], item["entity_id"]) == identity)
                if query not in full["matched_queries"]:
                    full["matched_queries"].append(query)
            compact_row = _compact_result(entity_type, row, full["ref"])
            compact_row["matched_queries"] = full["matched_queries"][:4]
            compact.append(compact_row)
        return {
            "query": query,
            "query_terms": page.terms,
            "total": page.total,
            "returned": len(compact),
            "results": compact,
        }

    def _detail_tool(self, arguments: dict[str, Any]) -> dict[str, Any]:
        entity_type = str(arguments.get("entity_type") or "")
        entity_id = int(arguments.get("entity_id") or 0)
        row = self.index.get(entity_type, entity_id)
        return _compact_result(entity_type, row, "detail")

    @staticmethod
    def _history(history: Any) -> list[dict[str, str]]:
        if not isinstance(history, list):
            return []
        output: list[dict[str, str]] = []
        for raw in history[-MAX_AGENT_HISTORY_MESSAGES:]:
            if not isinstance(raw, dict) or raw.get("role") not in {"user", "assistant"}:
                continue
            content = str(raw.get("content") or "").strip()[:3_000]
            if content and not _is_internal_protocol(content):
                output.append({"role": str(raw["role"]), "content": content})
        return output

    def _cache_key(self, prompt: str, history: Any) -> str:
        material = json.dumps(
            {
                "database": str(self.db.path.resolve()),
                "fingerprint": self.index.source_fingerprint(),
                "response_format": LIBRARIAN_RESPONSE_FORMAT_VERSION,
                "question": prompt,
                "history": self._history(history),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @classmethod
    def _cache_get(cls, key: str) -> dict[str, Any] | None:
        stamp = time.monotonic()
        with cls._response_cache_lock:
            cached = cls._response_cache.get(key)
            if not cached:
                return None
            created_at, payload = cached
            if stamp - created_at > AGENT_CACHE_TTL_SECONDS:
                cls._response_cache.pop(key, None)
                return None
            cls._response_cache.move_to_end(key)
            result = copy.deepcopy(payload)
            result["cache_hit"] = True
            return result

    @classmethod
    def _cache_put(cls, key: str, payload: dict[str, Any]) -> None:
        with cls._response_cache_lock:
            cls._response_cache[key] = (time.monotonic(), copy.deepcopy(payload))
            cls._response_cache.move_to_end(key)
            while len(cls._response_cache) > AGENT_CACHE_MAX_ENTRIES:
                cls._response_cache.popitem(last=False)

    def _plan_recall(self, prompt: str, history: Any) -> tuple[list[str], str, QueryAnalysis]:
        fallback = _fallback_recall_queries(prompt)
        recent = self._history(history)
        try:
            payload = self.client.request_json(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是实验文献证据检索规划器。检索范围固定为全部文章，不接受论文范围限制。"
                            "把当前中文研究问题改写为2到8个简短检索式。"
                            "硬条件由本地确定性程序独立解析；你不得补充或输出材料、粒子、温度、剂量等条件。"
                            "同义词只用于检索式，不得把它们写成新的问题事实。"
                            "检索式应覆盖材料、实验条件和每个目标物理量；复杂问题要拆分，不能把所有词都塞进一个检索式。"
                            "同时保留至少一个严格组合检索式和若干材料+性质、辐照类型+性质的召回检索式。"
                            "若存在无法从当前问题或有限历史确定的关键指代/比较对象，在clarification中提出一句澄清问题；"
                            "否则needed必须为false。只输出JSON对象："
                            "{\"queries\":[\"...\"],\"focus\":\"一句话研究意图\","
                            "\"clarification\":{\"needed\":false,\"question\":\"\",\"options\":[]}}。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"最近对话：{json.dumps(recent, ensure_ascii=False)[:4_000]}\n"
                            f"当前问题：{prompt}\n本地保底检索式：{json.dumps(fallback, ensure_ascii=False)}"
                        ),
                    },
                ],
                task="librarian_planning",
                max_tokens=1_200,
                thinking=False,
                temperature=0.0,
            )
            planned = payload.get("queries") or []
            if not isinstance(planned, list):
                planned = []
            analysis = build_query_analysis(prompt, model_payload=payload, history=recent)
            combined = _dedupe_text(
                [str(value) for value in planned[:4] if isinstance(value, (str, int, float))]
                + fallback
                + soft_recall_queries(analysis),
                MAX_AGENT_RECALL_QUERIES,
            )
            return combined or fallback, "deepseek+local", analysis
        except Exception:
            analysis = build_query_analysis(prompt, history=recent)
            combined = _dedupe_text(fallback + soft_recall_queries(analysis), MAX_AGENT_RECALL_QUERIES)
            return combined or fallback, "local_fallback", analysis

    def _run_recall(self, queries: list[str]) -> int:
        operations = 0
        limits = {"item": 10, "finding": 8, "table": 6, "figure": 6}
        recalled: dict[tuple[str, int], dict[str, Any]] = {}
        self.index.ensure_fresh()
        for query in queries:
            for entity_type in ("item", "finding", "table", "figure"):
                page = self.index.search(
                    query,
                    entity_types={entity_type},
                    limit=limits[entity_type],
                    refresh=False,
                )
                operations += 1
                for row in page.rows:
                    identity = (entity_type, int(row.get("item_id") or row.get("id") or 0))
                    candidate = recalled.setdefault(identity, {
                        "entity_type": entity_type,
                        "entity_id": identity[1],
                        "payload": row,
                        "matched_queries": [],
                        "query_hits": 0,
                        "best_score": 0.0,
                    })
                    if query not in candidate["matched_queries"]:
                        candidate["matched_queries"].append(query)
                        candidate["query_hits"] += 1
                    score = float(row.get("search_score") or 0)
                    if score > candidate["best_score"]:
                        candidate["best_score"] = score
                        candidate["payload"] = row
        selected: list[dict[str, Any]] = []
        for entity_type in ("item", "finding", "table", "figure"):
            ranked = sorted(
                (value for value in recalled.values() if value["entity_type"] == entity_type),
                key=lambda value: (-value["query_hits"], -value["best_score"], value["entity_id"]),
            )
            selected.extend(ranked[:MAX_RESULTS_PER_TYPE[entity_type]])
        selected.sort(key=lambda value: (-value["query_hits"], -value["best_score"], value["entity_type"], value["entity_id"]))
        self._collected = []
        for index, value in enumerate(selected[:MAX_AGENT_RESULTS], start=1):
            self._collected.append({
                "ref": f"R{index}",
                "entity_type": value["entity_type"],
                "entity_id": value["entity_id"],
                "payload": value["payload"],
                "matched_queries": value["matched_queries"],
            })
        return operations

    def _reasoned_candidates(self, analysis: QueryAnalysis) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        compact: list[dict[str, Any]] = []
        for item in self._collected:
            row = _compact_result(item["entity_type"], item["payload"], item["ref"])
            row["matched_queries"] = item["matched_queries"][:4]
            compact.append(row)
        reasoned = reason_candidate_rows(compact, analysis)
        bundles = build_evidence_bundles(reasoned, analysis)
        return reasoned, bundles

    def _summary_candidates(self, reasoned: list[dict[str, Any]]) -> list[dict[str, Any]]:
        quotas = {"item": 16, "finding": 14, "table": 9, "figure": 9}
        balanced: list[dict[str, Any]] = []
        for entity_type in ("item", "finding", "table", "figure"):
            balanced.extend(
                [item for item in reasoned if item["entity_type"] == entity_type][:quotas[entity_type]]
            )
        if len(balanced) < MAX_SUMMARY_RESULTS:
            identities = {(item["entity_type"], item["entity_id"]) for item in balanced}
            balanced.extend(
                item for item in reasoned
                if (item["entity_type"], item["entity_id"]) not in identities
            )
        identities = {(item["entity_type"], item["entity_id"]) for item in balanced[:MAX_SUMMARY_RESULTS]}
        return [
            item for item in reasoned
            if (item["entity_type"], item["entity_id"]) in identities
        ][:MAX_SUMMARY_RESULTS]

    @staticmethod
    def _payload_refs(raw_values: Any, available: set[str]) -> set[str]:
        if isinstance(raw_values, (str, int)):
            raw_values = [raw_values]
        if not isinstance(raw_values, list):
            return set()
        return {
            f"R{match.group(1)}"
            for value in raw_values if isinstance(value, (str, int))
            if (match := re.search(r"(\d+)", str(value)))
        } & available

    @staticmethod
    def _unsafe_cross_bundle_comparison(
        text: str,
        refs: set[str],
        candidates: list[dict[str, Any]],
    ) -> bool:
        # Cross-bundle free prose cannot be made safe by enumerating comparison
        # words. Only the deterministic retrieval-count overview is allowed;
        # independent quantitative rows remain available in the evidence matrix.
        by_ref = {str(row.get("ref")): row for row in candidates}
        bundles = {str(by_ref[ref].get("bundle_id") or "") for ref in refs if ref in by_ref}
        crosses_bundles = bool(bundles) and ("" in bundles or len(bundles) > 1)
        return crosses_bundles and not _is_safe_cross_bundle_overview(text)

    @staticmethod
    def _has_unsupported_numbers(
        text: str,
        refs: set[str],
        candidates: list[dict[str, Any]],
    ) -> bool:
        """Reject model prose whose quantitative tokens are absent from its evidence."""

        by_ref = {str(row.get("ref")): row for row in candidates}
        evidence_values: list[Any] = []
        structured_pairs: list[tuple[Any, Any]] = []
        for ref in refs:
            candidate = by_ref.get(ref)
            if candidate:
                values, pairs = _candidate_quantitative_inputs(candidate)
                evidence_values.extend(values)
                structured_pairs.extend(pairs)
        return _has_unsupported_quantitative_claim(
            text,
            evidence_values,
            structured_value_units=structured_pairs,
            ignore_retrieval_counts=True,
        )

    @staticmethod
    def _related_notes(
        payload: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> dict[str, str]:
        output: dict[str, str] = {}
        available = {
            str(row.get("ref"))
            for row in candidates
            if row.get("match_class") == "adjacent"
        }
        raw = payload.get("related_notes") or []
        if isinstance(raw, dict):
            raw = [{"ref": key, "summary": value} for key, value in raw.items()]
        if not isinstance(raw, list):
            return output
        for item in raw[:8]:
            if not isinstance(item, dict):
                continue
            refs = LibrarianAgentRuntime._payload_refs(item.get("refs") or item.get("ref"), available)
            summary = " ".join(str(item.get("summary") or "").split()).strip()[:260]
            if (
                summary
                and refs
                and not LibrarianAgentRuntime._unsafe_cross_bundle_comparison(
                    summary,
                    refs,
                    candidates,
                )
                and not LibrarianAgentRuntime._has_unsupported_numbers(
                    summary,
                    refs,
                    candidates,
                )
            ):
                for ref in refs:
                    output[ref] = summary
        return output

    def _report_from_payload(
        self,
        payload: dict[str, Any],
        analysis: QueryAnalysis,
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        available = {str(row.get("ref")) for row in candidates}
        direct_available = {str(row.get("ref")) for row in candidates if row.get("match_class") == "direct"}
        adjacent_available = {str(row.get("ref")) for row in candidates if row.get("match_class") == "adjacent"}
        legacy_refs = self._payload_refs(payload.get("selected_refs"), available)
        direct_refs = self._payload_refs(payload.get("direct_refs"), direct_available) | (
            legacy_refs & direct_available
        )
        related_refs = self._payload_refs(payload.get("related_refs"), adjacent_available) | (
            legacy_refs & adjacent_available
        )
        direct_text = str(payload.get("direct_conclusion") or payload.get("answer") or "").strip()
        all_text_refs = _cited_refs(direct_text)
        orphan_refs = all_text_refs - available
        text_refs = all_text_refs & available
        direct_refs.update(text_refs & direct_available)
        related_refs.update(text_refs & adjacent_available)
        if (
            _is_internal_protocol(direct_text)
            or bool(orphan_refs)
            or bool(text_refs - direct_available)
            or self._unsafe_cross_bundle_comparison(direct_text, direct_refs, candidates)
            or self._has_unsupported_numbers(direct_text, direct_refs, candidates)
        ):
            direct_text = ""
        followups = payload.get("suggested_followups") or []
        if not isinstance(followups, list):
            followups = []
        return build_research_report(
            analysis,
            candidates,
            direct_text=direct_text,
            direct_refs=sorted(direct_refs, key=lambda value: int(value[1:])),
            related_refs=sorted(related_refs, key=lambda value: int(value[1:])),
            related_notes=self._related_notes(payload, candidates),
            suggested_followups=[str(value) for value in followups[:3]],
        )

    def _summarize(
        self,
        prompt: str,
        queries: list[str],
        analysis: QueryAnalysis,
        reasoned: list[dict[str, Any]],
        bundles: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], set[str], str]:
        candidates = self._summary_candidates(reasoned)
        available = {str(row.get("ref")) for row in candidates}
        messages = [
            {
                "role": "system",
                "content": (
                    "你是实验文献证据库的中文图书管理员。后端已确定性解析硬条件，并把每项候选标为direct、"
                    "adjacent（只缺一个硬条件）或expansion（缺多个条件）。你不得更改这一分层。"
                    "direct_conclusion只总结direct候选；没有direct时必须为空，后端会诚实报告缺口。"
                    "related_refs只能选择adjacent候选，不能把expansion冒充相关证据。"
                    "数值必须紧邻包含该数值的真实[R编号]。定量前后比较只能使用同一bundle_id中的同种材料、"
                    "同一实验条件记录；否则只做定性陈述。禁止编造论文、数值、曲线点或实验条件。"
                    "建议追问给2到3个简短、可直接继续检索的问题。只输出JSON对象："
                    "{\"direct_conclusion\":\"中文直接结论\",\"direct_refs\":[\"R1\"],"
                    "\"related_refs\":[\"R2\"],\"related_notes\":[{\"refs\":[\"R2\"],"
                    "\"summary\":\"为何相关\"}],\"suggested_followups\":[\"问题1\",\"问题2\"]}。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"研究问题：{prompt}\n检索方案：{json.dumps(queries, ensure_ascii=False)}\n"
                    f"确定性条件：{json.dumps(analysis.as_dict(), ensure_ascii=False)}\n"
                    f"证据包：{_bounded_json_list(bundles[:24], 16_000)}\n"
                    f"候选证据：{_bounded_json_list(candidates, 48_000)}"
                ),
            },
        ]
        try:
            payload = self.client.request_json(
                messages,
                task="librarian_synthesis",
                max_tokens=3_600,
                thinking=False,
                temperature=0.0,
            )
            if isinstance(payload, dict):
                report = self._report_from_payload(payload, analysis, candidates)
                selected = report_references(report) & available
                return report, selected, "deepseek_json"
        except Exception:
            pass
        try:
            message = self.client.request_tool_message(
                messages, [], task="librarian_synthesis",
                max_tokens=3_600, temperature=0.0,
            )
            answer = str(message.get("content") or "").strip()
            selected = _cited_refs(answer) & available
            if answer and selected and not _is_internal_protocol(answer):
                report = self._report_from_payload(
                    {"answer": answer, "selected_refs": sorted(selected)},
                    analysis,
                    candidates,
                )
                return report, report_references(report) & available, "deepseek_text_fallback"
        except Exception:
            pass
        report = build_research_report(analysis, candidates)
        return report, report_references(report) & available, "deterministic_fallback"

    def run(self, question: str, *, history: Any = None) -> dict[str, Any]:
        prompt = str(question or "").strip()[:MAX_AGENT_QUESTION_CHARS]
        if not prompt:
            raise ValueError("问题不能为空")
        agent = self.agents.get("librarian")
        cache_key = self._cache_key(prompt, history)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        self._collected = []
        queries, plan_mode, analysis = self._plan_recall(prompt, history)
        if analysis.needs_clarification:
            report = {
                "schema_version": "research-report-v1",
                "direct_conclusion": {
                    "status": "clarification",
                    "text": analysis.clarification_question,
                    "refs": [],
                },
                "evidence_matrix": [],
                "related_evidence": [],
                "database_gaps": ["尚未执行证据检索：需要先明确关键科研对象，避免系统替用户猜测条件。"],
                "suggested_followups": list(analysis.clarification_options)[:3],
            }
            cited: set[str] = set()
            reasoned: list[dict[str, Any]] = []
            bundles: list[dict[str, Any]] = []
            search_operations = 0
            summary_mode = "clarification"
        else:
            search_operations = self._run_recall(queries)
            reasoned, bundles = self._reasoned_candidates(analysis)
            if reasoned:
                report, cited, summary_mode = self._summarize(prompt, queries, analysis, reasoned, bundles)
            else:
                report = build_research_report(analysis, [])
                cited = set()
                summary_mode = "no_results"
        answer = report_markdown(report)
        collected_by_ref = {str(item["ref"]): item for item in self._collected}
        public_results: list[dict[str, Any]] = []
        for candidate in reasoned[:MAX_AGENT_RESULTS]:
            item = collected_by_ref[str(candidate["ref"])]
            row = public_evidence_dto(item["payload"])
            row["agent_ref"] = item["ref"]
            row["agent_entity_type"] = item["entity_type"]
            row["agent_cited"] = item["ref"] in cited
            row["agent_match_queries"] = item["matched_queries"][:4]
            row["agent_match_class"] = candidate.get("match_class")
            row["agent_matched_constraints"] = candidate.get("matched_constraints") or []
            row["agent_missing_constraints"] = candidate.get("missing_constraints") or []
            row["agent_constraint_coverage"] = candidate.get("constraint_coverage")
            row["agent_bundle_id"] = candidate.get("bundle_id") or ""
            row["agent_relaxed_condition"] = (
                candidate.get("missing_constraints", [{}])[0].get("label")
                if candidate.get("match_class") == "adjacent" and candidate.get("missing_constraints")
                else ""
            )
            public_results.append(row)
        class_counts = {
            match_class: sum(1 for row in public_results if row.get("agent_match_class") == match_class)
            for match_class in ("direct", "adjacent", "expansion")
        }
        result = {
            "agent": {"id": agent.agent_id, "name": agent.name},
            "response_format": LIBRARIAN_RESPONSE_FORMAT_VERSION,
            "answered_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "evidence_version": self.index.source_fingerprint(),
            "answer": answer,
            "report": report,
            "query_analysis": analysis.as_dict(),
            "evidence_bundles": bundles,
            "results": public_results,
            "tool_calls": len(queries),
            "search_operations": search_operations,
            "candidate_count": len(public_results),
            "cited_count": len(cited),
            "match_counts": class_counts,
            "bundle_count": len(bundles),
            "recall_queries": queries,
            "plan_mode": plan_mode,
            "summary_mode": summary_mode,
            "clarification_required": analysis.needs_clarification,
            "scope": {"paper_ids": [], "mode": "all"},
            "model": self.client.settings.librarian_synthesis_model,
            "planning_model": self.client.settings.librarian_planning_model,
            "cache_hit": False,
        }
        self._cache_put(cache_key, result)
        return result


def build_agent_catalog(db: EvidenceDB) -> list[dict[str, Any]]:
    return LibrarianAgentRuntime(db).agents.public_list()
