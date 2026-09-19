---
name: auto-research-evidence-maintainer
description: Maintain and continue the Auto Research scientific-evidence desktop product in its sanitized development checkout. Use when Codex needs to inspect, debug, extend, extract papers into, search, validate, release, hand off, or safely publish this project's six-column evidence database, visual evidence, DeepSeek Harness runtime, dataset bundles, Fusion desktop workbench, or versioned evidence packages. Also use when a new account or agent must recover prior project decisions without relying on conversation memory.
---

> 工程重建规则（2026-09-19）：当前进度只见 PROJECT_HANDOFF.md。开发使用净化后的独立仓库，原科研目录仅作保护源。
> 已批准计划取代下文旧候选覆盖规则：每份候选二进制独立身份，禁止覆盖 build54；源码提交不自动构建。
> 必需工程检查统一为 `python scripts/check.py`，共享与 Mac pytest 分别串行；Windows 冻结。
> 旧计数和回退记录是历史证据，不能作为当前验收。问题、分支、PR 和发布必须关联。


# Auto Research Evidence Maintainer

## Establish authority

Locate the sanitized development checkout through its `PROJECT_HANDOFF.md` and Git remote. Do not develop in the protected original Zotero research directory. Treat current project instructions as authority over historical account memory.

Read in this order before changing anything:

1. `PROJECT_HANDOFF.md`
2. `AGENT.md`
3. `docs/ARCHITECTURE_GOVERNANCE.md`
4. `STABLE_RELEASE.md`
5. `git status --short`

For installed-App diagnosis/acceptance, also read the effective-workspace check
in `references/operations.md`; a correct build identity does not prove that the
App is using the correct data root.

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
- The production Librarian and selected-evidence AI use the pinned DeepSeek Harness composition around local four-type coverage recall. Harness remains read-only and cannot receive shell, filesystem, PTY, editor, subagent or arbitrary network tools; provider choice cannot change local hard conditions or source scope.
- The product is one personal desktop workbench. The current 1.2 release line is macOS; Windows 1.2 migration is frozen until the user approves Mac. The embedded frontend, loopback service and historical read-only permissions remain internal implementation/test boundaries, not separate browser products.
- Distribution separates the signed desktop App from versioned, verified evidence packages and from each user's private library. End-user AI extraction and Librarian calls are BYOK through provider-separated platform secure credential envelopes; never package a developer key or accept an arbitrary provider URL.
- The official distribution database is `distribution-sqlite-v1`, not EvidenceDB v12. Its canonical activity selector is `<app-data>/official-packages/active.json`; only signature/checksum verification followed by `OfficialEvidenceRepository` audit may atomically change it.
- Applications trust only reviewed public keys in `auto_research.product.trusted_publishers`. Maintainer signing private keys stay outside Git, App data, logs and `.aresearch` files; never silently regenerate a missing key under an existing `key_id`.
- Keep platform logic thin. macOS and Windows may implement lifecycle, native selection, credentials and a protected bridge, but must reuse product package, identity, audit and federated-search contracts rather than copy them.
- Codex develops the project. Application runtime AI is limited to the audited provider registry (initially DeepSeek and OpenAI), with fixed endpoints/models and server-prepared informed actions.

## Continue from the 1.2 Mac release line

Read the candidate ledger linked from `PROJECT_HANDOFF.md`. Source commits, installed binary identity, functional acceptance and scientific validation are different claims. Current quota reserve comes from the latest user instruction and handoff, not a fixed number in this skill.

Use Issue → branch → PR → checks → merge. Ordinary commits do not produce an App. A candidate gets a unique identity, cannot be overwritten, and is packaged from the same App after acceptance; follow `docs/decisions/0003-candidate-identity.md`. A collaborating task that cannot finish must return an honest handoff, not an invented success.

