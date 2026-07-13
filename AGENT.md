# Auto Research Agent Notes

This project is a local literature automation workflow for fusion materials, radiation damage, cascade simulations, MLIP/MLIAP, and HEA/RHEA research. The agent must prioritize real, auditable acquisition paths and must never create fake PDFs or treat metadata-only records as full-text successes.

## Current acquisition capability summary

### Paths that can directly yield article metadata + a real local PDF

These paths have been validated in the current workflow. A paper should count as `success_local_pdf` only if Zotero or the local project has an actual PDF file that can be opened locally.

1. **OSTI public full text / PURL**
   - Source candidate type: `osti_fulltext`
   - Typical URL shape: `https://www.osti.gov/servlets/purl/<id>`
   - Observed behavior: often downloads a real PDF directly, especially for DOE-funded materials, irradiation, high-entropy alloy, and fusion-related reports/articles.
   - Examples successfully obtained:
     - `10.1016/j.cossms.2022.101001`
     - `10.1557/s43578-020-00071-8`
     - `10.1063/5.0302848`
     - `10.2172/1214790`
     - `10.1103/physrevb.108.054312` via OA/preprint alternative in some cases

2. **OpenAlex best OA PDF**
   - Source candidate type: `openalex_best_oa`
   - Access mode: usually `open`
   - Observed behavior: reliable when the candidate URL is a direct PDF, e.g. Nature PDF, arXiv PDF, or other publisher OA PDF.
   - Examples successfully obtained:
     - Nature OA PDFs such as `https://www.nature.com/articles/...pdf`
     - arXiv PDFs such as `https://arxiv.org/pdf/<id>`
     - `10.1038/s41467-023-38000-y`
     - `10.1038/s41598-018-34486-5`
     - `10.48550/arxiv.2201.08906`

3. **arXiv PDF URLs surfaced by OpenAlex or arXiv discovery**
   - Source candidate types: `openalex_best_oa`, `arxiv_pdf`
   - Typical URL shape: `https://arxiv.org/pdf/<arxiv_id>`
   - Observed behavior: direct PDF download succeeds when arXiv is reachable and rate limits are respected.
   - Caveat: arXiv API/discovery may return 429 or timeouts; do not hammer it. Use backoff and cached results.
   - Examples successfully obtained:
     - `10.48550/arxiv.2201.08906`
     - `10.48550/arxiv.2409.08030`
     - `10.48550/arxiv.2305.08140`

4. **Publisher open-access direct PDFs**
   - Source candidate types: `openalex_best_oa`, sometimes `openalex_location`
   - Typical URL shapes:
     - `https://www.nature.com/articles/<id>.pdf`
     - `https://link.springer.com/content/pdf/<doi>.pdf`
     - publisher-hosted OA PDF URLs surfaced by OpenAlex/Unpaywall
   - Observed behavior: works when the PDF endpoint is genuinely open and returns `application/pdf` or bytes beginning with `%PDF`.
   - Caveat: some pages claim OA but return HTML, login, captcha, or access-denied pages. These must not be treated as PDFs.

5. **Unpaywall OA locations**
   - Source candidate types: `unpaywall_best_oa`, `unpaywall_oa`
   - Observed behavior: useful as a DOI-based enrichment path; if `url_for_pdf` is present and direct, it may produce a PDF.
   - Caveat: not every Unpaywall URL is a direct PDF; landing pages may require browser/manual handling.

6. **Already-authorized manual/browser downloads**
   - Source path: `browser-acquire`, `open`, then `attach-pdf`
   - Observed behavior: can become a full-text success only after a real PDF has been downloaded through a lawful user/institutional session and attached.
   - Success criterion: the attached file exists locally, opens as a PDF, and roughly matches the Zotero item/title.

### Paths that usually provide reliable metadata but not necessarily a PDF

These paths are useful for discovery, DOI verification, title/year/authors, and Zotero metadata import. They must not be counted as full-text success unless a real local PDF is attached.

1. **Crossref works API**
   - Source: `crossref`
   - Gives: DOI, title, authors, year, journal/container, references/links when available.
   - PDF behavior: Crossref `link` entries may point to licensed PDFs, but they often return `permission_denied` or require publisher access.
   - Treat as: metadata + DOI authenticity verification, not guaranteed full text.

2. **OpenAlex metadata and DOI landing-page locations**
   - Source: `openalex`
   - Gives: title, DOI, abstract inverted index, authorship, OA status, locations, best OA candidate.
   - PDF behavior: only direct PDF-like OA URLs are reliable PDF candidates. DOI landing pages such as `https://doi.org/...` commonly become `needs_human`, `needs_login`, or `permission_denied`.
   - Treat as: metadata + source-candidate discovery; do not assume PDF.

3. **DOI landing pages**
   - Source candidate type: usually `openalex_location` or publisher link.
   - Typical URL shape: `https://doi.org/<doi>`
   - Observed behavior: resolves to publisher pages, not direct PDFs. Often requires JavaScript, institutional login, subscription access, or manual selection of PDF.
   - Treat as: metadata/landing page only unless browser/manual acquisition produces a real PDF.

