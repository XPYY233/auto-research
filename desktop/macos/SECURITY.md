# Auto Research 桌面安全模型

## 安全目标

1. 用户数据默认不公开、不遥测、不进入代码仓库；
2. App、官方资料包、用户私人库与本机状态物理分离；
3. 更新不能覆盖或静默迁移科学证据；
4. 数据离开电脑前，必须说明接收方、内容范围和目的；
5. 无 API 密钥时导入、离线搜索和证据浏览仍可用。

## 0.3.0-preview.1 的对话保护

```text
图书管理员历史
    ↓ JSON 结构与大小验证
AES-256-GCM 认证加密
    ├── 预览期密钥：Application Support 私有目录，0600
    └── 密文：~/Library/Application Support/Auto Research/Private Data/
              librarian-history-v2.enc
```

- 32 字节随机密钥与对话密文分文件保存，不进入科学数据库、资料包或日志；
- 密文目录为 `0700`，密钥和密文为 `0600`；
- 每次写入使用随机 nonce 和原子替换；
- 篡改、错误密钥或私有存储不可用时拒绝读取；
- 失败时不降级为持久化明文 localStorage；
- 历史接口只存在于受随机会话 Cookie 和同源检查保护的本机服务。

### 为什么预览期不是 Keychain

当前 App 采用 ad-hoc 临时签名，重建可能改变代码身份。访问旧 Keychain 项会触发用户看到的“登录钥匙串密码”框。预览版因此绝不读取或删除旧项，也不要求用户输入任何 Auto Research 密码。

代价是预览期密钥安全边界依赖 macOS 账户权限和 FileVault；取得当前解锁账户权限的恶意程序可能同时读取密钥与密文。拥有稳定签名后，macOS 发行切回 Keychain；Windows 正式版使用 Credential Manager。

## 外部 AI 与 BYOK

离线搜索不调用模型。上传新 PDF 后的 AI 抽取、图书管理员和证据解释会把有限内容发送给用户选择的 AI 服务，因此不能宣称“全部数据不出电脑”。正式界面必须说明：

- API 密钥是 AI 服务通行证，不是软件密码；
- 谁接收什么数据、为什么发送；
- 可以暂不配置，离线功能仍可用；
- 密钥只进入 OS secure credential store，不写 SQLite、日志、资料包或导出。

不得把一个无限额开发者主密钥分发给所有用户。内测使用独立、可撤销、有限额的密钥；正式版使用用户自己的密钥或带账户、配额和审计的网关。

### macOS 凭据后端契约

- 桌面内部接口仅返回 `provider / configured / storage`，永不返回密钥、尾号或可复原提示；
- `GET /api/desktop/credentials/deepseek` 查询状态，`POST` 仅接受 `api_key`，`DELETE` 显式删除；三者均要求每次启动随机桌面会话；
- 当前 ad-hoc 预览默认使用 Application Support 私有目录中的 AES-256-GCM 密文与独立 `0600` 随机密钥，因此不会探测旧 Keychain 项或弹出登录钥匙串密码；
- 稳定签名构建通过 `AUTO_RESEARCH_MACOS_CREDENTIAL_STORE=keychain` 切换到 `kSecAttrAccessibleWhenUnlockedThisDeviceOnly` 的新 Keychain service；
- 保存后密钥只装载到当前 App 进程内存供既有 DeepSeek 核心读取，不写 SQLite、资料包、普通设置或日志；删除时同时清除持久化凭据与当前进程值；
- 错误响应使用稳定错误码，不携带密钥、OSStatus 或请求正文。

## 资料包与版权边界

`.aresearch` 包必须校验版本、哈希、签名和相对路径。默认不包含绝对路径、Zotero/device key、私人对话、API 密钥、未授权全文、截图或长摘录。付费或机构订阅 PDF 由用户自行合法导入。

`0.4.0-preview.1` 的正式导入路径只调用共享 official-package core 的内置发布策略：签名 key 必须同时匹配允许的 `package_id`、发布者名称、`internal-preview` 通道和 `internal-preview-only` 权利范围。桌面端不会仅凭“签名能验过”就接受资料包，也不会通过传入另一份公钥字典绕过策略。活动包只有在安装树和只读仓库再次审计成功后才进入离线就绪状态；刷新失败会清空内存搜索句柄，不继续提供旧结果。

浏览器、导师只读和 ngrok 已退出用户产品入口；相关代码只可保留为内部权限回归。正式分享使用可验证资料包，而不是暴露开发机。

## 正式发布前安全门

- Windows 干净机安装、导包、离线搜索与 Credential Manager 验收；
- 用户私人库与官方包升级隔离；
- 损坏、错误签名、版本冲突和路径越界包全部拒绝；
- 依赖审计、代码签名、发布哈希、更新回退和删除演练；
- 云 AI 数据最小化、外发提示和不记录正文的本地审计。
