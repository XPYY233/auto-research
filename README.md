# Auto Research

Local, resumable literature automation for fusion materials, radiation damage, cascade simulations, MLIP, and HEA/RHEA research.

## Project handoff and project skill

Before continuing this repository from a new Codex account or a new engineering session, read [`PROJECT_HANDOFF.md`](PROJECT_HANDOFF.md). The project-local, portable Codex skill is [`skills/auto-research-evidence-maintainer`](skills/auto-research-evidence-maintainer/SKILL.md); it records the task routing, scientific guardrails, current state, workflows, operations, recovery points, and rejected paths without depending on prior conversation memory.

Install or link the skill for the current macOS account with:

```bash
mkdir -p ~/.codex/skills
ln -s /Users/USER/Zotero/auto-research/skills/auto-research-evidence-maintainer \
  ~/.codex/skills/auto-research-evidence-maintainer
```

The older `auto-research-zotero` skill remains specific to lawful PDF acquisition and Zotero corpus maintenance; use the new evidence-maintainer skill for the desktop workbench product, extraction database, quality gate, visuals, search, Librarian, release, and handoff work.

Last built desktop checkpoint: **Auto Research `0.4.0-preview.1` for Apple Silicon macOS**, artifact-tagged `evidence-demo-2026-08-01-macos-workbench-preview-3` from `f34bf68`. It is an internal development preview, not a portable public release: the editable scientific workspace still depends on this checkout and the schema-v12 database. That release worktree passed 591 automated tests (399 shared core, 110 macOS and 82 Windows), isolated App smoke, signing, DMG verification and a real launch/health/clean-shutdown check.

Current source checkpoint is `64e0592`. It contains Librarian V3 shared-UI transport, official/private federated exact search, the reviewed private-table import gate and the shared offline/private workflow added after the last App/DMG build. Windows backend parity is recorded at `a134acd`, but `installer_ready=false`: there is no Setup and no Windows 11 clean-machine acceptance. Source readiness must not be reported as a newly built desktop release.

The scientific corpus has not become complete merely because the product build is stable: it remains 17/50 data-ready and 30/50 visual-ready. The schema-v12 baseline contains 6,501 raw rows, 5,048 reportable numeric occurrences, 3,142 independent physical facts, 937 qualitative findings and 291 visual assets. The 243-asset count is the preserved historical pre-cloud freeze rather than the current total.

The first internal `.aresearch` package is a separate, signed, read-only distribution artifact: 2.36 MB, SHA-256 `73672f94335604609d729671ab4a950e361b8cb569523a18980f0112c7c9f91d`, with 60 paper metadata records and 4,356 entities (3,142 item, 936 finding, 46 table and 232 figure). It contains no PDFs or binary images. Importing it does not replace or write the editable v12 workspace.

## Product entry point

As of 2026-08-01, the only supported product shape is a **personal desktop research workbench**. The current development preview is Auto Research.app on macOS; the intended end-user target is Windows. The preview must open directly without an Auto Research edit password. The HTML/JavaScript frontend and loopback Python service remain internal App components; users are not expected to start or visit localhost ports.

The former browser workbench, mentor read-only page, ports `8765`/`8766`, ngrok tunnel and their launchers are retired historical compatibility paths. They are retained only long enough to support rollback, migration and automated permission tests; they are not release, sharing or daily-use entry points.

The intended distribution is the desktop App plus a separately supplied, signed and hashed `.aresearch` evidence package. Import enables offline search; a user's own PDFs and extraction results stay in a separate private library. DeepSeek extraction and Librarian use the user's own API key, stored outside the database and evidence package. The ad-hoc-signed macOS preview deliberately uses Application Support private files with a separate AES-256-GCM key to avoid a misleading Keychain password prompt; a formally signed macOS release must use Keychain, and Windows uses Credential Manager. The complete cross-platform and non-technical onboarding contract is in [`docs/DESKTOP_PRODUCT_AND_EVIDENCE_PACKAGE.md`](docs/DESKTOP_PRODUCT_AND_EVIDENCE_PACKAGE.md).

The shared source UI separates **本地文献工作区** from **离线资料库**. Offline exact search selects official literature, private experiments, or both and makes one federated backend request; it never merges two client-side result lists. Both sources reuse only `item`, `table`, `figure`, and `finding`. Librarian V3 remains official-literature-only and never synthesizes private experiment values.

