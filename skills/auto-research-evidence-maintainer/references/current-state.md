# Current state checkpoint

Use this file for fast orientation. Verify drift-prone counts with read-only commands before publishing them.

## Identity

- Active root: `/Users/USER/Zotero/auto-research`
- Research-brief pre-change protection commit: `6ce9536`
- Research-brief pre-change tag: `evidence-demo-2026-07-30-pre-research-brief-1`
- Previous Librarian reasoning/presentation implementation commit: `8e9c4c1`
- Research-brief implementation/documentation commit: use the current shared-core `git log -1`; this file does not self-reference its commit hash
- Current desktop artifact source: `9985386`
- Current desktop tag: `evidence-demo-2026-08-09-macos-workbench-preview-4`
- Current desktop version: Auto Research `0.5.0-preview.1` for Apple Silicon macOS
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
| latest clean release tests | 722: 467 shared, 151 macOS, 104 Windows contracts |

Target DOI `10.1016/j.jnucmat.2018.08.031` has 231 independent facts and 403/403 source-localizable automatic records at this checkpoint.

Independent read-only browser acceptance: a tungsten question returned 65 candidates (0 direct, 5 adjacent, 60 expansion), with 5 report citations and a 200 brief export. The console was clean; reload showed 0 history, disabled export and no runtime warnings.

## Honest completion boundary

- The application and currently published evidence are stable for demonstration.
- The original at-least-30-processed-paper goal is incomplete: 13 more papers are needed to move from 17 to 30; 33/50 remain not data-ready.
- Adversarial DeepSeek agreement is not independent scientific accuracy. A human gold standard remains missing.
- The only supported product shape is a personal desktop workbench. The current tested artifact is Auto Research `0.5.0-preview.1` on macOS. Windows has backend parity but `installer_ready=false`, no Setup and no clean-machine acceptance. Browser workbench, mentor read-only, ports 8765/8766 and ngrok are retired historical compatibility paths, not sharing options.
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
- The `0.5.0-preview.1` artifact includes Librarian V3, official/private/all federated exact search, reviewed private CSV/TSV/XLSX imports and the shared offline/private UI. It remains a checkout-dependent internal preview, not a portable or public release.
- The 291 current visuals include the unchanged 243-asset historical pre-cloud freeze; 243 is not the current total.

## Pre-change protection point

- Commit: `6ce9536`
- Tag: `evidence-demo-2026-07-30-pre-research-brief-1`
- This commit is also the previous Librarian reasoning/presentation release commit; the protection tag is not the new stable tag.

## Stable recovery artifacts

- macOS App: `/Users/USER/Zotero/auto-research/desktop/macos/dist/Auto Research.app`.
- macOS DMG: `/Users/USER/Zotero/auto-research/desktop/macos/dist/Auto-Research-0.5.0-preview.1-macOS-arm64.dmg`; SHA-256 `cad6c8b8f2769b9006a0820fabf9ee22f03ca36ae5593f6f2e96adc88f2374a1`.
- Historical stable SQLite snapshot: `/Users/USER/Zotero/auto-research-backups/experimental_evidence-librarian-brief-stable-2026-07-30-v1.sqlite`; recorded SHA-256 `d62dc5c43ac9e0fb97e0ad2ecb85deaf50447fb7a036acae5147e8f6111236f6`. Do not infer that the current active user database still has this hash.
- Git bundle belongs under `/Users/USER/Zotero/auto-research-backups/` and must be generated after the release documentation commit while preserving the artifact tag at `9985386`.
- Previous stable tag and artifacts remain available under `evidence-demo-2026-07-30-librarian-reasoning-stable-1` and the `librarian-reasoning-stable-2026-07-30-v1` backup names.

Do not restore over the live tree. Verify a snapshot in a separate location before asking the user to switch.
