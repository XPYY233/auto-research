from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from auto_research.evidence.review_queue import ReviewQueueError, ReviewQueueService


REVIEW_QUEUE_PATH = "/api/desktop/review-queue"
REVIEW_ACTION_PATH = "/api/desktop/review-queue/actions"
MAX_REVIEW_REQUEST_BYTES = 64 * 1024


class ReviewQueueHTTPHandler(Protocol):
    path: str

    def _content_length(self, maximum: int, *, require_body: bool = False) -> int: ...

    def _read_exact_body(self, length: int) -> bytes: ...

    def json_response(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None: ...


class ReviewQueueAPI:
    """Thin macOS adapter; session, Origin and CSRF remain host responsibilities."""

    def __init__(self, service: ReviewQueueService) -> None:
        self.service = service

    def handle_get(self, handler: ReviewQueueHTTPHandler) -> bool:
        parsed = urlparse(handler.path)
        if parsed.path != REVIEW_QUEUE_PATH:
            return False
        try:
            query = parse_qs(
                parsed.query,
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=1,
            )
            if set(query) - {"paper_uid"} or any(
                len(values) != 1 or values[0] == "" for values in query.values()
            ):
                raise ReviewQueueError("review_queue_invalid")
            payload = self.service.list(
                paper_uid=query.get("paper_uid", [None])[0]
            )
        except (ValueError, ReviewQueueError) as exc:
            error = exc if isinstance(exc, ReviewQueueError) else ReviewQueueError("review_queue_invalid")
            handler.json_response(error.public_dict(), _error_status(error))
        except Exception:
            error = ReviewQueueError("review_queue_unavailable")
            handler.json_response(error.public_dict(), _error_status(error))
        else:
            handler.json_response(payload)
        return True

    @staticmethod
    def is_post_route(path: str) -> bool:
        return urlparse(path).path == REVIEW_ACTION_PATH

    def handle_post(self, handler: ReviewQueueHTTPHandler) -> bool:
        parsed = urlparse(handler.path)
        if parsed.path != REVIEW_ACTION_PATH:
            return False
        try:
            if parsed.query:
                raise ReviewQueueError("review_queue_invalid")
            length = handler._content_length(MAX_REVIEW_REQUEST_BYTES, require_body=True)
            body = json.loads(handler._read_exact_body(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise ReviewQueueError("review_queue_invalid")
            action = body.get("action")
            if action not in {"approve", "reject", "correct"}:
                raise ReviewQueueError("review_queue_invalid")
            required = {"review_token", "action", "fields"} if action == "correct" else {
                "review_token", "action"
            }
            if set(body) != required:
                raise ReviewQueueError("review_queue_invalid")
            payload = self.service.review(
                review_token=body["review_token"],
                action=action,
                fields=body.get("fields"),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError, ReviewQueueError) as exc:
            error = exc if isinstance(exc, ReviewQueueError) else ReviewQueueError("review_queue_invalid")
            handler.json_response(error.public_dict(), _error_status(error))
        except Exception:
            error = ReviewQueueError("review_queue_unavailable")
            handler.json_response(error.public_dict(), _error_status(error))
        else:
            handler.json_response(payload)
        return True


def _error_status(error: ReviewQueueError) -> HTTPStatus:
    return {
        "review_queue_invalid": HTTPStatus.BAD_REQUEST,
        "review_queue_not_found": HTTPStatus.NOT_FOUND,
        "review_token_invalid": HTTPStatus.NOT_FOUND,
        "review_token_expired": HTTPStatus.GONE,
        "review_candidate_changed": HTTPStatus.CONFLICT,
        "review_conflict": HTTPStatus.CONFLICT,
        "review_queue_unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
    }[error.code]
