# Current state checkpoint

## v1.2.0 single-candidate recovery checkpoint (2026-09-01)

- Installed identity: `1.2.0` build54, manifest `release_status=candidate`, core commit `505d0c0`. It is not the stable release. Build24 remains the verified rollback.
- Verified in this release batch: Librarian, selected-evidence chat and personal suggestion in the installed App; an isolated one-page PDF extraction with two real model calls; 1,133 shared tests (80 explicit skips), 309 macOS tests; first/repeat official-package import; and the installed official `search → Table 3 source image → PDF → return` chain.
- Remaining ordered gates: recover Table 3 human-verified row/column structure from the exact source PDF without fabrication and publish it through an independently versioned official package; then run the final installed workflow and release gates.
- Release rule: overwrite build54 for the whole batch. Do not create build55, DMG, UserKit or another persistent release worktree for an isolated fix. Allocate/finalize a release artifact only after the complete installed-App checklist passes.
- Two superseded build54 rollback copies were removed after identity verification; only the 0.5 comparison, build24 stable rollback and immediate build54 candidate rollback remain. The clean build54 worktree is retained only until this candidate batch closes. The unknown-database build49 worktree remains protected. Windows remains frozen.
- Production DB SHA-256 is `0389a5aa0faf4967696e3c3a5a574eaaf0bec08a34f48c69b98f50c705aa80b0`; keep it and `paper_056` user artifacts untracked and untouched.

The build27 notes below are historical only and must not be used as current release authority.
- Production DB SHA-256 at the candidate checkpoint is `18b9a4a3a4cbdf9ffcbe4fe14fa0e4855727a6211c1f61902e3d30324903bc84`; keep it and `paper_056` user artifacts untracked and untouched.

## v1.1.0 Mac source checkpoint (2026-08-22)

- Source contract: Auto Research `1.1.0` build 24 for Apple Silicon macOS. Windows 1.1 is frozen until user acceptance; do not edit or build `desktop/windows/**` in this line.
- Fusion: independently scrollable and title/author/DOI-filterable literature tree, server-side four-type filtering, recoverable preview/pinned `DocumentTabStore`, at most two complete editor groups, module-isolated inspector state, source-highlighted in-app PDF return, and a real approve/reject/correct review queue. The context/editor/editor/inspector boundaries are pointer- and keyboard-resizable, resettable, responsive and stored as non-sensitive local preferences.
- Production AI: pinned `deepseek-harness-sdk==0.1.1rc1` and `deepseek-harness-runtime-bin==0.1.1rc1`; eight read-only Auto Research tools only, prepared-action consent/budgets remain authoritative, no old-loop fallback. Encrypted history retains at most 20 sessions or 30 days.
- Dataset: `dataset-bundle-v1` produces JSONL, Parquet, data card and manifest with deterministic paper-level train/validation/test split. Immutable source plan: 60 papers, 4,356 records, 2,417 unreviewed, content fingerprint `8f853102d0a4774efad59fdd3be88d2ca6b685930aeb767dfa09bd80ca0341ea`.
- Scientific audit: 14 human-gold dimensions for numeric/unit/meaning/conditions/table/figure/finding/excerpt/locator quality. Software tests and model agreement are not scientific accuracy.
- Official package v2: user froze an explicit 59-paper scope, excluding DOI `10.2172/6065200` as `source_pdf_unavailable` without deleting local/Zotero records. The signed candidate has 4,369 entities, 59 real PDFs and 291 visual assets; SHA-256 `909cc7323b8a91e3a238e8f49d03fe62022f5a9d078f2cc6785bb8a0b73a69c4`. Fresh import, repeat import and every installed PDF/asset passed runtime verification. Historical visual review status is still `draft`, so scientific review is not complete.
- Build 24 implementation commit `393efda` passed 880 shared tests (80 explicit skips) and 234 macOS tests, plus Python/JavaScript, release-contract and diff checks. A clean-worktree App/DMG rebuild, frozen smoke, strict ad-hoc signature, transactional installation, single-launchable-App check and DMG verification passed. Real Mac read-only review covered literature filtering, two full editor groups, accessible splitters, a real table image and the highlighted in-app PDF return/close chain. DMG SHA-256: `f3363092dc8dfea9af4aaa9fe1920e164781c585536e7146d718c0cd7e4dfc5c`; Mac UserKit SHA-256: `2c943904090bbcfde8ce1aa45c2b107f1482905f7090bdd3b648abe2596ca99c`.
- Protection: tag `auto-research-v1.0.0-pre-1.1-protection`; external backup `/Users/USER/Zotero/auto-research-backups/v1.1-protection-20260822-154915`. The production `db/experimental_evidence.sqlite` remains the only expected dirty file and must not be staged, reset or tested.

