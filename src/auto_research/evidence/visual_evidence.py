from __future__ import annotations

import difflib
import hashlib
import contextlib
import io
import json
import re
from pathlib import Path
from typing import Any

import fitz

from auto_research.paths import DATA_DIR, ROOT

from .db import EVIDENCE_DB_PATH, EvidenceDB, now
from .six_column import ELEMENT_SEARCH_ALIASES, ELEMENT_SYMBOLS

try:
    # Some publisher PDFs contain harmless structure-tree defects. Rendering is
    # still valid; suppress MuPDF's stderr chatter so CLI JSON remains parseable.
    fitz.TOOLS.mupdf_display_errors(False)
    fitz.TOOLS.mupdf_display_warnings(False)
except AttributeError:
    pass


VISUAL_ASSET_DIR = DATA_DIR / "evidence" / "visual_assets"
VISUAL_TYPES = {"table", "figure"}
JSON_FIELDS = {
    "physical_quantities": "physical_quantities_json",
    "variables": "variables_json",
    "materials": "materials_json",
    "tags": "tags_json",
}
VISUAL_EDITABLE_FIELDS = (
    "display_name", "physical_quantities", "variables", "materials", "conditions_text",
    "methods_text", "context_explanation", "tags",
)
VISUAL_REVIEW_ACTIONS = {"automatic", "confirmation", "correction", "ambiguous", "rejected"}


def _caption_body_is_reference(body: str) -> bool:
    """Reject prose references such as 'Table 1 lists...' as visual titles."""

    return not body or bool(re.match(
        r"^(?:[()\[\],;:]|shows?\b|lists?\b|summari[sz]es?\b|is\b|are\b|presents?\b|"
        r"reports?\b|depicts?\b|illustrates?\b|demonstrates?\b)",
        body,
        re.IGNORECASE,
    ))


