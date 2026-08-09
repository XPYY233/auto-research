# Auto Research Windows 客户端

这里是 Windows 正式用户端的平台适配目录。当前 backend-parity 源码检查点为 `a134acd`：已组合共享 HTTP bridge、原生资料包与私人表格选择、签名资料包导入、官方/私人联合精确搜索、私人导入确认流程、Librarian V3 bridge、Windows Credential Manager 和 readiness v2。Windows 层只做平台生命周期与安全投影，不复制共享搜索、私人仓库或 Agent 逻辑。

这仍不是可交付安装包。`production-dependencies.json` 必须保持 `installer_ready=false`，当前没有 `Setup.exe`，也没有 Windows 11 clean-machine、WebView2、Credential Manager、升级/卸载或真实 DeepSeek 调用验收。旧 OneDrive 资料夹只表示 `BUILD KIT READY / SETUP_PRESENT=NO`。

正式安装包必须“开罐即用”：自带运行时和依赖，不要求用户配置开发环境；首次启动只需选择并导入 `.aresearch` 数据包。

共享界面源码已经提供两个搜索工作区：本地文献工作区，以及可选择“官方文献 / 我的实验 / 全部”的离线资料库。离线查询只调用一次共享联合搜索并只返回 `item/table/figure/finding`；Librarian 仍固定只查官方文献。私人表格必须经过 `previewed → draft_saved → confirmed/indexable`，并以最新 `expected_revision/reviewed_revision` 完成明确复核，才可进入私人搜索。

## 最终用户验收路径

1. 在干净 Windows 电脑上安装；
2. 导入签名 `.aresearch` 官方资料包；
3. 无 API key 完成离线搜索与证据查看；
4. 配置用户自己的 DeepSeek key；
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

只有冻结共享接口、生成真实 Setup，并在 Windows 11 clean machine 完成上述全流程后，才能改变 `installer_ready` 或向用户描述为 Windows 安装版。Mac 上的静态测试、backend parity 和构建资料夹都不能替代该门。
