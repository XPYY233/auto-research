# Auto Research Evidence 阶段发布状态

## 版本身份

- 科学数据基线：`2026.07.30-librarian-brief-stable.1`
- 桌面候选：Auto Research `0.6.0-preview.1`（Apple Silicon macOS）
- 制品源码：`7909100`
- 制品标签：`evidence-demo-2026-08-09-macos-workbench-preview-6`
- 改造前保护提交：`6ce9536`
- 改造前保护标签：`evidence-demo-2026-07-30-pre-research-brief-1`
- 当前状态：内部开发预览；联合测试、隔离 App 构建、冻结二进制 smoke、DMG 镜像验证和隔离 GUI 进程启动/关闭均已通过，但本地文献工作区仍依赖 checkout v12
- 证据库结构：`v12`
- 固定验收语料：`config/evidence_test_set_50.json`
- 当前用户入口：Auto Research.app（macOS 内部开发预览）
- 已退役入口：浏览器工作台、导师只读页、`8765`/`8766`、ngrok 公网链接

本阶段在 `2026.07.30-librarian-reasoning-stable.1` 上完成图书管理员研究简报、签名官方资料包、联合只读检索和 macOS 桌面接线。它保留既有数据、图表、原文定位、质量门、DeepSeek 证据对话和校对历史，没有重新提取论文、重新查询 PDF、生成稳定截图或猜测图中曲线点。HTML/JavaScript 与 loopback webapp 仍是 App 内部实现，不再作为独立网页版产品交付。

## 0.6.0-preview.1 验收摘要

- 一级入口收敛为“文献处理、搜索数据、上传实验数据”。“文献处理”复用原上传与当前文章校对页面作为两个阶段，没有复制第二套状态；上传 PDF 只验证、去重和入库，DeepSeek 只由“开始自动提取与核验”明确触发。
- CSV/TSV/XLSX 先做安全预览，再由 DeepSeek 基于工作表名、行数、列名、类型、已有单位/本地角色与含义及最多5行对齐样例生成待核验建议。用户只需浏览、修正错误并点击一次“我已检查，确认导入”；后台仍执行 draft、revision/CAS、confirm 和私人搜索刷新，AI 永远不能确认数据。
- 延迟 AI 响应不会覆盖用户已编辑字段；AI 运行期间最终确认被禁用。私人暂存只清理受控命名、超过 TTL 的普通文件，不跟随符号链接。
- 干净 release worktree 联合验证745项（共享核心479、macOS 157、Windows 109），最终文案与导航另通过22项契约检查；Python/JavaScript/差异检查、冻结 App smoke、ad-hoc codesign、DMG校验和真实安装界面检查通过。
- 唯一可启动副本为 `/Applications/Auto Research.app`，Info.plist 为 `0.6.0` / build `7`。DMG SHA-256：`57f782897fdd84262c1e1bf5553c7b0e81efaed707d064ebbfa8fa5aff039022`；可执行文件 SHA-256：`128627d8708c573c563313fb53503d2bf2ae984e39680d34ef5f13778f61d834`。
- Windows 源码已同步相同共享 UI、AI 建议、单次核验编排和凭据解析薄适配，并通过109项平台契约；但 `installer_ready=false`，没有 Setup 或 Win11 真机验收，不得称为 Windows 安装版。

上一回退制品为 `0.5.1-preview.1`、标签 `evidence-demo-2026-08-09-macos-workbench-preview-5`。旧安装副本均改为非 `.app` 后缀，仅保留回退文件，不会形成系统重复 App。

## 0.5.1-preview.1 验收摘要