TARGET_DOI = "10.1016/j.jnucmat.2018.08.031"
TARGET_VISUAL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "asset_type": "table", "number": 1, "page": 2, "bbox": [28, 630, 567, 757],
        "caption": "Nominal (measured) material compositions (at%).",
        "physical_quantities": ["名义元素组成", "实测元素组成", "原子百分比"],
        "variables": {"rows": "材料及名义/实测状态", "columns": "Fe, Cr, Ni, Mn, Co, Al, Mo, Si, C, N"},
        "materials": ["Al0.3CoCrFeNi", "CoCrFeMnNi", "316H"],
        "conditions": "均匀化或固溶处理后的初始材料；成分单位为 at%。",
        "methods": "HEA成分由EDS测量；括号内为实测值。",
        "context": "比较三种材料的名义与实测元素组成；316H同时列出主要合金元素，并以脚注补充微量元素。",
        "tags": ["材料成分", "名义成分", "实测成分", "EDS", "at%", "HEA", "不锈钢"],
        "source_context": "Table 1 shows the nominal and measured compositions of these materials. The measured HEA compositions by EDS were close to the design values.",
    },
    {
        "asset_type": "table", "number": 2, "page": 4, "bbox": [28, 61, 291, 139],
        "caption": "Microstructural parameters of as-received specimens.",
        "physical_quantities": ["晶粒尺寸", "夹杂物体积分数", "位错密度"],
        "variables": {"rows": "微观结构参数", "columns": "Al0.3CoCrFeNi, CoCrFeMnNi, 316H"},
        "materials": ["Al0.3CoCrFeNi", "CoCrFeMnNi", "316H"],
        "conditions": "未辐照、初始态试样。",
        "methods": "EBSD晶粒尺寸、BSE夹杂物体积分数、TEM位错密度。",
        "context": "集中比较三种材料在辐照前的晶粒尺寸、夹杂物体积分数和位错密度，用作后续辐照响应对照。",
        "tags": ["初始微观结构", "晶粒尺寸", "夹杂物", "位错密度", "EBSD", "BSE", "TEM"],
        "source_context": "The grain size in Table 2 was measured by EBSD. The corresponding inclusion volume fractions and the dislocation densities of the as-received materials are also reported.",
    },
    {
        "asset_type": "table", "number": 3, "page": 5, "bbox": [306, 648, 568, 752],
        "caption": "Nanoindentation measurements before and after ion irradiation.",
        "physical_quantities": ["辐照前硬度", "辐照后硬度", "实测硬化增量", "计算硬化增量"],
        "variables": {"rows": "H0, Hirr, ΔH, ΔHc", "columns": "Al0.3CoCrFeNi, CoCrFeMnNi, 316H"},
        "materials": ["Al0.3CoCrFeNi", "CoCrFeMnNi", "316H"],
        "conditions": "1 MeV Kr离子，300 °C，1 dpa；固定压入深度100 nm。",
        "methods": "Hysitron TI 950纳米压痕；ΔHc由位错环参数和Orowan硬化模型计算。",
        "context": "并列给出三种材料辐照前后硬度、实测硬化增量以及由微观结构模型计算的硬化增量。",
        "tags": ["纳米硬度", "辐照硬化", "纳米压痕", "Kr离子", "300°C", "1 dpa", "Orowan模型"],
        "source_context": "The hardness values are shown in Table 3. All materials increased by about 1 GPa after irradiation; calculated hardening is compared with measured hardening.",
    },
    {
        "asset_type": "table", "number": 4, "page": 8, "bbox": [28, 665, 291, 758],
        "caption": "The parameters δ, ΔHmix, ΔSmix and Ω for HEAs and 316H.",
        "physical_quantities": ["原子尺寸失配δ", "混合焓ΔHmix", "混合熵ΔSmix", "熔化温度Tm", "热力学参数Ω"],
        "variables": {"rows": "材料", "columns": "δ, ΔHmix, ΔSmix, Tm, Ω"},
        "materials": ["Al0.3CoCrFeNi", "CoCrFeMnNi", "316H"],
        "conditions": "根据材料组成、参考原子半径和二元混合焓计算；316H只考虑Fe、Cr、Mn、Ni、Mo和Si。",
        "methods": "热力学与原子尺寸参数计算。",
        "context": "比较三种材料的晶格失配和固溶体稳定性参数，用于讨论Al0.3CoCrFeNi较低的相稳定性。",
        "tags": ["热力学参数", "晶格失配", "混合焓", "混合熵", "熔点", "Ω", "计算量"],
        "source_context": "The values of δ, ΔHmix, ΔSmix, Tm and Ω are given in Table 4 and used to discuss phase stability.",
    },
    {
        "asset_type": "figure", "number": 1, "page": 2, "bbox": [296, 389, 559, 624],
        "caption": "SRIM calculation of the displacement damage and distributions of 1 MeV krypton ions in 316H, CoCrFeMnNi and Al0.3CoCrFeNi.",
        "physical_quantities": ["空位分布", "Kr离子分布", "损伤深度剖面"],
        "variables": {"x": "Depth (nm)", "y_left": "Vacancies·ion⁻¹·nm⁻¹", "y_right": "Kr ion distribution (nm⁻¹)"},
        "materials": ["Al0.3CoCrFeNi", "CoCrFeMnNi", "316H"],
        "conditions": "1 MeV Kr离子；SRIM计算。", "methods": "SRIM模拟",
        "context": "展示位移损伤和Kr离子注入分布随深度的变化，支持约500 nm损伤层深度的讨论。",
        "tags": ["SRIM", "损伤深度", "空位", "Kr分布", "离子注入", "深度剖面"],
        "source_context": "The SRIM calculated damage and implantation profile of 1 MeV Kr ions are similar in the HEAs and 316H.",
    },
    {
        "asset_type": "figure", "number": 2, "page": 3, "bbox": [38, 225, 568, 750],
        "caption": "The EBSD maps, backscattering electron SEM images, and weak-beam dark field TEM images of the as-received Al0.3CoCrFeNi, CoCrFeMnNi and 316H.",
        "physical_quantities": ["晶粒形貌", "夹杂物分布", "初始位错结构"],
        "variables": {"columns": "三种材料", "rows": "EBSD, BSE-SEM, WBDF-TEM"},
        "materials": ["Al0.3CoCrFeNi", "CoCrFeMnNi", "316H"],
        "conditions": "未辐照初始态。", "methods": "EBSD、背散射电子SEM、WBDF-TEM",
        "context": "以三种成像方法并列展示三种材料辐照前的晶粒、夹杂物和位错背景。",
        "tags": ["EBSD", "SEM", "TEM", "初始组织", "晶粒", "夹杂物", "位错"],
        "source_context": "Figure 2 is used to compare the as-received microstructures and inclusions before irradiation.",
    },
    {
        "asset_type": "figure", "number": 3, "page": 4, "bbox": [28, 363, 290, 750],
        "caption": "EDS spectrum of the inclusions in Al0.3CoCrFeNi, CoCrFeMnNi and 316H.",
        "physical_quantities": ["EDS能谱强度", "夹杂物元素组成"],
        "variables": {"x": "Energy (keV)", "y": "Intensity"},
        "materials": ["Al0.3CoCrFeNi", "CoCrFeMnNi", "316H"],
        "conditions": "未辐照材料中的夹杂物。", "methods": "能量色散X射线谱 EDS",
        "context": "三幅EDS谱分别用于识别三种材料中夹杂物的主要元素组成。",
        "tags": ["EDS", "能谱", "夹杂物", "元素组成", "Al氧化物", "Cr富集", "MnS"],
        "source_context": "The inclusions were characterized with EDS: alumina oxides in Al0.3CoCrFeNi, Cr-rich and Mn-rich oxides in CoCrFeMnNi, and MnS precipitates in 316H.",
    },
    {
        "asset_type": "figure", "number": 4, "page": 5, "bbox": [38, 60, 568, 594],
        "caption": "The 110 zone diffraction patterns and corresponding intensity profiles along 200 g directions before and after irradiation at 300 °C to 1 dpa.",
        "physical_quantities": ["电子衍射图样", "归一化衍射强度", "倒易空间间距", "有序化信号"],
        "variables": {"x": "Inverse d spacing (nm⁻¹)", "y": "Normalized intensity + offset"},
        "materials": ["Al0.3CoCrFeNi", "CoCrFeMnNi", "316H"],
        "conditions": "辐照前与1 MeV Kr、300 °C、1 dpa辐照后对比。", "methods": "TEM电子衍射与强度剖面",
        "context": "比较辐照前后衍射斑点和强度剖面；Al0.3CoCrFeNi辐照后出现fcc禁戒{100}反射痕迹。",
        "tags": ["电子衍射", "强度剖面", "有序化", "相稳定性", "1 dpa", "300°C"],
        "source_context": "A trace of prohibited {100} reflections was observed in irradiated Al0.3CoCrFeNi, while CoCrFeMnNi and 316H retained matrix fcc structures.",
    },
    {
        "asset_type": "figure", "number": 5, "page": 6, "bbox": [34, 60, 562, 365],
        "caption": "WBDF and BF TEM micrographs of 316H irradiated with 1 MeV Kr ions at 300 °C.",
        "physical_quantities": ["辐照诱导位错环形貌", "位错环随剂量演化"],
        "variables": {"columns": "0.01, 0.06, 0.1, 0.5, 1 dpa", "rows": "WBDF, BF"},
        "materials": ["316H"], "conditions": "1 MeV Kr；300 °C；0.01–1 dpa。", "methods": "原位WBDF/BF TEM",
        "context": "展示316H中位错环从0.01 dpa到1 dpa的连续形貌演化。",
        "tags": ["316H", "位错环", "原位TEM", "WBDF", "BF", "剂量演化", "Kr离子"],
        "source_context": "The irradiated microstructures consist of a high density of dislocation loops; no void was observed.",
    },
    {
        "asset_type": "figure", "number": 6, "page": 6, "bbox": [28, 441, 568, 750],
        "caption": "WBDF and BF TEM micrographs of CoCrFeMnNi irradiated with 1 MeV Kr ions at 300 °C.",
        "physical_quantities": ["辐照诱导位错环形貌", "位错环随剂量演化"],
        "variables": {"columns": "0.01, 0.06, 0.1, 0.5, 1 dpa", "rows": "WBDF, BF"},
        "materials": ["CoCrFeMnNi"], "conditions": "1 MeV Kr；300 °C；0.01–1 dpa。", "methods": "原位WBDF/BF TEM",
        "context": "展示CoCrFeMnNi中位错环从0.01 dpa到1 dpa的连续形貌演化。",
        "tags": ["CoCrFeMnNi", "位错环", "原位TEM", "WBDF", "BF", "剂量演化", "Kr离子"],
        "source_context": "The irradiated microstructures consist of a high density of dislocation loops; no void was observed.",
    },
    {
        "asset_type": "figure", "number": 7, "page": 7, "bbox": [38, 60, 568, 369],
        "caption": "WBDF and BF TEM micrographs of Al0.3CoCrFeNi irradiated with 1 MeV Kr ions at 300 °C.",
        "physical_quantities": ["辐照诱导位错环形貌", "位错环随剂量演化"],
        "variables": {"columns": "0.01, 0.06, 0.1, 0.5, 1 dpa", "rows": "WBDF, BF"},
        "materials": ["Al0.3CoCrFeNi"], "conditions": "1 MeV Kr；300 °C；0.01–1 dpa。", "methods": "原位WBDF/BF TEM",
        "context": "展示Al0.3CoCrFeNi中位错环从0.01 dpa到1 dpa的连续形貌演化。",
        "tags": ["Al0.3CoCrFeNi", "位错环", "原位TEM", "WBDF", "BF", "剂量演化", "Kr离子"],
        "source_context": "The irradiated microstructures consist of a high density of dislocation loops; no void was observed.",
    },
    {
        "asset_type": "figure", "number": 8, "page": 7, "bbox": [38, 376, 568, 566],
        "caption": "The density and size of irradiation-induced dislocation loops in 316H, CoCrFeMnNi and Al0.3CoCrFeNi irradiated with 1 MeV Kr at 300 °C.",
        "physical_quantities": ["位错环密度", "位错环尺寸"],
        "variables": {"x": "Dose (dpa)", "y_left": "Density (m⁻³)", "y_right": "Size (nm)"},
        "materials": ["Al0.3CoCrFeNi", "CoCrFeMnNi", "316H"],
        "conditions": "1 MeV Kr；300 °C；0.01–1 dpa。", "methods": "原位TEM统计；密度和尺寸带误差棒",
        "context": "比较三种材料的位错环密度和尺寸随剂量变化；正文指出密度约0.1 dpa后逐渐饱和，多数环保持数纳米。",
        "tags": ["位错环密度", "位错环尺寸", "剂量", "误差棒", "TEM", "辐照损伤"],
        "source_context": "Loop density increased with dose until about 0.1 dpa and gradually saturated; most loops remained a few nanometers up to 1 dpa.",
    },
    {
        "asset_type": "figure", "number": 9, "page": 8, "bbox": [28, 60, 568, 248],
        "caption": "Bright-field TEM images of Al0.3CoCrFeNi, CoCrFeMnNi and 316H irradiated with 1 MeV Kr at 300 °C to 1 dpa.",
        "physical_quantities": ["辐照后位错环形貌", "位错环串状结构"],
        "variables": {"columns": "三种材料", "insets": "衍射条件"},
        "materials": ["Al0.3CoCrFeNi", "CoCrFeMnNi", "316H"],
        "conditions": "1 MeV Kr；300 °C；1 dpa；较厚箔区。", "methods": "明场TEM",
        "context": "比较三种材料在1 dpa后的位错环和排列结构，正文讨论了小位错环串状排列。",
        "tags": ["明场TEM", "位错环", "串状结构", "1 dpa", "300°C", "较厚箔区"],
        "source_context": "All three materials exhibited a similar microstructure consisting of a high density of small dislocation loops and aligned structures.",
    },
    {
        "asset_type": "figure", "number": 10, "page": 9, "bbox": [38, 60, 300, 291],
        "caption": "Representative nanoindentation load-displacement curves of CoCrFeMnNi before and after 1 MeV Kr irradiation at 300 °C to 1 dpa.",
        "physical_quantities": ["纳米压痕载荷", "压入位移", "辐照硬化响应"],
        "variables": {"x": "Displacement (nm)", "y": "Force (µN)"},
        "materials": ["CoCrFeMnNi"],
        "conditions": "未辐照与1 MeV Kr、300 °C、1 dpa辐照后对比。", "methods": "Berkovich纳米压痕，固定最大位移100 nm",
        "context": "代表性载荷-位移曲线用于展示CoCrFeMnNi辐照后的力学响应变化；不自动读取曲线点。",
        "tags": ["载荷-位移曲线", "纳米压痕", "CoCrFeMnNi", "辐照硬化", "1 dpa", "300°C"],
        "source_context": "Figure 10 shows representative load-displacement curves before and after irradiation; numerical hardness values are reported in Table 3.",
    },
)


