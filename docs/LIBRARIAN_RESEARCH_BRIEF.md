# 图书管理员研究简报

适用版本：`2026.07.30-librarian-brief-stable.1`
导出结构：`librarian-research-brief-v1`
证据库结构：schema v12

> 产品入口更新（2026-08-01）：当前在 macOS 桌面工作台中开发验证，正式用户端预计为 Windows。下文的浏览器/只读描述仅保留为内部兼容与权限契约；用户在桌面 App 内使用本功能。

## 它是什么

研究简报是当前一次图书管理员结构化公开回答的确定性 Markdown 派生导出。它复用已经完成的五段报告和公开结果卡，不重新调用 DeepSeek，不重新检索 SQLite 或 PDF，也不新增任何科学事实。

简报仍然只有四类证据：

- `item`：数据条目；
- `finding`：实验结论；
- `table`：原始表格；
- `figure`：论文图片。

它不是第五类“AI 结果”，也不是新的科学数据库、会话备份或 PDF 摘要器。

## 普通使用方式

1. 在个人桌面工作台中打开“图书管理员”。
2. 提交研究问题并等待当前轮结构化五段报告完成。
3. 当前回答通过服务端导出资格门后，点击输入框旁的“导出研究简报”。
4. App 保存 `librarian-research-brief.md`。

按钮只对当前服务进程签名、通过一致性门、不是澄清回答且至少有一条实际引用证据的最新结构化回答开放。以下情况不能导出：

- 回答仍在生成、请求失败或没有结构化 `report`；
- `clarification` 澄清回答；
- 报告没有规范 R#，或最终没有可解析的实际引用；
- R# 与 `agent_cited` 公开结果、引用计数或四类证据身份不一致；
- 当前进程签名缺失、被篡改或已经失效。

恢复旧历史不会重新调用模型，但也不会自动获得当前进程的导出资格。服务重启后，旧回答的进程级签名自然失效；用户必须重新提交问题完成一次新检索，再导出新的简报。

App 内部服务允许这个导出请求，因为服务端只验证并转换自己在当前进程签发的公开快照；它不写科学数据库或项目文件。历史只读模式继续验证同一权限边界，但不再作为公开产品。

## 签名快照信封

导出接口为：

```http
POST /api/agents/librarian/research-brief.md
Content-Type: application/json
```

图书管理员聊天响应顶层附带：

```json
{
  "research_brief": {
    "snapshot_token": "当前进程签发的不透明 HMAC token",
    "answered_at": "回答时间",
    "evidence_fingerprint": "当前证据版本",
    "eligible": true,
    "ineligible_reason": ""
  }
}
```

前端只有在 `eligible=true` 时开放按钮，并把当前内存中的公开快照与 token 原样回传：

```json
{
  "snapshot": {
    "question": "用户本轮研究问题",
    "report": {
      "direct_conclusion": {
        "status": "found",
        "text": "当前回答的直接结论。",
        "refs": ["R1"]
      },
      "evidence_matrix": [],
      "related_evidence": [],
      "database_gaps": [],
      "suggested_followups": []
    },
    "query_analysis": {},
    "results": [
      {
        "agent_ref": "R1",
        "agent_entity_type": "finding",
        "agent_cited": true
      }
    ],
    "model": "deepseek-v4-pro",
    "plan_mode": "",
    "summary_mode": "",
    "candidate_count": 0,
    "cited_count": 1,
    "match_counts": {
      "direct": 1,
      "adjacent": 0,
      "expansion": 0
    },
    "bundle_count": 1,
    "answered_at": "回答时间",
    "evidence_version": "与 evidence_fingerprint 相同的值"
  },
  "snapshot_token": "原样回传的当前进程 token"
}
```

其中 `question`、结构化 `report`、公开 `results`、`answered_at` 和 `evidence_version` 一起构成 HMAC 绑定的公开快照；其余字段用于硬条件、模型与召回统计。`evidence_version` 的值必须等于响应信封中的 `evidence_fingerprint`。资格字段控制界面和服务端门禁，但不替代验签。

接口不接受会话 ID 代替 `{snapshot, snapshot_token}`，也不接受原始 SQLite、PDF、文件路径或“请重新检索”指令。服务端不会根据会话 ID 查找桌面历史。`research_brief` 授权只存在于当前页面的瞬态 `state.librarianBriefAuth`；history schema 和保存策略完全不变。重启或只恢复历史后只能重新检索，不能重新签发旧回答。

服务端 HMAC 只绑定规范化公开响应快照。它不是会话存储，不写数据库，不重查 Search V2/PDF，也不调用模型。当前进程使用临时秘密签发并以恒定时间比较验签，进程重启后旧 token 自然失效。

验签和一致性门通过后，同一快照和同一 `generated_at` 输入会得到相同结构及相同 Markdown。网页导出使用当次生成时间，因此不同时间下载的文件会在生成时间字段上不同。

## 导出结构

结构化简报包含：

| 字段 | 含义 |
|---|---|
| `schema_version` | 固定为 `librarian-research-brief-v1` |
| `generated_at` | 导出时间 |
| `question` | 本轮研究问题 |
| `hard_conditions` | 快照中本地确定性解析出的硬条件 |
| `report` | 直接结论、证据矩阵、相关证据、数据库缺口、建议追问 |
| `cited_evidence` | 五段报告实际引用并能在公开 `results` 中解析的 R# |
| `evidence_by_type` | 同一批已引用证据按四类分组 |
| `statistics` | 模型、候选、引用、匹配分级与 bundle 计数 |
| `integrity` | 引用、类型、段落和声明计数检查 |
| `limitations` | 固定显示的科学与数据边界 |