Private CSV/TSV/XLSX import uses the desktop native picker and the fixed `previewed → draft_saved → confirmed/indexable` flow. Users must review project/sample/run metadata and every included column's role, meaning, and unit. Confirmation binds the latest draft revision to the reviewed revision; editing the draft invalidates the prior review. Renderer-facing cards and errors contain no local path, file hash, internal database ID, import ID, or draft ID. Trend-image attachment is a future capability, not a current UI feature.

It prioritizes open/official sources and your lawful local access path. It does **not** bypass paywalls, crack captchas, use proxy pools, or impersonate institutional access.

## Quick start

```bash
cd auto-research
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
auto-research init
auto-research discover "MLIP cascade HEA radiation damage" --limit 50
auto-research acquire --limit 20
auto-research parse --limit 20
auto-research analyze --limit 20
auto-research matrix
```

Outputs:

- SQLite state DB: `db/research.sqlite`
- PDFs: `data/pdf/`
- extracted text + JSON profiles: `data/papers/`
- research cards: `data/reports/`
- matrices: `data/matrix/`

## Human handoff

When a paper requires login, captcha, subscription confirmation, or manual Zotero/Browser handling, its job state becomes `needs_login`, `needs_captcha`, `needs_subscription`, or `needs_human`. After you resolve it and place a PDF path with `auto-research attach-pdf`, the pipeline resumes.

## Main commands

```bash
# Discover domain papers from OpenAlex/Crossref/arXiv/OSTI plus Unpaywall DOI enrichment
auto-research discover "MLIP cascade HEA radiation damage" --limit 100

# Try legal/open PDF candidates first
auto-research acquire --limit 50

# If a paper needs login/captcha/manual download, open it and attach the PDF afterward
auto-research open 12
auto-research browser-acquire 12 --wait 120
auto-research attach-pdf 12 /absolute/path/to/downloaded.pdf

# Continue the pipeline
auto-research parse --limit 50
auto-research analyze --limit 50
auto-research matrix

# Zotero bridge
auto-research zotero-status
auto-research zotero-export
```

## State machine

Normal path:

```text
discovered -> source_candidates_found -> downloading -> downloaded -> parsed -> analyzed -> archived_to_zotero
```

Handled exception states:

```text
needs_login
needs_human
needs_captcha
needs_subscription
permission_denied
no_fulltext_found
parse_failed
duplicate
```

## Research-specific extraction

The first implementation uses deterministic extraction so it can run locally without an API key. It extracts:

- materials and HEA/RHEA/CCA indicators
- MLIP family: GAP, MTP, SNAP, DP, NEP, MACE
- cascade / PKA / radiation-damage indicators
- experimental methods: ion/neutron irradiation, TEM, APT, nanoindentation
- simulation methods: DFT, MD, LAMMPS, GPUMD, VASP
- parameters such as temperature, dose, PKA energy, and box size when explicitly reported

A later LLM-backed extractor can replace `src/auto_research/analysis/extractor.py` while preserving the same JSON profile schema and matrix generator.

## Authenticity verification is mandatory

Before a paper is trusted in cards/matrices, run:

```bash
auto-research verify --limit 200
```

The verifier records `authenticity_status`, `authenticity_score`, and detailed evidence in SQLite. It checks:

- DOI syntax and DOI resolution through Crossref/OpenAlex
- title/year agreement against trusted registries
- trusted discovery/acquisition provenance
- PDF integrity and rough title consistency when a PDF is attached

Statuses:

- `verified`: strong registry/provenance evidence
- `likely_real`: trustworthy but one or more checks are incomplete
- `needs_review`: do not rely on it before manual inspection

The review matrices include authenticity columns, and the gap summary only promotes verified/likely-real papers as promising papers.

## Experimental-evidence database

The evidence database is deliberately separate from Zotero and from `db/research.sqlite`.
Zotero remains the source of papers/PDFs; every publishable value must retain a
page or table/figure locator and pass the automatic adversarial quality gate.
Human checking remains an optional correction and calibration path, not a
mandatory per-row publication step.

The active stable visual pipeline uses the original local PDF screenshots and
search index. DeepSeek enriches captions and nearby extracted text only. The
discarded MinerU/cloud-visual experiment is not part of the current database,
search results, interface or runtime configuration.

The current evidence workflow first classifies what kind of experiment the paper
contains, then chooses the extraction focus accordingly. Irradiation remains the
first mature pilot type, but it is no longer the hard-coded project boundary.
The classifier currently recognizes irradiation, mechanical testing,
microscopy/characterization, thermal measurement, electrical transport,
spectroscopy, electrochemical/corrosion testing, processing experiments, and
magnetic measurements.

