from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedValue:
    value_kind: str = "text"
    value_num: float | None = None
    uncertainty_num: float | None = None
    value_min: float | None = None
    value_max: float | None = None


NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"


def parse_value(raw: str) -> ParsedValue:
    text = raw.strip().replace("−", "-")
    match = re.fullmatch(rf"~?\s*({NUMBER})\s*(?:±|\+/-)\s*({NUMBER})", text)
    if match:
        return ParsedValue("number", float(match.group(1)), float(match.group(2)))
    match = re.fullmatch(rf"~?\s*({NUMBER})", text)
    if match:
        return ParsedValue("number", float(match.group(1)))
    match = re.fullmatch(rf"({NUMBER})\s*(?:-|–|—|to)\s*({NUMBER})", text, re.I)
    if match:
        low, high = float(match.group(1)), float(match.group(2))
        return ParsedValue("range", value_min=min(low, high), value_max=max(low, high))
    return ParsedValue()


def normalize_value(value: float | None, uncertainty: float | None, unit: str | None) -> tuple[float | None, float | None, str | None]:
    if value is None or not unit:
        return None, None, None
    compact = " ".join(unit.strip().replace("μ", "µ").split())
    conversions: dict[str, tuple[float, float, str]] = {
        "°C": (1.0, 273.15, "K"),
        "C": (1.0, 273.15, "K"),
        "K": (1.0, 0.0, "K"),
        "eV": (1.0, 0.0, "eV"),
        "keV": (1e3, 0.0, "eV"),
        "MeV": (1e6, 0.0, "eV"),
        "Pa": (1.0, 0.0, "Pa"),
        "MPa": (1e6, 0.0, "Pa"),
        "GPa": (1e9, 0.0, "Pa"),
        "m": (1.0, 0.0, "m"),
        "µm": (1e-6, 0.0, "m"),
        "um": (1e-6, 0.0, "m"),
        "nm": (1e-9, 0.0, "m"),
        "dpa": (1.0, 0.0, "dpa"),
        "cm^-2": (1e4, 0.0, "m^-2"),
        "cm-2": (1e4, 0.0, "m^-2"),
        "ions cm^-2": (1e4, 0.0, "ions m^-2"),
        "ions cm^-2 s^-1": (1e4, 0.0, "ions m^-2 s^-1"),
    }
    conversion = conversions.get(compact)
    if not conversion:
        return None, None, None
    scale, offset, normalized_unit = conversion
    return value * scale + offset, uncertainty * scale if uncertainty is not None else None, normalized_unit


def infer_evidence_type(category: str, parameter: str, value: str, note: str = "") -> str:
    text = f"{category} {parameter} {value} {note}".lower()
    if any(token in text for token in ["calculated", "predicted", "srim", "fispact", "model"]):
        return "calculated"
    if any(token in text for token in ["derived", "hardening Δ", "difference", "ratio"]):
        return "derived"
    if any(token in text for token in ["qualitative", "not detected", "no dislocation", "trend", "appeared", "increased", "decreased"]):
        return "qualitative"
    return "measured"


def source_precision_from_legacy(source_level: str, note: str) -> str:
    text = f"{source_level} {note}".lower()
    if "fig" in text or "趋势" in text or "curve" in text:
        return "trend" if any(x in text for x in ["趋势", "trend"]) else "figure_only"
    if "table" in text or "表格" in text or "补充材料" in text:
        return "exact_table"
    return "exact_text"

