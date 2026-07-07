# Auto Research Agent Notes

This project is a local literature automation workflow for fusion materials, radiation damage, cascade simulations, MLIP/MLIAP, and HEA/RHEA research. The agent must prioritize real, auditable acquisition paths and must never create fake PDFs or treat metadata-only records as full-text successes.

## Current acquisition capability summary

### Paths that can directly yield article metadata + a real local PDF

These paths have been validated in the current workflow. A paper should count as `success_local_pdf` only if Zotero or the local project has an actual PDF file that can be opened locally.

1. **OSTI public full text / PURL**
   - Source candidate type: `osti_fulltext`
   - Typical URL shape: `https://www.osti.gov/servlets/purl/<id>`
   - Observed behavior: often downloads a real PDF directly, especially for DOE-funded materials, irradiation, high-entropy alloy, and fusion-related reports/articles.
   - Examples successfully obtained:
     - `10.1016/j.cossms.2022.101001`
     - `10.1557/s43578-020-00071-8`
     - `10.1063/5.0302848`
     - `10.2172/1214790`
     - `10.1103/physrevb.108.054312` via OA/preprint alternative in some cases

2. **OpenAlex best OA PDF**
   - Source candidate type: `openalex_best_oa`
   - Access mode: usually `open`
   - Observed behavior: reliable when the candidate URL is a direct PDF, e.g. Nature PDF, arXiv PDF, or other publisher OA PDF.
   - Examples successfully obtained:
     - Nature OA PDFs such as `https://www.nature.com/articles/...pdf`
     - arXiv PDFs such as `https://arxiv.org/pdf/<id>`
     - `10.1038/s41467-023-38000-y`
     - `10.1038/s41598-018-34486-5`
     - `10.48550/arxiv.2201.08906`

3. **arXiv PDF URLs surfaced by OpenAlex or arXiv discovery**
   - Source candidate types: `openalex_best_oa`, `arxiv_pdf`
   - Typical URL shape: `https://arxiv.org/pdf/<arxiv_id>`
   - Observed behavior: direct PDF download succeeds when arXiv is reachable and rate limits are respected.
   - Caveat: arXiv API/discovery may return 429 or timeouts; do not hammer it. Use backoff and cached results.
   - Examples successfully obtained:
     - `10.48550/arxiv.2201.08906`
     - `10.48550/arxiv.2409.08030`
     - `10.48550/arxiv.2305.08140`

4. **Publisher open-access direct PDFs**
   - Source candidate types: `openalex_best_oa`, sometimes `openalex_location`
   - Typical URL shapes:
     - `https://www.nature.com/articles/<id>.pdf`
     - `https://link.springer.com/content/pdf/<doi>.pdf`
     - publisher-hosted OA PDF URLs surfaced by OpenAlex/Unpaywall
   - Observed behavior: works when the PDF endpoint is genuinely open and returns `application/pdf` or bytes beginning with `%PDF`.
   - Caveat: some pages claim OA but return HTML, login, captcha, or access-denied pages. These must not be treated as PDFs.

5. **Unpaywall OA locations**
   - Source candidate types: `unpaywall_best_oa`, `unpaywall_oa`
   - Observed behavior: useful as a DOI-based enrichment path; if `url_for_pdf` is present and direct, it may produce a PDF.
   - Caveat: not every Unpaywall URL is a direct PDF; landing pages may require browser/manual handling.

6. **Already-authorized manual/browser downloads**
   - Source path: `browser-acquire`, `open`, then `attach-pdf`
   - Observed behavior: can become a full-text success only after a real PDF has been downloaded through a lawful user/institutional session and attached.
   - Success criterion: the attached file exists locally, opens as a PDF, and roughly matches the Zotero item/title.

### Paths that usually provide reliable metadata but not necessarily a PDF

These paths are useful for discovery, DOI verification, title/year/authors, and Zotero metadata import. They must not be counted as full-text success unless a real local PDF is attached.

1. **Crossref works API**
   - Source: `crossref`
   - Gives: DOI, title, authors, year, journal/container, references/links when available.
   - PDF behavior: Crossref `link` entries may point to licensed PDFs, but they often return `permission_denied` or require publisher access.
   - Treat as: metadata + DOI authenticity verification, not guaranteed full text.

2. **OpenAlex metadata and DOI landing-page locations**
   - Source: `openalex`
   - Gives: title, DOI, abstract inverted index, authorship, OA status, locations, best OA candidate.
   - PDF behavior: only direct PDF-like OA URLs are reliable PDF candidates. DOI landing pages such as `https://doi.org/...` commonly become `needs_human`, `needs_login`, or `permission_denied`.
   - Treat as: metadata + source-candidate discovery; do not assume PDF.