- 左侧工作区固定为“数据检查、搜索数据、上传文献、上传实验数据”四个一级入口；“人工补录”和“修正历史”不再向用户展示，但旧 DOM、处理器和历史数据读取仍保留为兼容层，不删除既有数据。
- “上传实验数据”是独立页面，复用现有 `PersonalImportService → private snapshot → FederatedSearchSession`，提供 CSV/TSV/XLSX 原生选择、受限预览、逐列角色/意义/单位确认、版本绑定确认和私人搜索刷新；没有复制第二套导入或索引逻辑。
- 最终干净 release worktree 联合验证：726 项通过（共享核心467、macOS 154、Windows 105）；前端 JavaScript 语法、Python 编译和差异检查通过。
- Apple Silicon App 与 DMG 从干净源码 `63b34f2` 单次构建。冻结二进制 smoke 确认 `primary_personal_import_navigation`、六路个人导入、联合检索、双原生选择器、Librarian V3、桌面会话/CSRF、导出与旧工作区均进入制品。
- App 通过 ad-hoc codesign、Info.plist 版本 `0.5.1`/build `6` 和安装后真实界面验收；系统中唯一可启动副本为 `/Applications/Auto Research.app`。DMG SHA-256 为 `7646527a9d7e59a64a11fdb9beb91f36b49b0eea12ae4d11b459db7e946b4529`；可执行文件 SHA-256 为 `7af68cc5933e2819c223cc41e81d986720f235ed70ecea50d1db4646bbe8c8bf`。
- Windows 共享静态资源契约已锁定相同四入口和同名原生私人文件选择器，但 `installer_ready=false`，数据检查/上传文献等工作区路由尚未完成 Windows 生产接线，因此不得称为 Windows 完整安装版。

上一回退制品为 `0.5.0-preview.1`、标签 `evidence-demo-2026-08-09-macos-workbench-preview-4`；安装副本已改为 `.app.rollback`，不会形成系统中的重复 App。

## 0.5.0-preview.1 验收摘要

- 最终干净 release worktree 联合验证：722 项通过（共享核心467、macOS 151、Windows 104）；Python 编译、两份前端 JavaScript 语法、macOS 脚本语法和差异检查通过。
- Apple Silicon App 与 DMG 从干净源码 `9985386` 单次构建。冻结二进制 smoke 验证 schema-v12 临时副本、桌面会话/CSRF、加密历史、四类检索、Librarian V3、官方/私人资料库路由、双原生选择器和导出均通过；临时数据库 SHA-256 前后不变。
- App 通过 ad-hoc codesign、Info.plist 版本 `0.5.0`/build `5`、DMG 校验和隔离 GUI 进程启动六秒无错误后关闭。最终 DMG SHA-256 为 `cad6c8b8f2769b9006a0820fabf9ee22f03ca36ae5593f6f2e96adc88f2374a1`；可执行文件 SHA-256 为 `45db994969851ded745611899da4961a8964cbb5eae6c0cd530b91bc3bde947e`。
- 新版已包含 Librarian V3 多轮状态、官方/私人/全部联合精确搜索，以及 CSV/TSV/XLSX 私人实验的预览、草稿、逐列复核、确认和检索刷新。图书管理员仍只查官方文献，不读取私人实验。
- 首个内部 `.aresearch` 包约2.36 MB，SHA-256 `73672f94335604609d729671ab4a950e361b8cb569523a18980f0112c7c9f91d`，含60篇论文元数据与4,356个实体（3,142 item、936 finding、46 table、232 figure），不含PDF和二进制图片。
- 官方包只通过 `OfficialEvidenceRepository` 审计后进入只读联合搜索；它不经过 `EvidenceDB.init()`，不写入或替换 schema-v12 可编辑工作区。
- 旧浏览器工作台、导师只读页、固定端口和 ngrok 已退役；对应启动脚本只允许显示迁移提示。
- Windows 后端同源契约在本轮104项平台测试中通过，但 `installer_ready=false`，没有真实 Setup 或 Win11 clean-machine 验收，因此不能称为 Windows 安装版。

上一回退制品 `0.4.0-preview.1`、标签 `evidence-demo-2026-08-01-macos-workbench-preview-3` 与 DMG SHA-256 `89766b8efd8e2821abef0d7940dec513cd60ebaf2a36eea042adf784fa1483ee` 均保留，不因本次发布重命名或覆盖。

## 图书管理员与 Search V2

