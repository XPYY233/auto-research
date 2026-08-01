# macOS 开发预览架构与跨平台产品边界

## 一条核心原则

当前 Mac App 是桌面产品的开发外壳，不是第五套证据模型，也不是第二套网页。本阶段只做稳定 macOS 演示版；仅在接口上保留未来 Windows 迁移能力。

```text
Auto Research.app
    ├── pywebview / WKWebView 原生窗口
    ├── 随机 127.0.0.1 本地端口与每次启动会话令牌
    ├── 已冻结的当前 Python 核心
    └── 当前 Mac 上的外部数据工作区
            ├── db/experimental_evidence.sqlite
            ├── data/evidence/visual_assets
            ├── Zotero / uploaded PDFs
            └── config
```

`launcher.py` 在任何 evidence 模块导入之前，将核心路径指向外部项目工作区。这样数据库、PDF 和图表不会被放入只读 `.app` 包，也不会在更新应用时被覆盖。

构建与复验不会直接对生产库启动服务。验证器先用 SQLite 在线备份建立临时工作区，再让冻结应用对临时库完成 HTTP、搜索、图表、原文和 PDF 检查；因此即使现有 8765 编辑服务仍在运行，也不会把它的合法并行写入误判为桌面应用改库。

## 三个生命周期

### 1. 科学数据生命周期

SQLite、PDF、图表、原文定位、校对历史和抽取产物属于用户数据。它们不跟随应用覆盖更新。任何未来迁移必须先备份、校验 schema，再显式执行。

### 2. 核心功能生命周期

`src/auto_research/` 是唯一核心。搜索、Librarian、质量门和前端优化完成测试与提交后，通过重新构建进入新的 `.app`。桌面目录不复制这些文件。

### 3. 桌面壳生命周期

`desktop/macos/` 只负责项目定位、原生窗口、内部服务生命周期、构建、验证、版本与回滚入口。

### 4. 私人对话生命周期

图书管理员历史不进入科学 SQLite，也不依赖 pywebview 的持久化浏览器缓存。当前 ad-hoc 开发预览使用 `0600` 私有随机密钥，将会话认证加密后保存到 `~/Library/Application Support/Auto Research/Private Data/`；它不会读取旧 Keychain 项，因此重建后不向用户索要钥匙串密码。正式签名发行切换到统一的 OS secure credential store 抽象：macOS Keychain，未来 Windows 为 Credential Manager。

### 4.1 外部 AI 凭据生命周期

DeepSeek API 密钥属于桌面私有状态，不属于科学数据或网页状态。桌面本机服务提供受会话保护的状态、保存和删除端点；浏览器只能知道“是否已配置”和存储类型。当前 ad-hoc 预览使用独立本地 AES-256-GCM 存储，稳定签名版切换到新的 macOS Keychain service。两种后端共用相同错误码与 API，不要求共享核心或未来 Windows 壳复制前端业务逻辑。

### 5. 跨平台资料生命周期

正式产品把官方只读资料包、用户私人库和 App 状态物理分开。`.aresearch` 只使用包内相对资源身份、版本、哈希与签名，不携带开发机绝对路径、API 密钥或未授权全文。本阶段只冻结契约，不增加 Windows 构建或依赖。

## 第一阶段路径策略

查找顺序：

1. 启动参数 `--project-root`；
2. 环境变量 `AUTO_RESEARCH_DESKTOP_PROJECT_ROOT`；
3. `~/Library/Application Support/Auto Research/project-root.txt`；
4. 开发工作树；
5. 当前 Mac 默认位置 `~/Zotero/auto-research`。

找到目录后必须存在生产证据库、`data/evidence` 和 `config`。找不到时只显示错误说明，不创建空数据库、不写示例数据、不猜测新位置。

## 已知开发预览限制

- 仍依赖当前项目工作区；移动整个项目后需要显式指定新位置。
- 数据库中的论文 PDF 仍大多是本机绝对路径。
- 桌面层通过 HttpOnly、SameSite=Strict 的每次启动随机 Cookie 保护本地接口；公开发布前仍需独立安全审计。
- 启动前要求 SQLite 完整性通过且 schema 精确等于 v12；预览版不自动猜测或执行跨 schema 迁移。
- macOS 文件锁阻止两个编辑实例同时操作同一数据库。
- 启动和安全更新都会检查旧 8765 浏览器编辑服务；检测到它时 fail closed，不自动终止另一个对话的进程。
- 关闭原生窗口会显式停止内部 HTTP 服务；强制结束进程仍可能留下未完成的外部 DeepSeek 请求审计，需要沿用项目运行状态收口机制。
- pywebview 默认禁止下载，桌面层显式允许项目现有 CSV/XLSX/JSONL/Markdown 导出。
- `target=_blank` 默认会交给无桌面会话 Cookie 的 Safari，因此当前预览将这些链接留在 WKWebView；这只是 Mac 实现，不进入跨平台核心契约。
- 当前构建是 arm64，不包含 Intel Mac。
- 当前仅使用临时 ad-hoc 签名，不能面向普通用户正式分发。

## 下一阶段入口

下一阶段首先实现 `.aresearch` 导入、用户私人库、无密钥离线搜索和 BYOK 引导。Windows 壳留到 Mac 稳定演示与数据契约冻结之后；不要通过复制生产数据库或重写绝对路径来伪造“可迁移”。
