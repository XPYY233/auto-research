# Current state checkpoint

Use this file for fast orientation. Verify drift-prone counts with read-only commands before publishing them.

## Identity

- Active root: `/Users/USER/Zotero/auto-research`
- Pre-change protection commit: `17e6f60`
- Stable tag: `evidence-demo-2026-07-30-librarian-reasoning-stable-1`
- Release: `2026.07.30-librarian-reasoning-stable.1`
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
| automated tests at checkpoint | 226 |

Target DOI `10.1016/j.jnucmat.2018.08.031` has 231 independent facts and 403/403 source-localizable automatic records at this checkpoint.

## Honest completion boundary

- The application and currently published evidence are stable for demonstration.
- The original at-least-30-processed-paper goal is incomplete: 13 more papers are needed to move from 17 to 30; 33/50 remain not data-ready.
- Adversarial DeepSeek agreement is not independent scientific accuracy. A human gold standard remains missing.
- Persistent group hosting is not implemented; public sharing depends on the local Mac and a temporary ngrok URL.
- GitHub is not configured. `origin` points to a local historical bundle.
- Librarian reasoning now has deterministic hard-condition parsing, direct/adjacent/expansion classification, evidence bundles and a five-section research report. This did not change scientific evidence or corpus readiness.
- The local deterministic parser plus bounded history is the only hard-condition authority. DeepSeek plans queries, selects bounded evidence and explains it; it cannot create or rewrite hard conditions. Scientific-notation fluence, equivalent units and particle/material role boundaries are covered by regression tests.
- The 291 current visuals include the unchanged 243-asset historical pre-cloud freeze; 243 is not the current total.

## Pre-change protection point

- Commit: `17e6f60`
- Tag: `evidence-demo-2026-07-30-pre-librarian-reasoning-presentation-1`
- SQLite: `/Users/USER/Zotero/auto-research-backups/experimental_evidence-pre-librarian-reasoning-presentation-2026-07-30-v1.sqlite`
- SQLite SHA-256: `c9d63be31ad660294e52a7d093d37d7c4bbbfb22b3c2e5138005070f6f3b5038`

## Stable recovery artifacts

- SQLite: `/Users/USER/Zotero/auto-research-backups/experimental_evidence-librarian-reasoning-stable-2026-07-30-v1.sqlite`; SHA-256: `bfded1930856019c3413096fc20dee9b7f6b33e3310960b9913b94ee1dd2220e`.
- Git bundle: `/Users/USER/Zotero/auto-research-backups/auto-research-librarian-reasoning-stable-2026-07-30-v1.bundle`; SHA-256: 见相邻 `.sha256`.
- Standalone skill archive: `/Users/USER/Zotero/auto-research-backups/auto-research-evidence-maintainer-skill-2026-07-30-v2.zip`; SHA-256: 见相邻 `.sha256`.

Do not restore over the live tree. Verify a snapshot in a separate location before asking the user to switch.
