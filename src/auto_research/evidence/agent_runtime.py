from __future__ import annotations

import json
import re
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


_DSML_MARKER = "DSML"


def _dsml_tool_calls(content: Any) -> list[dict[str, Any]]:
    """Recover DeepSeek tool calls occasionally emitted as DSML text.

    Some DeepSeek gateways serialize an otherwise valid tool call into the
    assistant content field instead of the OpenAI-compatible ``tool_calls``
    field.  The DSML block is an internal protocol and must never become a
    user-facing answer.
    """

    raw = str(content or "")
    if _DSML_MARKER not in raw:
        return []
    normalized = raw.replace("｜", "|")
    invokes = re.findall(
        r'<\|\|DSML\|\|invoke\s+name="([^"]+)">(.*?)</\|\|DSML\|\|invoke>',
        normalized,
        flags=re.DOTALL,
    )
    calls: list[dict[str, Any]] = []
    for index, (name, body) in enumerate(invokes, start=1):
        arguments: dict[str, Any] = {}
        for param_name, string_flag, value in re.findall(
            r'<\|\|DSML\|\|parameter\s+name="([^"]+)"(?:\s+string="([^"]+)")?>(.*?)</\|\|DSML\|\|parameter>',
            body,
            flags=re.DOTALL,
        ):
            value = value.strip()
            if string_flag.lower() == "true":
                parsed: Any = value
            else:
                try:
                    parsed = json.loads(value)
                except (TypeError, ValueError):
                    parsed = value
            arguments[param_name] = parsed
        calls.append({
            "id": f"dsml-call-{index}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
        })
    return calls


def _is_internal_protocol(content: Any) -> bool:
    return _DSML_MARKER in str(content or "")


def _cited_refs(answer: str) -> set[str]:
    refs = {f"R{value}" for value in re.findall(r"\bR(\d+)\b", answer or "")}
    for start, end in re.findall(r"\bR(\d+)\s*[\-–—]\s*R?(\d+)\b", answer or ""):
        first, last = int(start), int(end)
        if first <= last and last - first <= MAX_AGENT_RESULTS:
            refs.update(f"R{value}" for value in range(first, last + 1))
    return refs


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
                "不要引用或列出已经判断为无关、仅用于排除的候选结果。"
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
            if content and not _is_internal_protocol(content):
                output.append({"role": str(raw["role"]), "content": content})
        return output

    def run(self, question: str, *, history: Any = None) -> dict[str, Any]:
        prompt = str(question or "").strip()[:MAX_AGENT_QUESTION_CHARS]
        if not prompt:
            raise ValueError("问题不能为空")
        agent = self.agents.get("librarian")
        self._collected = []
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": f"{agent.system_prompt}\n当前检索范围固定为全部文章，不接受论文范围限制。"},
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
                calls = _dsml_tool_calls(message.get("content"))
                if calls:
                    message = {"role": "assistant", "content": "", "tool_calls": calls}
            if not calls:
                if tool_count == 0:
                    # A history-aware model may try to answer from earlier text
                    # without touching the current database.  Run one bounded
                    # full-library search so every response has fresh evidence.
                    result = self._search_tool({
                        "query": prompt,
                        "entity_types": ["item", "table", "figure", "finding"],
                        "limit": 12,
                    })
                    messages.append({
                        "role": "system",
                        "content": (
                            "你刚才没有调用检索工具。系统已为本轮执行一次完整文献库检索。"
                            "必须仅根据以下结果生成中文回答并引用真实编号；不得沿用历史回答中的旧编号：\n"
                            + json.dumps(result, ensure_ascii=False, default=str)[:24_000]
                        ),
                    })
                    tool_count = 1
                    continue
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
            evidence = [
                _compact_result(item["entity_type"], item["payload"], item["ref"])
                for item in self._collected[:MAX_AGENT_RESULTS]
            ]
            try:
                summary = self.client.request_json(
                    [
                        {
                            "role": "system",
                            "content": (
                                "你是实验文献证据库的中文图书管理员。只根据给定候选证据回答问题。"
                                "输出JSON对象，且只能包含answer字符串。answer须概括直接相关证据并用[R1]格式引用；"
                                "必须严格满足问题中明确限定的材料、粒子、温度和实验类型；不满足条件的候选不得用于回答。"
                                "不引用无关候选，不编造数值、论文或曲线点；没有直接证据时必须明确回答证据不足。"
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"研究问题：{prompt}\n候选证据："
                                + json.dumps(evidence, ensure_ascii=False, default=str)[:48_000]
                            ),
                        },
                    ],
                    task="analysis",
                    max_tokens=3_200,
                    thinking=False,
                    temperature=0.1,
                )
                answer = str(summary.get("answer") or "").strip()
            except Exception:
                answer = ""
        if _is_internal_protocol(answer):
            answer = ""
        if not answer and self._collected:
            counts = {
                entity_type: sum(1 for item in self._collected if item["entity_type"] == entity_type)
                for entity_type in ("item", "table", "figure", "finding")
            }
            answer = (
                f"已从完整文献库检索到{len(self._collected)}项候选证据："
                f"数据条目{counts['item']}项、表格{counts['table']}张、图片{counts['figure']}幅、"
                f"实验结论{counts['finding']}项。模型本次未生成可靠的中文综述，内部检索指令已隐藏；"
                "请通过下方分类结果核对原文证据，或重新发送问题。"
            )
        if not answer:
            raise ValueError("图书管理员没有返回可展示的回答")
        cited = _cited_refs(answer)
        selected_results = [item for item in self._collected if not cited or item["ref"] in cited]
        public_results: list[dict[str, Any]] = []
        for item in selected_results[:MAX_AGENT_RESULTS]:
            row = dict(item["payload"])
            row["agent_ref"] = item["ref"]
            row["agent_entity_type"] = item["entity_type"]
            public_results.append(row)
        return {
            "agent": {"id": agent.agent_id, "name": agent.name},
            "answer": answer,
            "results": public_results,
            "tool_calls": tool_count,
            "scope": {"paper_ids": [], "mode": "all"},
            "model": self.client.settings.analysis_model,
        }


def build_agent_catalog(db: EvidenceDB) -> list[dict[str, Any]]:
    return LibrarianAgentRuntime(db).agents.public_list()