The review page shows this classification before extraction/review. Prompt
packets generated without a configured DeepSeek runtime also include the
detected `experiment_profile` and `extraction_foci`, so the fallback path follows
the same “identify experiment type first” contract.

```bash
# Create the balanced 30-paper HEA/RHEA + tungsten pilot and import the
# six-paper/108-record benchmark as drafts
auto-research evidence-seed

# Locally prepare relevant-page packets for all 30 papers
auto-research evidence-prepare-pilot

# Validate the publication gates and pilot balance
auto-research evidence-validate

# Maintainer-only internal service check; normal users open Auto Research.app
auto-research evidence-serve
```

Normal users open **Auto Research.app**. `evidence-serve` and the loopback URL are
maintainer diagnostics used internally by the App and test suite. The former
`打开本地编辑工作台.command` and `创建导师公网链接.command` files are migration
notices only and must not silently start a browser service or public tunnel.

The local literature workspace opens with Librarian V3. Research questions search the full official
evidence library and return the existing four result types: numeric items,
tables, figures and qualitative findings. Use precise search when a query must
be limited by paper title, DOI or author, or when searching private experiments. System-capability questions and ordinary conversation are answered locally with zero evidence recall and zero model calls. The current ad-hoc macOS preview
persists Librarian history with AES-GCM and a separate private key under
Application Support; a formally signed release switches that key to macOS
Keychain. Historical browser-local
and `readonly-none` adapters remain compatibility/test boundaries, not product
modes. No history mode writes the scientific evidence database, and reopening a saved
conversation does not call the model. The desktop product is a separate
packaging boundary; this core checkpoint includes the shared-frontend adapter
contract but does not claim that `desktop/macos/**` source is published in the
core release commit. Research-brief authorization is transient only and is not
stored in session meta/messages, `localStorage`, or desktop encrypted history.
Each research turn uses the V3 local intent router. Focused lookup and broad review may use DeepSeek to plan bounded short queries, then run a
coverage search over all four official evidence types and apply deterministic
hard-condition matching for material, irradiation, particle, temperature,
dose/fluence, property and specimen state. DeepSeek plans queries, selects
bounded evidence and explains it in Chinese; it cannot create or rewrite hard
conditions or promote partially matching records to direct evidence. Scientific
notation and equivalent fluence units retain their exponent semantics, while
energy/temperature and particle/material roles remain distinct. Candidates are
classified locally as `direct`, `adjacent` or `expansion` and grouped into
paper/material/condition evidence bundles. The answer is shown as a five-part research report: direct conclusion,
evidence matrix, related evidence with the relaxed condition, database gaps and
two-to-three clickable follow-up questions. The page reports both the total
recalled candidates and the smaller set cited by the report; cited cards and
their evidence bundles remain openable through the normal evidence-detail
interface. Internal DeepSeek tool protocols are never shown as answers.
Search V2, visual detail and Librarian routes use one public DTO projection that
omits local filesystem paths, Zotero/local keys, reviewer identities and internal notes.
An identical question with identical bounded history reuses the complete answer
and candidate set for one hour while the database source fingerprint remains
unchanged. The UI labels this reuse; any evidence change invalidates it.

After a structured Librarian answer is complete, choose **导出研究简报** to
download `librarian-research-brief.md`. Export is enabled only for the latest
non-clarification answer that has at least one actual citation and carries a
valid current-process HMAC token over the public response snapshot. The server
also requires every canonical R# to map uniquely to an `agent_cited=true`
`item/finding/table/figure` result with consistent counts. Restored history does
not gain a new token; after a service restart, run the query again before
exporting. Missing title/DOI/page/excerpt remains empty, makes the brief a
visible provenance warning, and is listed in the Markdown.

The HMAC binds only the public snapshot, including its answer time and evidence
version. It does not create server-side history, call DeepSeek again, search the
database or PDF, read curve pixels, create cross-bundle quantitative
comparisons, or write scientific data. The endpoint accepts
`{snapshot, snapshot_token}` issued by the current process, not an arbitrary
snapshot, session id, database or PDF. See
[`docs/LIBRARIAN_RESEARCH_BRIEF.md`](docs/LIBRARIAN_RESEARCH_BRIEF.md) for the
signed-envelope contract, privacy whitelist, consistency gate and acceptance
boundary.