4. **Publisher pages requiring institution/subscription**
   - Examples observed:
     - Elsevier / ScienceDirect article pages
     - AIP pages
     - Springer/TMS pages
     - ASTM licensed PDF links
     - IOP pages with captcha/human verification
   - States observed: `needs_human`, `needs_login`, `needs_captcha`, `needs_subscription`, `permission_denied`.
   - Treat as: metadata-only until the user completes lawful institution access and a real PDF is attached.

5. **Records with `no_fulltext_found`**
   - Meaning: metadata was discovered and may be authentic, but no direct PDF candidate was available or all candidates failed.
   - Treat as: metadata-only. Do not import as a full-text success.

## Current Zotero test collection status

Test collection:

- Name: `Auto Research Test - Verified PDFs - 2026-05-21`
- Zotero collection key: `269H8A53`

Current imported test set:

- 30 top-level literature items
- 20 items with real local PDF attachments
- 10 metadata-only institutional/blocked-page candidates

Verification report paths:

- Markdown: `data/matrix/zotero_test_collection_verification.md`
- CSV: `data/matrix/zotero_test_collection_verification.csv`

Current status counts:

- `success_local_pdf`: 20
- `metadata_verified_no_pdf`: 10
- `needs_review`: 0

## Mandatory success criteria

A paper is a full-text acquisition success only when all are true:

1. A real local PDF file exists.
2. The file opens with PyMuPDF or Zotero can expose a local `file://` URL for the attachment.
3. The file is not a generated placeholder.
4. The title/DOI metadata is verified through at least one trusted registry or source.
5. The PDF first page or extracted text roughly matches the item title, when text extraction is possible.

If any of these fail, classify the item as one of:

- `metadata_verified_no_pdf`
- `needs_human`
- `needs_login`
- `needs_captcha`
- `needs_subscription`
- `permission_denied`
- `no_fulltext_found`
- `needs_review`

## Agent behavior rules

1. **Never fabricate PDFs.**
   - Do not create fake PDFs for testing.
   - Do not attach generated placeholder PDFs to Zotero items.
   - Do not mark metadata-only records as PDF successes.

2. **Prefer direct open/official sources first.**
   - Try OSTI PURLs, OpenAlex best OA PDFs, arXiv PDFs, PMC/Europe PMC PDFs, and Unpaywall `url_for_pdf` before browser/manual paths.

3. **Use publisher/DOI landing pages conservatively.**
   - Landing pages are useful for metadata and human handoff.
   - They are not full-text successes unless a real PDF is downloaded and attached.

4. **Do not bypass access controls.**
   - Do not solve CAPTCHAs automatically.
   - Do not bypass paywalls.
   - Do not use proxy pools or disguise institutional identity.
   - If institutional access is needed, mark the item for user handoff.

5. **Record provenance and failure reason.**
   - For each candidate, keep `source_type`, `url`, `access_mode`, `attempted`, and `last_error`.
   - This is essential for later improving coverage.

6. **Verify before analysis/matrix use.**
   - Run authenticity verification before trusting the item in reports or review matrices.
   - The matrix should expose authenticity/PDF status clearly.

## Recommended workflow

```bash
PYTHONPATH=src python3 -m auto_research.cli discover "MLIP cascade HEA radiation damage" --limit 100
PYTHONPATH=src python3 -m auto_research.cli acquire --limit 80
PYTHONPATH=src python3 -m auto_research.cli verify --limit 120 --include-all
PYTHONPATH=src python3 -m auto_research.cli parse --limit 80
PYTHONPATH=src python3 -m auto_research.cli analyze --limit 80
PYTHONPATH=src python3 -m auto_research.cli matrix
```

For metadata-only institutional candidates:

```bash
PYTHONPATH=src python3 -m auto_research.cli open <paper_id>
# user completes lawful login/download manually
PYTHONPATH=src python3 -m auto_research.cli attach-pdf <paper_id> /absolute/path/to/downloaded.pdf
PYTHONPATH=src python3 -m auto_research.cli verify --limit 120 --include-all
```

## Experimental evidence workflow

This section is the authoritative handoff for the local six-column evidence demo. Keep it updated whenever the extraction, review, search, database schema, or checkpoint workflow changes.

The evidence project must not assume every paper is an irradiation experiment.
Before DeepSeek extraction or readiness claims, classify the local paper's
experiment type from the title and readable PDF text. Use the detected profile
to choose extraction foci. Irradiation is the first mature pilot type, but the
six-column database is intended to store experimental evidence across physics,
materials, chemistry, and engineering papers.

The web review page must expose that classification to the user as an
experiment-profile card. Fallback prompt packets must also include
`experiment_profile` and `extraction_foci`; do not reintroduce a prompt that
assumes every paper is an irradiation experiment.

Maintain `PROJECT_LOG.md` as the user-facing project change log. `AGENT.md` records operating rules for future agents; `PROJECT_LOG.md` records what changed, why it changed, and how it was verified.

### Current target article

- Title: `Irradiation effects in high entropy alloys and 316H stainless steel at 300 °C`
- DOI: `10.1016/j.jnucmat.2018.08.031`
- Authoritative PDF: `/Users/USER/Zotero/storage/XJZQ42XP/Chen 等 - 2018 - Irradiation effects in high entropy alloys and 316H stainless steel at 300 °C.pdf`
- Evidence database: `db/experimental_evidence.sqlite`
- Local review UI: `http://127.0.0.1:8765`
- macOS launcher: `scripts/start_evidence_ui.command`

