# Windows 客户端变更记录

## 0.1.0-dev.1 — 2026-08-01

- 建立 `%LOCALAPPDATA%\Auto Research` 平台目录契约；
- 物理分离官方资料包、私人库、上传文件、历史、缓存、日志、暂存和回退；
- 建立 Windows Credential Manager 原生适配边界；
- 为图书管理员历史 AES 密钥和 DeepSeek BYOK 使用不同凭据目标；
- 添加按数据工作区隔离的 Windows 原生单实例保护契约；
- 添加 loopback 假服务生命周期、崩溃标记与随机端口恢复；
- 添加默认关闭候选安装包输出的 Windows 构建门；
- 添加内置 Python/生产依赖的逐文件哈希清单与篡改检查；
- 添加无 Git、Node、系统 Python、项目环境变量或源码目录的干净机验收夹具；
- 添加可在 macOS 上运行的纯契约测试；
- 明确当前不是可交付安装包，等待共享 distribution 与 federated read 契约冻结。
