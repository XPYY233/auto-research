# Build54 installed acceptance — single candidate

Updated: 2026-09-04. Status: **not accepted; no stable release**.

## Workspace correction — 2026-09-04

The earlier acceptance checked the installed binary but failed to check its
effective data root. `project-root.txt` and the running process cwd both pointed
to `/private/tmp/auto-research-build54-extract.XignFQ`, an extraction acceptance
snapshot with incomplete visual files. Therefore the earlier workspace checks
must not be described as tests of the user's canonical workspace. Independent
official-package checks remain scoped to their own source.

For the latest missing Table 4/image report, the immutable release snapshot's
four table and ten figure records for DOI `10.1016/j.jnucmat.2018.08.031` were
compared to the canonical workspace assets: all fourteen files existed and
matched the recorded SHA-256. No production SQLite was directly read or edited.
The App was quit normally, its saved root restored to
`/Users/USER/Zotero/auto-research`, and the same installed build54 reopened.
The process cwd was checked again. Real WebView clicks now display Table 4's
original table and Figure 10's force/displacement plot in the right editor.
No App build, model call, package rewrite or deletion was needed. The temporary
root is preserved because it may contain isolated extraction work.

Source-only prevention in this batch: persisted OS-temporary roots fail closed
with a specific startup message; explicit/process-local acceptance roots remain
supported and do not consume the saved preference. Fusion visual-detail failures
now terminate with a visible state rather than a silent text-only summary;
workspace image errors no longer instruct users to reimport the official pack.
These guards are not yet in the installed App. No new build is allocated.
The verified-grid, AI-quality, dataset and remaining UI queue stays open; a source
image is not a claim of reviewed machine-readable cells.

Recheck at 2026-09-04 16:33 local time: the saved root and running App process
cwd both remained the canonical workspace. The already-installed build54
displayed Table 4's actual source image (including its numeric rows) and, after
a real UI click, Figure 10's load/displacement plot in the second editor.
Table 4 still explicitly has no verified structured rows; image visibility
does not close that separate data-reuse gate. No new App, model call or data
mutation was needed for this recheck; prevention changes remain source-only.

Targeted validation: 73 desktop-runtime/Fusion-detail/Fusion-contract tests
passed serially (1.58s), JavaScript syntax and release web-asset hashes passed,
and the scoped diff passed whitespace checks. The initial hash-check invocation
omitted its required path argument; it was corrected and rerun successfully.

## Authority and order

Librarian coverage source repair (2026-09-04; not installed): bounded recall
now spends its existing 12 local queries on material/condition/property
conjunctions before broad material-only searches. The 64-document pool and
16-model-evidence bound are unchanged. Selection reserves an eligible
representative per evidence type, without admitting expansion records or
changing direct/adjacent classification. Missing temperature stays missing;
conditions are not borrowed from a paper title or another bundle.

Read-only replay against the existing immutable build54 workspace snapshot
(published Search V2, limit 8 per query, no refresh/model/production DB) kept
58 candidates and selected 15 direct plus 1 adjacent: 4 figures, 9 findings,
2 items and 1 table. The table still lacks the temperature dimension. The old
query ordering on that same snapshot selected 7 direct plus 5 adjacent; the
earlier live direct=0 result was not reproduced after correcting the data root.
This is bounded-recall improvement, not proof that the installed full-source
Librarian/model failure is resolved. Actual answer/citation adequacy remains open.

84 targeted assembler/preflight/Librarian/security/runtime tests passed, including
rare eligible type retention and exclusion of a table missing two conditions.
No additional model call, changed budget, new build or scientific-data mutation.

Selected-table AI source repair (2026-09-04; not installed): a small shared
context authority now reads the same official table service / opaque workspace
resolver used by the detail view. Only verified rows are frozen into the
selected-evidence prompt, with headers, units, uncertainty and review version;
missing/pending/rejected tables send no cells. Large contexts fail explicitly
(500 cells / 24,000 cell characters), not by silent truncation. No raw asset,
local path, reviewer note or private experiment is sent. Workspace-to-official
linking still belongs to the existing DOI/PDF/page/asset identity service.

