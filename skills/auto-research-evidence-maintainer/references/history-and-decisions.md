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

## Decisions that remain active

- Preserve the six-column user model; improve indexing instead of adding a column.
- Search `meaning` above `context_explanation`.
- Keep local and public modes on one frontend.
- Prefer DOI/title over Zotero key across devices.
- Keep public result types fixed to item/finding/table/figure.
- Preserve all cited and expansion candidates in Librarian responses.
- Treat `agent_cited` as a reference anywhere in the final fixed report; preserve match class, missing constraints and evidence bundle separately.
- Keep the five-part report and direct/adjacent/expansion rules deterministic. Do not delegate these publication semantics to DeepSeek.
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
