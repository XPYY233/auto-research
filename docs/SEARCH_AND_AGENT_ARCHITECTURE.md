# Search V2 与内置 Agent 架构

## 目标

让非数据库用户直接用自然语言查找证据，同时保留精确字段检索、原文回溯和导出能力。该层只读取现有科学记录，不改变证据权威来源。

自 2026-08-01 起，唯一产品形态是个人桌面工作台。当前在 macOS 开发预览，正式用户端预计为 Windows。本文中的 HTML、HTTP 和只读模式均指 App 内部实现或历史兼容测试；浏览器工作台、导师只读页、8765/8766 和 ngrok 不再作为产品发布。

## 数据流

```text
六列事实 / 定性结论 / 原始表格 / 论文图片
                    │
                    ▼
       search_index_documents + FTS v3
          （可删除、可重建的投影）
                    │
         ┌──────────┴──────────┐
         ▼                     ▼
     精确检索             图书管理员 Agent
    既有四模式       DeepSeek规划 + 本地硬条件解析
                          → 本地四类覆盖召回与分级
                          → DeepSeek证据选择/语义总结
                          → 本地五段式报告完整性门
         │                     │
         └──────────┬──────────┘
                    ▼
         既有结果卡、详情、原文证据
                    │
                    ▼
   当前进程签名公开回答（内存、只读）
                    │
                    ▼
     确定性研究简报导出 → Markdown 下载
```

## Librarian V3 核心（平台中立，尚未接入 UI）

V3 在既有四类证据和五段报告外增加一层本地控制平面；旧字段 `answer/report/results`、旧接口路径与四类详情契约保持不变。

```text
用户问题
  → 本地意图路由
     ├─ system_capability / conversation → 0 召回、0 模型
     ├─ clarification                  → 本地澄清、0 召回
     ├─ followup_ref / followup_bundle → 验签后解析稳定 anchor
     ├─ research_lookup                → focused 召回
     └─ research_review                → review_map 主题聚类 + 有界代表证据
  → 本地硬条件、direct/adjacent/expansion、bundle 与引用完整性门
  → DeepSeek V4 Pro 仅综合有界公开 DTO
  → 本地建议追问 dry-run、状态签名与兼容响应
```

本地意图固定为 `system_capability/research_lookup/research_review/followup_ref/followup_bundle/clarification/conversation`；召回策略固定为 `none/resolve_anchors/focused/review_map`。系统能力问答读取本地 `capability-manifest-v1`，可说明 Flash 规划、Pro 综合、文本模型和本地确定性边界，但不得返回密钥、底层路径或内部异常。

### `research-state-v1`

每轮研究回答新增 path-free `research_state` 和当前进程 `state_token`。状态包含：`conversation_id`、`turn_id`、`evidence_version`、由本地硬条件生成的非原文主题摘要和结构化条件、R# 到 `source_scope/source_id/entity_uid/entity_type` 的稳定映射、B# 到 `bundle_uid/member_entity_uids` 的映射、已选择 anchor、签发/过期时间、父状态哈希和当前状态哈希。状态不保存用户原始 prompt。R#、B# 只是本轮显示别名；后续解析只认验签后的稳定身份。

状态签名绑定会话、轮次、证据指纹、完整状态哈希和父状态链。篡改、过期、跨会话、语料变化、未知/重复 anchor 或 bundle 成员都 fail closed。令牌第一次只绑定一个请求指纹：相同请求可从完整响应缓存幂等返回，不同问题复用同一令牌会在模型调用前拒绝；若幂等结果已被逐出内存，也拒绝再次付费调用。签名器和时钟可注入测试，生产默认使用当前进程随机 HMAC 密钥，不持久化到历史或数据库。

### 宽泛综述与建议动作

`review_map` 先按现有候选的研究用途/机制做本地主题聚类，每个主题最多选择少量跨论文代表 R#，再交给 Pro 做定性综合；不会把几十张卡直接堆给模型。跨不兼容 bundle 只能分别陈述，定量比较返回稳定错误码 `unsupported_comparison`。

旧 `report.suggested_followups` 继续保留，但内容来自已通过本地验证的 `suggested-action-v1`。新顶层 `suggested_actions` 包含 `text/intent/anchor_refs/bundle_uid/estimated_matches/answerable/reason_code`。模型建议必须通过当前有界候选 dry-run；未知 R#、无本地证据或催化/光学/电池等领域漂移会被剔除，再用可解析 anchor 生成确定性建议。

