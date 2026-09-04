# Auto Research 阶段性交班总览

## 1.2 单候选验收（2026-09-05，当前唯一开发权威）

当前安装和逐项结果只以`docs/BUILD54_INSTALLED_ACCEPTANCE.md`顶部为准：同一build54候选已聚合到7ed286b；完整中文图书管理员实机1次/约5秒成功，16引用重启可恢复，R16可打开原图与核验网格；4689条训练数据集已实际保存，JSONL/Parquet逐条一致，重启收据存在。仍未完成提取取消/续跑/收尾、完整拖拽布局矩阵及其余用户包端到端验收，不是稳定版。主额度最后72%，严格保留70%；前端协作停手、Windows冻结，不再新建build/DMG/UserKit。三用途回退与生产DB哈希已核实，重复build/dist及前候选回退已清理。

00:27补验：隔离合成PDF运行中停止及重启后的取消状态保留已通过；授权期间发生外部UI变化，实际有2次调用，不能记作零调用取消。尚未验证中断续跑/零调用收尾。已恢复正式根，主额度最新71%，不再开启模型或构建批次，保留70%硬下限。

以下9月4日及更早段落保留为历史；锁屏、安装身份、失败和成功均不得覆盖当前台账。

- 20:45最新安装：同一`1.2.0/build54/candidate`已集中更新至`e870094`，1367共享通过/80跳过、338 Mac通过、冻结HTTP/签名/事务安装通过。历史引用、warning终态、v2披露、数值格式及CSV分页状态修复已安装；下一步真实点击被Mac锁屏阻断，尚未通过，仍不是稳定版。先解锁，再按`docs/BUILD54_INSTALLED_ACCEPTANCE.md`队列继续。主额度剩余76%，70%硬下限；前端协作已停手，不再启动构建/模型。正式App明确使用canonical根，旧候选与dist重复App已清理，三用途回退保持。

- 后续隔离CSV实机门已通过：3行对照与123×3表均预览正常，123行分页→手动序列→一次确认→私人搜索→曲线122有效/1缺失/误差棒→重启重新搜索与打开全部完成；末行1.22 dpa/3.322 GPa/0.050 GPa。原生Open改用键盘Return，之前AX超时不能再写为产品必然故障。已恢复正式App根；生产DB哈希不变。共享1367通过/80跳过；本批历史/授权/终态/分页与提示修复仍待聚合候选安装。主额度剩余77%。

- 19:42当前安装权威已更新为同一`1.2.0/build54/candidate`、core=`20cfea8`、可执行SHA=`18e1007f...d67d`；完整身份、测试、回退与逐流程证据只看`docs/BUILD54_INSTALLED_ACCEPTANCE.md`。仍不稳定，不产生build55/DMG/UserKit。
- 实机选中Table 3后，证据AI已用官方同源5x4核验行列正确报告三种材料的辐照前后硬度、硬化量与不确定度；图书管理员短问真实成功，但完整跨材料问题仍有非确定性的数值门拒绝。源码只收紧最终回答格式，未放松数字或引用门；必须安装后重复验收。
- 已修复但未安装：Librarian降级终态不再永久计时；加密历史16条引用不会恢复为0；v2授权准确披露官方+已发布工作区+有限历史+可选已确认记忆，私人实验始终不发送；旧v1不能继续授权。48项聚合AI/历史/授权目标通过。
- 隔离123行CSV已被原生选择器接受并复制入一次性暂存，但Open之后WebView自动读取超时，未勾预览/确认。已退出隔离进程并恢复正式根；生产DB哈希仍为`0389a5aa...80b0`。主额度最新剩余80%，继续守70%下限。

- 18:55当前覆盖权威：已聚合安装同一`1.2.0/build54/candidate`，core=`22807aa`；共享1355通过/80跳过、Mac337通过、冻结冒烟/签名/事务安装通过。正式工作区Table 4原图可见。没有新增build/DMG/UserKit，dist重复App已移除，回退只保留0.5/build24/上一候选9f77aff三用途。
- 实机AI验收仍失败：设置连接验证成功且四scope有效，但复合问题先`harness_invalid`后`business_action_invalid`，未出现provider活动。已在源码复现并修复URL-safe授权编号首字符误拒、混合来源seed执行错序、稀疏R号重编和模型前失败被fallback/minimum-call掩盖。111组合目标、最后76 Harness目标通过，未重新构建。首个现场错误是否就是nonce问题尚无trace证明。
- 隔离123行CSV实机流程停在原生选择器自动操作；没有预览/确认成功，不得勾验收。已退出隔离进程、恢复正常App与正式根。下一步先核对当前AI修复/错误血缘和原生选择器，再集中安排同候选复验；不是直接再升版。
- 额度最新快照：主账户剩余83%，用户要求至少70%；所有任务共享。前端已有任务只读诊断后停手。完整当前实机状态/哈希/回退/未通过项统一看`docs/BUILD54_INSTALLED_ACCEPTANCE.md`，下面当日早先“未安装”的段落保留为历史，不再覆盖本段。

- 集中安装前审查补齐私人主栏恢复断点：活动标签已有校验page时直接投影，不依赖已清空/重启后的搜索列表；异步恢复只更新仍活动的同一主栏/页面。79项包含恢复/曲线/分页/详情的目标通过；前端已停手。最终共享1355通过/80明确跳过、Mac337通过（5项现有SWIG警告），发布哈希/生产JS/差异检查通过。将复用现有build54-current干净工作树进行一个聚合候选，不新建release目录或DMG。

- 额度约束：用户要求主账户至少保留70%。本轮读取已用12%、剩余88%；这是读取时快照，所有Codex任务共享，继续前须复查并留出交接余量。前端任务已收到停手待命通知；不为赶额度降低稳定验收门、不扩展新功能。
- 私人实验曲线源码已接入：主/次栏共用被动绘图模块，完整序列按原行序展示，缺失断开、单位/误差棒可核验；同名标签关闭再打开不会接收旧请求。真实123行CSV从导入至JS校验为122有效点、1缺失、49+73两个连续段。73项前端/数值与40项Mac/发布/所有权目标串行通过（后者5项已有SWIG警告），另14项核心安全目标通过；套件重叠不相加。集成首次发现只读请求白名单漏接，修复后整组重跑通过。新资源已纳入Mac保护静态路由/冻结清单/发布哈希；未构建、未安装，实际曲线显示、返回及重启验收仍开放。
- 18:06解锁后已在同一正式根App实际点击复验：Table 4原始数值表截图、Figure 10载荷/位移曲线均可见，随后返回Table 4。此前锁屏阻断记录不再是最新状态；仍不代表Table 4已拥有审核后的结构化行列，亦不代表本轮曲线或其他源码修复已装入App。

- 私人实验数值链源码收口：123行合成CSV通过真实预览、一次确认、私人搜索、服务重建、4页读取及实际Fusion渲染回查，数值/单位/缺失值保持一致。主/次组共用条件与序列说明、活动标签检查器；空尾页显示“当前页没有数据”，不再出现151–123行。共享完整序列只读接口已具备，5000行硬上限、缺失保留间断、不补零、不拟合；曲线UI尚未接入。47项共享/Mac与66项前端目标串行通过，无模型/App/构建/生产数据操作。不是截图所示论文Table 4的结构化恢复，也不宣称安装版已包含本批。
- 最新表图复验尝试：保存数据根及PID15999的cwd均为正式工作区；Computer Use返回Mac锁屏，未能重新点击Table 4/Figure。本次不能把17:03/17:05的先前可见记录当作新的实机通过。待解锁后先复验，不新增build。

- 导出恢复源码收口：等待600次仍未终态、网络中断或返回身份不符时保留原job，任务列表提供“继续查看原任务”，只GET且单飞；未知终态期间阻断再次导出，真实终态后才释放。回执force刷新合并但不丢后续GET，失败可重入重试，旧GET/POST不倒退revision。28项前端目标通过（含上述四种任务情景和并发回执），未装入App，不升build。17:03实机复核同一build54有效数据根仍为正式目录，Table 4原表及Figure 10曲线图均真实可见；Table 4已核验行列仍未完成。

- 数据集文件链源码收口：真实PyArrow/ZIP/Mac保存token/加密回执隔离集成覆盖123篇492条，JSONL/Parquet逐条一致、manifest校验和/大小一致、服务重建后回执保留。修复中文文件名被builder误拒绝、原生保存错误血缘、生成途中同名文件覆盖风险；前端协作修复私人范围旧计划迟到及picker→请求→轮询重复导出。48共享/Mac+23前端目标通过。仍未装入App；导出轮询超时后继续跟踪、force回执刷新并发两项P1下一步继续，不生成新build。

- AI反馈与草稿源码收口：现有前端协作任务实现活动计时排除授权等待、终态冻结、重绘不增长；授权期间切A→B只清原A输入，明确断言prepare/history/execute仍绑定A。root按四业务细化阶段文案，实验建议不再假称引用完成，提取仍以发布/索引收据为准。80项目标检查、JS/哈希/diff通过；未装入App、未模型调用，真实验收仍打开。

- 图书管理员召回源码收口：12条本地查询优先材料×条件×物理量联合式；64候选/16模型证据预算不变，保留四类中真正符合条件的代表。既有不可变工作区快照重放为58候选、15直接+1相关（表格仍缺温度）；旧顺序同快照为7直接+5相关，不能把早先错误数据根现场的0直接简单归因于召回。84项目标测试通过；未装入App、未调用模型，真实回答充分性仍待验收。

- 选中表格AI上下文已做源码接线：复用详情页同一官方表格服务/工作区opaque resolver，只发已核验行列和版本，单独内容快照在消费授权时重核，源变化停止旧动作；不发送未核验值、审核备注、路径或私人实验，不增加模型调用。107项共享/macOS目标检查通过（5项现有SWIG弃用警告）；还未构建或真实模型复验，Table 3数值解释仍不能勾通过。前端协作任务只读确认请求身份绑定正常，另报授权切标签可能清掉另一标签同文草稿及出站断言缺口，留作后续前端批次。

- 数据集计划误拒绝已做源码修复：对齐后端`paper.title/doi/year`缺失字段；解除错误的100项权利提示上限，按论文/记录真实资源边界验证完整列表并每页显示50项，翻页不清除确认、不截断身份。84项目标测试通过，含真实builder→service→前端的123篇/246风险合成语料链。尚未装入App或完成真实文件导出验收，不能把下文失败项直接勾为通过。

