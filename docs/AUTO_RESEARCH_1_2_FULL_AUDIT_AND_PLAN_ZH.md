# Auto Research 全面审计与下一阶段实施计划

> 审计日期：2026-08-25
> 原始审计基线：`aa64c21390fc5d6873822f2798d76c18871c0f20`
> 以下运行时检查点均为历史审计材料；实时安装身份与逐项验收唯一见[`BUILD54_INSTALLED_ACCEPTANCE.md`](BUILD54_INSTALLED_ACCEPTANCE.md)。
> 0.5 对照基线：`63b34f2`（0.5.1 build 6）
> 原始审计期间安装 App：1.2.0 build 50，来自 `45720ce`；不再代表当前安装状态。
> 原始只读审计已获用户批准并进入实施；此处旧禁写/待批准措辞描述历史阶段，不覆盖当前交班规则。生产 SQLite 保护持续有效。

> 2026-09-05复核：功能恢复与代码清理都未全部完成。当前单候选已恢复若干真实AI、表图、数据集和私人表格流程；提取全链、部分布局及原生用户包往返仍须验收。Phase 7不能划为完成：循环依赖、大Fusion控制器与兼容消费者退役仍是债务，代码规模和旧阶段测试计数不是最新测量。

> 2026-08-28 实施检查点：审计后已按契约恢复上传论文原子发布、视觉资产人工隔离、表格人工审核与verified-only导出、图书管理员/证据对话的有界历史与引用复核、私人表格分页核验与人工序列编辑、确认导入后的精确打开，以及官方包活动版本和审计数量的冷启动恢复。结构治理已修复循环审计器、消除finalizer/job/visual三模块环，并把资料包/dataset、任务历史与个人导入控制器从Fusion主脚本物理抽离。资料包导入、资料包导出和训练数据集导出现在写入独立AES-GCM、path-free的`operation-history-v1`，保留近30天最多100条；已落盘但回执pending的导出可以跨重启只恢复可信回执，不会重跑导出。queued/running重启后只会变成interrupted并要求用户重新开始，不是长任务续跑。共享/Mac也已迁出四个旧Web控制器的正向消费者，旧文件只因Windows冻结静态清单保留。运行时代码检查点`5c2055f`进一步收口任务感知栏位、图书管理员三层工作区和AI错误恢复血缘，并通过共享1116项（另80项跳过）及macOS307项。以上仍只是源码证据，尚未替换已安装 App，也不构成 1.2 稳定声明。生产 SQLite 与 `paper_056` 隔离现场继续排除在测试、构建与提交之外。

## 1. 执行结论

当前产品不能继续采用“发现一个界面故障，就在现有控制器上追加一个修补”的方式推进。问题已经不是单点 bug，而是以下四类漂移叠加：

1. **产品层级漂移**：Fusion 同时展示上下文栏、双编辑器、检查器、AI 对话和任务状态，默认界面成为五列墙；分栏从按需能力变成永久布局负担。
2. **功能迁移漂移**：0.5 可直接完成的上传、提取、搜索、查看原文和 AI 对话，在新安全契约下拥有更强后端，但前端没有逐条恢复完整用户闭环。
3. **发布证据漂移**：源码存在、自动测试通过、安装 App 真正可用被混为一谈。当前安装的 build 47 甚至不包含 HEAD 的后续修复。
4. **代码治理漂移**：从 0.5 基线到当前相关范围约净增 36,000 行；新模块持续加入，旧执行链、兼容层和大文件没有同步退役。

因此下一阶段目标不是“继续美化 1.2”，而是建立一个可证明的产品基线：

> **保留 0.5 的从容、直接反馈和完整工作流；采用 VS Code 的清晰层级、标签与按需分栏；保留当前版本已经建立的安全、原子发布、资料包和数据集能力。**

在用户批准本报告和静态目标界面前，不再修改生产 UI，不递增 build，不替换 App。

## 2. 三层真实性结论