### 新增响应字段与兼容边界

- `librarian_core_version=librarian-v3`
- `intent`（`intent-decision-v1`）与 `retrieval_policy`
- `research_state`、`state_token`
- `suggested_actions`（`suggested-action-v1`）
- `report.review_map`（仅宽泛综述）
- 结果可附 `source_scope/source_id/entity_uid/agent_bundle_uid`

缓存键加入意图、研究状态指纹和 `evidence_version`。DeepSeek 超时、非法/超长 JSON 或不可用时，系统能力回答、稳定 anchor 解析和确定性报告仍工作。候选论文文本中的提示词只作为有界证据字符串，不能改变本地意图、硬条件、候选、引用、预算或写权限。本阶段不修改 web、desktop、个人库、产品资料包或科学数据库；UI 需要在后续独立接线时原样转发状态和令牌。

## 不变量

- `data_items/data_versions`、定性结论聚类和 `visual_assets` 仍是科学记录来源。
- Search V2 只保存用于检索的反规范化 JSON 投影，不允许从索引反向写回科学数据。
- Agent 结果类型固定为 `item/table/figure/finding`；不新增第五种“AI 结果”。
- Agent 复用只读工具注册表；检索参数在本地校验，总候选和四类候选分别设上限。
- Agent 的 `[R编号]` 必须对应实际返回结果。DeepSeek 失败时精确检索继续工作。
- 图书管理员固定使用全库范围；论文范围控件只属于精确检索。除关键对象不明确、必须先澄清的请求外，每一轮新问题都执行当前数据库检索，历史回答不能替代新检索。
- 本地确定性解析材料、辐照类型、粒子、温度、剂量/注量、物理量和样品状态。用户原话中的这些字段是硬条件；同义词、元素名称/符号和英文写法只扩展召回，不提升为额外硬条件。
- 当前轮显式条件优先于历史。当前轮改变辐照类型或粒子时，成对的历史束流条件不得继承；“那缺陷呢”等省略式追问才继承上一轮未重述的材料与实验条件。
- DeepSeek V4 Flash 首先把长问题拆成若干短检索式；本地保底规划同时生成严格组合与材料/辐照类型/性质的分解查询，模型规划失败时也能继续召回。硬条件解析与最终证据分类仍由本地程序负责。
- 本地程序对 `item/table/figure/finding` 分别检索并去重，避免某一种类型占满结果。宽泛概念只在 Agent 层增加有限同义扩展，不改变精确搜索语义。
- 候选由本地程序划分为：`direct`（全部硬条件命中）、`adjacent`（恰好缺一个硬条件）和 `expansion`（缺两个或更多）。DeepSeek 只能选择，不能重写该分层。
- 证据按论文、记录中的实际材料和完整实验条件组成 bundle。跨 bundle 禁止自动进行定量前后比较。
- 最终证据选择与中文综合继续使用 DeepSeek V4 Pro。响应保留兼容字段 `answer/results`，并新增结构化 `report/query_analysis/evidence_bundles/match_counts`。报告固定为直接结论、证据矩阵、相关证据、数据库空白和建议追问五部分。
- 顶层 `recommended_articles` 将同一轮已召回四类证据按 `paper_id` 聚合为论文推荐，不执行第二套搜索、不新增第五类证据，也不改变五段报告引用。推荐优先 direct/adjacent；只有没有更近论文时才显示 expansion，并明确标为拓展阅读。
- 推荐论文同时携带全库 Search V2 四类覆盖计数。若某篇只有 table/figure 而没有 item/finding，页面显示“全文数值/结论尚未抽取”的覆盖预警；这表示处理阶段不完整，不表示论文没有数据。
- 页面保留全部有界候选并使用 `agent_cited` 标记最终报告引用；四类标签展示直接/相关/扩展数量，卡片显示证据组和被放宽的条件，默认打开直接引用最集中的类型。
- 同一数据库证据版本、同一问题和同一有限历史可在服务进程内复用一小时；缓存键包含检索源指纹，事实、结论或图表变化后自动失效。缓存只减少重复模型调用，不取代首次覆盖召回。
- 对话历史不进入 SQLite，也不作为证据权威来源。正式 App 用 Keychain 管理密钥并以 AES-GCM 持久化；`browser-local` 与 `readonly-none` 仅为兼容测试。恢复历史不调用模型。
- App 内部只维护一套 HTML、JavaScript 和 CSS；历史权限模式由内部服务决定，不构成第二个用户产品。
- 共享前端只负责桌面历史适配契约，桌面产品层是独立打包边界；不能把前端适配写成 `desktop/macos/**` 已随核心提交发布。
- 研究简报只是当前公开结构化回答的派生只读导出，不是第五类证据、对话持久化或新的 Agent 调用。
- 研究简报只接受当前进程 HMAC 签名、非澄清、有实际引用且通过一致性门的回答。旧历史在服务重启后必须重新检索，不能重新签名。

