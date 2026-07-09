# Auto Research

Local, resumable literature automation for fusion materials, radiation damage, cascade simulations, MLIP, and HEA/RHEA research.

It prioritizes open/official sources and your lawful local access path. It does **not** bypass paywalls, crack captchas, use proxy pools, or impersonate institutional access.

## Quick start

```bash
cd auto-research
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
auto-research init
auto-research discover "MLIP cascade HEA radiation damage" --limit 50
auto-research acquire --limit 20
auto-research parse --limit 20
auto-research analyze --limit 20
auto-research matrix
```

Outputs:

- SQLite state DB: `db/research.sqlite`
- PDFs: `data/pdf/`
- extracted text + JSON profiles: `data/papers/`
- research cards: `data/reports/`
- matrices: `data/matrix/`

## Human handoff

When a paper requires login, captcha, subscription confirmation, or manual Zotero/Browser handling, its job state becomes `needs_login`, `needs_captcha`, `needs_subscription`, or `needs_human`. After you resolve it and place a PDF path with `auto-research attach-pdf`, the pipeline resumes.

## Main commands

```bash
# Discover domain papers from OpenAlex/Crossref/arXiv/OSTI plus Unpaywall DOI enrichment
auto-research discover "MLIP cascade HEA radiation damage" --limit 100

# Try legal/open PDF candidates first
auto-research acquire --limit 50

# If a paper needs login/captcha/manual download, open it and attach the PDF afterward
auto-research open 12
auto-research browser-acquire 12 --wait 120
auto-research attach-pdf 12 /absolute/path/to/downloaded.pdf

# Continue the pipeline
auto-research parse --limit 50
auto-research analyze --limit 50
auto-research matrix

# Zotero bridge
auto-research zotero-status
auto-research zotero-export
```

## State machine

Normal path:

```text
discovered -> source_candidates_found -> downloading -> downloaded -> parsed -> analyzed -> archived_to_zotero
```

Handled exception states:

```text
needs_login
needs_human
needs_captcha
needs_subscription
permission_denied
no_fulltext_found
parse_failed
duplicate
```

## Research-specific extraction

The first implementation uses deterministic extraction so it can run locally without an API key. It extracts:

- materials and HEA/RHEA/CCA indicators
- MLIP family: GAP, MTP, SNAP, DP, NEP, MACE
- cascade / PKA / radiation-damage indicators
- experimental methods: ion/neutron irradiation, TEM, APT, nanoindentation
- simulation methods: DFT, MD, LAMMPS, GPUMD, VASP
- parameters such as temperature, dose, PKA energy, and box size when explicitly reported

A later LLM-backed extractor can replace `src/auto_research/analysis/extractor.py` while preserving the same JSON profile schema and matrix generator.

## Authenticity verification is mandatory

Before a paper is trusted in cards/matrices, run:

```bash
auto-research verify --limit 200
```

The verifier records `authenticity_status`, `authenticity_score`, and detailed evidence in SQLite. It checks:

- DOI syntax and DOI resolution through Crossref/OpenAlex
- title/year agreement against trusted registries
- trusted discovery/acquisition provenance
- PDF integrity and rough title consistency when a PDF is attached

Statuses:

- `verified`: strong registry/provenance evidence
- `likely_real`: trustworthy but one or more checks are incomplete
- `needs_review`: do not rely on it before manual inspection

The review matrices include authenticity columns, and the gap summary only promotes verified/likely-real papers as promising papers.

## Irradiation experimental-evidence database

The evidence database is deliberately separate from Zotero and from `db/research.sqlite`.
Zotero remains the source of papers/PDFs; every publishable value must retain a
page or table/figure locator and pass human review.

```bash
# Create the balanced 30-paper HEA/RHEA + tungsten pilot and import the
# six-paper/108-record benchmark as drafts
auto-research evidence-seed

# Locally prepare relevant-page packets for all 30 papers
auto-research evidence-prepare-pilot

# Validate the publication gates and pilot balance
auto-research evidence-validate

# Start the local Chinese review/search interface
auto-research evidence-serve
```

