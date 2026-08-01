# Auto Research 架构治理与扩展规则

更新日期：2026-08-01
适用范围：共享核心、macOS 预览、Windows 客户端、官方资料包、私人实验库与 Agent。

## 1. 产品边界

Auto Research 只有一个用户产品：桌面个人工作台。macOS 是当前开发与验收平台，Windows 11 x64 是后续正式用户平台。App 内部可以复用 loopback HTTP 与共享 Web UI，但浏览器工作台、导师只读页、固定端口和 ngrok 均是退役兼容路径，不得再次成为产品入口。

正式分发由两部分组成：

1. 桌面 App：代码、界面、只读搜索、PDF 上传、质量抽取、Agent 与平台安全凭据适配。
2. `.aresearch` 官方资料包：经过清洗、签名、校验且可回退的只读结构化证据；与用户私人库、PDF 和 API key 分离。

## 2. 依赖方向

依赖只能向下，不允许反向引用或跨层直接访问实现细节。

```text
macOS / Windows shell / shared UI
                ↓
desktop services and stable DTOs
                ↓
federated read-only search / AI orchestration
          ↙                         ↘
official package repository     private experiment repository
          ↓                         ↓
signed package core            private import contracts
                ↓
platform-neutral utilities
```

强制规则：

- `product/**` 不导入 `desktop/**`，也不读取平台凭据或 UI 状态。
- `desktop/macos/**` 与 `desktop/windows/**` 只做平台生命周期、原生文件选择、安全凭据、受保护 bridge 和服务编排；不得复制签名、SQLite 审计、科学抽取或搜索算法。
- 官方仓库与私人仓库永不 `ATTACH`、合并写入或共享内部主键。联合搜索只消费无路径、只读公开投影。
- `EvidenceDB v12` 是历史可编辑科研工作区；`distribution-sqlite-v1` 是不可变官方分发库。任何桌面导入路径都不得对后者调用 `EvidenceDB.init()`。
- 搜索结果始终只有 `item`、`table`、`figure`、`finding` 四类。Agent 报告、推荐文章、研究简报与聊天历史均不是第五类科学证据。
- DeepSeek 只经受控客户端调用。提取/验证/总结与查询规划可使用不同模型，但模型不得绕过本地硬条件、来源分类、证据定位和质量门。

## 3. 单一正式实现

每项能力只能有一个正式路径。新实现进入稳定版前必须标记为以下一种状态：

| 状态 | 含义 | 发布规则 |
|---|---|---|
| `stable` | 当前产品唯一正式实现 | 必须有回归测试、文档和回退点 |
| `experimental` | 隔离试验，不改变稳定默认 | 默认关闭，不得写正式结果 |
| `compatibility` | 为迁移或旧数据保留 | 不在用户界面宣传，不新增功能 |
| `retired` | 已废弃路径 | 只能给出迁移提示，不能继续启动服务 |

以下路径当前已冻结：

- `stable`：本地 PyMuPDF 图表资产、DeepSeek 文本语义增强、四类证据搜索、对抗式质量门、签名资料包核心。
- `experimental`：官方与私人来源的联合召回、资料包桌面导入 UI，直至 macOS/Windows 实机验收完成。
- `compatibility`：历史 read-only 权限回归；只用于测试安全边界。
- `retired`：MinerU 云端视觉替换、浏览器编辑工作台、导师公网链接、ngrok 产品路径、无约束 Librarian 工具循环、重复 DeepSeek 预览按钮。

退役代码若仍被测试引用，先迁移测试契约，再物理删除；不得为了“先能跑”重新接回用户入口。

## 4. 稳定接口

跨模块和跨平台只能依赖下列类型的接口：

- 纯数据契约或冻结 dataclass/DTO；公开错误采用稳定 `code + safe_message + retryable`，不传播 Python/SQLite 文本。
- 官方包：`verify → install staging → repository audit → atomic activate → readiness refresh`；任一步失败均保留旧 active。
- 官方读取：仅 `OfficialEvidenceRepository`；打开时核对签名安装树、schema、身份和内容指纹。
- 私人读取：仅 confirmed 且 indexable 的 path-free projection。
- 联合搜索：按 `source_scope/source_id/entity_uid` 保留来源身份，不改变四类 payload。
- 平台凭据：macOS Keychain/预览本机加密存储、Windows Credential Manager；API key 不进 SQLite、包、日志、诊断或 Git。

新增接口必须先写消费者无关的契约测试，再由 macOS 和 Windows 分别接入。平台实现不得用临时分支字段迫使共享核心猜测平台行为。

## 5. 文件与所有权

并行开发必须声明文件所有权。共享工作区中未提交文件默认属于创建它的任务；其他任务不得覆盖、格式化或顺手提交。

- 核心组长：`src/auto_research/product/**`、跨源稳定契约、总架构与发布门。
- 搜索/个人实验：`src/auto_research/personal/**`、联合召回契约及其目标测试。
- macOS：`desktop/macos/**`、共享 UI 中已协调的桌面接入区。
- Windows：`desktop/windows/**`，最后完成安装包和 Win11 真实流程。
- 安全专员：威胁模型、攻击回归和经组长批准的最小补丁范围。

生产 SQLite、`paper_056` 现场运行产物和用户 PDF 不属于代码任务，除非用户明确授权数据操作。

## 6. 防止“屎山”的工程门

每个功能提交必须满足：

1. 一项职责：安全、核心能力、平台接入、UI 或文档不得混成不可回退的大提交。
2. 先复用：优先实现适配器与依赖注入，不复制已有签名、搜索、凭据或质量逻辑。
3. 无隐式降级：安全存储、签名、schema、来源身份或确认门失败时必须 fail closed。
4. 无废案并存：同一正式按钮、API 或数据库投影不得保留两套活跃实现。
5. 有界资源：文件、ZIP、XML、SQLite、HTTP、模型调用、缓存和并发均有上限与超时。
6. 可观测但不泄漏：进度和错误对用户清楚，对日志只记录稳定状态码、计数和耗时，不记录路径、密钥或原始载荷。
7. 可删除：实验功能必须有独立模块、开关和删除条件；不得在稳定核心中散落分支。
8. 可迁移：schema 与数据包均显式版本化；升级失败保留旧版本并支持回退。

当单文件接近约 800–1000 行或同时承担三种以上职责时，新增功能前先拆分服务、DTO、存储和路由。不得仅用注释掩盖耦合。

## 7. 发布门与测试顺序

开发阶段只运行目标测试。最终候选严格串行：

1. 目标模块测试与语法/差异检查；
2. 安全攻击回归；
3. 一次共享核心全测；
4. 一次候选构建；
5. macOS 实机启动、导包、搜索、上传与 BYOK 冒烟；
6. 最后在 Windows 11 clean-machine 路径完成安装、导包、离线搜索、PDF 上传、BYOK 提取与卸载验证；
7. 生成最终用户手册、SHA256SUMS、Git 标签和 bundle。

不得用测试通过替代以下声明：语料完成度、科学准确率、版权可分发性或跨平台实机通过。四者分别验收。

## 8. 架构决定记录

出现以下变化时，必须在项目日志和本文件或独立 ADR 中记录：

- 新数据库/schema、包格式或来源身份；
- 新 Agent、新写权限或新外部服务；
- 平台凭据、HTTP bridge 或文件导入边界变化；
- 正式路径替换、实验失败或退役代码删除；
- 影响科学证据含义、质量门或公开字段的变更。

记录至少包含：问题、选择、拒绝方案、安全/性能影响、迁移办法、回退点和验收证据。中间废案只保留必要的决定原因，不保留可误启动的产品入口。