- 本轮表图缺失的现场根因为App仍指向`/private/tmp/auto-research-build54-extract.XignFQ`临时验收目录。已恢复正式根`/Users/USER/Zotero/auto-research`并重启同一build54，真实右栏Table 4原表与Figure 10曲线图均显示。临时目录保留，不删除其未知实验数据。以前只核对App清单、未核对有效数据根的工作区验收需重新限定结论；详见验收清单的Workspace correction。新增启动/错误反馈防护仅在源码，未构建。

- 当前安装清单已现场核对：`1.2.0/build54`、`release_status=candidate`、core commit `9f77aff`。下方`b2578a6`安装记录已是历史，不再用于判断当前App。
- 当前验收权威为 [`docs/BUILD54_INSTALLED_ACCEPTANCE.md`](docs/BUILD54_INSTALLED_ACCEPTANCE.md)。真实调用已揭示证据AI遗漏可见表格数值、图书管理员直接证据分类不足、耗时展示失真；9月4日真实数据集计划又返回`dataset_plan_invalid`。这些均未修复验收，禁止称稳定、生成DMG或递增build。
- 先完成既有验收队列，再集中修复；新表格问题排在既有工作之后，不因新截图更换主线。官方读取、设置状态及部分按钮路径已通过，不等于整体功能通过。源码全测、旧版成功和真实科学正确性必须分别记录。
- 现有功能协作任务因额度限制未完成只读诊断；root继续低负载收集证据，不伪称协作完成，不另起子agent。Windows冻结、root唯一Git写入、生产SQLite及`paper_056`保护规则不变。

## 1.2 单候选恢复线（2026-09-02，历史检查点）

- 当前唯一可启动App为`/Applications/Auto Research.app`（`1.2.0` / build54，manifest `release_status=candidate`，core commit `b2578a6`）。可执行文件SHA-256为`1f0e7e7d334248049239cd57aa0628d559489eaa00d089d284c0c26576c8cbd7`，深层签名验证通过。build24继续作为已验证稳定回退点；build54仍须完成剩余产品问题后才能称稳定版。
- build54同批次已经完成：真实安装App中的图书管理员、选中证据问答和私人表格AI建议；隔离单页PDF以2次模型调用完成四类候选、质量门、原子发布和索引刷新；共享1,133项（80项按设计跳过）、macOS 309项及Python/JavaScript/发布哈希检查通过。
- 官方`1.1.0`包已经在真实App首次导入和重复导入，活动库保持59篇、4,369条、59份PDF和291项视觉资产。真实安装App已完成`搜索官方资料 → 打开Table 3真实截图 → 查看原文 → PDF缩放/翻页控件 → 返回来源标签`，返回后保持原详情与证据AI对话；后端资产租约使用已校验的不可变快照，不向渲染器暴露路径或资产内部ID。
- 官方Table 3结构化行列已进入同一build54安装App并完成真实WebView验收：从官方包声明的同一PDF SHA、第5页和维护者审计bbox建立5×4人工转录候选，在主编辑组逐格核对并确认为`verified`；App重启后仍保持已核验状态、20个单元格和CSV/XLSX入口。真实导出文件逐格一致，CSV SHA-256为`6da9f00af46bb286f7253eaebb4e740d5bb7f7d8fd2b735f8c08affff81b66e8`，XLSX SHA-256为`19bcb181b6c3e080d00a20e15f38b20061392284e6fe60d8c5d2a60a9a90e22a`。只有`verified`版本可导出；候选/拒绝/缺失仍失败关闭。不可变`1.1.0`包尚未修改，携带sidecar的新包必须独立升包版本并重新签名、审计和验收。
- 当前源码检查点为`fab4c8a`。它包含`7180df7`的工作区表格严格同源只读链接：只有DOI、工作区PDF实际SHA、来源页及工作区截图实际SHA与活动官方包唯一匹配时，才显示官方已核验结构；Table 3真实快照返回5×4行列，同时保留工作区自己的原始截图，且不复制官方审核状态。文献提取恢复P0新增零模型收尾入口：认证检查点后只执行本机质量包校验、原子发布和索引刷新；任务目录不可读/损坏时失败关闭，同一论文与当前PDF已有未完成任务时在prepared action及模型调用前阻断。随后`d8fd944 / ad72177`补齐安全取消：queued任务零模型终止，running任务在收费调用安全边界停止，成功回执先持久化，未知结果保持`outcome_unknown`；Fusion只对文献任务显示单一停止按钮，刷新后恢复`cancelled`终态，关闭PDF选择器则明确显示未导入、未调用模型。`47a6d7d / fab4c8a`进一步为图书管理员返回的本机表格建立path-free opaque详情链：真实截图、同源已核验二维行列和PDF使用同一公开身份；缺少可靠行列时明确终止，不退回旧数字ID或伪造表格。当前安装build54（`b2578a6`）尚不包含这些源码修复。
- 文献提取恢复批次通过111项提取/检查点/AI控制器/macOS路由目标、59项Fusion契约和27项路由/架构/发布契约目标；这些套件有重叠，不相加冒充全套。没有调用模型、构建App、生成DMG/UserKit或创建build55；生产SQLite与`paper_056`现场未暂存。
- 本批源码定向门为154项（领域、macOS、Fusion、真实签名包与发布契约）通过；完整串行门为共享1,139项（80项按设计跳过）和macOS 323项。签名资料包与科学证据层共用的来源身份、边界框、行列规范化和内容指纹已抽到仅依赖标准库的中立契约，消除了`product → evidence`反向依赖；固定金向量锁定v1指纹字节，未放宽敏感信息或科学边界。真实安装App已经补齐官方Table 3的候选、主栏审核、批准、重启恢复和两种导出；生产SQLite SHA-256验收前后均为`0389a5aa0faf4967696e3c3a5a574eaaf0bec08a34f48c69b98f50c705aa80b0`。
- 发布流程保持单候选制：源码提交、目标测试或单个缺陷修复不递增build、不生成DMG/UserKit。工作区表格链接、文献零模型收尾和安全取消源码批次已经收口；继续聚合剩余Fusion流程，只有完整安装后用户验收表通过才覆盖构建并封箱一个候选。
- opaque工作区表格批次通过89项共享/macOS/Fusion/发布契约定向检查；在此之前同一源码线已经串行通过共享1,152项（80项按设计跳过）和macOS 326项。此后未调用模型、未构建App、未生成新build；下一次全套与真实WebView表格验收仍只在聚合候选时串行执行一次。
- 仓库外现只保留0.5功能比较App、build24稳定回退App、当前`b2578a6`候选回退、一个完整Git bundle以及不可重建的资料包/数据库/软著输入。三份旧build54回退、两个旧发布App和干净build54发布工作树已在身份/哈希核对后删除，约释放2.7GB；含未知SQLite改动的build49工作树继续隔离，不得自动清理。

以下build53/build51/build50内容只作历史追溯，不再描述当前安装版或现行发布步骤。

