# Operations and release

## File map

| Need | Read | Main implementation |
|---|---|---|
| handoff and rules | `PROJECT_HANDOFF.md`, `AGENT.md` | n/a |
| DB and six columns | `docs/irradiation_evidence_database.md` | `src/auto_research/evidence/db.py`, `six_column.py`, `fact_model.py` |
| extraction and quality | `docs/adversarial_quality_gate.md` | `deepseek_extraction.py`, `quality_pipeline.py`, `prompts.py` |
| visuals | `AGENT.md` visual sections | `visual_evidence.py` |
| search/Librarian | `docs/SEARCH_AND_AGENT_ARCHITECTURE.md` | `search_index.py`, `agent_runtime.py` |
| selected chat | `AGENT.md` evidence-chat section | `context_chat.py` |
| upload/dedup | `README.md` | `uploads.py`, `document_recognition.py` |
| web UI | `STABLE_RELEASE.md` | `webapp.py`, `web/index.html`, `web/app.js`, `web/app.css` |
| health/release | `MAINTENANCE_WORKFLOW.md` | `maintenance.py`, `db_health.py`, `self_check.py` |
| Zotero corpus | `AUTO_RESEARCH_HANDOFF_1000.md` | `zotero/`, `acquisition/`, root `scripts/` |

All implementation paths above are under `src/auto_research/evidence/` unless stated otherwise.

## Start services

Editable:

```bash
PYTHONPATH=src python3 -m auto_research.cli evidence-serve --host 127.0.0.1 --port 8765
```

User launcher: `/Users/USER/Zotero/打开本地编辑工作台.command`.

Read-only sharing: use `/Users/USER/Zotero/创建导师公网链接.command`; it starts read-only port 8766 and ngrok. Never expose the editable port.

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
