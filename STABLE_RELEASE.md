# Auto Research Evidence 阶段发布状态

> 当前稳定交付为 Auto Research `1.1.0` / build `24`。它只面向 Apple Silicon macOS，采用 ad-hoc 签名、未公证，不是 App Store/Developer ID 公开发行。Windows 1.1迁移冻结。

> build `26` 目前只是源码候选：build 25 已完成工作台布局验收，但真实 DeepSeek 验证暴露了推理模型输出预算过小的问题，因此已撤回。build 26 修复连接探针和安全错误提示后，仍须重新通过四项付费AI、完整测试和安装验收；失败时继续使用build 24。

## v1.1.0 macOS 稳定交付

- Fusion：独立滚动文献目录、真实服务端四类筛选、多标签/双编辑器组、五模块隔离检查器、应用内PDF临时高亮与完整返回链。
- 布局：上下文栏、双编辑器和检查器之间的边界均可拖动，支持键盘调整与双击复位；布局偏好仅在本机保存，不包含论文正文或敏感字段。
- AI：DeepSeek Harness精确锁定SDK/runtime及内含二进制哈希，只拥有八个只读科研工具；逐次授权、provider/model白名单、预算和引用门仍由Auto Research执行。Harness不可用时不回退旧图书管理员或旧证据AI。
- 数据集：`dataset-bundle-v1`同时生成JSONL、Parquet、数据卡与manifest，按论文确定性划分；默认不复制PDF/图片、不混入私人实验。
- 科学边界：不可变快照的结构化规模为60篇/4,356条，但2,417条仍未审核；稳定软件不能被表述为科学准确率完成。科学发布需另过14维人工金标准审计。
- 官方包：按用户冻结范围发布59篇，并在manifest明确排除 DOI `10.2172/6065200`，不删除本地或Zotero记录。新包包含4,369条四类证据、59份真实PDF与291个图表资产，SHA-256为`909cc7323b8a91e3a238e8f49d03fe62022f5a9d078f2cc6785bb8a0b73a69c4`；首次导入、重复导入以及全部PDF/资产运行时校验已在隔离数据根通过。视觉资产旧状态仍为`draft`，科学人工审核没有被虚报为完成。
- 已完成目标验证与发布级串行测试：共享880项（80项明确跳过）、macOS 234项，Python/JavaScript、发布契约和差异检查通过。build 24实现由`393efda`封口，并从独立干净worktree重建、事务式安装；严格签名、唯一App、冻结冒烟和DMG校验通过。真实Mac只读验收覆盖文献筛选、双编辑器、可拉伸边界、真实表格原图、应用内PDF高亮/返回/关闭链。DMG SHA-256为`f3363092dc8dfea9af4aaa9fe1920e164781c585536e7146d718c0cd7e4dfc5c`，Mac UserKit SHA-256为`2c943904090bbcfde8ce1aa45c2b107f1482905f7090bdd3b648abe2596ca99c`。
- 回退：旧`auto-research-internal-evidence-1.0.0.aresearch`和1.0源码保护标签保持不可变；1.1失败不得覆盖活动包、生产SQLite或已安装1.0 App。

以下 v1.0/0.9/0.8 小节是历史发布记录。

## v1.0.0 macOS 稳定候选

- 功能：文献 PDF 导入与分阶段提取、四类证据及真实图表/图片详情、应用内 PDF/返回、精确与联合搜索、图书管理员、受控 AI、个人 CSV/TSV/XLSX 一次核验与真实分页表格、官方资料包、用户论文/实验包，以及单条和批量数据导出。
- 界面：Fusion 四区工作台、单一导航、中央标签、上下文侧栏、证据检查器、日/夜/跟随系统主题和舒适/紧凑密度。生产页面没有 0.9 审核占位词或旧页面副本。
- 验证：共享核心 `773/773`、macOS `226/226`，合计 `999/999`；前端语法、Python 编译、发布哈希同步与差异检查通过。生产 SQLite 字节哈希未变。
- 官方包：`auto-research-internal-evidence-1.0.0.aresearch`，SHA-256 `d1337a43aa4c0b83030a70e6a500bc60b85a994cb03d287396e895957ae4604d`；60 篇论文、4,356 条四类实体、零 PDF/零二进制资产，仍受课题组内部签名与 rights 契约约束。
- 发布门：必须从最终干净提交构建 App/DMG，先把 0.9.2/build21 保存为不可启动回退，再完成真实 Mac 用户流程和 UserKit 哈希封箱。完成前本节只表示源码候选，不表示已经安装。
- Windows：v1 RC 可移植性源码已恢复并完成170项平台契约，构建链包含锁定的Python/Inno/WebView2离线工具和冻结EXE冒烟；仍没有经Win11验收的Setup，保持`installer_ready=false / SETUP_PRESENT=NO`。

