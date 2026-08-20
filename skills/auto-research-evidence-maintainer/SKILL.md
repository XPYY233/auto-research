---
name: auto-research-evidence-maintainer
description: Maintain and continue the local Auto Research experimental-literature evidence product in /Users/USER/Zotero/auto-research. Use when Codex needs to inspect, debug, extend, extract papers into, search, validate, release, hand off, or safely publish this project's six-column evidence database, visual evidence, DeepSeek adversarial quality pipeline, Librarian Agent, local editor, or read-only sharing service. Also use when a new account or agent must recover prior project decisions without relying on conversation memory.
---

# Auto Research Evidence Maintainer

## Establish authority

Work from `/Users/USER/Zotero/auto-research`. Treat the project-local files as authority over account memory.

Read in this order before changing anything:

1. `PROJECT_HANDOFF.md`
2. `AGENT.md`
3. `docs/ARCHITECTURE_GOVERNANCE.md`
4. `STABLE_RELEASE.md`
5. `git status --short`

Do not develop in the former iCloud checkout. Preserve unrelated or user-owned worktree changes.

## Route the task

- For current counts, release identity, gaps, backups, and active paths, read `references/current-state.md`.
- For Zotero acquisition or experimental-evidence ingestion/extraction, read `references/workflows.md`.
- For scientific, security, model, visual, public-sharing, and mutation boundaries, read `references/guardrails.md` before implementation.
- For startup, tests, health checks, architecture map, release, rollback, or new-account setup, read `references/operations.md`.
- For why the architecture exists, rejected paths, and major historical phases, read `references/history-and-decisions.md`.

Read only the references needed for the current request. Use `rg` to locate details in `AGENT.md` instead of loading it repeatedly in full.

## Preserve the product model

Keep these invariants:

- Zotero supplies papers/PDFs; `db/experimental_evidence.sqlite` is an independent scientific evidence store.
- Public evidence types remain `item`, `finding`, `table`, and `figure`.
- Numeric records retain the six user fields plus page, locator, excerpt, version, and provenance.
- Separate measured, derived, calculated, and qualitative evidence.
- The local PyMuPDF visual pipeline is production authority. DeepSeek only enriches caption/nearby-text semantics.
- New automatic publication passes the two-branch adversarial gate and optional third review; low-confidence records remain quarantined.
- Search V2 is a disposable projection. Never write index content back into scientific records.
- The Librarian uses a code-reviewed trusted Agent-capable provider for planning and evidence synthesis around local four-type coverage recall. It remains read-only and full-corpus; provider choice cannot change local hard conditions or source scope.
- The product is one personal desktop workbench. The current preview is macOS and the intended end-user target is Windows. The embedded frontend, loopback service and historical read-only permissions remain internal implementation/test boundaries, not separate browser products.
- Distribution separates the signed desktop App from versioned, verified evidence packages and from each user's private library. End-user AI extraction and Librarian calls are BYOK through provider-separated platform secure credential envelopes; never package a developer key or accept an arbitrary provider URL.
- The official distribution database is `distribution-sqlite-v1`, not EvidenceDB v12. Its canonical activity selector is `<app-data>/official-packages/active.json`; only signature/checksum verification followed by `OfficialEvidenceRepository` audit may atomically change it.
- Applications trust only reviewed public keys in `auto_research.product.trusted_publishers`. Maintainer signing private keys stay outside Git, App data, logs and `.aresearch` files; never silently regenerate a missing key under an existing `key_id`.
- Keep platform logic thin. macOS and Windows may implement lifecycle, native selection, credentials and a protected bridge, but must reuse product package, identity, audit and federated-search contracts rather than copy them.
- Codex develops the project. Application runtime AI is limited to the audited provider registry (initially DeepSeek and OpenAI), with fixed endpoints/models and server-prepared informed actions.

## Continue from the 0.9.2 Fusion functional line

Before editing, read the top `PROJECT_HANDOFF.md` release section. The installed checkpoint is `0.9.2-preview.1` build 21: the approved Fusion workbench now restores real four-type evidence details, authoritative table/figure screenshots and the central PDF return chain. It remains an internal preview rather than a complete functional replacement for every 0.8 workflow.

