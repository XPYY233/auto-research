# 项目维护与稳定发布流程

本流程用于普通开发检查点、导师展示前检查和后续 GitHub 发布准备。所有命令均在 `/Users/USER/Zotero/auto-research` 执行。

## 1. 冻结运行状态

- 停止正在运行的全文抽取、质量批次、本地编辑服务和 ngrok 分享。
- 确认没有 SQLite WAL/SHM 写入活动。
- 先复制 `db/experimental_evidence.sqlite` 到仓库外的备份目录。

## 2. 收口中断任务

```bash
PYTHONPATH=src python3 -m auto_research.cli evidence-reconcile-runs --older-than-hours 6
```

该命令只能处理运行审计元数据：结束遗留的运行中任务并清除成功记录里的旧错误文本。它不得改变六列数据、图表、原文证据、校对版本或 DeepSeek 输出文件。

## 3. 数据库和功能验收

```bash
PYTHONPATH=src python3 -m auto_research.cli evidence-db-health
PYTHONPATH=src python3 -m auto_research.cli evidence-self-check \
  10.1016/j.jnucmat.2018.08.031 \
  --query 温度 --query 硬度 --query 钨 --query 'Wei-Ying Chen' \
  --min-rows 100 --min-highlight-ratio 0.8
PYTHONPATH=src python3 -m auto_research.cli evidence-test-set-audit \
  --config config/evidence_test_set_50.json
```

固定语料审计非零退出并不一定代表程序损坏；必须查看是 PDF/身份失败、数据尚未抽取，还是搜索/证据链回归。未完成论文应明确标为 `pending_extraction`，不得生成虚假数据使审计变绿。

## 4. 代码与启动检查

- 运行 `src/tests` 全部测试。
- 编译全部 Python 文件并检查 `src/auto_research/evidence/web/app.js` 语法。
- 检查所有 `.command` 的 zsh 语法、Python 依赖和 Git 对象完整性。
- 启动编辑端和只读端，实测数据搜索、图表搜索与原文证据。
- 对只读端发送一个写入请求，必须返回 403；操作前后 SQLite SHA-256 必须相同。

## 5. 文档与版本一致性

- 更新 `RELEASE_INFO`、`STABLE_RELEASE.md`、`TEACHER_DEMO.md`、`PROJECT_LOG.md` 和阶段审计。
- 只有长期有效的架构边界和操作规则写入 `AGENT.md`；一次性错误输出只写项目日志。
- 对照初始目标分别报告“产品功能”“语料完成度”“独立科学准确率”，不得把单篇成功等同于全项目完成。

## 6. 提交和双重备份

1. 确认差异中没有密钥、`.env`、PDF 全文或无关个人文件。
2. 提交代码、文档、数据库和本次可审计产物。
3. 创建带日期的稳定标签。
4. 在仓库外复制最终 SQLite，并记录 SHA-256。
5. 创建包含所有分支和标签的 Git bundle，执行 `git bundle verify`。
6. 最终 `git status --short` 应为空。

## 7. GitHub 发布边界

- 不把 DeepSeek/ngrok 凭据、Zotero 数据库、原始 PDF、上传临时文件或本地绝对路径公开。
- 私有 GitHub 仓库可保存完整开发历史，但仍建议把凭据和 PDF 排除；当前 Git 历史包含约 149 MB 对象，推送前需要单独检查大文件和版权边界。
- 公共仓库建议只发布代码、示例配置、脱敏小型演示数据库和自制截图；真实论文截图、原文片段和完整生产数据库应留在私有环境。
- GitHub Pages 只能承载静态只读快照，不能运行本项目的 Python 后端、SQLite 写入、PDF 上传或 DeepSeek 对话。