| 能力层 | 当前事实 | 能否宣称稳定 |
|---|---|---|
| 领域代码 | 提取、质量门、审核、原子发布、索引、四类搜索、PDF/视觉资产、四项 AI、资料包和数据集服务均存在 | 只能说明“有实现” |
| 自动测试 | 历史交接记录有大规模共享/macOS测试；大量 AI 测试使用 fake client，UI测试偏重字符串、路由和状态契约 | 只能说明“契约在测试环境成立” |
| 已安装 App | 当前 build 47 来自旧提交 `033b12b`；用户已反复证明布局、AI、按钮可见性和连续流程不可靠 | **不能宣称稳定** |
| 科学质量 | 有人工金标准与科学审计框架，但不能从软件测试推导整库准确率 | **不能宣称语料已全部审核** |

后续所有验收单必须同时列出这三列，禁止再用“测试通过”代替“用户能完成”。

## 3. 0.5 与当前版本应如何取舍

### 3.1 必须保留的 0.5 优点

- 主操作总在首屏：上传、开始提取、查看原文、进入审核不藏在页底。
- 页面一次只表达一个主要任务，留白充足，标题、内容和反馈区有明确距离。
- AI 对话是完整页面，有历史、进行反馈、回答和连续追问，不是检查器底部的小表单。
- 用户能看见后台是否在工作；耗时任务有阶段和进度，不以“按钮无反应”代替状态。
- 论文、证据详情和原文之间的返回关系直观。

### 3.2 必须保留的当前版本进步

- prepared action、逐次授权、预算和凭据安全存储。
- `AtomicEvidenceDBFinalizer`、人工审核队列和 `saved_index_pending` 语义。
- 四类证据、真实 PyMuPDF 资产、官方/本机/私人联合搜索。
- `official-package-v2`、用户资料包、JSONL/Parquet 数据集和稳定实体身份。
- DocumentTabStore、主题/密度、路径无关 DTO、平台中立业务边界。

### 3.3 不应恢复的旧实现

- 旧 `/api/agents/librarian/chat`、`/api/context-chat` 和收费 workflow 旁路。
- 环境变量密钥、无授权直连模型、Harness 失败时回退旧循环。
- 旧 DOM、第二导航所有者、浏览器编辑工作台、ngrok 和固定端口产品入口。
- 从曲线像素推断或虚构数值。

## 4. 两条核心功能主链审计

### 4.1 主链 A：高精度论文提取

目标链：

`导入 PDF → 免费预检 → 预算授权 → 四类提取 → 质量门 → 人工审核 → 原子发布 → 索引 → 三类收据`

| 环节 | 当前权威 | Fusion状态 | 实机结论 | 处置 |
|---|---|---|---|---|
| PDF导入/去重 | `UploadService`、`/api/uploads/pdf` | 有入口但连续流程多次回归 | 未在当前HEAD制品验收 | 保留服务，重做薄控制器 |
| 预检/预算 | literature prepared action | DTO完整 | 用户反馈状态不直观 | 首屏展示预检结果与预算卡 |
| 四类提取 | `DeepSeekEvidenceExtractor`、stages、quality pipeline | 已接线 | 真实模型完整论文未在同一制品通过 | 继续复用，不复制 prompt/parser |
| 质量门 | dual/third/manual review | 已接线 | 科学盲测未闭合 | 建立20篇金标准+1篇盲测 |
| 视觉资产 | PyMuPDF、`literature_visual_stage` | 有详情入口 | 部分论文资产曾不可显示 | 以真实PDF+历史SHA验收 |
| 人工审核 | `ReviewQueueService` | approve/reject/correct已有 | 当前新制品未实机审核真实候选 | 作为提取任务的固定下一步 |
| 原子发布 | `AtomicEvidenceDBFinalizer` | 解析commit receipt | 未完成新制品端到端 | 保留，禁止前端伪造完成 |
| 搜索索引 | `EvidenceSearchIndex.refresh_papers` | 能识别pending | 缺少显眼恢复操作 | 提供真实“重试索引”入口 |
| 收据 | extraction/publication/dataset membership | 代码能解析 | 用户看不到统一验收单 | 任务完成页集中展示 |

结论：领域核心明显强于 0.5，但 Fusion 适配和真实安装验收没有达到同样可靠性。应该恢复用户工作流，不应回退安全和原子发布实现。

### 4.2 主链 B：搜索、核验与复用

目标链：

`输入条件 → 服务端四类召回 → 分组列表 → 多详情标签 → 原图/结构核验 → PDF高亮 → AI解释 → 导出`

