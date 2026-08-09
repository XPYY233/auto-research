# Windows 共享架构迁移边界

状态：共享契约已冻结，Windows 黄金子集已对齐；运行时尚未迁移，`installer_ready=false`。

2026-08-09 增量：Windows 安全 HTTP 外壳已对齐共享资料包中心路由、
`package_center.js`、异步 `package-job-v1` 轮询及只接受
`PrivatePdfLease` 的用户论文 PDF 流。平台层仅转发共享
`PackageCenter/PackageExportService/PackageTransferImportService/PackageJobService`；
在安全 v12 快照、私人仓库解析器和 transfer activation 生产组合注入前，
`UnavailablePackageCenterBridge` 固定失败关闭，不把源码契约误报成可用功能。

## 目的

Windows 不应通过复制 macOS 或旧 `webapp` 的路由、资料包任务、搜索或个人导入算法来获得功能一致性。共享核心现已提供四项正式契约，Windows 候选只消费这些契约：

1. `auto_research.desktop.application_facade.DesktopApplicationFacade`：平台无关的工作区与文献处理业务；
2. `auto_research.desktop.routing.RouteSpec` 与 `route_catalog`：固定 method、path、body cap、controller 与错误投影；
3. `auto_research.product.package_job_contract`：固定阶段、进度、错误及 install-audit-activate 生命周期；
4. `config/release-contract.json` 与 `auto_research.release_contract`：分别声明核心、macOS、Windows、资料包和前端资源身份。

机器可读权威为 `architecture-migration-contract.json`。本文只解释迁移方法，不定义另一套接口。

## 必须保留的 Windows 职责

- Credential Manager 及运行时 secret resolver；
- 原生文件选择、选择后替换检测与 opaque selection handle；
- 单实例、进程和 WebView2 生命周期；
- `127.0.0.1` 随机端口上的 Host/session/Origin/CSRF/body-cap 安全外壳；
- Windows 自包含构建、安装、升级、卸载和 clean-machine 验收。

共享 facade 永远不能接收原始 Windows 路径或 API key。Windows renderer 永远不能接收 secret、仓库对象或底层异常。

## 当前可删除或收缩清单

只有在 macOS 与 Windows 同时通过共享黄金契约后才能执行：

- `shared_http_bridge.py`：删除逐业务路由分支，保留安全外壳、静态资源和通用 `RouteSpec` dispatch；
- `package_import_progress.py`：删除平台自有 stage/error/progress 语义；
- `package_import_service.py`：收缩为 Windows scheduler、原生输入与共享 job 调用；
- `package_import_bridge.py`：收缩为 path-free renderer DTO；
- 删除 `shared_http_bridge.py` 中的硬编码版本，改为只读 release contract 注入。

不得在迁移中删除 Windows 凭据、picker、安全会话、单实例和失败关闭逻辑。

## 迁移顺序

1. 以共享 `RouteRegistry.golden_contract()`、package job DTO 和 release contract 冻结当前 URL、DTO、安全边界及包任务顺序；
2. 当前 Windows 已验证19条现有桌面路由均属于共享 catalog，但尚未把 HTTP dispatch 切到 facade；
3. 等 macOS 先接入并完成实机冒烟；
4. Windows 只增加薄 adapter，运行同一 golden contract；
5. Windows 当前 stage 名称与共享 official-import stage 一致，但进度仍按阶段索引换算，必须改成共享 DTO 百分比后再删平台语义；
6. 双端一致后删除上述重复实现；
7. 最后进行 Win11 安装、导包、离线搜索、PDF、BYOK、个人表格和卸载验收。

任何阶段失败均保持 `installer_ready=false`，不能用源码测试替代 Windows 11 实机验收。
