# Candidate delivery and data recovery

## Resolve identities before touching data

Read `PROJECT_HANDOFF.md`, its linked installed-candidate ledger, `STABLE_RELEASE.md`, and `docs/FUNCTION_ACCEPTANCE.md`. The installed App, source main, a locally built test candidate and an existing DMG can have different identities. Inspect the installed manifest and the effective workspace separately.

On macOS the default workspace is `~/Library/Application Support/Auto Research/workspace`. An explicit launch argument, process environment or saved `project-root.txt` may select another location. Inspect before assuming an image is missing. Do not overwrite the user's persistent selection with a test directory.

Raw SQLite file hashing is not a consistent backup of a live WAL database. Use the existing backup mechanism, verify logical records and file hashes, and retain corresponding encrypted-state recovery dependencies. Keep original PDFs, image bytes, units, source identity, review versions and immutable official packages unchanged.

## Build and installation

The public README is the user installation route. Use its prerequisite and build commands; never make installation depend on maintainer data or keys. The builder creates a clean environment from hash locks, excludes research payloads, generates an immutable candidate ID, signs the candidate ad-hoc and runs isolated initialization/HTTP smoke checks.

For explicit installation/release tasks:

1. Verify a clean, committed source and applicable tests; create one candidate with its source, dependency locks and environment manifest.
2. Check App file inventory, symlinks and signature. Relocate it to an owned test directory and run with an isolated user directory and no developer Python/source path.
3. Verify first-run defaults, core usage, restart, cancellation/recovery and actual output files. Empty-workspace HTTP success is useful but cannot stand in for PDF extraction, renderer or export acceptance.
4. Before daily-App replacement, ensure no active extraction is interrupted, preserve the accepted rollback App and snapshot actual workspace data. Use existing transactional installation mechanisms.
5. Verify candidate identity and effective data root after launch; compare logical database records and original assets. Test existing and new functions through real services/UI.
6. Package the same accepted App; do not rebuild after acceptance. Verify the mounted/read-only package payload against the accepted inventory. Do not overwrite a published candidate or Release asset.

No routine source commit needs a new binary. An installation-path test may build its own identified candidate without replacing the user's installed App. Retain recoverable originals and only clean this task's disposable artifacts.

## Acceptance evidence

Use separate columns for source presence, behavior regressions, frozen/installed execution and outstanding gates. Include failures honestly. For model-dependent tests, synthetic offline responses prove orchestration, not real provider quality or billing. An unknown provider outcome is not permission to repeat a charge.

The agreed scientific target is 20 independently labelled papers plus at least one held-out paper, four evidence kinds, 14 dimensions, tested-paper coverage 100%, applicable F1 at least 0.90, no unsourced values or wrong original images. This is offline human evaluation, not a product approval workflow. Do not lower thresholds or substitute AI self-grading to declare success.

A second genuinely clean Mac, tested OS range, real upgrade/rollback and data-preserving uninstall are distinct gates. A temporary HOME on the developer's Mac does not prove them. Track external requirements explicitly while continuing independent core work.

## Distribution and rights

`COPYRIGHT.md` and `THIRD_PARTY_NOTICES.md` own the current rights. Original code currently permits limited downloading/building/installing/local use; it is not generally open-source licensed. Third-party rights and obligations remain unchanged. Do not upload a binary or a source archive containing research data merely because source main is public.

PyMuPDF/MuPDF and all shipped libraries need an actual distribution-rights review and required notices/materials. No automatic commercial license, Developer ID or notarization purchase. Describe ad-hoc signing and missing notarization accurately; never instruct users to disable macOS system security.

## Desktop acceptance process lifetime

Keep an isolated GUI test process attached to a live terminal session. A detached child may exit when its tool parent ends; desktop inspection can then automatically relaunch the App with the real user directory. Verify the running executable, explicit workspace argument and test user directory before UI inspection. After a bundle identifier changes, desktop tooling may cache the old application identity; record that limitation instead of treating a failed tool lookup as a product failure or claiming production GUI acceptance from an isolated copy.

Use `desktop/macos/install_fusion_review.command` for installation. It acquires the same instance lock as the desktop App and keeps the transaction child holding that lock if its supervisor exits. Do not invoke the internal transaction script directly or bypass the running-App refusal; the runtime binary needs no rebuild for a source-only installer change.
