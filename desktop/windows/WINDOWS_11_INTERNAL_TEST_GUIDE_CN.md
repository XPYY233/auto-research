# Auto Research Windows 11 内部测试：零基础手册

## 这次拿到的是什么

这是 **BUILD KIT READY** 的内部测试资料夹，不是已经完成的安装包。Mac 不能生成或验证可信的 Windows `Setup.exe`，因此资料夹里故意没有伪造的安装程序。

你这次只需要验证：Windows 11 能否在不预装 Python、Git、Node.js，不关闭 Windows 安全中心的情况下，自动建立隔离工具链并跑完现有 Windows 定向测试。

## 你只需要点击一次

1. 等 OneDrive 把整个 `Auto-Research-Windows-Internal-Test` 文件夹下载完成；文件图标不要仍显示“云朵”。
2. 打开该文件夹，双击根目录的 `Build-Windows-Preview.cmd`。
3. 如果 Windows 弹出“是否允许运行”，确认文件来自你自己的 OneDrive 测试资料夹后选择运行；**不要关闭 Defender，也不要关闭 SmartScreen**。
4. 黑色窗口会自动下载 Python 官方签名安装器，并安装到你账户的 `%LOCALAPPDATA%\AutoResearchBuildKit`。它不会修改系统 Python，也不要求管理员权限。
5. 等窗口显示 `The build kit check passed`，按任意键关闭。
6. 回到资料夹，打开 `Windows-Build-Report.txt`。把这个文件完整发回本 Windows 开发任务。

如果失败，也不要自己安装开发环境。保留 `Windows-Build-Report.txt`，并截图黑色窗口最后十行发回来。

## 本轮正确结果

正确结果是报告中出现：

```text
STATUS=BUILD_KIT_READY
SETUP_PRESENT=NO
```

`SETUP_PRESENT=NO` 不是你的操作失败，而是本轮刻意的安全门：共享桌面 bridge 尚未在 Windows 11 真机完成集成与冒烟测试，所以当前不能诚实地产生并命名为可用安装包。

## 后续拿到真实 Setup 后的体验顺序

只有下一轮资料夹明确包含经过 Win11 构建和冒烟验证的 `Setup.exe` 时，才按下面流程体验：

1. 双击 `Setup.exe` 安装并打开 Auto Research。
2. 首屏选择“导入资料包”，选择本资料夹里的 `auto-research-internal-evidence-0.1.0-preview.1.aresearch`。
3. 等界面依次显示检查文件、验证资料包、安装、准备离线搜索、完成。
4. 在搜索页先不填 API key，搜索“钨”或浏览四类证据，确认离线官方库可用。
5. 在设置中填写你自己的 DeepSeek API key；软件不附带开发者密钥。
6. 上传一份你有权使用的真实 PDF，观察上传、提取、搜索全过程。
7. 出现错误时只截图界面错误码；不要发送 API key、个人 PDF 或本机路径。

## 文件校验

`SHA256SUMS.txt` 记录资料包和冻结源码的 SHA-256。资料包正确值必须为：

```text
73672f94335604609d729671ab4a950e361b8cb569523a18980f0112c7c9f91d
```

本包不含 PDF、图片二进制、开发者密钥或生产数据库。官方资料包只含结构化只读证据。