## v1.0.0 stable-candidate checkpoint (2026-08-21)

- Source identity: Auto Research `1.0.0` build 22 for Apple Silicon macOS. The final commit/tag/artifact hashes must be filled only after the clean-worktree build and real installation acceptance; the currently installed App remains 0.9.2/build21 until that transaction succeeds.
- Production surface: one Fusion workbench with Literature, Search, Experiment and Package Center plus Settings. Restored capabilities include staged PDF extraction, four evidence details and real visual assets, central PDF/return, exact/federated search, Librarian and selected-evidence AI, reviewed personal imports with real paginated tables, official/user package flows, and single/batch exports.
- Validation so far: 773 shared tests and 226 macOS tests, 999 total, plus JavaScript/Python/release-contract checks. Windows was deliberately excluded and remains paused without Setup.
- Official package: `auto-research-internal-evidence-1.0.0.aresearch`, SHA-256 `d1337a43aa4c0b83030a70e6a500bc60b85a994cb03d287396e895957ae4604d`; 60 papers and 4,356 entities (3,142 item / 936 finding / 46 table / 232 figure), no PDFs or binary assets.
- Data protection: production SQLite SHA-256 remains `d3e225d35f4c9e21fcf825405d0e38f28210c4479caac67b87be9f8382fb6f72`. It is the only expected dirty worktree item and must never be staged, reset or used for release tests.
- Honest boundary: v1 is a research-group stable Mac build, ad-hoc signed and not notarized because this machine has no Developer ID identity. Corpus readiness remains 17/50 data-ready and 30/50 visual-ready; software release status does not imply corpus completeness or human gold-standard scientific validation.

The 0.9 and 0.8 checkpoints below are retained only for rollback/history.

## 0.9.2 Fusion visual-evidence checkpoint (2026-08-20)

- Installed identity: `0.9.2-preview.1` build 21 for Apple Silicon macOS, functional source `83cf5e4f6031dda8b3efc375e1e0aa183453f92a`, annotated tag `auto-research-0.9.2-preview.1-build21`.
- Restored production capability: all four public evidence types open in the central Fusion detail workspace. Workspace `table` and `figure` records load the audited visual-asset DTO and actual no-store PNG/JPEG bytes; `item` and `finding` keep their distinct safe projections.
- PDF behavior: a source-local paper opens inside the central workbench and provides explicit `返回证据详情` and `返回证据列表` actions. Late detail/image requests are generation-guarded and cannot overwrite a newer tab.
- Privacy/scientific boundary: renderer DTOs remain path-free; missing official/private binary assets terminate with an honest unavailable state. The UI never redraws or invents table cells, curves or images.
- Validation: 753/753 shared tests and 215/215 macOS tests passed serially, with JavaScript/Python syntax, release-contract sync, frozen smoke, strict ad-hoc signature and DMG verification. Real-App review opened an actual Table 4 screenshot, Figure 9 TEM image, central PDF and a search-result table without a model call.
- Delivery: `/Users/USER/Zotero/auto-research-releases/Auto-Research-0.9.2-preview.1-build21-UserKit.zip`; SHA-256 `1848453eeff79e1ad8fe206e3f3475282116afd3bbeac7de9a8657a80dfd0331`. DMG SHA-256 `87e29d20d926111aac784d8883ea19330ac818b754b8f3f86171f22b94943a11`.
- Rollback: build20 remains as a non-launchable `.app.rollback` under `/Users/USER/Zotero/auto-research-backups/app-rollbacks/`. The system has exactly one launchable `/Applications/Auto Research.app`.
- Still incomplete: automatic extraction, private experiment paginated row/curve details, selected-evidence AI, Package Center mutations and real export remain later 0.9 work. Windows remains paused and has no Setup.

## Historical 0.9.1 Fusion GUI review checkpoint (2026-08-13)

- Target identity: `0.9.1-preview.1` build 19 for Apple Silicon macOS. It is a GUI acceptance build, not a complete functional replacement for 0.8.
- Production surface: one physical Fusion DOM with title bar, four-item activity rail, context sidebar, central tabs/editor, evidence inspector and status bar. The only bundled Web assets are `index.html`, `app.css`, `workbench.css` and `fusion_review.js`.
- Runtime boundary: literature is read from an atomic isolated schema-v12 snapshot; experiment tables are explicitly synthetic and session-only. Upload, extraction, confirmation, rollback, real export, native bridges, credentials and model calls are blocked.
- Validation: 28 Fusion/release/core targets, the complete core evidence test module and 212 macOS tests passed, as did isolated HTTP/frozen smoke, ad-hoc signing, DMG verification and real WebView review. A final runtime regression also verifies that settings sections cannot overwrite the active literature tab label. The final tagged source is `4fd4f24`; the installed App is 0.9.1/build19.
- Delivery: `/Users/USER/Zotero/auto-research-releases/Auto-Research-0.9.1-preview.1-build19-FusionReview/`; DMG SHA-256 `c1786d4b4ee6734390f36c878fdcf008f5d0c3ab405f5eac87031ffe40b92f69`.
- Rollback: keep macOS 0.8 build18 as a verified non-`.app` rollback before installing build19. Preserve the production SQLite byte hash and do not stage it.
- Windows is paused. Its 0.8 internal source remains `installer_ready=false`; do not migrate Fusion until the user approves the Mac GUI.

