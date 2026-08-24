# Auto Research macOS v1.1.0

这是 Auto Research 面向课题组本机使用的 macOS 稳定版。核心、`.aresearch` 资料包、私人库和凭据接口保持跨平台，但当前只发布 Apple Silicon Mac 版。用户唯一入口是 App，不再使用浏览器工作台、导师只读或 ngrok。

## 当前阶段

- 当前稳定身份：`1.1.0`（bundle `1.1.0`，build `24`）
- 当前源码候选：`1.1.0` / build `41`；真实App与付费AI验收完成前不得称为稳定版
- 界面：继续使用项目唯一的 `src/auto_research/evidence/web/` 前端
- 核心：继续使用项目现有 Python、SQLite 和 PyMuPDF；图书管理员与选中证据问答统一由锁定的 DeepSeek Harness 运行时执行
- 数据：v12 本地文献工作区继续可编辑；官方 `.aresearch` 和本机私人实验库作为相互隔离的只读检索来源，不复制回 v12
- 分发：ad-hoc 签名，未使用 Developer ID，未经 Apple 公证，不是 App Store 发行版
- Windows：1.1迁移冻结；本Mac套件不包含Windows文件，既有Windows 1.0本机构建经验单独保留

桌面壳不复制第二套搜索、Agent 或证据逻辑。应用、科学数据工作区、官方资料包和每位用户的私人库保持相互分离。

当前版本包含 Fusion 科研工作台、多标签和双编辑器组、四类服务端筛选、DeepSeek Harness 图书管理员、原文临时高亮与返回链、原生资料包导入与回退、官方/私人/全部联合精确搜索、个人 CSV/TSV/XLSX 导入确认门、分阶段文献提取、`dataset-bundle-v1` 数据集导出与 BYOK 设置。图书管理员只查官方资料和用户已发布文献，不读取私人实验。

源码中的 `version.json` 和发布契约已更新为 `1.1.0` / build `41` 候选。build 40 已恢复四项 AI 的阶段进度和连续对话；build 41 进一步把收费执行改为会话绑定的后台任务，界面实时显示 Harness 版本核验、模型请求、只读证据工具、引用核验、结果校验与耗时，而不是静态三点或伪造的思考动画。活动记录不会展示模型内部思维链、提示词、工具参数、PDF正文、路径或密钥。图书管理员回答右侧提供独立、可收起的证据与推荐论文结果栏；点击会保留对话并在第二编辑组打开真实证据，同时把右侧证据 AI 绑定到该实体。图书管理员与证据问答仍经过 prepared action、逐次授权、预算和只读 Harness 工具边界。它必须重新通过真实 App 的四项付费 AI 验收，才可替代 build 24 稳定回退点。App、DMG、签名和实机验收结果必须来自同一干净提交，不沿用旧构建的测试数或哈希。Fusion上下文栏、两个编辑器组和检查器可拖至边缘吸附收起，也可用键盘、按钮或命令面板恢复；拖拽过程中不重建编辑器，损坏布局会回到至少一个可见编辑组。宽屏单击证据会自然地在右侧打开预览。

设置页的“AI 与 API”分别显示提供商连接、Harness和四项业务能力。内置DeepSeek/OpenAI，也允许高级用户配置一个通过本地安全门的公开HTTPS OpenAI-compatible服务。密钥只进安全存储且永不回显；保存后先做一次低成本连接验证，具体业务首次使用时再单独验证能力。

## 文件入口

| 想做什么 | 使用哪个入口 |
|---|---|
| 第一次生成应用 | 双击 `build_app.command` |
| 查看应用来源 | 双击 `查看当前桌面版本.command` |
| 检查已生成应用 | 双击 `verify_app.command` |
| 稳定提交后安全更新 | 双击 `更新桌面版.command` |
| 生成 macOS DMG | 双击 `make_dmg.command` |
| 学习迭代规范 | 阅读 `NON_ENGINEER_UPDATE_GUIDE.md` |
| 查看安全边界 | 阅读 `SECURITY.md` |
| 查看数据包路线 | 阅读 `../PRODUCT_DATA_PACKAGE_PLAN.md` |

生成物位于 `desktop/macos/dist/Auto Research.app` 和 `desktop/macos/dist/Auto-Research-1.1.0-macOS-arm64.dmg`，上一版保存在 `desktop/macos/releases/`。二者都不进入 Git，应用更新与科学数据更新分离。

## 首次打开

本版使用 ad-hoc 签名且未经 Apple 公证。如 macOS 首次拦截，请在访达中按住 Control 点击 `Auto Research.app`，选择“打开”，然后在系统对话框中再次确认。如仍被拦截，请打开“系统设置 → 隐私与安全性”，对 Auto Research 选择“仍要打开”。

这个打开方式不等于 Developer ID 签名、Apple 公证或 App Store 审核。安装前应当从课题组受信渠道获取 DMG，并核对随发布验收记录提供的 SHA-256。

## 发行边界

- 不把生产 SQLite、PDF、截图和模型历史烘焙进 `.app`。
- 不声称另一台电脑已经能无迁移使用当前生产库。
- 当前只发布 Apple Silicon Mac 版；Windows 1.0 安装线保持冻结，不同步 1.1，Linux 没有安装包。
- DeepSeek Harness 依赖是开发预览版本，发行版核验精确 SDK/runtime/二进制哈希；不匹配时 AI 明确不可用且不回退旧循环。
- 软件稳定、资料包完整和科学人工准确率是三个不同结论；1.1 不把自动测试或模型一致性写成科学审核完成。
- 不做自动更新服务器、App Store 或公网部署。
- 不把 ad-hoc 签名描述为 Developer ID 签名或 Apple 公证。

## 开发验证

```bash
python3 -m unittest discover -s desktop/macos/tests -p 'test_*.py'
PYTHONPATH=src python3 desktop/macos/launcher.py --smoke-test
```

构建器要求工作树干净。并行对话仍在编辑时会停止，避免把半成品装进 App。