- build51源码候选已收口到运行时代码检查点`5c2055f`：任务感知栏位投影会隐藏无意义的检查器/第二编辑器控制，图书管理员宽屏保持“历史 + 完整对话 + 可点击结果”三层，AI失败保留具体阶段与恢复入口；布局投影保持纯计算，拖拽或命令面板不得通过副作用隐藏全部编辑器。该检查点串行通过共享`1116`项（另80项明确外部样本跳过）、macOS`307`项、Python编译、10份生产JavaScript语法和发布资源哈希检查。验证前后用户生产SQLite哈希均为`0389a5aa0faf4967696e3c3a5a574eaaf0bec08a34f48c69b98f50c705aa80b0`；未暂存数据库或`paper_056`现场。
- build51已从干净提交`4040a73`完成冻结冒烟、ad-hoc签名、DMG生成和事务式安装，当前唯一可启动App为`/Applications/Auto Research.app`（`1.2.0` / build51）。旧build50保存在仓库外不可启动回退副本。DMG SHA-256为`8e5d2696037a6679cf533bb52018b380c55ee73a25ef50a08165459492977799`，App可执行文件SHA-256为`347e3fb19787f80f174e19676a5389cda7a5d371ba169e3ee76583574d7f46f5`。
- build51候选用户套件位于`/Users/USER/Zotero/auto-research-releases/v1.2.0-build51-mac-candidate/Auto-Research-1.2.0-build51-Mac-Candidate.zip`，SHA-256为`b67fc7e578e270d13e275598c7308d9f693d3f36ed93a02884f3a7abcc266289`。套件包含DMG、独立`1.1.0`官方资料包、8页中文教程和校验清单；三项制品与ZIP均已复核。官方包再次在隔离新数据根完成真实安装和只读仓库审计：59篇、4,369条（item 3,142 / finding 936 / table 49 / figure 242）、59份PDF、291项视觉资产，内容指纹`bc226740124c83c97d1c98e28686cc0fae648656e3c4f57578e60200afc3f301`。
- build51仍未完成同一安装制品的真实WebView与四项AI点击验收，稳定声明继续暂停。Mac当前锁屏，Computer Use不能读取或操作App；解锁后必须完成四入口、布局/分栏、搜索、真实证据/PDF、设置/历史，以及图书管理员、证据问答、实验建议和一页隔离PDF提取各一次。不得把上述构建、资料包审计或此前源码级真实模型调用写成build51稳定验收。
- 上一候选build50来自干净提交`45720ce`，曾完成构建与安装，现已由build51事务式替换并保存在仓库外不可启动`.app.rollback`；以下build50记录只作历史追溯。
- build50已串行通过共享`1109`项（另有80项明确外部样本跳过）、macOS`305`项、Python编译、10份生产JavaScript语法、14份发布资源哈希、冻结App冒烟、严格深层签名、DMG校验和隔离60篇快照流程；生产SQLite与`paper_056`现场未进入测试或构建。
- build50修复收费调用硬超时、拒绝响应释放、结果未知错误血缘，并把文献提取压缩为每个“分支×完整页分段”一次合并提取；1/7/15页初始调用上限由`6/12/24`降为`2/4/8`，仍保留双分支、按需覆盖核验、第三审核、表图与曲线安全门。
- 用户套件已原子生成：`/Users/USER/Zotero/auto-research-releases/v1.2.0-build50-mac/Auto-Research-1.2.0-build50-Mac-UserKit.zip`，SHA-256为`69c2373a2e85cc9acce76d2bd780b2e64cbfab3d0f9b27652a5350213dbb5820`。内含DMG、独立版本`1.1.0`官方包和8页中文教程；ZIP与套件内三项文件均已复核。
- build50当时也因Mac锁屏未完成安装后真实WebView点击验收，现不再是当前候选。此前真实DeepSeek已分别跑通图书管理员、证据问答、实验建议和一页文献提取，但这些历史调用不能代替build51安装制品验收；优化后的整篇调用规划也尚未再次付费压力测试，不得把自动测试写成新的科学准确率证据。
- 用户已批准 `Auto-Research-1.2-Audit-Proposal/expected-workbench.html` 作为最终生产界面的验收预期：保留0.5的从容、主操作可见和完整工作流，采用VS Code的活动栏、上下文栏、标签与按需分栏。
- 全面审计与串行实施计划由提交`c8b2c66`冻结，权威文件为`docs/AUTO_RESEARCH_1_2_FULL_AUDIT_AND_PLAN_ZH.md`、`docs/WORKBENCH_LAYOUT_AUDIT_1_2.md`和`config/workbench-recovery-contract.json`。
- build47来自提交`033b12b`，已被用户实机证明存在布局、AI和连续工作流回归，只保留为失败历史，不得作为稳定基线。
- 当前源码检查点为`30c7080`。Phase 6已在源码层接入独立`operation-history-v1`：资料包导入、资料包导出和训练数据集导出的状态保留近30天、最多100条，macOS使用独立AES-GCM存储，Fusion在“资料包 → 任务与收据”显示queued/running/completed/failed/interrupted。重启后的未完成任务只标记“已中断并返回相应流程”，不会伪装续跑；已落盘但完成回执为pending的导出可跨App重启只恢复可信回执，不重新选择位置、不重跑导出或dataset构建。历史不保存路径、selection/destination/plan token、敏感正文、密钥或原始job id。`8525763 → 8929883 → d9148dc → 78d15bd → a28b9e2 → 30c7080`分别完成平台核心、通用安全存储、任务生命周期观察、Mac API恢复、生产组合和Fusion投影。
- 资料包/dataset与personal-import已从Fusion主脚本物理拆分；Fusion已把取消零请求、三项用户包风险确认和逐篇PDF权限迁到当前控制器测试；私人实验保存后若搜索刷新失败，实验首屏显示幂等“恢复私人搜索索引”，不会重复确认、导入或调用AI。共享/Mac正向路径已停止读取四个旧Web控制器；旧JavaScript只因Windows冻结静态清单保留。
- Phase 0—6的主要源码接线、完整串行测试、候选构建和事务式安装已完成。仍未完成的是安装后真实WebView点击验收、跨重启长任务continuation/checkpoint、cancelled终态与旧四套加密JSON存储迁移；这些未完成项继续保留在架构债务中，不因build50生成而改写为完成。上一轮旧Fusion hydration实验仍隔离在Git stash `quarantine-interrupted-fusion-experiment-20260825`，不得恢复到当前主线。
- Phase 2只恢复公开标签身份、按需第二编辑器、每标签内存滚动/焦点和异步请求绑定。重启后只懒加载当前可见标签，正文、聊天、路径和滚动位置不写入布局缓存；标签在请求期间移栏时旧结果失败关闭并可重试，不会串栏或抢焦点。
- 页面默认三层、按需四层，永不同时显示上下文栏、两个编辑器组和检查器。文献、搜索、图书管理员、实验、资料包、设置的允许栏位和首屏主操作由`workbench-recovery-contract.json`唯一规定。
- 表格恢复链提交为`32cb790`：只有从不可变PDF快照提取、经人工确认的二维字符串结构才能导出，候选、拒绝或缺失结构均不会伪装成数据；Fusion主/次编辑组按标签隔离结构状态。build50的完整串行门已经覆盖该链，仍需安装后真实表格与原图并列点击验收。
- root仍是唯一Git写入、集成与发布者；其他同项目Codex任务只编辑明确文件范围，不stage/commit。电脑发热时最多一个前端开发任务与root低负载审查并行；全测、构建和模型调用继续冻结。
- 生产`db/experimental_evidence.sqlite`和`data/evidence/visual_assets/paper_056_4e85e57cc6/`保持用户现场，不得暂存、还原、测试、构建或清理。Windows继续冻结。

以下历史章节只用于追溯，不再覆盖本节的当前权威。

## v1.1.0 build 43 Mac 候选（2026-08-25，历史记录）

- 当前安装版为 build 42；build 24 仍是已验证稳定回退点，build 41已有不可启动回退副本。build 43 保持 `1.1.0` 语义版本，只有干净提交构建、事务式安装、真实 WebView 和四项付费 AI 验收全部通过后，才能替换稳定结论。
- 修复截图中的分栏 P0：拖动边界期间只更新布局变量，不重建标签与编辑器 DOM；`pointerup / pointercancel / lostpointercapture / window blur` 统一结束拖动。损坏的“双编辑组同时隐藏”状态会自动恢复至少一个有内容编辑组，工具菜单与抽屉不会滞留成空白页。
- 图书管理员回答新增独立可点击结果栏，引用证据与推荐论文可直接打开到第二编辑组；空间不足时自动收起无关栏位，点击结果后保留研究对话并把检查器中的证据 AI 绑定到真实实体。
- 证据 AI 按证据身份维护本次会话内的独立多轮对话。用户在某条证据回答期间可继续浏览，结果写回原证据线程；界面不会把后台任务误标成当前证据正在回答，也不会跨来源串线。
- 四项收费动作使用会话绑定的后台执行任务和真实活动事件。进度只显示固定白名单中的 Harness、模型请求、只读证据工具、引用核验和结果校验阶段；不展示或接受模型内部思维链、提示词、PDF正文、工具参数、路径或密钥。外部执行器不能伪造完成状态或注入显示文本。
- build 41 已通过共享 `926/926`（80项明确外部样本跳过）、macOS `237/237` 和目标 `94/94`，并完成干净构建、DMG、事务式安装与真实 WebView；但真实图书管理员在8次模型调用后返回 `harness_output_invalid`，因此不得称为AI稳定版。本轮付费上限25次已使用8次，build 42重新验收最多还可使用17次，禁止在源码门通过前重试。
- build 42 增加任务感知的宽松布局：1280px双编辑器自动释放检查器，设置页临时隐藏第二编辑器和检查器，图书管理员聊天/结果/详情按任务切换无关栏位；标签、请求与滚动状态保留。舒适密度增加正文留白、段落行距与约820–840px聊天阅读宽度。
- build 42 为 Harness 保留第六次无工具最终回答轮次，避免耗尽8次调用后没有可校验终稿；最终投影只保留应用核验过的引用和推荐论文身份，允许单个纯JSON围栏但不从混合文字中猜测恢复。
- 图书管理员历史现在从本机加密状态区恢复、搜索、重命名与删除；“研究记忆”与聊天历史使用独立密钥和文件，只保存用户逐条确认且带官方/工作区来源的结论，不保存PDF正文、私人实验、路径或凭据。跨不兼容证据包的定量综合继续失败关闭。
- 当前阶段验证包括布局/历史/研究记忆72项、四项AI与Harness相关290项，以及冻结发布接线21项；这些套件存在重叠，不能相加冒充全套数量。完整共享/macOS套件、干净提交、App/DMG、事务式安装及剩余真实DeepSeek验收尚未完成。
- build 42 源码冻结后的完整串行结果：共享 `941/941`（80项明确外部样本跳过）、macOS `241/241`；生产 JavaScript 语法、Python 编译、发布资源哈希和差异检查通过。干净提交、App/DMG、事务式安装及剩余真实 DeepSeek 验收仍未完成。
- build 42 已从干净提交 `69b1f07` 构建、签名、生成有效DMG并事务式安装；真实 WebView 布局、搜索、加密历史和四项就绪状态正常。但首个图书管理员业务在模型调用前被冻结运行时的 `ai_consent_invalid` 阻断，因此 build 42 不合格且没有新增收费调用。
- build 43 删除同意绑定对精确 Python 类身份的依赖，改为逐字段、逐类型和摘要完整性验证；新增冻结 App 内“不调用模型的准备→签发→消费”同意闭环烟测，避免源码测试通过而 PyInstaller App 失败。修复后共享 `942/942`（80项明确外部样本跳过）、macOS `241/241` 通过；仍需完成干净构建与四项真实 AI 验收。
- 生产SQLite SHA-256基线保持 `18b9a4a3a4cbdf9ffcbe14fa0e4855727a6211c1f61902e3d30324903bc84`，仍是用户未提交现场，不得暂存或进入构建。Windows继续冻结；本候选不运行Windows测试或构建。

## v1.1.0 build 27 Mac 候选（2026-08-23，尚未宣称稳定）

- build 24 仍是已验证的稳定回退点。build 25 因 DeepSeek 推理探针正文为空撤回；build 26 修复连接与工具能力后，在真实 App 发现实验预填验证计划为2次、前端硬上限却为1次，导致无确认框直接失败，也已撤回。build 27 保持 `1.1.0` 语义版本；在真实 App、真实 DeepSeek 付费四业务和用户流程通过前，不得覆盖稳定结论或创建正式标签。
- 新增唯一 `PaneLayoutController` 与 `fusion-pane-layout-v2`：上下文栏、检查器和两个编辑器组均可吸附收起，保留标签/请求/滚动状态并恢复上次宽度。宽屏点击文献或搜索证据会自动在右侧打开可替换预览；双击、提问或打开 PDF 后固定。主工具栏不再显示含糊的 `↶`。
- 新增 `ai-readiness-v1`，分别投影提供商连接、Harness 和文献提取/图书管理员/选中证据问答/实验预填四项能力。设置页可管理 DeepSeek、OpenAI 和受本地 SSRF/DNS 重绑定安全门限制的公开 HTTPS OpenAI-compatible 提供商；密钥和已保存 endpoint 永不回显。
- 保存密钥只触发用户确认后最多一次低成本连接验证；四项业务首次使用再分别执行有界能力验证。取消保持零业务请求。底层错误保留 `cause_code / stage / next_action`，缺少 PDF 等免费本地前置错误不会再被 AI readiness 通用错误遮蔽。
- build 26 已用用户保存的本机 DeepSeek 凭据完成连接、V4 Pro/Flash结构化输出与工具调用，以及文献提取/图书管理员/证据问答三个能力门；个人预填因前端验证上限错误未发模型请求。build 27 将业务动作预算与能力验证预算分离。密钥未输出。生产 SQLite SHA-256 基线仍为 `18b9a4a3a4cbdf9ffcbe4fe14fa0e4855727a6211c1f61902e3d30324903bc84`，禁止进入测试、构建或 Git。
- 尚未完成：四项真实业务能力与端到端付费验收、完整测试、干净提交构建、事务式安装、DMG/UserKit/教程封箱。Windows继续冻结；其 Fusion 新静态资源清单缺口只记录为后续迁移门，本轮不修改。

