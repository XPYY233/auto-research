# 保留功能与验收矩阵

2026-09-19。关联 #2–#5、#17。所有列均以证据为准；源码存在和自动回归通过不等于安装版或科学验收完成。

工程结果以各 PR 的 GitHub Actions 为准；安装候选固定为 [build55 的唯一二进制](CANDIDATE_55_ACCEPTANCE.md)，后续源码不自动生成安装版。

| 保留功能 | 源码入口 / 自动行为覆盖 | 安装版或当前真实数据证据 | 尚需取得 |
|---|---|---|---|
| 首次启动与空库 | workspace、desktop_runtime；隔离用户目录/真实 HTTP 回归 | 冻结候选空库初始化与自包含冒烟通过 | 第二台无开发环境 Mac |
| 旧库迁移与恢复 | workspace_migration、workspace_switch；哈希/记录/锁/进程中断/回退回归 | 实际切换并重启通过：21 表、550 引用、388 份私人/官方文件核验；27 个旧哈希类型校正留档 | 第二台 Mac 和真实升级/回退联合场景 |
| PDF 导入与预检 | uploads、LiteratureImportController；真实 PDF、重复/非法文件、授权限制 | 历史新文章已进入现用库；新 facade 源码回归通过 | 新候选上的完整导入联合操作 |
| 提取与表图发现 | literature_extraction_*、visual_evidence；阶段/长图注/合并段落/结构化结果回归 | 当前新文章 5 张图已保存且哈希一致；主 PDF 未发现独立表格 | 20+1 人工原文对照与提取联合验收 |
| 审核、原子发布与索引 | review_queue、review_queue_contract、literature_extraction_finalizer、search_index；真实 DTO、数据库/HTTP 回归 | 安装版队列 160 项；新文章 Figure 1/5 实际显示，5 张均经服务核验 | 人工审核保存、发布、目录更新的完整实机流程 |
| 检索、详情、原图、PDF 定位 | search_index、workspace_evidence_resolver、source_highlight；公开接口回归 | 迁移后 64 论文/65 文档/309 图像引用哈希正确；安装版已打开新文章 PDF | 安装版逐类详情、定位与重启联合操作 |
| 取消、续跑与避免重复收费 | literature_extraction_recovery*、literature_task_*；中断/收据/未知结果回归 | 四份复制的加密断点认证通过：3 completed、1 cancelled | 真实网络故障场景；不能用测试替代提供商账单核对 |
| 私人表格、确认与曲线 | personal/*、fusion_personal_*；格式解析、确认门、系列曲线回归 | 本机私人状态已备份及恢复验证 | 安装版真实文件导入、确认、搜索、曲线和重启 |
| 资料包与真实导出 | package_center、transfer_package、dataset_export、evidence_export；往返/攻击/文件格式回归 | 不可变官方包与私人状态保全 | 安装版导出到真实目的文件、隔离往返和回退 |
| 证据 AI 与图书管理员 | Harness 组合、受控业务授权、引用验证；离线注入回归 | 加密对话/操作历史恢复验证；本轮未调用收费模型 | 授权后的真实模型质量和引用人工核验 |
| 跨标签、键盘、布局与保存反馈 | DocumentTabStore、布局控制器、Fusion 行为回归 | 已修复迟到响应/审核失效/键盘与拖拽相关源码回归 | 固定候选上完整交互与重启验收；替换旧片段拼接测试 |

已知限制：6 份旧历史输出文件原本缺失，迁移如实登记；没有补造输出。AGPL/其他分发材料、Developer ID/公证与第二台 Mac 状态见 #4，未完成不能正式组内交付。

科学验收仍固定：20 篇人工金标准＋至少 1 篇未参与调试的论文，覆盖率 100%，适用维度 F1 ≥ 0.90，无无来源数值或错配原图。不能通过降低门槛或模型自评关闭 #5。
