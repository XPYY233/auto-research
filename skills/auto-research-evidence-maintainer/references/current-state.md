# Current state checkpoint

## 0.9.1 Fusion GUI review checkpoint (2026-08-13)

- Target identity: `0.9.1-preview.1` build 19 for Apple Silicon macOS. It is a GUI acceptance build, not a complete functional replacement for 0.8.
- Production surface: one physical Fusion DOM with title bar, four-item activity rail, context sidebar, central tabs/editor, evidence inspector and status bar. The only bundled Web assets are `index.html`, `app.css`, `workbench.css` and `fusion_review.js`.
- Runtime boundary: literature is read from an atomic isolated schema-v12 snapshot; experiment tables are explicitly synthetic and session-only. Upload, extraction, confirmation, rollback, real export, native bridges, credentials and model calls are blocked.
- Validation: 28 Fusion/release/core targets, the complete core evidence test module and 212 macOS tests passed, as did isolated HTTP/frozen smoke, ad-hoc signing, DMG verification and real WebView review. The final tagged source is `0b3242d`; the installed App is 0.9.1/build19.
- Delivery: `/Users/USER/Zotero/auto-research-releases/Auto-Research-0.9.1-preview.1-build19-FusionReview/`; DMG SHA-256 `e01d547b3dd58e1e3068dd4250eec292bd2a3af5a5f87e0ab8fe9a7a3dbf44d1`.
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
- Current desktop source/tag: the 0.8 final release commit and annotated build18 tag; exact values are recorded in the user-kit acceptance report
- Current desktop version: Auto Research `0.8.0-preview.1` build 18 for Apple Silicon macOS
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
| latest clean release tests | 1,178: 810 shared, 229 macOS, 139 Windows contracts |

Target DOI `10.1016/j.jnucmat.2018.08.031` has 231 independent facts and 403/403 source-localizable automatic records at this checkpoint.

Independent read-only browser acceptance: a tungsten question returned 65 candidates (0 direct, 5 adjacent, 60 expansion), with 5 report citations and a 200 brief export. The console was clean; reload showed 0 history, disabled export and no runtime warnings.

## Honest completion boundary

- The application and currently published evidence are stable for demonstration.
- The original at-least-30-processed-paper goal is incomplete: 13 more papers are needed to move from 17 to 30; 33/50 remain not data-ready.
- Adversarial DeepSeek agreement is not independent scientific accuracy. A human gold standard remains missing.
- The only supported product shape is a personal desktop workbench. The current release line is Auto Research `0.8.0-preview.1` build 18 on macOS. Its primary navigation is Literature, Search, Experiment and Package Center; manual entry/revision-history views and the pet scene are retired. Windows shares the same UI contract but `installer_ready=false`, no Setup and no clean-machine acceptance. Browser workbench, mentor read-only, ports 8765/8766 and ngrok are retired historical compatibility paths, not sharing options.
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
- The 0.8 artifact includes Librarian V3, official/private/all federated exact search, reviewed private CSV/TSV/XLSX import, Package Center and a shared light/dark workbench. It remains an internal, ad-hoc signed, non-notarized preview, not a public release.
- The 291 current visuals include the unchanged 243-asset historical pre-cloud freeze; 243 is not the current total.

## Pre-change protection point

- Commit: `6ce9536`
- Tag: `evidence-demo-2026-07-30-pre-research-brief-1`
- This commit is also the previous Librarian reasoning/presentation release commit; the protection tag is not the new stable tag.

## Stable recovery artifacts

- macOS App: `/Applications/Auto Research.app` (bundle `0.8.0`, build `18` after installation).
- macOS UserKit: `/Users/USER/Zotero/auto-research-releases/Auto-Research-0.8.0-preview.1-build18-UserKit.zip`; SHA-256 `fc139f4b3a3f268b246218d01eb8805d71049596ad63cf0f241db1ae94ca7e27`. DMG SHA-256 is recorded in the kit acceptance report.
- Historical stable SQLite snapshot: `/Users/USER/Zotero/auto-research-backups/experimental_evidence-librarian-brief-stable-2026-07-30-v1.sqlite`; recorded SHA-256 `d62dc5c43ac9e0fb97e0ad2ecb85deaf50447fb7a036acae5147e8f6111236f6`. Do not infer that the current active user database still has this hash.
- Maintainer-only Git bundle: `/Users/USER/Zotero/auto-research-backups/source-bundles/auto-research-0.8.0-preview.1-build18-private.bundle`; it is intentionally excluded from the user kit because repository history contains historical database blobs.
- Previous stable tag and artifacts remain available under `evidence-demo-2026-07-30-librarian-reasoning-stable-1` and the `librarian-reasoning-stable-2026-07-30-v1` backup names.

Do not restore over the live tree. Verify a snapshot in a separate location before asking the user to switch.