Do not substitute the accepted manuscript, a handbook, or a metadata record for this final published PDF. Do not modify the Zotero database for the evidence demo.

### Six-column record contract

Every extracted or manually entered datum must expose these editable fields:

1. `value_text`: the value exactly as reported, including uncertainty, range, inequality, or qualitative wording when applicable.
2. `meaning`: the physical meaning of the value, such as experimental temperature, loop diameter, hardness, thermal conductivity, resistivity, corrosion current, or alloy composition.
3. `unit`: the reported unit; preserve an empty unit when the paper reports none.
4. `article_title`: the paper title.
5. `doi`: the DOI.
6. `context_explanation`: the main search field. Include material, specimen state, experiment type, environment, control variables, temperature/time/field/pressure/dose when applicable, measurement method, and other conditions needed to distinguish the datum.

Do not force heterogeneous values into a normalized scientific template. Preserve the reported value and unit. The source page, locator, and excerpt remain provenance metadata outside the six editable columns.

### Paper-selector workflow

The primary local command is:

```bash
PYTHONPATH=src python3 -m auto_research.cli evidence-run-article "10.1016/j.jnucmat.2018.08.031"
```

It must resolve the paper selector, set the current paper, run or prepare extraction, audit every automatic row against the local PDF, and report learning-sample counts. A paper selector may be a DOI, exact title, unique title fragment, paper id, or a legacy local/Zotero key. Prefer DOI or title in user-facing docs because Zotero storage keys differ across devices.

Use the read-only self-check before claiming the six-column workflow is ready for a paper:

```bash
PYTHONPATH=src python3 -m auto_research.cli evidence-self-check "10.1016/j.jnucmat.2018.08.031" --query 温度 --query 硬度 --min-rows 100 --min-highlight-ratio 0.8
```

The self-check must not call DeepSeek, switch the current paper, save snapshots, or modify rows. It verifies selector resolution, local PDF presence, experiment-type detection, six editable fields, PDF highlight coverage, fuzzy search, CSV/Excel export generation, learning-sample channel availability, and the core web review UI contract: editable left table, immutable original pane, confirm-before-save behavior, manual entry, whole-database search, and source-highlight entry points.

The self-check JSON must include a `requirements` array that restates the user-facing acceptance criteria in plain language: article selector to experiment type and extracted rows, six required columns, editable review preserving the original, manual entry without a fabricated original, free-text fuzzy search/export, and the review-learning loop. Do not claim the goal is ready unless both low-level `checks` and user-facing `requirements` are all `ok`.

Use `evidence-goal-audit <paper-selector>` when judging progress against the user's persistent end-to-end objective. It must distinguish `ready_for_human_review` from `goal_complete`: the target article can be ready for review while still not complete because unreviewed automatic rows remain. Do not mark the persistent goal complete until the audit proves the target data has been human-reviewed and all goal requirements are satisfied.

The web paper-switch form is intentionally read-only with respect to AI extraction: submitting a paper selector must call `/api/current-paper` only, load saved local rows, and never trigger DeepSeek or `/api/current-paper/run-workflow`. AI extraction in the web UI must require an explicit click on the separate current-article extraction button.

The web UI must clearly show whether the current article has already been scanned. A paper is considered scanned when it already has six-column rows or at least one completed DeepSeek extraction run. Re-scanning a scanned paper must require an explicit browser confirmation and the backend request must include `force_rescan`; otherwise the API must reject the run with `already_scanned`. The command-line DeepSeek paths must follow the same policy: `evidence-run-article` and `evidence-deepseek-extract` must not call DeepSeek again for scanned papers unless `--force-rescan` is present.

Scanned article data must be visibly saveable from the web UI. The save action writes the current six-column rows to a timestamped CSV snapshot under `data/evidence/saved_scans/` and records the latest snapshot path in the evidence database. Saving a snapshot must not call DeepSeek and must not mutate individual row values.

For a non-target article with a readable PDF and configured DeepSeek runtime, run the evidence-grounded DeepSeek extractor. Commit verified candidates only when that paper has no six-column rows; otherwise create a preview. If DeepSeek is unavailable, prepare a constrained prompt packet. For an article without a readable PDF, stop and report the missing local source.

### Review and learning rules

