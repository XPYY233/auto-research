# Guardrails

## Scientific provenance

- Require a real local PDF for every scientific record.
- Retain paper identity, page, locator, and a short source excerpt.
- Preserve original text, unit, and conflicting reports.
- Keep measured, derived, calculated, and qualitative evidence distinct.
- Keep exact table/text values distinct from trends and figure-only evidence.
- Prefer an empty unsupported field to a fabricated complete record.
- Never infer curve points or exact values from pixels.
- Never treat publication as proof that a physical conclusion is correct.

## Six-column contract

Retain `value_text`, `meaning`, `unit`, `article_title`, `doi`, and `context_explanation` as the user-facing record. `meaning` is the primary search field; `context_explanation` contains material, specimen, conditions, method, and article context. Do not add a seventh user column to compensate for poor indexing.

`value_text` must be numeric or an explicit compact table marker. Move prose phenomena to findings/context rather than numeric search.

## Model boundary

- Runtime AI is DeepSeek; Codex is a development tool.
- DeepSeek receives bounded extracted text, not a claim of visual pixel inspection.
- Treat API/network/model JSON as unreliable and preserve local non-AI functions on failure.
- Do not bypass the adversarial gate for automatic publication.
- Do not expose keys, prompts with sensitive content, full PDFs, or response bodies in logs.
- Read credentials only from environment or project-specific macOS Keychain service `auto-research-deepseek`.

## Visual boundary

- Production authority is the existing local PyMuPDF screenshots and metadata.
- Do not reintroduce MinerU, cloud visual routes, caches, credentials, or UI without a new explicit user decision.
- Do not regenerate all stable screenshots for a semantic-only change.
- DeepSeek may enrich Chinese titles, explanations, and tags only from captions and nearby extracted text.

## Search and Agent boundary

- Search indexes are rebuildable projections, never evidence authority.
- Keep public types fixed to item/finding/table/figure.
- Keep Librarian read-only and full-corpus; paper scoping belongs to exact search.
- Keep direct and related evidence separate.
- Every `[R#]` must resolve to a returned record.
- Browser-local history is convenience state, not server evidence.
- Selected-evidence chat cannot write or review records.

## UI and sharing boundary

- Editable and public read-only modes share one frontend and database.
- Public mode exposes search and read-only evidence only; block upload, review, extraction, article lists that leak local context, and all writes server-side.
- Do not share port 8765. ngrok must front the read-only service on 8766.
- Do not maintain a separate mentor/teacher UI.

## Workspace and Git boundary

- Work only in `/Users/USER/Zotero/auto-research`, not the old iCloud copy.
- Inspect and preserve a dirty worktree; never reset user changes destructively.
- Do not commit `.env`, credentials, production PDFs, temporary uploads, Zotero DB, or unrelated personal files.
- Public GitHub requires a separate scope decision. Real paper screenshots, excerpts, production DB, and copyrighted PDFs are not automatically public-safe.
- Current `origin` is a local bundle, not GitHub.

## Claim boundary

Report separately:

1. product/program stability;
2. corpus completion;
3. independent scientific accuracy.

Do not claim the original 30-paper target is complete while the fixed corpus remains 17 data-ready. Do not call two-model agreement a human gold standard.
