from __future__ import annotations

import re
import unicodedata
import math
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Iterable

from .fact_model import PROPERTY_ALIASES
from .six_column import ELEMENT_SEARCH_ALIASES


CONSTRAINT_FIELDS = (
    "material",
    "irradiation",
    "particle",
    "temperature",
    "dose",
    "property",
    "state",
)

CONSTRAINT_LABELS = {
    "material": "材料",
    "irradiation": "辐照类型",
    "particle": "粒子",
    "temperature": "温度",
    "dose": "剂量/注量",
    "property": "物理量",
    "state": "样品状态",
}

MATCH_CLASS_ORDER = {"direct": 0, "adjacent": 1, "expansion": 2}


_MATERIAL_ALIASES: OrderedDict[str, tuple[str, ...]] = OrderedDict((
    ("难熔高熵合金", ("难熔高熵合金", "难熔高熵", "rhea", "refractory high entropy alloy")),
    ("高熵合金", ("高熵合金", "hea", "high entropy alloy", "high-entropy alloy")),
    ("中熵合金", ("中熵合金", "mea", "medium entropy alloy", "medium-entropy alloy")),
    ("钨及钨合金", ("钨合金", "钨及钨合金", "纯钨", "pure w", "pure tungsten", "w alloy", "tungsten alloy", "tungsten", "钨")),
    ("钢", ("不锈钢", "steel", "316h", "316l", "316ss", "钢")),
    ("SiC", ("sic", "silicon carbide", "碳化硅")),
))

_IRRADIATION_ALIASES: OrderedDict[str, tuple[str, ...]] = OrderedDict((
    ("中子辐照", ("中子辐照", "neutron irradiation", "neutron-irradiated", "neutron irradiated")),
    ("氦离子辐照", ("氦离子辐照", "he离子辐照", "he+ irradiation", "helium ion irradiation", "he irradiation")),
    ("氢离子辐照", ("氢离子辐照", "h+ irradiation", "hydrogen ion irradiation", "proton irradiation")),
    ("电子辐照", ("电子辐照", "electron irradiation", "electron-irradiated")),
    ("重离子辐照", ("重离子辐照", "heavy ion irradiation", "heavy-ion irradiation")),
    ("离子辐照", ("离子辐照", "ion irradiation", "ion-irradiated", "ion irradiated", "ion beam")),
))

_PARTICLE_ALIASES: OrderedDict[str, tuple[str, ...]] = OrderedDict((
    ("中子", ("中子", "neutron", "neutrons")),
    ("氦离子", ("氦离子", "he离子", "he+", "helium ion", "helium ions")),
    ("氢离子/质子", ("氢离子", "h+", "hydrogen ion", "proton", "protons")),
    ("电子", ("电子辐照", "electron irradiation", "electron-irradiated")),
    ("重离子", ("重离子", "heavy ion", "heavy-ion")),
))

_STATE_ALIASES: OrderedDict[str, tuple[str, ...]] = OrderedDict((
    ("辐照后", ("辐照后", "after irradiation", "irradiated state", "ion-irradiated", "neutron-irradiated")),
    ("辐照前/未辐照", ("辐照前", "未辐照", "before irradiation", "unirradiated", "as-received", "pristine")),
    ("退火后", ("退火后", "after annealing", "annealed")),
    ("制备态", ("制备态", "as-prepared", "as-deposited", "as-fabricated")),
))

_EXTRA_PROPERTY_ALIASES: OrderedDict[str, tuple[str, ...]] = OrderedDict((
    ("缺陷结构", ("缺陷结构", "defect structure", "defect microstructure")),
    ("气泡", ("气泡", "bubble", "bubbles", "helium bubble")),
    ("位错环", ("位错环", "dislocation loop", "dislocation loops")),
    ("空洞", ("空洞", "空腔", "void", "voids")),
    ("拉伸强度", ("拉伸强度", "tensile strength")),
    ("屈服强度", ("屈服强度", "yield strength")),
    ("断裂韧性", ("断裂韧性", "fracture toughness")),
    ("微观结构", ("微观结构", "microstructure", "microstructural")),
    ("元素偏聚", ("元素偏聚", "偏聚", "segregation", "enrichment", "depletion")),
))

_PROPERTY_LABELS = {
    "temperature": "温度",
    "irradiation_temperature": "辐照温度",
    "annealing_temperature": "退火温度",
    "dose": "辐照剂量",
    "fluence": "辐照注量",
    "flux": "辐照通量",
    "ion_energy": "离子能量",
    "hardness": "硬度",
    "thermal_conductivity": "热导率",
    "electrical_resistivity": "电阻率",
    "swelling": "肿胀",
    "void": "空洞",
    "dislocation_loop_density": "位错环密度",
    "dislocation_loop_size": "位错环尺寸",
    "dislocation": "位错",
    "composition": "成分",
    "grain_size": "晶粒尺寸",
    "phase": "相组成/相变",
    "lattice": "晶格",
    "thickness": "厚度",
    "depth": "深度",
    "time": "时间",
    "pressure": "压力/真空",
    "migration_energy": "迁移能",
    "formation_energy": "形成能",
    "elastic_modulus": "弹性模量",
}

_PROPERTY_ALIASES: OrderedDict[str, tuple[str, ...]] = OrderedDict(_EXTRA_PROPERTY_ALIASES)
for _concept, _aliases in PROPERTY_ALIASES.items():
    _PROPERTY_ALIASES.setdefault(_PROPERTY_LABELS.get(_concept, _concept), tuple(_aliases))

_GENERIC_AMBIGUOUS_TERMS = {
    "结果", "数据", "情况", "表现", "变化", "影响", "性能", "实验", "文章", "论文", "研究",
}

_COMPARISON_MARKERS = ("比较", "对比", "相比", "差异", "哪个更", "哪一种更")
_PRONOUN_MARKERS = ("它", "它们", "这个", "这些", "上述", "前者", "后者", "第二篇", "第三篇", "第四篇")


