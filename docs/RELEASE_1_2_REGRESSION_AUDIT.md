# Auto Research 1.2 regression audit

Status: mandatory release gate, not a release claim.

Baseline evidence:

- 0.5.1 build 6, source checkpoint `63b34f2`, was transactionally installed and exercised on macOS on 2026-08-25.
- The installed candidate is Auto Research 1.2.0 build54 (`release_status=candidate`, core commit `b2578a6`). Real Librarian, selected-evidence, personal-suggestion and isolated one-page extraction checks have passed in this batch. The installed WebView also passed the official Table 3 source-image, PDF-open/return, 5×4 human review, restart persistence and CSV/XLSX export chain. Source checkpoint `ad72177` additionally fixes strict workspace-to-official grid linking, validated-checkpoint zero-model finalization, a persisted safe-cancellation boundary and visible zero-request PDF-picker cancellation; it is not yet installed. No build55 or stable claim is allowed until the complete walkthrough passes. Build24 remains the verified rollback.
- The production evidence database and the isolated `paper_056` recovery directory are outside this audit and must not be used by tests or builds.
- Windows remains frozen until the Mac workflow is accepted.

## Product rule

1.2 keeps the Fusion workbench, themes, secure provider store, prepared actions and read-only evidence DTOs. It restores the proven 0.5 user workflows through the current shared services. It must not restore the old DOM, a second navigation owner, environment-variable credentials, direct billable routes, or an unconstrained model loop.

No version bump, App replacement, DMG or “stable” statement is allowed until every P0 gate below passes in the installed Mac App.

## Implementation checkpoint — 2026-08-27

This checkpoint records source progress only; it does not change the installed
App or satisfy the release gate.

- The uploaded-literature chain now has a synthetic end-to-end proof from an
  immutable PDF snapshot through atomic evidence publication, incremental
  search refresh and source highlighting (`c4a413a`).
- A visual stage with zero discovered assets is reported as `not_found`, never
  as ready (`9daf9a2`, `4b74656`). Newly generated screenshots remain in
  `manual_review` and are excluded from published-only search (`760d4a0`).
- Table structure is now a separate scientific projection, not an overload of
  `visual_assets.variables_json`: conservative PyMuPDF parsing (`8e450e6`),
  append-only reviewed versions (`0343e91`), verified-only literal grid export
  (`87ab033`), atomic staging with the screenshot (`f641ebe`) and a
  platform-neutral review/export service (`7292a14`).
- A table candidate or parsing failure never fabricates numeric evidence. A
  screenshot may remain useful while structure is explicitly unavailable;
  only a human-approved version can enter CSV/XLSX or later dataset projection.
- Commit `32cb790` connects the same review service to the Mac desktop and
  Fusion document tabs. The original screenshot remains authoritative; main
  and secondary tabs keep independent request generations, and review POSTs
  reuse the shared session, Origin, CSRF and body-size authority.
- Targeted checks through this checkpoint passed: 19 parser tests, 14 store
  tests, 11 grid-export tests, 53 visual/finalizer adjacency tests and 48
  service/store/export tests. The integrated table chain then passed 139 target
  tests and the synchronized release contract passed 23 checks. These groups
  overlap and must not be summed as a unique full-suite count.

The current source now includes dataset/package projection of approved grids,
bounded Librarian and selected-evidence history, personal experiment/package
flows, task receipts and the task-aware workbench projection. The full serial
source gate passed on 2026-08-28 (1116 shared plus 80 explicit skips; 307 macOS).
Build 51 has since completed the clean build, signing, DMG, transactional install
and candidate-kit packaging gates. Still open before a stable release claim are
the installed-App walkthrough, four real AI workflow checks against that exact
frozen App, and the separately human-adjudicated scientific gold-set gate.

## Implementation checkpoint — 2026-09-02 safe cancellation

- Literature extraction now has one authenticated, session-bound cancel route.
  A queued task reaches a zero-model terminal state; a running task records a
  cancellation request separately from the main checkpoint CAS revision and
  stops only at a safe paid-call boundary.
- A successful provider result is sealed in its receipt before cancellation is
  committed. An unknown provider outcome remains `outcome_unknown` and is never
  mislabeled as cancelled or automatically retried.
- Fusion exposes one stop control only for queued/running literature jobs,
  prevents duplicate cancel requests, survives refresh/reconnect, and projects
  `cancelled` as a terminal state with an explicit restart action.
- Closing the native PDF picker or returning an empty selection is a visible
  terminal no-op: no upload, prepare, consent or model request is sent.
- Source commits `d8fd944` and `ad72177` passed 117 backend target tests, 139
  combined target tests and 81 Fusion/release-contract tests. These suites
  overlap and are not a full release claim. No App, DMG or new build was made.

## User-flow lineage and release gates

