# Auto Research 阶段性交班总览

> 交班快照：2026-07-30  
> 活跃项目：`/Users/USER/Zotero/auto-research`  
> 稳定提交：`8c57825`  
> 稳定标签：`evidence-demo-2026-07-29-librarian-recall-stable-1`  
> 交班文档标签：`evidence-demo-2026-07-30-project-handoff-skill-1`  
> 证据库版本：`2026.07.29-librarian-recall-stable.1` / schema v12

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

目标是从真实 PDF 中提取可检索、可回到原文的实验数据、定性结论、完整表格和论文图片，并通过浏览器完成搜索、证据查看、校对、导出和 AI 辅助问答。

核心原则：

```text
论文身份 → 真实 PDF → 实验类型 → 数据/结论/图表候选
        → 原文证据 → 自动质量门 → 搜索与只读分享
```

Zotero 是论文和 PDF 来源；`db/experimental_evidence.sqlite` 是独立证据数据库。不要把它们合并，也不要直接修改 Zotero 数据库来实现证据功能。

## 3. 为什么采用当前形态

用户是物理背景，希望不写 SQL、不写提示词也能完成真实实验数据整理。因此产品采用：

- 中文浏览器工作台；
- 固定六列数据模型；
- 每条记录保留页码、定位和短原文；
- 数值数据、定性结论、表格、图片四类分开；
- DeepSeek 承担项目运行时 AI；Codex 只负责开发和维护；
- 自动质量门决定自动候选是否进入搜索；人工校对用于纠错和校准；
- 搜索默认覆盖整个数据库，并支持 CSV/Excel 导出；
- 本地编辑端与公网只读端共用一套前端和数据库。

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
- 图书管理员采用固定三段式：DeepSeek 查询规划、本地四类覆盖召回、DeepSeek 条件核验与中文总结；
- 页面显示候选总数和回答引用数，保留所有有界候选；
- 相同数据库证据版本、问题和有限历史可复用一小时完整结果；
- 图书管理员固定搜索全库，精确检索才允许限制论文范围；
- 详情 AI 对话只读取当前选中条目和有限 PDF 相关页，不能写数据库。

### 4.7 本地与分享版本

- 本地编辑端：数据检查、文章切换、上传、人工补录、质量抽取、搜索与导出；
- 公网只读端：只展示搜索、详情、原文证据和只读 AI 能力；
- 两端共用 `web/index.html`、`app.js`、`app.css`，禁止维护第二套前端；
- ngrok 只开放只读端口，不公开本地编辑端。

## 5. 当前真实状态

以2026-07-29稳定版为准：

| 对象 | 数量/状态 |
|---|---:|
| 数据库论文/文档 | 60 |
| 原始六列历史记录 | 6,501 |
| 可报告数值来源 | 5,048 |
| 独立物理事实 | 3,142 |
| 定性实验结论 | 937 |
| 隔离旧文字值 | 1,453 |
| 图表资产 | 291（59表格、232图片） |
| 固定验收 PDF | 50/50 身份与内容有效 |
| 数据就绪论文 | 17/50 |
| 图表就绪论文 | 30/50 |
| 自动测试 | 196 项通过 |

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

### 8.1 启动本地编辑端

双击：

`/Users/USER/Zotero/打开本地编辑工作台.command`

或：

```bash
PYTHONPATH=src python3 -m auto_research.cli evidence-serve --host 127.0.0.1 --port 8765
```

### 8.2 启动只读公网端

双击：

`/Users/USER/Zotero/创建导师公网链接.command`

脚本应启动本地只读端口8766，再由 ngrok 暴露临时网址。不要把8765编辑端分享出去。

### 8.3 基础只读验收

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
node --check src/auto_research/evidence/web/app.js
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
- 不维护第二套“导师页面”；只读模式必须复用同一前端；
- 不把浏览器本地 Agent 历史当服务器证据；
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

- DeepSeek key 优先来自 `DEEPSEEK_API_KEY`，其次来自 macOS Keychain 服务 `auto-research-deepseek`；
- ngrok 配置在本机非Git环境文件中；
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
| 搜索/Agent | `docs/SEARCH_AND_AGENT_ARCHITECTURE.md` | `search_index.py`、`agent_runtime.py` |
| 详情AI对话 | `AGENT.md`证据对话章节 | `context_chat.py` |
| 网页 | `STABLE_RELEASE.md` | `webapp.py`、`web/index.html`、`web/app.js`、`web/app.css` |
| 上传/去重 | `README.md`证据上传章节 | `uploads.py`、`document_recognition.py` |
| 维护发布 | `MAINTENANCE_WORKFLOW.md` | `maintenance.py`、`db_health.py`、`self_check.py` |
| Zotero语料 | `AUTO_RESEARCH_HANDOFF_1000.md` | `zotero/`、`acquisition/`、`scripts/*zotero*` |

