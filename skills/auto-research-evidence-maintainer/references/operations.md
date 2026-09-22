# Development and GitHub operations

## Establish the safe checkout

Read `PROJECT_HANDOFF.md`, `git status --short`, `git remote -v` and the latest GitHub issue/PR state. Resolve paths from the current checkout rather than copying a previous machine's absolute path. The public repository must never receive branches or hidden references from the private history archive.

The active local checkout can differ from the task's cwd. Pass its working directory explicitly to every command. Preserve unknown/uncommitted changes. Root is the sole integration writer; delegation requires non-overlapping ownership and a no-stage/no-commit handoff.

## Checks

Use the repository's configured Python runtime and lock files; get exact versions from `CONTRIBUTING.md` and build scripts, not from this skill.

```sh
.venv/bin/python scripts/check.py
.venv/bin/python -m pip_audit --strict -r requirements-dev.lock
python3 scripts/check_repository.py
python3 scripts/audit_public_history.py
git diff --check
```

`check.py` runs required shared and Mac tests separately and serially, plus frontend/resource/dependency-direction gates. Module basename collisions make combined collection unsafe. `unittest discover` does not collect module-level pytest functions. Required missing dependencies or unexpected skipped tests must not become green results. Private corpus tests are separately selected; explicit deselection does not prove corpus accuracy.

Use focused behavior tests while developing and the required full checks once the batch is ready. Follow the current workflow's PR scope rules; do not consume CI minutes with duplicate pushes or rebuild for prose changes. Results must be tied to the exact tested source.

## Public push and PR

- Verify the remote is the public source repository and the branch contains only intended work. Stage explicit paths and inspect the staged list.
- Verify the active identity separately for Git, the CLI and any connector; they can be different accounts. Creating a public Issue does not prove push access. Restore the user-selected account through its supported login flow; never expose tokens or silently migrate ownership. If the CLI cannot reach GitHub but system-proxy-aware requests work, check child-process proxy configuration rather than repeatedly starting new authorizations.
- Author and committer identities must use GitHub noreply addresses. Verify automatic merge identities too; a local config does not control GitHub web commits.
- Privacy scanning includes full reachable history and binary/file-format checks, not only `.gitignore` or the current tree. Test-secret and original-asset exceptions bind exact paths and content hashes.
- Keep real research names/excerpts, local paths, credentials and confidential screenshots out of public issue bodies, comments and logs. Share a synthetic reproduction and aggregate evidence.
- Use a GitHub connector, authenticated CLI or Git transport already available. Never print credential-helper output, tokens, or authenticated URLs. A retry after a network observation failure starts by checking remote state to avoid duplicate mutations.
- Match the actual PR head and required checks before merge. For permission/plan limitations, record the limitation; do not silently disable protections, purchase a plan or claim local checks are server protection.

## History and incident handling

The original Git history and old Issue/PR discussions are private. The sanitized public history has changed hashes; use both committed mappings for provenance. Do not mirror-push the private repository or transfer old releases/Actions artifacts to public without a separate content audit. Privacy changes cannot erase others' existing public copies.

If sensitive material is detected, stop the affected upload and identify scope without reproducing the value. Preserve local recovery evidence; credential rotation and remote history cleanup must address the actual exposure. Never edit test allowlists merely to get the scan green.

## Interrupted work

Persist completed, verified slices through Issue/PR. Leave unfinished edits explicitly owned and described; do not create a release to clear a dirty worktree. Keep private acceptance artifacts outside tracked files. Record live process handles separately from stale log files; an observation timeout is not proof a process ended.

Quota follows the latest user request and live account state. When an authorized limit is reached, finish only necessary safe cleanup and record the next step. Do not repeatedly invoke costly checks with no new information. Continue independently useful work when an external gate is blocked, without claiming that gate passed.
