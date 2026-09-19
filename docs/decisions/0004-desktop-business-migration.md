# ADR 0004：按业务将 Mac 接入共享应用层

状态：部分实施，2026-09-19。关联 #3、#12。

Mac 仍通过继承旧 `EvidenceHandler` 获取多项业务行为。一次性复制整份控制器只会制造第二套规则。迁移按公开业务入口逐项进行：Mac 负责 HTTP、会话、Origin/CSRF 和模式；共享 facade 约束路由/权限/大小，注入的应用控制器调用已有业务服务。

首个迁移入口为 `POST /api/uploads/pdf`：Mac `LiteratureImportAPI` → `DesktopApplicationFacade` → `LiteratureImportController` → 现有 `UploadService`。导入规则、重复处理和“等待用户明确启动提取”仍只有 `UploadService` 一处权威。成功 DTO 保持兼容；错误改为稳定且不含本机路径的中文消息。

回归通过真实 HTTP 发送合成 PDF，并禁用旧 `EvidenceHandler.do_POST`，证明该入口不再依赖旧业务分发。另覆盖重复文件、无会话、错误 Origin、无 CSRF、媒体类型、只读、空文件、超限、无效年份、存储错误。源码检查与安装候选验收分别记录。

剩余继承没有被隐藏或宣称解决。后续迁移文献导航、检索/详情、审核及导出；所有生产入口迁出后才删除 Mac 对旧控制器的继承。Windows 本轮冻结。