- Version `0` is the immutable automatic extraction for an automatic row.
- `confirmation` means the researcher reviewed the current content without changing it.
- `correction` means one or more of the six fields changed before confirmation.
- `manual` means the researcher added a missed datum; it has no automatic original.
- `ambiguous` means the evidence or sample/condition relation cannot yet be resolved. It is a reviewed negative sample, remains fully traceable, and is excluded from ordinary search.
- `rejected` means the researcher decided that an automatic candidate must not be used as article data. It is a reviewed negative sample, remains fully traceable, and is excluded from ordinary search.
- Ambiguous and rejected decisions must record a reason and may be reversed to `automatic` (pending review) by appending a new version; never delete or overwrite their history.
- Typing in a cell is temporary. Only `确认当前内容` may create a new version.
- Temporary cell edits must be visibly marked and must trigger a discard warning before switching papers, importing, re-running extraction, or leaving the page.
- The right pane must always retain the immutable automatic original for automatic rows.
- Learning export must include confirmations, corrections, manual additions, rejections, and ambiguities as distinct sample types. Rejections teach DeepSeek to avoid a candidate pattern; ambiguities teach it to route unresolved relations to a pending task rather than guessing.
- The web learning trail must offer both current-paper and whole-database JSONL exports, because later DeepSeek extraction uses the whole reviewed sample pool as guidance.
- The web learning trail must show a human-readable learning report and prompt-guidance preview. The preview must match the guidance generator used by DeepSeek extraction.
- Every automatic row must retain a source page, locator, excerpt, and highlighted source-view path.
- DeepSeek extraction may include human review samples as prompt guidance for field boundaries and Chinese wording style. These samples are never evidence: every accepted row must still be supported by the current PDF page text, local numeric/page checks, and independent verification.
- The manual-entry page must provide its own registered-paper selector. Saving a manual row writes to that selected paper, does not switch the review page's current article, and must never call DeepSeek.

### Search rules

- Search is free text, not tag selection.
- Rank `meaning` highest (weight 6), followed one level lower by `context_explanation` (weight 5), then value, unit, title, DOI, first/corresponding author, and source excerpt.
- Support partial and fuzzy scientific terms, including alloy names, temperatures, doses, particles, measurement names, and common element Chinese-name/symbol/English aliases such as `钨` / `W` / `tungsten`.
- CSV and Excel export must reuse the current query and filters. The browser may
  render only the first 100 matches for responsiveness, but it must state the
  full count and clearly label exports as containing all matches.
- Ordinary search and export must exclude rows whose current review action is `rejected` or `ambiguous`. Pending automatic candidates remain searchable but must be visibly labelled `待审核`; confirmed, corrected, and manual rows must also expose their review state.
- The review page paper switcher must be a selectable list of registered papers and should appear only on the review page. The visible option text should be paper title plus helpful bibliographic context such as first author, year/DOI, and saved-row count; do not make Zotero/storage codes the displayed selector. Use the internal paper id for switching.
- Article-picker research-object and method tags are deterministic, conservative, non-exclusive navigation aids. Do not write them back as scientific truth or use them to override the extraction classifier. Filtering must never switch the current article or trigger DeepSeek.
- Search first and corresponding authors only when those fields exist in the evidence database. Show missing author metadata instead of guessing it from filenames or titles.
- Keep the fixed five-paper validation set in `config/evidence_test_set_5.json`. Every member must resolve to a registered paper and a real local PDF whose fingerprint still matches the database. Run `evidence-test-set-audit` after changes to source evidence, article navigation, calibration review, or PDF association, and keep its reports under `data/evidence/test_sets/`.
- The search page must search the whole six-column database by default, independent of the currently selected paper. Empty search/export from the search page must also use the whole database.
- The review page should provide direct downloads for both the next unreviewed Markdown checklist and all remaining unreviewed rows via `/api/current-paper/review-batch.md`. These are read-only manual-review aids and must not call DeepSeek or write database rows.
- The same review-batch endpoint accepts `strategy=calibration`. Calibration selection must deterministically spread a small batch across source kinds, locator forms, value shapes, provisional candidate roles, semantic families, pages, and review priorities. These facets are sampling hints only: they must never mutate six-column data or be presented as a human-confirmed evidence classification. Keep the ordinary priority-ordered batch available separately.
- The review page must also expose the calibration batch as an in-page review mode through `/api/current-paper/review-batch`. It should show only the selected unreviewed rows, keep confirm-and-next inside the active batch, report the remaining batch count, and allow leaving without writing unconfirmed edits. The JSON endpoint is local-editor functionality and must remain unavailable from public read-only mode.
- Preserve an unfinished in-page calibration batch per paper in browser-local storage. A refresh, temporary exit, or paper switch must offer to continue the same item IDs instead of silently replacing the sample. Store only paper/item identifiers, never row contents or credentials; completing the batch must clear the saved marker. Resume behavior must not confirm, edit, or otherwise mutate database rows.
- Every review state on an automatic row must be reversible. `confirmation`, `correction`, `rejected`, and `ambiguous` rows must be able to return to `automatic`/pending through an explicit confirmation. Reopening appends a version, preserves the immutable version-0 extraction and every review-history version, and removes the row from current learning guidance until the researcher reviews it again. Manual rows are not eligible.
- Keep reviewer rationale separate from the six scientific data fields. The optional note beside the immutable source is transient until confirmation, must participate in the unsaved-edit guard, and must be stored only as the new version's `edit_note`. Include a bounded rationale in learning guidance for confirmations and corrections, under the same rule that historical values or claims cannot become evidence for another PDF.
- The review page should keep the human-review progress visible near the table, including reviewed/unreviewed totals and confirmation/correction/manual/rejected/ambiguous counts. This is a review aid only and must not mutate extracted rows.
- Each automatic row should expose a direct row-level source-evidence button that calls the existing highlighted source viewer. Manual rows should not pretend to have automatic source evidence.
- `/api/papers` should expose per-paper six-column workflow state and label so the review article picker can act as a work queue. Use `not_scanned`, `scanned_empty`, `pending_review`, and `reviewed`; labels should be reader-facing Chinese such as `未扫描` or `待审核 112/114`.
- After any confirmation, correction, rejection, ambiguity decision, restoration, or manual entry, the web UI should refresh paper workflow labels, current/all learning reports, evidence audit, and extraction status so the researcher can immediately see how the human review sample changes the next extraction guidance.

