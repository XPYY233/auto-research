# 并行开发保护说明

## 本阶段所有权

本对话负责：

- `desktop/macos/**`
- 桌面产品化记忆更新
- 后续在并行功能工作完成后追加的桌面交接记录

本对话明确不拥有：

- `src/auto_research/evidence/agent_runtime.py`
- `src/auto_research/evidence/librarian_reasoning.py`
- 另一个 Codex 对话正在修改的搜索、推理展示或前端功能
- 生产 SQLite、PDF、图表和抽取结果

## 集成方式

桌面应用从唯一 `src/` 核心构建，不复制核心文件。因此并行功能完成后只需要：测试核心 → 提交核心 → 运行安全更新入口 → 验证桌面应用。

如果工作树仍有未知修改，安全更新必须停止。不要用 `git reset`、覆盖复制或“先打包再说”来消除提示。
