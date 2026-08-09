# Desktop Application Facade 与共享路由契约

状态：`experimental`（P0 架构收敛骨架）
更新：2026-08-09

## 目标

`auto_research.desktop` 建立 macOS 与 Windows 共用的应用分发边界，但不改变任何现有 URL 或业务 DTO。当前阶段只提供声明和分发骨架；现有平台 Handler 仍是正式运行路径，后续按控制器逐项迁移。

依赖方向固定为：

```text
macOS / Windows protected loopback host
        -> RequestContext
        -> DesktopApplicationFacade
        -> injected controller
        -> existing shared product/evidence/personal service
```

共享层不提供 HTTP server，不解析 Cookie/Origin，不打开数据库或文件，不持有 API key，不实现签名、搜索、DeepSeek prompt、私人仓库或科学抽取算法。

## 公共对象

### `RouteSpec`

每条路由声明固定包含：

- `route_id`：稳定内部路由身份；
- `method`：仅 `GET / POST / DELETE`；
- `path` 或锚定 `pattern`：二选一；
- `controller`：平台中立控制器名；
- `body_cap_bytes`：读取前由宿主执行，Facade 再复核；
- `mutation` 与 `csrf_required`：任何 mutation 都必须要求 CSRF；
- `allowed_modes`：`desktop / maintenance / search_only_compat` 的显式子集。

注册器拒绝重复 `route_id` 和重复的 `method + path/pattern`。精确路径优先于正则；多个正则同时命中会失败关闭。

### `RequestContext`

宿主完成 Host、session、Origin、Content-Type、Transfer-Encoding、Content-Length、短读和 JSON 解析后，才构造上下文。上下文只携带请求身份、URL 路由、公开 query、已解析 payload、体积、授权结果和模式；不携带本机文件路径、凭据或底层异常。

### `DesktopErrorDTO`

公共错误仅含：

```json
{"code":"desktop_request_failed","message":"桌面请求未能完成。","retryable":true}
```

可选 `stage` 只允许稳定小写标识。HTTP 状态保留在 transport metadata 中，不写入响应体。Facade 捕获未知控制器异常后只返回固定错误，不返回异常文本、路径、SQLite 内容、请求体或 key。

### `DesktopApplicationFacade`

Facade 根据目录解析路由，依次复核 session、mode、body cap、CSRF，再调用显式注册的 controller。未接入 controller 返回 `desktop_controller_unavailable`，不能自动回落到平台私有算法。

## 当前声明范围

`DEFAULT_DESKTOP_ROUTES` 已覆盖以下现有路由家族：

- `package_center.*`：官方版本目录、用户包检查、导出计划、导出、导入与任务查询；
- readiness 与 `ui-mode`；
- DeepSeek credential 与 Librarian history；
- 官方资料包状态、导入、回退与 job；
- 私人实验 preview、draft、confirm、AI suggestion、单次核验导入及搜索刷新；
- official/private 联合精确搜索与详情；
- Librarian V3 chat、研究简报和选中证据对话；
- 文献工作区的论文、六列、图表、上传、处理状态和主要核验动作。

这份清单是迁移目录，不代表新建第二套 API，也不代表所有 controller 已经接入。现有 URL、请求和响应 DTO 的业务所有权保持不变。

## 平台接入规则

1. macOS/Windows 继续各自负责一次性 bootstrap、精确 Host、session、Origin、CSRF、body framing、原生 picker 与安全凭据。
2. 两个平台把同一 RouteSpec 目录投影到各自 Handler；不得复制 route 的业务校验、资料包签名、召回排序或 prompt。
3. 每迁移一个 controller，先做当前平台 URL/DTO 黄金测试，再切换该路由；不得一次替换整个 Handler。
4. `search_only_compat` 只是权限回归标记，不是浏览器产品入口。
5. Windows 在消费冻结目录并完成 Win11 真机验收前继续 `installer_ready=false`。

## 验收门

- route ID 和 method+locator 唯一；
- 非法 method/body cap/CSRF 组合在注册时失败；
- 未授权模式、缺 session、缺 CSRF、超限 body 在 controller 前失败；
- pattern 只返回命名的稳定参数；
- 未注册 controller 与内部异常只返回 path-free 固定错误；
- package、package_center、personal、federated、readiness、credential、Librarian、workspace 八个家族均在黄金目录中。