| 环节 | 当前权威 | 当前缺口 | 处置 |
|---|---|---|---|
| 四类搜索 | Search V2、FederatedSearchSession | GUI拥挤；主搜索框、筛选与导出争夺一行 | 搜索栏独占主行，次操作放第二行 |
| 四类筛选 | 服务端 `item/finding/table/figure` | 视觉上仍容易退化为同质列表 | 分类型分组、真实数量、类型特有摘要 |
| 多详情 | DocumentTabStore | 分栏默认策略和内容挂载不稳定 | 单击右侧预览，固定后不覆盖；分栏按需出现 |
| 表格/图片 | 视觉资产与详情 DTO | 真实原图与解析结构有时只显示占位 | 原图/结构并列；无资产时给出准确原因 |
| PDF定位 | 工作区/官方PDF路由、source view | 返回、缩放、高亮在迭代中反复回归 | 唯一PDF文档控制器和往返验收 |
| 证据问答 | selected evidence Harness action | 曾被放在全局检查器，缺少连续对话体验 | 进入具体证据文档，作为完整对话面板 |
| 图书管理员 | bounded local recall + Harness | 真实模型失败、速度下降、历史/记忆可见性差 | 先本地召回，最多一次模型综合；完整聊天页 |
| CSV/XLSX | 后端 evidence/current/search exports | Fusion入口不明确或不可见 | 在详情与结果工具栏恢复显眼入口 |
| Dataset | dataset-bundle-v1 | 后端真实，产品说明不足 | 与 `.aresearch` 分享流程彻底分开 |

## 5. AI 四业务审计

| 业务 | 代码 | 自动测试 | 安装App | 下一步门 |
|---|---|---|---|---|
| 文献提取 | 真 prepared-action 与分阶段任务 | 主要以受控client验证 | 未证明 | 真实盲测PDF完整发布 |
| 图书管理员 | 本地召回、Harness只读工具、引用核验 | 契约较多 | build47真实输出失败；HEAD修复未安装 | 一次调用完成引用/推荐/局限 |
| 证据问答 | 当前实体+同论文兼容证据 | 有范围安全测试 | 当前可见入口与连续对话未证明 | 具体证据详情内实测 |
| 实验AI预填 | 有界表头/类型/样例，只建议不确认 | 有安全与投影测试 | 未在同一制品证明 | 真实CSV建议+人工确认 |

AI设计必须遵循：

- 连接成功、Harness可用、某项业务可用是三种不同状态。
- 进度展示安全的阶段、工具动作、调用次数和耗时，不展示模型私有思维链。
- 图书管理员默认采用“本地解析硬条件 → 本地召回 → 一次模型综合”；Harness只用于受限编排，不为展示而制造多轮慢循环。
- 历史与研究记忆继续使用独立加密存储；历史不是科学证据，记忆必须由用户逐条批准。

## 6. 资料包、私人实验和数据集审计

### 官方资料包

现有 v2 产物是真实能力：59篇论文、4,369条四类证据、59份PDF、291个视觉资产；`10.2172/6065200`只从新版发布声明排除。UI必须持续显示论文/PDF/表/图覆盖数、导入结果和“前往官方搜索”。

### 私人实验

当前链条已具备真实分页表格和confirmed/indexable门，但首屏操作与搜索详情仍不够直观。目标是：选择文件、可选AI建议、人工核验、一次确认四步固定在顶部；搜索结果直接打开真实表格，不只显示表名。

### 分享与训练数据

必须保持三类目标分开：

1. `.aresearch`论文集合包；
2. `.aresearch`私人实验包；
3. JSONL/Parquet/CSV/XLSX数据集或单证据导出。

“生成计划”只用于核对，计划完成后必须紧邻显示“选择保存位置并导出”，且任务与收据持续可见。

## 7. 代码结构审计

### 7.1 规模与变化

- `src/auto_research` Python当前约68,141行。
- 共享Web资源当前约9,314行；增长主要来自把压缩在主脚本中的资料包逻辑改为可审查的独立格式化模块；最大文件仍是兼容资源 `app.js` 4,379行。
- 从0.5基线到当前核心范围约新增39,416行、删除2,923行。
- 19个Python模块超过1,000行，涉及repository、transfer、提取、质量、AI和webapp关键边界。

