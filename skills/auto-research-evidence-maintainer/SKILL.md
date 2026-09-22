---
name: auto-research-evidence-maintainer
description: Continue Auto Research engineering rescue and maintenance through its public GitHub workflow, preserving research data and API credentials while fixing core behavior, verifying Mac delivery, and maintaining reproducible handoff. Use for this project's repair, acceptance, release, or restart after an interruption.
---

# Auto Research engineering rescue

## Recover the current context

Resolve this skill's real directory; its parent `../../` is the source checkout. A copied standalone skill needs an explicit checkout containing `PROJECT_HANDOFF.md`. Never default to an old Zotero research directory or a former private checkout.

Read the checkout's `PROJECT_HANDOFF.md` first, then relevant sections of `AGENT.md`, `CONTRIBUTING.md` and `docs/ARCHITECTURE_GOVERNANCE.md`. Check Git status, remote, current GitHub issues/PRs and the installed candidate separately. [State authority](references/current-state.md) identifies the authoritative files. User instructions override stale skill examples and historical logs.

Expected development remote is public `XPYY233/auto-research`. `auto-research-private-history` is a private history archive, never a public push source. Old private issue numbers and public issue numbers are different namespaces; use explicit links. Preserve both commit maps under `docs/history/` when tracing original candidate identities.

## Work in priority order

1. Core correctness: import → preflight → extract → discover figures/tables → automatic AI verification → atomic publication → index → visible catalogue/detail.
2. Recovery: cancellation, timeout, save failure, interrupted finalization, restart and repeated actions without duplicate publication or repeated successful model charges.
3. Independent delivery and research-data recovery, then maintenance boundaries and small UI improvements.

Keep the agreed rescue scope: Apple Silicon Mac, all existing capabilities, Windows frozen. A zero-cycle dependency check or an empty-workspace smoke test proves only its own scope. Do not substitute engineering checks for full installed behavior or scientific accuracy.

Literature verification and publication are AI-automatic. No human-review queue, approve/reject UI or manual prerequisite may return. Historical `manual_review` ciphertext/DB states may remain isolated for compatibility. Unsupported candidates must remain failed/retryable with a reason, never fabricated or automatically waved through. Offline human-labelled scientific evaluation is separate from product operation.

## Reusable execution cycle

- Use or open a public Issue with a synthetic reproduction and concrete acceptance criteria. Make a short branch, preserve unrelated edits, and keep one Git integrator.
- Reproduce the actual failing boundary before fixing it. Prefer public service/HTTP contracts and service-generated DTOs to tests that rewrite source strings or fabricate success responses.
- Make a coherent fix, run the relevant regression, then the repository's required checks. Read [development and GitHub operations](references/operations.md) for exact entry points, privacy checks and interrupted-work handling.
- Submit a PR with the trigger, resulting behavior, tests and compatibility limits. Merge through live server requirements; do not bypass them or claim an unobserved review. Avoid duplicate expensive CI runs for documentation-only work.
- Only build a candidate when the task needs a binary or installation validation. Each binary gets its own immutable candidate identity; source version alone is insufficient. Read [delivery and recovery](references/delivery.md) before building or replacing an App.
- Keep `PROJECT_HANDOFF.md` current, append material completed work to `PROJECT_LOG.md`, and link evidence from the Issue/PR. Do not replicate mutable versions/counts in multiple authority files.

## Preserve the boundaries

Read [research and privacy guardrails](references/guardrails.md) when touching data, AI, credentials, packages or publication. Research PDFs, visual assets, databases, literature packages and API state remain local: not in Git history, PR/Issue attachments, CI logs, release assets or screenshots. Encryption is not permission to upload them.

Use isolated synthetic workspaces for automated verification. Inspect effective paths before any installed-App acceptance; a correct build using the wrong workspace is not a valid test. Never regenerate scientific data to make an engineering check pass.

The latest user instruction controls quota and spending. Do not carry a fixed historical reserve percentage into future work. Permission to use more Codex quota is not permission to buy services, spend application-model credits or publish private material. Use existing authorized actions without inventing extra permission gates.

## Finish or hand off honestly

Report separately: source checks, frozen/installed behavior, data restoration and independent scientific accuracy. Stable delivery requires the agreed full acceptance, not merely a successful build. Preserve a verified rollback and keep incomplete gates open.

Before stopping: record the exact source/candidate, remaining failure, test evidence, ownership of uncommitted files and one actionable next step. Never mark the rescue complete while required independent-Mac or scientific evidence is missing.

For domain workflows, consult [extraction and search](references/workflows.md). [Historical decisions](references/history-and-decisions.md) are background only; never use their versions, paths, quotas or review UI as current authority.
