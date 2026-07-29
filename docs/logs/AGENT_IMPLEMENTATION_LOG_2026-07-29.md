# Agent implementation log — 2026-07-29

## Failure reproduced

- The saved browser session contained 18 backend candidates, but the retained `table` tab exposed only 2 cards.
- The final DeepSeek JSON summary had failed, so the backend returned a count-only fallback instead of a scientific answer.
- The previous response contract filtered successful answers down to cited records, hiding retrieval breadth.

## Runtime change

`LibrarianAgentRuntime.run()` now orchestrates three bounded stages:

1. `request_json()` plans short recall queries; `_fallback_recall_queries()` always provides a local plan.
2. Registered `search_evidence` executes every query separately for `item`, `finding`, `table`, and `figure`, deduplicating under global and per-type caps.
3. A balanced 48-candidate packet is sent to the JSON synthesizer. It returns `answer` plus `selected_refs` and must separate direct joint matches from partial evidence.

The clean text fallback and deterministic cited digest keep the response useful if JSON synthesis fails. Model tool-call messages are no longer part of the primary Librarian path, so DSML cannot control retrieval; the existing frontend protocol guard remains for old history and defensive rendering.

## Response and UI contract

- The endpoint returns `candidate_count`, `cited_count`, `recall_queries`, `search_operations`, `plan_mode`, and `summary_mode`.
- Every bounded candidate remains in `results`; `agent_cited` distinguishes answer evidence from expansion candidates.
- The frontend displays total recall and citation counts, sorts cited cards first, and selects the result type with the most citations for each new response.
- Old browser histories infer citation flags from their latest assistant answer without calling DeepSeek.
- Identical question/history pairs reuse the complete response for one hour when the database source fingerprint is unchanged. The cache copies the answer, citations and all candidates together, reports `cache_hit`, and is bounded to 32 entries.
- Planner and synthesizer temperatures are fixed at zero; the response cache removes remaining repeat-query citation drift and saves duplicate model calls.

## Verification

- Real DeepSeek model: `deepseek-v4-pro`.
- Regression question: `高熵合金在中子辐照后，硬度和缺陷结构有哪些变化？`
- Repeated live runs consistently recalled 58 candidates (24 items, 3 tables, 11 figures, 20 findings). Before caching, stochastic citation selection varied from 4 to 10; the release-server reference run cited 6 records, and its second identical request reused the complete response with an identical content hash.
- The answer correctly reported that direct HEA neutron-irradiation evidence was absent and separated HEA ion evidence from pure-W neutron evidence.
- 196 automated tests passed, including exact-query cache invalidation boundaries; JavaScript syntax, Python compilation and whitespace checks passed.