- 搜索页默认邀请用户向“图书管理员”描述自然语言问题；精确检索保留为进阶入口。
- 图书管理员使用既有 DeepSeek 项目配置，只能调用数据条目、原始表格、论文图片和实验结论四类只读工具，不建立第五套结果模型。
- Search V2 使用可随时重建的 SQLite FTS 投影；科学事实仍以原六列、图表和证据表为唯一权威来源。
- 当前发布基准中，代表性查询约为 0.018–0.177 秒。论文新增或修正后只刷新发生变化的论文索引。
- Agent 使用“DeepSeek规划 + 本地硬条件解析—本地覆盖召回与分级—DeepSeek证据选择—本地五段式报告完整性门”增量流程；回答以 `[R编号]` 连接实际搜索结果，模型网络失败时精确检索、详情、原文证据和导出不受影响。
- 材料、辐照类型、粒子、温度、剂量/注量、物理量和样品状态按硬条件处理；同义词和元素名称/符号仅扩展召回。当前轮显式条件覆盖历史，避免把上一轮中子与本轮离子辐照错误拼接。
- 硬条件完全由本地确定性解析器和有界历史继承产生；DeepSeek 只规划检索、选择有界证据并解释，不能创建、跨字段注入或改写硬条件。科学计数法注量会保持指数语义并做等价单位比较，`300 keV` 不会变成 `300 K`，`Ni/He` 粒子也不会冒充材料。
- 候选由本地程序固定分为直接证据、只放宽一个条件的相关证据和缺少多个条件的扩展候选；DeepSeek 不能改变该分层。证据按论文、实际材料和完整实验条件成组，禁止跨组自动拼接定量前后关系。
- 图书管理员固定检索官方文献全库（不含私人实验）；论文范围和官方/私人/全部来源选择只出现在精确检索。当前 ad-hoc macOS 内部预览使用 Application Support 私有目录中的随机密钥进行 AES-GCM 持久化，避免代码身份变化引发系统密码框；正式签名 macOS 发行使用 Keychain，Windows 使用 Credential Manager。历史浏览器适配仅作为兼容测试边界。任何模式都不把历史写入科学数据库，恢复旧对话不会调用模型。
- 回答固定展示直接结论、证据矩阵、相关证据、数据库空白和建议追问；建议追问可继续提交，最新 `[R#]` 可直接跳到证据卡。结果按“条目、结论、表格、图片”横向切换。
- 加载动画明确标注“预计阶段”，真实秒数降低无障碍播报频率；提交新问题会先清空上一轮证据卡，历史轮引用不再错误绑定当前卡片。
- 长问题会拆成严格组合和若干分面检索式，四类证据分别召回并受类型上限保护；页面同时公开候选总数与回答引用数，不再把单个标签中的少量记录误认为全部结果。
- 最终中文总结走独立 JSON 通道；失败时依次降级到干净文本总结和确定性报告。内部协议、孤立 `[R编号]`、无证据数值、跨实验条件定量比较和无证据历史回答会被拦截。
- Search V2、图表详情和图书管理员使用同一公开字段投影；只读响应不暴露本地路径、Zotero key、本机文章 key、审核者和内部备注。
- 相同数据库版本、问题和有限历史会在服务进程内复用一小时的完整稳定结果；页面明确显示复用状态，数据库证据变化会自动使缓存失效。
- App 内部继续复用同一套前端、数据库和 Agent 接口；历史只读权限模式保留用于回归，不再构成公开产品入口。

## 图书管理员研究简报

