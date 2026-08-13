# Auto Research Windows 客户端

这里是 Windows 正式用户端的平台适配目录。0.8 预版本已组合共享 Workbench 日/夜界面、设置存储、DeepSeek/OpenAI 分槽 Credential Manager、prepared-action AI 控制器、原生资料包与私人表格选择、签名资料包导入、官方/私人联合精确搜索、私人导入确认流程和 readiness v2。Windows 层只做平台生命周期与安全投影，不复制共享搜索、私人仓库、模型协议或 Agent 逻辑。

这仍不是可交付安装包。`production-dependencies.json` 必须保持 `installer_ready=false`，当前没有 `Setup.exe`，也没有 Windows 11 clean-machine、WebView2、Credential Manager、升级/卸载或真实 DeepSeek 调用验收。旧 OneDrive 资料夹只表示 `BUILD KIT READY / SETUP_PRESENT=NO`。

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

## 0.8 预览套件怎么用

1. 把整个预览 ZIP 解压到本机普通目录；不要在压缩包内直接运行。
2. 双击 `desktop\windows\Build-Windows-Preview.cmd`，或在 PowerShell 中运行 `desktop\windows\build_windows.ps1`。
3. 脚本会在当前用户的 `%LOCALAPPDATA%\AutoResearchBuildKit` 下建立隔离 Python 3.12 工具链，校验 Python 官方安装器签名，并运行 Windows 定向源码契约。
4. 将生成的 `Windows-Build-Report.txt` 发回 Windows 开发任务。看到 `BUILD_KIT_READY / SETUP_PRESENT=NO` 只表示源码检查通过，不表示已经有可安装软件。
5. 当前没有 `Setup.exe`；不要从未知来源下载所谓 0.8 安装器，也不要关闭 Defender 或 SmartScreen。

只有冻结共享接口、生成真实 Setup，并在 Windows 11 clean machine 完成上述全流程后，才能改变 `installer_ready` 或向用户描述为 Windows 安装版。Mac 上的静态测试、backend parity 和构建资料夹都不能替代该门。
