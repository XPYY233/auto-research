from __future__ import annotations

from dataclasses import dataclass


# These limits are shared by the prompt, result validator, stage planner and
# prepared-action budget.  Keeping them in one dependency-free module prevents
# the user-visible consent ceiling from drifting away from the executable plan.
BRANCH_COUNT = 2
MAX_EXTRACTED_RECORDS_PER_RESPONSE = 64
VERIFICATION_BATCH_SIZE = 20
THIRD_REVIEW_BATCH_SIZE = 10
MAX_VERIFICATION_BATCHES_PER_BRANCH_BLOCK = 2
MAX_THIRD_REVIEW_CALLS_PER_TASK = 4

INITIAL_CALLS_PER_BLOCK = BRANCH_COUNT
COVERAGE_GAP_CALLS_PER_BLOCK = BRANCH_COUNT
INITIAL_MAX_TOKENS_PER_CALL = 16_000
COVERAGE_GAP_MAX_TOKENS_PER_CALL = 12_000
VERIFICATION_MAX_TOKENS_PER_CALL = 4_000
THIRD_REVIEW_MAX_TOKENS_PER_CALL = 5_000


@dataclass(frozen=True)
class LiteratureExtractionTaskBudget:
    max_calls: int
    max_tokens: int


def task_budget_for_page_blocks(page_block_count: int) -> LiteratureExtractionTaskBudget:
    """Return a hard task ceiling derived from the frozen PDF block count.

    Each branch can produce one initial and one coverage-gap response per page
    block.  Automatic verification is intentionally limited to two batches per
    branch and block; overflow remains manual review.  The whole paper gets at
    most four third-review calls, with the remaining records also staying
    manual.  This is both an execution ceiling and the number shown at consent.
    """

    if isinstance(page_block_count, bool) or not isinstance(page_block_count, int):
        raise ValueError("page block count must be an integer")
    if page_block_count < 1:
        raise ValueError("page block count must be positive")

    verification_calls_per_block = (
        BRANCH_COUNT * MAX_VERIFICATION_BATCHES_PER_BRANCH_BLOCK
    )
    calls_per_block = (
        INITIAL_CALLS_PER_BLOCK
        + COVERAGE_GAP_CALLS_PER_BLOCK
        + verification_calls_per_block
    )
    tokens_per_block = (
        INITIAL_CALLS_PER_BLOCK * INITIAL_MAX_TOKENS_PER_CALL
        + COVERAGE_GAP_CALLS_PER_BLOCK * COVERAGE_GAP_MAX_TOKENS_PER_CALL
        + verification_calls_per_block * VERIFICATION_MAX_TOKENS_PER_CALL
    )
    return LiteratureExtractionTaskBudget(
        max_calls=(
            calls_per_block * page_block_count
            + MAX_THIRD_REVIEW_CALLS_PER_TASK
        ),
        max_tokens=(
            tokens_per_block * page_block_count
            + MAX_THIRD_REVIEW_CALLS_PER_TASK * THIRD_REVIEW_MAX_TOKENS_PER_CALL
        ),
    )


__all__ = [
    "BRANCH_COUNT",
    "COVERAGE_GAP_MAX_TOKENS_PER_CALL",
    "INITIAL_MAX_TOKENS_PER_CALL",
    "MAX_EXTRACTED_RECORDS_PER_RESPONSE",
    "MAX_THIRD_REVIEW_CALLS_PER_TASK",
    "MAX_VERIFICATION_BATCHES_PER_BRANCH_BLOCK",
    "THIRD_REVIEW_BATCH_SIZE",
    "THIRD_REVIEW_MAX_TOKENS_PER_CALL",
    "VERIFICATION_BATCH_SIZE",
    "VERIFICATION_MAX_TOKENS_PER_CALL",
    "LiteratureExtractionTaskBudget",
    "task_budget_for_page_blocks",
]