## v1.1.0 macOS 核心能力恢复线（2026-08-22，历史记录）

- 当前源码与发布契约固定为 Auto Research `1.1.0` / build `27` 候选；Windows 1.1迁移完全冻结，现有 Windows 1.0身份仍为`installer_ready=false`，不得因Mac源码变化生成或宣称新的Setup。
- Fusion工作台已恢复独立滚动文献目录、题名/作者/DOI筛选、服务端四类多选筛选、可持久恢复的预览/固定标签与两个完整编辑器组、按模块隔离的检查器、中央PDF来源高亮/返回链和图书管理员独立标签。新上传论文会自动选中并滚动到可见位置，迟到请求绑定原标签。
- 上下文栏、两个编辑器组和右侧检查器之间的三条边界可像VS Code一样拖动调整；支持键盘方向键、Shift步进、Home/End、双击复位，并只在本机保存非敏感宽度偏好。窄窗口仍按既有抽屉/单组规则收敛，不丢失标签状态。
- 生产图书管理员和选中证据AI已迁到精确锁定的DeepSeek Harness SDK/runtime；八个工具全部只读，不提供Shell、文件系统、PTY、编辑器、子Agent或任意网络。prepared action、逐次授权、provider/model白名单、token/调用预算、凭据与引用完整性仍由Auto Research掌握；校验失败不回退旧AI。
- Harness历史使用本机加密状态区，默认最多20个会话或30天并支持立即清除；旧收费路由在Mac桌面固定410。旧Librarian V3仅保留兼容测试/历史简报边界，列为P1物理退役债务。
- 新增`dataset-bundle-v1`：同一规范投影生成JSONL、真实Parquet、数据卡和manifest，以论文为单位确定性划分train/validation/test；默认不复制PDF/图片、不包含私人实验，不泄露路径、密钥、会话或内部数据库主键。
- 不可变官方快照的数据集计划为60篇/4,356条（item 3,142、finding 936、table 46、figure 232），train 3,556、validation 425、test 375；缺失DOI 3，未审核记录2,417，内容指纹`8f853102d0a4774efad59fdd3be88d2ca6b685930aeb767dfa09bd80ca0341ea`。这些数量不代表科学人工验收完成，含未审核记录的导出必须显式确认。
- 科学发布审计独立覆盖数值、单位、意义、条件、表格、图片、结论、片段和定位等14类指标，并要求论文级人工金标准、TP/FP/FN、页码/IoU和泄漏检查；自动测试通过不能替代科学准确率。
- 新版官方资料包按用户冻结范围明确发布59篇，并在manifest排除 DOI `10.2172/6065200`（`source_pdf_unavailable`）；这不删除本机或Zotero记录。其余4个HTML占位输入已由期刊/Europe PMC真实PDF替换并逐份核对DOI、题名、页数和哈希。签名包包含4,369条证据（item 3,142 / finding 936 / table 49 / figure 242）、59份PDF和291个图表资产，大小374,780,867 bytes，SHA-256 `909cc7323b8a91e3a238e8f49d03fe62022f5a9d078f2cc6785bb8a0b73a69c4`。隔离新根验收为首次`installed`、重复`already_active`，59份PDF与291个资产全部通过安装后运行时校验；视觉状态仍诚实报告为历史`draft`，不能写成科学人工审核完成。
- 架构与技术债事实已写入`docs/ARCHITECTURE_AUDIT_1_1.md`、`config/architecture-debt.json`和`config/module-ownership.json`。全仓Python约109,950行，Fusion前端约7,338行，`app.js`约4,379行；仓库目录内历史Mac制品约1.06GB仍是P2迁移债务。
- 当前工作树仍只允许用户的`db/experimental_evidence.sqlite`作为未提交现场；禁止暂存、还原、清理或用于发布测试。1.1保护标签为`auto-research-v1.0.0-pre-1.1-protection`，仓库外保护目录为`/Users/USER/Zotero/auto-research-backups/v1.1-protection-20260822-154915`。
- build 24发布实现由提交`393efda`封口，最终发布标签以文档封存提交为准。冻结接口后串行通过共享880项（其中80项因仓库未携带历史外部样本而明确跳过）和macOS 234项；Python/JavaScript语法、发布契约与差异检查通过。测试日志仍暴露少量SQLite `ResourceWarning`，列为后续连接生命周期债务，不伪装为科学错误。
- 本轮新增人工审核队列及原子发布/索引恢复、工作区+官方联合Harness只读检索、完整PDF分段提取、视觉资产事务式暂存、正式证据搜索过滤和path-free稳定错误；旧收费路由、旧DOM和第二导航没有恢复。
- 已从独立干净worktree重建、冻结冒烟、ad-hoc签名并事务式安装`/Applications/Auto Research.app`；版本`1.1.0`/build`24`、严格签名、唯一可启动副本和DMG镜像均已复核。DMG位于`/Users/USER/Zotero/auto-research-releases/v1.1.0-build24-mac/Auto-Research-1.1.0-macOS-arm64.dmg`，SHA-256为`f3363092dc8dfea9af4aaa9fe1920e164781c585536e7146d718c0cd7e4dfc5c`；完整Mac UserKit SHA-256为`2c943904090bbcfde8ce1aa45c2b107f1482905f7090bdd3b648abe2596ca99c`。旧build24 App已保存为仓库外不可启动rollback。真实Mac只读验收已完成：文献筛选、双编辑器、三条可访问分隔线、真实表格原图、应用内PDF、重新显示高亮、返回证据和关闭PDF均正常；本轮未调用收费模型。

### Windows 全离线 Build Kit 长期规则

- Windows构建包必须在Mac端预先物化并校验离线Python、锁定wheelhouse、Inno Setup 6.7.3与WebView2 Evergreen x64；Windows端只负责从本机C盘生成Setup，不应联网补依赖。
- 所有云同步/移动盘输入先复制到本机普通短路径，再核验大小和SHA；资料包审计使用私有临时副本，避开OneDrive占位、Defender/索引器锁和打开文件删除差异。
- 二进制文件描述符必须带`O_BINARY`；Python 3.12 Windows不得假设`os.fchmod`存在；关闭所有句柄后再replace/unlink，对WinError 5/32/33仅做有界重试。
- Inno、wheel、WebView2、源码ZIP和官方包均绑定精确哈希；Setup生成后仍需在真实Win11完成安装、导包、搜索、上传、BYOK、升级与卸载。Mac验证不能解除`installer_ready=false`。

## v1.0.0 macOS 首个稳定交付线（2026-08-21，当前权威）

- 当前源码身份固定为 Auto Research `1.0.0` / build `22`，唯一生产界面是 Fusion 四区工作台：文献、搜索、实验、资料包；设置是底部工具入口，不是第五个科研工作流。当前主工作区仅保留用户的 `db/experimental_evidence.sqlite` 未提交现场，禁止暂存、还原、清理或用于发布测试。
- v1 已在 Fusion DOM 内恢复既有成熟能力：PDF 导入与分阶段自动提取、四类证据中央详情及真实表格/论文图片、中央 PDF 与返回链、精确/联合搜索、图书管理员、选中证据受控 AI、CSV/TSV/XLSX 预览与可选 AI 预填/一次确认、私人实验分页真实表格、官方包导入/回退、论文集合与私人实验包导入导出、单条证据 CSV/XLSX 导出和工作区测量/结论批量导出。
- 活跃页面已经移除“测试、体验、预览、合成、后续版本”等面向用户的发布占位文案；没有恢复旧 0.8 页面、第二套导航、浏览器工作台、导师只读页或 ngrok。私人数据、官方包和可写工作区仍保持独立来源与生命周期。
- 串行发布前验证已通过：共享核心 `773/773`、macOS `226/226`，共 `999/999`；JavaScript 语法、Python 编译、release-contract 同步和 Git diff 检查通过。Windows 本轮完全暂停，未运行 Windows 测试或构建。
- 官方 v1 资料包为 `auto-research-internal-evidence-1.0.0.aresearch`，SHA-256 `d1337a43aa4c0b83030a70e6a500bc60b85a994cb03d287396e895957ae4604d`；包含 60 篇论文元数据和 4,356 条四类实体（item 3,142 / finding 936 / table 46 / figure 232），不含 PDF、图片、私人实验、路径或密钥。其签名身份与 rights 仍是受保护的课题组内部发布者契约，不能改写成公开再分发许可。
- 生产 SQLite 在本轮开发与测试前后 SHA-256 均为 `d3e225d35f4c9e21fcf825405d0e38f28210c4479caac67b87be9f8382fb6f72`。科学完成度仍独立为 17/50 data-ready、30/50 visual-ready；软件稳定不代表语料完成或有人类金标准验证。
- 发布边界：v1 是 Apple Silicon macOS 课题组稳定版；当前机器没有 Developer ID 身份，因此采用 ad-hoc 签名、未公证，首次打开需遵循中文指南中的 Gatekeeper 安全步骤。Windows 仍无 Setup，必须等用户明确下令后才恢复迁移。
- Windows v1 RC 已在用户明确下令后恢复，源码修复提交 `ff7b5d2` 关闭了 Windows 二进制模式、Python 3.12 `fchmod`、Defender 文件锁、冻结资源定位、Win32 进程探测、WebView2 离线部署、保留文件名/ADS与重解析点等平台差异。平台定向契约170项及真实v1官方包冻结输入审计通过；新的全离线Build Kit尚待重打并由用户在真实Win11生成Setup，因此仍是`installer_ready=false / SETUP_PRESENT=NO`。
- 当前 `/Applications/Auto Research.app` 在 v1 安装事务完成前仍是 0.9.2/build21。构建、安装、DMG、标签和 UserKit 哈希只有在干净发布工作区及最终验收报告绑定同一提交后才可写入本节；不要提前把源码候选描述为已安装制品。

