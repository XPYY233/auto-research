# Auto Research Windows 架构基线

Windows 是正式用户端，不是 macOS 预览的逐文件复制。Windows 壳继续复用唯一 Python 核心、SQLite/Search V2、四类证据和共享前端；平台代码只负责窗口、进程、目录、凭据、安装与升级。

## 数据隔离

默认根目录为 `%LOCALAPPDATA%\Auto Research`：

```text
Auto Research\
├── Repositories\Official   官方只读资料包激活结果
├── Repositories\Private    用户自己的实验与论文证据
├── Private Data\Uploads    用户合法上传的原始文件
├── Private Data\History    加密对话历史密文
├── Packages\Inbox          用户选择的原始 .aresearch 文件
├── Packages\Staging        尚未激活的校验暂存区
├── Packages\Rollback       上一版官方资料包恢复点
├── State                    非科学应用状态
├── Cache                    可删除缓存
└── Logs                     不含密钥、PDF 正文和完整模型输入的诊断日志
```

安装目录、官方资料包、私人库和应用状态属于不同更新域。卸载程序默认不得删除用户库；升级 App 不得覆盖资料包或私人库；资料包升级不得覆盖用户上传文件。

## 凭据边界

- DeepSeek BYOK 和本地历史 AES 密钥进入 Windows Credential Manager。
- SQLite、资料包、日志、浏览器存储和导出中都不保存 API key。
- 对话密文仍保存在 `Private Data\History`，Credential Manager 只保存32字节加密密钥。
- 无 key 时离线导包、搜索和证据浏览必须可用。

## 桌面壳方向

- Python 3.12 x64 + PyInstaller；
- pywebview + Microsoft Edge WebView2；
- 本地服务仅绑定随机 `127.0.0.1` 端口，并使用每次启动随机会话令牌；
- 每个数据工作区持有独立 Windows 命名互斥锁，阻止两个进程同时导包或写入私人库；
- Inno Setup 生成按用户安装包；
- Windows Authenticode 签名、干净机安装、升级/回退和卸载必须在正式候选中验收。

## 零环境配置发布门

- 安装包自带 Python 运行时和全部生产依赖，用户不安装 Python、Git、Node 或数据库工具；
- 用户不配置环境变量、不选择项目源码目录、不运行命令行；
- WebView2 缺失时由安装器检测并通过微软官方 Evergreen Bootstrapper 安装；
- 首次启动直接进入 `.aresearch` 资料包导入，导入后立即支持离线搜索；
- DeepSeek key 只在首次使用 AI 功能时配置，不是安装、导包或离线搜索前置条件。

候选程序必须携带 `bundled-runtime-manifest.json`，逐项记录内置 Python、标准库、SQLite、加密库、PyMuPDF、pywebview、WebView2 loader、业务核心和共享界面的相对路径、大小与 SHA-256。clean-machine harness 在空工作目录、空 `PATH` 和无项目环境变量的临时用户目录中复验首次启动契约。

App shell coordinator 只编排平台组件，不导入科学核心：准备数据目录、获取工作区互斥锁、创建一次性启动令牌、启动随机 loopback 端口、打开 Edge WebView2 窗口、关闭服务并释放锁。真实 server builder 等共享接口冻结后注入；`pywebview` 只在显示窗口时延迟载入。

## Windows 版本策略

- 正式首发与实机验收：受支持且已更新的 Windows 11 x64；
- Windows 10 22H2 x64（build 19045+）：兼容测试级别，必须显示系统已结束常规支持的提示；
- 更旧 Windows、32 位 Windows 与 Windows on ARM：首版拒绝安装；
- WebView2 是否存在另行检测，系统版本兼容不等于运行时已经安装。

## 资料包输入边界

文件选择、拖放和未来安装器注册的双击文件关联共用 `PackageInputBroker`。一次只接受一个本机普通 `.aresearch` 文件；目录、符号链接/重解析点、UNC 网络路径、Windows 设备路径、上级跳转和超长路径在 importer 之前拒绝。窗口只持有不透明句柄，错误与公开状态不包含用户路径；importer 请求受控路径时再次核验大小、设备和文件身份，防止选择后的替换攻击。当前不执行任何注册表变更。

## 当前阻塞

本目录目前只建立平台安全地基，不伪造可用安装包。启动器必须等待共享核心冻结 distribution schema v1、stable source/entity identity、official/private federated read、资料包激活/回退 API 和私人导入 API 后再接入。
