from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import fitz


EXPERIMENT_TYPE_RULES: dict[str, dict[str, Any]] = {
    "irradiation_experiment": {
        "label": "辐照/离子束/中子实验",
        "description": "关注粒子、能量、剂量、注量、通量、温度、辐照设施和辐照后表征。",
        "keywords": [
            ("irradiat", 4), ("ion beam", 4), ("ion-irradiated", 5), ("neutron", 4),
            ("implant", 3), ("dpa", 5), ("fluence", 4), ("flux", 3),
            ("radiation damage", 5), ("in situ tem", 4), ("krypton", 3), ("helium", 2),
        ],
    },
    "mechanical_testing": {
        "label": "力学性能实验",
        "description": "关注硬度、压痕、拉伸、压缩、蠕变、疲劳、断裂韧性和应力-应变条件。",
        "keywords": [
            ("hardness", 4), ("nanoindent", 5), ("indentation", 4), ("tensile", 4),
            ("compression", 3), ("stress-strain", 4), ("yield strength", 4),
            ("ultimate tensile", 4), ("creep", 4), ("fatigue", 4), ("fracture toughness", 4),
        ],
    },
    "microscopy_characterization": {
        "label": "显微结构/成分表征",
        "description": "关注 TEM、SEM、STEM、EBSD、APT、EDS、XRD 等表征方法和图表结果。",
        "keywords": [
            ("tem", 4), ("transmission electron", 5), ("sem", 3), ("scanning electron", 4),
            ("stem", 4), ("ebsd", 4), ("atom probe", 5), ("apt", 4),
            ("eds", 3), ("xrd", 4), ("diffraction", 3), ("microstructure", 3),
            ("dislocation loop", 4), ("void", 3), ("precipitate", 3),
        ],
    },
    "thermal_measurement": {
        "label": "热学/热分析实验",
        "description": "关注热导率、热膨胀、热容、DSC/TGA、相变温度和升温条件。",
        "keywords": [
            ("thermal conductivity", 5), ("thermal expansion", 5), ("heat capacity", 4),
            ("specific heat", 4), ("dsc", 5), ("differential scanning calorimetry", 5),
            ("tga", 4), ("thermogravimetric", 4), ("calorimetry", 4),
            ("melting temperature", 3), ("phase transition", 3),
        ],
    },
    "electrical_transport": {
        "label": "电学/输运实验",
        "description": "关注电阻率、电导率、阻抗、Hall、I-V、介电和温度/频率依赖测量。",
        "keywords": [
            ("resistivity", 5), ("electrical conductivity", 5), ("impedance", 5),
            ("hall effect", 4), ("i-v", 4), ("current-voltage", 4), ("dielectric", 4),
            ("seebeck", 4), ("carrier concentration", 3),
        ],
    },
    "spectroscopy": {
        "label": "光谱/能谱实验",
        "description": "关注 Raman、FTIR、XPS、XAS、EELS、PL 等光谱峰位、强度和测试条件。",
        "keywords": [
            ("raman", 5), ("ftir", 4), ("infrared spectroscopy", 4), ("xps", 5),
            ("x-ray photoelectron", 5), ("xas", 4), ("xanes", 4), ("eels", 4),
            ("photoluminescence", 4), ("spectroscopy", 3), ("binding energy", 3),
        ],
    },
    "electrochemical_testing": {
        "label": "电化学/腐蚀实验",
        "description": "关注腐蚀、电化学阻抗、循环伏安、极化曲线、电解液和电位/电流条件。",
        "keywords": [
            ("corrosion", 5), ("electrochemical", 5), ("cyclic voltammetry", 5),
            ("potentiodynamic", 5), ("polarization", 4), ("electrolyte", 4),
            ("galvanostatic", 4), ("eis", 4),
        ],
    },
    "synthesis_processing": {
        "label": "材料制备/工艺实验",
        "description": "关注熔炼、烧结、退火、轧制、热处理、沉积、样品制备和工艺参数。",
        "keywords": [
            ("arc-melt", 4), ("arc melt", 4), ("sinter", 4), ("anneal", 4),
            ("homogeniz", 4), ("solution anneal", 4), ("cold roll", 4), ("hot roll", 4),
            ("heat treatment", 4), ("deposition", 3), ("sputter", 4), ("additive manufacturing", 4),
            ("sample preparation", 3),
        ],
    },
    "magnetic_measurement": {
        "label": "磁学实验",
        "description": "关注磁化强度、磁滞回线、矫顽力、居里温度和外场/温度条件。",
        "keywords": [
            ("magnetization", 5), ("magnetic", 3), ("hysteresis", 4),
            ("coercivity", 4), ("curie temperature", 4), ("vsm", 4), ("squid", 4),
        ],
    },
}

NON_EXPERIMENTAL_KEYWORDS = (
    "review", "perspective", "first-principles", "density functional", "molecular dynamics",
    "phase-field simulation", "monte carlo", "machine learning potential", "calculation",
)


def _read_pdf_text(pdf_path: Path, max_pages: int = 5) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    with fitz.open(pdf_path) as document:
        for index in range(min(len(document), max_pages)):
            pages.append({"page": index + 1, "text": document[index].get_text("text")})
    return pages


