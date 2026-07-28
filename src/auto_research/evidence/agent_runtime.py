from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from auto_research.ai.deepseek import DeepSeekClient

from .db import EvidenceDB
from .search_index import ENTITY_TYPES, EvidenceSearchIndex


MAX_AGENT_QUESTION_CHARS = 2_000
MAX_AGENT_HISTORY_MESSAGES = 8
MAX_AGENT_TOOL_CALLS = 6
MAX_AGENT_RESULTS = MAX_AGENT_TOOL_CALLS * 12


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
                "你是实验文献证据库的图书管理员。用户不需要会组织关键词；你要把自然语言问题拆成材料、"
                "实验类型、条件、物理量和证据类型，并使用工具检索。数据库只有四类合法结果：item数据条目、"
                "table原始表格、figure论文图片、finding实验结论。必须至少调用一次search_evidence后再回答；"
                "问题较宽时可分两到三次换关键词检索，但总工具调用不超过6次。优先返回直接相关且有原文页码的结果。"
                "不得编造数据库外论文、数值或曲线点，不得把visual interpretation写成直接测量。"
                "最终用中文给出简洁结论，引用工具结果编号如[R1]；明确说明检索范围和证据不足。"
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
                    "paper_ids": {"type": "array", "items": {"type": "integer"}},
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
        paper_ids = arguments.get("paper_ids") or []
        limit = min(max(int(arguments.get("limit") or 8), 1), 12)
        page = self.index.search(query, entity_types=entity_types, paper_ids=paper_ids, limit=limit)
        compact: list[dict[str, Any]] = []
        for row in page.rows:
            entity_type = "item" if "fact_id" in row else "finding" if "finding_id" in row else str(row.get("asset_type"))
            identity = (entity_type, int(row.get("item_id") or row.get("id") or 0))
            if not any((item["entity_type"], item["entity_id"]) == identity for item in self._collected):
                ref = f"R{len(self._collected) + 1}"
                full = {"ref": ref, "entity_type": entity_type, "entity_id": identity[1], "payload": row}
                self._collected.append(full)
            else:
                full = next(item for item in self._collected if (item["entity_type"], item["entity_id"]) == identity)
            compact.append(_compact_result(entity_type, row, full["ref"]))
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
            if content:
                output.append({"role": str(raw["role"]), "content": content})
        return output

    def run(self, question: str, *, history: Any = None, paper_ids: list[int] | None = None) -> dict[str, Any]:
        prompt = str(question or "").strip()[:MAX_AGENT_QUESTION_CHARS]
        if not prompt:
            raise ValueError("问题不能为空")
        agent = self.agents.get("librarian")
        self._collected = []
        scope = sorted({int(value) for value in paper_ids or []})
        scope_text = f"当前只允许检索论文ID：{scope}。" if scope else "当前检索范围为全部文章。"
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": f"{agent.system_prompt}\n{scope_text}"},
            *self._history(history),
            {"role": "user", "content": prompt},
        ]
        schemas = [self.tools.get(name).api_schema() for name in agent.tools]
        tool_count = 0
        answer = ""
        while tool_count < MAX_AGENT_TOOL_CALLS:
            message = self.client.request_tool_message(messages, schemas, task="analysis")
            calls = message.get("tool_calls") or []
            if not calls:
                answer = str(message.get("content") or "").strip()
                break
            messages.append(message)
            for call in calls:
                if tool_count >= MAX_AGENT_TOOL_CALLS:
                    break
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                    if name == "search_evidence" and scope:
                        arguments["paper_ids"] = scope
                    result = self.tools.get(name).handler(arguments)
                except Exception as exc:
                    result = {"error": str(exc)}
                messages.append({
                    "role": "tool",
                    "tool_call_id": str(call.get("id") or f"call-{tool_count + 1}"),
                    "content": json.dumps(result, ensure_ascii=False, default=str)[:24_000],
                })
                tool_count += 1
        if not answer:
            messages.append({
                "role": "system",
                "content": "工具调用已达到上限。请立即基于已有结果给出中文总结，不再调用工具。",
            })
            message = self.client.request_tool_message(messages, [], task="analysis")
            answer = str(message.get("content") or "").strip()
        if not answer:
            raise ValueError("图书管理员没有返回可展示的回答")
        public_results: list[dict[str, Any]] = []
        for item in self._collected[:MAX_AGENT_RESULTS]:
            row = dict(item["payload"])
            row["agent_ref"] = item["ref"]
            row["agent_entity_type"] = item["entity_type"]
            public_results.append(row)
        return {
            "agent": {"id": agent.agent_id, "name": agent.name},
            "answer": answer,
            "results": public_results,
            "tool_calls": tool_count,
            "scope": {"paper_ids": scope, "mode": "selected" if scope else "all"},
            "model": self.client.settings.analysis_model,
        }


def build_agent_catalog(db: EvidenceDB) -> list[dict[str, Any]]:
    return LibrarianAgentRuntime(db).agents.public_list()