### Visual evidence search contract

The search workspace has exactly three user-facing modes: `数据条目`,
`原始表格`, and `论文图片`. They are three views over the same evidence database,
not separately maintained applications. Read-only sharing must use the same
frontend and database as the local editor and may expose the public visual search,
visual metadata, and visual image routes without exposing mutation APIs.

Schema version 8 adds `visual_assets` and `data_item_visual_links`:

- `visual_assets` stores one paper-level table or figure object, its label,
  caption, PDF page, crop coordinates, rendered image path and checksum, source
  PDF fingerprint, searchable tags, quantities, variables, materials,
  conditions, methods, and source-grounded explanation.
- `data_item_visual_links` connects six-column data rows to their primary or
  supporting table/figure. A link enriches provenance; it must never rewrite,
  merge, confirm, or reject the underlying six-column record.
- Keep `source_kind` distinct as `text`, `table`, `text_with_figure`, or
  `figure_only`. Do not classify a calculated or qualitative result as a direct
  measurement merely because it is linked to a figure.

Table and figure display must use high-resolution crops rendered from the
authoritative local PDF, with page and PDF fingerprint retained for audit. Do
not redraw the original table, use OCR text as a substitute for the original
view, or infer exact data points from graph pixels. Generic caption/image
detection may create pending visual objects for other readable PDFs; ambiguous
crops or compound-panel boundaries require human checking.

Treat the search page as an evidence index rather than a generic card grid. Its
visual hierarchy is: reported value -> physical meaning -> experiment context ->
source excerpt -> paper identity and evidence actions. Keep the deep navy,
paper-white and beam-orange palette consistent across editable and read-only
modes. Suggestions, review/source filters, result ordering, local recent
searches, keyword highlighting and the `/` focus shortcut are presentation
features only and must never write to SQLite.

In `数据条目`, records linked to the same table are grouped and show the first
three rows by default. The remaining rows are available through a native expand
control. This is display-only collapsing: result counts, exports, review state,
and row identity must remain unchanged. Exact queries such as `Table 3` or
`Figure 8` should resolve through visual links rather than broad same-page
matching.

Use `evidence-index-visuals <paper-selector>` to create or refresh visual assets.
The stable public routes are `/api/visual-search`,
`/api/visual-assets/{id}`, and `/api/visual-assets/{id}/image`. Health checks
must require schema version 8, the visual indexes, and the existence of every
recorded image file.

### Current verified baseline

As of 2026-07-13:

- Target article rows: 114
- Rows with PDF highlight localization: 114/114
- Indexed visual evidence: 4 tables and 10 figures from PDF pages 2-9
- Sentence or fragment-level strong localization: 109/114
- Test command: `PYTHONPATH=src python3 -m unittest src/tests/test_evidence.py`
- The 114-row target baseline remains article-specific and human review is still incomplete. B2 can now process other readable PDFs through DeepSeek, but do not claim general scientific accuracy until the benchmark and human-review metrics support it.

### Git checkpoint protocol

After each meaningful implementation or verified data-review milestone:

1. Run the evidence tests and relevant live workflow checks.
2. Commit the whole project state, including `db/experimental_evidence.sqlite` and tracked extraction outputs.
3. Create a descriptive local milestone tag using the `evidence-demo-YYYY-MM-DD-<slug>` pattern.
4. Create and verify a complete Git bundle outside the repository:

```bash
git bundle create /Users/USER/Zotero/auto-research-git-backups/auto-research-YYYYMMDD-<commit>.bundle --all
git bundle verify /Users/USER/Zotero/auto-research-git-backups/auto-research-YYYYMMDD-<commit>.bundle
```

5. Confirm `git status --short` is empty after the final checkpoint. Do not copy Zotero PDF storage into Git; the evidence database stores the authoritative absolute PDF path and fingerprint.

### B1 PDF intake and duplicate control

The local review UI now owns a PDF intake boundary at `POST /api/uploads/pdf`. Uploaded files must open as real PDFs before they are accepted. Project-managed uploaded PDFs live under the ignored `data/papers/evidence-uploads/` directory; Zotero PDFs remain in Zotero storage and are indexed in place without copying or modifying Zotero.

Deduplication runs in this order:

1. exact PDF SHA-256;
2. normalized DOI;
3. fuzzy normalized title with compatible year/first author;
4. normalized full-text hash, then bottom-k text-sketch similarity.

An exact file is not saved twice. A non-identical PDF that matches an existing paper is stored as an `alternate` document and creates a blocked `duplicate_review` job; it must not create another extraction job. A genuinely new readable PDF creates one `extract` job. A text-poor but readable PDF creates an `ocr` job. Upload attempts are audited in `upload_events`.