def _asset_label(asset_type: str, number: int) -> str:
    return f"{'Table' if asset_type == 'table' else 'Figure'} {number}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _render_crop(pdf_path: Path, page_number: int, bbox: list[float], output_path: Path) -> str:
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_number - 1]
        clip = fitz.Rect(*bbox) & page.rect
        if clip.is_empty or clip.width < 20 or clip.height < 20:
            raise ValueError(f"invalid visual crop on page {page_number}: {bbox}")
        pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=clip, alpha=False)
        payload = pix.tobytes("png")
    finally:
        doc.close()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _target_specs(paper: dict[str, Any]) -> list[dict[str, Any]] | None:
    if str(paper.get("doi") or "").lower() != TARGET_DOI:
        return None
    return [dict(spec) for spec in TARGET_VISUAL_SPECS]


def _short_visual_name(caption: str, asset_type: str) -> str:
    """Return a compact Chinese fallback; DeepSeek may refine it from context later."""

    text = caption.casefold()
    rules = (
        (("short-range order", "order to disorder"), "化学短程有序与转变温度"),
        (("atomic snapshots", "grain boundary structures"), "辐照级联后的晶界结构"),
        (("stopping and range", "srim"), "SRIM 辐照损伤深度分布"),
        (("nanoindentation hardness",), "纳米压痕硬度对比"),
        (("isotopic abundance", "isotope"), "辐照钨同位素丰度对比"),
        (("grain size",), "平均晶粒尺寸对比"),
        (("bubble volume density",), "He 气泡体密度对比"),
        (("cluster", "msm"), "MSM 团簇定量参数"),
        (("dislocation loop",), "位错环演化"),
        (("void", "swelling"), "空洞与肿胀特征"),
        (("composition", "elemental"), "元素组成对比"),
        (("diffraction", "saed"), "衍射与相结构"),
        (("spectrum", "spectra"), "光谱与能谱特征"),
        (("hardness",), "硬度对比"),
        (("density",), "密度对比"),
        (("size", "diameter", "radius"), "尺寸分布"),
    )
    for terms, name in rules:
        if any(term in text for term in terms):
            return name
    return "实验数据表" if asset_type == "table" else "实验结果图"


def _infer_visual_metadata(caption: str, asset_type: str) -> dict[str, Any]:
    """Create conservative Chinese search metadata without reading curve values."""

    text = caption.casefold()
    rules = (
        (("hardness", "nanoindent"), "硬度/压痕", "纳米压痕"),
        (("void", "swelling"), "空洞/肿胀", "TEM"),
        (("dislocation", "loop"), "位错/位错环", "TEM"),
        (("grain", "microstructure"), "晶粒/微观结构", "显微表征"),
        (("composition", "elemental", "concentration"), "元素组成", "EDS/EDX"),
        (("diffraction", "saed"), "衍射/相结构", "衍射分析"),
        (("spectrum", "spectra", "spectroscopy"), "光谱/能谱", "光谱分析"),
        (("damage", "dpa", "irradiat"), "辐照损伤", "辐照实验"),
        (("energy", "formation energy", "binding energy"), "能量", "计算/能量分析"),
        (("diffusion", "mean square displacement"), "扩散", "扩散分析"),
        (("density",), "密度", "统计分析"),
        (("size", "diameter", "radius"), "尺寸", "统计分析"),
    )
    quantities: list[str] = []
    methods: list[str] = []
    for terms, quantity, method in rules:
        if any(term in text for term in terms):
            if quantity not in quantities:
                quantities.append(quantity)
            if method not in methods:
                methods.append(method)
    variable_match = re.search(r"as (?:a )?function of ([^.;]+)", caption, re.I)
    variables = {"independent": variable_match.group(1).strip()} if variable_match else {}
    # Tags describe the actual evidence object.  Do not add generic material or
    # method placeholders when the caption does not support them.
    tags = list(dict.fromkeys([*quantities, *methods]))
    return {
        "display_name": _short_visual_name(caption, asset_type),
        "physical_quantities": quantities,
        "variables": variables,
        "methods": "、".join(methods),
        "tags": tags,
    }


_FIGURE_CAPTION_RE = re.compile(
    # Reject inline panel references such as Fig. 9a, Fig. 9(a), or Figure 9[b].
    # A real whole-figure caption continues after the figure number delimiter.
    r"^(?:fig(?:ure)?\.?)\s*(\d+)(?!\s*(?:[\[(]|[a-z](?:\b|[.)])))\s*(?:[.|:]\s*)?(.*)$",
    re.I,
)
_TABLE_CAPTION_RE = re.compile(r"^table\s*(\d+)\s*(?:[.|:]\s*)?(.*)$", re.I)


def _caption_context(blocks: list[tuple[Any, ...]], index: int) -> str:
    selected = blocks[max(0, index - 2):min(len(blocks), index + 3)]
    return " ".join(" ".join(str(block[4]).split()) for block in selected)[:4000]


def _complete_caption(
    blocks: list[tuple[Any, ...]], index: int, body: str, *, stop_y: float | None = None
) -> tuple[str, fitz.Rect]:
    """Join publisher captions split into one text block per rendered line."""

    parts = [body.strip(" |")] if body.strip(" |") else []
    rect = fitz.Rect(*blocks[index][:4])
    previous = rect
    for following in blocks[index + 1:index + 9]:
        text = " ".join(str(following[4]).split()).strip()
        next_rect = fitz.Rect(*following[:4])
        if not text:
            break
        if stop_y is not None and next_rect.y0 >= stop_y - 3:
            break
        if _FIGURE_CAPTION_RE.match(text) or _TABLE_CAPTION_RE.match(text):
            break
        # PDF block order may wrap from a bottom caption back to the running
        # header. Caption continuation lines must move down the page.
        if next_rect.y0 < previous.y0 - 2:
            break
        if next_rect.y0 - previous.y1 > 14 or abs(next_rect.x0 - rect.x0) > 18:
            break
        if _x_overlap(next_rect, rect) < 0.35:
            break
        parts.append(text)
        rect |= next_rect
        previous = next_rect
    return " ".join(parts)[:2400], rect