## 0.8 release freeze checkpoint (2026-08-13)

- Source identity: Auto Research `0.8.0-preview.1` build 18; builds 16 and 17 were blocked before release by frozen smoke gates. The annotated build18 tag is created from the final release commit. Last rollback remains `0.7.0-preview.2` build 15, source `888aae5`.
- Completed: Workbench A light/dark/system and density; four-entry single-owner shell; settings; DeepSeek/OpenAI trusted registry and provider-separated credentials; four-scope server-prepared AI actions; staged literature commit; personal reviewed import; package center; macOS runtime; Windows thin source parity.
- Security: no arbitrary provider URL; renderer cannot create final outbound payload; every billable stage has disclosure, explicit confirmation, one-time nonce and budget; search and local import remain available without a key.
- Validation: 1178/1178 serial tests (810 core, 229 macOS, 139 Windows), Python compile, six JS syntax checks, release hash sync, diff check and Git object check. Production SQLite was not used or staged.
- User-authorized cleanup: paper_056 deepseek/quality/visual leftovers were deleted. The only expected dirty worktree item is the production `db/experimental_evidence.sqlite`.
- Pending after the Mac release: real Windows 11 build/install acceptance. Windows remains `installer_ready=false`; no Setup exists.

Use this file for fast orientation. Verify drift-prone counts with read-only commands before publishing them.

## Identity

- Active root: `/Users/USER/Zotero/auto-research`
- Research-brief pre-change protection commit: `6ce9536`
- Research-brief pre-change tag: `evidence-demo-2026-07-30-pre-research-brief-1`
- Previous Librarian reasoning/presentation implementation commit: `8e9c4c1`
- Research-brief implementation/documentation commit: use the current shared-core `git log -1`; this file does not self-reference its commit hash
- Current desktop source/tag: 1.1 source is the current `git log -1`; create the annotated 1.1 tag only after final build/install acceptance
- Current desktop version: Auto Research `1.1.0` build 24 stable delivery for Apple Silicon macOS; verify the installed App live rather than trusting this note
- Release: `2026.07.30-librarian-brief-stable.1`
- Evidence schema: v12
- Fixed evidence corpus: `config/evidence_test_set_50.json`
- Evidence DB: `db/experimental_evidence.sqlite`
- Production PDF source: Zotero local attachments plus explicitly uploaded real PDFs

## Verified release counts

| Object | State |
|---|---:|
| papers/documents in evidence DB | 60 |
| raw six-column versions | 6,501 |
| reportable numeric occurrences | 5,048 |
| independent physical facts | 3,142 |
| qualitative findings | 937 |
| quarantined legacy prose values | 1,453 |
| visual assets | 291: 59 tables, 232 figures |
| fixed PDFs valid and identity-matched | 50/50 |
| data-ready fixed papers | 17/50 |
| visual-ready fixed papers | 30/50 |
| latest 1.1 release tests | 1,114: 880 shared plus 234 macOS; shared has 80 explicit external-fixture skips |

Target DOI `10.1016/j.jnucmat.2018.08.031` has 231 independent facts and 403/403 source-localizable automatic records at this checkpoint.

Independent read-only browser acceptance: a tungsten question returned 65 candidates (0 direct, 5 adjacent, 60 expansion), with 5 report citations and a 200 brief export. The console was clean; reload showed 0 history, disabled export and no runtime warnings.

## Honest completion boundary

