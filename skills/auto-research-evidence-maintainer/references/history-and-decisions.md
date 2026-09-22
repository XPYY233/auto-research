> Historical context only. Current user instructions and PROJECT_HANDOFF.md supersede every old path, release identity, quota and manual-review workflow below.

# History and architectural decisions

## Major phases

### Corpus acquisition

The project began as a resumable literature discovery, lawful PDF acquisition, authenticity verification, parsing, analysis, and Zotero workflow. A historical clean checkpoint collected 300 valid PDFs and organized them into 30 object+method child collections.

### Six-column evidence pilot

The evidence product started with HEA/RHEA and tungsten irradiation papers. It established the six user fields, immutable original versions, source page/locator/excerpt, Chinese review UI, CSV/Excel export, and source highlighting.

### General experimental recognition

The extractor stopped assuming every paper was an irradiation paper. It now recognizes paper mode and multiple experiment types before selecting extraction foci.

### Visual evidence

Tables and figures became independent searchable/reviewable objects. Early crop failures were repaired with local PDF geometry and regression examples. The stable policy keeps original high-resolution PDF screenshots and does not digitize curves.

### Cloud visual experiment and rollback

MinerU/cloud enhancement ran in shadow form but produced worse results and added complexity. The user explicitly rejected it. All production authority returned to local PyMuPDF visuals; do not reintroduce the cloud path implicitly.

### Adversarial automatic quality gate

The user did not want every candidate blocked on manual approval. Two independent DeepSeek branches plus a third low-score review became the publication gate. Manual review remains a correction/calibration route and quarantine for unresolved items.

### Chinese visual semantics and selected-evidence chat

DeepSeek-generated titles, explanations, and tags were restored from caption/context text. Later, entity-scoped chat added bounded PDF pages without giving the model write access or the entire corpus.

### Fixed 50-paper corpus and health audit

The test set expanded to 50 real identity-matched PDFs. The health audit separated a stable product demonstration from incomplete corpus processing: only 17/50 were data-ready at the stable checkpoint.

### Search V2 and Librarian

SQLite FTS replaced slow broad scans. The Librarian became the primary search invitation while exact search remained available. A first free model tool loop exposed protocol text and too few cards; it was replaced by deterministic three-stage orchestration and complete candidate visibility.

### Librarian reasoning and research-report presentation

The stable three-stage orchestration gained a deterministic scientific-condition layer rather than a new Agent database. Material, irradiation, particle, temperature, dose/fluence, property and state are hard dimensions; aliases remain soft recall. The local parser plus bounded history is the sole hard-condition authority: DeepSeek plans queries, selects bounded evidence and explains it, but cannot create or rewrite hard conditions. Local code assigns direct/adjacent/expansion classes and evidence bundles, while the user-facing answer is a fixed five-section report with traceable matrix rows, explicit relaxed conditions, database gaps and clickable follow-ups. Scientific-notation fluence and equivalent units preserve their numeric meaning; particle/material and energy/temperature roles are disambiguated. Current-turn conditions override history, and incompatible experiments may not be joined into quantitative comparisons. Search V2, visual detail and Librarian routes share one public DTO projection that excludes local paths, local/Zotero keys, reviewer identity and internal notes.

### Librarian research brief and secure history adaptation

The next stable increment added a deterministic Markdown derivative of the latest current-process HMAC-signed structured Librarian response. Eligible output must be non-clarification, contain at least one canonical citation, and pass an exact R#-to-`agent_cited` consistency gate. The chat response carries a top-level opaque `research_brief` envelope; export accepts only `{snapshot, snapshot_token}` from that process, not an arbitrary snapshot, session id, raw SQLite or PDF. HMAC binds only the canonical public snapshot and does not invoke DeepSeek, query Search V2/database/PDF, read curves, infer missing provenance or create cross-bundle quantitative comparisons. Missing public provenance stays empty, produces a visible warning and is listed by R#.