def _x_overlap(left: fitz.Rect, right: fitz.Rect) -> float:
    width = max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0))
    return width / max(1.0, min(left.width, right.width))


def _connected_table_rules(
    drawings: list[fitz.Rect], caption_rect: fitz.Rect, page_height: float
) -> list[fitz.Rect]:
    """Return the continuous horizontal-rule group immediately below a table title."""

    candidates = sorted(
        (
            rect for rect in drawings
            if rect.y0 >= caption_rect.y1 - 2
            and rect.y0 <= page_height - 24
            and rect.width > 40
            and rect.height < 10
        ),
        key=lambda rect: (rect.y0, rect.x0),
    )
    if not candidates or candidates[0].y0 - caption_rect.y1 > 90:
        return []
    group: list[fitz.Rect] = []
    last_y = candidates[0].y0
    for rect in candidates:
        if rect.y0 - last_y > 52:
            break
        group.append(rect)
        last_y = max(last_y, rect.y0)
    return group


def _nearest_detected_table(
    table_rects: list[fitz.Rect], caption_rect: fitz.Rect
) -> fitz.Rect | None:
    candidates = [
        rect for rect in table_rects
        if rect.y0 >= caption_rect.y0 - 8
        and rect.y0 - caption_rect.y1 <= 140
        and rect.y1 >= caption_rect.y1 + 18
        and _x_overlap(rect, caption_rect) >= 0.2
    ]
    return min(candidates, key=lambda rect: abs(rect.y0 - caption_rect.y1)) if candidates else None


def _detected_table_rects(page: fitz.Page) -> list[fitz.Rect]:
    """Use PyMuPDF's table detector when available, without polluting CLI JSON."""

    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return [fitz.Rect(table.bbox) for table in page.find_tables().tables]
    except Exception:
        return []


def _previous_page_figure_crop(
    doc: fitz.Document, page_index: int, caption_rect: fitz.Rect
) -> tuple[int, int, fitz.Rect] | None:
    """Handle a full-page raster figure whose caption starts the next page."""

    if page_index <= 0 or caption_rect.y0 > doc[page_index].rect.height * 0.22:
        return None
    previous = doc[page_index - 1]
    image_rects = [
        rect
        for image in previous.get_images(full=True)
        for rect in previous.get_image_rects(image[0])
    ]
    text_blocks = [
        fitz.Rect(*block[:4])
        for block in previous.get_text("blocks")
        if len(" ".join(str(block[4]).split())) > 4 and float(block[3]) < previous.rect.height - 45
    ]
    if not text_blocks:
        return None
    text_bottom = max(rect.y1 for rect in text_blocks)
    if text_bottom >= previous.rect.height * 0.72:
        return None
    figure_images = [
        rect for rect in image_rects
        if rect.get_area() >= previous.rect.get_area() * 0.08
        and rect.y0 >= text_bottom - 24
    ]
    if figure_images:
        image_rect = max(figure_images, key=lambda rect: rect.get_area())
        crop = fitz.Rect(
            image_rect.x0 - 10, image_rect.y0 - 10,
            image_rect.x1 + 10, image_rect.y1 + 10,
        ) & previous.rect
        return page_index, page_index + 1, crop
    if not any(rect.get_area() >= previous.rect.get_area() * 0.65 for rect in image_rects):
        return None
    x0 = min(rect.x0 for rect in text_blocks)
    x1 = max(rect.x1 for rect in text_blocks)
    crop = fitz.Rect(
        max(24, x0), min(previous.rect.height - 160, text_bottom + 12),
        min(previous.rect.width - 24, x1), previous.rect.height - 70,
    ) & previous.rect
    return page_index, page_index + 1, crop


def _visual_index_kinds(blocks: list[tuple[Any, ...]]) -> set[str]:
    """Identify list-of-figures/tables pages so references are not saved as assets."""

    text = "\n".join(str(block[4]) for block in blocks)
    dotted_leader = bool(re.search(r"\.{4,}\s*\d+", text))
    kinds: set[str] = set()
    for kind, pattern in (
        ("table", r"\bTable\s+\d+[A-Za-z]?\s*[.:]"),
        ("figure", r"\b(?:Fig(?:ure)?\.?)[ \t]+\d+[A-Za-z]?\s*[.:]"),
    ):
        references = re.findall(pattern, text, flags=re.IGNORECASE)
        heading = bool(re.search(rf"\bList\s+of\s+{kind}s\b", text, flags=re.IGNORECASE))
        if heading or (dotted_leader and len(references) >= 3):
            kinds.add(kind)
    return kinds