以下 0.9/0.8 内容仅作历史回退与决策记录，不再是当前功能说明。

## 0.9 Fusion 功能恢复线（2026-08-20，历史）

- `0.9.2-preview.1` / build `21` 已恢复四类证据的中央结构化详情，并把工作区 `table/figure` 重新接回真实 PyMuPDF 截图，不再显示类型占位符。文献列表与搜索结果共用同一中央详情，原图失败有明确结束态；官方/私人来源没有二进制路由时只显示结构化字段，禁止借用工作区编号或生成替代图。
- build21真实Mac验收已打开 Table 4 与 Figure 9 原图，显示图注、物理量、变量、材料、实验条件、方法和关联数；中央PDF打开后可返回原证据，搜索页也能进入同一真实表格详情。生产SQLite SHA-256前后均为 `d3e225d35f4c9e21fcf825405d0e38f28210c4479caac67b87be9f8382fb6f72`。
- 串行验证：共享核心753项、macOS 215项、Fusion详情11项、冻结HTTP冒烟、codesign与DMG校验全部通过。源码提交 `83cf5e4`，标签 `auto-research-0.9.2-preview.1-build21`，DMG SHA-256 `87e29d20d926111aac784d8883ea19330ac818b754b8f3f86171f22b94943a11`。
- `/Applications/Auto Research.app` 当前为0.9.2/build21；build20已保存为仓库外不可启动 `.app.rollback`。UserKit位于 `/Users/USER/Zotero/auto-research-releases/Auto-Research-0.9.2-preview.1-build21-UserKit.zip`，SHA-256 `1848453eeff79e1ad8fe206e3f3475282116afd3bbeac7de9a8657a80dfd0331`，含DMG、中文教程、验收报告和已重新验签的官方0.2资源包。
- 用户已明确接受0.9.1 Fusion工作台作为后续功能重构的唯一界面底座；旧0.8页面没有复活，Windows继续暂停。
- `0.9.1-preview.2` / build `20` 已在这一套Fusion DOM内恢复Mac核心功能：文献页顶部“导入 PDF / 开始自动提取与核验 / 打开 PDF”，搜索页“精确检索 / 图书管理员（官方全库）”，实验页顶部“选择 CSV / TSV / XLSX”及一次核验导入，设置页提供DeepSeek/OpenAI受信配置。
- 中央PDF查看和“返回当前论文”已通过真实App验收；精确检索“辐照温度”返回82条，图书管理员入口、实验文件入口和双provider设置均可见。未调用收费模型，生产SQLite哈希保持不变。
- 完整验证为共享750项、macOS 214项、冻结HTTP冒烟、JS/Python检查、codesign、DMG校验和真实WebView用户流程。构建源码提交 `056e2fb`；DMG SHA-256 `470dabcf5b9f491f08807677e2a3068fe185748eed2f3ee21299492e6bd26396`。
- 当前仍未恢复为正式可用的域：资料包/通用导出中心、选中证据AI、私人实验大表分页详情。界面必须继续明确禁用，不能伪造完成。用户套件位于 `/Users/USER/Zotero/auto-research-releases/Auto-Research-0.9.1-preview.2-build20-UserKit.zip`。

## 0.9.1 Fusion GUI 审核基线（2026-08-13）

- 当前开发目标固定为 `0.9.1-preview.1` / build `19`：只实现 `fusion-concept.html` 的生产级四区工作台，不并行修复搜索详情、PDF返回、真实导出、自动提取或私人实验详情。只有用户在真实Mac App中明确认可GUI后，才依次进入0.9.2—0.9.5。
- 运行界面只有一套Fusion DOM和一个 `fusion_review.js` 导航所有者；App包仅包含 `index.html`、`app.css`、`workbench.css`、`fusion_review.js` 四个前端资产。0.8旧脚本保留在源码历史边界供后续逐域迁移，但build19不打包、Fusion路由固定403，不能成为第二套生产界面。
- build19只读浏览启动时从schema-v12生产库创建独立SQLite快照；实验页只使用明确标记的合成CSV。除外观/密度设置外，POST/DELETE和其他PATCH固定403，PDF、导出、上传、提取、确认、资料包操作与模型调用均被后端能力策略阻断。
- Windows完全暂停：不要同步Fusion资源、版本或路由，不运行Windows测试/构建。Mac主流程获用户认可后才恢复Windows薄迁移。
- 0.8 build18仍是完整功能回退点。安装build19前必须把它复制为已验证的非 `.app` rollback；安装事务失败必须恢复旧App。生产SQLite哈希必须在开发、构建和安装前后保持一致。
- build19源码标签固定到 `4fd4f24`。Fusion/发布/核心目标28项、完整核心证据测试、macOS 212项、冻结HTTP冒烟、ad-hoc签名、DMG校验和真实WebView验收均通过；实际验证了四区导航、真实文献/证据同步、合成CSV两工作表与键盘选择、日夜/系统主题、舒适/紧凑持久化、窄窗口检查器抽屉/Escape焦点恢复，以及设置分类不会污染文献中央标签。
- `/Applications/Auto Research.app` 当前为0.9.1/build19；系统与用户Applications中只有这一份可启动App。0.8 build18回退副本位于仓库外 `auto-research-backups/app-rollbacks` 且后缀为 `.app.rollback`。DMG与中文指南在 `auto-research-releases/Auto-Research-0.9.1-preview.1-build19-FusionReview/`，DMG SHA-256为 `c1786d4b4ee6734390f36c878fdcf008f5d0c3ab405f5eac87031ffe40b92f69`。

### 当前恢复顺序

1. 保留工作树中的 `db/experimental_evidence.sqlite`，绝不暂存、还原或清理。
2. 只在Fusion DOM内继续串行恢复Mac功能；不复活旧页面，不启动Windows迁移。
3. 下一阶段优先恢复私人实验分页表格与核验曲线、资料包/通用导出、选中证据详情AI；每项先冻结共享DTO，再接Fusion界面。
4. build21只能称为Mac内部功能预览；ad-hoc签名、未公证、未执行收费模型验收，也没有Windows Setup。随附官方0.2资源包已经验签，但Fusion资料包导入/回退UI仍未恢复，不得宣称可在本版界面导入。

## 0.8 发布冻结点（2026-08-13，优先阅读）

- Auto Research `0.8.0-preview.1` / build `18` 源码已冻结；build 16/17 分别在冻结路径与前端契约冒烟门被拦截且未发布。发布身份和六个共享 Web 资源哈希由 `config/release-contract.json` 单一管理。
- Workbench A 成为唯一生产设计系统：日间/夜间/跟随系统、舒适/紧凑密度、四个科研入口、设置中心、命令面板与键盘导航均使用同一共享前端。个人实验和资料包 DOM 已静态归位；旧人工补录、修正历史和宠物进度场景已从生产 DOM/处理器退役。
- 运行时 AI 只允许代码审查过的受信提供商（首批 DeepSeek/OpenAI）和固定官方 endpoint/model 目录。Librarian、选中证据、文献提取、个人表格四域全部走服务端 prepared action、逐阶段披露、一次性 consent nonce 和调用/token 预算，旧收费旁路固定退役。
- macOS 已接完整四域业务、设置、资料包、私人实验与文献最终提交事务。Windows 0.8 源码消费同一共享契约、工作台资产和 Credential Manager 槽；Windows 仍 `installer_ready=false / SETUP_PRESENT=NO`，等待 Win11 真机构建验收。
- 本轮串行验证：共享核心 810、macOS 229、Windows 139，共 1178/1178；Python 编译、六个 JavaScript 语法、release-contract 同步、diff-check 与 Git 对象检查通过。测试使用临时根，没有读取或写入生产科学数据库。
- `paper_056` 的 4 个 DeepSeek run、2 个 quality run 与 13 个图表遗留文件已在用户明确授权下删除；它们未进入 Git。工作树仍只保留未暂存的 `db/experimental_evidence.sqlite` 用户现场。
- 可回退用户版保持 Auto Research `0.7.0-preview.2` / build `15`，源码 `888aae5`，标签 `evidence-demo-2026-08-12-macos-package-center-0.7.0-preview-2-build15`。

### 后续恢复顺序

1. 读本节、`STABLE_RELEASE.md`、用户套件内 `RELEASE_ACCEPTANCE.json` 与项目 Skill current-state。
2. 运行 `git status --short`；只允许生产 SQLite 作为既有未提交现场，禁止 `git reset/checkout/clean`。
3. macOS 后续修复从 0.8 发布标签分支；先跑目标测试，再决定是否需要新 build。不要在主工作区直接构建。
4. Windows 下一步只是在 Win11 使用预版本构建套件完成真实依赖、Credential Manager、WebView2、启动、导包、搜索、上传和 BYOK 验收；通过前不得生成或宣称正式 Setup。
5. 0.9 前继续拆分旧 `app.css` 与 `app.js`，但每次必须删除被替代规则/处理器，不能再叠加新皮肤或复制平台 UI。

### 多对话协作纪律

- 使用用户已有的同项目 Codex 对话，不新建子 agent：前端/macOS、共享核心、Windows、安全四个职责位。
- root 是唯一 Git 暂存/提交者。其他对话只在明确文件范围内编辑，完成后停手并报告，不得 stage/commit。
- 同一文件只能有一个对话所有者；接口先由共享核心冻结，macOS 先消费并验证，Windows 最后薄同步。
- 电脑发热时最多保留两个开发对话；禁止并行全测、构建、模型调用或 App 实机。目标测试小步串行，全套只在接口冻结后运行一次。

> 交班快照：2026-08-13
> 活跃项目：`/Users/USER/Zotero/auto-research`  
> 历史 0.8 发布候选：Auto Research `0.8.0-preview.1` / build `18`；当时的 DMG、套件与验收哈希见仓库外 0.8 用户套件的 `RELEASE_ACCEPTANCE.json`
> 当前 Mac 制品提交：`700e642`；构建清单、build18 标签与 UserKit 验收报告一致。后续 Windows 文档收口提交不改变 Mac App 字节
> 官方资料包：`0.2.0-preview.1`，SHA-256 `89ec7f8dcdeea2d862d91aaf798674fd600c21fd75c4553d270a0c0b806f999e`；旧 `0.1.0-preview.1` 保持不可变回退点
> Windows：继续 `installer_ready=false`，无 Setup、无 Win11 真机验收；不得把源码对齐描述为安装版
> 证据库版本：`2026.07.30-librarian-brief-stable.1` / schema v12

