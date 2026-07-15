# Auto Research Zotero Handoff: Extend 300 PDF Corpus to 1000

## Purpose

This document hands off the current Auto Research + Zotero workflow to a future conversation/agent. The next target is to extend the verified PDF-only literature corpus from **300 papers** to **1000 papers**, focused on:

- Fusion materials
- Radiation damage
- Collision/displacement cascade
- MLIP / MLIAP
- HEA / RHEA / CCA
- Tungsten / refractory alloys
- Nuclear materials when relevant to the above themes

## Current project path

`/Users/USER/Zotero/auto-research`

The new conversation should start from this directory.

## Installed skill

A reusable Codex skill has been installed globally:

`/Users/USER/.codex/skills/auto-research-zotero`

Skill name:

`auto-research-zotero`

In the next conversation, ask Codex to use this skill explicitly, e.g.:

> Use the `auto-research-zotero` skill. Continue from `/Users/USER/Zotero/auto-research` and extend the verified PDF-only Zotero corpus from 300 papers to 1000 papers.

## Current Zotero state

### Clean parent collection

Name:

`Auto Research PDF-only 300 CLEAN - Object+Method`

Collection key:

`IJ4ZT63W`

Verified state:

- Top-level items: 300
- Valid local PDFs: 300
- Metadata-only items: 0
- Child collections: 30
- Classification style: `ResearchObject + ResearchMethod`

### Child collection naming convention

Every child collection under the clean parent is named:

`<ResearchObject> + <ResearchMethod>`

Current research objects:

- `HEA-RHEA-CCA`
- `W-Refractory-Alloys`
- `Fusion-Materials`
- `Nuclear-Materials`
- `General-Materials`

Current research methods:

- `MLIP`
- `MD-Cascade`
- `DFT-AbInitio`
- `Irradiation-Experiment`
- `Review-Report`
- `General-Modeling`

Current child collections and counts are recorded in:

`data/matrix/pdf_only_300_zotero_collection_categories.md`

### Deduplication / unfiled cleanup status

After cleanup:

- Active unfiled top-level regular items: 0
- Active duplicate DOI groups: 0
- Active exact-title duplicate groups: 0

Reports:

- `data/matrix/zotero_unfiled_classification_report.csv`
- `data/matrix/zotero_duplicate_merge_report.csv`
- `data/matrix/zotero_final_exact_title_merge_report.csv`

## Important backups

Zotero SQLite backups were created before direct database edits:

- `db/zotero.sqlite.backup-before-combo-collections-20260521-175304`
- `db/zotero.sqlite.backup-before-unfiled-classify-duplicates-20260521-214649`
- `db/zotero.sqlite.backup-before-merge-duplicates-20260521-214815`
- `db/zotero.sqlite.backup-before-final-exact-title-merge-20260521-215031`

Before extending to 1000 papers, create a new backup of:

`~/Zotero/zotero.sqlite`

## PDF acquisition rules

Only use sources that can provide real local PDFs. Do **not** count metadata-only records.

### Proven direct PDF sources

- OSTI full text / PURL: `https://www.osti.gov/servlets/purl/<id>`
- OpenAlex `best_oa_location.pdf_url`
- arXiv PDF URLs: `https://arxiv.org/pdf/<id>`
- Publisher OA direct PDFs, e.g. Nature/Springer direct PDF URLs
- Unpaywall direct PDF URLs when `url_for_pdf` is present
- User-authorized manual/browser PDF downloads, only after a real local PDF is attached

### Metadata-only or risky paths

Do not count these as PDF success unless a real local PDF is attached:

- Crossref metadata
- OpenAlex metadata without direct PDF
- DOI landing pages
- Publisher pages requiring login/captcha/subscription
- Any `needs_human`, `needs_login`, `needs_captcha`, `needs_subscription`, `permission_denied`, or `no_fulltext_found` state

## Existing useful scripts

Run from the project root.

### Harvest PDFs

`PYTHONPATH=src python3 -u scripts/fast_harvest_pdf_corpus.py --target 1000 --pages 4 --workers 8`

This script harvested the corpus to 300 valid local PDFs. For 1000, inspect and extend its query list first; avoid over-broad OSTI reports if topical precision matters.

### Build verified RIS + manifest

`PYTHONPATH=src python3 scripts/build_pdf_only_zotero_import.py`

Currently hardcoded for 300 in places. For 1000, modify `TARGET=1000`, or create a new script such as `build_pdf_only_zotero_import_1000.py`.

### Import clean Zotero collection

Current script:

`python3 scripts/import_pdf300_clean.py`

For 1000, create a new clean parent collection in Zotero first, select it, then use a 25-item batch import pattern. Avoid sending a giant 1000-record RIS in one request; the connector may time out and continue in the background, creating duplicates.

### Verify Zotero collection PDFs

`python3 scripts/verify_pdfonly_clean_collection.py`

For 1000, adjust collection key and output filenames.

### Create object+method child collections

`python3 scripts/create_combo_collections_sqlite.py`

For 1000, adjust parent collection name/key and manifest/verification file. Zotero must be closed before direct SQLite modification.

## Recommended 1000-paper plan

1. Start by backing up Zotero DB.
2. Confirm current clean 300 collection remains valid.
3. Extend harvesting until there are at least 1000 unique valid local PDFs in the project database.
4. Improve query strategy to avoid irrelevant general reports:
   - Prioritize HEA/RHEA + irradiation/cascade/MLIP queries.
   - Prioritize W/refractory/fusion/cascade/MLIP queries.
   - Use OSTI, OpenAlex OA PDFs, arXiv, Unpaywall direct PDF.
5. Verify all candidate PDFs locally with PyMuPDF.
6. Build a `pdf_only_1000_manifest.csv` and `pdf_only_1000_import.ris`.
7. Create a new parent Zotero collection, e.g.:
   - `Auto Research PDF-only 1000 CLEAN - Object+Method`
8. Batch import in chunks of 25 or 40.
9. Verify Zotero top-level item count and valid PDF count.
10. Create child collections named `ResearchObject + ResearchMethod` under the new parent collection.
11. Deduplicate conservatively.
12. Produce final reports.

## Suggested next conversation prompt

Copy this into the next conversation:

```text
Use the `auto-research-zotero` skill.

Continue from this project:
/Users/USER/Zotero/auto-research

Current state: Zotero has a clean verified parent collection `Auto Research PDF-only 300 CLEAN - Object+Method` with collection key `IJ4ZT63W`, containing 300 top-level items and 300 valid local PDFs. It has 30 child collections named by `ResearchObject + ResearchMethod`.

Task: extend this workflow to 1000 papers. Only count papers that have real, locally openable PDFs. Do not include metadata-only records. Keep classification by research object + research method in Zotero collections, not just tags. Create a new clean parent collection for the 1000-paper corpus. Back up Zotero before direct DB edits. Verify final Zotero item count, PDF count, child collection count, and deduplication status.

Before executing, read `AUTO_RESEARCH_HANDOFF_1000.md` and `AGENT.md`.
```

## Final caution

Direct Zotero SQLite edits were necessary because Zotero Desktop local API returned `501 Method not implemented` for direct item/collection mutation. Always quit Zotero and back up the database before any SQLite write.
