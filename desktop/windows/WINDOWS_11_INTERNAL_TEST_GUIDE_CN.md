# Auto Research v1 Windows 11：零基础构建与验收手册

## 先看结论

Mac 不能生成、签认或替你验收 Windows `Setup.exe`。仓库当前状态固定为：

```text
版本：1.0.0-windows.rc.1
installer_ready=false
SETUP_PRESENT=NO
```

你在真实 Windows 11 x64 上双击构建入口后，会得到一个 **待验收 RC Setup**。它只有完成本文全部流程，才可以改名、分享或称为 Windows 安装版。

官方 v1 资料包不需要重做。Windows 与 Mac 使用同一字节文件：

```text
auto-research-internal-evidence-1.0.0.aresearch
SHA-256: d1337a43aa4c0b83030a70e6a500bc60b85a994cb03d287396e895957ae4604d
```

它是 `distribution-sqlite-v1`、schema 1，兼容 App `[0.6.0, 2.0.0)`；包含 60 篇论文和 4,356 条四类结构化证据，不含 PDF、图片二进制、私人实验、路径或密钥。

## 一、Windows 11 上一键构建

1. 把完整源码套件和上面的 `.aresearch` 文件下载到普通本地文件夹。OneDrive 图标必须已经不是云朵；不要在 ZIP 内直接双击。
2. 不要预装 Python、Git、Node.js，不要关闭 Defender、SmartScreen 或防火墙。
3. 双击 `desktop\windows\Build-Windows-Preview.cmd`。
4. 第一次运行会联网下载 Python 3.12.10 与 Inno Setup 6.7.3。脚本会核对 Authenticode、发布者、固定版本，建立仅当前用户使用的隔离工具链。
5. 脚本随后核对共享 Fusion 资源哈希、构建文件锁、完整解析后的 Python 依赖清单，以及上面 v1 资料包的字节哈希、Ed25519 签名、checksums、rights、`distribution-sqlite-v1` 审计和官方/私人隔离。
6. 成功后打开 `Windows-Output`。应有：

   - `Auto-Research-1.0.0-windows.rc.1-Setup.exe`
   - `auto-research-internal-evidence-1.0.0.aresearch`
   - `SHA256SUMS.txt`

7. 同时保留根目录中的 `Windows-Build-Report.txt` 和 `Windows-Build-Report.json`。成功状态是 `RC_SETUP_BUILT_ACCEPTANCE_PENDING`，不是正式发布。

若失败，不要自行安装开发环境。把两个 Build Report 和黑色窗口最后十行截图发回本 Windows 对话；报告不应包含 API key、PDF 内容或私人数据。

## 二、安装与安全提示

1. 先在 `SHA256SUMS.txt` 核对 Setup 和资料包各自的 SHA-256。
2. 双击 RC Setup。当前是内部未签名/未取得公开代码签名信誉的候选，SmartScreen 可能提示“未知发布者”。只有确认文件来自你自己的构建输出且哈希一致时，才选择“更多信息 → 仍要运行”。不要全局关闭 SmartScreen。
3. 安装是当前用户级，不要求管理员权限。默认位置在 `%LOCALAPPDATA%\Programs\Auto Research`。
4. 应用使用 Microsoft Edge WebView2。Windows 11 通常已自带；若界面无法打开，只从 Microsoft 官方渠道安装 Evergreen WebView2 Runtime，不要下载第三方 DLL。
5. DeepSeek 与 OpenAI 密钥分别保存到 Windows Credential Manager。软件不得把密钥写入 SQLite、资料包、日志或 Build Report。

## 三、真实 Win11 验收顺序

严格按顺序完成并逐项截图；某项失败就停止分享：

1. **Setup 安装与启动**：安装后从开始菜单打开；确认只有一个实例，第二次启动不会创建第二个数据写入进程。
2. **Fusion 四区**：左侧只有“文献、搜索、实验、资料包”四个科研入口；设置是底部工具，不是第五区。
3. **官方资料包**：进入资料包区，导入同一个 v1 `.aresearch`。应完成检查、验签、安装、只读仓库审计和离线搜索激活；不得出现本机路径。
4. **离线精确搜索**：不配置 API key，浏览或搜索“钨”，检查 `item/finding/table/figure` 四类、详情和来源身份。
5. **图书管理员**：配置 provider 后提出一个有引用的问题，确认结果仅来自官方文献，私人实验值不会进入回答；研究简报只由当前有效回答导出。
6. **PDF 上传与提取**：上传一篇你有权使用的真实 PDF；明确点击自动提取，查看分阶段进度与候选质量门，确认未自动发布失败候选。
7. **个人实验**：导入 CSV，再至少测试一次 TSV 或 XLSX；预览、核验、一次确认后进入私人库。打开分页表格，核对列、单位、样品/批次和分页。
8. **联合搜索**：分别选择“官方文献 / 我的实验 / 全部”。离线精确搜索不应依赖 API key；图书管理员仍只能使用官方文献。
9. **详情、PDF 与导出**：从条目/表格/图表打开详情；有本地论文权限时打开 PDF 并返回。分别测试单条 CSV/XLSX、当前论文和统一批量导出。
10. **双 provider Credential Manager**：分别保存 DeepSeek、OpenAI key，测试连接与固定模型；删除其中一个后另一个仍保持。任何界面和报告不得回显 key。
11. **重启**：退出并重开，确认活动官方包、私人实验、设置和 provider 分槽状态仍在，且不会重新导入或自动收费调用。
12. **升级**：安装一个后续 RC 覆盖版本，确认私人库、官方包 selector 和凭据不被覆盖；失败时旧版仍可恢复。
13. **卸载**：从 Windows 设置卸载程序。程序文件应删除；用户数据默认保留。只有用户明确选择清理数据时才删除 `%LOCALAPPDATA%\Auto Research`，Credential Manager 密钥需单独按产品流程删除。

## 四、Defender、WebView2 与故障回传

- Defender 隔离文件：不要关闭防护。记录威胁名称、文件名、Setup SHA-256 和时间，发回本任务判断是否误报。
- 白屏/窗口打不开：记录 Windows 版本、WebView2 Runtime 版本和 Build Report；不要尝试改防火墙规则。
- 导包失败：只回传稳定错误码、资料包 SHA-256 和阶段；不要发送本机完整路径。
- Credential Manager 失败：截图“凭据状态”而非 key；不要把 key 粘贴到聊天。
- 上传或提取失败：只提供文件类型、大小、稳定错误码和是否为扫描 PDF；未经许可不要发送论文。

## 五、何时才算可以分享

只有以上 13 项全部通过、Setup SHA-256 已封存、Build Report 与 Win11 截图齐全，组长才能把 `installer_ready` 改为 true 并发布新的验收身份。在此之前，即使已经生成 `Setup.exe`，诚实状态仍是：

```text
RC_SETUP_BUILT_ACCEPTANCE_PENDING
INSTALLER_READY=NO
```
