# Operations and release

## File map

| Need | Read | Main implementation |
|---|---|---|
| handoff and rules | `PROJECT_HANDOFF.md`, `AGENT.md` | n/a |
| DB and six columns | `docs/irradiation_evidence_database.md` | `src/auto_research/evidence/db.py`, `six_column.py`, `fact_model.py` |
| extraction and quality | `docs/adversarial_quality_gate.md` | `deepseek_extraction.py`, `quality_pipeline.py`, `prompts.py` |
| visuals | `AGENT.md` visual sections | `visual_evidence.py` |
| search/Harness | `docs/SEARCH_AND_AGENT_ARCHITECTURE.md`, `config/auto-research-harness.cordis.yml` | `harness_business_action.py`, `harness_federated_backend.py`, `../ai/harness_*.py` |
| selected chat | `AGENT.md` evidence-chat section | Harness selected-evidence scope; legacy `context_chat.py` is compatibility only |
| dataset export | `docs/ARCHITECTURE_AUDIT_1_1.md` | `../product/dataset_bundle.py`, `dataset_bundle_sources.py`, `dataset_export_service.py` |
| official package v2 | `PROJECT_HANDOFF.md` | `../product/official_package_assets.py`, `official_package_v2_release.py` |
| scientific audit | `docs/ARCHITECTURE_AUDIT_1_1.md` | `scientific_release_audit.py` |
| upload/dedup | `README.md` | `uploads.py`, `document_recognition.py` |
| web UI | candidate ledger linked by `PROJECT_HANDOFF.md` | release-contract-listed Fusion scripts, `web/index.html`, `web/workbench.css` |
| health/release | `MAINTENANCE_WORKFLOW.md` | `maintenance.py`, `db_health.py`, `self_check.py` |
| Zotero corpus | `AUTO_RESEARCH_HANDOFF_1000.md` | `zotero/`, `acquisition/`, root `scripts/` |

All implementation paths above are under `src/auto_research/evidence/` unless stated otherwise.

## Start the product and internal services

Users open the installed desktop workbench. Mac 1.2 acceptance uses the exact
installed candidate and its protected internal server; Windows migration is
frozen. Do not start a legacy browser server as a substitute for App acceptance.
For isolated runs, see the effective-workspace check below.

## Read-only maintenance checks

Instantiate the relevant service with an explicitly owned immutable/disposable
snapshot. Verify the effective database path before running health, search,
source or corpus checks. The historical CLI defaults to the project database
and some subcommands have no database override: do not run them unscoped in the
canonical checkout. Run reconciliation only on disposable acceptance state;
it writes run metadata and is not a read-only diagnostic.

For performance changes to scientific clustering/export, compare full canonical
output fingerprints against the prior implementation on the same immutable
snapshot. Fast approximate scores may reject impossible pairs only when they
are proven upper bounds; they must not silently replace acceptance thresholds.

## Code checks

```sh
.venv/bin/python scripts/check.py
```

Use `CONTRIBUTING.md` for the locked development environment and dependency audit. This single entry runs shared and Mac pytest in separate serial processes, rejects missing prerequisites/unexpected skips, and checks production resources. Use targeted tests during edits; the complete entry is the PR gate. It does not read production research data or call paid models.

## Stable checkpoint

All acceptance mutations use an owned disposable snapshot, never the live
scientific/private database. Resolve rollback storage from the primary Git
checkout, not a release worktree's parent; verify the destination before backup.
The installer path test exercises both layouts without installing an App.

1. Confirm no active extraction or sharing job will be interrupted.
2. Reconcile abandoned metadata only in the isolated acceptance copy.
3. Use an identified immutable snapshot; hash protected files without using them as test inputs.
4. Run code, isolated DB/corpus, installed WebView, export, source and read-only checks.
5. Scan the diff for credentials, PDFs, local secrets, generated noise, and unrelated files.
6. Update durable rules, project log, architecture, stable release, and human handoff as applicable.
7. Commit intentionally. Create a stable tag only after required user acceptance.
8. At an accepted stable checkpoint, replace the one complete recovery bundle, verify it, then remove its superseded copy; ordinary fixes need no bundle.
9. Confirm a clean worktree.

Do not mark a release stable if required work remains or if the fixed-corpus failure is unexplained.

Provider connection, scope capability and actual business success are separate
gates. Use the App-saved key through prepared actions; never request keys in
Codex, logs or shell arguments. Stay within the acceptance call budget. Record
cancellation/timeout/429/budget behavior separately from scientific output.
Rejected answers must stop progress honestly; never weaken citation/number
checks merely to pass the UI.

## Windows offline Build Kit

1. Materialize the exact Python 3.12 runtime, locked wheelhouse, Inno Setup 6.7.3, WebView2 Evergreen x64, source archive and official package on the Mac side.
2. Verify every tool and payload against the release manifest before copying. Do not ship cloud placeholders or let Windows fetch dependencies from the public network.
3. On Windows, copy the entire kit to a short ordinary local path such as `C:\\AutoResearch`; do not build from OneDrive, a mobile disk or a previewed archive.
4. Open archives/PDF/SQLite/tabular files with `O_BINARY`; guard absent `os.fchmod` on Python 3.12 Windows; close every descriptor before replace/unlink/remove.
5. Retry only WinError 5/32/33 for bounded Defender/indexer interference. All other errors fail closed.
6. A generated Setup is still a release candidate until real Win11 install, package import, search, PDF, upload, BYOK, upgrade and uninstall pass. Keep `installer_ready=false` until then.

## Interrupted-development checkpoint

When budget, heat, time or an external interruption stops a multi-thread change:

1. Tell every project thread to stop new edits/tests/builds and return an exact file/status handoff.
2. Commit only independently complete, reviewed slices. Never commit an unverified UI/platform half-route just to make the tree clean.
3. Record remaining modified/untracked code separately from user DB/run artifacts in `PROJECT_HANDOFF.md`.
4. Update the handoff and acceptance ledger with source/installed identity, remaining gates and next step; keep transient hashes out of durable Skill rules.
5. Normally retain committed history plus the existing verified recovery bundle. Create an interruption bundle only for genuine uncovered loss risk; preserve uncommitted source separately if necessary, excluding protected data and secrets.
6. Verify any necessary new recovery artifact before removing its superseded duplicate. Do not call an interrupted checkpoint stable or buildable.
7. On resume, inspect `git status`, restore no files destructively, finish the earliest shared contract, then proceed macOS before Windows.

## New-account skill installation

The skill in the sanitized checkout is canonical: `<checkout>/skills/auto-research-evidence-maintainer`. Install it from that checkout using the user's normal skill installation workflow. Do not point new installations at protected recovery copies or overwrite an existing installation without checking its ownership.

# Effective workspace check (before installed-App acceptance)

Verify the installed manifest and the running process data root separately.
On macOS, compare `~/Library/Application Support/Auto Research/project-root.txt`
with the process cwd and the intended workspace. A copied database in an
extraction sandbox is not a complete visual corpus, even if its titles/counts
look correct. Never conclude that assets were deleted or the renderer is broken
until the source identity, effective root, image existence and SHA agree.

Use `--project-root` or a process-local `AUTO_RESEARCH_DESKTOP_PROJECT_ROOT` for
isolated acceptance; never write a temporary root into the user's persistent
preference. Record whether a check used official data, a sandbox or the canonical
workspace. After acceptance, restore the prior launch configuration, restart and
verify one table and one figure in the actual App. Preserve unknown sandbox data
for separate review. Missing/invalid image bytes must remain a visible error,
not a text-only apparent success or invented table cells.
