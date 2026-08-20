from __future__ import annotations

from http import HTTPStatus
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from auto_research.personal.table_detail import PersonalTableDetailService, PersonalTableError


PERSONAL_TABLE_PATH = "/api/desktop/personal-experiments/table"


class PersonalTableHTTPHandler(Protocol):
    path: str

    def json_response(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None: ...


class PersonalTableAPI:
    def __init__(self, service: PersonalTableDetailService) -> None:
        self.service = service

    def handle_get(self, handler: PersonalTableHTTPHandler) -> bool:
        parsed = urlparse(handler.path)
        if parsed.path != PERSONAL_TABLE_PATH:
            return False
        try:
            query = parse_qs(
                parsed.query,
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=4,
            )
            if not {"source_id", "entity_uid"} <= set(query) or not set(query) <= {
                "source_id",
                "entity_uid",
                "page",
                "page_size",
            }:
                raise PersonalTableError("personal_table_invalid")
            if any(len(values) != 1 or values[0] == "" for values in query.values()):
                raise PersonalTableError("personal_table_invalid")
            page = _positive_integer(query.get("page", ["1"])[0], maximum=None)
            page_size = _positive_integer(query.get("page_size", ["50"])[0], maximum=100)
            payload = self.service.get_page(
                source_id=query["source_id"][0],
                entity_uid=query["entity_uid"][0],
                page=page,
                page_size=page_size,
            ).public_dict()
        except (ValueError, PersonalTableError) as exc:
            error = (
                exc
                if isinstance(exc, PersonalTableError)
                else PersonalTableError("personal_table_invalid")
            )
            handler.json_response(error.public_dict(), _error_status(error))
        else:
            handler.json_response(payload)
        return True


def _positive_integer(value: str, *, maximum: int | None) -> int:
    if (
        len(value) > 10
        or not value.isascii()
        or not value.isdigit()
        or value.startswith("0")
    ):
        raise ValueError("invalid positive integer")
    parsed = int(value)
    if parsed < 1 or (maximum is not None and parsed > maximum):
        raise ValueError("invalid positive integer")
    return parsed


def _error_status(error: PersonalTableError) -> HTTPStatus:
    return {
        "personal_table_not_found": HTTPStatus.NOT_FOUND,
        "personal_table_invalid": HTTPStatus.BAD_REQUEST,
        "personal_table_changed": HTTPStatus.CONFLICT,
        "personal_table_unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
    }[error.code]
