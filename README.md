# Auto Research

**把论文中的数值、结论、表格和图片整理成可追溯的本地证据库。**

Auto Research 是面向科研工作的桌面应用，当前开发重点为 Apple Silicon Mac。它把 PDF 导入、AI 提取与交叉核验、证据检索、原文定位和数据导出放在一个工作台中，让每条结果都能回到来源论文。

> 当前处于工程重建与候选版验收阶段。公开仓库提供源码和开发记录，不提供维护者的文献资料包、研究数据库或 AI API 密钥。尚未宣布稳定发行。

## 能做什么

| 能力 | 说明 |
| --- | --- |
| 四类证据 | 数值条目 `item`、定性结论 `finding`、表格 `table`、论文图片 `figure` |
| 来源追溯 | 保留文章身份、PDF 页码、原文片段、单位和版本；原图来自本地 PDF |
| 自动 AI 核验 | 两路提取/核验及必要的第三路核验，通过后自动发布；未通过的结果隔离并保留原因，没有人工审核队列 |
| 图表补全 | 已提取论文可单独补全图表，复用正文结果与任务恢复机制 |
| 本地检索 | 按论文、材料、条件和证据类型检索，并打开详情及来源 |
| 有界 AI 问答 | 围绕选中证据或检索结果回答，要求来源引用，不能改写科研记录 |
| 私人数据与导出 | 支持私人表格、数据导出和可验证资料包；使用者自行管理文件与授权 |
| 可恢复任务 | 保存提取进度、调用回执和发布状态，支持取消与恢复 |

原图、结构化表格和已核验数值是不同结果。程序不会从曲线像素臆造数值；AI 核验一致也不等于科学结论已经正确。

## 当前可用范围

- 已实测：Apple Silicon、macOS 26.3.1；开发与构建使用 Python 3.14.3、Node 24.15.0。
- 本机候选：1.2.0 / build57。已有自动检查、独立空工作区冒烟和本机使用记录，见 [验收报告](docs/CANDIDATE_57_ACCEPTANCE.md)。
- 尚待完成：第二台干净 Mac、所有保留功能联合验收、完整分发许可核对和独立科学金标准。
- Windows 代码保留作历史兼容，当前暂停开发和交付。

公开迁移重写了提交身份；旧候选的源码哈希仍属于原始私有历史。不要把旧 App 宣称为从净化后的哈希构建，详见 [公开迁移说明](docs/PUBLIC_REPOSITORY.md)。

## 从源码开始

```sh
git clone https://github.com/XPYY233/auto-research.git
cd auto-research
python3.14 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
.venv/bin/python scripts/check.py
```

统一检查使用合成数据，在两个独立进程中串行执行共享与 Mac 测试。无需维护者资料库或 API 密钥，不调用收费模型。完整说明见 [CONTRIBUTING.md](CONTRIBUTING.md)。

在干净、已提交的 Apple Silicon 检出目录中构建候选 App：

```sh
AUTO_RESEARCH_DESKTOP_PYTHON="$PWD/.venv/bin/python" desktop/macos/build_app.command
```

构建器创建独立环境并校验依赖哈希，生成唯一候选，不自动替换已安装 App。首次运行初始化本机空工作区；使用者自行导入有权使用的 PDF。候选为 ad-hoc 签名、未经 Apple 公证，不能据此宣称已完成组内发行。

## 隐私与 AI

- 文献资料包、PDF、科研图片、数据库、提取产物、私人表格和备份均不随源码发布。
- 每位使用者自行配置 AI 提供商及 API 密钥；密钥存于本机受保护的凭据存储，不写进源码或安装包。
- AI 功能会在授权后把所需原文片段或证据发送给所选提供商。请先核对自己的文献使用权及提供商规则；本地存储不意味着 AI 请求完全离线。
- 不要将 API 密钥、密文凭据、资料包或含研究内容的截图附到 Issue、PR、Actions 日志或 Release。只提交合成复现样例。
- [隐私与披露规则](SECURITY.md)说明禁传范围和提交前检查。`.gitignore` 不是安全审计的替代。

## 开发与项目状态

采用 **Issue → 短期分支 → PR → 检查 → 合并 → 唯一候选 → 实机验收 → Release**。

- [当前进度](PROJECT_HANDOFF.md)
- [架构与模块约束](docs/ARCHITECTURE_GOVERNANCE.md)
- [功能验收矩阵](docs/FUNCTION_ACCEPTANCE.md)
- [发布边界](STABLE_RELEASE.md)
- [历史迁移与提交对照](docs/PUBLIC_REPOSITORY.md)

常规源码改动不生成 App；文档变更不重复触发完整 macOS 检查。公开 CI 不接收任何维护者的 AI 密钥或科研资料。

## 版权与第三方组件

Copyright © 2026 XPYY233 and contributors，限各自原创贡献。

当前原创源码保留权利；公开可见不等于授予任意使用、修改或再分发许可。具体范围见 [COPYRIGHT.md](COPYRIGHT.md)。维护者已选择暂时保留权利；待另行确定开源许可后再开放修改和再分发。

第三方组件保留各自版权与许可，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。PyMuPDF/MuPDF 提供 AGPL 与商业许可选项；公开源码并不自动完成二进制分发合规。论文正文、出版图表和用户数据的权利属于相应权利人，不由本仓库授权。