The prepared action now has a separate table-content unit. Its bounded opaque
handle stores only the public identity; consuming authorization rereads the
authority, so changed/withdrawn/reviewed rows or unavailable sources invalidate
the old snapshot even when the Search V2 fingerprint is unchanged. Both Mac
production and frozen-smoke composition inject this authority and reuse the
same workspace resolver as the UI. One model call and the existing output
contract remain unchanged.

107 targeted tests passed serially in 10.33s (5 existing PyMuPDF/SWIG deprecation
warnings): context/assembler/SDK prompt, authorization, Mac composition/routes,
workspace resolver/exact source link and official table service. Python compile
and scoped diff checks passed. These prove data delivery and gates, not the
model's numerical interpretation. Actual installed Table 3 AI must still report
the visible values and uncertainties correctly before its failure row is closed.

Existing frontend task completed a read-only collaboration review: prepare
captures the visible detail's four-field identity and replies bind to their
original conversation. It found a separate P1: switching tabs while authorizing
can clear another tab's identical draft because clearing rereads the active host.
Add exact outbound identity/history assertions and bind draft clearing to the
original host in the later frontend batch; no frontend files were changed here.

Dataset source repair (2026-09-04; not installed): the renderer now accepts the
builder's `paper.title/doi/year` missing-field keys and complete rights-risk
lists instead of rejecting lists over 100 or truncating long public identities.
The existing 100,000-record bound is paired with a 100,000-paper bound, and the
renderer validates those limits, identities and counts. Risks render 50 per page,
retain the complete list, preserve acknowledgements when paging and ignore stale
page callbacks after a new plan. Long identities wrap within the pane. No rights
acknowledgement is bypassed and binary assets remain excluded from the dataset.

84 targeted builder/service/Fusion tests passed serially (1.87s), including a
real builder → export service → JavaScript projection with 123 synthetic papers,
246 risks, missing paper fields and long asset identities. This establishes the
contract repair, not installed-App export success; native destination selection,
actual JSONL/Parquet consistency and saved receipt remain open. No model, build,
DMG, production SQLite or Windows work was performed in this source batch.

- Installed `/Applications/Auto Research.app` manifest was read on 2026-09-04:
  `build_number=54`, `release_status=candidate`,
  `core_commit=9f77affea1714282a48e6c7e0ef22a7729683c4d`.
- Keep the same 1.2.0/build54 batch. Do not allocate build55, create a DMG,
  duplicate UserKit, or call a passing source test a passing user workflow.
- Finish the existing acceptance and repair queue before expanding the new
  table-display issue. Collect failures first, fix one ordered aggregate, and
  rerun the failed installed-App paths before packaging.
- Root alone integrates/Git-writes. Windows stays frozen. Production SQLite,
  `paper_056`, current private data and the immutable official package are not
  test or cleanup inputs. No experimental import is confirmed into the live
  private library during these checks.

## Evidence, not broad completion claims

The 2026-09-02 build used the clean `9f77aff` checkout. Its recorded serial
source gate was 1,152 shared tests (80 explicit skips), 331 macOS tests,
frozen smoke and signature checks. The App was installed in the existing
build54 slot; no new build number or DMG was generated. Those checks do not
prove scientific answer quality or the remaining installed workflows.

