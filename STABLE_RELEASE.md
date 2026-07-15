# Auto Research Evidence 图表云端影子增强版

## 版本身份

- 版本：`2026.07.15-cloud-shadow.1`
- Git 标签：`evidence-demo-2026-07-15-cloud-shadow-1`
- 证据库结构：`v11`
- 默认图表来源：`legacy`
- 本地编辑端：`http://127.0.0.1:8765/`

本版本保留 `2026.07.15-local-stable.1` 的全部稳定功能，并增加实验性的
MinerU + DeepSeek 影子增强。云端功能没有替换稳定图表，也不会在未经
质量验收和逐图表人工采用时改变搜索结果。

## 稳定数据快照

| 对象 | 数量 | 说明 |
|---|---:|---|
| 已登记论文 | 35 | 以 DOI 或题目作为跨设备身份 |
| 原文图表资产 | 243 | 40 个表格、203 幅图片 |
| 图表覆盖论文 | 30 | 这是稳定全库覆盖范围，不是云端测试规模 |
| 云端质量测试集 | 10 篇 / 91 个图表 | 固定配置，首批只处理这十篇 |

云端改造前基线保存在
`data/evidence/baselines/visual-baseline-2026-07-15-pre-mineru.json`。
基线清单 SHA-256 为
`b2eabbbf1115ec0f2d93eb3f34110686b34f8dfd13140370f10f556c04d60c68`；
243 个稳定截图均存在且文件哈希一致。

## 本版新增能力

- MinerU 异步任务：整篇 PDF 上传、排队、解析页数、结果下载和安全解包。
- DeepSeek 图表语义：基于图注、上下文、表格 HTML 和结构信息生成待核验解释。
- 稳定版/云端候选并排校对，可逐图表保留、采用增强、仅采用解释或标错。
- `legacy / shadow / hybrid` 三种来源模式；默认 `legacy`，后端强制执行
  十篇质量门，禁止页面绕过。
- 表格候选保存 HTML、行列、表头、脚注和单元格抽查信息；图片候选保存
  图注、坐标轴、图例、材料、条件、方法和趋势来源分类。
- 禁止从图片生成曲线数据点；视觉解释产生的精确数值不能进入实验结论。
- MinerU Token 仅保存在 macOS 钥匙串 `auto-research-mineru`，不进入 Git、
  SQLite、环境示例或日志。

## 零退化验收

- 243/243 个基线图表记录仍存在，243/243 个截图哈希未改变。
- 十篇固定质量集仍为 10 篇、91 个图表，旧搜索、截图详情和 PDF 跳转保持可用。
- 已知 Figure 9、Table 2、ODS-NiCoFeCr Table 1、SiC Table 1/Table 7
  回归路径继续由稳定资产覆盖。
- 模拟 MinerU 测试覆盖断网、超时式失败、损坏/异常候选隔离、表格结构、
  递归曲线点删除、逐资产采用和全局质量门。
- Python 自动测试：164/164 通过；前端 JavaScript 语法和 Python 编译通过。

## 当前云端状态

本机尚未配置 MinerU Token，因此尚未上传十篇论文，也没有把任何真实云端
候选标为通过。对比报告当前为 `awaiting_mineru_runs`。这属于可预期状态：
稳定功能可以正常交付，云端增强保持实验性影子模式，默认数据源不变。

配置入口：双击 `/Users/USER/Zotero/配置MinerU云端解析.command`。
配置后应先只运行固定十篇并完成对照校验，不得直接扩展到 30 篇。

## 启动与恢复

- 本地编辑端：双击 `/Users/USER/Zotero/打开本地编辑工作台.command`。
- 只读公网链接：双击 `/Users/USER/Zotero/创建导师公网链接.command`。
- 云端改造前标签：`evidence-demo-2026-07-15-pre-mineru-baseline`。
- 本版标签：`evidence-demo-2026-07-15-cloud-shadow-1`。
- 本版 Git bundle：
  `/Users/USER/Zotero/auto-research-backups/auto-research-cloud-shadow-2026-07-15.bundle`。

## 科学与隐私边界

- “程序稳定”不等于 DeepSeek/MinerU 候选已经获得物理学人工认可。
- DeepSeek 负责科学语义，不单独决定覆盖稳定截图；人工最终决定保留版本。
- MinerU 会接收整篇 PDF。仅应上传有权使用且允许交由第三方云服务处理的文献。
- 任何网络或云端失败均应回退到稳定资产，不得阻断本地搜索和校对。