def _generic_specs(pdf_path: Path) -> list[dict[str, Any]]:
    """Find caption-led visual regions without interpreting curve values."""

    specs: list[dict[str, Any]] = []
    doc = fitz.open(pdf_path)
    try:
        for page_index, page in enumerate(doc):
            blocks = [b for b in page.get_text("blocks") if str(b[4]).strip()]
            index_kinds = _visual_index_kinds(blocks)
            image_rects: list[fitz.Rect] = []
            for image in page.get_images(full=True):
                for rect in page.get_image_rects(image[0]):
                    if rect.width > 60 and rect.height > 40:
                        image_rects.append(rect)
            drawings = [drawing["rect"] for drawing in page.get_drawings()]
            detected_tables = _detected_table_rects(page)
            for block_index, block in enumerate(blocks):
                text = " ".join(str(block[4]).split())
                match = _FIGURE_CAPTION_RE.match(text)
                if match and "figure" not in index_kinds:
                    caption_body = match.group(2).strip(" |")
                    if _caption_body_is_reference(caption_body):
                        continue
                    caption, caption_rect = _complete_caption(blocks, block_index, caption_body)
                    above = [
                        rect for rect in image_rects
                        if rect.y1 <= caption_rect.y0 + 12
                        and caption_rect.y0 - rect.y1 <= 180
                        and _x_overlap(rect, caption_rect) >= 0.25
                    ]
                    below = [
                        rect for rect in image_rects
                        if rect.y0 >= caption_rect.y1 - 12
                        and rect.y0 - caption_rect.y1 <= 180
                        and _x_overlap(rect, caption_rect) >= 0.25
                    ]
                    candidates = above or below
                    actual_page = page_index + 1
                    page_end = actual_page
                    if candidates:
                        image_rect = min(
                            candidates,
                            key=lambda rect: (
                                min(abs(caption_rect.y0 - rect.y1), abs(rect.y0 - caption_rect.y1)),
                                -rect.get_area(),
                            ),
                        )
                        crop = image_rect | caption_rect
                        crop = fitz.Rect(crop.x0 - 8, crop.y0 - 7, crop.x1 + 8, crop.y1 + 7) & page.rect
                    else:
                        previous_crop = _previous_page_figure_crop(doc, page_index, caption_rect)
                        if previous_crop:
                            actual_page, page_end, crop = previous_crop
                        else:
                            nearby_drawings = [
                                rect for rect in drawings
                                if rect.get_area() > 100
                                and (
                                    (rect.y1 <= caption_rect.y0 + 12 and caption_rect.y0 - rect.y1 <= 300)
                                    or (rect.y0 >= caption_rect.y1 - 12 and rect.y0 - caption_rect.y1 <= 300)
                                )
                            ]
                            if not nearby_drawings:
                                continue
                            crop = nearby_drawings[0]
                            for rect in nearby_drawings[1:]:
                                crop |= rect
                            crop |= caption_rect
                            crop = fitz.Rect(crop.x0 - 10, crop.y0 - 10, crop.x1 + 10, crop.y1 + 10) & page.rect
                    inferred = _infer_visual_metadata(caption, "figure")
                    specs.append({
                        "asset_type": "figure", "number": int(match.group(1)), "page": actual_page,
                        "page_end": page_end,
                        "bbox": list(crop), "caption": caption,
                        "display_name": inferred["display_name"],
                        "physical_quantities": inferred["physical_quantities"], "variables": inferred["variables"], "materials": [],
                        "conditions": "", "methods": inferred["methods"], "context": "",
                        "tags": inferred["tags"], "source_context": _caption_context(blocks, block_index),
                    })
                    continue
                match = _TABLE_CAPTION_RE.match(text)
                if not match or "table" in index_kinds:
                    continue
                caption_body = match.group(2).strip(" |")
                if _caption_body_is_reference(caption_body):
                    continue
                initial_caption_rect = fitz.Rect(*block[:4])
                detected_table = _nearest_detected_table(detected_tables, initial_caption_rect)
                caption, caption_rect = _complete_caption(
                    blocks, block_index, caption_body,
                    stop_y=detected_table.y0 if detected_table is not None else None,
                )
                horizontal = _connected_table_rules(drawings, caption_rect, page.rect.height)
                if detected_table is not None:
                    crop = fitz.Rect(
                        min(caption_rect.x0, detected_table.x0) - 6,
                        caption_rect.y0 - 6,
                        max(caption_rect.x1, detected_table.x1) + 6,
                        min(detected_table.y1 + 5, page.rect.height - 28),
                    ) & page.rect
                elif horizontal:
                    bottom = max(rect.y1 for rect in horizontal)
                    crop = fitz.Rect(
                        min(caption_rect.x0, *(rect.x0 for rect in horizontal)) - 6,
                        caption_rect.y0 - 6,
                        max(caption_rect.x1, *(rect.x1 for rect in horizontal)) + 6,
                        min(bottom + 4, page.rect.height - 28),
                    ) & page.rect
                else:
                    full_width = caption_rect.width >= page.rect.width * 0.45
                    x0 = 28 if full_width else max(24, caption_rect.x0 - 8)
                    x1 = page.rect.width - 28 if full_width else min(page.rect.width - 24, caption_rect.x1 + 30)
                    following_images = [rect for rect in image_rects if rect.y0 >= caption_rect.y1]
                    bottom = min((rect.y0 - 8 for rect in following_images), default=caption_rect.y1 + 220)
                    crop = fitz.Rect(x0, max(30, caption_rect.y0 - 6), x1, min(page.rect.height - 28, bottom))
                inferred = _infer_visual_metadata(caption, "table")
                specs.append({
                    "asset_type": "table", "number": int(match.group(1)), "page": page_index + 1,
                    "bbox": list(crop), "caption": caption,
                    "display_name": inferred["display_name"],
                    "physical_quantities": inferred["physical_quantities"], "variables": inferred["variables"], "materials": [],
                    "conditions": "", "methods": inferred["methods"], "context": "",
                    "tags": inferred["tags"], "source_context": _caption_context(blocks, block_index),
                })
    finally:
        doc.close()
    unique: dict[tuple[str, int], dict[str, Any]] = {}
    for spec in specs:
        unique.setdefault((spec["asset_type"], int(spec["number"])), spec)
    return list(unique.values())


def _resolve_image_path(stored_path: str) -> Path:
    path = Path(stored_path)
    return path if path.is_absolute() else ROOT / path