## Maintenance and release audit

Before a stable checkpoint, stop extraction batches and run:

```bash
auto-research evidence-reconcile-runs --older-than-hours 6
auto-research evidence-db-health
auto-research evidence-self-check 10.1016/j.jnucmat.2018.08.031 --query 温度 --query 硬度 --query 钨 --min-rows 100
auto-research evidence-test-set-audit --config config/evidence_test_set_50.json
```

The first command only closes abandoned run metadata; it never edits evidence
or graph/table files. See `MAINTENANCE_WORKFLOW.md` for the full release and
backup checklist.

The App uses the same evidence-detail component for all four search types.
After a user opens one numeric fact, table, or figure, the workbench presents a
two-column workspace: DeepSeek Pro evidence chat on the left and the selected
object's complete details on the right. The default question can be edited or
replaced. Chat receives only that object's structured fields and bounded text
pages from its source PDF, never the complete database, and it does not write
review or scientific-data state.

### Retired browser and public-link paths

The browser workbench, mentor read-only page, ngrok tunnel and GitHub Pages
snapshot are no longer supported product paths. Their server modes and permission
tests remain only as internal compatibility protection while the desktop App is
stabilized. Do not configure or share `8765`, `8766`, `.env.ngrok` or a localhost
URL with users. Future distribution should use the signed desktop App plus a
separately reviewed portable evidence package; it must not depend on this Mac
remaining online.

For maintenance, `evidence-db-health` checks the SQLite file, foreign keys,
six-column view and indexes, required fields, stable-key uniqueness, stale AI
runs, and missing JSON artifacts from completed DeepSeek runs:

```bash
auto-research evidence-db-health
```

DeepSeek transient network failures, HTTP 429, and service-side 5xx responses
are retried once. `DEEPSEEK_TIMEOUT_SECONDS` defaults safely to 180 seconds and
is constrained to 10–1800 seconds. Error messages never include the API key or
remote response body.

Additional commands:

```bash
# Generate an excerpt-only, schema-constrained packet for interactive AI extraction
auto-research evidence-prompt <paper-db-id>

# Classify the experiment type of a local evidence paper without calling DeepSeek
auto-research evidence-classify-experiment "10.1016/j.jnucmat.2018.08.031"

# Import returned JSON; all records remain drafts until human confirmation
auto-research evidence-import-ai <paper-db-id> result.json

# Export confirmed evidence (or explicitly include drafts)
auto-research evidence-export
auto-research evidence-export --include-drafts
auto-research evidence-export --measured-only
```

Artifacts:

- independent SQLite DB: `db/experimental_evidence.sqlite`
- pilot manifest: `data/matrix/irradiation_evidence_pilot_30.csv`
- prompt packets: `data/evidence/prompt_packets/`
- verified CSV export: `data/matrix/irradiation_evidence_verified.csv`

See `docs/irradiation_evidence_database.md` for the original irradiation-pilot
field guide; the current code now treats that guide as one mature experiment
type rather than the only supported scope.

### Six-column correction-learning demo

The current single-paper demo is intentionally simpler than the normalized
pilot schema. Each extracted datum has exactly six editable fields: value,
physical meaning, unit, article title, DOI, and contextual explanation. Search
ranks the specific physical meaning first and the contextual explanation one
level below it. The contextual explanation records the material, specimen,
experiment type, control variables, environment, and other conditions needed to
interpret the value.

DOI is preferred but optional. Older valid papers without a DOI remain
traceable by their article title and verified PDF fingerprint; an empty DOI does
not block numeric facts, qualitative findings, visual evidence, or review.

The canonical source is the final published PDF stored locally at:

`/Users/USER/Zotero/storage/XJZQ42XP/Chen 等 - 2018 - Irradiation effects in high entropy alloys and 316H stainless steel at 300 °C.pdf`

The similarly titled 18-page file is an accepted manuscript of the same paper.
It is useful for cross-checking scientific content, but its page numbers are
not used as evidence locators.

```bash
# Rebuild the immutable original extraction for the current target article
auto-research evidence-seed-target

# Maintainer-only internal service check
auto-research evidence-serve
```

Normal users open the desktop workbench and never need this loopback URL. In the
App, edits in the left table remain temporary until `确认当前内容` is pressed;
the immutable original remains visible on the right.
Confirmed corrections create a new version during the demo, and manual entries
have no synthetic original version. The original six-column export is written
to `data/extractions/XJZQ42XP_six_column_original.csv`.