- Preserve all original research data, user artifacts and recovery archives. Historical deletion requests do not authorize new cleanup or restoration over current state.
- Use existing project Codex threads for frontend/macOS, core, Windows and security. Root is the only Git writer; every other thread receives an exact file list and returns a no-stage/no-commit handoff.
- Keep the four-scope prepared-action registry, pinned Harness runtime and provider registry as the only billable AI authority. Do not restore legacy direct Librarian, context-chat, personal-suggestion or workflow model routes. Harness failure must remain explicit and must not fall back to an old loop.
- Use `ai-readiness-v1` as the only renderer-facing AI availability projection. Built-in DeepSeek/OpenAI and an optional user-configured public HTTPS OpenAI-compatible endpoint must share provider-separated credentials, connection verification, per-scope capability verification and the same prepared-action budgets. A custom endpoint never expands Harness tools or source scope.
- Keep `PaneLayoutController` as the only pane geometry authority and Fusion as the only navigation/content owner. Collapse must preserve tabs, requests and scroll state; `48/96px` are snap thresholds, not content minimum widths.
- On a hot machine, run at most two development threads and only targeted tests. Run the shared full suite, build and real App flow serially once after interfaces freeze.
- Keep ordinary development and candidate production separate. Bind every candidate to reviewed source, locked dependencies and a unique manifest; preserve the verified rollback App and required data recovery materials.
- Clean only identified, reconstructible duplicates after identity/hash checks. Keep historical migration archives, uncommitted-change protection and non-reconstructible signing/data inputs. Never auto-delete a worktree containing unknown scientific data.
- Keep the Fusion DOM, `DocumentTabStore`, at most two editor groups and single navigation controller authoritative. Reuse audited services behind narrow controllers; do not restore hidden 0.8 pages, duplicate navigation listeners, cross-view DOM reparenting or CSS skin overlays.
- Four-type filtering is a server-side `evidence-filter-v1` contract. Never reintroduce a client-only filter over the first page or first 100 results.
- For workflow acceptance, feed actual service-generated DTOs into production renderer tests; handwritten success fixtures cannot prove cross-layer compatibility. Keep wire units explicit (review-queue-v1 scores are 0–100, not fractions). After publication, verify the receipt, refreshed paper catalogue, reachable review candidates and search together; a failed queue request must not become an empty successful queue.
- `dataset-bundle-v1` exports JSONL, real Parquet, a data card and manifest from one safe canonical projection. Split deterministically by paper, default private data off and binary assets out, and show missing/unreviewed/rights risks before export.
- `official-package-v2` may ship only when every declared PDF and visual asset passes identity/hash/ordinary-file validation and the package remains below 2GB. Never replace a missing report with a similarly titled journal article, silently publish 59/60, or weaken the importer.
- For tables and figures, display only the stored, source-local PyMuPDF visual asset and its audited metadata. Never redraw a chart, fabricate cells, borrow a workspace asset for an official/private source, or expose local paths in a renderer DTO.
- Build and package testing must use isolated schema-v12 snapshots. Preserve the production database byte-for-byte and keep paid model calls disabled unless a later task explicitly requires and authorizes them.
- Windows 1.2 work is paused. Do not edit, test or build `desktop/windows/**` until the user explicitly approves the Mac 1.2 workflow.
- Preserve the successful offline Build Kit rules: materialize Python/wheels/Inno 6.7.3/WebView2/source/package on Mac, verify exact hashes, copy to a short local C: path, use binary mode, close handles before replace/unlink and only bounded-retry WinError 5/32/33. Setup still requires Win11 install/import/search/upload/BYOK/upgrade/uninstall acceptance before changing `installer_ready=false`.

## Choose the safe action level

- For explanation, review, diagnosis, status, or discussion: inspect and report only. Do not mutate files, databases, Zotero, services, or remote systems unless asked.
- For implementation: create a protection point, make the smallest compatible change, test proportionally, update relevant docs, and preserve rollback.
- For extraction: verify a real PDF and identity first. Require explicit `--force-rescan` for already scanned papers.
- For Zotero SQLite mutation: require Zotero closed, a timestamped backup, minimal deterministic edits, and post-reopen verification.
- For public release or GitHub: perform an explicit copyright, credential, path, PDF, screenshot, and production-database scope review.

## Validate before claiming success

Use targeted tests during development. Before a desktop release, run shared
and macOS suites serially and separately (their modules can share basenames),
check production resources in `release-contract.json`, then verify the installed
App's workflow ledger. See `references/operations.md`. Database health, search,
source/highlight and corpus audits must explicitly target an owned isolated
snapshot; never run a CLI that defaults to the production evidence database.
Do not revive retired frontend checks merely to satisfy an old command list.

Retain a failing behavior regression for each correction and verify the actual
test-file handoff. Unrelated old passing tests do not validate new behavior.
Check pasted Chinese input before a billable UI action; automation can silently
drop characters. When UI access times out, distinguish native selection,
completed staged read and inaccessible WebView before blaming the product.
Source replay is diagnostic, never frozen-App acceptance.

Run the full suites and desktop build once after the batch freezes. Never use
the active production database or `paper_056` as disposable package inputs.
Remove only this run's identified temporary fixtures after diagnosis; preserve
unknown or user-owned data.

Interpret a fixed-corpus audit honestly: pending extraction is not necessarily code failure. Never fabricate evidence to make an audit pass.

## Maintain records

Update `PROJECT_LOG.md` for completed work. Put durable rules in `AGENT.md`; keep transient errors out of it. Update architecture or stable-release docs when their contracts change.

For an ordinary source checkpoint, commit intentionally and record the targeted test evidence; do not create a tag, App build or Git bundle. For a user-accepted stable rollback, create the tag, copy the read-only SQLite snapshot outside the repository when required, create and verify one replacement complete Git bundle, record SHA-256 values, then remove the superseded complete bundle after verification. An interruption bundle is allowed only when committed history is otherwise at genuine loss risk. Do not use destructive Git commands to handle existing changes.

Report product stability, corpus completion, and scientific accuracy as three separate claims.