def _normal_text(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold().replace("℃", "°c")


def _compact_text(value: Any) -> str:
    text = _normal_text(value).replace("μ", "µ").replace("−", "-").replace("–", "-").replace("—", "-")
    text = text.replace("⁻", "-").replace("²", "2").replace("³", "3")
    return re.sub(r"[^0-9a-z\u4e00-\u9fff.+×x<>=±°µ^-]+", "", text)


def _has_alias(text: str, alias: str) -> bool:
    raw = str(text or "")
    candidate = str(alias or "").strip()
    if not candidate:
        return False
    if re.fullmatch(r"[A-Za-z]{1,4}", candidate):
        # Domain abbreviations such as HEA/MEA/RHEA/SiC and element symbols
        # are tokens, not substrings. Without both boundaries, "hea" in
        # "heat-treated" or "wheat" becomes a false material constraint.
        return bool(re.search(
            rf"(?<![A-Za-z0-9]){re.escape(candidate)}(?![A-Za-z0-9])",
            raw,
            re.I,
        ))
    return _normal_text(candidate) in _normal_text(raw)


def _dedupe(values: Iterable[Any], limit: int = 12) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(str(value or "").split()).strip()
        key = _normal_text(cleaned)
        if not cleaned or key in seen:
            continue
        seen.add(key)
        output.append(cleaned[:160])
        if len(output) >= limit:
            break
    return tuple(output)


def _extract_from_aliases(question: str, groups: OrderedDict[str, tuple[str, ...]]) -> list[str]:
    matches: list[str] = []
    for canonical, aliases in groups.items():
        if any(_has_alias(question, alias) for alias in aliases):
            matches.append(canonical)
    return matches


_ELEMENT_SYMBOLS = (
    "He", "Li", "Be", "Ne", "Na", "Mg", "Al", "Si", "Cl", "Ar", "Ca", "Sc", "Ti", "Cr",
    "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br", "Kr", "Rb", "Sr",
    "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn", "Sb", "Te", "Xe",
    "Cs", "Ba", "La", "Ce", "Pr", "Nd", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm",
    "Yb", "Lu", "Hf", "Ta", "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl", "Pb", "Bi", "Th",
    "Pa", "Np", "Pu", "H", "B", "C", "N", "O", "F", "P", "S", "K", "V", "Y", "I", "W", "U",
)
_ELEMENT_PATTERN = "|".join(sorted(_ELEMENT_SYMBOLS, key=len, reverse=True))
_FORMULA_RE = re.compile(rf"(?<![A-Za-z])(?:(?:{_ELEMENT_PATTERN})\d*(?:\.\d+)?[-–—]?)+(?<![-–—])(?![a-z])")


def _material_formulas(question: str) -> list[str]:
    output: list[str] = []
    for match in _FORMULA_RE.finditer(str(question or "")):
        value = match.group(0)
        symbols = re.findall(_ELEMENT_PATTERN, value)
        if len(symbols) >= 2 and len(value) >= 3:
            output.append(value)
    for named in ("316H", "316L", "ODS-NiCoFeCr", "SiC"):
        if _has_alias(question, named):
            output.append(named)
    for match in re.finditer(r"(?<![A-Za-z0-9])W(?![A-Za-z0-9])", str(question or "")):
        # A number followed by W is normally a power unit, not elemental
        # tungsten. Formulae such as W-Ta are already handled above.
        if re.search(r"\d(?:\.\d+)?\s*$", str(question or "")[:match.start()]):
            continue
        output.append("W")
    return output


def _condition_values(question: str) -> tuple[list[str], list[str]]:
    raw = str(question or "")
    superscript = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")
    # NFKC turns 10¹⁶ into 1016. Preserve the exponent marker before
    # normalization so scientific fluence notation remains unambiguous.
    raw = re.sub(
        r"10([⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+)",
        lambda match: "10^" + match.group(1).translate(superscript),
        raw,
    )
    text = (
        unicodedata.normalize("NFKC", raw)
        .replace("℃", "°C")
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
    )
    temperatures: list[str] = []
    doses: list[str] = []
    comparison_prefix = r"(?:高于|超过|大于|低于|小于|不低于|不高于|≥|<=|>=|≤|>|<)?\s*"
    temperature_re = re.compile(
        comparison_prefix + r"[+-]?\d+(?:\.\d+)?\s*(?:°\s*C|K)(?![A-Za-z])\s*(?:以上|以下)?",
        re.I,
    )
    scientific_number = (
        r"(?:\d+(?:\.\d+)?\s*[×x]\s*10\s*\^\s*[+-]?\d+"
        r"|\d+(?:\.\d+)?\s*[eE]\s*[+-]?\d+"
        r"|10\s*\^\s*[+-]?\d+"
        r"|\d+(?:\.\d+)?)"
    )
    area_unit = r"(?:cm|m)\s*(?:\^?\s*-?\s*2|-2)"
    beam_species = (
        r"(?:(?:neutrons?|protons?|ions?|particles?|n)"
        r"|(?:helium|hydrogen)\s*(?:ions?)?"
        rf"|(?:(?:{_ELEMENT_PATTERN})|D|T)(?:\d*\+|\+)?\s*(?:ions?)?)"
    )
    fluence_re = re.compile(
        comparison_prefix + scientific_number
        + r"\s*" + beam_species + r"?\s*/?\s*" + area_unit
        + r"(?:\s*/?\s*s\s*(?:\^?\s*-?\s*1|-1))?\s*(?:以上|以下)?",
        re.I,
    )
    dpa_re = re.compile(
        comparison_prefix + r"\d+(?:\.\d+)?(?:\s*[eE]\s*[+-]?\d+)?\s*dpa\s*(?:以上|以下)?",
        re.I,
    )
    temperatures.extend(" ".join(match.group(0).split()) for match in temperature_re.finditer(text))
    doses.extend(" ".join(match.group(0).split()) for match in fluence_re.finditer(text))
    doses.extend(" ".join(match.group(0).split()) for match in dpa_re.finditer(text))
    for qualitative in ("高温", "室温", "低温"):
        if qualitative in text:
            temperatures.append(qualitative)
    return temperatures, doses


def _fluence_beam_constraints(question: str) -> tuple[list[str], list[str]]:
    irradiation: list[str] = []
    particles: list[str] = []
    for token in _condition_values(question)[1]:
        if "dpa" in _normal_text(token):
            continue
        raw = unicodedata.normalize("NFKC", token)
        normalized = _normal_text(raw)
        if re.search(r"(?<![a-z])(?:neutrons?|n)\s*/?\s*(?:cm|m)", normalized):
            irradiation.append("中子辐照")
            particles.append("中子")
            continue
        if re.search(r"(?<![a-z])(?:helium|he)(?:\d*\+|\+)?\s*(?:ions?)?\s*/?\s*(?:cm|m)", normalized):
            irradiation.append("氦离子辐照")
            particles.append("氦离子")
            continue
        if re.search(r"(?<![a-z])(?:hydrogen|protons?|h)(?:\d*\+|\+)?\s*(?:ions?)?\s*/?\s*(?:cm|m)", normalized):
            irradiation.append("氢离子辐照")
            particles.append("氢离子/质子")
            continue
        element = re.search(
            rf"(?<![A-Za-z])({_ELEMENT_PATTERN}|D|T)(?:\d*\+|\+)?\s*(?:ions?)?\s*/?\s*(?:cm|m)",
            raw,
        )
        if element:
            symbol = element.group(1)
            irradiation.append("离子辐照")
            particles.append(f"{symbol}离子")
        elif re.search(r"(?<![a-z])ions?\s*/?\s*(?:cm|m)", normalized):
            irradiation.append("离子辐照")
    return irradiation, particles


def _property_values(question: str) -> list[str]:
    matched = _extract_from_aliases(question, _PROPERTY_ALIASES)
    # Prefer the specific concept when a generic alias is nested inside it.
    if "辐照温度" in matched and "温度" in matched:
        matched.remove("温度")
    if "退火温度" in matched and "温度" in matched:
        matched.remove("温度")
    if "位错环密度" in matched:
        matched = [value for value in matched if value not in {"位错", "位错环"}]
    if "位错环尺寸" in matched:
        matched = [value for value in matched if value not in {"位错", "位错环"}]
    if "缺陷结构" in matched:
        matched = [value for value in matched if value != "微观结构"]
    return matched


def extract_local_constraints(question: str) -> dict[str, tuple[str, ...]]:
    temperatures, doses = _condition_values(question)
    fluence_irradiation, fluence_particles = _fluence_beam_constraints(question)
    materials = _extract_from_aliases(question, _MATERIAL_ALIASES)
    if "难熔高熵合金" in materials and "高熵合金" in materials:
        materials.remove("高熵合金")
    materials.extend(_material_formulas(question))
    irradiation = _extract_from_aliases(question, _IRRADIATION_ALIASES)
    irradiation.extend(fluence_irradiation)
    if any(value in irradiation for value in ("氦离子辐照", "氢离子辐照", "重离子辐照")):
        irradiation = [value for value in irradiation if value != "离子辐照"]
    particles = _extract_from_aliases(question, _PARTICLE_ALIASES)
    particles.extend(fluence_particles)
    particles.extend(
        f"{match.group(1)}离子"
        for match in re.finditer(rf"(?<![A-Za-z°])({_ELEMENT_PATTERN})\s*(?:离子|ions?)(?![A-Za-z])", question, re.I)
    )
    states = _extract_from_aliases(question, _STATE_ALIASES)
    properties = _property_values(question)
    if temperatures:
        properties = [value for value in properties if value not in {"温度", "辐照温度", "退火温度"}]
    if doses:
        properties = [value for value in properties if value not in {"辐照剂量", "辐照注量", "辐照通量"}]
    return {
        "material": _dedupe(materials),
        "irradiation": _dedupe(irradiation),
        "particle": _dedupe(particles),
        "temperature": _dedupe(temperatures),
        "dose": _dedupe(doses),
        "property": _dedupe(properties),
        "state": _dedupe(states),
    }


def aliases_for(field: str, value: str) -> tuple[str, ...]:
    groups: OrderedDict[str, tuple[str, ...]] | None = {
        "material": _MATERIAL_ALIASES,
        "irradiation": _IRRADIATION_ALIASES,
        "particle": _PARTICLE_ALIASES,
        "property": _PROPERTY_ALIASES,
        "state": _STATE_ALIASES,
    }.get(field)
    if groups:
        for canonical, aliases in groups.items():
            if _normal_text(value) == _normal_text(canonical) or any(
                _normal_text(value) == _normal_text(alias) for alias in aliases
            ):
                return _dedupe((canonical, *aliases), 16)
    if field == "material":
        element_aliases = ELEMENT_SEARCH_ALIASES.get(_normal_text(value))
        if element_aliases:
            return _dedupe((value, *element_aliases), 16)
    if field == "particle" and value.endswith("离子"):
        symbol = value[:-2].strip()
        if re.fullmatch(_ELEMENT_PATTERN, symbol, re.I):
            return _dedupe((value, f"{symbol} ion", f"{symbol} ions", f"{symbol}+"), 8)
    return (str(value),)


def merge_model_constraints(
    question: str,
    local: dict[str, tuple[str, ...]],
    model_constraints: Any,
    *,
    current_question: str | None = None,
    explicit_fields: Iterable[str] = (),
) -> dict[str, tuple[str, ...]]:
    # DeepSeek may suggest queries and a human-readable focus, but it may not
    # create hard scientific conditions. Text occurrence is insufficient to
    # disambiguate roles such as Ni material vs Ni ion, He material vs He
    # particle, or 300 °C vs 300 dpa. The deterministic local parser (plus
    # bounded user-history inheritance) is the sole hard-condition authority.
    _ = question, model_constraints, current_question, explicit_fields
    return {field: _dedupe(local.get(field, ())) for field in CONSTRAINT_FIELDS}


def _critical_ambiguity(question: str, constraints: dict[str, tuple[str, ...]], history: Any) -> tuple[bool, str, list[str]]:
    text = str(question or "").strip()
    has_history = isinstance(history, list) and any(
        isinstance(item, dict) and str(item.get("content") or "").strip() for item in history
    )
    if not has_history and any(marker in text for marker in _PRONOUN_MARKERS):
        return True, "你提到的对象在当前对话中还不明确。请补充材料名称、论文或要比较的证据。", [
            "按材料名称重新描述问题",
            "给出论文题目或 DOI",
        ]
    if not has_history and any(marker in text for marker in _COMPARISON_MARKERS) and len(constraints["material"]) < 2:
        return True, "你希望比较哪些材料、样品或论文？请至少给出两个明确对象。", [
            "比较两种指定材料的同一物理量",
            "比较同一材料在两种实验条件下的结果",
        ]
    meaningful = sum(len(constraints[field]) for field in CONSTRAINT_FIELDS)
    residual = set(re.findall(r"[\u4e00-\u9fff]{2,6}", text)) - _GENERIC_AMBIGUOUS_TERMS
    if meaningful == 0 and (len(text) < 10 or not residual):
        return True, "这个问题还缺少可检索的科研对象。请补充材料，以及至少一个实验条件或物理量。", [
            "查找钨合金辐照后的空洞结果",
            "查找高熵合金辐照后的硬度数据",
        ]
    return False, "", []


@dataclass(frozen=True)
class QueryAnalysis:
    focus: str
    constraints: dict[str, tuple[str, ...]]
    soft_expansions: dict[str, tuple[str, ...]]
    needs_clarification: bool = False
    clarification_question: str = ""
    clarification_options: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "focus": self.focus,
            "constraints": {
                field: {
                    "label": CONSTRAINT_LABELS[field],
                    "values": list(self.constraints.get(field, ())),
                    "mode": "hard",
                }
                for field in CONSTRAINT_FIELDS if self.constraints.get(field)
            },
            "soft_expansions": {key: list(values) for key, values in self.soft_expansions.items() if values},
            "needs_clarification": self.needs_clarification,
            "clarification_question": self.clarification_question,
            "clarification_options": list(self.clarification_options),
        }