## 统一搜索体验契约

搜索入口把用户选择拆成两个互不混淆的维度：

- `requested_scope` 表示内容领域：`literature`（文献数据库）、`personal`（我的实验）或 `all`（两者一起）。个人实验库尚未建立时必须明确返回不可用或部分范围，不能把文献结果伪装成用户自己的记录。
- `source_scope` 表示资料来源的信任边界：`private`（用户私有库）或 `official`（签名只读资料包）。`source_id` 标识已挂载的数据源，`entity_uid` 为未来跨资料包稳定定位同一实体预留。当前单库模式允许三者为空。

`search-route-v1` 只做本地确定性分诊，不打开数据库、不重建索引、不调用模型。体验模式为 `exact`、`librarian`、`browse` 和 `filter`；`auto` 根据问题信号选择 `exact` 或 `librarian`，用户始终可以覆盖自动判断。带 DOI、样品号、批次号或明确定位语言的问题优先精确搜索；解释、比较、趋势和综合问题交给图书管理员。即使进入统一入口，DeepSeek 不可用时精确搜索仍须独立工作。

文献检索结果可附带 `source_scope/source_id/entity_uid`，但 `entity_type` 仍严格限定为 `item/table/figure/finding`。这些来源字段不是第五类证据，也不能改变原证据内容或审核状态。个人实验记录将使用独立私有数据模型，不能塞入论文六列事实表。

## 索引维护

- `evidence-search-reindex`：全量重建。用于结构升级、词典变化和发布前维护。
- 普通新增、修正、图表校对、质量状态或数据—图表关联变化后，`ensure_fresh()` 比较论文级指纹，只刷新变化论文。`visual_asset_reviews` 是真实图表版本表，不得再引用历史上不存在的 `visual_asset_versions`。
- `evidence-search-benchmark`：运行固定中文整句、关键词、短性质和单字元素查询。
- 索引格式改变时递增 `INDEX_FORMAT_VERSION`，强制安全重建。

## 扩展新 Agent

1. 在 `AgentRegistry` 注册新的 `AgentDefinition`。
2. 优先复用 `ToolRegistry` 中已有只读工具；只有出现新的稳定领域能力才新增工具。
3. 前端从 `/api/agents` 读取 Agent 清单，避免把未来 Agent 名称散落在多个页面。
4. 新 Agent 默认无写权限。任何写入能力必须单独设计确认流程、审计记录和只读端禁用规则。

## 安全与隐私

- API key 继续只从项目环境或 macOS Keychain 读取，不写日志、数据库或 Git。
- Agent 只向 DeepSeek 发送用户问题和命中的结构化短证据，不上传整库或整篇 PDF。DeepSeek V4 API 是文本模型；论文图片仍由本地 PyMuPDF 建档，模型只读取图注和邻近文字语义。
- App 内部 Agent 接口具有进程内速率限制；历史只读权限模式仍不能上传、校对或启动抽取。
- DeepSeek 采用 BYOK：新文献提取和图书管理员均使用当前用户自己的 API key。首次需要 AI 时，App 应用非技术语言说明密钥用途、发送对象、费用和上传范围；密钥只进入平台安全凭据库（macOS Keychain / Windows Credential Manager），不进入 SQLite、数据包、日志或 Git。
- 日志只记录耗时、工具次数和错误类别，不记录密钥。

## 回答完整性门

1. DeepSeek 规划只产生短检索式，程序合并本地保底检索式并限制数量。
2. 每个检索式都由本地 Search V2 执行，历史回答不能替代当前数据库召回。
3. DeepSeek 总结只能引用实际候选编号；孤儿引用、无证据数值、跨证据组定量比较和把相邻证据写成直接结论都会被本地完整性门拒绝。
4. JSON 总结失败时依次使用无工具文本总结和确定性候选摘要；任何 DSML/内部协议均被拒绝。
5. 页面隐藏历史 DSML 和没有绑定结果的旧 `[R编号]`，并为旧历史按回答引用自动补齐卡片标记。
6. 结果页同时公开“召回总数”和“回答引用数”，所有卡片仍使用既有四类详情与原文证据接口。
7. 相同输入命中稳定缓存时，报告、条件解析、证据组、引用与候选必须整体复用并在页面明确提示，不能只缓存回答文字。

