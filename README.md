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
