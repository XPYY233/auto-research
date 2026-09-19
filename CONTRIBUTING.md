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
锁文件目前用于工程检查，不证明 App 构建已锁定或 Harness 二进制可用。

私人历史语料的 81 项测试显式标为 corpus，默认不选中；不算工程检查通过或跳过。
持有相应合法样本的维护者可以用 `python -m pytest -m corpus src/tests/test_evidence.py` 单独评估；
缺少样本必须失败。它们不是新计划中尚待人工建立的 20+1 科学金标准。

## 评审与发布

每个 PR 说明问题编号、行为变化、测试、兼容与回退。依赖与 Actions 更新经过 PR，禁止自动合并。
2026-09-19 私有仓库分支保护 API 返回 403（需 Pro）；当前采用受控 PR 合并，尚无服务端强制保护。不自动购买订阅或公开仓库。
常规源码提交不构建 App；候选二进制独立编号且不覆盖。候选验收后封装同一制品。
发布必须附源码、依赖和许可清单、构建环境、测试/实机验收记录及哈希。
macOS 支持范围以实测为准；Windows 本轮冻结；无第二台干净 Mac 验收不得称为可交付。