新增远多于删除并不自动等于“屎山”，但在本项目中它伴随旧路径、重复状态和治理清单漂移，已经构成高风险结构债务。

### 7.2 已确认的循环依赖

原始审计确认5组强连通分量；截至 `5c2d72e`，3模块finalizer/job/visual环已经通过独立名义受信端口消除，当前剩余4组：

1. 9模块：CLI、webapp、goal/self-check、export及多组测试集工具互相依赖。
2. 6模块：deepseek extraction、benchmark、learning、quality pipeline、six-column、visual evidence互相依赖。
3. 2模块：OpenAI-compatible client与AI runtime state互相依赖。
4. 2模块：package transfer payloads与transfer package互相依赖。

前两组直接穿过核心提取链。审计器此前会把 `from . import module` 错判为导入整个包，从而虚构20模块大环；该解析错误已增加回归测试并修复。现有 `python-import-cycle-baseline.json` 未被放宽，真实循环仍只能缩小。

### 7.3 前端主要债务

- `fusion_review.js`当前874行；资料包/数据集逻辑在924行的`fusion_package_center.js`，持久任务历史在114行的`fusion_operation_history.js`，个人导入核验及私人搜索索引恢复在661行的`fusion_personal_import.js`。主脚本仍掌管文献、搜索、设置、AI、标签和布局协调。
- 当前生产HTML只加载 `ai_consent.js`、`document_tab_store.js`、`pane_layout_controller.js`、`workspace_layout_controller.js`、`fusion_pdf_controller.js`、`fusion_ai_experience.js`、`fusion_operation_history.js`、`fusion_package_center.js`、`fusion_personal_import.js` 与 `fusion_review.js`。旧 `app.js`、`desktop_product.js`、旧 `package_center.js`、`workbench.js` 不属于生产启动链。
- 这些旧资源仍被桌面静态白名单、safe-update检查、权限/路由兼容测试或历史研究测试消费，必须先迁移消费者，不能用一次粗暴删除冒充清债。
- 四个旧控制器的逐文件消费者、不得迁移行为和Batch B—E删除门已经冻结在`docs/LEGACY_WEB_CONSUMER_AUDIT_1_2.md`；Windows冻结期间只迁移Mac/Fusion消费者，不通过共享改动偷跑Windows删除。
- DocumentTabStore、PaneLayoutController和Fusion各自拥有部分可见性/恢复语义，导致“所有栏被收起”及标签内容错位。
- 当前UI回归测试大量以字符串、DOM ID和fake DOM为主，无法证明真实WebView的排版、焦点、滚动和拖拽。

当前生产前端拆分边界冻结为：

1. `fusion_review.js`继续作为唯一bootstrap、导航和共享请求端口；
2. `DocumentTabStore`只拥有文档身份与组别，不直接投影栏位；
3. `WorkspaceLayoutController`只拥有可见栏位组合，`PaneLayoutController`只拥有几何；
4. package与personal切片已完成；个人真实表格详情、标签、分栏、检查器和导航仍由Fusion持有，新模块只负责选文件、分页预览、人工核验、可选AI建议与确认导入；
5. 新模块只能消费bootstrap注入的端口，不得创建第二请求包装器、第二导航监听器或第二全局状态机；
6. 每新增一个生产静态资源，必须同步macOS资源白名单、冻结冒烟、发布哈希和MIME/no-store契约。Windows继续冻结，不借共享拆分偷偷启动Windows迁移。

### 7.4 Python主要债务

- repository和transfer模块把schema、identity、archive、storage、validation和projection放在同一文件。
- AI运行时层级过多：provider、readiness、prepared action、execution job、Harness SDK、Harness contract、业务action与平台API；错误血缘和用户状态容易在层间丢失。
- macOS仍继承历史EvidenceHandler，同时又有专用桌面API；RouteSpec尚未成为唯一业务路由权威。
- 旧Librarian/context chat执行实现仍作为兼容代码存在，容易被误认为生产权威。

### 7.5 治理数据自身不可信

