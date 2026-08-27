# Auto Research 1.2 旧 Web 控制器消费者审计

## 1. 结论

当前 macOS 生产页面只加载 Fusion 九个脚本。`app.js`、`desktop_product.js`、旧
`package_center.js` 和 `workbench.js` 均不在 `index.html`、macOS App 资源清单或
macOS 静态路由白名单中，因此它们不是当前产品界面。

这四个文件仍不能直接删除。它们的最后消费者混合了四种不同性质：

1. 仍有价值的安全和业务不变量，应该迁到 Fusion 或平台中立服务；
2. 只证明旧页面存在的兼容测试，应该改为当前生产资源测试；
3. 已被 Fusion 正确取代的旧交互，不应迁移；
4. Windows 冻结期间仍在静态白名单中的兼容资源，只能在 Mac 获认可、Windows
   解冻后由 Windows 薄平台任务移除。

物理删除顺序必须是“迁移消费者 → 验证当前主链 → 移除平台清单 → 删除文件”，
不得先删文件再追逐测试失败。

## 2. 当前生产权威

当前 `index.html` 的脚本顺序是：

1. `ai_consent.js`
2. `document_tab_store.js`
3. `pane_layout_controller.js`
4. `workspace_layout_controller.js`
5. `fusion_pdf_controller.js`
6. `fusion_ai_experience.js`
7. `fusion_package_center.js`
8. `fusion_personal_import.js`
9. `fusion_review.js`

其中 `fusion_review.js` 是唯一启动、导航和共享请求权威；其他模块只能通过注入端口
参与当前页面，禁止自行注册第二生命周期、第二请求封装或第二导航状态机。

## 3. 逐文件消费者与处置

| 旧文件 | 当前直接消费者 | 仍有价值的能力 | 不应迁移的内容 | 删除前必须完成 |
|---|---|---|---|---|
| `workbench.js` | Windows 静态白名单、所有权清单、Fusion/macOS负向契约 | 无独占领域能力；主题、密度、快捷键、设置和命令面板已由Fusion拥有 | 第二套外观和导航控制器 | Mac不再依赖；Windows解冻后移除静态映射和相应兼容断言；保留“生产页不得加载旧脚本”的负向门 |
| 旧 `package_center.js` | `test_package_center_js_runtime.py`、非生产 `desktop_product.js`、Windows静态白名单 | 取消授权时零导出请求；未加密、来源未认证、组内使用三项逐项确认；论文PDF逐篇分享权限 | 旧资料包DOM、旧状态和旧初始化顺序 | 将三项安全行为改写为Fusion controller运行时测试；确认Fusion导出/回执主链；Windows解冻后移除映射；再删除旧测试装配和文件 |
| `desktop_product.js` | 旧资料包运行时测试、旧联邦PDF UI测试、一个macOS旧静态资源GET测试、Windows静态白名单 | 异步私人搜索不得抢走资料包页；资料包初始化失败不得阻断私人导入；私人索引失败应有可见且幂等的恢复入口 | `_blank`外部PDF、新旧页面间DOM搬运、旧全局desktop facade | 把异步隔离和失败隔离迁到Fusion；PDF测试只检查中央阅读器和返回链；核实私人索引恢复等价；macOS测试改查真实Fusion资源；Windows解冻后移除映射 |
| `app.js` | CSRF运行时测试、AI prepared-action运行时测试、旧只读历史测试、旧图书管理员历史保留测试、旧提取授权测试、`safe_update.py`语法检查、Windows静态白名单 | CSRF令牌轮换；prepared action严格路由/请求体/未知scope零请求；图书管理员20会话/30天保留；提取授权一次性和预算边界 | 旧DOM、旧导航、浏览器本地历史权威、旧直连AI循环、旧维护面板作为普通用户入口 | 安全断言迁到共享请求/AI授权模块；历史断言迁到当前加密会话服务和Fusion投影；提取授权迁到当前Fusion主链；safe-update只检查发布契约中的生产脚本；Windows解冻后移除映射 |

## 4. `app.js` 中尚需产品决定的历史功能

以下能力不能因为旧文件很大就自动恢复，也不能在没有决定时悄悄删除：

- 图书管理员研究简报快照的授权与下载；
- 面向维护者的逐行证据校准、原始值对比、重开决定和批量核验；
- evidence audit、实验画像、旧模型运行记录、质量候选和审核批次Markdown；
- 维护者手动保存证据快照。