- The installed application identity is drift-prone; read its Info.plist live. Build 24 completed protected build and installation acceptance; the protected v1 source/package and build24 rollback remain recovery points.
- The original at-least-30-processed-paper goal is incomplete: 13 more papers are needed to move from 17 to 30; 33/50 remain not data-ready.
- Adversarial DeepSeek agreement is not independent scientific accuracy. A human gold standard remains missing.
- The only supported product shape is a personal desktop workbench. The current source and installed line is Auto Research `1.1.0` build 24 on macOS. Its primary navigation is Literature, Search, Experiment and Package Center; manual entry/revision-history views and the pet scene are retired. Windows 1.1 work is paused and its release contract remains `installer_ready=false`. Browser workbench, mentor read-only, ports 8765/8766 and ngrok are retired historical compatibility paths, not sharing options.
- GitHub is not configured. `origin` points to a local historical bundle.
- Librarian reasoning now has deterministic hard-condition parsing, direct/adjacent/expansion classification, evidence bundles and a five-section research report. This did not change scientific evidence or corpus readiness.
- The local deterministic parser plus bounded history is the only hard-condition authority. DeepSeek plans queries, selects bounded evidence and explains it; it cannot create or rewrite hard conditions. Scientific-notation fluence, equivalent units and particle/material role boundaries are covered by regression tests.
- The research brief is a deterministic read-only derivative of the latest current-process HMAC-signed, non-clarification Librarian answer with at least one actual citation. It is not a fifth evidence type.
- The chat response carries top-level `research_brief.snapshot_token`, `answered_at`, `evidence_fingerprint`, `eligible` and `ineligible_reason`; the signed snapshot uses `answered_at` and `evidence_version`. Export accepts only `{snapshot, snapshot_token}` from that response, not an arbitrary snapshot, session id, raw SQLite or PDF.
- Every canonical R# must map uniquely to an `agent_cited=true` `item/finding/table/figure`, with exact counts. Clarification, zero-reference, inconsistent and expired/tampered inputs are rejected.
- The process-local HMAC binds only the public snapshot. It does not create server history, call DeepSeek, re-query Search V2/database/PDF, read curve pixels or add cross-bundle quantitative comparisons. Restart invalidates old history tokens; re-run the query before export.
- Brief authorization exists only in transient `state.librarianBriefAuth`; it never enters session meta/messages, `localStorage`, or desktop encrypted history. The history schema and persistence policy remain unchanged.
- The brief uses a public-field whitelist. Missing title/DOI/page/excerpt stays empty, sets `integrity.status=warning`, and is displayed by R# in the Markdown.
- Librarian history in the ad-hoc macOS preview uses an Application Support private AES-GCM key/ciphertext pair to avoid unstable-code-identity Keychain prompts; a formally signed macOS release uses Keychain and Windows uses Credential Manager. `browser-local` and `readonly-none` remain compatibility/test adapters only. No history is written to the evidence DB, and a stable App must not prompt for an Auto Research edit password.
- The intended distribution model is a signed desktop App plus separately delivered, versioned evidence packages. Users import a package for immediate offline search and may add their own PDFs. Any DeepSeek extraction or Librarian use is BYOK: the user's key goes to the OS credential store only, never the database, package, logs or Git. Package implementation follows the stable macOS preview.
- Build21 restores real read-only table/figure visual evidence and the central PDF chain in the Fusion shell. Automatic extraction, private experiment row paging/curves, selected-evidence AI and real export are not yet reconnected. It remains an internal, ad-hoc signed, non-notarized preview, not a public release.
- The 291 current visuals include the unchanged 243-asset historical pre-cloud freeze; 243 is not the current total.

## Pre-change protection point

- Commit: `6ce9536`
- Tag: `evidence-demo-2026-07-30-pre-research-brief-1`
- This commit is also the previous Librarian reasoning/presentation release commit; the protection tag is not the new stable tag.

## Stable recovery artifacts

- macOS App: `/Applications/Auto Research.app` (bundle `1.1.0`, build `24` after transactional installation).
- macOS UserKit: `/Users/USER/Zotero/auto-research-releases/Auto-Research-1.1.0-build24-Mac-UserKit.zip`; SHA-256 `2c943904090bbcfde8ce1aa45c2b107f1482905f7090bdd3b648abe2596ca99c`. DMG SHA-256 is `f3363092dc8dfea9af4aaa9fe1920e164781c585536e7146d718c0cd7e4dfc5c`.
- Historical stable SQLite snapshot: `/Users/USER/Zotero/auto-research-backups/experimental_evidence-librarian-brief-stable-2026-07-30-v1.sqlite`; recorded SHA-256 `d62dc5c43ac9e0fb97e0ad2ecb85deaf50447fb7a036acae5147e8f6111236f6`. Do not infer that the current active user database still has this hash.
- Maintainer-only historical Git bundles remain under `/Users/USER/Zotero/auto-research-backups/source-bundles/`; they are intentionally excluded from user kits because repository history contains historical database blobs.
- Previous stable tag and artifacts remain available under `evidence-demo-2026-07-30-librarian-reasoning-stable-1` and the `librarian-reasoning-stable-2026-07-30-v1` backup names.

Do not restore over the live tree. Verify a snapshot in a separate location before asking the user to switch.
