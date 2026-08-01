# 内部官方资料包 0.1.0-preview.1

状态：内部工程预览；不得解释为公开可分发版本。

## 身份与内容

- 文件：`auto-research-internal-evidence-0.1.0-preview.1.aresearch`
- Package ID：`auto-research-internal-evidence`
- 发布者 key ID：`auto-research-internal-preview-2026-v1`
- Package SHA-256：`73672f94335604609d729671ab4a950e361b8cb569523a18980f0112c7c9f91d`
- Manifest SHA-256：`68a8faa659adc28a6647a0bc2a564cd2d43106d5502b46ec2d65c775f1395057`
- 公开内容指纹：`d09e3c6134386a55d6510154afee22cfe63cd0aa979518aa223d96bd8e34a08c`
- 稳定源快照 SHA-256：`d62dc5c43ac9e0fb97e0ad2ecb85deaf50447fb7a036acae5147e8f6111236f6`
- 压缩包大小：2,357,099 bytes；内部 SQLite 为 16,621,568 bytes。

| 对象 | 数量 |
|---|---:|
| 论文元数据 | 60 |
| 四类证据总数 | 4,356 |
| 数值条目 `item` | 3,142 |
| 定性结论 `finding` | 936 |
| 原始表格 `table` | 46 |
| 论文图片 `figure` | 232 |

其中 33 篇至少有一个可搜索四类对象，27 篇当前仅有论文元数据。这不等同于 33 篇已经完成科学人工金标准，也不改变固定语料 17/50 数据就绪的项目口径。

## 包内边界

包内固定只有六个成员：

- `manifest.json`
- `checksums.json`
- `signature.json`
- `evidence/repository.sqlite`
- `rights/licenses.json`
- `provenance/sources.json`

本包不含 PDF、截图或其他二进制资产，不含本机路径、Zotero key、审核者身份、私人实验、对话历史、API key 或签名私钥。权利范围明确为 `internal-preview-only`；外发前必须另做逐论文版权与摘录预算审计。

## 构建与导入证据

- 由稳定 v12 快照生成公开白名单投影，源 SHA 不匹配时构建失败。
- supplied paper/entity UID 必须与规范身份一致；冲突的次级别名被安全忽略，规范 UID 别名始终保留，不删除论文或四类证据。
- Ed25519 公钥随 App 代码受审发布；私钥只存在于维护者外部安全位置，缺失时不会用原 key ID 静默重建。
- 构建完成前只写私有 staging；失败不会留下可误用的正式输出目录。
- 真实回验执行了签名、校验和、ZIP 安全、SQLite/schema、来源、权利、稳定身份、内容计数、原子激活和 active repository 重新打开。
- 临时干净数据根上的验证导入与重新打开耗时 1.774 秒；SQLite `integrity_check` 为 `ok`。

## 应用接入规则

- App 版本必须处于清单范围 `>=0.4.0,<1.0.0`。
- 验签公钥只从 `auto_research.product.trusted_publishers` 读取，不能信任资料包自己附带的公钥。
- canonical selector 为 `<app-data>/official-packages/active.json`。
- 导入完成不等于可搜索；只有 `open_active_official_repository` 再审计成功并建立四类只读搜索服务后，readiness 才可显示 `offline_ready`。
- `distribution-sqlite-v1` 永远不走 `EvidenceDB.init()`、v12 migration 或可写 Search V2 投影。

## 尚未完成

- macOS 0.4.0-preview.1 的真实资料包 UI、联合搜索接线与实机验收。
- Windows 11 自包含 launcher、server composition、共享 UI、安装包和 clean-machine 流程。
- 官方包图表二进制资产与 PDF 的权利白名单。
- 公开外发版权许可与更严格摘录上限。