For the first human-learning cycle, press `开始分层校准（20 条）` in the review
progress card. The browser selects a deterministic, representative subset from
the remaining unreviewed rows and keeps only those rows in the table. The subset
covers different source kinds, value shapes, provisional roles, semantic
families and PDF pages. Confirm-and-next stays inside that subset; leaving the
mode never changes unconfirmed rows. This is a sampling aid, not an automatic
accuracy judgment.

For current development-preview use, open Auto Research.app. The retired
`scripts/start_evidence_ui.command` remains only for maintainer rollback and must
not be documented as the Windows end-user path.

The review page has three explicit objects: `数据条目`, `原始表格`, and
`论文图片`. Table/figure cards show the immutable high-resolution PDF crop and
original caption beside editable search metadata. Confirm, correct, ambiguous,
rejected, and reopen decisions are appended as visual-review versions; they do
not rewrite the screenshot. A linked table or figure can also be opened from a
numeric data row.

Automatic processing indexes table/figure screenshots locally before sending
PDF text to DeepSeek. This preserves visual evidence even if the network or
model stage fails. DeepSeek currently receives the PDF text layer, not image
pixels: captions and nearby text may produce candidate metadata, but curves are
never digitized automatically. Caption-led cropping also supports old PDFs that
store each page as one raster image with an OCR text layer.

The current presentation-ready checkpoint, verified workflows, data counts,
known scientific-review boundary, and recovery instructions are recorded in
[`STABLE_RELEASE.md`](STABLE_RELEASE.md).

The whole-database search page now has four modes:

- `数据条目` searches independent physical facts. Repeated mentions of the same
  value under the same material and experimental conditions appear once, while
  every original sentence/table/figure location remains available as supporting
  evidence. Records linked to the same source table are then grouped for display.
- `原始表格` treats each original paper table as one searchable evidence object.
  Opening a result shows a high-resolution crop from the published PDF on the
  left and source-grounded quantities, variables, materials, conditions,
  methods, context, page, DOI, and related data rows on the right.
- `论文图片` uses the same evidence-object model for figures. It stores and
  searches the original figure image and its documented meaning, but never
  guesses precise curve points from pixels.
- `实验结论` searches source-grounded prose observations, trends and comparisons
  that cannot be represented as numeric rows. Methods, instruments, facilities
  and standalone condition labels are excluded from this collection.

`数据条目` additionally supports human-review status, evidence source and result
ordering filters. It performs live search after a short pause, offers example
queries and local recent searches, highlights matching text, and shows the
source excerpt directly in every result. The page renders at most the first 100
matches for responsiveness, states the complete match count, and exports all
matching records—not just the visible first page—to CSV or Excel with the same
query and filter conditions.

The editable and read-only services share the same HTML, JavaScript, CSS,
SQLite database, and visual assets. The read-only service exposes these four
search modes and the public evidence viewers without exposing review or
mutation APIs.

```bash
# Index or refresh table/figure evidence for one local article
auto-research evidence-index-visuals "10.1016/j.jnucmat.2018.08.031"
```

Rendered evidence crops are tracked under `data/evidence/visual_assets/`. The
database stores their PDF page, caption/label, crop coordinates, source PDF
fingerprint, search metadata, and links back to six-column records. Re-indexing
is deterministic and does not modify the six editable values or their review
history.

### B1 local PDF intake

The `上传文献` view validates a local PDF before accepting it, checks exact file,
DOI, fuzzy title/year/author, and normalized-text fingerprints, then creates a
processing job only for a genuinely new paper. Exact duplicates are not stored
again. Different PDF versions of the same paper are retained as alternate
documents and blocked for version review instead of being extracted twice.

Uploaded PDF binaries are stored under ignored `data/papers/evidence-uploads/`;
the SQLite audit records and processing queue are tracked. Existing Zotero PDFs
are indexed in place and Zotero itself is not modified.

Runtime AI is reserved for DeepSeek and uses BYOK. The product must ask each
user to configure their own key (or a key supplied specifically to them) before
the first extraction or Librarian request. It must explain the provider,
possible cost and bounded data sent. The current ad-hoc macOS preview stores the
key in a private AES-GCM Application Support store; a formally signed macOS
release uses Keychain, and the intended Windows build uses Credential Manager. A key is never
stored in SQLite, evidence packages, logs or Git. With no key configured,
package import, upload validation, deduplication, queueing, offline search and
human review still work normally.