def build_query_analysis(
    question: str,
    *,
    model_payload: Any = None,
    history: Any = None,
) -> QueryAnalysis:
    explicit_local = extract_local_constraints(question)
    local = dict(explicit_local)
    history_texts: list[str] = []
    if isinstance(history, list):
        history_texts = [
            str(item.get("content") or "")
            for item in history[-6:]
            if isinstance(item, dict) and item.get("role") == "user" and str(item.get("content") or "").strip()
        ]
    # A follow-up such as “那缺陷呢？” inherits only dimensions omitted from
    # the current turn. Explicit current conditions always take precedence.
    # Irradiation family and particle are coupled: when the current turn
    # explicitly changes either one, carrying the other over from a previous
    # experiment would create an impossible hybrid such as ion irradiation +
    # neutron particle.
    current_beam_family = bool(explicit_local["irradiation"] or explicit_local["particle"])
    current_anchored_fields = {
        field for field in CONSTRAINT_FIELDS if explicit_local[field]
    }
    if current_beam_family:
        current_anchored_fields.update({"irradiation", "particle"})
    for previous in reversed(history_texts):
        inherited = extract_local_constraints(previous)
        for field in CONSTRAINT_FIELDS:
            if field in {"irradiation", "particle"} and current_beam_family:
                continue
            if not local[field] and inherited[field]:
                local[field] = inherited[field]
    payload = model_payload if isinstance(model_payload, dict) else {}
    anchor_text = "\n".join([question, *history_texts])
    constraints = merge_model_constraints(
        anchor_text,
        local,
        payload.get("constraints"),
        current_question=question,
        explicit_fields=current_anchored_fields,
    )
    needs_clarification, clarification_question, options = _critical_ambiguity(question, constraints, history)
    model_clarification = payload.get("clarification") if isinstance(payload.get("clarification"), dict) else {}
    if needs_clarification:
        proposed = " ".join(str(model_clarification.get("question") or "").split()).strip()
        if proposed and len(proposed) <= 180:
            clarification_question = proposed
        proposed_options = model_clarification.get("options") or []
        if isinstance(proposed_options, list):
            cleaned = _dedupe(proposed_options, 3)
            if cleaned:
                options = list(cleaned)
    soft_expansions: dict[str, tuple[str, ...]] = {}
    for field in ("material", "irradiation", "particle", "property"):
        aliases: list[str] = []
        for value in constraints[field]:
            aliases.extend(alias for alias in aliases_for(field, value) if _normal_text(alias) != _normal_text(value))
        soft_expansions[field] = _dedupe(aliases, 8)
    focus = " ".join(str(payload.get("focus") or question).split()).strip()[:240]
    return QueryAnalysis(
        focus=focus or str(question).strip(),
        constraints=constraints,
        soft_expansions=soft_expansions,
        needs_clarification=needs_clarification,
        clarification_question=clarification_question,
        clarification_options=tuple(options[:3]),
    )