The shared frontend also gained mode-aware Librarian history. The Apple Silicon macOS product layer persists through AES-GCM with a key managed by macOS Keychain; a browser without the desktop bridge uses bounded local history; public read-only uses `readonly-none` and does not access desktop history or Librarian `localStorage`. History never enters the scientific database. Brief authorization exists only in transient `state.librarianBriefAuth` and does not enter session meta/messages, `localStorage`, or desktop encrypted history. A restart or history-only restore therefore requires a new query before export. The desktop product layer remains a separate packaging boundary, so a core frontend adapter does not imply that `desktop/macos/**` source was shipped in the core release.

### Desktop-only product decision (2026-08-01)

The formal product is now a personal desktop workbench. The current development preview is macOS, while the expected end-user target is Windows. The mentor read-only page, standalone browser workbench, ports 8765/8766, ngrok and browser launchers are retired. The App may continue to embed the same HTML/JavaScript and loopback webapp internally; permission modes stay covered by regression tests, but they are not independently released. Future distribution separates a signed App from reviewed portable evidence packages rather than depending on the developer Mac staying online. Users import a package for offline search and may add their own PDFs. DeepSeek extraction and Librarian calls are BYOK: a user may paste a key supplied to them or use their own, but the key is stored only in the platform credential store—Keychain on macOS and Credential Manager on Windows—and never in evidence data, packages, logs or Git. The App must not require a project edit password; unexpected credential prompts block release.

## Decisions that remain active

- Preserve the six-column user model; improve indexing instead of adding a column.
- Search `meaning` above `context_explanation`.
- Keep one internal frontend for the App; retain historical permission modes only as compatibility tests.
- Prefer DOI/title over Zotero key across devices.
- Keep public result types fixed to item/finding/table/figure.
- Preserve all cited and expansion candidates in Librarian responses.
- Treat `agent_cited` as a reference anywhere in the final fixed report; preserve match class, missing constraints and evidence bundle separately.
- Keep the five-part report and direct/adjacent/expansion rules deterministic. Do not delegate these publication semantics to DeepSeek.
- Keep the research brief a derived read-only export of the latest current-process-signed, non-clarification response with at least one actual citation. It is not a fifth evidence type, session backup, model call or scientific record.
- Accept only `{snapshot, snapshot_token}` issued with the response, never an arbitrary snapshot, session id, original database/PDF or filesystem path. Do not resolve or re-sign desktop/browser history inside the exporter.
- Export only canonical R# evidence that maps uniquely to `agent_cited=true` records from the four public types with exact counts. Reject clarification, zero-reference and inconsistent inputs.
- Keep the HMAC process-local and bind only the canonical public snapshot. Restart invalidates old tokens; users must re-run the query before exporting restored history.
- Preserve the field whitelist. Missing title/DOI/page/excerpt stays empty, sets a warning and must appear by R# in the Markdown.
- Do not let brief export call DeepSeek/Search V2, open the database/PDF, digitize curves or create quantitative comparisons across evidence bundles.
- Keep public Librarian history at `readonly-none`; desktop Keychain/AES-GCM persistence belongs to the separate product layer, and no history mode may write the scientific database.
- Keep hard-condition creation local and deterministic. DeepSeek is limited to query planning, bounded evidence selection and explanation.
- Preserve scientific-notation and equivalent-unit matching, and keep particle/material and energy/temperature roles distinct in parsing and bundle identity.
- Clarify critical unresolved objects before recall instead of guessing. This is the bounded exception to the normal fresh-search rule.
- Cache identical complete Librarian results only under an unchanged database fingerprint.
- Keep runtime AI DeepSeek-only.
- Keep visual pixels local and semantics text-grounded.
- Separate program stability, corpus completion, and scientific validation.

## Deferred work

- Build a 30-50-question human gold-standard Librarian suite, including a curated condition vocabulary and boundary-regression cases for scientific notation, unit equivalence, particle/material roles and history inheritance.
- Process at least 13 more fixed papers to reach the original 30-paper line.
- Add user feedback for missed/wrong evidence without mutating scientific records.
- Decide private GitHub vs public code plus sanitized demo data.
- Consider vector search only after the gold standard demonstrates lexical recall failures.
- Consider persistent hosting only after repository/data/copyright boundaries are explicit.
