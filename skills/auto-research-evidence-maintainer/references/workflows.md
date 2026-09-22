# Extraction, recovery and search

## Reproduce a reported missing figure/table

Identify the actual article and effective App workspace. Verify the source PDF, article identity, available figure/table captions, stored asset bytes and hashes. Then trace the whole path: local detection → AI semantic verification → automatic publication → index → refreshed catalogue → actual detail rendering → restart.

A file on disk, a queued candidate or a passing isolated test is not proof the user's missing-image case is fixed. Use the prepared visual-repair workflow for a paper with existing text when that task is authorized; preserve prior numeric/text/table versions and original image identity. Never invent a table because the user expected one.

## Import and automatic quality

Validate a real PDF and deduplicate by the existing source contract. Test missing/invalid/oversized files, duplicate operations and storage failure before changing import logic. Use the shared application facade from desktop code.

The normal product path uses bounded prepared AI actions with per-provider credentials, consent and budgets. Independent branches verify candidates; passing dual/third verification can publish. Unresolved candidates remain isolated automatic-verification failures with a retry reason; no manual approval queue is a completion target. Retain legacy audit state without making it a live workflow.

Keep `item`, `finding`, `table`, `figure` distinct. Values, units, material/condition, page, locator and excerpt stay traceable to their source. Model agreement alone does not establish scientific truth.

## Recovery and repeated requests

Test failures around each persisted boundary: source snapshot, authorization, model call start/result receipt, quality gate, publication transaction, index update, completion receipt and UI refresh. Reopen stores/processes in tests so an in-memory mock cannot hide restart failures.

A successful model result should be reused; an unknown outcome must be visible and must not trigger blind retries. A finalizing task needs a zero-model local recovery route even after database publication succeeded and a later receipt failed. Repeated requests must not duplicate evidence or charges. Test cancellation before and after the irreversible publication boundary separately.

A published database row does not prove catalogue and detail freshness. Feed production service DTOs to the production renderer behavior tests and verify delayed responses after a user switches articles/tabs.

## Search and AI citations

Search indexes are disposable projections of source records, never scientific authority. Verify item/finding/table/figure retrieval and safe detail DTOs after index failure/recovery. Librarian reads only the authorized literature scopes; private experiment values do not enter its context. Selected-evidence chat is read-only and each citation must resolve to an actual returned source.

Use the current Harness/local retrieval composition; do not resurrect historical free tool loops or direct provider fallbacks. Offline synthetic responses are for orchestration regressions. Actual provider spending follows current user authorization, not a skill's examples.

## Historical Zotero acquisition

Corpus acquisition/classification is a separate task from product maintenance. Only enter that workflow when requested; consult the project Zotero guidance and real attachments. Do not run historical evidence CLI examples that silently default to the protected production database.
