"""Bounded plotting projection of user-confirmed tabular series, never digitization."""
from __future__ import annotations

import math
import re
from decimal import Decimal
from typing import Any, Mapping, Sequence

MAX_SERIES_ROWS = 5_000
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$", re.ASCII)


class SeriesPlotError(ValueError):
    pass


def _number(value: str) -> float | None:
    text = value.strip().replace("−", "-")
    if len(text) > 100 or not _NUMBER.fullmatch(text):
        return None
    number = float(text)
    if not math.isfinite(number) or abs(number) > 1e150 or (number == 0 and Decimal(text) != 0):
        return None
    return number


def project_series(
    *, columns: Sequence[Mapping[str, Any]], names: Sequence[str],
    rows: Sequence[Sequence[str]], series: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep every row and its exact source text; missing/invalid values are gaps."""
    if len(rows) > MAX_SERIES_ROWS:
        raise SeriesPlotError("personal_series_too_large")
    by_name = {column["name"]: column for column in columns}
    x, y, uncertainty = (series.get(key) for key in ("x_column", "y_column", "uncertainty_column"))
    for name in (x, y, uncertainty):
        if name is not None and (name not in by_name or name not in names or by_name[name]["role"] == "ignore"):
            raise SeriesPlotError("personal_series_invalid")
    if not x or not y or x == y:
        raise SeriesPlotError("personal_series_invalid")
    if uncertainty and by_name[uncertainty].get("unit") != by_name[y].get("unit"):
        # No implicit unit conversion for error bars.
        raise SeriesPlotError("personal_series_invalid")
    xi, yi = names.index(x), names.index(y)
    ui = names.index(uncertainty) if uncertainty else None
    points = []
    counts = {key: 0 for key in ("valid_points", "missing_rows", "invalid_rows", "uncertainty_missing_rows", "uncertainty_invalid_rows")}
    for index, row in enumerate(rows, 1):
        xt, yt = str(row[xi]) if xi < len(row) else "", str(row[yi]) if yi < len(row) else ""
        xv, yv = _number(xt), _number(yt)
        state = "missing" if not xt.strip() or not yt.strip() else "invalid" if xv is None or yv is None else "valid"
        counts[{"valid": "valid_points", "missing": "missing_rows", "invalid": "invalid_rows"}[state]] += 1
        ut = (str(row[ui]) if ui < len(row) else "") if ui is not None else None
        uv = _number(ut) if ut is not None else None
        if ut is not None:
            if not ut.strip():
                counts["uncertainty_missing_rows"] += 1
            elif uv is None or uv < 0:
                counts["uncertainty_invalid_rows"] += 1
                uv = None
        points.append({"row": index, "x": xv, "y": yv, "x_text": xt, "y_text": yt,
                       "uncertainty": uv, "uncertainty_text": ut, "status": state})
    return {
        "name": series["name"], "x_column": x, "y_column": y,
        "uncertainty_column": uncertainty,
        "x_unit": by_name[x].get("unit"), "y_unit": by_name[y].get("unit"),
        "uncertainty_unit": by_name[uncertainty].get("unit") if uncertainty else None,
        "total_rows": len(rows), **counts, "points": points,
    }