Runtime AI in the released project is DeepSeek-only. Codex is for project development, not an application dependency. Configuration comes only from environment variables documented in `.env.example`; never store or display a real API key. Until `DEEPSEEK_API_KEY` is configured, B1 upload, validation, deduplication, queueing, review, and search must continue to work, while DeepSeek jobs remain queued.

On this Mac, the project-specific credential may instead be stored in macOS Keychain under service `auto-research-deepseek` and the current macOS account. `DeepSeekSettings` checks `DEEPSEEK_API_KEY` first and this project Keychain entry second. Never print the credential, return it through `/api/ai/status`, reuse it in another project, or place it in a Git-tracked file.

Treat DeepSeek as an unreliable external dependency. The runtime may retry one
transient network error, HTTP 429, or 5xx response once, but must not include the
API key or remote response body in raised errors. Parse
`DEEPSEEK_TIMEOUT_SECONDS` defensively and keep it within 10–1800 seconds so a
malformed local environment file cannot prevent the web application from
starting.

### B2 DeepSeek evidence extraction

`DeepSeekEvidenceExtractor` is the only runtime path for new AI extraction. It processes the real local PDF in two-page blocks and uses two complementary extraction passes per block: methods/materials/conditions/tables, then results/calculations/observations. Candidates must then pass all gates:

1. constrained field and enum validation;
2. source page belongs to the supplied block;
3. numeric anchors and verbatim/table evidence can be found on that PDF page;
4. candidates admitting `assumed`, `implied`, `possibly`, or background-literature provenance are rejected;
5. an independent DeepSeek verification pass supports value, meaning, context relation, page, and excerpt;
6. repeated semantic candidates are deduplicated before preview/import.

Every run is audited in `ai_extraction_runs` and writes a short-evidence JSON artifact under `data/evidence/deepseek_runs/`. A failed or interrupted run must remain `failed`; partial counts are progress only, never publishable data. Existing rows require explicit `force_rescan` before a new DeepSeek call and then produce non-overwriting preview/candidate output. `commit` is permitted only for a paper with no six-column rows, and imported results remain unreviewed version-0 candidates.

Primary command:

```bash
auto-research evidence-deepseek-extract <paper-selector>
```

### Extraction benchmark

Use `evidence-benchmark <paper-selector> --run-id <id>` to compare a saved
DeepSeek artifact with the current six-column baseline without making a model
call or modifying review rows. The evaluator must use deterministic one-to-one
matching and require an identity signal from source text, locator, meaning, or
experimental scope; value + unit + page alone is insufficient because scientific
tables frequently repeat them.

Keep the language conservative while any baseline row remains unreviewed:
`candidate_agreement_rate` and `baseline_coverage_rate` are provisional baseline
comparison metrics, not scientific accuracy/precision/recall. Treat unmatched
candidates as pending human decisions rather than false positives. Preserve the
JSON and Chinese Markdown reports under `data/evidence/benchmarks/` so later
prompt, gate, and deduplication changes can be compared against the same run.
Historical replay must preserve the original artifact, re-run only deterministic
local gates and deduplication, and report raw, rejected, merged, and retained
counts separately. A compound observation containing distinct physical findings
must be split into atomic rows; do not accept a single row such as “loops present,
no voids”. Do not discard a PDF page merely because its lower half contains many
references when its upper portion still contains results, discussion, formulas,
or experimental conditions.
Deduplication must never use value/unit similarity alone. Preserve equal values
when their physical meanings, material scopes, or experiment types differ (for
example, 300 °C annealing versus 300 °C irradiation), and keep merged candidate
ids on the retained row for audit.
Keep the full experiment-type score list for diagnosis, but build extraction
focuses only from title-supported or threshold-selected types. Combine selected
type-specific concerns into one targeted pass so a page block uses the general
methods pass, general results pass, and at most one targeted pass. Follow those
with one inventory-aware coverage-gap audit that must return only omitted atomic
evidence. Incidental low-score terms must not create separate DeepSeek scans. A
gap-audit API failure becomes a pending retry task and must not invalidate the
already verified candidates.

Do not automatically treat the latest completed run as the best run: DeepSeek
recall varies between otherwise identical previews. `evidence-ensemble-preview`
may retain an explicitly chosen primary run and add only candidates carrying the
requested supplemental focus (normally `coverage_gap_audit`). The ensemble must
re-run local gates and deduplication, preserve source-run provenance, remain
non-overwriting, and report zero database row changes.
The CLI accepts repeated `--supplemental-focus` options. Prefer narrow aliases:
`coverage_gap_audit` for omitted atomic data and `qualitative_results` for
explicit observations from a results pass. Use `composition_table` to select
only candidates whose material, element, at% unit, and nominal/measured identity
are explicit. `--supplemental-run-focus RUN_ID:FOCUS` may assign different
selectors to different supplemental runs. Broader aliases (`results`,
`methods`, `targeted`, `all`) are diagnostic options and should be retained only
when their added coverage justifies the extra review candidates. Benchmark
comparison may treat standalone English number words such as `five` as numeric
equivalents, but it must never rewrite the candidate's source-preserving value.

