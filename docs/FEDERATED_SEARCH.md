# 平台中立联合召回 v1

## 目标

`FederatedEvidenceSearch` 是 macOS、Windows 和后续 API 可共用的纯内存、只读召回层。产品层只需将已审计的 active `OfficialEvidenceRepository` 与用户的 `PrivateRepositorySearchSource` 传入，不需要把两个 SQLite 合并、`ATTACH` 或复制到新索引。

## 输入边界

每个 source 只实现 `iter_search_documents()`，产出结构化 Mapping 或已有 `EvidenceSearchDocument`。

- 官方源：直接消费 `OfficialEvidenceRepository.iter_search_documents()` 的公开 Mapping，不导入或依赖 product 实现。
- 私人源：`PrivateRepositorySearchSource.iter_search_documents()` 只产出 `confirmed + indexable` 记录的无路径 DTO。
- 两者都必须只使用 `item` / `table` / `figure` / `finding`，并带完整的 `source_scope` / `source_id` / `entity_uid`。完整三元组是 get 和去重的稳定身份。

服务初始化时对四类、来源身份、重复身份、嵌套深度和字段大小做 fail-closed 校验。`data_root`、SQLite/PDF/文件路径、Zotero/设备 key、审核者和内部数字主键不得进入服务。

## 查询 API

```python
service = FederatedEvidenceSearch((official_repository, private_search_source))

page = service.search(
    "W-Ta 纳米硬度",
    page=1,
    page_size=20,
    entity_types=("item", "figure"),
    source_scopes=("official", "private"),
    source_ids=None,
)

document = service.get(
    source_scope="private",
    source_id="personal-...",
    entity_uid="private:item:...",
)
```

- `query=""` 是稳定排序的浏览，不要为“显示全部”伪造关键词。
- 非空查询先通过确定性覆盖门：1–2 个词需全部命中，3 个及以上需命中至少 `max(2, ceil(2/3 × 词数))`；再按命中词数和标题、科学含义、材料/方法/条件、原文证据的固定权重排序。短元素符号不被删除，但单个通用“辐照”不能放行多词查询。
- 支持页码/页大小、四类、`source_scope` 和 `source_id` 组合过滤；页大小最多 100，查询最长 500 字符。
- `get()` 必须携带完整稳定身份，不接受底层数据库主键或本机路径。

## 输出与性能边界

`FederatedSearchPage` 只在原公开 DTO 外包一层分页与排名元数据：`score`、`matched_terms`、`document`。`document` 本身仍是原有四类官方 Mapping 或 `evidence-search-document-v1`，不新增第五类证据。

所有文档在服务初始化时建立有界的字符串检索快照，单次查询仅扫描内存中的索引字段。当前上限为 100,000 份公开文档；固定 4,356 文档回归要求单查询在宽松的 1.5 秒门禁内完成。这是平台中立核心的性能保护，不代表桌面 UI、包验签或首次打开的端到端时间。

## 当前未接入

本层尚未接入 webapp、Search V2、图书管理员、macOS 或 Windows UI。它不写入私人 SQLite、科学生产库、官方资料包或磁盘索引；桌面层在 active official repo 与私人库分别打开后才将它们组合。
