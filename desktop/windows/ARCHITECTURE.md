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

## 当前阻塞

本目录目前只建立平台安全地基，不伪造可用安装包。启动器必须等待共享核心冻结 distribution schema v1、stable source/entity identity、official/private federated read、资料包激活/回退 API 和私人导入 API 后再接入。
