# AI 提供商与设置契约

Auto Research 的共享 AI 运行时只允许连接经过代码审计的提供商。首批注册
DeepSeek 与 OpenAI 的 OpenAI-compatible Chat Completions 接口；这不是“任意兼容 URL”
功能。增加提供商必须通过代码评审、能力声明和安全回归后进入内置注册表。

## 信任与能力

- endpoint 只由 `provider_id` 在内置注册表中解析；设置、环境变量和 UI 均不能提供 URL。
- 当前提供商必须声明 Agent、结构化 JSON 和工具调用能力；厂商 thinking 控制是
  可选能力，只有声明支持时才允许发送对应字段。
- UI catalog 按图书管理员所需能力过滤，能力不足的提供商不能被选择。
- 模型名必须同时满足安全格式与 provider 专属内置目录。DeepSeek 只允许
  `deepseek-v4-pro|deepseek-v4-flash` 并保留 Pro/Flash 现有任务分工；OpenAI 首批只允许
  `gpt-5.6-sol|gpt-5.6-terra|gpt-5.6-luna`，默认四任务均为 `gpt-5.6-terra`。
  这些目录是经官方能力文档审计的代码基线，公开 catalog 可返回 `model_options`，但不
  返回 endpoint。`model_validation=builtin-reviewed` 只表示模型进入代码目录，不表示
  用户账号可用；OpenAI 的 `runtime_activation=connection-test-required`，连接测试仍需
  验证用户账号、密钥与四任务模型在该账号中实际可访问。DeepSeek 保留现有
  `legacy-compatible` 激活路径以维持已发布行为。
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
`ai-runtime-state-v1` 保存 provider、四任务模型、selection revision 和后端签发的
attestation。attestation 严格绑定 provider registry version、provider、完整任务模型、
credential generation 与 selection revision；任一变化都会在读取时自动失效。UI patch
只接受 provider、task models 和 expected revision，不能提交 verified、attestation、
credential generation、credential ref 或 token。
设置变更和验证成功写回都会递增 revision；并发或陈旧验证只能有一个 CAS 成功。

后端 `record_verification()` 只有在注入的 verifier 对每个唯一模型均确认 structured JSON
与 tool calling 后才请求 signer 签发 token；验证可能产生模型费用，因此桌面路由/UI 必须
另行取得用户明确授权。验证过程不保存请求、响应或错误正文。普通 runtime 由
`resolve_runtime()` 决定：OpenAI 无有效 attestation 时 fail closed；DeepSeek 旧路径公开为
`legacy_compatible`，不得冒充 `connection_verified`。平台稍后实现原子 CAS store、凭据
generation provider 和安全 signer；公开 DTO 永不包含 credential ref/generation、token、
路径、密钥或内部 ID。

现有 `DeepSeekClient` 与 `DeepSeekSettings` 保持兼容，当前 DeepSeek 产品路径不会因新增
通用提供商契约而改变。`DeepSeekClient` 已成为 `OpenAICompatibleClient` 的薄兼容层；
endpoint、禁止 redirect、HTTP 重试、payload 和响应解析只有通用客户端一套实现。
`DeepSeekSettings.base_url` 仅为旧构造兼容的 fail-closed 校验输入，不能决定 endpoint；
公开 status 不再返回 base URL 或 credential source。桌面与 Web 接线、设置 UI、版本和
发布制品属于后续独立阶段。

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
