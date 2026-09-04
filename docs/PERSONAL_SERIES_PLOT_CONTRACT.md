# Personal experiment full-series plotting

Source implementation checkpoint: 2026-09-04. Renderer connected; not installed.
This is visualization of user-confirmed numeric table rows, not digitization of
paper images, AI extraction, fitting, interpolation, or a scientific accuracy claim.

## Read authority

`PersonalTableDetailService.get_series` shares the ordinary-file, SHA-256,
confirmed/indexable run, opaque source/entity identity, sheet-name/row-count and
column checks used by `get_page`. Both consume one captured tabular snapshot.
It cannot read drafts, another source, a changed original, or arbitrary paths.
Nothing is written; no model is called. Private values never enter Librarian.

Mac thin route: `GET /api/desktop/personal-experiments/series` with exact query
`source_id`, `entity_uid`, optional zero-based `series_index` (0–199, default 0).
No `page`, `page_size`, internal run/file keys or duplicate query keys. It uses
the existing desktop session/security envelope and JSON no-store response.
Windows remains frozen; no Windows route implementation is claimed.

## `personal-series-plot-v1`

The response includes:

- `schema_version`, `source_id`, `entity_uid`, `series_index`;
- `name`, `x_column`, `y_column`, nullable `uncertainty_column`;
- `x_unit`, `y_unit`, nullable `uncertainty_unit`;
- `total_rows`, `valid_points`, `missing_rows`, `invalid_rows`,
  `uncertainty_missing_rows`, `uncertainty_invalid_rows`;
- `points`: every original row in original order, including gaps. Each is
  `{row, x, y, x_text, y_text, uncertainty, uncertainty_text, status}`.
  `row` is one-based; numeric fields are finite numbers or null; text retains
  original cell representation; `status` is `valid`, `missing`, or `invalid`.

At most 5000 source rows are plotted in one complete response. Larger series
fail with `personal_series_too_large`/413; the original table remains pageable.
There is no silent first-page, first-N or downsampled substitute. Numeric lexical
validation rejects ambiguous thousands/decimal conventions, nonfinite/overflow
and underflow-to-zero values. Missing/invalid XY values stay gaps, never zeros.
Invalid/negative uncertainty yields no error bar and increments an explicit
counter. Different uncertainty/y units fail rather than being silently converted.
Original scientific cell text is retained; floating plotting coordinates are
not the authority for data export or training.

## Renderer implementation and remaining acceptance

`fusion_personal_series.js` is a passive strict projection/geometry renderer;
Fusion alone owns GET requests, tab identity, events and selection. The same
controls are used in primary and secondary table views. Session-monotonic request
tokens prevent a closed/reopened deterministic tab ID from accepting an old
response. Mac static authorization, frozen resources and release hashes include
the script. Windows is unchanged.

The real 123-row CSV workflow validates the resulting public DTO in this renderer:
122 drawable points, one missing row, two original-order segments of 49 and 73
points, and exact original values/units. Runtime tests cover source/series changes,
late responses, close/reopen, pagination, missing/invalid values and terminal errors.
These are source checks, not installed-WebView visual acceptance.

Use one shared primary/secondary renderer, fixed to the selected table+series
identity. Show the full-series row/valid/gap counts and units. Show original row
and exact values on focus/selection; provide an accessible source-value table.
Render gaps without connecting across missing rows; do not reorder rows, fit a
line, infer curve points or turn absent uncertainties into zeros. Only draw error
bars for valid nonnegative same-unit uncertainties. A series switch or late
response must not overwrite another tab. Loading/error/empty/oversize states must
terminate; all-invalid series must state no drawable values, not show a fake plot.
The existing 50-row table remains independently pageable. Preserve privacy and
the original table return path. Real App confirmation/import/search/plot/return
and restart remain open acceptance gates.