## 回答呈现

- 最新一轮结构化报告显示已解析硬条件、五个固定分区和可点击 `[R#]`；历史轮引用只作记录，不能错误跳到当前卡片。
- 证据矩阵固定展示材料、辐照/实验条件、性质、结果和论文证据。相关证据必须写出“放宽条件”。
- 建议追问提供二到三个可直接继续提交的问题。提交新问题时先清空旧证据卡，避免旧结果挂到新问题下。
- 结果继续使用条目、结论、表格、图片四个横向标签；不存在第五类 Agent 结果。
- 进度场景明确标注为预计阶段，真实耗时不进入高频无障碍播报。

## 研究简报派生导出

### 输入与数据来源

- 用户完成一次图书管理员回答后，服务端从规范公开响应生成快照，并用当前进程秘密签发 HMAC。聊天响应顶层的 `research_brief` 信封包含 `snapshot_token`、`answered_at`、`evidence_fingerprint`、`eligible` 和 `ineligible_reason`。
- 签名快照包含 `question`、`report`、`query_analysis`、公开 `results`、模型/候选/引用/匹配/bundle 统计、`answered_at` 和 `evidence_version`；后者的值等于 `evidence_fingerprint`。
- `POST /api/agents/librarian/research-brief.md` 只接受当前进程签发的 `{snapshot, snapshot_token}`，并恒定时间验签。不得接受任意快照、session id、原始 SQLite、PDF 或本地路径，也不得从浏览器/桌面历史补齐或重签。
- 导出器是纯派生路径：HMAC 只绑定公开快照，不建立服务器端历史，不调用 DeepSeek，不执行 Search V2，不打开数据库或 PDF，不读取曲线，也不把导出结果写回任何科学记录。
- `clarification`、零规范引用、最终零可解析证据、签名失效/篡改、进行中/失败轮次和没有 `report` 的旧文本消息都不能导出。`not_found` 只有在仍引用至少一条真实相关证据且通过全部门禁时才能导出，并保留“没有直接证据”的状态。

### 引用与顺序

- 导出器只使用结构化报告中的规范 R#；每个 R# 必须在公开 `results` 中唯一映射到 `agent_cited=true` 且类型为 `item/finding/table/figure` 的记录。
- 声明引用数必须与规范唯一引用完全一致，且最终至少收录一条。未被报告引用或 `agent_cited=false` 的候选不进入附录；研究简报不会把有界召回全集误写成报告证据。
- 引用和证据按 R# 及规范化内容确定性排序。给定相同快照和相同生成时间，结构化简报与 Markdown 必须相同。
- 孤儿/重复/无效/未收录引用、缺失段落和声明计数不一致会使一致性门失败并拒绝文件。缺失题目、DOI、页码或原文片段保持为空，令 `integrity.status=warning`，并在 Markdown 中按 R# 展示缺失字段，不能猜测补全。

### 隐私与科学边界

- 使用白名单复制公开字段，不得整体序列化候选对象。禁止本地路径和 URL、Zotero/本机 key、审核者和内部备注、桌面历史标识、PDF/截图载荷、API key 或模型协议进入导出。
- 研究简报只报告当前五段报告实际引用的四类证据；它不是第五类结果，不改变 `agent_cited`、match class 或 evidence bundle。
- 不从图片或曲线补数，不把相邻/扩展证据升级为直接证据，不新增跨 bundle 定量比较。
- App 内部服务允许该 POST 仅因为它验签并把当前进程绑定的合格公开快照转换成 `no-store` Markdown 附件；调用前后 SQLite 必须字节不变，历史只读权限仍拒绝其他写接口。
- history schema 和保存策略完全不变；`research_brief` 授权只存在于瞬态 `state.librarianBriefAuth`，不进入 session meta/messages、`localStorage` 或桌面加密历史，也不持久化 HMAC secret。重启或只恢复历史都必须重新检索后才能导出。

完整快照结构、字段说明和验收见 `LIBRARIAN_RESEARCH_BRIEF.md`。