本文面向下一位 Codex Agent、工程维护者和未来的项目负责人。它说明项目为何存在、过去完成了什么、当前真正能做什么、日常工作流、禁止触碰的边界、验证与发布方法，以及尚未完成的目标。

项目内可移植 Skill 位于：

`skills/auto-research-evidence-maintainer/`

## 1. 新 Agent 的前十分钟

不要直接开始改代码或重跑论文。按以下顺序建立现场认知：

1. `cd /Users/USER/Zotero/auto-research`
2. 阅读本文件。
3. 阅读 `AGENT.md`；它是长期工程和科学边界的权威规则。
4. 阅读 `STABLE_RELEASE.md`；它给出当前版本、数据库数量和真实完成度。
5. 阅读 `MAINTENANCE_WORKFLOW.md`；涉及提交、发布或导师展示时必须遵守。
6. 执行 `git status --short`，保留所有既有未提交工作，绝不擅自还原。
7. 执行只读健康检查：

   ```bash
   PYTHONPATH=src python3 -m auto_research.cli evidence-db-health
   ```

8. 根据用户任务只读取对应架构文件，不要每次从头扫描全部历史日志。
9. 用户只要求解释、诊断或讨论时，不要修改文件、数据库或外部服务。
10. 用户要求实现后，先保护当前基线，再做最小变更、测试、提交、标签和 bundle。

## 2. 项目究竟是什么

Auto Research 不是一个单一脚本，而是两条相互关联、数据边界不同的工作流。

### 2.1 文献获取与 Zotero 语料工作流

目标是发现真实论文、合法获取可打开的本地 PDF、验证身份、导入 Zotero、分类并去重。

历史稳定检查点曾完成：

- Zotero 父集合 `Auto Research PDF-only 300 CLEAN - Object+Method`；
- 300 个顶层条目、300 个有效本地 PDF；
- 30 个“研究对象 + 研究方法”子集合；
- 当时没有元数据空壳、未归档条目或 DOI/精确题目重复组。

这是文献语料检查点，不等于实验数据证据库已经处理了300篇。开始新的 Zotero 写入前必须重新核验，因为 Zotero 状态可能在交班后变化。详细历史见 `AUTO_RESEARCH_HANDOFF_1000.md`；相关专用 Skill 是 `auto-research-zotero`。

### 2.2 实验数据证据库工作流

目标是从真实 PDF 中提取可检索、可回到原文的实验数据、定性结论、完整表格和论文图片，并在个人工作台 App 中完成搜索、证据查看、校对、导出和 AI 辅助问答。当前先在 macOS 本机完成开发预览，正式用户端预计为 Windows。

核心原则：

```text
论文身份 → 真实 PDF → 实验类型 → 数据/结论/图表候选
        → 原文证据 → 自动质量门 → App 内搜索、校对与导出
```

Zotero 是论文和 PDF 来源；`db/experimental_evidence.sqlite` 是独立证据数据库。不要把它们合并，也不要直接修改 Zotero 数据库来实现证据功能。

## 3. 为什么采用当前形态

用户是物理背景，希望不写 SQL、不写提示词也能完成真实实验数据整理。因此产品采用：

- 中文个人桌面工作台（当前 macOS 开发预览，正式用户端预计为 Windows）；
- 固定六列数据模型；
- 每条记录保留页码、定位和短原文；
- 数值数据、定性结论、表格、图片四类分开；
- DeepSeek 承担项目运行时 AI；Codex 只负责开发和维护；
- 自动质量门决定自动候选是否进入搜索；人工校对用于纠错和校准；
- 搜索默认覆盖整个数据库，并支持 CSV/Excel 导出；
- App 内部复用既有前端与 loopback 服务；localhost 不作为用户产品入口。

## 4. 已完成的主要阶段

### 4.1 文献获取与真实性

- 建立开放/官方来源的发现和下载链路；
- 明确只有真实、可打开的本地 PDF 才算获取成功；
- 完成 Zotero PDF-only 300 检查点、对象/方法分类和去重；
- 固定50篇证据验收语料，50/50 PDF 可打开且 DOI/题目身份匹配。

### 4.2 六列证据数据库

- 建立独立 SQLite，不改 Zotero 原始数据库；
- 固定六列：具体数值、具体意义、单位、文章题目、DOI、数据在文中的解释；
- 后三项之外另保留页码、表图编号、原文短片段、稳定键和版本历史；
- 纯文字材料名、方法名和趋势不再冒充数值，转入定性结论或上下文字段；
- 原始版本不可静默覆盖，确认、修正、歧义和不采用均保留历史。

### 4.3 通用实验类型识别

项目从“只针对辐照实验”升级为“先识别实验类型，再选择抽取重点”。当前可识别辐照、力学、显微/成分、热学、电输运、光谱、电化学/腐蚀、材料制备和磁学等实验类型。辐照材料仍是数据最成熟的方向，但不是硬编码边界。

### 4.4 DeepSeek 抽取与自动质量门

- PDF 文字层按有限页块发送给 DeepSeek；
- 两个独立分支分别偏重完整性和精确性；
- 本地程序按照数值、单位、意义、材料/条件、页码和原文片段配对评分；
- 低分候选由第三次 DeepSeek 对原文页复核；
- `dual_pass`、`third_pass` 和人工采用的 `manual_approved` 可以进入搜索；
- `manual_review` 和 `rejected` 保持隔离；
- 单路普通抽取默认只是预览，不能绕开质量门直接发布。

### 4.5 图表证据

- PyMuPDF 从真实 PDF 建立本地图表截图、图号、页码、哈希和跳转；
- 表格和图片在搜索、详情和校对页面中作为独立对象；
- DeepSeek 只依据图注和邻近正文生成中文标题、解释和标签；
- 已回滚 MinerU/云端视觉增强实验，生产路径不再依赖它；
- 不对曲线像素自动读点，不把视觉估算当精确数值。

### 4.6 搜索与图书管理员 Agent

- Search V2 使用 SQLite FTS 可重建投影，不改变科学事实表；
- 搜索覆盖条目、表格、图片、结论，支持题目、DOI、一作/通讯作者、材料、实验条件和中英文元素别名；
- “具体意义”的搜索权重高于“数据在文中的解释”；
- Librarian V3 先由本地路由区分系统能力、研究检索、宽泛综述、R#/B# 追问、澄清和普通对话；系统能力与普通对话保持零召回、零模型，研究路径才进入有界规划、四类覆盖召回、证据选择和本地完整性门；
- `research-state-v1` 与当前进程签名 token 绑定 conversation、证据版本、anchor/bundle 和父状态链；共享 UI 只在内存中携带状态，传输失败会清除旧 R#/B#，不会把 token 写入历史；
- 材料、辐照类型、粒子、温度、剂量/注量、物理量和样品状态是本地硬条件。DeepSeek 不能创建、跨字段注入或改写硬条件，同义词和元素别名只做软扩展；
- 本地将候选固定分成 `direct`、`adjacent`、`expansion`，并按论文、实际材料和完整实验条件建立 evidence bundle；只允许在同一兼容 bundle 内做定量前后比较；
- 科学计数法注量保持指数语义并支持等价单位比较；能区分 `300 keV` 与 `300 K`，以及 `Ni/He` 是粒子还是材料；
- 回答固定为直接结论、证据矩阵、相关证据、数据库空白和建议追问五段；页面显示候选总数和报告引用数，保留所有有界候选；
- Search V2、图表详情和图书管理员共用公开 DTO，不向只读响应暴露本地路径、Zotero key、本机文章 key、审核者或内部备注；
- 相同数据库证据版本、问题和有限历史可复用一小时完整结果；
- 图书管理员固定搜索官方文献全库，精确检索才允许限制论文或选择私人/全部来源；私人实验当前不进入 Agent 综合；
- 详情 AI 对话只读取当前选中条目和有限 PDF 相关页，不能写数据库。
- 当前结构化五段报告只有在同一服务进程 HMAC 签名、不是澄清回答、至少有一条实际引用且通过一致性门时，才能确定性导出为 Markdown 研究简报。
- 聊天响应顶层的 `research_brief` 信封包含 `snapshot_token`、`answered_at`、`evidence_fingerprint`、`eligible` 和 `ineligible_reason`；签名快照使用 `answered_at` 与 `evidence_version`。导出只接收当前进程签发的 `{snapshot, snapshot_token}`，不接受任意快照或 session id。
- 每个规范 R# 必须唯一映射到 `agent_cited=true` 的 `item/finding/table/figure`，引用计数完全一致。澄清、零引用、孤儿/重复/无效引用或过期/篡改 token 均拒绝导出。
- HMAC 只绑定公开响应快照，不保存服务器会话，不调用 DeepSeek，不重查 Search V2、SQLite 或 PDF，不读取曲线，不生成跨 bundle 定量比较，也不创建第五类证据。
- 导出采用公开字段白名单；缺失题目、DOI、页码或原文片段保持为空，使 `integrity.status=warning` 并在文件中展示。本地路径、Zotero/设备 key、审核身份、内部备注、历史标识和 PDF/图片载荷不进入文件。

### 4.7 个人工作台 App