Maintainers may use `DEEPSEEK_API_KEY` from the shell for isolated development
tests. `/api/ai/status` reports only whether a credential is available and never
returns it. A developer credential must never be bundled for end users.

```bash
# Redacted local configuration check (does not call the API)
auto-research evidence-deepseek-status

# Minimal synthetic JSON request; does not send any paper content
auto-research evidence-deepseek-smoke-test
```

### B2 evidence-grounded DeepSeek extraction

For a real local PDF, the runtime now performs two complementary extraction
passes over each two-page block, checks every candidate against the stated PDF
page, asks DeepSeek to independently verify the evidence relation, and then
deduplicates the supported candidates. An empty paper can import supported
candidates as unreviewed version 0. A paper that already has six-column rows or
a completed AI run is treated as scanned: command-line extraction refuses to
call DeepSeek again unless `--force-rescan` is passed.

```bash
# Safe preview; writes an audited JSON run without changing existing rows
auto-research evidence-deepseek-extract "10.1016/j.jnucmat.2018.08.031" --max-pages 10

# The paper selector may be a DOI, exact title, unique title fragment, paper id,
# or a legacy local/Zotero key.
auto-research evidence-run-article "Irradiation effects in high entropy alloys and 316H stainless steel at 300 C"

# Read-only acceptance check for a processed article and the review UI contract.
# This does not call DeepSeek, switch the current paper, or modify saved rows.
auto-research evidence-self-check "Irradiation effects in high entropy alloys and 316H stainless steel at 300 C" \
  --query 温度 --query 硬度 --query "Wei-Ying Chen" \
  --min-rows 100 --min-highlight-ratio 0.8

# Write a Markdown handoff for manual review: article status, review progress,
# shortcuts, local URLs, and the next verification steps.
auto-research evidence-review-handoff "Irradiation effects in high entropy alloys and 316H stainless steel at 300 C"

# Write a smaller checklist for the next N unreviewed rows.
auto-research evidence-review-batch "Irradiation effects in high entropy alloys and 316H stainless steel at 300 C" --limit 20

# Select a representative calibration set instead of 20 adjacent high-priority
# rows. This covers different source forms, value shapes, semantic families,
# PDF pages, and provisional measured/derived/calculated/qualitative roles.
# Sampling labels guide coverage only and never change the six-column records.
auto-research evidence-review-batch \
  "Irradiation effects in high entropy alloys and 316H stainless steel at 300 C" \
  --limit 20 --strategy calibration

# The review page also has "下载分层校准集", "下载待审核清单" and
# "下载全部待审核" buttons.
# They download Markdown checklists without writing database rows or calling
# DeepSeek. The all-unreviewed link follows the current remaining count.
# The progress card above the table shows reviewed, unreviewed, confirmed,
# corrected, ambiguous, rejected, and manually added counts for the current article.
# The default review order places rows with weak source localization, figure or
# trend provenance, and approximate/qualitative wording first. This is a review
# priority only; it never changes the six fields or claims that a row is wrong.
# Use "只看重点项" to focus that queue, then open the highlighted source before
# confirming or correcting each row.
# The review page keeps article switching compact. Extraction, export, and
# diagnostic details are under "提取、导出与文章状态" and stay collapsed during
# ordinary review. "专注校对" hides the surrounding workspace and expands the
# editable table plus immutable original pane; press Escape or the visible exit
# button to return. The highest-priority visible row is selected automatically,
# but this selection never saves or confirms data.
# Each automatic row also has an "原文证据" button that opens the highlighted PDF
# evidence directly.
# If a candidate cannot be resolved, use "存在歧义"; if it is not article data,
# use "不采用". Both decisions require a reason, preserve the full version trail,
# feed negative guidance to later DeepSeek runs, and can be restored to "待审核".
# Ordinary search and CSV/Excel exports exclude those two states. Pending automatic
# candidates remain visible in search with a clear "待审核" label.

# Write a Markdown audit against the original user goal. This distinguishes
# "ready for human review" from "automation goal complete".
auto-research evidence-goal-audit "Irradiation effects in high entropy alloys and 316H stainless steel at 300 C"

# The JSON output includes low-level checks and a user-facing requirements
# section: article selector -> extracted rows, six required columns, editable
# review with original preserved, manual entry, fuzzy search/export, and the
# human-review learning loop.

# Publish new automatic results through two independent DeepSeek extractors,
# adversarial comparison, a third review for low scores, and the search gate.
auto-research evidence-quality-run <paper-selector>

# Rebuild only the searchable visual semantics for existing stable screenshots.
# Two DeepSeek branches and the third-review gate generate Chinese titles,
# Chinese context explanations and visual-specific tags. This does not re-crop
# the PDF, replace the original caption or alter manual visual-review history.
auto-research evidence-quality-visuals <paper-selector>

# The legacy command remains useful as a non-publishing single-branch preview.
# Adding --commit now routes to the same adversarial quality gate.
auto-research evidence-deepseek-extract <paper-selector>
auto-research evidence-deepseek-extract <paper-selector> --commit

# Explicitly allow a repeat DeepSeek run for an already scanned paper
auto-research evidence-deepseek-extract <paper-selector> --force-rescan
auto-research evidence-quality-run <paper-selector> --force-rescan
auto-research evidence-run-article <paper-selector> --force-rescan

# Compare a saved DeepSeek run with the current six-column review baseline.
# This reads local files only and does not call DeepSeek or modify review rows.
auto-research evidence-benchmark <paper-selector> --run-id <run-id>

# Keep one best primary run and add only coverage-gap candidates from a later run.
# This writes a preview/report only and never changes the six-column review table.
auto-research evidence-ensemble-preview <paper-selector> \
  --primary-run 23 --supplemental-run 24

# Multiple focused supplements can be combined without taking a whole variable run.
# This adds the gap audit plus qualitative observations from the results pass.
auto-research evidence-ensemble-preview <paper-selector> \
  --primary-run 23 --supplemental-run 25 \
  --supplemental-focus coverage_gap_audit \
  --supplemental-focus qualitative_results
```

