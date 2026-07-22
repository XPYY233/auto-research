from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import fitz


EXPERIMENT_TYPE_RULES: dict[str, dict[str, Any]] = {
    "scattering_beam_measurement": {
        "label": "散射/束流测量实验",
        "description": "关注微分截面、散射角、背散射、束流能量、靶材和探测几何。",
        "keywords": [
            ("large-angle scattering", 7), ("large angle scattering", 7),
            ("light ion scattering", 6),
            ("differential cross section", 6), ("cross-section ratio", 6),
            ("rutherford scattering", 6), ("rutherford backscattering", 6),
            ("ion scattering", 5), ("backscattering", 5),
            ("scattering angle", 5), ("angular distribution", 5),
            ("scattering experiment", 5),
        ],
    },
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

DOCUMENT_MODE_LABELS = {
    "experimental": "实验研究",
    "mixed_experiment_computation": "实验与计算联合研究",
    "computational_modeling": "计算模拟/理论研究",
    "review_report": "综述/报告",
    "unknown": "研究类型不明确",
}

COMPUTATIONAL_MODE_KEYWORDS = (
    ("first-principles", 10), ("first principles", 10),
    ("density functional", 10), ("ab initio", 9),
    ("molecular dynamics", 9), ("phase-field", 9), ("phase field", 9),
    ("kinetic monte carlo", 9), ("monte carlo", 7),
    ("machine learning potential", 9), ("neural network potential", 9),
    ("machine-learned potential", 9), ("machine learned potential", 9),
    ("interatomic potential", 7),
    ("multiscale modeling", 8), ("modelling capabilities", 8),
    ("modeling capabilities", 8), ("numerical simulation", 8),
    ("computer simulation", 8), ("atomistic simulation", 8),
    ("phy-x", 10), ("srim programs", 9),
    ("calculated using", 4), ("theoretical calculation", 7),
)

EXPERIMENTAL_PROCEDURE_KEYWORDS = (
    ("experimental methods", 10), ("materials and methods", 9),
    ("experimental procedure", 9), ("experimental details", 9),
    ("experiments were performed", 8), ("experiment was performed", 8),
    ("was measured", 7), ("were measured", 7), ("we measured", 7),
    ("irradiation was performed", 8), ("were irradiated", 7),
    ("samples were prepared", 7), ("specimens were prepared", 7),
    ("samples were characterized", 7), ("specimens were characterized", 7),
    ("microscopy was performed", 6), ("nanoindentation was performed", 7),
    ("experimental results", 5), ("measured experimentally", 7),
)

EXPERIMENTAL_TITLE_KEYWORDS = (
    ("experimental", 8), ("measurement", 6), ("measured", 6),
    ("in situ", 4), ("in-situ", 4), ("irradiated", 5),
    ("irradiation effects", 5), ("characterization", 5),
    ("nanoindentation", 6), ("microscopy study", 5),
)

REVIEW_TITLE_KEYWORDS = (
    ("review", 10), ("perspective", 10), ("roadmap", 9),
    ("state of the art", 9), ("recent progress", 8), ("an overview", 9),
)


def _mode_score(text: str, weighted_keywords: tuple[tuple[str, int], ...], multiplier: int = 1) -> tuple[int, list[dict[str, Any]]]:
    matches: list[dict[str, Any]] = []
    score = 0
    for keyword, weight in weighted_keywords:
        if _contains(text, keyword):
            points = int(weight) * multiplier
            score += points
            matches.append({"keyword": keyword, "weight": points})
    return score, matches


def _document_mode(title_text: str, body_text: str, scored_types: list[dict[str, Any]]) -> dict[str, Any]:
    comp_title, comp_title_matches = _mode_score(title_text, COMPUTATIONAL_MODE_KEYWORDS, 3)
    comp_body, comp_body_matches = _mode_score(body_text, COMPUTATIONAL_MODE_KEYWORDS)
    exp_title, exp_title_matches = _mode_score(title_text, EXPERIMENTAL_TITLE_KEYWORDS, 3)
    exp_body, exp_body_matches = _mode_score(body_text, EXPERIMENTAL_PROCEDURE_KEYWORDS)
    review_title, review_matches = _mode_score(title_text, REVIEW_TITLE_KEYWORDS, 3)
    top_type_score = int(scored_types[0]["score"]) if scored_types else 0
    computational_score = comp_title + comp_body
    experimental_score = exp_title + exp_body

    if review_title >= 24 and experimental_score < 14:
        mode = "review_report"
    elif comp_title >= 24 and experimental_score < 14:
        mode = "computational_modeling"
    elif computational_score >= 16 and experimental_score < 8:
        mode = "computational_modeling"
    elif computational_score >= 18 and experimental_score >= 14:
        mode = "mixed_experiment_computation"
    elif experimental_score >= 8 or (top_type_score >= 8 and comp_title < 18):
        mode = "experimental"
    elif computational_score >= 8:
        mode = "computational_modeling"
    else:
        mode = "unknown"

    ranked = sorted(
        ((experimental_score, "experimental"), (computational_score, "computational_modeling"), (review_title, "review_report")),
        reverse=True,
    )
    lead, runner_up = ranked[0][0], ranked[1][0]
    confidence = 0.0 if lead <= 0 else min(0.99, round((lead + max(lead - runner_up, 0)) / (lead + 16), 3))
    return {
        "paper_mode": mode,
        "paper_mode_label": DOCUMENT_MODE_LABELS[mode],
        "mode_confidence": confidence,
        "mode_scores": {
            "experimental": experimental_score,
            "computational_modeling": computational_score,
            "review_report": review_title,
        },
        "mode_signals": {
            "experimental_title": exp_title_matches,
            "experimental_procedure": exp_body_matches,
            "computational_title": comp_title_matches,
            "computational_body": comp_body_matches,
            "review_title": review_matches,
        },
    }


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
    mode = _document_mode(title_text, body_text, scored)
    top_score = scored[0]["score"] if scored else 0
    focus_threshold = max(8, round(top_score * 0.35)) if top_score else 0
    selected_types = [
        item for item in scored
        if item["score"] >= focus_threshold
        or any(match.get("source") == "title" for match in item.get("matches", []))
    ]
    confidence = min(0.98, round(top_score / max(top_score + 8, 1), 3)) if top_score else 0.0
    if mode["paper_mode"] == "computational_modeling":
        primary = {
            "type_id": "computational_modeling",
            "label": DOCUMENT_MODE_LABELS["computational_modeling"],
            "description": "以第一性原理、分子动力学、数值模拟或其他计算模型为主要证据来源。",
            "score": mode["mode_scores"]["computational_modeling"],
            "matches": mode["mode_signals"]["computational_title"] + mode["mode_signals"]["computational_body"],
        }
        selected_types = []
    elif mode["paper_mode"] == "review_report":
        primary = {
            "type_id": "review_report",
            "label": DOCUMENT_MODE_LABELS["review_report"],
            "description": "以文献综合或报告总结为主，不把被引用论文的数据当作本文直接测量。",
            "score": mode["mode_scores"]["review_report"],
            "matches": mode["mode_signals"]["review_title"],
        }
        selected_types = []
    elif not scored:
        primary = {
            "type_id": "non_experimental_or_unknown",
            "label": "非实验或实验类型不明确",
            "description": "未从标题和前几页文本中识别出稳定实验类型；应先人工检查或扩大文本范围。",
            "score": 0,
            "matches": [],
        }
    else:
        primary = scored[0]
    is_experimental = mode["paper_mode"] in {"experimental", "mixed_experiment_computation"}
    return {
        "primary_type": primary["type_id"],
        "primary_label": primary["label"],
        "is_experimental": is_experimental,
        "confidence": confidence,
        "types": scored,
        "selected_types": selected_types,
        "focus_threshold": focus_threshold,
        "non_experimental_signals": non_experimental_matches,
        "pages_used": [page.get("page") for page in pages],
        **mode,
    }


def extraction_focuses_for_profile(profile: dict[str, Any]) -> tuple[str, ...]:
    mode = profile.get("paper_mode")
    if mode == "computational_modeling":
        return (
            "Focus on model identity, calculation method, input structure, boundary conditions, convergence settings, and explicitly reported numerical outputs.",
            "Classify outputs as calculated or derived, never as direct experimental measurements. Preserve the source equation, table, or exact text locator.",
            "Do not import measurements quoted from references as results of this paper, and do not infer curve points from figures.",
        )
    if mode == "review_report":
        return (
            "Focus on the review scope, comparison framework, and explicit synthesis conclusions.",
            "Do not publish values attributed to cited studies as direct measurements of this paper; retain them only as clearly attributed secondary evidence.",
        )
    selected = profile.get("selected_types") or profile.get("types", [])
    type_ids = {item.get("type_id") for item in selected}
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
    targeted: list[str] = []
    if "irradiation_experiment" in type_ids:
        targeted.append("irradiation conditions: particle species, energy, dose/dpa, fluence, flux, temperature, beam geometry, facility, and pre/post comparisons")
    if "mechanical_testing" in type_ids:
        targeted.append("mechanical data: hardness with uncertainty in the same value, indentation depth, modulus, strength, strain rate, creep/fatigue/fracture metrics")
    if "thermal_measurement" in type_ids:
        targeted.append("thermal data: conductivity, expansion, heat capacity, DSC/TGA transitions, rates, atmosphere, and temperature ranges")
    if "electrical_transport" in type_ids:
        targeted.append("electrical/transport data: resistivity, conductivity, impedance, Hall, I-V, carrier, dielectric, frequency, field, and temperature dependence")
    if "microscopy_characterization" in type_ids or "spectroscopy" in type_ids:
        targeted.append("characterization data: method/settings, phase, defect/feature sizes, composition, peaks, intensities, and qualitative observations")
    if "synthesis_processing" in type_ids:
        targeted.append("processing data: melting, sintering, annealing, rolling, deposition, heat treatment, times, temperatures, pressures, atmospheres, and sample preparation")
    if "electrochemical_testing" in type_ids:
        targeted.append("electrochemical/corrosion data: electrolyte, potential/current, scan rate, impedance, polarization, corrosion rate, and cycling")
    if "magnetic_measurement" in type_ids:
        targeted.append("magnetic data: magnetization, coercivity, hysteresis, transitions, applied field, and temperature")
    if "scattering_beam_measurement" in type_ids:
        targeted.append("scattering and beam data: projectile/target species, beam energy, scattering angle, detector geometry, differential cross section, and uncertainty")
    if targeted:
        foci.append("Targeted experiment-specific pass. Focus on " + "; ".join(targeted) + ".")
    if mode == "mixed_experiment_computation":
        foci.append("Mixed-method paper: explicitly separate measured results from calculated/derived outputs and preserve the evidence source for each.")
    return tuple(foci)
