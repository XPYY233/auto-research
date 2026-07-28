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

## 工作台与协议兼容增量

- Agent 模式固定全库检索；前端不传论文范围，工具 schema 不再接受 `paper_ids`。精确检索继续独立支持题目、作者和 DOI 范围。
- 浏览器本地最多保留 16 个会话，并受 2.5 MB 总量限制。每个会话保存最近消息、已返回结果和模型元数据；恢复不发请求，新轮仅发送最近 8 条消息。
- 结果继续复用既有四类型 payload，在前端用横向标签分组。若最终回答带 `[R#]`，响应只携带被引用的记录；无引用的安全降级回答保留已收集候选。
- 新增 DSML 兼容解析：将 `<｜｜DSML｜｜invoke>` 与 parameter 节点恢复为白名单工具调用。DSML 历史被丢弃，页面也有二次显示拦截。
- 每轮零工具调用时由运行器执行一次受限的全库检索，避免历史答案产生孤立引用；工具循环结束后改用 `request_json` 生成单字段中文总结。
- 最终摘要提示严格遵守问题中的材料、粒子、温度和实验类型限制；不满足条件的候选不得作为结论，直接证据不足时必须说明不足。
- 前端先转义再渲染有限 Markdown：标题、粗体、列表、表格和 `[R#]` 徽标。没有引入第三方 Markdown 运行时，也不执行模型 HTML。
- 加载进度展示五个阶段与真实秒数；使用本机 Codex 6 帧工作宠物条带，`prefers-reduced-motion` 下停止动画。

## 增量验证

- DSML 模拟、零工具调用强制检索、四类型返回和全库范围单元测试通过。
- 真实 DeepSeek 回归首先复现 DSML 泄漏，修复后同一问题返回正常中文总结，无 `DSML/tool_calls/invoke` 文本，并绑定 5 项实际证据。
- 浏览器验证历史恢复、旧异常回答拦截、横向分类结果、Codex 宠物动画和安全 Markdown 渲染。
- 全量 193 项测试通过；SQLite schema v12 完整性、外键、291 个图表文件、质量发布关系和 403/403 原文高亮通过。
