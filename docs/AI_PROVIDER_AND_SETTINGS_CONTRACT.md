# AI 提供商与设置契约

Auto Research 的共享 AI 运行时只允许连接经过代码审计的提供商。首批注册
DeepSeek 与 OpenAI 的 OpenAI-compatible Chat Completions 接口；这不是“任意兼容 URL”
功能。增加提供商必须通过代码评审、能力声明和安全回归后进入内置注册表。

## 信任与能力

- endpoint 只由 `provider_id` 在内置注册表中解析；设置、环境变量和 UI 均不能提供 URL。
- 当前提供商必须声明 Agent、结构化 JSON 和工具调用能力；厂商 thinking 控制是
  可选能力，只有声明支持时才允许发送对应字段。
- UI catalog 按图书管理员所需能力过滤，能力不足的提供商不能被选择。
- 模型名只是受限 ASCII 标识，不得包含 URL、路径、空白或控制字符。OpenAI 没有内置
  默认模型名，平台接入时必须显式选择四项任务模型，防止随时间漂移。
- 格式合法不等于模型具备所声明能力。DeepSeek 当前内置四模型组合属于代码审查基线；
  OpenAI 自选模型保存后固定为 `connection-test-required`，公开状态为
  `verified=false / availability=verification_required`。普通 runtime 在解析密钥前
  fail closed；只有后端专用验证流程可以进入 `verification_mode`。
- HTTP 客户端禁止跟随重定向，避免 bearer credential 被转发到另一个接收方。
- OpenAI 使用 Chat Completions 的 `max_completion_tokens`；DeepSeek 旧兼容路径继续使用
  `max_tokens` 与既有 `thinking` 结构。两者均使用结构化 JSON 的 `response_format`。

## 设置与凭据

`ai-provider-selection-v1` 只包含 `provider_id`、opaque `credential_ref` 和四项
`task_models`。`credential_ref` 只存在于后端可持久化选择中。公开 catalog、settings 和
runtime status 只包含提供商显示信息、能力、模型、configured/available/verified 与
availability；不得包含 endpoint、credential_ref、API key、文件路径或底层凭据错误。
运行时错误统一返回固定错误码、安全中文说明和 `retryable`，不得拼入请求、响应、密钥
或底层网络异常文本。

密钥仍由 macOS/Windows 的安全凭据实现管理。共享设置服务只在真正构造运行时客户端时
通过 `credential_ref` 延迟解析密钥；持久化选择与公开 DTO 都不能序列化密钥。
下一阶段需由平台安全设置存储保存与 `provider_id + 四任务模型` 绑定的验证凭证，并在
模型集合变化时原子失效；本核心不会接受 UI 提交的布尔 `verified`。

现有 `DeepSeekClient` 与 `DeepSeekSettings` 保持兼容，当前 DeepSeek 产品路径不会因新增
通用提供商契约而改变。桌面与 Web 接线、设置 UI、版本和发布制品属于后续独立阶段。

## 桌面偏好设置

`desktop-settings-v1` 与 AI 提供商及凭据设置完全分离。公开快照只包含递增的
`revision`、外观主题 `system|light|dark`、界面密度 `comfortable|compact`，以及
`locale.selected=zh-CN` 和 `locale.supported=[zh-CN]`；不接受额外字段、其他语言、密钥、
路径或内部身份。

`DesktopSettingsService.get()` 负责读取并验证快照；
`patch_preferences(payload, expected_revision)` 合并严格白名单 patch，并通过 revision/CAS
阻止陈旧界面覆盖较新的设置。共享核心不选择文件路径，也不直接写平台文件。macOS 和
Windows 必须注入实现 `AtomicDesktopSettingsStore` 的原子耐久化存储：`read()` 返回完整
快照或空值；`compare_and_swap(expected_revision, value)` 必须在同一原子操作中比较当前
revision 并替换完整快照，写入失败不得破坏旧值。任何底层异常只映射为固定、无路径的
`settings_store_unavailable`；CAS 失败映射为 `settings_revision_conflict`。
