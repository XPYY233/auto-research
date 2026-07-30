# Agent implementation log — Librarian reasoning and presentation

Date: 2026-07-30  
Scope: Stage 3 reasoning upgrade and Stage 4 answer presentation  
Canonical root: `/Users/USER/Zotero/auto-research`

## Protection point

- Pre-change commit: `17e6f60`
- Pre-change tag: `evidence-demo-2026-07-30-pre-librarian-reasoning-presentation-1`
- Pre-change SQLite snapshot: `/Users/USER/Zotero/auto-research-backups/experimental_evidence-pre-librarian-reasoning-presentation-2026-07-30-v1.sqlite`
- Pre-change SQLite SHA-256: `c9d63be31ad660294e52a7d093d37d7c4bbbfb22b3c2e5138005070f6f3b5038`

## Implemented architecture

### Deterministic reasoning

- `librarian_reasoning.py` parses material, irradiation type, particle, temperature, dose/fluence, property and specimen state.
- The deterministic local parser plus bounded history is the sole hard-condition authority. DeepSeek may plan queries, select bounded evidence and explain it, but cannot create, cross-inject or rewrite hard conditions.
- Current-turn explicit conditions override bounded user history. Irradiation and particle are a coupled inheritance family; changing either blocks the other from historical carry-over.
- Synonyms and element aliases are soft recall expansions, never additional hard conditions.
- Common fluence forms (`1e15`, `2E16`, `5×10¹⁶`) preserve exponent semantics and equivalent area units can match; energy is not temperature, particle tokens are not material constraints, and a power-unit `W` is not tungsten.
- Candidates are classified locally as `direct`, `adjacent` or `expansion`. DeepSeek cannot change the class.
- Evidence bundles are keyed by paper plus actual record material and actual experimental-condition signature.
- Critical unresolved pronouns/comparisons return a clarification report without evidence recall.

### Model boundary and integrity

- Runtime remains DeepSeek-only; the real integration used `deepseek-v4-pro` from project Keychain configuration.
- DeepSeek plans recall and selects/summarizes bounded evidence. The backend constructs the evidence matrix and database-gap assessment deterministically.
- Four evidence types receive prompt-budget slots before remaining candidates are filled.
- The integrity gate rejects DSML, orphan references, non-direct references in a direct conclusion, unsupported quantitative tokens and cross-bundle quantitative comparison.
- Legacy `answer/results` remain; new fields are `report`, `query_analysis`, `evidence_bundles`, `match_counts`, `bundle_count` and `response_format`.

### Search and public API maintenance

- Search index format is v3.
- Source fingerprints use the real `visual_asset_reviews` table and include quality-candidate revisions and data-item/visual links.
- Librarian recall ensures index freshness once and then executes all bounded query/type operations without repeating fingerprint work.
- Results from multiple recall queries are fused by query-hit count and Search V2 score before per-type caps.
- `public_dto.py` is shared by Search V2, item/finding search, visual search/detail and Librarian responses. It excludes local paths, Zotero/local keys, reviewer identity and internal notes.
- Single-letter Latin element symbols no longer expand from whole-document unit text, preventing `°C` from creating a false carbon alias.

### Frontend presentation

- The newest assistant turn progressively renders the five fixed report sections.
- Query hard conditions are shown as chips; matrix columns are material, condition, property, result and paper evidence.
- Related evidence shows the relaxed condition; follow-up buttons submit two-to-three bounded questions.
- Only newest-turn references are interactive. Clicking a reference selects the correct item/finding/table/figure tab and locates its card.
- Cards show match class, final-report citation state and evidence bundle.
- A new request clears old result cards before HTTP submission. Interrupted requests do not attach stale cards to a new question.
- Browser-local history stores the structured report and metadata; old legacy messages still render through escaped Markdown.
- Progress labels are explicitly estimated. The elapsed timer is hidden from frequent `aria-live` announcements.

## Regression defects found during implementation

1. A prior user question about neutron irradiation could add `particle=neutron` to a later explicit ion-irradiation question. Fixed by coupled current-turn beam inheritance.
2. `300°C离子辐照` could be parsed as carbon ions. Fixed by excluding degree-prefixed element symbols.
3. The global search fingerprint referenced nonexistent `visual_asset_versions`. Replaced with `visual_asset_reviews` and tested through an actual correction refresh.
4. Model candidate JSON was valid after prior work but could still starve later evidence types. Replaced with type-balanced bounded serialization.
5. Old evidence cards remained visible while a new request was running or failed. New submissions now clear the result state first.
6. Historical `[R#]` could point to the latest result set. Only the latest assistant report now owns interactive references.

## Validation evidence

- Focused reasoning/frontend suite: passed.
- Full Python suite at release checkpoint: 226 tests passed.
- Real DeepSeek query with bounded history:
  - 71 candidates across item/finding/table/figure;
  - direct 4, adjacent 8, expansion 59;
  - three evidence-matrix rows, six related rows and three suggested follow-ups;
  - no forbidden local fields.
- Critical ambiguity query returned clarification with zero search operations and zero result cards.
- Editable browser:
  - five report sections, hard-condition chips, three matrix rows and three follow-up buttons;
  - reference R13 switched to finding and located one direct-cited B3 card;
  - table tab rendered nine cards and nine source images;
  - no browser console warnings/errors.
- Read-only HTTP/browser:
  - same Agent UI and shared frontend;
  - item/table/figure/finding search and visual detail returned safe DTOs;
  - write request returned 403;
  - SQLite SHA-256 unchanged across the read-only checks.

## Non-changes

- No PDF was rescanned.
- No six-column fact, finding, visual screenshot or review decision was changed.
- No vector database, fifth evidence type, cloud visual path or write-capable Agent was added.
- Corpus readiness remains 17/50 data-ready and 30/50 visual-ready.

## Stable release artifacts

- Release: `2026.07.30-librarian-reasoning-stable.1`; schema v12.
- Feature implementation commit: `8e9c4c1`.
- Stable tag: `evidence-demo-2026-07-30-librarian-reasoning-stable-1`.
- Final SQLite: `/Users/USER/Zotero/auto-research-backups/experimental_evidence-librarian-reasoning-stable-2026-07-30-v1.sqlite`; SHA-256: `bfded1930856019c3413096fc20dee9b7f6b33e3310960b9913b94ee1dd2220e`.
- Final Git bundle: `/Users/USER/Zotero/auto-research-backups/auto-research-librarian-reasoning-stable-2026-07-30-v1.bundle`; SHA-256: 见相邻 `.sha256`.
- Standalone skill: `/Users/USER/Zotero/auto-research-backups/auto-research-evidence-maintainer-skill-2026-07-30-v2.zip`; SHA-256: 见相邻 `.sha256`.
- Current visual total is 291 (59 tables, 232 figures). The 243-asset pre-cloud freeze remains historical and unchanged.

## Rollback

Code can return to the pre-change tag without restoring scientific data. Search V2 is disposable and can be rebuilt. If a database copy is needed, verify the pre-change SQLite snapshot in a separate path before replacing the live file.