def _contains(text: str, keyword: str) -> bool:
    keyword = keyword.casefold()
    if keyword.endswith("-") or keyword.endswith(" "):
        return keyword in text
    if re.search(r"[\W_]", keyword):
        return keyword in text
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(keyword)}", text))


def classify_experiment_types(paper: dict[str, Any], *,
                              pages: list[dict[str, Any]] | None = None,
                              pdf_path: Path | None = None,
                              max_pages: int = 5) -> dict[str, Any]:
    """Classify the experimental character of a paper without calling an LLM."""

    if pages is None and pdf_path:
        pages = _read_pdf_text(pdf_path, max_pages=max_pages)
    pages = pages or []
    title = str(paper.get("title") or "")
    body_text = "\n".join(str(page.get("text") or "") for page in pages)
    title_text = title.casefold()
    body_text = body_text.casefold()

    scored: list[dict[str, Any]] = []
    for type_id, rule in EXPERIMENT_TYPE_RULES.items():
        matches: list[dict[str, Any]] = []
        score = 0
        for keyword, weight in rule["keywords"]:
            title_match = _contains(title_text, keyword)
            body_match = _contains(body_text, keyword)
            if title_match:
                score += int(weight) * 3
                matches.append({"keyword": keyword, "weight": int(weight) * 3, "source": "title"})
            if body_match:
                score += int(weight)
                matches.append({"keyword": keyword, "weight": int(weight), "source": "pdf_text"})
        if score:
            scored.append({
                "type_id": type_id,
                "label": rule["label"],
                "description": rule["description"],
                "score": score,
                "matches": matches[:12],
            })
    scored.sort(key=lambda item: (-item["score"], item["type_id"]))

    non_experimental_matches = [
        keyword for keyword in NON_EXPERIMENTAL_KEYWORDS
        if keyword in title_text or keyword in body_text
    ]
    top_score = scored[0]["score"] if scored else 0
    confidence = min(0.98, round(top_score / max(top_score + 8, 1), 3)) if top_score else 0.0
    if not scored:
        primary = {
            "type_id": "non_experimental_or_unknown",
            "label": "非实验或实验类型不明确",
            "description": "未从标题和前几页文本中识别出稳定实验类型；应先人工检查或扩大文本范围。",
            "score": 0,
            "matches": [],
        }
    else:
        primary = scored[0]
    is_experimental = bool(scored) and not (
        primary["score"] < 5 and len(non_experimental_matches) >= 2
    )
    return {
        "primary_type": primary["type_id"],
        "primary_label": primary["label"],
        "is_experimental": is_experimental,
        "confidence": confidence,
        "types": scored,
        "non_experimental_signals": non_experimental_matches,
        "pages_used": [page.get("page") for page in pages],
    }


def extraction_focuses_for_profile(profile: dict[str, Any]) -> tuple[str, ...]:
    type_ids = {item.get("type_id") for item in profile.get("types", [])}
    foci = [
        (
            "Focus on experimental setup, sample/material identity, composition, preparation, "
            "control variables, environmental conditions, instrument settings, and tables. "
            f"Detected experiment profile: {profile.get('primary_label', 'unknown')}."
        ),
        (
            "Focus on experimental results, measured values, derived/calculated quantities, "
            "qualitative observations, comparisons, trends, uncertainties, and result tables."
        ),
    ]
    if "irradiation_experiment" in type_ids:
        foci.append("Focus on irradiation-specific conditions: particle species, energy, dose, dpa, fluence, flux, temperature, beam geometry, facility, and pre/post-irradiation comparisons.")
    if "mechanical_testing" in type_ids:
        foci.append("Focus on mechanical testing data: hardness, indentation depth, modulus, yield strength, tensile/compression conditions, strain rate, creep/fatigue/fracture metrics, and uncertainties.")
    if "thermal_measurement" in type_ids:
        foci.append("Focus on thermal measurement data: thermal conductivity, expansion, heat capacity, DSC/TGA transitions, heating/cooling rates, atmosphere, and temperature ranges.")
    if "electrical_transport" in type_ids:
        foci.append("Focus on electrical/transport data: resistivity, conductivity, impedance, Hall, I-V, carrier, dielectric, frequency, field, and temperature-dependent measurements.")
    if "microscopy_characterization" in type_ids or "spectroscopy" in type_ids:
        foci.append("Focus on characterization data: microscopy/spectroscopy method, instrument settings, phase/defect/feature sizes, compositions, peak positions, intensities, and qualitative observations.")
    if "synthesis_processing" in type_ids:
        foci.append("Focus on processing data: melting, sintering, annealing, rolling, deposition, heat treatment, times, temperatures, pressures, atmospheres, and sample preparation.")
    if "electrochemical_testing" in type_ids:
        foci.append("Focus on electrochemical/corrosion data: electrolyte, potential/current, scan rate, impedance, polarization, corrosion rate, and cycling conditions.")
    if "magnetic_measurement" in type_ids:
        foci.append("Focus on magnetic data: magnetization, coercivity, hysteresis, transition temperatures, applied field, and temperature conditions.")
    return tuple(foci)