- Preserve the uncommitted production evidence database. The legacy paper_056 run/quality/visual artifacts were explicitly deleted by the user before the 0.8 build and should not be recreated unless a new real extraction is authorized.
- Use existing project Codex threads for frontend/macOS, core, Windows and security. Root is the only Git writer; every other thread receives an exact file list and returns a no-stage/no-commit handoff.
- Keep the four-scope prepared-action registry and provider runtime as the only billable AI authority. Do not restore legacy direct Librarian, context-chat, personal-suggestion or workflow model routes.
- On a hot machine, run at most two development threads and only targeted tests. Run the shared full suite, build and real App flow serially once after interfaces freeze.
- Keep the 0.9 sequence strict. Build21 restored read-only visual evidence and PDF viewing only; do not infer that extraction, private experiment row paging, selected-evidence AI, package mutation or real export is already complete.
- Keep the Fusion DOM, central tabs and single navigation controller authoritative. Reuse audited services behind narrow controllers; do not restore hidden 0.8 pages, duplicate navigation listeners, cross-view DOM reparenting or CSS skin overlays.
- For tables and figures, display only the stored, source-local PyMuPDF visual asset and its audited metadata. Never redraw a chart, fabricate cells, borrow a workspace asset for an official/private source, or expose local paths in a renderer DTO.
- Build and package testing must use isolated schema-v12 snapshots. Preserve the production database byte-for-byte and keep paid model calls disabled unless a later task explicitly requires and authorizes them.
- Windows 0.9 work is paused. Do not edit, test or build `desktop/windows/**` until the user explicitly approves the Mac Fusion GUI.
- Windows remains `installer_ready=false` until real Win11 build/install/import/search/upload/BYOK acceptance. Do not turn its source/build kit into a fake Setup.

## Choose the safe action level

- For explanation, review, diagnosis, status, or discussion: inspect and report only. Do not mutate files, databases, Zotero, services, or remote systems unless asked.
- For implementation: create a protection point, make the smallest compatible change, test proportionally, update relevant docs, and preserve rollback.
- For extraction: verify a real PDF and identity first. Require explicit `--force-rescan` for already scanned papers.
- For Zotero SQLite mutation: require Zotero closed, a timestamped backup, minimal deterministic edits, and post-reopen verification.
- For public release or GitHub: perform an explicit copyright, credential, path, PDF, screenshot, and production-database scope review.

## Validate before claiming success

Run the relevant subset and, before a stable release, all of:

```bash
PYTHONPATH=src python3 -m unittest discover -s src/tests -p 'test_*.py'
PYTHONPATH=src python3 -m auto_research.cli evidence-db-health
PYTHONPATH=src python3 -m auto_research.cli evidence-search-benchmark
PYTHONPATH=src python3 -m auto_research.cli evidence-self-check \
  10.1016/j.jnucmat.2018.08.031 \
  --query 温度 --query 硬度 --query 钨 --query 'Wei-Ying Chen' \
  --min-rows 100 --min-highlight-ratio 0.8
PYTHONPATH=src python3 -m auto_research.cli evidence-test-set-audit \
  --config config/evidence_test_set_50.json
python3 -m compileall -q src
node --check src/auto_research/evidence/web/app.js
git diff --check
```

For a desktop/package release, first use synthetic temporary roots and the signed-package target tests. Run the shared full suite and desktop build only once after all platform interfaces freeze. Never use the activity production database or user `paper_056` run artifacts as disposable package test data.

Interpret a fixed-corpus audit honestly: pending extraction is not necessarily code failure. Never fabricate evidence to make an audit pass.

## Maintain records

Update `PROJECT_LOG.md` for completed work. Put durable rules in `AGENT.md`; keep transient errors out of it. Update architecture or stable-release docs when their contracts change.

For a stage checkpoint, commit intentionally, tag it, copy SQLite outside the repository, create and verify a Git bundle, record SHA-256 values, and leave a clean worktree. Do not use destructive Git commands to handle existing changes.

Report product stability, corpus completion, and scientific accuracy as three separate claims.
