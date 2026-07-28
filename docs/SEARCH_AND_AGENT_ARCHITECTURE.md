# Search V2 与内置 Agent 架构

## 目标

让非数据库用户直接用自然语言查找证据，同时保留精确字段检索、原文回溯和导出能力。该层只读取现有科学记录，不改变证据权威来源。

## 数据流

```text
六列事实 / 定性结论 / 原始表格 / 论文图片
                    │
                    ▼
       search_index_documents + FTS
          （可删除、可重建的投影）
                    │
         ┌──────────┴──────────┐
         ▼                     ▼
     精确检索             图书管理员 Agent
    既有四模式       DeepSeek + 只读工具注册表
         │                     │
         └──────────┬──────────┘
                    ▼
         既有结果卡、详情、原文证据
```

## 不变量

- `data_items/data_versions`、定性结论聚类和 `visual_assets` 仍是科学记录来源。
- Search V2 只保存用于检索的反规范化 JSON 投影，不允许从索引反向写回科学数据。
- Agent 结果类型固定为 `item/table/figure/finding`；不新增第五种“AI 结果”。
- Agent 工具是只读白名单，最多调用 6 次；参数在本地校验，返回数量受限。
- Agent 的 `[R编号]` 必须对应实际返回结果。DeepSeek 失败时精确检索继续工作。
- 本地编辑端和公网只读端只维护一套 HTML、JavaScript 和 CSS；权限由服务端模式决定。

## 索引维护

- `evidence-search-reindex`：全量重建。用于结构升级、词典变化和发布前维护。
- 普通新增、修正或图表校对后，`ensure_fresh()` 比较论文级指纹，只刷新变化论文。
- `evidence-search-benchmark`：运行固定中文整句、关键词、短性质和单字元素查询。
- 索引格式改变时递增 `INDEX_FORMAT_VERSION`，强制安全重建。

## 扩展新 Agent

1. 在 `AgentRegistry` 注册新的 `AgentDefinition`。
2. 优先复用 `ToolRegistry` 中已有只读工具；只有出现新的稳定领域能力才新增工具。
3. 前端从 `/api/agents` 读取 Agent 清单，避免把未来 Agent 名称散落在多个页面。
4. 新 Agent 默认无写权限。任何写入能力必须单独设计确认流程、审计记录和只读端禁用规则。

## 安全与隐私

- API key 继续只从项目环境或 macOS Keychain 读取，不写日志、数据库或 Git。
- Agent 只向 DeepSeek 发送用户问题和命中的结构化短证据，不上传整库或整篇 PDF。
- 公网 Agent 接口具有进程内速率限制；只读端不能上传、校对或启动抽取。
- 日志只记录耗时、工具次数和错误类别，不记录密钥。