Open `http://127.0.0.1:8765`. The server refuses non-local bind addresses.

Additional commands:

```bash
# Generate an excerpt-only, schema-constrained packet for interactive AI extraction
auto-research evidence-prompt <paper-db-id>

# Import returned JSON; all records remain drafts until human confirmation
auto-research evidence-import-ai <paper-db-id> result.json

# Export confirmed evidence (or explicitly include drafts)
auto-research evidence-export
auto-research evidence-export --include-drafts
auto-research evidence-export --measured-only
```

Artifacts:

- independent SQLite DB: `db/experimental_evidence.sqlite`
- pilot manifest: `data/matrix/irradiation_evidence_pilot_30.csv`
- prompt packets: `data/evidence/prompt_packets/`
- verified CSV export: `data/matrix/irradiation_evidence_verified.csv`

See `docs/irradiation_evidence_database.md` for the physics-oriented field guide.

### Six-column correction-learning demo

The current single-paper demo is intentionally simpler than the normalized
pilot schema. Each extracted datum has exactly six editable fields: value,
physical meaning, unit, article title, DOI, and contextual explanation. Search
ranks the specific physical meaning first and the contextual explanation one
level below it. The contextual explanation records the material, specimen,
irradiation environment, and other conditions needed to interpret the value.

The canonical source is the final published PDF stored locally at:

`/Users/USER/Zotero/storage/XJZQ42XP/Chen 等 - 2018 - Irradiation effects in high entropy alloys and 316H stainless steel at 300 °C.pdf`

The similarly titled 18-page file is an accepted manuscript of the same paper.
It is useful for cross-checking scientific content, but its page numbers are
not used as evidence locators.

```bash
# Rebuild the immutable original extraction for the current target article
auto-research evidence-seed-target

# Open the local correction/search page
auto-research evidence-serve
```

Open `http://127.0.0.1:8765`. Edits in the left table remain temporary until
`确认当前内容` is pressed; the immutable original remains visible on the right.
Confirmed corrections create a new version during the demo, and manual entries
have no synthetic original version. The original six-column export is written
to `data/extractions/XJZQ42XP_six_column_original.csv`.

### B1 local PDF intake

The `上传文献` view validates a local PDF before accepting it, checks exact file,
DOI, fuzzy title/year/author, and normalized-text fingerprints, then creates a
processing job only for a genuinely new paper. Exact duplicates are not stored
again. Different PDF versions of the same paper are retained as alternate
documents and blocked for version review instead of being extracted twice.

Uploaded PDF binaries are stored under ignored `data/papers/evidence-uploads/`;
the SQLite audit records and processing queue are tracked. Existing Zotero PDFs
are indexed in place and Zotero itself is not modified.

Runtime AI is reserved for DeepSeek. Copy `.env.example` values into the local
shell environment when a key is available; never put a real key in a file that
will be committed. With no key configured, upload, validation, deduplication,
queueing, search, and human review still work normally.

On macOS this project can use a dedicated Keychain credential named
`auto-research-deepseek`. The project checks `DEEPSEEK_API_KEY` first, then that
project-only Keychain service. `/api/ai/status` reports only whether a credential
is available and never returns the credential itself.

```bash
# Redacted local configuration check (does not call the API)
auto-research evidence-deepseek-status

# Minimal synthetic JSON request; does not send any paper content
auto-research evidence-deepseek-smoke-test
```

### B2 evidence-grounded DeepSeek extraction

For a real local PDF, the runtime now performs two complementary extraction
passes over each two-page block, checks every candidate against the stated PDF
page, asks DeepSeek to independently verify the evidence relation, and then
deduplicates the supported candidates. An empty paper can import supported
candidates as unreviewed version 0. A paper that already has six-column rows or
a completed AI run is treated as scanned: command-line extraction refuses to
call DeepSeek again unless `--force-rescan` is passed.

