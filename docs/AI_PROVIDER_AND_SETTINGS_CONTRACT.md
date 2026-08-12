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

共享 `OpenAICompatibleCapabilityVerifier` 提供默认生产验证实现，但不会自动运行。每个
唯一模型固定且最多调用两次：一次要求精确的极小 JSON 对象，一次要求唯一
`auto_research_capability_check` 工具及固定参数；工具永不执行。两次调用都使用 32 token
上限、禁重试和统一 `OpenAICompatibleClient` 的固定 endpoint/禁重定向网络实现。
`verification_mode` 只给这两个内部固定动作放行，普通 JSON 或工具请求仍 fail closed。
验证器通过后端 credential resolver/ref 延迟取得 key；公开结果只返回 provider、model 和
两项能力布尔值，不保存或返回请求、响应、key、credential ref、endpoint 或 token。

`AIDesktopService` 是 macOS/Windows 共用的 `ai-desktop-service-v1` 编排边界。平台只注入
原子 runtime store、attestation signer 和实现 `CredentialManager` 的安全凭据后端；固定
credential ref 由共享服务按 provider 选择，renderer 永远不能提交。公开方法固定为
`catalog/get/patch/credential_status/credential_save/credential_delete/test`。保存密钥只写
平台安全存储，不联网；替换或删除都必须令 generation 严格递增，使旧 attestation 在下次
读取时自动失效。平台的 save/delete 必须把密钥变更与 generation++ 作为同一个原子操作，
不得出现“密钥已变但 generation 未变”的可见状态。`test` 只接受
`ai-capability-test-consent-v1` 和当前 revision，并在任何
可能收费的模型调用前核对 provider/revision；随后复用共享固定 verifier 和
`record_verification()`。公开 catalog、状态、操作结果和错误均不含 key、credential ref、
generation、endpoint、attestation、token、路径或内部 ID。DeepSeek 旧 alias 后续只能委托
同一个 manager/service，不能成为第二套凭据权威。

能力证明是固定 15 分钟短租约；`issued_at/expires_at` 与 provider、模型、credential
generation、registry version 和 selection revision 一起进入签名 claims，但不进入公开 DTO。
只有 `issued_at <= now < expires_at` 才有效，时钟异常、未来签发或到期均 fail closed 为
`verification_required`。时钟通过共享核心 `Clock` 注入，平台不得自行解释租约。

`catalog.capability_test` 在用户授权前公开本次测试的 provider、revision、唯一模型数及
`maximum_model_calls = 2 × unique_model_count`；这里的两次是每个唯一模型的上限，不是整次
测试总共两次。共享服务对相同 provider+revision 实施进程内单飞；并发重复请求返回稳定
`ai_desktop_test_busy`，不会启动第二组收费调用。前端 consent nonce 属于下一阶段，不在本
契约中伪造。

## AI 知情同意

共享 `AIConsentService` 是所有外发 AI 动作的服务端一次性门，scope 固定为
`librarian`、`literature_extraction`、`personal_suggestion` 和
`selected_evidence_chat`，每个 scope 都有独立版本化 disclosure。平台只有在用户明确
同意后调用 `issue()`；取消时不调用，因此不生成 nonce、不写状态且零网络。provider 与
内部 revision 每次都从 `AIRuntimeStateService` 权威状态读取，不接受 renderer 自报；因此
provider 切换或 A→B→A 后旧 nonce 均不能复活。

nonce 是进程内随机不透明句柄，TTL 固定 5 分钟。进程随机 HMAC claims 绑定 session、
provider/revision、scope、disclosure version、issued/expires 和一次性 action digest；
`consume()` 必须在模型调用前对同一实际外发 DTO 重新计算 digest，以恒时比较验签并原子
消费。过期、重放、篡改、跨 session/scope/provider/version 全部使用稳定 path-free 错误
拒绝；nonce 不持久化到数据库、历史或 localStorage。localStorage 最多记录用户已看过某个
`provider+scope+version` 的披露，用于避免重复展示，永远不能替代本次 action nonce。

`canonical_action_digest()` 只接受有限深度/节点数、规范 JSON 基本类型，总规范载荷上限
64 KiB；拒绝非有限数字、敏感键、本机绝对路径以及 file/sqlite URI。`issue()` 公开字段仅
为 schema、scope、provider、disclosure version、nonce 和 expires_at，不包含 session、
action digest、披露内容、key、endpoint、路径或内部 ID。

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