它们不是普通用户的两条核心主链。默认处置是从普通Fusion工作台退役，并保留为
可审计的维护命令或独立维护服务；若用户明确需要研究简报，则以当前Harness结果、
引用身份和path-free导出重新设计，禁止把旧 `app.js` 页面复活为第二产品。

## 5. 不得迁移的旧行为

- 不恢复浏览器工作台、导师只读页、ngrok或固定端口入口；
- 不恢复 `_blank` PDF，新产品继续使用中央PDF、来源高亮、关闭与返回链；
- 不恢复浏览器 `localStorage` 作为聊天或研究记忆权威；
- 不恢复旧DeepSeek直连、环境变量密钥权威或Harness失败后的收费旁路；
- 不恢复跨页面 `appendChild`、第二导航监听器或隐藏的第二套DOM；
- 不因兼容测试仍引用旧文件，就把旧文件重新加入App资源或发布哈希。

## 6. 串行迁移批次

### Batch B：资料包安全消费者

- 将取消零请求、三项风险确认和逐篇PDF权限迁到Fusion运行时测试；
- 只在当前controller确有缺口时做最小实现；
- 暂不删除旧文件，也不修改Windows。

状态：已由`4f90add`完成消费者迁移。三项行为均由当前Fusion controller真实运行时测试覆盖，生产控制器无需新增重复逻辑；旧文件仍暂留等待其他消费者退出。

### Batch C：`desktop_product.js` 消费者

- 证明迟到的私人搜索不会切换当前模块或抢焦点；
- 证明资料包读取失败不会阻断个人实验导入；
- 把旧PDF断言替换为中央PDF和path-free身份断言；
- 核实私人导入“已保存但索引待恢复”在Fusion中有显眼、幂等的恢复动作；
- 将macOS静态资源冒烟改为当前Fusion脚本。

状态：已由`0eb625b`、`fb2f3e1`和`9e63195`完成Mac/Fusion消费者迁移。Fusion现在读取`personal-search-readiness-v1`，只在后端明确`stale / retry_required`或保存返回`personal_search_refresh_failed`时显示恢复入口；恢复只调用幂等`search-refresh`，不会重复人工确认、导入或AI。迟到状态不会覆盖新状态或抢走资料包页。旧PDF测试已改为中央PDF和path-free身份，macOS静态冒烟已改为真实Fusion脚本，旧加载顺序装配测试已删除。`desktop_product.js`与旧`package_center.js`已无Mac或共享正向测试消费者，暂只因Windows冻结静态映射保留。

### Batch D：`app.js` 消费者

- 将CSRF轮换与prepared-action请求体断言迁到共享安全端口；
- 将20会话/30天和研究记忆测试迁到当前加密状态服务；
- 将文献提取授权测试迁到Fusion提取主链；
- 让safe-update从发布契约枚举生产JavaScript，不再硬编码 `app.js`；
- 对维护者功能作“独立维护能力”或“明确退役”决定。

状态（进行中）：`33e9ae5`已把macOS安全更新器改为从
`release-contract.json`枚举所有真实发布JavaScript，并在执行前拒绝越出生产Web目录、
缺失或空的脚本集合；更新器不再通过检查未加载的`app.js`制造假信心。
`6b7b009`删除了旧页面的二次提取授权测试和已退役浏览器只读历史测试；同等且更强的
约束已由Fusion的一次任务级授权、后台任务重连、path-free收据，以及macOS加密历史
服务覆盖。CSRF、四域prepared-action和Fusion历史投影仍在本批剩余迁移中，因此
`app.js`尚不物理删除。

### Batch E：平台清单与物理删除

- Mac当前生产资源、更新器、安全测试和兼容测试均不再读取旧文件；
- 用户认可Mac主链后解冻Windows，只做静态白名单和测试的薄同步；
- 四个旧文件均无消费者后按文件独立删除并运行目标回归；
- 最后更新所有权、架构债务、发布契约和交班记录。

## 7. 验收门

每个批次都必须满足：

- 生产HTML仍只加载一套Fusion界面；
- 安全断言的强度不降低，取消操作仍为零请求；
- 旧测试的产品价值被当前主链测试覆盖，而不是简单删测试；
- macOS资源清单和发布哈希只包含真实生产文件；
- 生产SQLite和`paper_056`现场不进入测试、构建或提交；
- 只运行批次目标测试，最终冻结后才串行运行全套、构建和实机验收。