```bash
# Safe preview; writes an audited JSON run without changing existing rows
auto-research evidence-deepseek-extract "10.1016/j.jnucmat.2018.08.031" --max-pages 10

# The paper selector may be a DOI, exact title, unique title fragment, paper id,
# or a legacy local/Zotero key.
auto-research evidence-run-article "Irradiation effects in high entropy alloys and 316H stainless steel at 300 C"

# Read-only acceptance check for a processed article. This does not call
# DeepSeek, switch the current paper, or modify saved rows.
auto-research evidence-self-check "Irradiation effects in high entropy alloys and 316H stainless steel at 300 C" \
  --query 温度 --query 硬度 --query "Wei-Ying Chen" \
  --min-rows 100 --min-highlight-ratio 0.8

# Only valid for a paper whose six-column table is still empty
auto-research evidence-deepseek-extract <paper-selector> --commit

# Explicitly allow a repeat DeepSeek run for an already scanned paper
auto-research evidence-deepseek-extract <paper-selector> --force-rescan
auto-research evidence-run-article <paper-selector> --force-rescan
```

Run metadata is stored in `ai_extraction_runs`; evidence-only result artifacts
are written to `data/evidence/deepseek_runs/`. Model output alone is never a
publication gate: schema, page, numeric/table anchors, background/inference
guards, independent verification, and human confirmation all remain required.
When human confirmations, corrections, or manual additions exist, DeepSeek
extraction includes a short learning-guidance block so later runs learn the
preferred six-column field boundaries and Chinese wording style. Those examples
are only prompt guidance, not evidence; accepted rows must still be grounded in
the current PDF page text.
The learning trail page can export JSONL for either the current paper or the
whole database, so multiple reviewed papers can feed the next extraction pass.

In the local web page, switching the current article only reads saved database
rows. It does not call DeepSeek or re-run extraction; automatic extraction must
be started with the separate current-article extraction button.
Temporary edits in the review table are highlighted but not saved until you
click `确认当前内容`; switching papers, importing results, re-running extraction,
or leaving the page warns before those unsaved edits are discarded.
The switcher displays registered papers by title, year/DOI, and saved-row count.
The visible option text does not require or foreground Zotero/storage codes; the
web app switches by the internal paper id.

The manual-entry page has a separate registered-paper selector. Manual rows are
saved directly to the selected paper and become searchable in the whole database;
this does not change the current review article and does not call DeepSeek.

The search page is database-wide by default. You do not need to choose a paper
before searching; CSV and Excel exports from that page follow the same
whole-database search result set. Search also expands common element names and
symbols, such as `钨` ↔ `W` ↔ `tungsten`, and indexes first/corresponding
authors when available.

The web page also marks whether an article has already been scanned. If a paper
already has six-column rows or a completed DeepSeek run, pressing an extraction
button opens a confirmation dialog; the backend rejects repeat scans unless that
confirmation sends `force_rescan`.

For scanned articles, click `生成当前文章 CSV 备份` to write the current six-column
rows to a timestamped CSV under `data/evidence/saved_scans/`. This is a local
backup/export action only: it does not re-run DeepSeek, does not change row
values, and is not required for normal row saving. The review page also provides
direct current-paper CSV and Excel downloads.

Chinese localization for already imported, still-unreviewed automatic rows:

```bash
auto-research evidence-deepseek-localize <paper-selector>
```

The command changes only `meaning` and `context_explanation`. It keeps values,
units, formulas, source excerpts, and evidence locations unchanged, and refuses
a translation when a numeric condition disappears.

The reproducible five-paper blind validation is documented in
`data/evidence/random5_20260708_audit.md`; its machine-readable manifest is
`data/evidence/random5_20260708_manifest.csv`.