| ID | User workflow | 0.5 proof | Current regression / risk | 1.2 authority and minimum adaptation | Installed-App acceptance |
|---|---|---|---|---|---|
| LIT-01 | Find and select a paper | `app.js`: `renderPaperLibraryList`, title/author/DOI and status filters; 63/63 papers visible in the installed baseline | Fusion list/search must not truncate, overlap or lose a newly imported paper | Keep Fusion DOM; restore server-backed paper filtering, independent list scroll, automatic row height and reveal-by-identity | 100+ synthetic papers: first/last/newly imported paper reachable; title/author/DOI filter is deterministic; central scroll is unchanged |
| LIT-02 | Import a PDF | 0.5 has a dedicated intake page, local preflight, deduplication result, processing queue and upload trail | Fusion primary action must not be buried or report success before a safe snapshot exists | Reuse current native picker, identity preflight and staged extraction service; show import receipt and select/reveal the new paper | Large/Chinese-name PDF, duplicate file, duplicate DOI, corrupt PDF and cancel all produce a terminal, path-free outcome |
| EXT-01 | Start and observe extraction | 0.5 exposes extraction status and queue; stable domain services include staged extraction, quality gate, visual stage and atomic finalizer | Fusion has repeatedly hidden or broken the start action and may look frozen | One visible “开始提取与核验”; one task-level consent; persistent progress with stage, elapsed time, calls used/limit, cancel and resume | A blind PDF completes item/finding/table/figure extraction; cancellation and restart do not repeat completed paid stages |
| EXT-02 | Trust extraction completion | 0.5 retains raw records and review priority; current core has quality and transaction services | UI completion can diverge from visual assets, publication or index refresh | Completion requires finalizer commit, asset hashes and search refresh; otherwise show `saved_index_pending` or review queue | Fault injection at every stage leaves no half-published record and never fabricates curve values |
| SRCH-01 | Search four evidence types | 0.5 precise search has a large query box and real item/table/figure/finding selectors | Fusion must not hide the search field or filter only a client-side prefix | Send query, source scope and multi-type filter to one server request; render grouped real counts | Each type alone and combinations return correct full-corpus results; rapid queries cannot be overwritten by stale responses |
| DET-01 | Inspect one evidence item | 0.5 opens a complete detail surface with value/meaning/context/excerpt/article/page/related visual/source actions | Fusion split the task between a central detail and a permanently visible global AI footer | Evidence detail is an independent document tab; metadata, visual asset, structured content, PDF location and actions stay together | Open item/finding/table/figure; table shows screenshot plus parsed grid, figure shows real image plus caption/context |
| AI-DET-01 | Ask about selected evidence | 0.5 shows “围绕当前证据提问” only after opening a concrete detail and keeps per-entity conversation state | Fusion enables a global inspector chat from list selection | Hide chat unless the active visible evidence tab identity equals the safe detail projection; reuse bounded context preparation and current prepared action | Selecting a list row causes zero AI UI/network; opening detail enables multi-turn chat; switching/closing cannot mix entities |
| TAB-01 | Keep several papers/evidence open | 0.5 detail was coherent but modal; Fusion introduces `DocumentTabStore` | Secondary group is still partly a static projection; task navigation can cancel or orphan work | Two real editor groups, preview/pin semantics, lazy hydration, identity-level focus restore; navigation changes projection only | Single click previews right; double-click/question/PDF pins; two groups retain independent tabs, scroll and async results |
| PANE-01 | Resize/collapse panes | 0.5 has fixed but coherent regions; Fusion adds `PaneLayoutController` | Window thresholds plus multiple visibility owners can collapse every work area or squeeze text vertically | Pane controller is sole geometry authority; container `ResizeObserver` projects wide/medium/narrow; temporary task focus never overwrites saved sizes | Drag every separator to zero and restore; at least one editor always visible; no blank page, vertical title or lost tab at 1440/1280/900 px |
| INSP-01 | See module-specific inspector | 0.5 task pages own their detail state | Fusion previously reused one inspector surface across unrelated modules | One physical inspector with independent paper/search/experiment/package/settings projections and explicit empty state | Switch all five destinations repeatedly; no stale title, form, AI answer or task from another module remains |
| PDF-01 | Verify source in PDF | 0.5 native viewer provides zoom controls; old `openSourceViewer` preserves evidence location but lacks a reliable return chain | Fusion restores return/highlight but lacks product-owned zoom and fit controls | One `FusionPdfController`: fit width/page, zoom in/out, 100%, page retention, `ResizeObserver`, source highlight and return identity | Workspace and official PDF: correct page, transient/repeatable highlight, resize-safe zoom, close/back restores tab, scroll and focus |
| LIB-01 | Ask the Librarian | 0.5 has a large chat surface, history, progress feedback, clickable evidence and related articles | Fusion has slow/invalid Harness output, old second-charge stage, cramped layout and non-clickable projections | Local hard-condition parser/Search V2/bundle authority; one bounded model call; locally preverified R refs; Harness only organizes the answer | Typical question completes in target 10–25 s on normal network; one paid call; citations/recommendations open real tabs; failure includes stage and next action |
| LIB-02 | Continue a research conversation | 0.5 persists 20 encrypted conversations and supports multi-turn follow-up | Current history/memory UI must not be confused with scientific authority | Retain encrypted bounded history; research memory only through explicit approval of cited public facts; no private experiment or PDF body | Restart restores history without model call; delete works; approved memory is editable/removable and remains separate from chat history |
| AI-SET-01 | Configure and understand AI | 0.5 immediately reports configured DeepSeek, but conflates connection and capability | Fusion has safer credentials/readiness but users see “verified” while a business scope fails | `ai-readiness-v1` separately reports provider, Harness and four business scopes; settings mutations are serialized and path/key free | Add/test/switch/delete key; buttons explain exact unavailable reason; “connection verified” never claims all four workflows work |
| EXP-01 | Import an experiment table | 0.5 has a prominent file action and safe CSV/TSV/XLSX intent; observed stale “saved, retry refresh” proves this path also needs repair | Fusion must not show a placeholder grid or lose the final indexing transition | Reuse safe preview, bounded AI suggestion, one confirm transaction and paged table detail; progress has a terminal state | CSV/TSV/XLSX preview real rows/columns; AI is optional; one confirm indexes once; retry cannot duplicate data |
| EXP-02 | Search and inspect private rows | Current core owns confirmed/indexable private projection and pagination | Showing only a table name makes import useless | Search result opens a table document with real paged rows, conditions, units and verified series | First/middle/last page and empty boundary match source; private values never enter Librarian |
| PKG-01 | Import an official package | 0.5 predates the current signed package contract | Current UI can leave users unsure whether import succeeded or where to search | Persist `installed/activated/already_active`, four counts, PDF/asset coverage and path-free `next_action` | Re-import is idempotent; failure keeps active package; “前往搜索官方资料” opens the correct source scope |
| PKG-02 | Share a usable package | Current package services separate official, literature collection and personal experiments | A metadata-only package cannot support detail/PDF/visual workflows | Manifest must enumerate all included evidence, permitted PDFs, visual assets, locators and hashes; no silent omissions | Fresh data root imports package, opens all four types and every declared PDF/asset; rights and 2 GB limits enforced |
| DATA-01 | Export reusable data | Current dataset service distinguishes JSONL/Parquet/data card from `.aresearch` | “生成计划” alone is not an export and mixed export semantics confuse users | Plan shows scope/count/rights/size; explicit start creates atomic artifact; CSV/XLSX quick export uses same snapshot authority | Generated files exist, checksum matches, JSONL/Parquet agree, paper-level splits are reproducible, failure leaves no partial artifact |
| UX-01 | Work without a crowded five-column wall | 0.5 has clearer task-local hierarchy but dated styling; Fusion has better themes and shell | Too many simultaneous panes and hard widths make the product feel cramped | Comfortable default spacing, contextual auto-collapse, visible restore controls, blue semantic accents only, no hidden duplicate page | Core workflows remain legible at normal Mac window sizes; compact density is opt-in; keyboard and reduced-motion paths work |