- 唯一正式产品形态为个人桌面工作台：当前 macOS 预览使用 Auto Research.app，后续正式用户端面向 Windows；数据检查、文章切换、上传、人工补录、质量抽取、搜索与导出均在 App 内完成；
- `web/index.html`、`app.js`、`app.css` 和本机 loopback 服务继续作为 App 内部实现，不能据此把浏览器页面作为第二个产品发布；
- 图书管理员历史由桌面层以 AES-GCM 持久化：当前 ad-hoc 签名的 macOS 内部预览使用 Application Support 私有目录中的独立随机密钥，避免向非技术用户弹出误导性的 Keychain 密码框；正式签名 macOS 发行切换到 Keychain，Windows 使用 Credential Manager；`browser-local` 和 `readonly-none` 仅为历史兼容/权限测试；
- history schema 和保存策略完全不变；`research_brief` 授权只存在于瞬态 `state.librarianBriefAuth`，不进入 session meta/messages、`localStorage` 或桌面加密历史。重启或只恢复历史都必须重新检索后才能导出；
- 任何历史都不写科学数据库；桌面产品层是独立打包边界，共享前端适配不代表 `desktop/macos/**` 源码随本核心提交发布；
- 正式 App 不要求 Auto Research 编辑密码；任何意外系统钥匙串授权窗都视为发布阻断，由桌面层修复后才可交付；
- 导师只读页、浏览器工作台、`8765`/`8766` 和 ngrok 已退役，不再用于展示或分享。
- 正式发行采用“桌面 App + 独立证据包”：用户导入经过版本、哈希与签名校验的数据包后离线检索；用户自己的 PDF 与私人库分离。DeepSeek 抽取和图书管理员使用用户自己的 key，并通过平台安全凭据库保存。跨平台契约见 `docs/DESKTOP_PRODUCT_AND_EVIDENCE_PACKAGE.md`。
- 共享搜索 UI 分为“本地文献工作区”和“离线资料库”；离线资料库对 `official/private/all` 每次只执行一次联合精确查询，结果继续只有 `item/table/figure/finding`。renderer 公共投影不含本机路径、文件哈希、内部数据库/导入/草稿 ID。
- 私人 CSV/TSV/XLSX 在安全预览后由 DeepSeek 生成有界待核验建议；用户只浏览整页、修正错误并点击一次确认。后台仍固定执行 `previewed → draft_saved → confirmed/indexable`、revision/CAS 和私人搜索刷新；AI 不得确认记录。当前 UI 不支持趋势图附件或曲线读点。

### 4.8 资料包中心候选

- `0.7.0-preview.1` 源码候选新增一级“导出 / 导入资料包”，把官方资料库导入、已安装版本与回退，论文集合导出、私人实验导出、用户包导入和后台任务进度放在同一页面；旧官方包导入入口继续复用，没有复制第二套状态。
- 官方包和用户包严格分流：官方包继续签名、审计并可切换活动版本；用户包固定分为 `literature_collection` 与 `personal_experiments`，不能混合，也不能改变官方 active selector 或进入 Librarian。
- 用户包仅使用 SHA-256 完整性校验，不加密、不认证发送者身份，只限课题组内部。导出使用一次明确风险确认；论文 PDF 还需逐篇确认组内分享权限。导入要求用户通过其他渠道核对校验码。
- 论文集合支持逐篇、当前筛选结果和证据库全部论文；只读搜索保留 `source_id/paper_uid`，可用 PDF 通过受保护、无路径、同一文件描述符 lease 流式打开。私人实验包只允许 confirmed/indexable 结构化内容和受控原始表格进入。
- `package-job-v1` 已把规划、校验、构建、导入和失败状态收敛为共享后台任务；macOS 已完成薄 API、原生 token 和运行时接线。共享前端的资料包业务已从 `desktop_product.js` 首次拆入独立 `package_center.js`。
- 以上是已提交源码候选，不等于已发布制品。尚未完成 `0.7.0-preview.1` DMG、真实 Mac 导出/隔离导入/搜索/PDF 打开验收，也未完成 Windows 安装版接入。

历史只读浏览器验收仍保留为权限回归证据，但不代表当前仍发布网页版。`0.6.0-preview.1` 已从干净源码 `7909100` 构建，包含合并后的“文献处理”、DeepSeek 辅助个人表格识别、单次核验导入、V3 和共享离线资料库；它完成745项联合测试、冻结 App smoke、DMG 校验和真实安装界面验收。它仍是依赖 checkout v12 的内部预览，不得写成可移植正式版。上一 `0.5.1-preview.1` 制品继续作为回退点。

## 5. 当前真实状态

科学数据仍使用既有证据库口径；桌面制品已更新到2026-08-09内部预览。程序验收不得改写数据库计数、语料完成度或科学准确率：

| 对象 | 数量/状态 |
|---|---:|
| 数据库论文/文档 | 60 |
| 原始六列历史记录 | 6,501 |
| 可报告数值来源 | 5,048 |
| 独立物理事实 | 3,142 |
| 定性实验结论 | 937 |
| 隔离旧文字值 | 1,453 |
| 图表资产 | 291（59表格、232图片；243 是历史冻结基线，不是当前总数） |
| 固定验收 PDF | 50/50 身份与内容有效 |
| 数据就绪论文 | 17/50 |
| 图表就绪论文 | 30/50 |
| 自动测试 | 745 项通过（core 479 / macOS 157 / Windows 109）；最终 UI 契约另22项通过 |
| 生产 SQLite SHA-256 | `d62dc5c43ac9e0fb97e0ad2ecb85deaf50447fb7a036acae5147e8f6111236f6` |

| 源码/平台状态 | 当前事实 |
|---|---|
| macOS 当前源码候选 | `0.7.0-preview.1`：资料包中心、共享后台任务、用户包与安全 PDF lease 已接线；未构建、未完成实机发布验收 |
| macOS 最后已构建制品 | `0.6.1-preview.1` / `48a30e1`；DMG SHA-256 `f22c6c58...5ca0b98a` |
| Windows 源码 | `f649532` 已对齐共享资料包中心、后台任务和安全 PDF 契约；安全生产 resolver 仍 fail-closed |
| Windows 制品 | `installer_ready=false`；无 Setup、无 Win11 clean-machine 验收 |

首个内部官方资料包 `0.1.0-preview.1` 继续作为不可变回退金标准；新版交付候选为 `0.2.0-preview.1`，兼容 App `>=0.6.0,<1.0.0`。官方包不含 PDF、截图、私人实验、路径、Zotero key 或 API 密钥。官方包与 schema-v12 可编辑工作区是两个数据源，不能 `ATTACH`、覆盖或互相写入。旧包的既有内容口径保持：60篇论文元数据、4,356个只读实体（3,142 item、936 finding、46 table、232 figure）。

重点 DOI `10.1016/j.jnucmat.2018.08.031` 当前有231个独立事实，403/403处自动记录可回到 PDF 定位。

必须诚实报告：产品功能可稳定展示，但最初“至少30篇完成处理”的目标尚未完成，33/50仍需全文抽取；双路 DeepSeek 一致性也不能替代独立人工科学金标准。

## 6. 证据数据模型

### 6.1 六个用户字段

1. `value_text`：具体数值或明确表格标记；不能是长句。
2. `meaning`：该数值代表的物理量或具体意义，是主要搜索字段。
3. `unit`：保留原始单位；不明确时为空，不能猜测。
4. `article_title`：论文题目。
5. `doi`：有 DOI 时保存；无 DOI 允许为空。
6. `context_explanation`：材料、样品、实验条件、方法和文章语境，是核心搜索字段。

### 6.2 必须保留的证据字段

- `source_page`
- `source_locator`
- `source_excerpt`
- 论文、图表或数据对象关系
- 原始版本和修正版本
- 自动质量来源及分数

### 6.3 物理证据分类

- `measured`：仪器或实验直接测量；
- `derived`：由测量值推导；
- `calculated`：SRIM、模型或软件计算；
- `qualitative`：论文明确报告但没有可靠数值的现象。

来源精度包括 `exact_table`、`exact_text`、`trend`、`figure_only`。不得把 calculated/derived 冒充 measured。

## 7. 新论文进入数据库的标准工作流

### 7.1 接入与去重

1. 接收用户 PDF 或从 Zotero 关联真实 PDF。
2. 验证文件可以打开并包含真实正文。
3. 优先用 DOI、规范化题目和 PDF 指纹识别重复；Zotero key 只作为本机兼容字段。
4. 重复论文提示“本文已经提取过”，默认读取已保存数据。
5. 已扫描文章再次运行必须由用户确认并使用 `--force-rescan`；不得自动反复收费扫描。

### 7.2 本地预处理

1. 识别文章模式：实验、计算/模拟、综述/报告。
2. 识别主要实验类型和抽取重点。
3. 读取 PDF 文字层并建立页级证据。
4. 复用已有 PDF 指纹和图表缓存；缺失时才用本地稳定图表提取器补建。

### 7.3 自动抽取与发布

推荐入口：

```bash
PYTHONPATH=src python3 -m auto_research.cli evidence-quality-run "<DOI或题目>"
```

对已扫描文章只有用户明确要求时才运行：

```bash
PYTHONPATH=src python3 -m auto_research.cli evidence-quality-run "<DOI或题目>" --force-rescan
```

质量门按双路、第三次复核和隔离规则运行。成功候选进入原有四类搜索模型，不新建“AI结果”第五种类型。

### 7.4 抽取后的检查

- 运行数据库健康检查；
- 用 DOI、题目、作者、材料、性质和条件进行搜索；
- 检查原文证据按钮；
- 检查表格/图片详情；
- 检查数值与单位没有被纯文字污染；
- 生成逐篇质量摘要；
- 每3–5篇运行一次固定50篇语料审计。

## 8. 日常操作

### 8.1 启动个人工作台

用户直接打开 Auto Research.app。旧 `打开本地编辑工作台.command` 与 `创建导师公网链接.command` 只保留迁移提示，不得静默启动浏览器、端口或公网隧道。

维护者可以在隔离测试中使用 `evidence-serve` 验证 App 内部服务，但不得把 localhost URL 交给用户。

### 8.2 基础只读验收

```bash
PYTHONPATH=src python3 -m auto_research.cli evidence-db-health
PYTHONPATH=src python3 -m auto_research.cli evidence-search-benchmark
PYTHONPATH=src python3 -m auto_research.cli evidence-self-check \
  10.1016/j.jnucmat.2018.08.031 \
  --query 温度 --query 硬度 --query 钨 --query 'Wei-Ying Chen' \
  --min-rows 100 --min-highlight-ratio 0.8
PYTHONPATH=src python3 -m auto_research.cli evidence-test-set-audit \
  --config config/evidence_test_set_50.json
```

固定语料审计失败时先判断是程序回归还是尚未抽取。禁止伪造数据让状态变绿。

### 8.4 代码验收

```bash
PYTHONPATH=src python3 -m unittest discover -s src/tests -p 'test_*.py'
python3 -m compileall -q src
python3 - <<'PY'
import json, subprocess
from pathlib import Path
for path in json.loads(Path('config/release-contract.json').read_text())['web_assets']:
    if path.endswith('.js'):
        subprocess.run(['node', '--check', path], check=True)
PY
zsh -n scripts/*.command
git diff --check
git fsck --full
```