def soft_recall_queries(analysis: QueryAnalysis, limit: int = 6) -> list[str]:
    properties = list(analysis.constraints.get("property", ())) or [""]
    queries: list[str] = []
    for field in ("material", "irradiation", "particle"):
        for alias in analysis.soft_expansions.get(field, ())[:3]:
            queries.append(" ".join(part for part in (alias, properties[0]) if part))
    return list(_dedupe(queries, limit))


def candidate_text(candidate: dict[str, Any]) -> str:
    fields = (
        "title", "value", "unit", "context", "evidence", "finding", "caption", "materials", "conditions",
        "quantities", "article_title", "doi", "label",
    )
    parts: list[str] = []
    for field in fields:
        value = candidate.get(field)
        if isinstance(value, (list, tuple, set)):
            parts.extend(str(item) for item in value)
        elif isinstance(value, dict):
            parts.extend(str(item) for item in value.values())
        elif value not in (None, ""):
            parts.append(str(value))
    return " ".join(parts)


def constraint_text(candidate: dict[str, Any], field: str) -> str:
    """Use record-level evidence for hard matching, never paper-title proximity."""

    field_map = {
        "property": ("title", "finding", "quantities", "caption", "context", "evidence", "label"),
        "material": ("materials", "context", "evidence", "caption", "title"),
        "irradiation": ("conditions", "context", "evidence", "caption", "title", "finding"),
        "particle": ("conditions", "context", "evidence", "caption", "title", "finding"),
        "temperature": ("conditions", "context", "evidence", "caption", "title", "finding"),
        "dose": ("conditions", "context", "evidence", "caption", "title", "finding", "value", "unit"),
        "state": ("conditions", "context", "evidence", "caption", "title", "finding"),
    }
    parts: list[str] = []
    for key in field_map.get(field, ()):
        value = candidate.get(key)
        if isinstance(value, (list, tuple, set)):
            parts.extend(str(item) for item in value)
        elif isinstance(value, dict):
            parts.extend(str(item) for item in value.values())
        elif value not in (None, ""):
            parts.append(str(value))
    return " ".join(parts)


