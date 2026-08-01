# Auto Research 技术债与废案清理清单

更新日期：2026-08-01

本清单只记录会影响架构、安全、性能或后续接入的事项。临时调试输出不进入这里；完成项在项目日志记录验证证据后删除。

| ID | 优先级 | 状态 | 事项 | 完成标准 |
|---|---|---|---|---|
| AR-001 | P0 | 进行中 | macOS App 仍以 checkout EvidenceDB v12 作为可编辑工作区 | 官方包独立导入/审计/搜索；可编辑库迁移到 Application Support；无项目目录也能启动 |
| AR-002 | P0 | 进行中 | Windows 尚无真实 Win11 构建与安装验收 | 自包含安装包通过 clean-machine 安装、导包、离线搜索、PDF 上传、BYOK、卸载 |
| AR-003 | P1 | 待办 | 旧浏览器、readonly、8765/8766 与 ngrok 脚本仍被自检引用 | 自检迁移到桌面契约；旧脚本只显示迁移提示；共享兼容代码不再可形成第二产品 |
| AR-004 | P1 | 待办 | `portable_repository.py` 同时承担身份、清洗、materialize、审计和读取 | 拆成 identity、sanitizer、writer、audit、repository；公共 API 与包字节保持兼容 |
| AR-005 | P1 | 待办 | `evidence_package.py` 同时承担 ZIP、签名、安装、active selector 与回退 | 拆成 archive/signature、installer、active store；攻击回归保持通过 |
| AR-006 | P1 | 进行中 | 旧 Search V2、官方包与私人实验尚未统一召回 | 一个只读 federated service 消费三类 source；四类 DTO 和来源身份不变；无数据库写入 |
| AR-007 | P1 | 待办 | OfficialEvidenceRepository 每次查询按路径重新打开 SQLite | 固定已审计文件身份或只读连接；替换文件后 fail closed；查询不重复全库哈希 |
| AR-008 | P1 | 待办 | v12 导出源仍使用路径前后哈希，存在本地替换窗口 | 先复制私有快照并锁定 application_id/schema/列视图契约，再从同一快照导出 |
| AR-009 | P1 | 待办 | 版本兼容比较未严格区分 SemVer 预发布 | 使用统一严格解析器；包层、仓库层、macOS、Windows 共用测试矩阵 |
| AR-010 | P1 | 待办 | 内部预览摘录预算不适合公开外发 | 外发前完成逐论文权利审计并收紧聚合摘录预算；没有明确许可时只发布结构字段 |
| AR-011 | P1 | 进行中 | macOS `desktop_server.py` 与启动器仍含多类业务/冒烟逻辑 | 路由、session、history、credential、readiness、package service 分离；正式入口不打包测试专用代码 |
| AR-012 | P2 | 待办 | 图表二进制资产尚未进入资料包 | 仅 rights allowlist 通过后加入；保留旧截图哈希、图号和来源；默认包继续无资产 |

## 执行纪律

- P0 未关闭不得创建稳定标签；允许创建明确标注的内部 preview。
- 同一项只允许一个负责人和一个正式实现。实验分支失败后应删除可执行入口，只保留决定原因和回归测试。
- 任何“顺手重构”必须先证明不会改科学证据、包字节身份或公开 DTO；否则拆成独立阶段。
- Windows 最后收口；最终用户手册只根据真实冻结界面编写一次，不为中间壳层反复维护。
