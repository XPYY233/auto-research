"""Small shared boundary for spreadsheet-formula neutralization."""

from __future__ import annotations

from typing import Mapping


def spreadsheet_safe_cell(value: object) -> object:
    """Neutralize formulas while preserving literal exported text."""

    if not isinstance(value, str) or not value:
        return value
    stripped = value.lstrip(" \t\r\n")
    if not stripped or stripped[0] not in {"=", "+", "-", "@"}:
        return value
    return "'" + value


def spreadsheet_safe_row(row: Mapping[str, object]) -> dict[str, object]:
    return {key: spreadsheet_safe_cell(value) for key, value in row.items()}


__all__ = ["spreadsheet_safe_cell", "spreadsheet_safe_row"]
