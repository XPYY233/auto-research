from __future__ import annotations

from http import HTTPStatus
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from auto_research.personal.table_detail import PersonalTableDetailService, PersonalTableError


PERSONAL_TABLE_PATH = "/api/desktop/personal-experiments/table"
PERSONAL_SERIES_PATH = "/api/desktop/personal-experiments/series"


class PersonalTableHTTPHandler(Protocol):
    path: str

    def json_response(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None: ...


class PersonalTableAPI:
    def __init__(self, service: PersonalTableDetailService) -> None:
        self.service = service

    def handle_get(self, handler: PersonalTableHTTPHandler) -> bool:
        parsed = urlparse(handler.path)
        if parsed.path not in {PERSONAL_TABLE_PATH, PERSONAL_SERIES_PATH}:
            return False
        try:
            query = parse_qs(
                parsed.query,
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=4,
            )
            is_series = parsed.path == PERSONAL_SERIES_PATH
            allowed = {"source_id", "entity_uid", "series_index"} if is_series else {"source_id", "entity_uid", "page", "page_size"}
            if not {"source_id", "entity_uid"} <= set(query) or not set(query) <= allowed:
                raise PersonalTableError("personal_table_invalid")
            if any(len(values) != 1 or values[0] == "" for values in query.values()):
                raise PersonalTableError("personal_table_invalid")
            identity = {key: query[key][0] for key in ("source_id", "entity_uid")}
            if is_series:
                raw_index = query.get("series_index", ["0"])[0]
                index = 0 if raw_index == "0" else _positive_integer(raw_index, maximum=199)
                payload = self.service.get_series(**identity, series_index=index)
            else:
                page = _positive_integer(query.get("page", ["1"])[0], maximum=None)
                page_size = _positive_integer(query.get("page_size", ["50"])[0], maximum=100)
                payload = self.service.get_page(**identity, page=page, page_size=page_size).public_dict()
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
        "personal_series_invalid": HTTPStatus.BAD_REQUEST,
        "personal_series_too_large": HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
    }[error.code]