- `config/module-ownership.json`已把生产Fusion资源与旧Web兼容资源分开登记，前者保持`experimental`，后者固定为`compatibility`；在真实安装App验收前不得把生产UI升为`stable`。
- `config/architecture-debt.json`在本检查点前仍引用旧行数与旧消费者边界；现已按真实生产加载链更新，但仍需在每个物理删除批次同步维护。
- `docs/ARCHITECTURE_AUDIT_1_1.md`把尚未完成实机验收的能力写成“已完成”，需要降级为源码状态说明。

## 8. 代码处置清单

### 保留

- UploadService、质量门、Search V2、PyMuPDF视觉资产。
- AtomicEvidenceDBFinalizer、ReviewQueueService、EvidenceSearchIndex。
- FederatedSearchSession、PersonalImportService、PrivateExperimentRepository。
- official-package-v2、dataset-bundle-v1及稳定公开DTO。
- prepared action、预算、凭据和path-free安全边界。

### 拆分

- `fusion_review.js` → bootstrap、workspace/tabs、literature、search/detail、AI chat、settings/status；package/dataset和personal-import已通过注入端口成为独立控制器。
- repository → schema/identity、read projection、transaction/storage、archive/export。
- literature extraction → orchestration、quality evaluation、visual staging、atomic publish、receipt。
- AI → provider/readiness、prepared authorization、business adapters、bounded agent runtime、history/memory。

拆分必须先写公开接口和依赖方向，保持单一所有者，不能只是把一个大文件切成多个互相调用的碎片。

### 替换

- 以`WorkspaceLayoutController`统一标签、分栏和侧栏投影；PaneLayoutController只计算几何。
- 以Document Controller统一论文、证据、PDF、表格和AI会话的生命周期。
- 以RouteSpec/Facade统一平台业务路由；macOS只保留Host/Origin/CSRF/body/native picker/credential。
- 以真实浏览器验收替代仅检查字符串存在的UI发布门。

### 物理删除（完成消费者迁移后）

- 旧收费Librarian/context-chat/workflow执行链。
- 不再由生产HTML加载的旧DOM控制器和重复事件处理器。
- retired browser/read-only/ngrok启动器及对应当前文档入口。
- 旧测试中只验证已退役行为的断言。
- 仓库内历史App/DMG制品；迁到仓库外并保留manifest/SHA/tag。

## 9. 目标工作台信息架构

原则：**默认三层，按需四层，永不五列。**

```text
标题工具栏：上下文 / 全局命令 / 任务与AI状态
活动栏 │ 上下文栏 │ 主编辑器（标签）
                    ├─ 按需第二编辑器：仅比较或固定详情时出现
                    └─ 按需检查器：仅选择列/证据/任务时出现
状态栏：资料源 / 索引 / AI / 后台任务 / 错误
```

- 文献默认：活动栏 + 文献目录 + 论文编辑器；主操作始终在顶部。
- 搜索默认：筛选栏 + 结果列表；点击结果自动打开右侧预览，固定后不被覆盖。
- 图书管理员：历史 + 大型聊天编辑器；有结果时才打开可点击引用导航，不显示全局检查器。
- 实验：工作表目录 + 大型数据网格 + 当前列检查器。
- 资料包：左侧选择一种任务，中央只显示该任务的四步流程。
- 设置：分类 + 设置页；没有第二编辑器和检查器。

宽度规则：

- `>=1600px`：上下文+单编辑器+检查器，或上下文+双编辑器；二者不同时出现。
- `1200–1599px`：上下文+单编辑器；检查器为抽屉；分栏时自动暂时收起上下文栏。
- `900–1199px`：单编辑器，侧栏均为抽屉。
- `<900px`：保持一个编辑器组和明确返回操作；不丢标签。

## 10. 下一步真正实施计划

每阶段只做一个可验收纵向切片。任一阶段失败，停在当前提交修复，不递增发布build。

### Phase 0：冻结和清场

- 保留0.5.1可启动回退点与当前build47只读证据。
- 将当前被中断的未提交前端实验隔离审查，不直接集成。
- 修正模块状态、债务清单和发布三层证据模板。
- 冻结目标DOM、WorkspaceLayout、Document、AI readiness、job/receipt契约。