以下 0.9/0.8 小节是历史发布记录。

## 0.9.2-preview.1 macOS Fusion真实视觉证据预览

- 文献与搜索结果中的item、finding、table、figure均进入中央唯一详情；table/figure从现有安全路由显示真实PyMuPDF截图，不再使用占位符。详情包含图注、原文语境、物理量、变量、材料、条件、方法和关联条目。
- 中央证据PDF有明确打开、返回详情和返回列表链；请求代次、视图和实体身份共同阻止晚响应覆盖。官方/私人来源没有原图能力时只显示结构化详情，绝不借图或重绘。
- 串行验证：共享753、macOS 215、Fusion详情11项、冻结HTTP冒烟、codesign和DMG验证。源码 `83cf5e4`，标签 `auto-research-0.9.2-preview.1-build21`，DMG SHA-256 `87e29d20d926111aac784d8883ea19330ac818b754b8f3f86171f22b94943a11`。
- `/Applications/Auto Research.app` 当前为0.9.2/build21；旧build20为仓库外不可启动rollback。真实WebView验收覆盖Table 4、Figure 9、科研元数据、搜索详情、中央PDF与返回；生产SQLite哈希不变，未调用模型。
- UserKit：`/Users/USER/Zotero/auto-research-releases/Auto-Research-0.9.2-preview.1-build21-UserKit.zip`，SHA-256 `1848453eeff79e1ad8fe206e3f3475282116afd3bbeac7de9a8657a80dfd0331`。内含DMG、中文教程、验收报告和已验签官方0.2资源包；当前Fusion资料包导入/回退/导出仍禁用。
- Windows完全暂停，无0.9同步、无Setup、无Win11验收。

## 0.9.1-preview.2 macOS Fusion功能预览

- 唯一Fusion工作台继续拥有文献、搜索、实验、资料包四区和设置；没有复活0.8 DOM。文献顶部有PDF导入、自动提取与应用内PDF；搜索保留精确检索与图书管理员；实验顶部有CSV/TSV/XLSX选择、可选AI预填和一次确认。
- 核心功能直接复用既有共享后端、prepared action、AI逐次授权、path-free错误、私人快照和联合搜索契约。DeepSeek/OpenAI只允许受信provider目录与固定模型，不接受自定义URL。
- 串行验证：共享750、macOS 214、冻结HTTP冒烟、codesign、DMG校验和真实Mac流程。App源码提交 `056e2fb`，DMG SHA-256 `470dabcf5b9f491f08807677e2a3068fe185748eed2f3ee21299492e6bd26396`。
- `/Applications/Auto Research.app` 当前为0.9.1/build20；旧build19是不可启动 `.app.rollback`。真实验收包含“辐照温度”82条、中央PDF打开/返回、三类显眼入口和双provider设置；没有调用模型，生产SQLite哈希未变。
- 用户套件：`/Users/USER/Zotero/auto-research-releases/Auto-Research-0.9.1-preview.2-build20-UserKit.zip`，内含DMG、中文教程、验收报告和校验清单，不含生产数据库、PDF、Git bundle或API密钥。
- Windows完全暂停，继续无0.9同步、无Setup、无Win11验收。

## 0.9.1-preview.1 macOS Fusion GUI审核候选