Markdown 顺序固定为研究问题、硬条件、五段报告、引用证据附录、模型与召回统计、完整性检查、明确限制。输入结果顺序变化不会改变 R# 排序后的导出顺序。

## 引用完整性

简报只使用结构化报告中的规范 R# 引用，然后要求每条引用同时满足：

1. R# 确实被当前报告引用；
2. R# 能在当前快照的 `results` 中解析；
3. 对应结果明确为 `agent_cited=true`；
4. R# 在结果中唯一，引用数与声明计数完全一致；
5. 结果类型属于 `item/finding/table/figure`。

未被报告引用或 `agent_cited=false` 的候选不会进入附录。孤儿引用、同一 R# 的重复结果、无效证据类型、缺失报告段落、声明计数不一致、零引用或因上限无法完整收录都会使一致性门失败，服务端不生成文件。

论文题目、DOI、页码或原文片段缺失时保持为空，并列入 `missing_provenance`；程序不会推测补齐。缺失来源允许生成简报，但必须令 `integrity.status=warning`，并在下载的 Markdown 完整性区逐条展示 R# 与缺失字段。warning 表示用户必须回到原证据核验，不表示程序已经修复或替换了来源。

## 隐私边界

导出采用字段白名单，而不是把结果对象整体序列化。允许的内容限于研究问题、五段报告、公开硬条件/统计、论文题目、DOI、页码、原文定位/短片段，以及四类证据各自的公开描述字段。

以下内容不得进入简报：

- 会话 ID、桌面历史记录和历史存储密钥；
- 本地文件路径、`file://`、图片/PDF 内部 URL；
- Zotero key、本机文章 key 或设备标识；
- 审核者身份、内部编辑备注和质量门内部记录；
- PDF 字节、截图文件或整个数据库；
- API key、完整提示词或模型内部协议。

已进入白名单文本的 URL 与本地路径仍会被移除。导出响应使用 `no-store`，不会成为服务器端会话存储。

## 科学边界

- 不重新调用 DeepSeek，不让模型重写报告或引用。
- 不重新查询 Search V2、SQLite、Zotero 或 PDF。
- 不读取图片像素，不从曲线生成数据点。
- 不把缺失元数据补成看似完整的来源。
- 不新增跨 evidence bundle 的定量比较。
- 不把 `adjacent` 或 `expansion` 自动升级为直接证据。
- 不创建、修正、确认、发布或审核科学记录。

简报只重放当前公开回答已经做出的陈述。若需要改变问题、条件、引用或证据范围，应重新向图书管理员提问，再从新的结构化回答导出。

## 对话历史与桌面边界

研究简报导出与历史存储彼此独立：

- Apple Silicon macOS 桌面产品层可用 macOS Keychain 管理密钥，并以 AES-GCM 持久化图书管理员历史；
- 普通本地浏览器在没有桌面安全桥接时仍可使用有限的浏览器本地历史；
- 公网只读端固定为 `readonly-none`，不读取桌面历史，也不使用 `localStorage` 保存图书管理员会话；
- 任一模式的历史都不是科学证据，且不得写入 `db/experimental_evidence.sqlite`；
- history schema 和保存策略完全不变；
- `research_brief` 授权只存在于瞬态 `state.librarianBriefAuth`，不进入 session meta/messages、`localStorage` 或桌面加密历史；
- 任何前端状态都不包含 HMAC secret；
- 导出模块只接收当前内存中的 `{snapshot, snapshot_token}`，不调用桌面历史接口；
- 服务重启后旧 token 失效，恢复的旧回答必须重新检索后才能导出。

桌面产品层属于独立打包边界。共享前端的适配契约进入本核心稳定版，不代表 `desktop/macos/**` 源码已经随本核心提交发布。

## 本版验收

- 核心自动测试：253/253 通过。
- 覆盖当前进程 HMAC 验签、澄清/零引用拒绝、四类 `agent_cited` 证据、未引用候选排除、孤儿/重复/无效类型、来源缺失 warning、结果顺序确定性、隐私字段和路径/URL 清洗。
- 只读 HTTP 仅对签名且通过一致性门的非澄清、有引用回答返回 Markdown；篡改/过期 token、澄清、零引用和不一致快照被拒绝，其他写入接口继续返回 403。
- 真实只读浏览器以“钨”问题召回 65 项（0 direct / 5 adjacent / 60 expansion），报告引用 5 项；简报 POST 返回 200，控制台无错误。刷新后历史为 0、导出禁用且无运行告警。
- 导出前后生产 SQLite SHA-256 均为 `d62dc5c43ac9e0fb97e0ad2ecb85deaf50447fb7a036acae5147e8f6111236f6`。
- 数据库健康口径保持为 6,501 条原始记录、5,048 处可报告数值来源、3,142 个独立物理事实、937 条定性结论和 291 个图表资产。
- 固定语料保持 17/50 data-ready、30/50 visual-ready；本功能没有提高语料完成度，也不等于独立人工科学验收。
