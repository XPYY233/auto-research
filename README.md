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

## 安装与首次使用（Apple Silicon Mac）

当前提供**从源码构建并安装的候选版**，没有可直接下载的正式 DMG。完成下面步骤后，你会得到可双击打开的 `Auto Research.app`，无需维护者的资料库，也不需要每次用终端启动。原创代码允许按本说明下载、构建、安装和本机自用；其他权利保留，见 [版权声明](COPYRIGHT.md)。

### 1. 准备工具（只需一次）

- 在“ → 关于本机”确认芯片为 Apple M 系列。当前完整实测环境为 macOS 26.3.1；Intel Mac、Windows 及其他 macOS 版本尚未完成交付验收。
- 安装 [Python 3.14.3 官方 macOS 安装包](https://www.python.org/downloads/release/python-3143/)（页面中的 **macOS installer**）。构建器固定检查 **3.14.3**，不是任意 3.14；这是项目当前锁定版本，并非 Python 最新版。
- 打开 macOS 自带的“终端”，输入以下命令安装 [Apple Command Line Tools](https://developer.apple.com/documentation/xcode/installing-the-command-line-tools)，按系统提示完成。若提示已经安装，可直接继续。

```sh
xcode-select --install
```

重新打开终端，检查输出：`arm64`、`Python 3.14.3`，以及 Git 版本号。

```sh
uname -m
python3.14 --version
git --version
```

首次构建需要联网下载依赖，并预留数 GB 磁盘空间。仅安装应用不需要 Node、Zotero、AI 密钥或开发测试环境。

### 2. 下载源码并生成 App

在终端依次运行以下命令。若已经存在同名目录，请先换一个空目录，不要覆盖旧项目。

```sh
mkdir -p "$HOME/Developer"
cd "$HOME/Developer"
git clone https://github.com/XPYY233/auto-research.git
cd auto-research
AUTO_RESEARCH_DESKTOP_PYTHON="$(command -v python3.14)" desktop/macos/build_app.command
```

等待终端显示“构建完成”。脚本会建立新的构建环境、按固定哈希安装依赖、生成 App，并在空工作区检查它能否运行；中途报错就不要继续安装。脚本不会替换电脑上已有的 Auto Research。若最后提示“按任意键关闭窗口”，先按一个键，再执行下一步。

请使用 `git clone`，不要用 GitHub 的“Download ZIP”：构建需要 Git 历史记录版本身份。不要把论文、API 配置或数据库放进源码目录。

### 3. 安装并打开

仍在刚才的 `auto-research` 目录中运行，Finder 会选中刚生成的**实际 App 文件**：

```sh
open -R "$(python3.14 -c 'from pathlib import Path; print(Path("desktop/macos/dist/Auto Research.app").resolve(strict=True))')"
```

1. 在 Finder 中复制选中的 `Auto Research.app`，粘贴到“应用程序”。不要只复制替身；上面的命令已经定位到实际文件。
2. 如果已有同名 App，先退出旧版并保留旧 App 的副本，再替换。首次安装不会遇到这一步。
3. 在“应用程序”中双击 Auto Research。之后都从这里打开，源码文件夹不必保持打开。

本机构建采用 ad-hoc 签名，未经 Apple 公证。如果系统提示开发者无法验证，只在确认是自己刚构建的 App 后，按 [Apple 官方步骤](https://support.apple.com/en-us/102445)在“系统设置 → 隐私与安全”对该 App 单独选择“仍要打开”。无需关闭 Gatekeeper；若提示文件损坏或恶意软件，应停止并保留错误信息。

### 4. 第一次使用

- 新用户会看到空资料库，这是正常状态。程序自动创建 `~/Library/Application Support/Auto Research/workspace`；数据库、论文和图片在此保存，私人设置另存于同一 Application Support 应用目录。无需连接维护者电脑或 Zotero。
- 打开“设置 → AI 与 API 密钥”，填写**你自己的**提供商配置，完成连接与功能验证。密钥不要写入终端、源码或 GitHub。未配置 AI 时可以打开软件，AI 提取与问答需要可用配置，调用费用由提供商收取。
- 在“文献”导入一篇自己有权处理的 PDF，完成预检，再按界面确认发送内容与调用预算后启动提取。AI 自动核验通过的证据自动发布；失败项显示原因并可重试，没有人工审核队列。
- 提取结束后检查论文详情、数值/结论/表格/图片及原文来源。已有正文但缺少图表时可使用“AI 补全图表”；没有表格的论文不会因此生成表格。

### 更新、回退与卸载

更新前退出 App，并备份 `~/Library/Application Support/Auto Research`；这是本机私人数据，**不要上传到 GitHub**。在源码目录运行 `git pull --ff-only`，再重复第 2 步的构建命令和第 3 步的安装操作。保留此前的 App 及对应数据备份；若新版数据格式发生变化，不能只替换旧 App，需按该版本迁移说明恢复匹配备份。完整跨机器升级/回退验收仍在进行。

卸载时退出 App，仅将“应用程序”里的 Auto Research 移到废纸篓；此操作保留 Application Support 中的资料与设置。重新安装后可继续使用。不要把删除数据目录当作常规卸载或排错步骤。

### 常见安装问题

| 现象 | 处理 |
| --- | --- |
| 找不到 `python3.14`，或版本不是 3.14.3 | 完成第 1 步，重新打开终端。官方安装路径通常是 `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14`；先用该路径检查版本，再把它作为 `AUTO_RESEARCH_DESKTOP_PYTHON` 的值 |
| Git / 编译工具不存在 | 完成 Command Line Tools 安装，再运行 `git --version` |
| 下载失败、超时或证书验证失败 | 检查网络和系统时间；使用 python.org 安装版时可运行“应用程序/Python 3.14/Install Certificates.command”。不要关闭 TLS 或哈希校验，修复后重试构建 |
| 提示源码有未提交改动 | 构建拒绝混合版本；在新目录重新克隆干净源码，保留原目录，不用强制清理命令丢弃文件 |
| 提示发布资源不一致或依赖哈希错误 | 停止安装，提交不含私人数据的错误信息；不要自行改哈希或跳过检查 |
| 启动提示已保存的数据目录不可用 | 恢复原目录/外部磁盘后重试，不要删数据库或随意切换到空库；这通常与旧安装的目录设置有关 |
| 软件打开后没有论文或图表 | 新库本来为空；先导入 PDF，再配置 AI 完成提取。若提取失败，保留界面错误码到 [Issues](https://github.com/XPYY233/auto-research/issues)，不要附论文、资料包或密钥 |

上述构建、独立目录运行与空库重装已在本机验证，范围见 [安装验收记录](docs/README_INSTALL_ACCEPTANCE.md)。

## 开发者检查

只想安装使用的读者可跳过这一节。开发需要 Python 3.14.3 与 Node 24.15.0；[开发说明](CONTRIBUTING.md)提供完整流程。

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
.venv/bin/python scripts/check.py
```

统一检查使用合成数据，在两个独立进程中串行执行共享与 Mac 测试，无需私人资料或 AI 密钥，不调用收费模型。

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

原创源码允许按上述说明下载、构建、安装和本机自用；其余权利保留，尚未授予开源许可或一般改编、再分发许可。具体范围见 [COPYRIGHT.md](COPYRIGHT.md)。

第三方组件保留各自版权与许可，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。PyMuPDF/MuPDF 提供 AGPL 与商业许可选项；公开源码并不自动完成二进制分发合规。论文正文、出版图表和用户数据的权利属于相应权利人，不由本仓库授权。
