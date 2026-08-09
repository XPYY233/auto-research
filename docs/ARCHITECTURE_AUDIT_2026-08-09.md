# Auto Research 全项目架构审计（2026-08-09）

## 结论

项目当前功能链已经覆盖文献处理、四类证据检索、Librarian V3、个人实验导入、官方资料包与两端桌面壳，但维护成本正在快速上升。最危险的不是单个超大文件，而是同一业务规则在共享核心、macOS 和 Windows 三处形成并行权威。

本轮治理遵循“先冻结契约、再迁移调用者、最后删除旧实现”，不做一次性重写。科学数据库、四类证据 DTO、`FederatedSearchSession`、`PersonalImportService` 和官方仓库审计继续作为稳定边界。

机器可读清单见 `config/architecture-debt.json`；发布身份权威见 `config/release-contract.json`。

## 规模与热点

- `src/` 与 `desktop/` 的 Python/JavaScript 源码总量约 49,700 行（不含打包制品）。
- 共享前端约 6,000 行，其中 `app.js` 约 3,992 行，`desktop_product.js` 约 857 行。
- 大型 Python 模块包括：`portable_repository.py` 1,846 行、`private_repository.py` 1,579 行、`six_column.py` 1,469 行、`research_brief.py` 1,456 行、`deepseek_extraction.py` 1,362 行、`webapp.py` 1,350 行、`agent_runtime.py` 1,325 行、`personal/import_service.py` 1,228 行、`evidence_package.py` 1,086 行。
- 浏览器工作台、read-only、ngrok、固定端口与导师模式仍有约数百处兼容引用；它们不是正式产品。
- 仓库内 `desktop/macos/dist` 约 224 MB、`desktop/macos/releases` 约 836 MB；这些历史制品应迁出源码树。

## P0：必须先收敛的三条权威

### 1. 桌面业务路由

目标为 `DesktopApplicationFacade + RouteSpec registry`。业务 controller、method、path、body 上限、mutation 标记和稳定错误映射由共享注册表声明；macOS/Windows 只实现 Host、session、Origin、CSRF、body framing 和原生能力。

迁移顺序：

1. 为现有 URL/DTO 建黄金契约；
2. 新 facade 委托现有实现，不改变响应；
3. macOS 先切换并实机验收；
4. Windows 消费同一 facade；
5. 两端通过后删除重复路由。

### 2. 资料包任务

官方包、用户包统一使用共享 `package-job-v1`。stage、error、retryable、outcome 和原子安装/审计/启用顺序由 product runtime 决定；平台只提供文件 token、后台调度和状态展示。

### 3. 发布身份

`config/release-contract.json` 统一记录 core、macOS、Windows、HTTP、官方包、用户包与共享 Web 资产哈希。平台 `version.json` 只作为生成物或本地镜像，不能继续独立决定版本。

## P1：按 facade 拆分，不重写

### 前端

逐步拆为浏览器原生 ES 模块：

- `api-store`：API、CSRF、全局状态与请求取消；
- `literature-workflow`：PDF 导入、当前论文、抽取与异常核验；
- `search-librarian`：联合精确搜索与 Librarian V3；
- `personal-import`：表格预览、AI 建议、一次核验导入；
- `package-center`：官方包、用户导出/导入和任务；
- `evidence-detail`：四类详情与原文证据；
- `history`：仅桌面安全历史。

不引入 Node 构建器；macOS/Windows 继续打包同一份静态资源。

### Python

- portable repository：schema / identity / sanitizer / read / export；
- private repository：schema / storage / transaction / projection；
- personal import：session / snapshot / AI suggestion / review orchestration；
- evidence package：archive verify / install / active selector；
- webapp：security envelope / route registry / controllers。

每次只移动一个职责，并以旧 facade 保持导入路径和 DTO 稳定。

## P2：兼容与制品清理

- 浏览器 editor、导师 read-only、ngrok 与固定端口仅在权限回归迁移完成后删除；合法 Browser PDF 获取能力单独保留。
- 历史 App/DMG 迁往 `/Users/USER/Zotero/auto-research-releases` 或备份目录；源码树只留 manifest、SHA、tag 和回退说明。
- `AGENT.md` 只保留长期不变量与当前检查点，历史决策迁入 ADR/项目日志。
- 清理 `.DS_Store`、`__pycache__`、硬编码版本和重复错误映射时使用独立提交，不能与功能修改混合。

## 协作与发布纪律

- root 是唯一 Git 写入者；子任务不暂存、不提交 main。
- 生产 SQLite 与 `paper_056` 现场始终排除。
- 各平台不得复制签名、搜索、prompt、私人仓库或 package 业务算法。
- 全测、真实模型调用和 App 构建串行执行。
- Windows 在真机 Setup 全流程前保持 `installer_ready=false`。

## 完成定义

架构治理完成不等于所有大文件都变小。完成条件是：每项能力只有一个正式权威、平台只做薄适配、版本由单一契约验证、旧路径有明确状态和删除门，并且 Mac/Windows 的 route/DTO/security 黄金契约持续一致。