- 目标：让真实App达到 `fusion-concept.html` 的四区工作台形态，支持light/dark/system、comfortable/compact、四工作区、设置、命令面板、响应式抽屉和合成CSV网格。
- 数据：真实文献只读自独立schema-v12快照；实验仅为会话级合成示例。App不读私人库、不改生产库、不调用模型、不保存密钥。
- 边界：上传、提取、确认、PDF、真实搜索详情、导出、资料包导入/回退均以禁用按钮预示后续版本，点击不会发请求或伪造成功。
- 源码与状态：最终标签 `auto-research-0.9.1-preview.1-build19` 指向 `4fd4f24`。`/Applications/Auto Research.app` 当前为0.9.1/build19，构建清单与提交、版本和标签一致。
- 回退：0.8 build18在替换前已保存为已验证且不可启动的 `.app.rollback`；系统只有一个可启动Auto Research App。
- 交付：`/Users/USER/Zotero/auto-research-releases/Auto-Research-0.9.1-preview.1-build19-FusionReview/`；DMG SHA-256 `c1786d4b4ee6734390f36c878fdcf008f5d0c3ab405f5eac87031ffe40b92f69`，已通过镜像校验。真实WebView验收覆盖四区、真实文献、CSV网格、设置持久化、跨模块标签同步和窄窗口抽屉。
- Windows：完全暂停，无0.9 Windows源码同步或Setup。

> 0.8 发布完成（2026-08-13）：`0.8.0-preview.1` / build `18` 已从干净提交 `700e642` 构建、安装并完成冻结冒烟与用户视角验收。串行验证共 1178 项。build 16/17 被冻结冒烟门拦截且未发布；0.7 build15 保持独立回退点。

## 版本身份

- 科学数据基线：`2026.07.30-librarian-brief-stable.1`
- 历史 0.8 发布候选：Auto Research `0.8.0-preview.1` / build `18`。采用 Workbench A 日/夜科研界面、设置中心、DeepSeek/OpenAI 受信提供商、四域 prepared-action AI 授权和分阶段原子文献工作流；历史 DMG、ZIP 和验收哈希以仓库外 0.8 用户套件为准
- 历史 0.8 构建制品：Auto Research `0.8.0-preview.1` / build `18` 从干净提交 `700e642` 生成；它不再是当前安装身份
- 历史 0.8 发布套件：`/Users/USER/Zotero/auto-research-releases/Auto-Research-0.8.0-preview.1-build18-UserKit.zip`
- 改造前保护提交：`6ce9536`
- 改造前保护标签：`evidence-demo-2026-07-30-pre-research-brief-1`
- 当前状态：0.8 Mac App、DMG、用户套件与 Windows 源码预览套件已生成并验收；0.7 build15 保持可验证回退。Mac 仍为课题组内部、ad-hoc 签名、未公证预览；Windows 仍无 Setup，不是公开发行版
- 证据库结构：`v12`
- 固定验收语料：`config/evidence_test_set_50.json`
- 当前用户入口：Auto Research.app（macOS 内部开发预览）
- 已退役入口：浏览器工作台、导师只读页、`8765`/`8766`、ngrok 公网链接

本阶段在 `2026.07.30-librarian-reasoning-stable.1` 上完成图书管理员研究简报、签名官方资料包、联合只读检索和 macOS 桌面接线。它保留既有数据、图表、原文定位、质量门、DeepSeek 证据对话和校对历史，没有重新提取论文、重新查询 PDF、生成稳定截图或猜测图中曲线点。HTML/JavaScript 与 loopback webapp 仍是 App 内部实现，不再作为独立网页版产品交付。

## 0.8.0-preview.1 macOS 课题组内部候选

