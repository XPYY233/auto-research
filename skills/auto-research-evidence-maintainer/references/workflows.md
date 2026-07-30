# Project workflows

## Workflow A: acquire and organize literature in Zotero

This is the older corpus workflow, distinct from evidence extraction.

1. Discover candidates through lawful open/official sources.
2. Acquire a real PDF; metadata alone never counts.
3. Validate PDF readability and paper identity.
4. Import into Zotero without duplicating the paper.
5. Classify by research object plus research method.
6. Verify local attachments, metadata-only count, unfiled items, and duplicates.

Historical verified checkpoint: `Auto Research PDF-only 300 CLEAN - Object+Method`, 300 valid PDFs and 30 child collections. Treat it as time-specific and re-audit before mutation. Read `AUTO_RESEARCH_HANDOFF_1000.md` and use the separate `auto-research-zotero` skill for corpus expansion.

Direct Zotero SQLite writes require Zotero closed, a timestamped backup, minimal deterministic changes, reopen, and verification.

## Workflow B: ingest one paper into the evidence product

### 1. Validate source and identity

- Confirm the PDF opens and contains real article text.
- Match DOI or normalized title; use PDF fingerprint as another duplicate signal.
- Treat Zotero keys as local compatibility only.
- If already scanned, load saved data by default. Rescan only after explicit confirmation with `--force-rescan`.

### 2. Recognize the paper

- Classify experimental, computational/modeling, or review/report mode.
- Detect one or more supported experiment types.
- Use the classification to choose extraction foci; do not force all papers into irradiation.

### 3. Build local evidence context

- Read the PDF text layer in bounded page blocks.
- Store page-level evidence context.
- Reuse existing PDF fingerprint and visual cache.
- Use the local PyMuPDF visual detector only when figures/tables are missing.

### 4. Extract and gate

Run the adversarial quality path:

```bash
PYTHONPATH=src python3 -m auto_research.cli evidence-quality-run "<DOI or title>"
```

The two branches scan independently. Pair candidates using value, unit, meaning, material/condition, page, locator, and excerpt. Publish high-confidence `dual_pass`; send lower confidence to a third page-bounded review. Publish passing `third_pass`; quarantine unresolved `manual_review`; retain `rejected` for audit.

Do not use single-branch preview output as published evidence.

### 5. Preserve evidence types

- Numeric direct facts remain `item`.
- Explicit prose findings remain `finding`.
- Complete original tables remain `table`.
- Complete paper images remain `figure`.

Do not create a fifth AI-owned type.

### 6. Verify after extraction

- Run database health.
- Search by DOI, title, author, material, property, condition, and Chinese/element aliases.
- Open source evidence and visual details.
- Confirm numeric values did not become prose and units were not guessed.
- Generate or update the paper audit.
- Re-run the fixed-corpus audit after every 3-5 new papers.

## Workflow C: search and Librarian

Search V2 indexes the four public types as a disposable projection. Exact search may scope by papers. The Librarian always searches the complete evidence library:

1. DeepSeek plans bounded short queries.
2. Local Search V2 performs coverage recall across all four types.
3. DeepSeek separates direct joint evidence from partially related evidence and produces cited Chinese synthesis.

Keep all bounded candidates in the response. Use `agent_cited` to distinguish prose evidence from expansion candidates. Cache complete identical responses only while the database source fingerprint is unchanged.

## Workflow D: selected-evidence chat

Open chat only after selecting one item/finding/table/figure. Send that entity plus bounded relevant PDF pages. Answer in Chinese with evidence pages and limitations. Never mutate, review, publish, or expose the whole database from chat.
