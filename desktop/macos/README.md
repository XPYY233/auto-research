# Auto Research macOS 开发预览

这是 Auto Research 桌面产品的 macOS 开发与验证外壳。正式用户端预计是 Windows；核心、`.aresearch` 资料包、私人库和凭据接口必须保持跨平台。用户唯一入口是 App，不再使用浏览器工作台、导师只读或 ngrok。

## 当前阶段

- 开发中版本：`0.4.0-preview.1`（尚未构建，等待联合发布门）
- 稳定回退版本：`0.3.0-preview.1`
- 当前构建：macOS arm64（Apple Silicon）开发预览
- 正式目标：Windows 桌面客户端
- 界面：继续使用项目唯一的 `src/auto_research/evidence/web/` 前端
- 核心：继续使用项目现有 Python、SQLite、PyMuPDF 和 DeepSeek 运行边界
- 数据：v12 开发工作区继续可编辑；官方 `.aresearch` 作为第二个独立只读来源安装，不复制回 v12
- 分发：本机开发预览，尚未 Developer ID 签名或 Apple 公证

桌面壳不复制第二套搜索、Agent 或证据逻辑。核心功能完成测试与提交后，通过一次受控重建进入 App。

`0.4.0-preview.1` 的界面与后端已经接入原生资料包选择、签名资料包导入、官方/本机来源切换、四类只读证据搜索和 BYOK 引导。当前仍是依赖本机 checkout 的开发候选；在完成一次联合构建与实机验收前，不应替换 `0.3.0-preview.1` 回退版。

## 文件入口

| 想做什么 | 使用哪个入口 |
|---|---|
| 第一次生成应用 | 双击 `build_app.command` |
| 查看应用来源 | 双击 `查看当前桌面版本.command` |
| 检查已生成应用 | 双击 `verify_app.command` |
| 稳定提交后安全更新 | 双击 `更新桌面版.command` |
| 生成本机测试 DMG | 双击 `make_dmg.command` |
| 学习迭代规范 | 阅读 `NON_ENGINEER_UPDATE_GUIDE.md` |
| 查看安全边界 | 阅读 `SECURITY.md` |
| 查看数据包路线 | 阅读 `../PRODUCT_DATA_PACKAGE_PLAN.md` |

生成物位于 `desktop/macos/dist/Auto Research.app`，上一版保存在 `desktop/macos/releases/`。二者都不进入 Git，应用更新与科学数据更新分离。

## 为什么当前预览不读取钥匙串历史

临时 ad-hoc 签名每次重建可能改变程序身份，导致 macOS 为旧历史密钥弹出“登录钥匙串密码”。`0.3.0-preview.1` 不读取也不删除旧 Keychain 项；历史仍由 AES-256-GCM 加密，但预览期随机密钥保存在 Application Support 私有目录并限制为当前账户读取。

这是预览期稳定性例外，不是正式安全方案。正式签名发行必须切回操作系统安全凭据库：macOS Keychain / Windows Credential Manager。

## 当前预览明确不做

- 不把生产 SQLite、PDF、截图和模型历史烘焙进 `.app`。
- 不声称另一台电脑已经能无迁移使用当前生产库。
- 本轮不生成 Windows/Linux 安装包，但不把 WKWebView、Keychain 或 Apple Silicon 写成永久产品边界。
- 不做自动更新服务器、App Store 或公网部署。
- 不把临时 ad-hoc 签名描述成正式发行身份。

## 开发验证

```bash
python3 -m unittest discover -s desktop/macos/tests -p 'test_*.py'
PYTHONPATH=src python3 desktop/macos/launcher.py --smoke-test
```

构建器要求工作树干净。并行对话仍在编辑时会停止，避免把半成品装进 App。
