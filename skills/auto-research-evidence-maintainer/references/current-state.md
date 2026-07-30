# Current state checkpoint

Use this file for fast orientation. Verify drift-prone counts with read-only commands before publishing them.

## Identity

- Active root: `/Users/USER/Zotero/auto-research`
- Stable commit before handoff: `8c57825`
- Stable tag: `evidence-demo-2026-07-29-librarian-recall-stable-1`
- Handoff documentation tag: `evidence-demo-2026-07-30-project-handoff-skill-1`
- Release: `2026.07.29-librarian-recall-stable.1`
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
| automated tests at checkpoint | 196 |

Target DOI `10.1016/j.jnucmat.2018.08.031` has 231 independent facts and 403/403 source-localizable automatic records at this checkpoint.

## Honest completion boundary

- The application and currently published evidence are stable for demonstration.
- The original at-least-30-processed-paper goal is incomplete: 13 more papers are needed to move from 17 to 30; 33/50 remain not data-ready.
- Adversarial DeepSeek agreement is not independent scientific accuracy. A human gold standard remains missing.
- Persistent group hosting is not implemented; public sharing depends on the local Mac and a temporary ngrok URL.
- GitHub is not configured. `origin` points to a local historical bundle.

## Recovery artifacts

- SQLite: `/Users/USER/Zotero/auto-research-backups/experimental_evidence-librarian-recall-stable-2026-07-29-v1.sqlite`
- SQLite SHA-256: `c9d63be31ad660294e52a7d093d37d7c4bbbfb22b3c2e5138005070f6f3b5038`
- Git bundle: `/Users/USER/Zotero/auto-research-backups/auto-research-librarian-recall-stable-2026-07-29-v1.bundle`
- Bundle SHA-256: `903f8e95c72727f8e92bc2c6f1357bb554f6051f0313bfeb619066feda88f9b4`
- Handoff SQLite copy: `/Users/USER/Zotero/auto-research-backups/experimental_evidence-project-handoff-skill-2026-07-30-v1.sqlite`
- Handoff SQLite SHA-256: `b1c9703f46577e6caac1246ca79a89f02ac4817a5ff93cbfae52b35680d264ee`
- Standalone skill archive: `/Users/USER/Zotero/auto-research-backups/auto-research-evidence-maintainer-skill-2026-07-30-v1.zip`

Do not restore over the live tree. Verify a snapshot in a separate location before asking the user to switch.
