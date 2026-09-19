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

- Production Librarian and selected-evidence AI run through the exact pinned DeepSeek Harness composition; Codex is a development tool.
- Harness receives bounded safe DTOs, never a claim of visual pixel inspection, and has no shell, filesystem, PTY, editor, subagent or arbitrary network tools.
- Prepared actions, explicit consent, provider/model allowlists, call/token budgets, credential generation and citation completeness remain Auto Research authority. Harness failure is explicit; never fall back to legacy Librarian/context-chat loops.
- Treat API/network/model JSON as unreliable and preserve local non-AI functions on failure.
- Do not bypass the adversarial gate for automatic publication.
- Do not expose keys, prompts with sensitive content, full PDFs, or response bodies in logs.
- Runtime AI is BYOK. Store the user's key only in the platform secure credential store (macOS Keychain / Windows Credential Manager); environment variables are maintainer-only fallback. Never package or reuse the developer key for end users.

## Visual boundary

- Production authority is the existing local PyMuPDF screenshots and metadata.
- Do not reintroduce MinerU, cloud visual routes, caches, credentials, or UI without a new explicit user decision.
- Do not regenerate all stable screenshots for a semantic-only change.
- DeepSeek may enrich Chinese titles, explanations, and tags only from captions and nearby extracted text.

## Search and Agent boundary

- Search indexes are rebuildable projections, never evidence authority.
- Keep public types fixed to item/finding/table/figure.
- Keep Harness Librarian read-only over official and published workspace literature. Bounded history and user-confirmed research memory require accurate versioned disclosure. Private experiments never enter either literature AI scope.
- Keep direct and related evidence separate.
- Every `[R#]` must resolve to a returned record.
- Encrypted desktop Harness history is convenience state, not scientific evidence; retain at most 20 sessions or 30 days and support immediate clear.
- Selected-evidence chat cannot write or review records.

## UI and sharing boundary

- The only product shape is a personal desktop workbench. The Mac acceptance ledger owns current version/status; Windows migration remains frozen until explicit user approval.
- The embedded frontend and loopback service are App internals. Historical editable/read-only modes share one frontend and remain permission tests only.
- Do not expose or document ports 8765/8766, ngrok, a mentor page or a browser workbench as user entry points.
- Distribute the App separately from versioned evidence packages. Packages must be portable, sanitized, verifiable and rollback-safe; official packages and user-private data never overwrite one another.
- An official-package-v2 release is complete only with every declared source PDF and visual asset validated. Do not substitute a related article for a missing report, silently omit a paper or weaken the 2GB/resource/rights gate.
- Dataset bundles default to no private experiments and no PDF/image payload. Split by paper, expose missing/unreviewed/rights risk, and never leak paths, keys, sessions or internal database identifiers.

## Workspace and Git boundary

- Develop only in the sanitized GitHub checkout identified by the current handoff. Preserve the original Zotero and former iCloud directories as recovery sources.
- Inspect and preserve a dirty worktree; never reset user changes destructively.
- Do not commit `.env`, credentials, production PDFs, temporary uploads, Zotero DB, or unrelated personal files.
- Public GitHub requires a separate scope decision. Real paper screenshots, excerpts, production DB, and copyrighted PDFs are not automatically public-safe.
- Verify the private GitHub remote before pushing. Follow Issue → branch → PR → checks → merge; local backups are not the development remote.

## Claim boundary

Report separately:

1. product/program stability;
2. corpus completion;
3. independent scientific accuracy.

Use the current acceptance matrix and human-gold protocol for completeness claims. Historical corpus counts are not current evidence. Never call two-model agreement a human gold standard.