| Flow | Observed in the installed candidate | Verdict |
|---|---|---|
| Literature and search | Independent catalog, title/author/DOI field, prominent intake/extraction controls and wide precise-search input present; `辐照温度` returned 69 items, 8 findings, 1 table, 5 figures | Basic flow observed; 100+ paper/zoom matrix still open |
| Official source | 2026-09-04 load terminated with active 1.1.0, 59 papers, 3,142 items, 936 findings, 49 tables, 242 figures, 59 PDFs, 291 visual assets and direct search action | Read/status passed; do not infer new-package publication |
| Official Table 3/PDF | Source image and verified 5×4 grid visible; PDF page 5, zoom controls and return-to-source worked on 2026-09-02 | This exact source/detail passed; other tables are not implicitly verified |
| Selected-evidence AI | Real DeepSeek reply and encrypted history returned, but it claimed exact hardening differences were unavailable although the visible grid contains ΔH 1.07±0.06 / 1.23±0.15 / 1.01±0.07 GPa | **Fail: context/answer adequacy** |
| Librarian | Real one-call answer around 6 seconds, progress and four clickable references worked; compound 300 °C HEA/316H hardening+microstructure question returned direct=0/related=4 despite relevant same-paper evidence | **Fail: direct-evidence coverage/classification**; do not fix by relabeling unrelated evidence |
| AI elapsed feedback | Librarian total showed 190 seconds while the current model event was about 6 seconds; personal trace used generic citation wording | **Fail: timing/stage presentation**; distinguish consent time from model time |
| Personal AI | Safe 108×8 sample preview paginated; one real AI call returned editable column meanings/reasons and a terminal 100% state; no confirmation/import performed | Execution passed; no measurement sequence was produced for this mixed long-form sample, so scientific import usefulness still requires a representative numeric experiment |
| Settings/API | 2026-09-04 settings and model controls visible after module changes; saved-key/connection-needs-validation, Harness availability and four scope states distinct; no key read or changed | Basic state display passed; previous transient blank setting state remains unconfirmed |
| Dataset plan | 2026-09-04 default private-off plan terminated with `dataset_plan_invalid · 数据集计划格式无效` | **Fail: export cannot continue** |
| Pane controls | Explicit context hide/restore retained central settings and restored width 244; opening a finding retained document tabs | Button path observed only; mouse drag and all narrow layouts remain open |
| Table without verified grid | Prior installed check of SiC test-matrix detail remained at `正在读取结构化行列` | Follow-up queue; do not silently claim a structure exists |

## Bounded diagnostic findings

1. `evidence/harness_business_action.py::_selected` freezes the sanitized current
   document plus compatible neighbors. The verified structure lives separately;
   audit the full provider payload before changing the allowed scientific
   context. Readiness is not proof that this payload contains the grid.
2. `fusion_package_center.js::publicDatasetPlan` rejects `rights_risks.length >
   100`, while `DatasetBundlePlan.public_dict()` emits the complete risk list.
   A no-network synthetic projection check on 2026-09-04 accepted one valid
   risk and rejected 101 valid risks with identical valid counts. This is a
   confirmed contract mismatch, not yet proof of the live response's sole
   rejection branch. Resolve with a bounded, complete backend/UI contract,
   never by dropping risks or auto-acknowledging them.
3. `fusion_ai_experience.js::stopTimer` clears the interval but retains the
   start time; later render calls derive elapsed from wall time again. Audit
   terminal/history and authorization timing before a repair.
4. Computer Use `drag` returned `noWindowsAvailable` while ordinary App state
   reads/clicks still worked. This is an automation limitation, not evidence
   that pointer resizing passed or failed. An AX click+Home did not establish
   separator focus, so keyboard resizing is also not counted as passed.
5. The existing function collaboration task stopped with a usage-limit error
   before delivering its read-only diagnosis. Do not claim delegation output
   was completed; root may continue local read-only checks without spawning
   replacement agents or adding heavy parallel work.

## Remaining ordered acceptance and repair gate

1. Finish isolated numeric-experiment, literature cancellation/resume/finalize,
   package/dataset saved-file/receipt/restart paths and the pane/history matrix.
   Skip repeated paid connection probes when existing valid proofs suffice.
2. Consolidate the selected-context, Librarian coverage, dataset plan and
   task-feedback failures with any remaining failures. Repair existing shared
   services/controllers; do not restore legacy billable routes or duplicate UI.
3. Targeted semantic and UI regressions must include real-shaped payloads and
   representative corpus sizes, not just small success fixtures. Then run one
   serial full gate and overwrite only this candidate.
4. Re-run the failed real flows against the installed artifact. Only after
   the entire user-flow ledger passes: one DMG, tutorial, independent package
   verification, and removal of the temporary build worktree after identity
   checks. Human scientific-quality gates remain separate from software tests.
5. User-requested closeout: after this repair/acceptance round, use the
   skill-creator workflow to streamline the maintainer Skill and project rules.
   Remove stale duplicated release conclusions, centralize current acceptance,
   preserve ordered work and single-candidate/retention/coordination boundaries,
   and validate the skill. Interim checkpoint banners are not completion of
   that governance task and must not displace the main product work.