The coverage-gap inventory must include meaning, material/context, page,
locator, and a bounded source excerpt. Equal values do not represent the same
datum when material, element, specimen state, or nominal/measured role differs.
Also supply a bounded per-page quantity-anchor checklist built from exact PDF
text lines so overlooked method quantities written as words, tolerances, or
spacing values receive an explicit audit. Anchors never override local evidence
checks and must not be used to infer precise curve points.

AI-run audit writes must tolerate concurrent readers. Keep SQLite busy waiting
enabled and use bounded retries for run creation, progress, completion, and
failure updates. A provider/network outage is not a dense-output error: propagate
`DeepSeekUnavailableError` without page/focus splitting, while malformed JSON
may still use the controlled extraction fallback.

Uncertainty belongs to its central value (`3.56±0.05`, not a second `0.05`
datum). If the source includes an uncertainty, rejecting a candidate that drops
it is mandatory. A nominal/measured table pair must become two rows with distinct
meanings. Simple scalar assignments such as `ΔH_mix = -7.27` should store only
`-7.27` in value_text and keep the variable identity in meaning.

### Five-paper blind validation (2026-07-08)

The first cross-paper blind validation is recorded in
`data/evidence/random5_20260708_manifest.csv` and
`data/evidence/random5_20260708_audit.md`. The fixed random seed was `20260708`.
The five user-facing local article keys are `MZNUIZ2E`, `FDEA83QY`, `L6R9RV5J`,
`7ZZTR5LB`, and `4RNGZVIX`. They currently expose 688 unreviewed automatic rows;
all 688 can be highlighted in their authoritative local Zotero PDFs.

Blind-test reliability rules are mandatory for later papers:

- verify candidates in batches of no more than 20 so verifier JSON is not truncated;
- retry a failed two-page extraction one page at a time;
- if a single dense page still fails, split it into narrow condition/result/calculation/trend slices;
- skip only bibliography-dominant pages, never正文、tables、figure captions, or supplements;
- reject values explicitly attributed to another `Ref`, `reference`, or `literature` source, but do not confuse crystallographic directions such as `[001]` with citations;
- write `meaning` and `context_explanation` in Chinese while preserving source-language excerpts and every numeric condition;
- localization is a retrieval aid, not an evidence gate: a localization failure keeps the evidence-grounded English fields instead of failing the extraction.

The five-paper results remain version-0 candidates. Do not describe their
physical interpretation as human-confirmed until the researcher reviews them.

### Human-review priority queue

The six-column review UI may rank unreviewed rows by deterministic provenance
signals: page-only or weak highlighting, figure/trend locators, approximate or
qualitative value wording, and calculated/derived wording. The ranking is a
triage aid only. It must never mutate the six editable fields, suppress rows,
or label a scientific datum as incorrect. The UI must explain that high
priority means "review first", not "known error".

`audit_six_column_evidence()` is the authoritative source for
`review_priority_rows` and `review_priority_counts`. The web table, the
"只看重点项" filter, and Markdown review-batch downloads must use the same
priority output. Keep ordinary item order available as a selectable fallback.
Local editable mode must hide the read-only badge; preserve the explicit
`.readonly-badge[hidden]` CSS rule because the badge's flex styling otherwise
overrides the HTML `hidden` attribute.

### Review UI hierarchy

The review table and immutable source pane are the primary workspace. Keep the
article selector visible, but place low-frequency extraction, export, AI-run,
experiment-profile, and diagnostic status content inside the collapsed
`#article-tools` disclosure. Do not let status text push the review table below
the first viewport again.

`#focus-review` is a presentation-only mode: it hides surrounding navigation
and article controls, expands the table/source split, and must never confirm,
save, call DeepSeek, or mutate database state. Escape and the visible exit
button must both leave focus mode. Selecting the first visible review row on
load or after a filter change is allowed because selection is not persistence;
the original pane should immediately show that row.

On narrow screens, the original pane must use normal document flow below the
editable table. Do not restore the old fixed overlay, because automatic row
selection would cover the article selector and review controls. Page headings
must follow the active view through `renderViewHeader()` so users can tell
whether they are reviewing, searching, uploading, manually entering, or
inspecting history.

### Read-only public sharing

The project now supports a share-safe read-only UI mode via:

```bash
PYTHONPATH=src python3 -m auto_research.cli evidence-serve --host 127.0.0.1 --port 8766 --read-only
```

This is not a second app or a copied database. It is the same web UI and the same
`db/experimental_evidence.sqlite`, started with server-side write protection.
Do not create or maintain a separate public HTML/JavaScript implementation. Any
search-card, export, terminology, or source-evidence fix must be made once in the
shared frontend and verified in both modes.
Starting either server must not seed demo rows, update paper timestamps, switch
the current article, or call DeepSeek. Editable startup may initialize a missing
schema and index genuinely new managed PDFs. Read-only startup must skip document
indexing entirely; an initialized database must remain byte-stable while the
shared service is opened and viewed.
When `read_only` is enabled, every POST request is rejected before route-specific
logic runs. The shared read-only server is search-only: GET routes are limited to
the static app, `/api/ui-mode`, whole-database search/export, row-level source
evidence images/metadata, and source PDF opening. Do not expose current-paper,
paper-list, upload queue, learning samples, review, or extraction endpoints in
public read-only mode. The frontend should hide every non-search navigation item
and default to the whole-database search view.