Safe supplemental-focus aliases are `coverage_gap_audit`,
`qualitative_results`, `composition_table`, `results`, `methods`, `targeted`,
and `all`. Prefer the
narrowest focus that adds verified coverage; selecting an entire later run can
increase human-review noise even when its candidates are evidence-grounded.

Different supplemental runs can use different selectors:

```bash
auto-research evidence-ensemble-preview <paper-selector> \
  --primary-run 23 --supplemental-run 25 --supplemental-run 26 \
  --supplemental-run-focus 25:coverage_gap_audit \
  --supplemental-run-focus 25:qualitative_results \
  --supplemental-run-focus 26:composition_table
```

Long DeepSeek runs use bounded SQLite lock retries for audit/progress writes.
Provider outages such as DNS failure, timeout, HTTP 429, or 5xx are classified
as unavailable and do not trigger the malformed-output page-splitting fallback.

Run metadata is stored in `ai_extraction_runs`; evidence-only result artifacts
are written to `data/evidence/deepseek_runs/`. Model output alone is never a
publication gate: schema, page, numeric/table anchors, background/inference
guards and independent verification remain required. The two-branch adversarial
gate may publish a candidate after `dual_pass` or `third_pass`; only candidates
that still fail those automatic checks require explicit human approval.
When human confirmations, corrections, manual additions, rejections, or ambiguity decisions exist, DeepSeek
extraction includes a short learning-guidance block so later runs learn the
preferred six-column field boundaries and Chinese wording style. Those examples
are only prompt guidance, not evidence; accepted rows must still be grounded in
the current PDF page text.
The learning trail page can export JSONL for either the current paper or the
whole database, so multiple reviewed papers can feed the next extraction pass.
It also shows a learning report card with the exact prompt-guidance preview that
will be used for later extraction, plus a Markdown export for audit.

In the local web page, switching the current article only reads saved database
rows. It does not call DeepSeek or re-run extraction; automatic extraction must
be started with the separate current-article extraction button.
Temporary edits in the review table are highlighted but not saved until you
click `确认当前内容`; switching papers, importing results, re-running extraction,
or leaving the page warns before those unsaved edits are discarded.
The switcher displays registered papers by title, first author, year/DOI, and
saved-row count. It can narrow the list by title/author/DOI, research object,
experiment method, workflow status, or the fixed 50-paper authenticity/function validation set.
Research-object and method tags are conservative, non-exclusive navigation aids;
they do not replace the scientific experiment classification used by extraction.
Filtering only changes the candidate list. It never switches the current paper
or calls DeepSeek until the user explicitly presses the switch button. The
visible option text does not require or foreground Zotero/storage codes; the web
app switches by the internal paper id.

The manual-entry page has a separate registered-paper selector. Manual rows are
saved directly to the selected paper and become searchable in the whole database;
this does not change the current review article and does not call DeepSeek.