以上代码路径均相对于 `src/auto_research/`。

## 13. Git、备份和回滚

当前 `origin` 指向本地历史 bundle，不是可推送的 GitHub 远端。不要把 `git push` 成功与否当作已有线上备份。

稳定发布顺序：

1. 停止写入任务；
2. 运行 `evidence-reconcile-runs --older-than-hours 6`，只收口运行元数据；
3. 复制 `db/experimental_evidence.sqlite` 到仓库外；
4. 运行全套健康、测试、浏览器和只读权限检查；
5. 检查密钥、PDF、数据库和无关个人文件；
6. 提交 Git；
7. 创建带日期标签；
8. 创建并验证包含全部引用的 Git bundle；
9. 记录 SQLite 和 bundle SHA-256；
10. 确保 `git status --short` 为空。

最近稳定恢复点：

- Git标签：`evidence-demo-2026-07-29-librarian-recall-stable-1`
- SQLite快照：`/Users/USER/Zotero/auto-research-backups/experimental_evidence-librarian-recall-stable-2026-07-29-v1.sqlite`
- Git bundle：`/Users/USER/Zotero/auto-research-backups/auto-research-librarian-recall-stable-2026-07-29-v1.bundle`

本次交班额外封存：

- 交班时 SQLite 快照：`/Users/USER/Zotero/auto-research-backups/experimental_evidence-project-handoff-skill-2026-07-30-v1.sqlite`
- 快照 SHA-256：`b1c9703f46577e6caac1246ca79a89f02ac4817a5ff93cbfae52b35680d264ee`
- 独立 Skill 压缩包：`/Users/USER/Zotero/auto-research-backups/auto-research-evidence-maintainer-skill-2026-07-30-v1.zip`
- Skill 压缩包 SHA-256：`ea3198cf0b9d85e880e85c1bccc632c346e8bc8ea6e7d3d4a12e77ad22f5e747`

不要使用破坏性 Git 命令回滚用户工作树。需要恢复时优先新建目录验证 bundle 或快照，再由用户确认切换。

## 14. 已否决或暂缓的方向

- MinerU/云端视觉增强：实测质量不如本地稳定图表链路，已经完整回滚；
- 自动曲线读点：证据风险过高，当前明确禁止；
- 每条数据必须人工批准后才能搜索：用户已选择自动质量门优先；
- 单独维护导师版网页：已改为同一前端的只读权限模式；
- 把全部模型输出放进一种“AI结果”：违反四类证据模型，禁止；
- 立即引入向量数据库：尚无金标准证明现有FTS无法满足，暂缓；
- 公共GitHub直接发布完整数据库/PDF：版权和隐私边界未解决，暂缓。

## 15. 下一阶段最有价值的工作

推荐顺序：

1. 建立30–50个图书管理员问题的人工金标准和自动回归指标；
2. 每批3–5篇补齐13篇数据就绪论文，使17/50达到至少30/50；
3. 为材料、粒子、温度、剂量和物理量增加确定性硬条件解析；
4. 建立轻量用户反馈，区分漏检、错引和条件理解错误；
5. 证明需要后再评估向量检索；
6. 完成私有GitHub或代码+脱敏演示库的发布范围设计；
7. 最后再考虑低成本常驻只读托管。

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
unzip /Users/USER/Zotero/auto-research-backups/auto-research-evidence-maintainer-skill-2026-07-30-v1.zip \
  -d ~/.codex/skills
```

然后在新对话中明确说：

> 使用 `$auto-research-evidence-maintainer`，从 `/Users/USER/Zotero/auto-research` 读取交班状态，再继续工作。

如果技能没有自动发现，也可以要求 Agent 直接读取项目内 `SKILL.md`。项目内副本优先于任何旧账号记忆。

## 17. 交班结论

当前项目是“可稳定展示、可继续开发”的实验文献证据平台，不是已经完成全部语料和科学人工验收的最终数据库。下一位 Agent 的第一责任是保护已有证据链、质量门、图表和统一前端；第二责任是用可测量的方式扩大语料和提高检索质量，而不是重新发明架构或追求看起来更智能但无法核验的功能。
