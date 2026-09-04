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
| web UI | `STABLE_RELEASE.md` | `webapp.py`, `web/index.html`, `web/app.js`, `web/app.css` |
| health/release | `MAINTENANCE_WORKFLOW.md` | `maintenance.py`, `db_health.py`, `self_check.py` |
| Zotero corpus | `AUTO_RESEARCH_HANDOFF_1000.md` | `zotero/`, `acquisition/`, root `scripts/` |

All implementation paths above are under `src/auto_research/evidence/` unless stated otherwise.

## Start the product and internal services

Users open the desktop workbench. Auto Research.app is the current macOS release line; Windows 1.1 migration is frozen. The commands below are maintainer-only internal checks:

```bash
PYTHONPATH=src python3 -m auto_research.cli evidence-serve --host 127.0.0.1 --port 8765
```

The retired root launchers are migration notices only. Do not expose either loopback port or start ngrok for users.

## Read-only maintenance checks

```bash
PYTHONPATH=src python3 -m auto_research.cli evidence-db-health
PYTHONPATH=src python3 -m auto_research.cli evidence-search-benchmark
PYTHONPATH=src python3 -m auto_research.cli evidence-self-check \
  10.1016/j.jnucmat.2018.08.031 \
  --query 温度 --query 硬度 --query 钨 --query 'Wei-Ying Chen' \
  --min-rows 100 --min-highlight-ratio 0.8
PYTHONPATH=src python3 -m auto_research.cli evidence-test-set-audit \
  --config config/evidence_test_set_50.json
```

Before release, reconcile stale run metadata only:

```bash
PYTHONPATH=src python3 -m auto_research.cli evidence-reconcile-runs --older-than-hours 6
```

This command must not change evidence, visuals, review history, or extraction artifacts.

## Code checks

```bash
PYTHONPATH=src python3 -m unittest discover -s src/tests -p 'test_*.py'
python3 -m compileall -q src
node --check src/auto_research/evidence/web/app.js
node --check src/auto_research/evidence/web/fusion_review.js
node --check src/auto_research/evidence/web/document_tab_store.js
node --check src/auto_research/evidence/web/pane_layout_controller.js
zsh -n scripts/*.command
git diff --check
git fsck --full
```

Run real HTTP checks for editable and read-only modes. Verify a read-only write returns 403 and compare SQLite hashes before/after.

## Stable checkpoint

1. Stop extraction and sharing jobs.
2. Reconcile only abandoned run metadata.
3. Copy SQLite outside the repository and hash it.
4. Run full code, DB, fixed-corpus, browser, export, source, and read-only checks.
5. Scan the diff for credentials, PDFs, local secrets, generated noise, and unrelated files.
6. Update durable rules, project log, architecture, stable release, and human handoff as applicable.
7. Commit intentionally and create an annotated date/version tag.
8. Create `git bundle ... --all`, run `git bundle verify`, and record SHA-256.
9. Confirm a clean worktree.

Do not mark a release stable if required work remains or if the fixed-corpus failure is unexplained.

For build27, the release remains blocked until the App-saved provider key passes connection plus isolated literature extraction, Librarian, selected-evidence chat and personal suggestion acceptance. Never ask the user to paste a key into Codex, logs or a shell command. Keep the run at or below the user-authorized 25 billable provider calls and record cancellation/timeout/429/budget behavior separately from successful scientific output. Build25 and build26 must not be promoted: the former failed the DeepSeek reasoning probe, and the latter blocked personal-suggestion capability verification through a frontend/server call-cap mismatch.

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
4. Update `PROJECT_LOG.md`, `AGENT.md`, this Skill and current-state with the exact committed HEAD, last usable release, test evidence and first next step.
5. Create a maintainer-private Git bundle for committed history and a separate text/diff snapshot for uncommitted code only. Never put production SQLite, paper_056, PDFs, API keys or the complete-history bundle into a user kit.
6. Hash and verify the recovery artifacts. Do not call the interrupted checkpoint stable or buildable.
7. On resume, inspect `git status`, restore no files destructively, finish the earliest shared contract, then proceed macOS before Windows.

## New-account skill installation

The project-local folder is canonical:

`/Users/USER/Zotero/auto-research/skills/auto-research-evidence-maintainer`

Install or link it:

```bash
mkdir -p ~/.codex/skills
ln -s /Users/USER/Zotero/auto-research/skills/auto-research-evidence-maintainer \
  ~/.codex/skills/auto-research-evidence-maintainer
```

Invoke with: `Use $auto-research-evidence-maintainer and continue from /Users/USER/Zotero/auto-research.`
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
