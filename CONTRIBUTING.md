# 开发流程

当前状态见 PROJECT_HANDOFF.md。问题 → 短期分支 → PR → 自动检查 → 合并。
主维护者统一集成；不提交用户数据库、论文、研究图片、私人路径、凭据或构建产物。

## 干净 Mac 开发环境

当前验证目标：Apple Silicon、Python 3.14.3、Node 24.15.0。

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
.venv/bin/python scripts/check.py
```

统一检查在两个独立进程中串行运行共享 pytest 与 Mac pytest，防止同名测试模块相互污染。
必需依赖缺失及意外 skip 均失败。使用临时用户目录和合成样本；禁止外部网络和收费模型。
检查覆盖前端行为、发布资源哈希、零静态循环依赖、逐文件职责和敏感文件。
安全依赖检查另外运行 `.venv/bin/python -m pip_audit --strict -r requirements-dev.lock`。
工程与 App 构建分别使用独立哈希锁；App 候选构建身份规则见 docs/decisions/0003-candidate-identity.md。

私人历史语料的 81 项测试显式标为 corpus，默认不选中；不算工程检查通过或跳过。
持有相应合法样本的维护者可以用 `python -m pytest -m corpus src/tests/test_evidence.py` 单独评估；
缺少样本必须失败。它们不是新计划中尚待人工建立的 20+1 科学金标准。

## 评审与发布

先在 GitHub 建立或关联 Issue，再建短期分支和 PR；禁止将长期本地分支作为交付版本。每个 PR 说明问题编号、行为变化、测试、兼容与回退。检查通过后合并；Issue 仅在其完成标准取得证据后关闭。依赖与 Actions 更新经过 PR，禁止自动合并。
公开迁移后继续采用 PR 流程；历史私有仓库及其检查记录保持私有。公开历史与隐私规则见 docs/PUBLIC_REPOSITORY.md 和 SECURITY.md。只在 PR 跑一次完整工程检查，合并后不重复跑同一套检查；文档变更不触发完整 Mac 检查，不自动购买额度。
常规源码提交不构建 App；候选二进制独立编号且不覆盖。候选验收后封装同一制品。
发布必须附源码、依赖和许可清单、构建环境、测试/实机验收记录及哈希。
macOS 支持范围以实测为准；Windows 本轮冻结；无第二台干净 Mac 验收不得称为可交付。

公开提交必须使用 GitHub noreply 邮箱，提交前检查 `git log -1 --format=fuller`。维护者本地已配置 noreply；在确认 GitHub 网页合并使用 noreply 前，由维护者在本地以 noreply 身份合并已审查 PR，历史扫描通过后再推送。不得让网页合并重新写入私人邮箱。
