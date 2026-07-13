from __future__ import annotations

from typing import Any, Iterable


OBJECT_TAGS = (
    "聚变堆材料",
    "高熵/中熵合金",
    "钨与难熔合金",
    "其他材料",
)

METHOD_TAGS = (
    "辐照实验",
    "辐照模拟/计算",
    "显微/缺陷表征",
    "力学性能",
    "氢同位素行为",
)


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def navigation_tags(paper: dict[str, Any]) -> dict[str, list[str]]:
    """Return conservative, non-exclusive navigation tags for one paper.

    These tags are only used to narrow the article picker. They are deliberately
    derived from bibliographic metadata and never replace the experiment-type
    classification shown during extraction and review.
    """

    title = str(paper.get("title") or "").casefold()
    focus = str(paper.get("material_focus") or "").casefold()
    text = f"{title} {focus}"

    object_tags: list[str] = []
    if _contains_any(
        text,
        (
            "fusion", "plasma-facing", "plasma facing", "deuterium", "tritium",
            "hfir", "low activation", "low-activation", "tungsten", "w-based",
            "w based", "w-ta", "wta", "w refractory", "w-refractory",
        ),
    ):
        object_tags.append("聚变堆材料")
    if _contains_any(
        title,
        (
            "high entropy", "high-entropy", "medium entropy", "medium-entropy",
            "complex concentrated", "refractory high-entropy", "crconi", "nicofecr",
            "cocrfem", "wtacrv", "wtacrvhf",
        ),
    ) or focus in {"hea-rhea-cca", "rhea", "hea", "mea"}:
        object_tags.append("高熵/中熵合金")
    if _contains_any(
        title,
        (
            "tungsten", "refractory", "w-based", "w based", "w-ta", "wta",
            "nbtivzr", "ti-zr-hf-v-ta",
        ),
    ) or focus in {"w-refractory-alloys", "w", "rhea"}:
        object_tags.append("钨与难熔合金")
    if not object_tags:
        object_tags.append("其他材料")

    # Require an explicit computational method signal. In particular, the
    # phrase "simulated fusion environment" still describes a physical test.
    computation_only = _contains_any(
        title,
        (
            "molecular dynamics", "monte carlo", "density functional", " dft ",
            "first-principles", "first principles", "phy-x", "srim program",
            "srim simulation", "computer simulation", "numerical simulation",
            "multiscale simulation", "kinetic monte carlo",
        ),
    )
    irradiation_signal = _contains_any(
        title,
        (
            "irradiat", "radiation", "neutron", "ion beam", "ion-irradiated",
            "helium", "heavy ion", "kr ion", "proton", "amorphization",
        ),
    )

    method_tags: list[str] = []
    if computation_only:
        method_tags.append("辐照模拟/计算")
    elif irradiation_signal:
        method_tags.append("辐照实验")
    if _contains_any(
        title,
        (
            "microstruct", "defect", "dislocation", "void", "grain", "tem ",
            "microscop", "channeling", "rutherford", "field-ion", "field ion",
            "amorphization", "chemical order", "phase stability", "nanostructur",
        ),
    ):
        method_tags.append("显微/缺陷表征")
    if _contains_any(
        title,
        ("hardness", "hardening", "mechanical", "elastic", "strength", "nanoindent"),
    ):
        method_tags.append("力学性能")
    if _contains_any(
        title,
        ("deuterium", "tritium", "hydrogen", "retention", "desorption", "plasma"),
    ):
        method_tags.append("氢同位素行为")

    return {"object_tags": object_tags, "method_tags": method_tags}


def annotate_navigation_tags(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source in papers:
        paper = dict(source)
        tags = navigation_tags(paper)
        paper["navigation_object_tags"] = tags["object_tags"]
        paper["navigation_method_tags"] = tags["method_tags"]
        result.append(paper)
    return result