## 9. 任何 Agent 都必须遵守的规则

### 9.1 应该做

- 只在活跃本地项目路径工作；
- 先检查 Git 状态，保护用户改动；
- 所有科学数据必须有真实 PDF 和具体原文位置；
- 保留原始单位、原始版本和矛盾记录；
- 只在证据关系明确时标准化单位；
- 区分直接测量、推导、计算和定性结论；
- 使用 DOI/题目作为跨设备身份，逐步弱化 Zotero key；
- 复用现有数据库、四类搜索模型和统一前端；
- DeepSeek 失败时保持本地搜索、证据和导出可用；
- 每个阶段提交、标签、SQLite快照和Git bundle；
- 同时更新 `AGENT.md`、`PROJECT_LOG.md`、稳定版说明和必要架构文档；
- 区分程序稳定、语料完成、科学准确三个不同结论。

### 9.2 不应该碰

- 不在旧 iCloud 项目副本继续开发；
- 不恢复 MinerU、云端视觉、旧缓存或凭据；
- 不删除或重写现有 PyMuPDF 稳定截图；
- 不从曲线像素猜精确数据点；
- 不把模型输出直接当科学事实；
- 不绕过对抗式质量门发布单路抽取；
- 不强制将矛盾证据合并；
- 不用 Zotero key 作为跨设备公开身份；
- 不在页面、日志、Git、数据库或回答中输出 API key；
- 不将 Zotero 数据库、生产 SQLite、PDF 全文或受版权保护截图直接发布到公共 GitHub；
- 不恢复或发布“导师只读”网页、浏览器工作台、ngrok 或 localhost 用户入口；
- 不删除 App 所依赖的内部共享前端和只读权限回归；
- 不把浏览器本地 Agent 历史当服务器证据；
- 不把桌面加密历史、浏览器历史、任意公开快照或 session id 当成研究简报输入；简报必须来自当前进程签名、合格且有实际引用的非澄清回答；
- 不为恢复的旧回答重新签名；进程 token 失效后必须重新检索；
- 不让研究简报重新调用模型、检索数据库/PDF、读取曲线或产生跨 evidence bundle 定量结论；
- 不在用户只要求讨论或诊断时擅自改代码、重扫论文或开启公网服务；
- 不宣称已完成30篇或全项目科学验收。

## 10. Zotero 直接写入的特殊禁区

优先使用 Zotero API/Connector。确实需要直接修改 `~/Zotero/zotero.sqlite` 时：

1. 关闭 Zotero；
2. 创建带时间戳的数据库备份；
3. 只做最小、确定性修改；
4. 重新打开 Zotero；
5. 用本地 API 和验证脚本复核条目、PDF、集合和重复项。

绝不在 Zotero 正在运行时直接写库，也不把元数据记录误判为已有PDF。

## 11. 凭据和外部依赖

- 最终用户的 DeepSeek key 来自本人或专门提供给他的账号；产品只存平台安全凭据库（macOS Keychain / Windows Credential Manager），开发环境变量仅供维护测试；
- 旧 ngrok 配置仅属本机历史兼容文件，不再是产品依赖；
- `/api/ai/status` 只能显示是否可用，不能返回凭据；
- `.env`、`.env.*`、日志和临时上传文件不得提交；
- DeepSeek 是不可靠外部依赖，网络、429、5xx和格式错误必须安全降级；
- 当前生产图表能力不依赖视觉云服务。

## 12. 架构导航

| 任务 | 先读文件 | 主要代码 |
|---|---|---|
| 数据库/六列 | `AGENT.md`、`docs/irradiation_evidence_database.md` | `evidence/db.py`、`six_column.py`、`fact_model.py` |
| DeepSeek抽取 | `docs/adversarial_quality_gate.md` | `deepseek_extraction.py`、`quality_pipeline.py`、`prompts.py` |
| 图表 | `AGENT.md`图表章节 | `visual_evidence.py` |
| 搜索/Agent/研究简报 | `docs/SEARCH_AND_AGENT_ARCHITECTURE.md`、`docs/LIBRARIAN_RESEARCH_BRIEF.md` | `search_index.py`、`agent_runtime.py`、`librarian_reasoning.py`、`public_dto.py`、`research_brief.py`、`web/librarian_brief.js` |
| 私人导入/联合搜索 | `docs/PERSONAL_EXPERIMENT_DATA.md`、`docs/SEARCH_AND_AGENT_ARCHITECTURE.md` | `personal/import_service.py`、`personal/public_projection.py`、`personal/search_source.py`、`evidence/federated_search_session.py`、`web/fusion_personal_import.js` |
| 详情AI对话 | `AGENT.md`证据对话章节 | `context_chat.py`、`web/fusion_review.js`、`web/fusion_ai_experience.js` |
| 桌面网页工作台 | `STABLE_RELEASE.md`、`docs/LEGACY_WEB_CONSUMER_AUDIT_1_2.md` | `web/index.html`、`web/fusion_*.js`、`web/*_controller.js`、`web/app.css`、`web/workbench.css` |
| 上传/去重 | `README.md`证据上传章节 | `uploads.py`、`document_recognition.py` |
| 维护发布 | `MAINTENANCE_WORKFLOW.md` | `maintenance.py`、`db_health.py`、`self_check.py` |
| Zotero语料 | `AUTO_RESEARCH_HANDOFF_1000.md` | `zotero/`、`acquisition/`、`scripts/*zotero*` |

以上代码路径均相对于 `src/auto_research/`。

## 13. Git、备份和回滚

当前 `origin` 指向本地历史 bundle，不是可推送的 GitHub 远端。不要把 `git push` 成功与否当作已有线上备份。

共享 checkout 同一时间只能有一个 Git 写入者。其他任务保持只读，直到写入权明确交接；提交前必须显式 stage 声明文件并打印 cached name list。普通代码/文档提交不得纳入生产 SQLite、`paper_056`、用户 PDF 或现场运行产物。

稳定发布顺序：

1. 停止写入任务；
2. 运行 `evidence-reconcile-runs --older-than-hours 6`，只收口运行元数据；
3. 复制 `db/experimental_evidence.sqlite` 到仓库外；
4. 运行全套健康、测试、App 内共享 UI 和历史只读权限回归；
5. 检查密钥、PDF、数据库和无关个人文件；
6. 提交 Git；
7. 创建带日期标签；
8. 创建并验证包含全部引用的 Git bundle；
9. 记录 SQLite 和 bundle SHA-256；
10. 确保 `git status --short` 为空。

当前恢复层级：

- 当前 macOS 制品：标签 `evidence-demo-2026-08-09-macos-workbench-preview-6`，源码 `7909100`，版本 `0.6.0-preview.1`；唯一安装入口为 `/Applications/Auto Research.app`，App/DMG 已生成并验收。
- 上一 macOS 回退制品：标签 `evidence-demo-2026-08-09-macos-workbench-preview-5`，源码 `63b34f2`，版本 `0.5.1-preview.1`。
- Windows 仍未生成真实 Setup，也未完成 Win11 clean-machine 验收。
- Librarian V3 改造前保护点：标签 `auto-research-pre-librarian-v3-integration-2026-08-08`，提交 `a2b60e1`。
- 科学 SQLite 快照仍使用已记录的 `2026.07.30-librarian-brief-stable.1` 身份；代码文档收口不得改写其 SHA、数据计数或现场文件。

不要使用破坏性 Git 命令回滚用户工作树。需要恢复时优先新建目录验证 bundle 或快照，再由用户确认切换。

## 14. 已否决或暂缓的方向

- MinerU/云端视觉增强：实测质量不如本地稳定图表链路，已经完整回滚；
- 自动曲线读点：证据风险过高，当前明确禁止；
- 每条数据必须人工批准后才能搜索：用户已选择自动质量门优先；
- 导师只读/浏览器/ngrok 产品路线：2026-08-01 已正式退役；内部权限模式只为 App 和回归保留；
- 把全部模型输出放进一种“AI结果”：违反四类证据模型，禁止；
- 立即引入向量数据库：尚无金标准证明现有FTS无法满足，暂缓；
- 公共GitHub直接发布完整数据库/PDF：版权和隐私边界未解决，暂缓。

## 15. 下一阶段最有价值的工作

推荐顺序：

1. 完成 `0.7.0-preview.1` macOS 真实候选构建与“论文选择→导出→隔离导入→联合搜索→PDF 打开→私人实验导出/导入”验收；
2. 让 Windows 消费冻结的资料包中心与共享路由，生成 Setup 并完成 clean-machine 安装、导包、离线搜索、Librarian、升级和卸载验收；
3. 建立30–50个图书管理员问题的人工金标准和自动回归指标；
4. 每批3–5篇补齐13篇数据就绪论文，使17/50达到至少30/50；
5. 建立轻量用户反馈并完成私有GitHub或代码+脱敏演示库的发布范围设计。

## 16. 更换账号后的 Skill 使用

项目内 Skill 权威副本：

`/Users/USER/Zotero/auto-research/skills/auto-research-evidence-maintainer`

新账号/新 Codex 环境可以直接复制或建立软链接：

```bash
mkdir -p ~/.codex/skills
ln -s /Users/USER/Zotero/auto-research/skills/auto-research-evidence-maintainer \
  ~/.codex/skills/auto-research-evidence-maintainer
```

如果项目工作树不可用，可以从独立压缩包恢复：

```bash
mkdir -p ~/.codex/skills
unzip /Users/USER/Zotero/auto-research-backups/auto-research-evidence-maintainer-skill-2026-07-30-v2.zip \
  -d ~/.codex/skills
```

然后在新对话中明确说：

> 使用 `$auto-research-evidence-maintainer`，从 `/Users/USER/Zotero/auto-research` 读取交班状态，再继续工作。

如果技能没有自动发现，也可以要求 Agent 直接读取项目内 `SKILL.md`。项目内副本优先于任何旧账号记忆。

## 17. 交班结论

当前项目是“可稳定展示、可继续开发”的实验文献证据平台，不是已经完成全部语料和科学人工验收的最终数据库。下一位 Agent 的第一责任是保护已有证据链、质量门、图表和统一前端；第二责任是用可测量的方式扩大语料和提高检索质量，而不是重新发明架构或追求看起来更智能但无法核验的功能。
