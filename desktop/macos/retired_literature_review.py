"""No desktop endpoint may re-enable the retired human literature workflow."""
from __future__ import annotations

import re


def is_retired_literature_review(path: str, method: str) -> bool:
    if path == "/api/desktop/review-queue" or path.startswith("/api/desktop/review-queue/"):
        return True
    if method == "GET":
        return path in {
            "/api/current-paper/review-progress", "/api/current-paper/review-batch",
            "/api/current-paper/review-batch.md",
        }
    if method == "POST":
        return path in {
            "/api/desktop/table-structures/reviews",
            "/api/desktop/table-structures/candidates",
        } or bool(re.fullmatch(
            r"/api/(?:six-data/[1-9][0-9]*/(?:confirm|decision)|"
            r"(?:visual-assets|quality-candidates|measurements)/[1-9][0-9]*/review)", path,
        ))
    return False