def _scientific_number(text: str) -> float | None:
    normalized = str(text or "").replace("−", "-").replace("–", "-")
    times = re.search(
        r"([+-]?\d+(?:\.\d+)?)\s*[×x]\s*10\s*\^\s*([+-]?\d+)",
        normalized,
        re.I,
    )
    if times:
        return float(times.group(1)) * (10 ** int(times.group(2)))
    exponent = re.search(r"([+-]?\d+(?:\.\d+)?)\s*[eE]\s*([+-]?\d+)", normalized)
    if exponent:
        return float(exponent.group(1)) * (10 ** int(exponent.group(2)))
    power = re.search(r"(?<![\d.])10\s*\^\s*([+-]?\d+)", normalized)
    if power:
        return float(10 ** int(power.group(1)))
    scalar = re.search(r"[+-]?\d+(?:\.\d+)?", normalized)
    return float(scalar.group(0)) if scalar else None


def _canonical_dose_values(text: str) -> list[tuple[str, float]]:
    """Normalize dpa, fluence and flux without changing stored evidence text."""

    output: list[tuple[str, float]] = []
    for token in _condition_values(text)[1]:
        value = _scientific_number(token)
        if value is None:
            continue
        normalized = _normal_text(token).replace("−", "-").replace("–", "-")
        if "dpa" in normalized:
            output.append(("dpa", value))
            continue
        area = "cm" if re.search(r"cm\s*(?:\^?\s*-?\s*2|-2)", normalized) else "m"
        per_second = bool(re.search(r"s\s*(?:\^?\s*-?\s*1|-1)", normalized))
        # Convert inverse square centimetres to inverse square metres. The
        # particle family remains a separate hard-condition dimension.
        base_value = value * 1e4 if area == "cm" else value
        output.append(("flux" if per_second else "fluence", base_value))
    return output


def _comparison_direction(value: str) -> str:
    normalized = _normal_text(value)
    if any(marker in normalized for marker in ("不高于", "不超过", "≤", "<=")):
        return "maximum"
    if any(marker in normalized for marker in ("不低于", "不少于", "≥", ">=")):
        return "minimum"
    if any(marker in normalized for marker in ("高于", "超过", "大于", "以上", ">")):
        return "minimum"
    if any(marker in normalized for marker in ("低于", "小于", "以下", "<")):
        return "maximum"
    return "exact"


def _constraint_matches(field: str, value: str, text: str) -> bool:
    compact = _compact_text(text)
    if field in {"temperature", "dose"}:
        expected = _compact_text(value)
        if expected and expected in compact:
            return True
        if field == "dose":
            requested = _canonical_dose_values(value)
            reported = _canonical_dose_values(text)
            direction = _comparison_direction(value)
            for requested_kind, target in requested:
                for reported_kind, candidate in reported:
                    if requested_kind != reported_kind:
                        continue
                    if direction == "minimum":
                        if candidate >= target:
                            return True
                    elif direction == "maximum":
                        if candidate <= target:
                            return True
                    elif math.isclose(candidate, target, rel_tol=1e-9, abs_tol=0.0):
                        return True
            return False
        # Preserve a conservative hard-condition policy: inequalities match
        # only when both sides carry the same unit and the candidate value is explicit.
        number = re.search(r"([+-]?\d+(?:\.\d+)?)", value)
        if not number:
            return False
        unit = "°c" if "°" in _normal_text(value) else "k" if re.search(r"\bk\b", _normal_text(value)) else "dpa" if "dpa" in _normal_text(value) else ""
        if not unit:
            return False
        candidate_values = [
            float(raw)
            for raw in re.findall(
                rf"([+-]?\d+(?:\.\d+)?)\s*{re.escape(unit)}(?![a-z])",
                _normal_text(text),
            )
        ]
        if not candidate_values:
            return False
        target = float(number.group(1))
        direction = _comparison_direction(value)
        if direction == "minimum":
            return any(candidate >= target for candidate in candidate_values)
        if direction == "maximum":
            return any(candidate <= target for candidate in candidate_values)
        return any(abs(candidate - target) <= max(1e-9, abs(target) * 1e-6) for candidate in candidate_values)
    return any(_has_alias(text, alias) for alias in aliases_for(field, value))