def _decode_asset(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for public, stored in JSON_FIELDS.items():
        raw = result.pop(stored)
        default = "{}" if public == "variables" else "[]"
        try:
            result[public] = json.loads(str(raw or default))
        except (json.JSONDecodeError, TypeError):
            result[public] = {} if public == "variables" else []
    result["image_url"] = f"/api/visual-assets/{result['id']}/image"
    result["pdf_url"] = f"/api/papers/{result['paper_id']}/pdf#page={result['page_start']}"
    review_fields: dict[str, Any] = {}
    raw_review_fields = result.pop("review_fields_json", None)
    if raw_review_fields:
        try:
            parsed = json.loads(str(raw_review_fields))
            if isinstance(parsed, dict):
                review_fields = parsed
        except (json.JSONDecodeError, TypeError):
            review_fields = {}
    for field in VISUAL_EDITABLE_FIELDS:
        result[f"original_{field}"] = result.get(field)
        if field in review_fields:
            result[field] = review_fields[field]
    result["version_no"] = int(result.pop("visual_version_no", 0) or 0)
    result["review_action"] = str(result.pop("visual_review_action", None) or "automatic")
    result["reviewer"] = str(result.pop("visual_reviewer", None) or "")
    result["review_note"] = str(result.pop("visual_review_note", None) or "")
    result["reviewed_at"] = result.pop("visual_reviewed_at", None)
    return result


def _upsert_asset(db: EvidenceDB, paper: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    asset_type = str(spec["asset_type"])
    number = int(spec["number"])
    label = _asset_label(asset_type, number)
    portable_identity = f"{str(paper.get('doi') or '').lower()}|{paper.get('title') or ''}"
    identity_hash = hashlib.sha256(portable_identity.encode("utf-8")).hexdigest()[:10]
    output_root = VISUAL_ASSET_DIR if db.path.resolve() == EVIDENCE_DB_PATH.resolve() else db.path.parent / "visual_assets"
    output = output_root / f"paper_{int(paper['id']):03d}_{identity_hash}" / f"{asset_type}_{number:02d}.png"
    image_sha = _render_crop(Path(str(paper["pdf_path"])), int(spec["page"]), list(spec["bbox"]), output)
    try:
        relative_path = str(output.relative_to(ROOT))
    except ValueError:
        relative_path = str(output)
    stamp = now()
    inferred = _infer_visual_metadata(str(spec.get("caption") or ""), asset_type)
    values = (
        int(paper["id"]), asset_type, label,
        str(spec.get("display_name") or inferred["display_name"]),
        number, str(spec["caption"]), int(spec["page"]),
        int(spec.get("page_end") or spec["page"]), _json(spec["bbox"]), relative_path, image_sha,
        _json(spec.get("physical_quantities") or inferred["physical_quantities"]), _json(spec.get("variables") or inferred["variables"]),
        _json(spec.get("materials") or []), str(spec.get("conditions") or ""),
        str(spec.get("methods") or inferred["methods"]), str(spec.get("context") or ""),
        _json(spec.get("tags") or inferred["tags"]), str(spec.get("source_context") or ""),
        str(spec.get("review_status") or "draft"), str(spec.get("extraction_method") or "pdf_layout"),
        str(spec.get("metadata_source") or "deterministic"), stamp, stamp,
    )
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO visual_assets(
              paper_id,asset_type,label,display_name,asset_number,caption,page_start,page_end,bbox_json,image_path,image_sha256,
              physical_quantities_json,variables_json,materials_json,conditions_text,methods_text,
              context_explanation,tags_json,source_context,review_status,extraction_method,metadata_source,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(paper_id,asset_type,label) DO UPDATE SET
              display_name=CASE WHEN (visual_assets.metadata_source='manual' OR
                (visual_assets.metadata_source='deepseek' AND visual_assets.caption=excluded.caption))
                AND excluded.metadata_source='deterministic' THEN visual_assets.display_name ELSE excluded.display_name END,
              asset_number=excluded.asset_number,caption=excluded.caption,page_start=excluded.page_start,
              page_end=excluded.page_end,bbox_json=excluded.bbox_json,image_path=excluded.image_path,
              image_sha256=excluded.image_sha256,
              physical_quantities_json=CASE WHEN (visual_assets.metadata_source='manual' OR
                (visual_assets.metadata_source='deepseek' AND visual_assets.caption=excluded.caption))
                AND excluded.metadata_source='deterministic' THEN visual_assets.physical_quantities_json ELSE excluded.physical_quantities_json END,
              variables_json=CASE WHEN (visual_assets.metadata_source='manual' OR
                (visual_assets.metadata_source='deepseek' AND visual_assets.caption=excluded.caption))
                AND excluded.metadata_source='deterministic' THEN visual_assets.variables_json ELSE excluded.variables_json END,
              materials_json=CASE WHEN (visual_assets.metadata_source='manual' OR
                (visual_assets.metadata_source='deepseek' AND visual_assets.caption=excluded.caption))
                AND excluded.metadata_source='deterministic' THEN visual_assets.materials_json ELSE excluded.materials_json END,
              conditions_text=CASE WHEN (visual_assets.metadata_source='manual' OR
                (visual_assets.metadata_source='deepseek' AND visual_assets.caption=excluded.caption))
                AND excluded.metadata_source='deterministic' THEN visual_assets.conditions_text ELSE excluded.conditions_text END,
              methods_text=CASE WHEN (visual_assets.metadata_source='manual' OR
                (visual_assets.metadata_source='deepseek' AND visual_assets.caption=excluded.caption))
                AND excluded.metadata_source='deterministic' THEN visual_assets.methods_text ELSE excluded.methods_text END,
              context_explanation=CASE WHEN (visual_assets.metadata_source='manual' OR
                (visual_assets.metadata_source='deepseek' AND visual_assets.caption=excluded.caption))
                AND excluded.metadata_source='deterministic' THEN visual_assets.context_explanation ELSE excluded.context_explanation END,
              tags_json=CASE WHEN (visual_assets.metadata_source='manual' OR
                (visual_assets.metadata_source='deepseek' AND visual_assets.caption=excluded.caption))
                AND excluded.metadata_source='deterministic' THEN visual_assets.tags_json ELSE excluded.tags_json END,
              source_context=excluded.source_context,review_status=excluded.review_status,
              extraction_method=excluded.extraction_method,
              metadata_source=CASE WHEN (visual_assets.metadata_source='manual' OR
                (visual_assets.metadata_source='deepseek' AND visual_assets.caption=excluded.caption))
                AND excluded.metadata_source='deterministic' THEN visual_assets.metadata_source ELSE excluded.metadata_source END,
              updated_at=excluded.updated_at""",
            values,
        )
        row = conn.execute(
            "SELECT * FROM visual_assets WHERE paper_id=? AND asset_type=? AND label=?",
            (int(paper["id"]), asset_type, label),
        ).fetchone()
    return _decode_asset(dict(row))


def _numbers_for_kind(text: str, asset_type: str) -> set[int]:
    token = r"Table" if asset_type == "table" else r"(?:Fig\.|Figure)"
    numbers: set[int] = set()
    for match in re.finditer(rf"{token}s?\s*(\d+)(?:\s*[-–e]\s*(\d+))?", text or "", re.I):
        start = int(match.group(1))
        end = int(match.group(2) or start)
        numbers.update(range(min(start, end), max(start, end) + 1))
    return numbers


def link_data_items_to_visuals(db: EvidenceDB, paper_id: int) -> dict[str, int]:
    with db.connect() as conn:
        assets = [dict(row) for row in conn.execute(
            "SELECT id,asset_type,asset_number FROM visual_assets WHERE paper_id=?", (paper_id,)
        )]
        rows = [dict(row) for row in conn.execute(
            "SELECT item_id,stable_key,source_locator,source_excerpt,context_explanation "
            "FROM v_current_six_column_data WHERE paper_id=?", (paper_id,)
        )]
        by_key = {(row["asset_type"], int(row["asset_number"])): int(row["id"]) for row in assets}
        inserted = 0
        for row in rows:
            locator = str(row.get("source_locator") or "")
            stable_key = str(row.get("stable_key") or "")
            table_numbers = _numbers_for_kind(locator, "table")
            if not table_numbers:
                table_match = re.match(r"table([1-4])_", stable_key, re.I)
                if table_match:
                    table_numbers.add(int(table_match.group(1)))
                elif stable_key.startswith("comp_"):
                    table_numbers.add(1)
            figure_numbers = _numbers_for_kind(locator, "figure")
            for asset_type, numbers in (("table", table_numbers), ("figure", figure_numbers)):
                for number in numbers:
                    asset_id = by_key.get((asset_type, number))
                    if not asset_id:
                        continue
                    relation = "primary" if asset_type == "table" else "supporting"
                    before = conn.total_changes
                    conn.execute(
                        "INSERT OR IGNORE INTO data_item_visual_links(item_id,asset_id,relation_kind,cell_locator,created_at) "
                        "VALUES(?,?,?,?,?)",
                        (int(row["item_id"]), asset_id, relation, locator or None, now()),
                    )
                    inserted += conn.total_changes - before
        return {"linked": inserted, "asset_count": len(assets), "row_count": len(rows)}


def index_visual_evidence(db: EvidenceDB, paper_id: int) -> dict[str, Any]:
    db.init()
    paper = db.get_paper(paper_id)
    if not paper:
        raise KeyError(f"paper not found: {paper_id}")
    pdf_path = Path(str(paper.get("pdf_path") or ""))
    if not pdf_path.is_file():
        raise FileNotFoundError(f"local PDF not found: {pdf_path}")
    try:
        doc = fitz.open(pdf_path)
        if doc.page_count < 1:
            raise ValueError("PDF contains no pages")
    finally:
        if "doc" in locals():
            doc.close()
    specs = _target_specs(paper) or _generic_specs(pdf_path)
    valid_keys = {(str(spec["asset_type"]), _asset_label(str(spec["asset_type"]), int(spec["number"]))) for spec in specs}
    output_root = VISUAL_ASSET_DIR if db.path.resolve() == EVIDENCE_DB_PATH.resolve() else db.path.parent / "visual_assets"
    with db.connect() as conn:
        stale_assets = [
            dict(row)
            for row in conn.execute("SELECT id,asset_type,label,image_path FROM visual_assets WHERE paper_id=?", (paper_id,))
            if (str(row["asset_type"]), str(row["label"])) not in valid_keys
        ]
        if stale_assets:
            stale_ids = [int(row["id"]) for row in stale_assets]
            placeholders = ",".join("?" for _ in stale_ids)
            conn.execute(f"DELETE FROM visual_assets WHERE id IN ({placeholders})", stale_ids)
    for stale in stale_assets:
        stale_path = _resolve_image_path(str(stale.get("image_path") or ""))
        try:
            stale_path.resolve().relative_to(output_root.resolve())
        except ValueError:
            continue
        if stale_path.is_file():
            stale_path.unlink()
    assets = [_upsert_asset(db, paper, spec) for spec in specs]
    links = link_data_items_to_visuals(db, paper_id)
    return {
        "paper": {"id": paper_id, "title": paper.get("title"), "doi": paper.get("doi"), "pdf_path": str(pdf_path)},
        "asset_count": len(assets),
        "table_count": sum(asset["asset_type"] == "table" for asset in assets),
        "figure_count": sum(asset["asset_type"] == "figure" for asset in assets),
        "links": links,
        "assets": assets,
    }


def _visual_metadata_messages(paper: dict[str, Any], assets: list[dict[str, Any]]) -> list[dict[str, str]]:
    evidence = [{
        "asset_id": int(asset["id"]),
        "type": asset["asset_type"],
        "label": asset["label"],
        "original_caption": asset["caption"],
        "nearby_source_text": asset.get("source_context") or "",
    } for asset in assets]
    return [
        {
            "role": "system",
            "content": (
                "你是科学论文图表证据编目助手。只能依据给出的原始图注和相邻原文，"
                "不得读取曲线点、猜测数值、补写原文没有的材料或实验条件。输出严格 JSON。"
                "为每个图表生成：display_name（4至24字的简短中文名称，保留 W、TEM、SRIM、dpa 等必要符号）；"
                "context_explanation（1至3句中文，说明图表在本文中的作用与比较关系）；"
                "physical_quantities（中文数组）；variables（对象）；materials（材料或样品数组）；"
                "conditions_text（中文）；methods_text（中文）；tags（2至7个针对该图表的具体检索标签）。"
                "标签不得使用‘原文图片’‘原文表格’‘材料’‘方法’这类空泛词。"
                "无法从证据确定的字段使用空字符串、空数组或空对象。"
                "返回 {\"assets\":[...]}，每项必须原样带回 asset_id。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps({
                "paper": {"title": paper.get("title"), "doi": paper.get("doi")},
                "visual_evidence": evidence,
            }, ensure_ascii=False),
        },
    ]


def _clean_model_visual_metadata(item: dict[str, Any]) -> dict[str, Any]:
    display_name = str(item.get("display_name") or "").strip()
    if not display_name or len(display_name) > 60:
        raise ValueError("invalid display_name")

    def string_list(name: str, limit: int = 12) -> list[str]:
        value = item.get(name, [])
        if not isinstance(value, list):
            raise ValueError(f"{name} must be a list")
        return list(dict.fromkeys(str(entry).strip() for entry in value if str(entry).strip()))[:limit]

    variables = item.get("variables", {})
    if isinstance(variables, list):
        variables = {
            f"变量{index}": str(value).strip()
            for index, value in enumerate(variables, start=1) if str(value).strip()
        }
    elif isinstance(variables, str):
        variables = {"变量关系": variables.strip()} if variables.strip() else {}
    elif not isinstance(variables, dict):
        raise ValueError("variables must be an object, list, or string")
    physical_quantities = string_list("physical_quantities")
    materials = string_list("materials")
    context_explanation = str(item.get("context_explanation") or "").strip()
    if not context_explanation:
        raise ValueError("context_explanation cannot be empty")
    generic_tags = {"材料", "方法", "图片", "表格", "原文图片", "原文表格"}
    tags = [tag for tag in string_list("tags", limit=7) if tag not in generic_tags]
    if len(tags) < 2:
        tags = list(dict.fromkeys([
            *tags, display_name, *physical_quantities, *materials,
        ]))[:7]
    if len(tags) < 2:
        raise ValueError("at least two specific tags are required")
    return {
        "display_name": display_name,
        "physical_quantities": physical_quantities,
        "variables": {
            str(key).strip(): str(value).strip()
            for key, value in variables.items()
            if str(key).strip() and str(value).strip()
        },
        "materials": materials,
        "conditions_text": str(item.get("conditions_text") or "").strip(),
        "methods_text": str(item.get("methods_text") or "").strip(),
        "context_explanation": context_explanation,
        "tags": tags,
    }


def enrich_visual_metadata(
    db: EvidenceDB, paper_id: int, *, client: Any | None = None, batch_size: int = 10
) -> dict[str, Any]:
    """Ground visual search metadata in captions/context while preserving original evidence."""

    if client is None:
        from auto_research.ai.deepseek import DeepSeekClient
        client = DeepSeekClient()
    paper = db.get_paper(paper_id)
    if not paper:
        raise KeyError(f"paper not found: {paper_id}")
    assets = list_visual_assets(db, paper_id=paper_id)
    updated = 0
    rejected: list[dict[str, Any]] = []
    for offset in range(0, len(assets), max(1, batch_size)):
        batch = assets[offset:offset + max(1, batch_size)]
        payload = client.request_json(
            _visual_metadata_messages(paper, batch), task="analysis", max_tokens=8_000, thinking=False
        )
        raw_items = payload.get("assets", [])
        if not isinstance(raw_items, list):
            raise ValueError("DeepSeek visual metadata response requires an assets list")
        allowed_ids = {int(asset["id"]) for asset in batch}
        for raw in raw_items:
            if not isinstance(raw, dict):
                rejected.append({"reason": "item is not an object"})
                continue
            try:
                asset_id = int(raw.get("asset_id"))
                if asset_id not in allowed_ids:
                    raise ValueError("asset_id is not in the requested batch")
                clean = _clean_model_visual_metadata(raw)
            except (TypeError, ValueError) as exc:
                rejected.append({"asset_id": raw.get("asset_id"), "reason": str(exc)})
                continue
            with db.connect() as conn:
                conn.execute(
                    """UPDATE visual_assets SET display_name=?,physical_quantities_json=?,variables_json=?,
                       materials_json=?,conditions_text=?,methods_text=?,context_explanation=?,tags_json=?,
                       metadata_source='deepseek',updated_at=? WHERE id=?""",
                    (
                        clean["display_name"], _json(clean["physical_quantities"]), _json(clean["variables"]),
                        _json(clean["materials"]), clean["conditions_text"], clean["methods_text"],
                        clean["context_explanation"], _json(clean["tags"]), now(), asset_id,
                    ),
                )
            updated += 1
    return {"paper_id": paper_id, "asset_count": len(assets), "updated": updated, "rejected": rejected}


def list_visual_assets(db: EvidenceDB, *, asset_type: str | None = None, paper_id: int | None = None) -> list[dict[str, Any]]:
    db.init()
    sql = (
        "SELECT a.*,p.title article_title,p.doi,p.year,p.first_author,p.corresponding_author,"
        "(SELECT COUNT(*) FROM data_item_visual_links l WHERE l.asset_id=a.id) linked_item_count,"
        "vr.version_no visual_version_no,vr.review_action visual_review_action,"
        "vr.fields_json review_fields_json,vr.reviewer visual_reviewer,vr.note visual_review_note,"
        "vr.created_at visual_reviewed_at "
        "FROM visual_assets a LEFT JOIN visual_asset_reviews vr ON vr.asset_id=a.id "
        "AND vr.version_no=(SELECT MAX(vr2.version_no) FROM visual_asset_reviews vr2 WHERE vr2.asset_id=a.id) "
        "JOIN papers p ON p.id=a.paper_id WHERE 1=1"
    )
    params: list[Any] = []
    if asset_type is not None:
        if asset_type not in VISUAL_TYPES:
            raise ValueError(f"unsupported visual asset type: {asset_type}")
        sql += " AND a.asset_type=?"
        params.append(asset_type)
    if paper_id is not None:
        sql += " AND a.paper_id=?"
        params.append(paper_id)
    sql += " ORDER BY p.id,a.asset_type,a.asset_number"
    with db.connect() as conn:
        assets = [_decode_asset(dict(row)) for row in conn.execute(sql, params)]

    # Cloud candidates are a read-time overlay only. The stable rows and image
    # hashes above are never rewritten, and legacy/shadow modes return exactly
    # the pre-cloud representation.
    from .cloud_visual import get_visual_processing_mode, list_cloud_candidates

    if get_visual_processing_mode(db) != "hybrid" or not assets:
        for asset in assets:
            asset["effective_source"] = "legacy"
            asset["stable_image_url"] = asset["image_url"]
        return assets

    paper_ids = sorted({int(asset["paper_id"]) for asset in assets})
    adopted: dict[int, dict[str, Any]] = {}
    for candidate_paper_id in paper_ids:
        for candidate in list_cloud_candidates(db, candidate_paper_id):
            linked_id = int(candidate.get("asset_id") or 0)
            if not linked_id or linked_id in adopted:
                continue
            if candidate.get("quality_status") != "passed":
                continue
            if candidate.get("adoption_state") not in {"enhancement", "interpretation_only"}:
                continue
            adopted[linked_id] = candidate

    semantic_fields = (
        "display_name", "physical_quantities", "variables", "materials",
        "conditions_text", "methods_text", "context_explanation", "tags",
    )
    for asset in assets:
        asset["stable_image_url"] = asset["image_url"]
        asset["effective_source"] = "legacy"
        candidate = adopted.get(int(asset["id"]))
        if not candidate:
            continue
        asset["cloud_candidate"] = candidate
        if candidate.get("analysis_status") == "completed":
            for field in semantic_fields:
                value = candidate.get(field)
                if value not in (None, "", [], {}):
                    asset[field] = value
            asset["trends"] = candidate.get("trends") or []
        if candidate.get("adoption_state") == "enhancement" and candidate.get("image_url"):
            asset["image_url"] = candidate["image_url"]
            asset["effective_source"] = "hybrid_cloud_image_and_semantics"
        else:
            asset["effective_source"] = "hybrid_cloud_semantics"
    return assets


def get_visual_asset(db: EvidenceDB, asset_id: int) -> dict[str, Any]:
    rows = [asset for asset in list_visual_assets(db) if int(asset["id"]) == int(asset_id)]
    if not rows:
        raise KeyError(f"visual asset not found: {asset_id}")
    return rows[0]


def _clean_visual_fields(fields: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for field in VISUAL_EDITABLE_FIELDS:
        value = fields.get(field, current.get(field))
        if field in {"physical_quantities", "materials", "tags"}:
            if not isinstance(value, list):
                raise ValueError(f"{field} must be a list")
            clean[field] = [str(item).strip() for item in value if str(item).strip()]
        elif field == "variables":
            if not isinstance(value, dict):
                raise ValueError("variables must be an object")
            clean[field] = {
                str(key).strip(): str(item).strip()
                for key, item in value.items() if str(key).strip() and str(item).strip()
            }
        else:
            clean[field] = str(value or "").strip()
    if not clean["display_name"]:
        raise ValueError("简短中文名称不能为空")
    if len(clean["display_name"]) > 60:
        raise ValueError("简短中文名称请控制在 60 个字符内")
    return clean


def review_visual_asset(db: EvidenceDB, asset_id: int, fields: dict[str, Any],
                        decision: str, *, reviewer: str = "本地研究者", note: str = "") -> dict[str, Any]:
    """Append a review decision while preserving the indexed screenshot and original metadata."""

    if decision not in VISUAL_REVIEW_ACTIONS:
        raise ValueError(f"unsupported visual review decision: {decision}")
    current = get_visual_asset(db, asset_id)
    clean = _clean_visual_fields(fields or {}, current)
    immutable_original = {
        field: current.get(f"original_{field}", current.get(field))
        for field in VISUAL_EDITABLE_FIELDS
    }
    if decision == "confirmation" and clean != immutable_original:
        decision = "correction"
    if decision in {"ambiguous", "rejected"} and not str(note or "").strip():
        raise ValueError("标记为存在歧义或不采用时，请填写原因")
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(version_no),0) version_no FROM visual_asset_reviews WHERE asset_id=?",
            (asset_id,),
        ).fetchone()
        version_no = int(row["version_no"] or 0) + 1
        conn.execute(
            """INSERT INTO visual_asset_reviews(
               asset_id,version_no,review_action,fields_json,reviewer,note,created_at
               ) VALUES(?,?,?,?,?,?,?)""",
            (asset_id, version_no, decision, _json(clean), str(reviewer or "本地研究者"),
             str(note or "").strip(), now()),
        )
    return get_visual_asset(db, asset_id)


def visual_asset_image_path(db: EvidenceDB, asset_id: int) -> Path:
    asset = get_visual_asset(db, asset_id)
    path = _resolve_image_path(str(asset["image_path"]))
    if not path.is_file():
        raise FileNotFoundError(f"visual asset image missing: {path}")
    return path


def _query_groups(query: str) -> list[list[str]]:
    raw_terms = [term for term in re.findall(r"[\w.+×<≥±°µΩΔ-]+", query, flags=re.UNICODE) if term.strip()]
    groups: list[list[str]] = []
    for raw in raw_terms:
        aliases = ELEMENT_SEARCH_ALIASES.get(raw.lower(), (raw,))
        groups.append(list(dict.fromkeys(str(alias).lower() for alias in aliases)))
    return groups


def _score_term(term: str, text: str, weight: float) -> float:
    candidate = str(text or "").lower()
    if not candidate:
        return 0.0
    if term in ELEMENT_SYMBOLS and len(term) <= 2:
        symbol = next((alias for alias in ELEMENT_SEARCH_ALIASES.get(term, ()) if str(alias)[:1].isupper()), term)
        if re.search(rf"(?<![a-z]){re.escape(str(symbol))}(?=$|[^a-z]|[A-Z0-9])", str(text)):
            return weight * 2.2
    if term in candidate:
        return weight * (3.0 if term == candidate else 2.0)
    words = re.findall(r"[\w.+×<≥±°µΩΔ-]+", candidate, flags=re.UNICODE)
    best = max((difflib.SequenceMatcher(None, term, word).ratio() for word in words), default=0.0)
    return weight * best if best >= 0.62 else 0.0


def search_visual_assets(db: EvidenceDB, query: str, *, asset_type: str, limit: int = 100) -> list[dict[str, Any]]:
    assets = list_visual_assets(db, asset_type=asset_type)
    groups = _query_groups(query)
    if not groups:
        return assets[:limit]
    weights = {
        "display_name": 7.0, "physical_quantities": 6.0, "caption": 5.0,
        "context_explanation": 5.5,
        "tags": 4.5, "variables": 4.0, "materials": 4.0, "conditions_text": 3.5,
        "methods_text": 3.0, "source_context": 2.0, "label": 1.5,
        "article_title": 1.0, "doi": 1.0, "first_author": 1.0, "corresponding_author": 1.0,
    }
    ranked: list[tuple[float, dict[str, Any]]] = []
    for asset in assets:
        total = 0.0
        matched = 0
        for group in groups:
            best = 0.0
            for field, weight in weights.items():
                value = asset.get(field)
                if isinstance(value, (list, dict)):
                    value = _json(value)
                best = max(best, *(_score_term(term, str(value or ""), weight) for term in group))
            if best:
                total += best
                matched += 1
        minimum = 1 if len(groups) == 1 else max(1, (len(groups) + 1) // 2)
        if total and matched >= minimum:
            result = dict(asset)
            result["search_score"] = round(total * (0.65 + 0.35 * matched / len(groups)), 3)
            ranked.append((result["search_score"], result))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["paper_id"], pair[1]["asset_number"]))
    return [asset for _, asset in ranked[:limit]]


def links_for_items(db: EvidenceDB, item_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not item_ids:
        return {}
    placeholders = ",".join("?" for _ in item_ids)
    with db.connect() as conn:
        rows = [dict(row) for row in conn.execute(
            f"""SELECT l.item_id,l.relation_kind,l.cell_locator,a.id,a.asset_type,a.label,a.display_name,
                       a.asset_number,a.page_start,a.caption,a.context_explanation,a.tags_json,a.image_path
                FROM data_item_visual_links l JOIN visual_assets a ON a.id=l.asset_id
                WHERE l.item_id IN ({placeholders})
                ORDER BY l.item_id,CASE l.relation_kind WHEN 'primary' THEN 0 ELSE 1 END,a.asset_number""",
            item_ids,
        )]
    output: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        row["image_url"] = f"/api/visual-assets/{row['id']}/image"
        try:
            row["tags"] = json.loads(str(row.pop("tags_json") or "[]"))
        except (json.JSONDecodeError, TypeError):
            row["tags"] = []
        output.setdefault(int(row["item_id"]), []).append(row)
    return output