- 用户可从当前最新的结构化五段报告下载 `librarian-research-brief.md`。
- 只有当前服务进程 HMAC 签名、不是澄清回答、至少有一条实际引用且通过一致性门的回答可以导出。澄清和零引用回答一律不生成文件。
- 聊天响应顶层的 `research_brief` 信封包含 `snapshot_token`、`answered_at`、`evidence_fingerprint`、`eligible` 和 `ineligible_reason`；签名快照使用 `answered_at` 与值相同的 `evidence_version`。
- 导出 POST 只接受当前进程签发的 `{snapshot, snapshot_token}`，而不是任意公开快照、session id、原始 SQLite、PDF 或文件路径。服务端不读取桌面历史来补齐或重新签名。
- HMAC 只绑定规范化公开响应快照。它不建立服务器端历史，不写数据库，不重新调用 DeepSeek，也不重新查询 Search V2 或 PDF。
- 每个规范 R# 必须唯一映射到 `agent_cited=true` 的公开结果，声明引用数完全一致，且最终至少有一条证据；类型继续固定为 `item/finding/table/figure`，研究简报不是第五类科学证据。
- Markdown 固定包含研究问题、硬条件、五段报告、已引用证据附录、模型与召回统计、引用完整性检查和明确限制。
- 字段白名单排除本地路径和 URL、Zotero/本机 key、审核者、内部备注、桌面历史标识、PDF/图片载荷和模型协议。缺失题目、DOI、页码或原文片段保持为空，使 `integrity.status=warning`，并按 R# 在下载文件中显示缺失字段。
- 孤儿/重复/无效/未收录引用、缺失段落和计数不一致会使一致性门失败，而不是生成看似正常的简报。
- 导出不从图片或曲线读取数据点，不新增跨 evidence bundle 的定量比较，也不创建、修正、确认、发布或审核科学记录。
- App 内部服务允许该 POST，仅因为它验签并把当前进程绑定的公开快照转换为 `no-store` Markdown 附件；导出前后 SQLite 字节不变。
- history schema 和保存策略完全不变；`research_brief` 授权只存在于瞬态 `state.librarianBriefAuth`，不进入 session meta/messages、`localStorage` 或桌面加密历史。重启或只恢复历史都必须重新检索后才能导出。
- 共享前端的桌面历史适配属于本核心发布；实际桌面产品层仍是独立打包边界，不能据此声称 `desktop/macos/**` 源码已随本核心提交发布。

完整契约、快照结构和验收见 `docs/LIBRARIAN_RESEARCH_BRIEF.md`。

## 当前数据库

| 对象 | 数量 | 说明 |
|---|---:|---|
| 已登记论文 / 文档 | 60 / 60 | 固定语料之外保留历史记录和用户上传论文 |
| 原始六列记录 | 6,501 | 包含完整追加式历史 |
| 可报告数值来源 | 5,048 | 保留同一事实的多处证据 |
| 独立物理事实 | 3,142 | 搜索、校对和导出的默认口径 |
| 已折叠重复提及 | 1,906 | 只折叠展示，不删除原始来源 |
| 定性实验结论 | 937 | 与数值事实分开检索 |
| 隔离的旧文字值 | 1,453 | 不进入数值事实搜索 |
| 原文图表资产 | 291 | 59 个表格、232 幅图片，文件全部存在 |

其中 243 个图表（40 表、203 图）是云端视觉实验之前的历史冻结基线；当前 291 是完整现状。历史冻结身份、文件和已存 SHA-256 保持不变，不能把 243 误写成当前总数。

## 本次体检和修复

- 最新已完成的联合候选验证为745项自动测试全部通过；除既有硬条件、证据分级、五段报告、公开 DTO 和兼容权限回归外，还覆盖签名资料包、发布者策略、官方/私人联合召回、AI辅助个人表格识别、单次核验导入、合并文献处理、macOS 安全 bridge、Windows共享契约、多词覆盖门与 WebView 状态回归。
- 历史只读兼容验收曾使用“钨”问题召回 65 项：0 条直接、5 条相关、60 条扩展，报告实际引用 5 条；研究简报 POST 返回 200。该结果保留为权限回归证据，不代表仍发布浏览器只读产品。
- 真实 DeepSeek `deepseek-v4-pro` 查询返回四类共 71 项候选，分为 4 条直接、8 条相关和 59 条扩展；生成 3 行证据矩阵、6 项相关证据和 3 个建议追问。关键含糊问题返回澄清而不猜测。
- 研究简报导出不产生新的 DeepSeek 调用，不重新召回证据；生产 SQLite 导出前后 SHA-256 均为 `d62dc5c43ac9e0fb97e0ad2ecb85deaf50447fb7a036acae5147e8f6111236f6`。
- 共享前端正式使用 `desktop-secure`；`browser-local` 和 `readonly-none` 只作为历史兼容与权限测试。桌面安全存储不可用时不会明文降级。
- 新增 `evidence-reconcile-runs`：自动结束超过期限且后台已不存在的抽取、质量和处理任务；只修改运行审计状态，不改变数据、图表或校对历史。
- 批量质量任务收到 `Ctrl+C` 时会把当前论文和批次明确写为“已中断”，不再残留假运行状态。
- DeepSeek 或质量流程成功完成时会清除旧错误文本，避免“已完成但仍显示失败原因”。
- 目标审计不再把“单篇样例功能通过”误写成“整个初始项目目标完成”；它同时读取固定语料完成度。
- 修正文章选择规范仍写成 35 篇的问题，当前固定集合严格为 50 篇 DOI/题目选择器。
- 数据搜索、图表搜索、图表详情和图书管理员接口已通过共享核心验证；历史只读权限回归仍确认写入返回 403 且 SQLite 哈希不变。最终联合全测、App/DMG 重建、镜像校验和实机启动健康检查均已完成。
- Python 编译、前端 JavaScript 语法、macOS 启动脚本语法、依赖一致性、Git 对象完整性、空白错误和凭据扫描通过。

