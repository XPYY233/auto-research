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

## Recover interrupted 0.8 work

Before editing 0.8, read the top `PROJECT_HANDOFF.md` checkpoint section. Treat `0450345` as the committed development baseline and `888aae5`/build15 as the user rollback release until a later handoff supersedes them.

- Preserve the uncommitted evidence-detail workbench files and user production DB/paper_056 artifacts; never reset, clean or broadly stage them.
- Use existing project Codex threads for frontend/macOS, core, Windows and security. Root is the only Git writer; every other thread receives an exact file list and returns a no-stage/no-commit handoff.
- The macOS trusted runtime plus personal-suggestion and selected-evidence-chat prepared adapters are committed. Finish Librarian and literature-extraction adapters, then freeze one four-scope registry/snapshot/HTTP contract; wire and validate macOS before synchronizing Windows. Do not implement provider, prompt, search, package or private-repository algorithms in platform folders.
- On a hot machine, run at most two development threads and only small targeted tests. Run the shared full suite, build and real App flow serially once after interfaces freeze.
- Do not sync release web hashes or increment final build identity until all shared web assets are frozen. Windows remains `installer_ready=false` until real Win11 acceptance.

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