The search page is database-wide by default. You do not need to choose a paper
before searching; CSV and Excel exports from that page follow the same
whole-database search result set. Search also expands common element names and
symbols, such as `钨` ↔ `W` ↔ `tungsten`, and indexes first/corresponding
authors when available.

The review page article selector is also a lightweight work queue. Each paper
option shows its six-column workflow state, such as `未扫描` or
`待审核 112/114`, and the current article strip repeats that processing status.
These labels come from the local evidence database, not from Zotero storage
codes.

An in-page 20-row calibration batch is preserved in that browser until the
batch is finished. Leaving the calibration view, refreshing the page, or
switching to another paper does not silently replace the unfinished sample;
return to the paper and press `继续本轮校准`. Only item IDs are stored locally,
and no row is confirmed or changed by this resume mechanism.

Every automatic row review decision is reversible. Confirmed, corrected,
ambiguous, and rejected rows expose `恢复待审核`; the action asks for explicit
confirmation and appends another version instead of deleting the prior review,
the edited fields, or the immutable automatic-extraction original. The reopened
row leaves the active learning-sample set until it is reviewed again.

The immutable-source pane also provides an optional reviewer rationale. This is
not a seventh data column and is not indexed as scientific data. It remains
temporary until the row is confirmed or corrected, then becomes the version's
audit note and a bounded `reviewer_rationale` hint for later DeepSeek extraction.
Clearing or leaving it unconfirmed does not write anything to the database.

After you confirm, correct, or manually add a row, the page refreshes the paper
workflow label, learning-sample summary, evidence audit, and extraction status.
This keeps the manual-review loop visible: every saved correction immediately
becomes part of the learning feedback used to guide later DeepSeek extraction.

The web page also marks whether an article has already been scanned. If a paper
already has six-column rows or a completed DeepSeek run, pressing an extraction
button opens a confirmation dialog; the backend rejects repeat scans unless that
confirmation sends `force_rescan`.

For scanned articles, click `生成当前文章 CSV 备份` to write the current six-column
rows to a timestamped CSV under `data/evidence/saved_scans/`. This is a local
backup/export action only: it does not re-run DeepSeek, does not change row
values, and is not required for normal row saving. The review page also provides
direct current-paper CSV and Excel downloads.

Chinese localization for already imported, still-unreviewed automatic rows:

```bash
auto-research evidence-deepseek-localize <paper-selector>
```

The command changes only `meaning` and `context_explanation`. It keeps values,
units, formulas, source excerpts, and evidence locations unchanged, and refuses
a translation when a numeric condition disappears.

The reproducible five-paper blind validation is documented in
`data/evidence/random5_20260708_audit.md`; its machine-readable manifest is
`data/evidence/random5_20260708_manifest.csv`.

The active fixed 50-paper authenticity/function validation set is declared in
`config/evidence_test_set_50.json`. The historical 35-paper file is retained to
document five rejected download/verification placeholders. Audit every active paper, local PDF,
fingerprint, six-column rows, source highlighting, search linkage, visual evidence,
and calibration sample with:

```bash
auto-research evidence-test-set-audit
```

The generated human-readable and machine-readable reports are stored under
`data/evidence/test_sets/`. The review-page article navigator exposes the same
set as a one-click scope, so it can be used repeatedly instead of silently
choosing a different random sample.

Benchmark reports are written to `data/evidence/benchmarks/` as JSON and
Chinese Markdown. Matching is deterministic and one-to-one: equal values on the
same PDF page are not considered the same datum unless source text, locator,
meaning, or experimental scope supplies an identity signal. Until the six-column
baseline is fully reviewed, report the results as provisional baseline agreement
and coverage, not as scientific precision, recall, or accuracy. An unmatched
candidate is a manual-review item, not automatically an extraction error.
Historical artifacts are replayed through the current deterministic evidence
gate and deduplication rules without changing the artifact or calling DeepSeek;
the report keeps raw, gate-passed, deduplicated, and rejected counts separately.
The experiment classifier may show low-score diagnostic types, but extraction
uses only title-supported or threshold-selected types and combines them into one
targeted pass. Together with the general methods and results passes, a normal
page block therefore uses three complementary extraction passes rather than one
pass for every incidental keyword category. A final coverage-gap audit receives
the existing candidate inventory and is instructed to return only overlooked
atomic evidence. If that optional audit fails, the run preserves verified data
and records a retry task instead of discarding the whole result.