3. **DOI landing pages**
   - Source candidate type: usually `openalex_location` or publisher link.
   - Typical URL shape: `https://doi.org/<doi>`
   - Observed behavior: resolves to publisher pages, not direct PDFs. Often requires JavaScript, institutional login, subscription access, or manual selection of PDF.
   - Treat as: metadata/landing page only unless browser/manual acquisition produces a real PDF.

4. **Publisher pages requiring institution/subscription**
   - Examples observed:
     - Elsevier / ScienceDirect article pages
     - AIP pages
     - Springer/TMS pages
     - ASTM licensed PDF links
     - IOP pages with captcha/human verification
   - States observed: `needs_human`, `needs_login`, `needs_captcha`, `needs_subscription`, `permission_denied`.
   - Treat as: metadata-only until the user completes lawful institution access and a real PDF is attached.

5. **Records with `no_fulltext_found`**
   - Meaning: metadata was discovered and may be authentic, but no direct PDF candidate was available or all candidates failed.
   - Treat as: metadata-only. Do not import as a full-text success.

## Current Zotero test collection status

Test collection:

- Name: `Auto Research Test - Verified PDFs - 2026-05-21`
- Zotero collection key: `269H8A53`

Current imported test set:

- 30 top-level literature items
- 20 items with real local PDF attachments
- 10 metadata-only institutional/blocked-page candidates

Verification report paths:

- Markdown: `data/matrix/zotero_test_collection_verification.md`
- CSV: `data/matrix/zotero_test_collection_verification.csv`

Current status counts:

- `success_local_pdf`: 20
- `metadata_verified_no_pdf`: 10
- `needs_review`: 0

## Mandatory success criteria

A paper is a full-text acquisition success only when all are true:

1. A real local PDF file exists.
2. The file opens with PyMuPDF or Zotero can expose a local `file://` URL for the attachment.
3. The file is not a generated placeholder.
4. The title/DOI metadata is verified through at least one trusted registry or source.
5. The PDF first page or extracted text roughly matches the item title, when text extraction is possible.

If any of these fail, classify the item as one of:

- `metadata_verified_no_pdf`
- `needs_human`
- `needs_login`
- `needs_captcha`
- `needs_subscription`
- `permission_denied`
- `no_fulltext_found`
- `needs_review`

## Agent behavior rules

1. **Never fabricate PDFs.**
   - Do not create fake PDFs for testing.
   - Do not attach generated placeholder PDFs to Zotero items.
   - Do not mark metadata-only records as PDF successes.

2. **Prefer direct open/official sources first.**
   - Try OSTI PURLs, OpenAlex best OA PDFs, arXiv PDFs, PMC/Europe PMC PDFs, and Unpaywall `url_for_pdf` before browser/manual paths.

3. **Use publisher/DOI landing pages conservatively.**
   - Landing pages are useful for metadata and human handoff.
   - They are not full-text successes unless a real PDF is downloaded and attached.

4. **Do not bypass access controls.**
   - Do not solve CAPTCHAs automatically.
   - Do not bypass paywalls.
   - Do not use proxy pools or disguise institutional identity.
   - If institutional access is needed, mark the item for user handoff.

5. **Record provenance and failure reason.**
   - For each candidate, keep `source_type`, `url`, `access_mode`, `attempted`, and `last_error`.
   - This is essential for later improving coverage.

6. **Verify before analysis/matrix use.**
   - Run authenticity verification before trusting the item in reports or review matrices.
   - The matrix should expose authenticity/PDF status clearly.

## Recommended workflow

```bash
PYTHONPATH=src python3 -m auto_research.cli discover "MLIP cascade HEA radiation damage" --limit 100
PYTHONPATH=src python3 -m auto_research.cli acquire --limit 80
PYTHONPATH=src python3 -m auto_research.cli verify --limit 120 --include-all
PYTHONPATH=src python3 -m auto_research.cli parse --limit 80
PYTHONPATH=src python3 -m auto_research.cli analyze --limit 80
PYTHONPATH=src python3 -m auto_research.cli matrix
```

For metadata-only institutional candidates:

```bash
PYTHONPATH=src python3 -m auto_research.cli open <paper_id>
# user completes lawful login/download manually
PYTHONPATH=src python3 -m auto_research.cli attach-pdf <paper_id> /absolute/path/to/downloaded.pdf
PYTHONPATH=src python3 -m auto_research.cli verify --limit 120 --include-all
```
