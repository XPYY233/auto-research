from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

from auto_research.ai.deepseek import DeepSeekClient

from .db import EvidenceDB
from .search_index import ENTITY_TYPES, EvidenceSearchIndex, plan_query


MAX_AGENT_QUESTION_CHARS = 2_000
MAX_AGENT_HISTORY_MESSAGES = 8
MAX_AGENT_RESULTS = 80
MAX_AGENT_RECALL_QUERIES = 16
MAX_SUMMARY_RESULTS = 48
MAX_RESULTS_PER_TYPE = {"item": 24, "finding": 20, "table": 18, "figure": 18}
AGENT_CACHE_TTL_SECONDS = 3_600
AGENT_CACHE_MAX_ENTRIES = 32

_GENERIC_RECALL_TERMS = {"变化", "影响", "结果", "情况", "表现", "关系", "规律", "研究"}
_IRRADIATION_TERMS = {"中子辐照", "离子辐照", "电子辐照", "辐照实验", "氦离子", "氢离子", "重离子"}
_CONCEPT_EXPANSIONS = {
    "缺陷结构": ("位错环", "空洞", "气泡", "位错密度", "微观结构"),
    "缺陷": ("位错环", "空洞", "气泡", "位错密度"),
    "力学性能": ("硬度", "拉伸强度", "屈服强度", "断裂韧性"),
    "肿胀": ("晶格肿胀", "体积肿胀", "空洞"),
}


_DSML_MARKER = "DSML"


