# Auto Research Windows 客户端

这里是 Windows 正式用户端的平台适配目录。当前源码身份是 `1.0.0-windows.rc.1`：直接消费 macOS v1 的 Fusion 四区前端、稳定 DTO、签名官方包、联合搜索、私人导入和受控 AI 契约。Windows 层只做平台生命周期、安全投影、Credential Manager、WebView2 和安装器，不复制共享搜索、抽取、私人仓库、模型协议或 Agent 逻辑。

仓库中的状态仍是 `installer_ready=false / SETUP_PRESENT=NO`。Win11 用户双击构建入口后可以生成一个 **未验收 RC Setup**；只有完成安装、四区、同一 v1 包、上传/抽取、私人表格、导出、双 provider、重启、升级和卸载检查后，才可分享为安装版。

正式安装包必须“开罐即用”：自带运行时和依赖，不要求用户配置开发环境；首次启动只需选择并导入 `.aresearch` 数据包。

共享界面源码已经提供两个搜索工作区：本地文献工作区，以及可选择“官方文献 / 我的实验 / 全部”的离线资料库。离线查询只调用一次共享联合搜索并只返回 `item/table/figure/finding`；Librarian 仍固定只查官方文献。私人表格必须经过 `previewed → draft_saved → confirmed/indexable`，并以最新 `expected_revision/reviewed_revision` 完成明确复核，才可进入私人搜索。

## 最终用户验收路径

1. 在干净 Windows 电脑上安装；
2. 导入签名 `.aresearch` 官方资料包；
3. 无 API key 完成离线搜索与证据查看；
4. 在设置中选择受信提供商并配置用户自己的 DeepSeek 或 OpenAI key；
5. 上传真实 PDF 并完成质量抽取；
6. 重启后确认官方包、私人库和加密对话均保持隔离；
7. 升级和回退 App/资料包，确认私人数据不被覆盖；
8. 卸载后按用户选择保留或删除私人数据。

## Mac 上可做与不可做

Mac 可以验证纯路径契约、凭据适配逻辑、Python 静态检查和共享核心接口；不能代替 Windows Credential Manager、WebView2、安装器、文件占用、Defender/SmartScreen、升级和卸载实测。

当前轻量测试：

```bash
python3 -m unittest discover -s desktop/windows/tests -p 'test_*.py'
python3 desktop/windows/build_plan.py
```

上述命令只验证构建契约，不会产生安装包。真正的 Windows 候选必须进一步通过内置运行时清单与 clean-machine acceptance harness。

## v1 RC 源码套件怎么用

1. 把整个预览 ZIP 解压到本机普通目录；不要在压缩包内直接运行。
2. 双击 `desktop\windows\Build-Windows-Preview.cmd`，或在 PowerShell 中运行 `desktop\windows\build_windows.ps1`。
3. 脚本在 `%LOCALAPPDATA%\AutoResearchBuildKit\v1` 建立隔离 Python 3.12.10 和 Inno Setup 6.7.3，核对签名发布者、固定版本、依赖解析、共享资源及 v1 官方包。
4. 脚本使用 PyInstaller 冻结程序，再由 Inno Setup 生成 `Windows-Output\Auto-Research-1.0.0-windows.rc.1-Setup.exe`，并分别计算 Setup 与资料包 SHA-256。
5. 将 `Windows-Build-Report.txt`、`Windows-Build-Report.json`、`Windows-Output\SHA256SUMS.txt` 发回本任务。此时仍是 `INSTALLER_READY=NO`。

只有冻结共享接口、生成真实 Setup，并在 Windows 11 clean machine 完成上述全流程后，才能改变 `installer_ready` 或向用户描述为 Windows 安装版。Mac 上的静态测试、backend parity 和构建资料夹都不能替代该门。