## Mandatory P0 order

1. Remove the old two-stage Librarian execution path and finish single-call local citation preverification.
2. Make task lifetime independent of primary navigation; fix pane geometry and real editor groups before further page styling.
3. Move evidence AI into the active evidence detail and implement the single PDF controller.
4. Run the literature and personal-table main chains against isolated data, then repair only demonstrated adapter gaps.
5. Complete package/dataset workflows and truthful import/export receipts.
6. Only then run full tests, real paid acceptance, installed-App walkthrough and release packaging serially.

The frozen three-round layout findings and per-page action hierarchy are in
`docs/WORKBENCH_LAYOUT_AUDIT_1_2.md`. UX acceptance must use that document rather
than adding one-off CSS exceptions for individual screenshots.

## Scientific release gate

- No fabricated numeric value or curve point.
- Source page correctness is 100% for the gold set.
- Table/figure discovery recall is at least 95%; a claimed structured table reaches at least 95% header/cell mapping accuracy.
- Measurement joint precision for value/unit/material/conditions is at least 95%, measurement recall at least 85%, finding precision at least 90%.
- Low-confidence or conflicting candidates remain quarantined and editable through an immutable audit trail.
- Search and dataset export include only published or explicitly approved evidence.

## Evidence collected from the installed 0.5.1 baseline

- Paper review: title/author/DOI/status/direction filters, article selector, four review tabs and direct PDF link.
- PDF intake: native file action, local metadata hints, deduplication notice, processing queue and upload trail.
- Precise search: large query field, source scopes, four evidence kinds, export controls and rich result cards.
- Evidence detail: source context, related visual/PDF actions and per-entity AI chat displayed only inside the opened detail.
- Librarian: new conversation, encrypted history, multi-turn composer, progress feedback, cited evidence and related articles.
- PDF: native zoom controls exist, but Escape and standard WebView back shortcuts did not restore the evidence page; 1.2 must combine zoom with Fusion's explicit return chain.
- Personal intake: prominent safe file action exists; the observed stale refresh state is a regression case, not a feature to copy.