## 固定 50 篇语料的真实状态

- 50/50 篇 PDF 可打开、正文有效且 DOI/题目身份匹配。
- 17/50 篇数据、证据定位和论文内搜索达到当前验收门。
- 30/50 篇已有可搜索图表。
- 33/50 篇仍需完成全文对抗式抽取。

因此，产品功能和当前已发布数据可作为稳定演示版使用；最初计划中的“至少 30 篇完成处理”尚未达到，不能宣称全项目最终验收完成。进一步的科学准确率还需要独立人工金标准，而不能仅用两路 DeepSeek 一致性代替。

## 稳定边界

- DeepSeek 双路和第三次复核可自动决定结果是否进入搜索；人工校对是纠错与校准通道，不是逐条发布前置条件。
- 每条已发布数值保留页码、定位信息、原文片段及证据类型；原始单位不被覆盖。
- 图表截图继续来自本地真实 PDF；DeepSeek 只根据图注与邻近正文生成中文标题、解释和标签。
- 系统不从曲线像素生成精确数据点，也不把计算结果冒充直接测量。
- “稳定”表示程序、数据库关系和已发布证据链通过验收，不表示所有模型生成内容已经得到物理学人工认可。

## 启动与维护

- 用户启动：打开 Auto Research.app；不输入项目编辑密码，不使用 localhost 或公网隧道。
- 旧 `打开本地编辑工作台.command` 与 `创建导师公网链接.command` 仅保留迁移提示，应明确引导用户打开 App，不再启动服务。
- `evidence-serve`、只读模式和相关端口仅供 App 内部或维护测试，不是用户入口。
- 完整维护流程：`MAINTENANCE_WORKFLOW.md`。
- 阶段审计：`STAGE_AUDIT_2026-07-27.md`。
- 固定语料审计：`data/evidence/test_sets/full-corpus-50-v1_audit.md`。

- 本轮改造前保护提交：`6ce9536`。
- 本轮改造前保护标签：`evidence-demo-2026-07-30-pre-research-brief-1`。
- 当前制品标签为 `evidence-demo-2026-08-09-macos-workbench-preview-6`；它明确是内部开发预览，不得重命名为稳定正式版。
- 最终 SQLite 快照：`/Users/USER/Zotero/auto-research-backups/experimental_evidence-librarian-brief-stable-2026-07-30-v1.sqlite`；SHA-256 为 `d62dc5c43ac9e0fb97e0ad2ecb85deaf50447fb7a036acae5147e8f6111236f6`。
- Git bundle 在最终 App 联合提交和标签完成后生成；本共享核心提交不单独打包。
- 上一稳定恢复点仍为标签 `evidence-demo-2026-07-30-librarian-reasoning-stable-1`、SQLite `/Users/USER/Zotero/auto-research-backups/experimental_evidence-librarian-reasoning-stable-2026-07-30-v1.sqlite` 和相邻 Git bundle。