- A Workbench 骨架替换宣传页式视觉，提供完整日间/夜间/跟随系统主题、舒适/紧凑密度、统一活动栏、设置和命令面板；B 风格只用于论文阅读层级，C 风格只用于实验数值和单位。
- 四个一级入口固定为文献、搜索、实验、资料包；人工补录、修正历史和宠物加载场景已物理退役。个人实验与官方包状态静态归位，导航只有一个所有者，迟到异步任务不能抢页或滚动。
- AI 提供商首批为 DeepSeek 和 OpenAI，endpoint 由代码注册表固定；用户只能选择受审计模型并保存自己的 key，不能填任意兼容 URL。四个业务域都先由服务端准备有界动作，再逐阶段显示提供商、模型、发送范围、最大调用数与 token 预算。
- 文献自动流程按阶段执行并在每阶段后持久化非敏感 checkpoint；只有最终显式提交才把候选原子写入科学库。个人表格 AI 只预填，用户浏览并一次确认；精确搜索、资料包管理和本地预览不依赖 AI 密钥。
- 源码发布门串行通过 1178 项（810 共享核心、229 macOS、139 Windows），Python 编译、六个前端脚本语法、发布哈希与 Git 对象检查通过。生产 SQLite 未进测试或提交；用户授权删除的 paper_056 遗留现场已清理。
- 最终 DMG SHA-256：`9b7fcaa5a2da2902d86ba305359d1d143a40b381c48054fa205fb61eafd9baa3`；UserKit ZIP SHA-256：`fc139f4b3a3f268b246218d01eb8805d71049596ad63cf0f241db1ae94ca7e27`。用户套件不包含 Git bundle、生产数据库或 PDF。
- Windows 0.8 预版本源码消费同一共享 Web/DTO/AI 契约和 Credential Manager，但真实 Setup 与 Win11 真机全流程仍未完成，继续 `installer_ready=false / SETUP_PRESENT=NO`。

## 0.7.0-preview.2 macOS 课题组内部候选

- 图书管理员、收费论文提取和私人表格 AI 识别共用版本化的首次知情同意门；取消时不发送请求，也不写入会话状态。纯本地提取路径不会错误提示收费或外发。
- 私人实验页和资料包中心保持一级入口；导入完成后的迟到搜索响应不得滚动用户已经离开的页面，资料包导航由共享视图路由唯一管理。
- macOS 导出目标继续要求本机普通目录、一次性 opaque token、目录身份复核和目标不存在；修复 PyObjC Foundation mapping/NSNumber 被误判为非本机的兼容问题，未放宽网络盘或未知卷。
- 发布门串行通过共享核心 619、macOS 186、Windows 源码契约 125，共 930 项。Windows 仍为 `installer_ready=false`，无 Setup 或 Win11 真机验收。

## 0.7.0-preview.1 macOS 课题组内部预览

- 新增一级“导出 / 导入资料包”，统一展示官方资料库当前版本、导入、已安装版本和回退，以及论文集合导出、私人实验导出、用户包导入和后台任务进度。共享前端已把资料包业务从 `desktop_product.js` 拆入独立 `package_center.js`，这是前端 P1 治理的第一个落地点。
- 用户包严格分为 `literature_collection` 与 `personal_experiments`，不能混装、不能写入官方库，也不进入 Librarian。论文集合支持逐篇、当前筛选和全部论文；私人实验只包含 confirmed/indexable 投影与受控原始 CSV/TSV/XLSX。
- 用户包固定为 `sha256-only`：未加密、来源未认证、仅限课题组内部。导出只要求一次明确风险确认；论文 PDF 分享权限仍逐篇确认。导入必须确认已通过其他渠道核对校验码。
- 共享 `package-job-v1` 已把规划、权限检查、归档、完整性审计、导入和失败投影改为后台任务；页面显示 `code/stage/outcome/progress`，失败不应改变既有活动库。
- macOS 已接入资料包中心 API、原生 opaque token、后台运行时和联合搜索刷新。论文集合 PDF 只通过 `source_id/paper_uid` 解析，并以受审计、无路径、同一文件描述符 lease 分块发送；私人实验结果不获得该入口。
- 新版官方包身份为 `0.2.0-preview.1`，兼容 `>=0.6.0,<1.0.0`；旧 `0.1.0-preview.1` 保持不可变回退点。应用与资料包继续独立更新。
- 干净 release worktree 串行通过 912 项联合测试（共享核心 605、macOS 182、Windows 125）；最终资料包/安全发布门另通过 92 项。冻结 App 冒烟、临时签名、DMG 校验、版本/构建号和隔离数据库哈希保持均通过。
- 官方包真实验收完成 `0.1 installed → 0.2 installed → 0.2 already_active → rollback 0.1 activated`，每一步均重新验签、审计并打开 60 篇/4356 条四类证据。测试未使用生产数据库。
- `/Applications` 中仅保留一个 `Auto Research.app`（`0.7.0` / build `9`）；旧 `0.4.0` 与 `0.6.1` 已移到仓库外回退目录。Windows 仍为 `installer_ready=false`，无 Setup。

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