def reason_candidates(candidates: list[dict[str, Any]], analysis: QueryAnalysis) -> list[dict[str, Any]]:
    reasoned: list[dict[str, Any]] = []
    active_fields = [field for field in CONSTRAINT_FIELDS if analysis.constraints.get(field)]
    for candidate in candidates:
        matched: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []
        for field in active_fields:
            values = analysis.constraints[field]
            scoped_text = constraint_text(candidate, field)
            hits = [value for value in values if _constraint_matches(field, value, scoped_text)]
            target = {
                "field": field,
                "label": CONSTRAINT_LABELS[field],
                "requested": list(values),
            }
            if hits:
                matched.append({**target, "matched": hits})
            else:
                missing.append(target)
        match_class = "direct" if not missing else "adjacent" if len(missing) == 1 else "expansion"
        reasoned.append({
            **candidate,
            "match_class": match_class,
            "matched_constraints": matched,
            "missing_constraints": missing,
            "constraint_coverage": 1.0 if not active_fields else round((len(active_fields) - len(missing)) / len(active_fields), 3),
        })
    reasoned.sort(key=lambda row: (
        MATCH_CLASS_ORDER.get(str(row.get("match_class")), 9),
        -float(row.get("constraint_coverage") or 0),
        -float(row.get("search_score") or 0),
        int(row.get("paper_id") or 0),
        int(str(row.get("ref") or "R9999")[1:] or 9999),
    ))
    return reasoned


def _candidate_material(candidate: dict[str, Any], analysis: QueryAnalysis) -> str:
    materials = candidate.get("materials")
    if isinstance(materials, list) and materials:
        return " / ".join(str(value) for value in materials[:2])
    if isinstance(materials, str) and materials.strip():
        return materials.strip()[:100]
    formulas = _material_formulas(constraint_text(candidate, "material"))
    if formulas:
        return " / ".join(_dedupe(formulas, 2))
    context = str(candidate.get("context") or "")
    first = re.split(r"[；;。]", context)[0].strip()
    if _looks_like_material_lead(first):
        return first
    matched = next((item.get("matched") for item in candidate.get("matched_constraints", []) if item.get("field") == "material"), None)
    if matched:
        return " / ".join(str(value) for value in matched[:2])
    return "材料未明确"


def _looks_like_material_lead(value: str) -> bool:
    cleaned = " ".join(str(value or "").split()).strip()
    if not cleaned or len(cleaned) > 120:
        return False
    if any(
        marker in _normal_text(cleaned)
        for marker in ("合金", "钢", "tungsten", "alloy", "sic", "316", "ods", "纯钨", "陶瓷", "涂层")
    ):
        return True
    letters = re.sub(r"[^A-Za-z]", "", cleaned)
    if not letters:
        return False
    symbols = re.findall(_ELEMENT_PATTERN, letters)
    return bool(symbols) and "".join(symbols) == letters


def _candidate_material_signature(candidate: dict[str, Any]) -> str:
    structured = candidate.get("materials")
    if isinstance(structured, list) and structured:
        return _normal_text("|".join(str(value) for value in structured if str(value).strip()))
    if isinstance(structured, str) and structured.strip():
        return _normal_text(structured)
    context = str(candidate.get("context") or "")
    first = re.split(r"[；;。]", context)[0].strip()
    if _looks_like_material_lead(first):
        return _normal_text(first)
    return f"record:{candidate.get('entity_type')}:{candidate.get('entity_id')}"


def _candidate_conditions(candidate: dict[str, Any]) -> str:
    text = constraint_text(candidate, "temperature")
    temperatures, doses = _condition_values(text)
    values: list[str] = [*temperatures, *doses]
    values.extend(_extract_from_aliases(text, _IRRADIATION_ALIASES))
    values.extend(_extract_from_aliases(text, _PARTICLE_ALIASES))
    values.extend(_extract_from_aliases(text, _STATE_ALIASES))
    values.extend(
        " ".join(match.group(0).split())
        for match in re.finditer(r"[+-]?\d+(?:\.\d+)?\s*(?:eV|keV|MeV)", text, re.I)
    )
    if values:
        return "；".join(_dedupe(values, 6))
    raw = str(candidate.get("conditions") or candidate.get("context") or "").strip()
    return raw[:180] or "条件未明确"


def _candidate_condition_signature(candidate: dict[str, Any], display: str) -> str:
    structured = str(candidate.get("conditions") or "").strip()
    context = str(candidate.get("context") or "").strip()
    if structured:
        raw = structured
    elif context:
        parts = re.split(r"[；;。]", context)
        raw = "；".join(parts[1:]).strip() if parts and _looks_like_material_lead(parts[0]) else context
    else:
        raw = ""
    if raw:
        return _normal_text(f"{display}|{raw}")[:800]
    if display != "条件未明确":
        return _normal_text(display)
    return f"record:{candidate.get('entity_type')}:{candidate.get('entity_id')}"


def build_evidence_bundles(candidates: list[dict[str, Any]], analysis: QueryAnalysis) -> list[dict[str, Any]]:
    grouped: OrderedDict[tuple[Any, ...], list[dict[str, Any]]] = OrderedDict()
    for candidate in candidates:
        material = _candidate_material(candidate, analysis)
        conditions = _candidate_conditions(candidate)
        key = (
            int(candidate.get("paper_id") or 0),
            _candidate_material_signature(candidate),
            _candidate_condition_signature(candidate, conditions),
        )
        grouped.setdefault(key, []).append(candidate)
    bundles: list[dict[str, Any]] = []
    for index, ((paper_id, _, _), members) in enumerate(grouped.items(), 1):
        bundle_id = f"B{index}"
        for member in members:
            member["bundle_id"] = bundle_id
        missing = _dedupe(
            item["label"]
            for member in members
            for item in member.get("missing_constraints", [])
        )
        match_class = min(
            (str(member.get("match_class") or "expansion") for member in members),
            key=lambda value: MATCH_CLASS_ORDER.get(value, 9),
        )
        bundles.append({
            "id": bundle_id,
            "classification": match_class,
            "paper_id": paper_id,
            "article_title": members[0].get("article_title") or "未命名文章",
            "doi": members[0].get("doi") or "",
            "material": _candidate_material(members[0], analysis),
            "conditions": _candidate_conditions(members[0]),
            "properties": list(_dedupe(
                ((member.get("title") or member.get("label") or "证据") for member in members),
                8,
            )),
            "refs": [str(member.get("ref")) for member in members],
            "entity_types": list(_dedupe((member.get("entity_type") for member in members), 4)),
            "relaxed_constraints": list(missing),
        })
    return bundles