def _is_internal_protocol(content: Any) -> bool:
    return _DSML_MARKER in str(content or "")


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

    def _plan_recall(self, prompt: str, history: Any) -> tuple[list[str], str]:
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
                            "检索式应覆盖材料、实验条件和每个目标物理量；复杂问题要拆分，不能把所有词都塞进一个检索式。"
                            "同时保留至少一个严格组合检索式和若干材料+性质、辐照类型+性质的召回检索式。"
                            "只输出JSON对象：{\"queries\":[\"...\"],\"focus\":\"一句话研究意图\"}。"
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
                task="analysis",
                max_tokens=1_200,
                thinking=False,
                temperature=0.0,
            )
            planned = payload.get("queries") or []
            if not isinstance(planned, list):
                planned = []
            combined = _dedupe_text(
                [str(value) for value in planned[:4] if isinstance(value, (str, int, float))] + fallback,
                MAX_AGENT_RECALL_QUERIES,
            )
            return combined or fallback, "deepseek+local"
        except Exception:
            return fallback, "local_fallback"

    def _run_recall(self, queries: list[str]) -> int:
        operations = 0
        limits = {"item": 10, "finding": 8, "table": 6, "figure": 6}
        for query in queries:
            for entity_type in ("item", "finding", "table", "figure"):
                if len(self._collected) >= MAX_AGENT_RESULTS:
                    return operations
                self.tools.get("search_evidence").handler({
                    "query": query,
                    "entity_types": [entity_type],
                    "limit": limits[entity_type],
                })
                operations += 1
        return operations

    def _summary_candidates(self) -> list[dict[str, Any]]:
        quotas = {"item": 16, "finding": 14, "table": 9, "figure": 9}
        balanced: list[dict[str, Any]] = []
        for entity_type in ("item", "finding", "table", "figure"):
            balanced.extend(
                [item for item in self._collected if item["entity_type"] == entity_type][:quotas[entity_type]]
            )
        if len(balanced) < MAX_SUMMARY_RESULTS:
            identities = {(item["entity_type"], item["entity_id"]) for item in balanced}
            balanced.extend(
                item for item in self._collected
                if (item["entity_type"], item["entity_id"]) not in identities
            )
        output: list[dict[str, Any]] = []
        for item in balanced[:MAX_SUMMARY_RESULTS]:
            compact = _compact_result(item["entity_type"], item["payload"], item["ref"])
            compact["matched_queries"] = item["matched_queries"][:4]
            output.append(compact)
        return output

    def _deterministic_answer(self, candidates: list[dict[str, Any]]) -> tuple[str, set[str]]:
        selected: list[dict[str, Any]] = []
        paper_counts: dict[int, int] = {}
        for row in candidates:
            paper_id = int(row.get("paper_id") or 0)
            if paper_counts.get(paper_id, 0) >= 2:
                continue
            selected.append(row)
            paper_counts[paper_id] = paper_counts.get(paper_id, 0) + 1
            if len(selected) >= 6:
                break
        if len(selected) < 3:
            selected = candidates[:6]
        lines = [
            "DeepSeek 本轮没有生成稳定的综合结论；系统已保留覆盖性检索结果。以下是相关度最高的候选证据，需结合原文确认是否同时满足全部条件："
        ]
        refs: set[str] = set()
        for row in selected:
            ref = str(row.get("ref") or "")
            refs.add(ref)
            title = str(row.get("title") or row.get("label") or "证据记录")
            article = str(row.get("article_title") or "未命名文章")
            lines.append(f"- [{ref}] {title}——{article}")
        return "\n".join(lines), refs

    def _summarize(self, prompt: str, queries: list[str]) -> tuple[str, set[str], str]:
        candidates = self._summary_candidates()
        available = {str(row.get("ref")) for row in candidates}
        messages = [
            {
                "role": "system",
                "content": (
                    "你是实验文献证据库的中文图书管理员。候选证据来自覆盖性召回，并不都同时满足问题条件。"
                    "先判断数据库是否存在同时满足材料、粒子/辐照类型、温度和物理量等硬条件的直接证据；"
                    "若不存在，必须明确写‘未检索到直接证据’，再把部分满足条件的内容列为相关证据，不能混称为直接回答。"
                    "回答要解释科学含义，并用[R1]格式引用3到8项最有用证据；实际有效证据不足3项时可以更少。"
                    "每个报告数值后必须紧邻引用包含该数值的候选；比较辐照前后变化时必须引用同一种材料的前值和后值，"
                    "不得把不同材料的数值拼成一组比较。找不到成对证据时只做定性表述并说明限制。"
                    "禁止编造候选之外的论文、数值或曲线点。只输出JSON对象："
                    "{\"answer\":\"中文回答\",\"selected_refs\":[\"R1\",\"R2\"]}。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"研究问题：{prompt}\n检索方案：{json.dumps(queries, ensure_ascii=False)}\n"
                    f"候选证据：{json.dumps(candidates, ensure_ascii=False, default=str)[:52_000]}"
                ),
            },
        ]
        try:
            payload = self.client.request_json(
                messages,
                task="analysis",
                max_tokens=3_600,
                thinking=False,
                temperature=0.0,
            )
            answer = str(payload.get("answer") or "").strip()
            raw_refs = payload.get("selected_refs") or []
            selected = {
                f"R{match.group(1)}"
                for value in raw_refs if isinstance(value, (str, int))
                if (match := re.search(r"(\d+)", str(value)))
            } & available
            selected.update(_cited_refs(answer) & available)
            if answer and selected and not _is_internal_protocol(answer):
                missing = [ref for ref in sorted(selected, key=lambda value: int(value[1:])) if f"[{ref}]" not in answer]
                if missing:
                    answer = f"{answer}\n\n证据索引：{' '.join(f'[{ref}]' for ref in missing)}"
                return answer, selected, "deepseek_json"
        except Exception:
            pass
        try:
            message = self.client.request_tool_message(messages, [], task="analysis", max_tokens=3_600, temperature=0.0)
            answer = str(message.get("content") or "").strip()
            selected = _cited_refs(answer) & available
            if answer and selected and not _is_internal_protocol(answer):
                return answer, selected, "deepseek_text_fallback"
        except Exception:
            pass
        answer, selected = self._deterministic_answer(candidates)
        return answer, selected, "deterministic_fallback"

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
        queries, plan_mode = self._plan_recall(prompt, history)
        search_operations = self._run_recall(queries)
        if self._collected:
            answer, cited, summary_mode = self._summarize(prompt, queries)
        else:
            answer = "未在当前数据库中检索到与该问题相关的已收录证据。你可以改写材料名称、实验条件或物理量后重试。"
            cited = set()
            summary_mode = "no_results"
        public_results: list[dict[str, Any]] = []
        for item in self._collected[:MAX_AGENT_RESULTS]:
            row = dict(item["payload"])
            row["agent_ref"] = item["ref"]
            row["agent_entity_type"] = item["entity_type"]
            row["agent_cited"] = item["ref"] in cited
            row["agent_match_queries"] = item["matched_queries"][:4]
            public_results.append(row)
        result = {
            "agent": {"id": agent.agent_id, "name": agent.name},
            "answer": answer,
            "results": public_results,
            "tool_calls": len(queries),
            "search_operations": search_operations,
            "candidate_count": len(public_results),
            "cited_count": len(cited),
            "recall_queries": queries,
            "plan_mode": plan_mode,
            "summary_mode": summary_mode,
            "scope": {"paper_ids": [], "mode": "all"},
            "model": self.client.settings.analysis_model,
            "cache_hit": False,
        }
        self._cache_put(cache_key, result)
        return result


def build_agent_catalog(db: EvidenceDB) -> list[dict[str, Any]]:
    return LibrarianAgentRuntime(db).agents.public_list()