The public source-evidence button must open `/source-view` directly from the
search result row. It must not prefetch `/api/six-data/<id>`, because that private
detail route intentionally remains unavailable in search-only mode. Verify the
snippet PNG, full-page highlight PNG, and PDF URL in an actual read-only browser.

In the editable review table, avoid a narrow single-column stack of controls that
artificially stretches every data row. Keep data text at a readable size, auto-fit
textarea height to content within a bounded range, and arrange routine actions in
a compact multi-column rail so row height is driven primarily by evidence text.

Use this mode for any public tunnel or external preview URL. Do not expose the
editable `8765` workbench through a public tunnel. The current recommended
free-account sharing path is:

```bash
./scripts/start_readonly_ngrok.command
```

The script reads `NGROK_AUTHTOKEN` from the environment or from the local
`.env.ngrok` file, including the user's convenience location
`/Users/USER/Zotero/.env.ngrok`. `.env.ngrok` is ignored by Git and must
never be committed. Export the token only through the child-process environment;
never pass it as an `--authtoken` command argument where it can appear in the
system process list. The script must verify `/api/ui-mode` before starting ngrok
so a public URL is never pointed at the editable workbench by accident. It
should also reuse/report an already-running ngrok tunnel for port `8766` instead
of starting a duplicate tunnel.

Temporary no-account/no-domain fallbacks may be tried if ngrok is unavailable,
but they have already proven unreliable on the current network:

```bash
npx --yes localtunnel --port 8766 --local-host 127.0.0.1
cloudflared tunnel --url http://127.0.0.1:8766
```

The tunnel URL is suitable for a short external review session while the local Mac
and both terminal processes remain running. For persistent group deployment,
prefer a GitHub Pages static read-only snapshot, a named Cloudflare Tunnel, or a
proper hosted read-only deployment after the user explicitly approves that next
step. GitHub Pages must be treated as a static export target only: do not expect
it to run DeepSeek extraction, upload PDFs, or mutate SQLite.

### Maintenance acceptance

Before a maintenance checkpoint, run the full unit suite, `evidence-db-health`,
and the target-paper `evidence-self-check`. Database health must cover SQLite
integrity, foreign keys, required indexes and fields, current-row stable-key
uniqueness, stale running AI jobs, and missing artifacts for completed AI runs.
Historical failed runs are audit history and should be reported without making
the database unhealthy. Also validate frontend syntax, macOS launcher syntax,
Python compilation, installed dependency consistency, Git object integrity, and
whitespace errors before committing and bundling the checkpoint.

### Article navigation and large-review performance

The review-page article picker is a navigation aid, not an extraction trigger.
Topic tags are conservative and non-exclusive; title/DOI search, first- or
corresponding-author search, processing status, fixed test-set scope, and recent
articles may narrow the dropdown, but none may switch the paper until the user
submits the switch form. Filtering must never call DeepSeek, scan a PDF, or write
the database. Prefer title, DOI, author and year in user-facing labels; keep the
device-specific Zotero key as legacy internal metadata only.

Treat `all`, `test_set`, and `recent` as article-collection presets, not ordinary
stackable filters. Selecting any collection must clear title, author, topic and
workflow-status filters before rendering the picker. The fixed five-paper preset
must therefore show exactly five papers whenever its configuration resolves;
`all` must show every registered paper. Label the topic reset as "全部方向" so it
cannot be mistaken for the all-articles preset.

Do not render hundreds of six-column textarea rows on initial load. Keep the
review table chunked at 80 rows and expand it only through the visible control or
an explicit next-item navigation. Full-paper evidence auditing is a background
article-level operation. A row confirmation, correction, negative decision or
reopen action must not synchronously recompute the full evidence audit.

### Numeric data rows and visual provenance

`value_text` in the searchable six-column item dataset must be a reportable
numeric expression or an explicit table marker such as `bal.` or `n/a`.
Material names, methods, facilities, conditions and qualitative prose belong in
`meaning`, `context_explanation`, evidence metadata or visual-asset metadata;
they must not become pure-text values. Preserve legacy rows for audit, hide them
from user-facing item search, and require the reviewer to correct them to a
numeric datum or mark them as not used. Do not delete history to enforce this
rule.

All user-facing current-data surfaces must use
`list_reportable_current_data()`: review rows, calibration batches, search,
current-paper CSV/XLSX export, paper-picker counts, evidence audits and fixed
test-set audits. `list_current_data()` is the raw provenance/history layer and
must not be returned directly as the current experimental dataset. A value with
digits embedded in narrative prose is still non-reportable; compact ranges,
inequalities, scientific notation, alloy formulas, units and explicit table
markers remain valid. Database health must report raw, reportable and excluded
counts without deleting the excluded history.

Visual assets are authoritative PDF screenshots created locally with PyMuPDF
layout detection and recorded as `extraction_method=pdf_layout`. The current
DeepSeek adapter receives extracted text for semantic candidate generation; it
does not receive figure pixels. Never claim that DeepSeek or GPT visually read,
cropped or digitized these screenshots. Do not infer curve points. Every fixed
five-paper test-set member must have at least one visual asset, and the audit
must report per-paper figure/table counts and the generation method.
