# Auto Research 1.1 架构与技术债审计

> 审计日期：2026-08-22
>
> 范围：共享核心、Fusion 前端、macOS 桌面壳、发布与资料包；Windows 本轮冻结。
>
> 结论边界：本报告描述源码结构和发布门，不把自动测试通过等同于科学准确率完成。

## 1. 当前规模

- `src/`、macOS 与 Windows Python 合计约 109,950 行，其中包含测试。
- 共享 Fusion HTML/CSS/JavaScript 合计约 7,338 行；`app.js` 约 4,379 行，仍是最大前端债务点。
- 主要超大 Python 模块仍包括 `portable_repository.py`、`private_repository.py`、`transfer_package.py`、`six_column.py`、`agent_runtime.py`、`research_brief.py`、`import_service.py` 与 `webapp.py`。
- 仓库目录中的历史 macOS `dist/` 与 `releases/` 合计约 1.06GB。它们不是源码权威，应继续迁到仓库外制品目录；Git 只保留标签、清单、哈希和回退说明。

## 2. 1.1 已完成的结构收敛

### Fusion 工作台

- `DocumentTabStore` 已成为论文、证据、PDF、私人实验表格和资料包任务的标签身份权威。
- 标签身份、顺序和两个编辑器组可恢复；内容延迟加载，不把敏感正文写入外观状态。
- 四类证据筛选由服务端执行，迟到请求绑定原请求与原标签，不能抢夺当前页面。
- 右侧只有一个物理检查器，但按文献、搜索、实验、资料包、设置保存五套隔离投影。
- 个人实验和资料包 DOM 保持静态唯一归属，没有恢复跨页面 `appendChild` 搬运或第二导航所有者。

### AI 运行时

- 生产桌面 AI 权威已改为精确锁定的 DeepSeek Harness SDK 与随附运行时。
- Harness 只能访问八个 Auto Research 只读工具；Shell、文件系统、PTY、编辑器、子 Agent 和任意网络工具不进入 composition。
- prepared action、逐次授权、提供商/模型白名单、调用与 token 预算、凭据和科研引用完整性仍由 Auto Research 掌握。
- Harness 校验失败时失败关闭；旧 `/api/agents/librarian/chat`、`/api/context-chat` 等收费旁路在桌面壳固定返回 410，不能作为回退。
- 旧 Librarian V3 仍作为兼容代码和历史研究简报测试存在，不再是生产桌面 AI 权威；其物理删除列为 P1。

### 数据集与科学质量

- `dataset-bundle-v1` 已拆成来源投影、格式/分割、任务编排三层；真实 JSONL 与 Parquet 使用同一规范记录。
- 划分以论文稳定身份为单位确定，避免同一论文泄漏到 train/validation/test 多集合。
- 默认不复制 PDF/图片，不混入私人实验；路径、密钥、会话、内部数据库主键和草稿被白名单投影排除。
- 科学发布审计独立计算数值、单位、意义、条件、表格、图片、结论、片段和定位等14类指标，必须基于人工金标准；软件测试不能替代这些指标。

### 官方资料包 v2

- 构建输入先从不可变快照规划，再逐 PDF/资产验证普通文件、魔数、页数、哈希、身份和总大小。
- 任一 HTML 占位、复制期变化、缺失权利清单或总量超过2GB均失败关闭；不静默遗漏、自动拆包或替换论文。
- `official-package-v2` 与旧 v1 包相互独立；旧包保持不可变回退点。

## 3. 尚未清偿的主要债务

| 优先级 | 债务 | 1.1处理 | 后续门 |
|---|---|---|---|
| P0 | Harness 为 Developer Preview | 精确锁版本和三类哈希；不可用即关闭 | 完成真实App无越权、预算和历史清除验收 |
| P0 | official-package-v2 的60份PDF与权利完整性 | 建立失败关闭输入规划器 | 必须60/60真实PDF后才发布，不允许59/60 |
| P0 | macOS仍继承历史 EvidenceHandler | 生产AI与新业务由共享facade/专用API接管 | 继续把剩余业务控制器迁到 RouteSpec 权威 |
| P1 | `app.js` 与若干Python大模块 | 新功能均放入独立模块，未继续膨胀旧AI循环 | 按API/store、搜索、证据、历史与controller拆分 |
| P1 | 旧 Librarian/context-chat 兼容实现 | 桌面固定410且Fusion无调用 | 兼容测试迁移后物理删除执行循环 |
| P2 | browser/read-only/ngrok历史引用 | 用户入口保持退役 | 权限测试迁移后删除启动器和固定端口文档 |
| P2 | 仓库目录历史App/DMG约1.06GB | 本轮不把制品提交Git | 核验哈希后迁出源码树 |

机器可读状态见 `config/architecture-debt.json` 与 `config/module-ownership.json`。

## 4. 发布禁止项

- 不得使用、暂存、还原或清理用户的 `db/experimental_evidence.sqlite`。
- 不得把缺少真实PDF的官方包描述为完整包，也不得以相似论文、网页打印或图像重建替代。
- 不得把 4,356 条结构化记录、自动测试通过或模型一致性描述为语料已完成人工科学审核。
- 不得在 Harness 不可用时回退旧图书管理员、旧选中证据AI或任意兼容 endpoint。
- 不得因 Mac 验收通过而修改 Windows `installer_ready=false`；Windows 迁移继续等待用户明确指令。

## 5. 1.1 发布验收顺序

1. 目标契约与安全测试。
2. 共享全套测试，随后单独运行 macOS 全套；两者不得并行。
3. JavaScript/Python、发布契约、依赖和凭据/路径扫描。
4. 从干净提交构建 App、签名、DMG；构建过程只使用隔离证据快照。
5. 安装事务、真实 Fusion 导航/筛选/标签/PDF/包/数据集流程；默认不调用收费模型。
6. 对比生产 SQLite 前后 SHA-256，确认只有一个可启动 App，旧版本为不可启动回退副本。