退出门：用户批准静态目标界面；工作树无不明代码修改；生产DB哈希未变。

### Phase 1：工作台壳与排版

- 只实现目标信息架构、宽度预算、按钮层级和面板展开/收起。
- 使用静态/只读数据，不接收费AI和写操作。
- 完成6个关键页的真实WebView视觉验收。

退出门：用户确认“从容、清晰、不拥挤”；拖拽不能产生空白页；没有隐藏主操作。

### Phase 2：文档、标签和分栏

- 统一DocumentTabStore与WorkspaceLayoutController职责。
- 自动右侧预览、固定、两组编辑器、焦点/滚动恢复。
- 只保留一个可见性所有者和有界异步代次。

退出门：多论文、多证据、PDF和表格反复切换无状态污染。

### Phase 3：论文提取纵向闭环

- 接回导入、预检、任务授权、进度、审核、原子发布、索引和收据。
- 一篇隔离真实PDF完成四类提取。

退出门：主链A全程实机可见；中断/取消不重复收费；无半成品。

### Phase 4：搜索、详情与PDF纵向闭环

- 服务端四类筛选、自动右侧详情、原图/结构并列、PDF高亮/返回。
- 恢复单证据和批量CSV/XLSX显眼入口。

退出门：主链B在无AI条件下完整可用。

### Phase 5：AI聊天与图书管理员

- 具体证据详情内完整多轮对话。
- 图书管理员采用快速本地召回+一次模型综合，恢复历史、研究记忆、进度和可跳转引用栏。
- 实验AI预填接回同一readiness与授权体验。

退出门：四项AI在同一安装App真实运行；无授权零调用；失败有准确下一步。

### Phase 6：私人实验、资料包与数据集

- 真实分页表格、核验曲线与列定义。
- 三类导出目的分流，计划与实际导出连续完成。
- 资料包导入结果和下一步持续可见。

退出门：文件确实落盘并可重新导入/读取；失败不影响旧库。

实施状态（`30c7080`）：私人表格分页核验、三类导出分流、导入结果、活动版本、加密完成回执及独立操作历史已完成源码接线。已落盘但回执pending的导出可跨重启恢复回执；未完成任务只标记interrupted并返回流程，不保存敏感执行上下文，也不自动续跑。完整共享/macOS测试、同一提交候选构建、安装后导入/导出往返和真实用户验收尚未执行，因此退出门仍未通过。

### Phase 7：物理清债

- 删除已无消费者的旧前端、旧AI执行链和浏览器入口。
- 打破5组循环依赖；为大模块设置体积和依赖方向预算。
- 更新module ownership和architecture debt为真实状态。

退出门：删除后完整契约与真实流程不退化；无第二业务权威。

### Phase 8：候选发布

- 串行运行目标测试、完整共享/macOS测试、真实WebView流程和收费AI验收。
- 从干净提交构建、签名、DMG、事务替换App。
- 用户逐项勾选安装App验收单后才创建正式标签。

退出门：源码、测试、安装App和科学质量四份收据分别成立。

## 11. 发布否决条件

出现以下任一情况，候选不得升级版本或称为稳定：

- 主操作隐藏在折叠页底或只有少量边缘可见。
- 拖拽后主编辑器全部消失、标题逐字换行或产生无法恢复空白页。
- AI按钮只显示“已验证”但真实业务不能完成。
- 真实模型失败后回退旧收费路径或伪造回答。
- 提取显示完成但发布、索引、视觉资产或收据未全部完成。
- 表格/图片没有真实资产却展示占位成功。
- `.aresearch`计划被误认为实际导出，或文件未落盘却显示完成。
- 测试通过但安装App没有完成同一用户流程。
- 构建使用生产SQLite或修改用户`paper_056`现场。

## 12. 用户审核点

本轮请先只审核静态目标工作台：

1. 默认三层、按需分栏是否符合“从容 + 层级感”；
2. 六个关键页面的首屏主操作是否足够直观；
3. 图书管理员、证据问答和结果导航是否符合连续科研对话；
4. 资料包计划与实际导出的区分是否清楚；
5. 日间/夜间和舒适/紧凑密度是否可作为后续生产标准。

收到明确批准后，从Phase 0开始，不跳阶段、不并行改六条主链。