def build_article_recommendations(
    candidates: list[dict[str, Any]],
    *,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Aggregate bounded evidence candidates into conservative paper suggestions.

    Recommendations are a read-only paper view over the existing four evidence
    types. They do not add a fifth evidence type and do not make uncited model
    claims. Direct and adjacent papers are preferred; expansion papers are used
    only when the database has no closer paper at all.
    """

    if limit < 1:
        return []
    grouped: OrderedDict[int, list[dict[str, Any]]] = OrderedDict()
    for candidate in candidates:
        paper_id = int(candidate.get("paper_id") or 0)
        if paper_id > 0:
            grouped.setdefault(paper_id, []).append(candidate)

    ranked: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for paper_id, members in grouped.items():
        best_class = min(
            (str(member.get("match_class") or "expansion") for member in members),
            key=lambda value: MATCH_CLASS_ORDER.get(value, 9),
        )
        supporting = [
            member for member in members
            if str(member.get("match_class") or "expansion") == best_class
        ]
        supporting.sort(key=lambda row: (
            -float(row.get("constraint_coverage") or 0),
            -float(row.get("search_score") or 0),
            int(str(row.get("ref") or "R9999")[1:] or 9999),
        ))
        matched_conditions = _dedupe(
            f"{item.get('label')}：{'、'.join(str(value) for value in item.get('matched') or [])}"
            for member in supporting
            for item in member.get("matched_constraints") or []
            if item.get("label") and item.get("matched")
        )
        relaxed = _dedupe(
            item.get("label")
            for member in supporting
            for item in member.get("missing_constraints") or []
            if item.get("label")
        )
        properties = _dedupe(
            member.get("title") or member.get("label") or "证据"
            for member in supporting
        )
        entity_types = _dedupe(
            member.get("entity_type") for member in supporting
        )
        refs = _dedupe(
            str(member.get("ref") or "") for member in supporting
            if re.fullmatch(r"R\d+", str(member.get("ref") or ""))
        )
        if best_class == "direct":
            reason = f"该论文有 {len(supporting)} 项候选同时满足当前问题的全部硬条件。"
            level = "direct"
        elif best_class == "adjacent":
            relaxed_text = "、".join(relaxed) or "一个硬条件"
            reason = f"这是最接近当前问题的相邻论文；阅读时需注意缺少或放宽：{relaxed_text}。"
            level = "related"
        else:
            reason = "当前数据库没有更接近的论文；此文仅作为拓展阅读，不能视为原问题的直接证据。"
            level = "expansion"
        first = supporting[0]
        recommendation = {
            "paper_id": paper_id,
            "article_title": first.get("article_title") or "未命名文章",
            "doi": first.get("doi") or "",
            "first_author": first.get("first_author") or "",
            "year": first.get("year"),
            "recommendation_level": level,
            "match_class": best_class,
            "constraint_coverage": max(
                float(member.get("constraint_coverage") or 0) for member in supporting
            ),
            "why_recommended": reason,
            "matched_conditions": list(matched_conditions),
            "relaxed_constraints": list(relaxed),
            "properties": list(properties),
            "entity_types": list(entity_types),
            "supporting_refs": list(refs[:4]),
            "evidence_count": len(members),
        }
        rank = (
            MATCH_CLASS_ORDER.get(best_class, 9),
            -recommendation["constraint_coverage"],
            -len(entity_types),
            -len(members),
            -max(float(member.get("search_score") or 0) for member in supporting),
            paper_id,
        )
        ranked.append((rank, recommendation))

    close = [item for item in ranked if item[1]["match_class"] in {"direct", "adjacent"}]
    selected = close if close else ranked
    selected.sort(key=lambda item: item[0])
    return [recommendation for _, recommendation in selected[:limit]]


def _select_diverse(candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    bundle_counts: dict[str, int] = {}
    for candidate in candidates:
        bundle = str(candidate.get("bundle_id") or "")
        if bundle_counts.get(bundle, 0) >= 2:
            continue
        selected.append(candidate)
        bundle_counts[bundle] = bundle_counts.get(bundle, 0) + 1
        if len(selected) >= limit:
            break
    return selected or candidates[:limit]


def _matrix_row(candidate: dict[str, Any], analysis: QueryAnalysis) -> dict[str, Any]:
    entity_type = str(candidate.get("entity_type") or "item")
    if entity_type == "item":
        value = " ".join(str(part) for part in (candidate.get("value"), candidate.get("unit")) if part not in (None, "")).strip()
    elif entity_type == "finding":
        value = str(candidate.get("finding") or "定性实验结论")[:220]
    else:
        value = "原始表格" if entity_type == "table" else "论文图片"
    return {
        "bundle_id": candidate.get("bundle_id") or "",
        "refs": [candidate.get("ref")],
        "material": _candidate_material(candidate, analysis),
        "conditions": _candidate_conditions(candidate),
        "property": candidate.get("title") or candidate.get("label") or "证据",
        "result": value or "见原文证据",
        "article_title": candidate.get("article_title") or "未命名文章",
        "doi": candidate.get("doi") or "",
        "source_page": candidate.get("source_page"),
        "entity_type": entity_type,
    }


def _coverage_gaps(analysis: QueryAnalysis, direct: list[dict[str, Any]]) -> list[str]:
    requested = [
        f"{CONSTRAINT_LABELS[field]}={','.join(analysis.constraints[field])}"
        for field in CONSTRAINT_FIELDS if analysis.constraints.get(field)
    ]
    if not direct:
        return [f"当前数据库未找到同时满足“{'；'.join(requested)}”的直接证据。"] if requested else ["当前数据库未找到可作为直接回答的证据。"]
    gaps: list[str] = []
    for field in ("material", "property"):
        for value in analysis.constraints.get(field, ()):
            if not any(_constraint_matches(field, value, constraint_text(candidate, field)) for candidate in direct):
                gaps.append(f"直接证据尚未覆盖{CONSTRAINT_LABELS[field]}“{value}”。")
    return gaps or ["当前直接证据覆盖了已解析的硬条件；仍建议打开原文核对实验口径与误差定义。"]


def _default_followups(analysis: QueryAnalysis, has_direct: bool) -> list[str]:
    material = next(iter(analysis.constraints.get("material", ())), "该材料")
    prop = next(iter(analysis.constraints.get("property", ())), "目标性质")
    if has_direct:
        return [
            f"只比较{material}中来自同一实验条件的{prop}结果",
            "打开回答引用对应的原始表格和论文图片",
            "按论文归纳这些证据的实验方法与误差来源",
        ]
    return [
        f"放宽一个实验条件后查找{material}的{prop}相关证据",
        "查看当前问题最接近的原始表格和论文图片",
        "列出数据库中缺少的材料与实验条件组合",
    ]


def build_research_report(
    analysis: QueryAnalysis,
    candidates: list[dict[str, Any]],
    *,
    direct_text: str = "",
    direct_refs: Iterable[str] = (),
    related_refs: Iterable[str] = (),
    related_notes: dict[str, str] | None = None,
    suggested_followups: Iterable[str] = (),
) -> dict[str, Any]:
    by_ref = {str(candidate.get("ref")): candidate for candidate in candidates}
    direct_all = [candidate for candidate in candidates if candidate.get("match_class") == "direct"]
    adjacent_all = [candidate for candidate in candidates if candidate.get("match_class") == "adjacent"]
    selected_direct = [by_ref[ref] for ref in _dedupe(direct_refs, 8) if ref in by_ref and by_ref[ref].get("match_class") == "direct"]
    selected_related = [by_ref[ref] for ref in _dedupe(related_refs, 6) if ref in by_ref and by_ref[ref].get("match_class") == "adjacent"]
    if not selected_direct and direct_all:
        selected_direct = _select_diverse(direct_all, 6)
    if not selected_related and adjacent_all:
        selected_related = _select_diverse(adjacent_all, 4)
    direct_ref_values = [str(candidate.get("ref")) for candidate in selected_direct]
    if direct_all:
        conclusion = direct_text.strip() or f"检索到 {len(direct_all)} 条满足全部硬条件的直接证据，已按论文、材料和实验条件整理。"
        if direct_ref_values and not any(f"[{ref}]" in conclusion for ref in direct_ref_values):
            conclusion = f"{conclusion} {' '.join(f'[{ref}]' for ref in direct_ref_values[:4])}"
        status = "found"
    else:
        conclusion = "未检索到同时满足全部硬条件的直接证据。下方相关证据每项只放宽一个条件，不能视为原问题的直接答案。"
        status = "not_found"
        direct_ref_values = []
    notes = related_notes or {}
    related_rows: list[dict[str, Any]] = []
    for candidate in selected_related:
        ref = str(candidate.get("ref"))
        missing = [str(item.get("label")) for item in candidate.get("missing_constraints", [])]
        related_rows.append({
            "refs": [ref],
            "summary": str(notes.get(ref) or candidate.get("title") or candidate.get("label") or "相关证据")[:260],
            "relaxed_constraints": missing,
            "article_title": candidate.get("article_title") or "未命名文章",
            "bundle_id": candidate.get("bundle_id") or "",
        })
    followups = list(_dedupe(suggested_followups, 3))
    if len(followups) < 2:
        followups = list(_dedupe([*followups, *_default_followups(analysis, bool(direct_all))], 3))
    return {
        "schema_version": "research-report-v1",
        "direct_conclusion": {
            "status": status,
            "text": conclusion,
            "refs": direct_ref_values,
        },
        "evidence_matrix": [_matrix_row(candidate, analysis) for candidate in selected_direct[:8]],
        "related_evidence": related_rows,
        "database_gaps": _coverage_gaps(analysis, direct_all),
        "suggested_followups": followups[:3],
    }


def report_references(report: dict[str, Any]) -> set[str]:
    refs = {str(value) for value in report.get("direct_conclusion", {}).get("refs", [])}
    for row in report.get("evidence_matrix", []):
        refs.update(str(value) for value in row.get("refs", []))
    for row in report.get("related_evidence", []):
        refs.update(str(value) for value in row.get("refs", []))
    return {ref for ref in refs if re.fullmatch(r"R\d+", ref)}


def report_markdown(report: dict[str, Any]) -> str:
    conclusion = report.get("direct_conclusion") or {}
    lines = ["## 直接结论", str(conclusion.get("text") or "未形成直接结论。").strip()]
    matrix = report.get("evidence_matrix") or []
    lines.extend(["", "## 证据矩阵"])
    if matrix:
        lines.append("| 材料 | 辐照/实验条件 | 性质 | 结果 | 论文证据 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for row in matrix:
            refs = " ".join(f"[{ref}]" for ref in row.get("refs", []))
            paper = str(row.get("article_title") or "未命名文章").replace("|", "／")
            page = f"PDF第{row.get('source_page')}页" if row.get("source_page") else "页码待核对"
            cells = [row.get("material"), row.get("conditions"), row.get("property"), row.get("result"), f"{paper}；{page} {refs}"]
            lines.append("| " + " | ".join(str(cell or "—").replace("|", "／") for cell in cells) + " |")
    else:
        lines.append("当前没有满足全部硬条件、可进入证据矩阵的记录。")
    lines.extend(["", "## 相关证据"])
    related = report.get("related_evidence") or []
    if related:
        for row in related:
            refs = " ".join(f"[{ref}]" for ref in row.get("refs", []))
            relaxed = "、".join(row.get("relaxed_constraints") or []) or "一个硬条件"
            lines.append(f"- {row.get('summary')}（放宽：{relaxed}）{refs}")
    else:
        lines.append("- 暂无只放宽一个硬条件即可成立的相邻证据。")
    lines.extend(["", "## 数据库缺口"])
    lines.extend(f"- {gap}" for gap in report.get("database_gaps") or ["尚未评估数据库缺口。"])
    lines.extend(["", "## 建议追问"])
    lines.extend(f"- {question}" for question in report.get("suggested_followups") or [])
    return "\n".join(lines)
