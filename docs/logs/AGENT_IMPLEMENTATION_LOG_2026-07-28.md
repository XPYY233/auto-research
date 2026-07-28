# Agent 实施日志：Search V2 / Librarian

## 变更范围

- Schema `v11 -> v12`：增加 `search_index_documents` 与 `search_index_state`。
- 新模块 `evidence/search_index.py`：四类型投影、FTS、中文查询规划、元素别名、分页、论文级增量刷新。
- 新模块 `evidence/agent_runtime.py`：`AgentRegistry`、`ToolRegistry`、只读工具、调用上限、速率限制。
- `ai/deepseek.py`：增加 OpenAI-compatible tool-call assistant message 适配，不改变现有 JSON 抽取接口。
- `webapp.py`：增加 `/api/search-v2`、索引状态、Agent 清单和图书管理员对话路由；既有四个搜索 API 改用同一索引返回原结构。
- 前端：Agent 为默认体验，精确检索可随时切回；混合结果仍复用原详情和证据入口。

## 关键不变量

- 不删除或重写旧搜索函数；导出和历史兼容调用仍可使用。
- Search V2 payload 保存原返回结构，避免新建另一套前端卡片协议。
- 只读模式允许图书管理员 POST，但其他写入继续返回 403。
- `/`、`/index.html` 和 `/readonly` 均服务同一个 `index.html`，禁止维护 `readonly.html` 分叉。
- Agent 每次最多 6 次工具调用，每次最多 12 条，最终全部引用结果随响应返回。
- 本地编辑端与公网只读端加载同一 `index.html`、Search V2 和 Agent Runtime；只读端仅由服务端权限层拒绝科学数据写入。
- 最终回归：191 项测试通过，Search V2 四类对象共 4,370 条，SQLite schema v12 健康检查通过。

## 维护命令

```bash
PYTHONPATH=src python3 -m auto_research.cli evidence-search-reindex
PYTHONPATH=src python3 -m auto_research.cli evidence-search-benchmark
PYTHONPATH=src python3 -m auto_research.cli evidence-db-health
PYTHONPATH=src python3 -m unittest discover -s src/tests -q
node --check src/auto_research/evidence/web/app.js
```

## 后续扩展规则

- 新 Agent 先注册 definition，再复用工具；不要复制 SearchIndex 或四类结果 schema。
- 需要新工具时先证明既有 `/api/search-v2`、详情或 PDF 证据接口无法表达。
- 写操作不得加入通用 AgentRuntime；必须另建明确确认和审计边界。
- 修改 query planner、别名规则或 payload 字段后递增 `INDEX_FORMAT_VERSION`。
