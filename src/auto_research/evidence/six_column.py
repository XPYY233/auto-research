from __future__ import annotations

import csv
import difflib
import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from auto_research.paths import DATA_DIR
from auto_research.ai.deepseek import DeepSeekSettings

from .db import EvidenceDB, now
from .fact_model import classify_nonreportable_row, cluster_fact_rows, cluster_qualitative_rows
from .importers import load_ai_result_payload
from .prompts import prompt_packet_path


TARGET_TITLE = "Irradiation effects in high entropy alloys and 316H stainless steel at 300 °C"
TARGET_DOI = "10.1016/j.jnucmat.2018.08.031"
TARGET_ZOTERO_KEY = "JIKJJZ33"
TARGET_LOCAL_ARTICLE_KEY = "XJZQ42XP"
TARGET_FIRST_AUTHOR = "Wei-Ying Chen"
TARGET_CORRESPONDING_AUTHOR = "Wei-Ying Chen"
TARGET_PDF_PATH = Path("/Users/USER/Zotero/storage/XJZQ42XP/Chen 等 - 2018 - Irradiation effects in high entropy alloys and 316H stainless steel at 300 °C.pdf")
TARGET_EXPORT = DATA_DIR / "extractions" / "XJZQ42XP_six_column_original.csv"
SEARCH_FIELD_WEIGHTS = {
    "meaning": 6.0,
    "context_explanation": 5.0,
    "value_text": 1.5,
    "unit": 1.5,
    "article_title": 1.0,
    "doi": 1.0,
    "first_author": 1.0,
    "corresponding_author": 1.0,
    "source_excerpt": 0.8,
    "source_locator": 0.8,
}
SEARCH_REVIEW_FILTERS = {"all", "reviewed", "pending"}
SEARCH_SOURCE_FILTERS = {"all", "text", "table", "figure", "manual"}
SEARCH_SORTS = {"relevance", "article", "source_page"}
CURRENT_PAPER_META_KEY = "six_column_current_paper_id"
SAVED_SCAN_META_PREFIX = "six_column_saved_snapshot_"
SAVED_SCANS_DIR = DATA_DIR / "evidence" / "saved_scans"
NON_NUMERIC_CELL_MARKERS = {"bal", "bal.", "n.m", "n.m.", "n/a", "na", "—", "-"}
REPORTABLE_VALUE_WORDS = {
    "to", "and", "or", "at", "from", "between", "about", "around",
    "approximately", "approximate", "approx", "below", "above", "under",
    "over", "less", "than", "greater", "more", "maximum", "minimum",
    "max", "min", "dpa", "appm", "ppm", "nm", "um", "mm", "cm",
    "pm", "m", "km", "k", "c", "pa", "kpa", "mpa", "gpa", "tpa",
    "ev", "kev", "mev", "gev", "hz", "khz", "mhz", "ghz", "s",
    "sec", "ms", "min", "h", "hr", "day", "w", "kw", "mw", "v",
    "mv", "kv", "a", "ma", "ua", "na", "j", "kj", "mj", "mol",
    "torr", "bar", "mbar", "atm", "ions", "ion", "atom", "atoms",
    "atomic", "weight", "percent", "vol", "rpm", "rad", "deg", "cps",
}
REPORTABLE_ELEMENT_SYMBOLS = {
    "h", "he", "li", "be", "b", "c", "n", "o", "f", "ne", "na",
    "mg", "al", "si", "p", "s", "cl", "ar", "k", "ca", "sc", "ti",
    "v", "cr", "mn", "fe", "co", "ni", "cu", "zn", "ga", "ge", "as",
    "se", "br", "kr", "rb", "sr", "y", "zr", "nb", "mo", "tc", "ru",
    "rh", "pd", "ag", "cd", "in", "sn", "sb", "te", "i", "xe", "cs",
    "ba", "la", "ce", "pr", "nd", "pm", "sm", "eu", "gd", "tb", "dy",
    "ho", "er", "tm", "yb", "lu", "hf", "ta", "w", "re", "os", "ir",
    "pt", "au", "hg", "tl", "pb", "bi", "po", "at", "rn", "fr", "ra",
    "ac", "th", "pa", "u", "np", "pu",
}

SIX_FIELDS = ("value_text", "meaning", "unit", "article_title", "doi", "context_explanation")
ROW_REVIEW_DECISIONS = {"rejected", "ambiguous", "automatic"}
SNAPSHOT_FIELDS = (
    "item_id", "paper_id", "stable_key", "value_text", "meaning", "unit",
    "article_title", "doi", "context_explanation", "source_page",
    "source_locator", "source_excerpt", "origin_type", "version_no",
    "review_action", "first_author", "corresponding_author", "fact_id",
    "fact_cluster_size", "fact_member_ids", "evidence_count", "evidence_occurrences",
)

ELEMENT_SEARCH_ALIASES = {
    "钨": ("钨", "W", "tungsten"),
    "tungsten": ("钨", "W", "tungsten"),
    "w": ("钨", "W", "tungsten"),
    "铝": ("铝", "Al", "aluminum", "aluminium"),
    "al": ("铝", "Al", "aluminum", "aluminium"),
    "铬": ("铬", "Cr", "chromium"),
    "cr": ("铬", "Cr", "chromium"),
    "钴": ("钴", "Co", "cobalt"),
    "co": ("钴", "Co", "cobalt"),
    "铁": ("铁", "Fe", "iron"),
    "fe": ("铁", "Fe", "iron"),
    "锰": ("锰", "Mn", "manganese"),
    "mn": ("锰", "Mn", "manganese"),
    "镍": ("镍", "Ni", "nickel"),
    "ni": ("镍", "Ni", "nickel"),
    "钽": ("钽", "Ta", "tantalum"),
    "ta": ("钽", "Ta", "tantalum"),
    "钒": ("钒", "V", "vanadium"),
    "v": ("钒", "V", "vanadium"),
    "铪": ("铪", "Hf", "hafnium"),
    "hf": ("铪", "Hf", "hafnium"),
    "钛": ("钛", "Ti", "titanium"),
    "ti": ("钛", "Ti", "titanium"),
    "锆": ("锆", "Zr", "zirconium"),
    "zr": ("锆", "Zr", "zirconium"),
    "钼": ("钼", "Mo", "molybdenum"),
    "mo": ("钼", "Mo", "molybdenum"),
    "铼": ("铼", "Re", "rhenium"),
    "re": ("铼", "Re", "rhenium"),
    "硅": ("硅", "Si", "silicon"),
    "si": ("硅", "Si", "silicon"),
    "碳": ("碳", "C", "carbon"),
    "c": ("碳", "C", "carbon"),
    "氦": ("氦", "He", "helium"),
    "he": ("氦", "He", "helium"),
}


def is_reportable_value_text(value: Any) -> bool:
    """Return whether a six-column value is a real datum rather than prose metadata.

    Numeric values are the normal contract. A small explicit set of table-cell
    markers is retained because values such as ``bal.`` and ``n.m.`` carry a
    defined column meaning in the source table. Material names, methods,
    facilities, conditions, and qualitative sentences belong in meaning or
    context_explanation instead of the value column.
    """

    text = str(value or "").strip()
    if not text:
        return False
    if text.casefold() in NON_NUMERIC_CELL_MARKERS:
        return True
    if not any(character.isdigit() for character in text):
        return False

    # The value column may retain units and compact comparison words from the
    # source, but it must not contain a narrative sentence with a number buried
    # inside it. Long physical explanations belong in meaning/context instead.
    words = re.findall(r"[A-Za-z]+", text)
    return all(
        word.casefold() in REPORTABLE_VALUE_WORDS
        or word.casefold() in REPORTABLE_ELEMENT_SYMBOLS
        for word in words
    )

ELEMENT_SYMBOLS = {
    "al", "cr", "co", "fe", "mn", "ni", "ta", "w", "v", "hf", "ti", "zr", "mo", "re", "si", "c", "he",
}


@dataclass(frozen=True)
class ExtractedDatum:
    stable_key: str
    value_text: str
    meaning: str
    unit: str
    context_explanation: str
    source_page: int
    source_locator: str
    source_excerpt: str

    def as_version(self) -> dict[str, Any]:
        return {
            "value_text": self.value_text,
            "meaning": self.meaning,
            "unit": self.unit,
            "article_title": TARGET_TITLE,
            "doi": TARGET_DOI,
            "context_explanation": self.context_explanation,
            "source_page": self.source_page,
            "source_locator": self.source_locator,
            "source_excerpt": self.source_excerpt,
        }


def _datum(key: str, value: str, meaning: str, unit: str, context: str,
           page: int, locator: str, excerpt: str) -> ExtractedDatum:
    return ExtractedDatum(key, value, meaning, unit, context, page, locator, excerpt)


def target_article_data() -> list[ExtractedDatum]:
    rows: list[ExtractedDatum] = []

    def add(key: str, value: str, meaning: str, unit: str, context: str,
            page: int, locator: str, excerpt: str) -> None:
        rows.append(_datum(key, value, meaning, unit, context, page, locator, excerpt))

    # Materials processing, irradiation, microscopy and indentation conditions.
    method_rows = [
        ("method_hea_cold_roll", "70", "冷轧压下率", "%", "Al0.3CoCrFeNi与CoCrFeMnNi高熵合金；铸锭；均匀化处理前；材料制备条件", 3, "Section 2", "The solidified ingots of HEAs were cold-rolled by 70%."),
        ("method_hea_homogenize_temp", "1200", "均匀化温度", "°C", "Al0.3CoCrFeNi与CoCrFeMnNi高熵合金；冷轧后；均匀化热处理；随后水淬", 3, "Section 2", "homogenized at 1200°C for 48 hours, followed by water quench"),
        ("method_hea_homogenize_time", "48", "均匀化时间", "h", "Al0.3CoCrFeNi与CoCrFeMnNi高熵合金；1200°C均匀化；随后水淬", 3, "Section 2", "homogenized at 1200°C for 48 hours, followed by water quench"),
        ("method_316h_anneal_temp", "1065", "固溶退火温度", "°C", "316H奥氏体不锈钢；固溶退火；随后水淬；辐照前材料制备", 3, "Section 2", "The 316H was solution annealed at 1065°C for 1 hour and then water quenched."),
        ("method_316h_anneal_time", "1", "固溶退火时间", "h", "316H奥氏体不锈钢；1065°C固溶退火；随后水淬", 3, "Section 2", "The 316H was solution annealed at 1065°C for 1 hour and then water quenched."),
        ("method_tem_disk_diameter", "3", "TEM圆片直径", "mm", "全部三种材料；透射电子显微镜样品；从未辐照材料切取并冲片", 3, "Section 2", "Three-mm disks for transmission electron microscopy (TEM) were sectioned and punched."),
        ("method_electrolyte_hclo4", "50", "电解液中高氯酸体积", "mL", "TEM薄片电解抛光；电解液配方；另含950 mL甲醇；-40°C", 3, "Section 2", "an electrolyte of 50 mL perchloric acid and 950 mL methanol at -40°C"),
        ("method_electrolyte_methanol", "950", "电解液中甲醇体积", "mL", "TEM薄片电解抛光；电解液配方；另含50 mL高氯酸；-40°C", 3, "Section 2", "an electrolyte of 50 mL perchloric acid and 950 mL methanol at -40°C"),
        ("method_electropolish_temp", "-40", "电解抛光温度", "°C", "TEM薄片；50 mL高氯酸与950 mL甲醇电解液；样品制备", 3, "Section 2", "electro-polished with an electrolyte ... at -40°C"),
        ("irradiation_energy", "1", "Kr离子辐照能量", "MeV", "Al0.3CoCrFeNi、CoCrFeMnNi和316H；Kr离子；原位TEM辐照；300°C；目标剂量1 dpa", 3, "Section 2", "irradiated with 1 MeV krypton ions with a 15° incident angle at 300°C to 1 dpa"),
        ("irradiation_angle", "15", "离子入射角", "°", "全部三种材料；1 MeV Kr离子；原位TEM；ANL IVEM-Tandem设施", 3, "Section 2", "1 MeV krypton ions with a 15° incident angle"),
        ("irradiation_temperature", "300", "辐照温度", "°C", "全部三种材料；1 MeV Kr离子；原位TEM辐照；目标剂量1 dpa", 3, "Section 2", "at 300°C to 1 dpa"),
        ("irradiation_target_dose", "1", "目标辐照剂量", "dpa", "全部三种材料；1 MeV Kr离子；300°C；原位TEM辐照终点", 3, "Section 2", "at 300°C to 1 dpa"),
        ("irradiation_flux", "6.3×10^15", "Kr离子通量", "ions/(m²·s)", "全部三种材料；1 MeV Kr离子；300°C；ANL IVEM-Tandem原位TEM辐照", 3, "Section 2", "with a flux of 6.3×10^15 ions/(m2·s)"),
        ("irradiation_beam_diameter", "1.5", "均匀离子束区域直径", "mm", "ANL IVEM-Tandem设施；1 MeV Kr离子束；束流均匀区域", 3, "Section 2", "The ion beam is homogeneous over an area of 1.5 mm in diameter."),
        ("tem_voltage", "300", "TEM工作加速电压", "keV", "Hitachi-9000透射电子显微镜；原位辐照观察", 3, "Section 2", "The microscope is a Hitachi-9000 TEM operated at 300 keV."),
        ("image_pixel", "0.2 × 0.2", "图像处理像素对应尺寸", "nm²", "WBDF位错环图像处理；中值滤波前的单像素空间尺寸", 3, "Section 2", "replacing each pixel (0.2 nm × 0.2 nm) with the median value"),
        ("median_filter", "9.6", "中值滤波邻域尺度", "nm", "WBDF位错环图像；去除厚度条纹背景；ImageJ分析前处理", 3, "Section 2", "median value of the neighboring pixels within 9.6 nm"),
        ("gaussian_sigma", "1", "高斯模糊sigma半径", "pixel", "WBDF位错环图像；去除异常高亮像素后的平滑步骤", 3, "Section 2", "Gaussian blurring with a sigma radius of one pixel"),
        ("loop_noise_tolerance", "±10", "位错环计数灰度噪声容差", "gray-scale units", "ImageJ局部极大值位错环计数；用于表征每次密度测量的背景波动不确定性", 3, "Section 2", "a variation of ±10 in grey scale reasonably represented the background fluctuation"),
        ("nano_polish_time", "20", "纳米压痕样品固定抛光时间", "s", "辐照和未辐照样品；去除机械抛光造成的表面变形层", 3, "Section 2", "A fixed polishing time of 20 seconds was used"),
        ("nano_dry_time", "≥5", "纳米压痕前胶粘固定干燥时间", "h", "磁性圆片上用强力胶固定的样品；Hysitron TI 950测试前", 3, "Section 2", "dried for at least 5 hours"),
        ("nano_depth", "100", "纳米压痕最大位移深度", "nm", "Hysitron TI 950；Berkovich压头；准静态位移控制；全部材料与辐照状态", 3, "Section 2", "nominal maximum displacement of 100 nm"),
        ("nano_spacing", "30", "纳米压痕间距", "µm", "全部试样；Hysitron TI 950纳米压痕测试", 3, "Section 2", "The Indent spacing was 30 µm."),
        ("nano_count", "≥10", "每个试样的纳米压痕数量", "indents", "每种材料与辐照状态；硬度均值和标准差的数据基础", 3, "Section 2", "At least 10 indents were performed for each specimen."),
        ("dose_steps", "0.01, 0.06, 0.1, 0.5, 1", "原位TEM观察剂量节点", "dpa", "316H、CoCrFeMnNi、Al0.3CoCrFeNi；1 MeV Kr；300°C；Figures 5-7", 15, "Figures 5-7", "0.01 dpa, 0.06 dpa, 0.1 dpa, 0.5 dpa, 1 dpa"),
        ("srim_damage_depth", "~500", "Kr离子损伤剖面终止深度", "nm", "三种材料；SRIM计算；1 MeV Kr；用于讨论100 nm纳米压痕塑性区覆盖整个损伤层", 6, "Section 3.3 / Fig. 1", "inhomogeneous damage profile that ends at about 500 nm"),
        ("plastic_zone_factor", "~5", "Berkovich压头塑性区相对压入深度倍数", "× indentation depth", "纳米压痕解释；100 nm压入深度对应约500 nm塑性区；引用文献[39]", 6, "Section 3.3", "plastic zone of typical Berkovich tip is about five times the indentation depth"),
        ("irradiation_duration", "~4", "辐照持续时间", "h", "1 MeV Kr；300°C；达到1 dpa；用于讨论Al0.3CoCrFeNi辐照诱导有序化", 5, "Section 3.2", "considering the short irradiation time period (~4 hours)"),
        ("orowan_taylor", "3.06", "Orowan硬化模型Taylor因子M", "—", "等轴fcc材料；由位错环密度和尺寸计算辐照硬化ΔHc", 7, "Section 3.3", "The Taylor factor M is 3.06 for equiaxed fcc."),
        ("orowan_alpha", "0.4", "位错环障碍强度因子α", "—", "Orowan硬化模型；由位错环计算辐照硬化；引用文献[41]", 7, "Section 3.3", "The barrier strength factor α of dislocation loops is 0.4."),
        ("orowan_mu", "77", "剪切模量µ", "GPa", "三种fcc材料的Orowan硬化计算；采用与奥氏体钢相同的值", 7, "Section 3.3", "The shear modulus µ is 77 GPa"),
        ("orowan_b", "0.257", "Burgers矢量b", "nm", "1/2<110> Burgers矢量；Orowan硬化模型；与奥氏体钢相同", 7, "Section 3.3", "The Burgers vector b is 1/2<110> = 0.257 nm."),
        ("hardness_k", "3", "硬度-屈服强度关系常数K", "—", "辐照奥氏体不锈钢；将Orowan模型屈服强度增量转换为计算硬度增量ΔHc", 7, "Section 3.3", "The constant K is 3 for irradiated austenitic stainless steels."),
    ]
    for row in method_rows:
        add(*row)

    # Table 1: exact nominal and measured compositions. Preserve bal. and n.m. verbatim.
    compositions = {
        "Al0.3CoCrFeNi": {
            "nominal": {"Fe": "23.3", "Cr": "23.3", "Ni": "23.3", "Co": "23.3", "Al": "6.8"},
            "measured": {"Fe": "23.7", "Cr": "23.0", "Ni": "22.7", "Co": "24.2", "Al": "6.4"},
        },
        "CoCrFeMnNi": {
            "nominal": {"Fe": "20", "Cr": "20", "Ni": "20", "Mn": "20", "Co": "20"},
            "measured": {"Fe": "20.4", "Cr": "19.8", "Ni": "19.8", "Mn": "19.3", "Co": "20.8"},
        },
        "316H": {
            "nominal": {"Fe": "bal.", "Cr": "18.4", "Ni": "12.5", "Mn": "2.1", "Mo": "1.4", "Si": "1.2", "C": "0.3", "N": "0.1", "P": "0.03", "S": "0.03", "B": "<0.01"},
            "measured": {"Cr": "18.3", "Ni": "12.5", "Mn": "2.0", "Mo": "1.4", "Si": "1.0", "C": "n.m.", "N": "n.m."},
        },
    }
    for material, kinds in compositions.items():
        for kind, elements in kinds.items():
            kind_cn = "名义" if kind == "nominal" else "EDS测量"
            for element, value in elements.items():
                meaning = f"{element}元素{kind_cn}原子分数"
                context = f"材料组成；{material}；{kind_cn}成分；元素{element}；辐照前样品；Table 1"
                note = "n.m.表示未测量" if value == "n.m." else ("bal.表示余量" if value == "bal." else "原子百分比")
                add(f"comp_{material}_{kind}_{element}", value, meaning, "at%", f"{context}；{note}", 8, "Table 1", f"{material} {kind} {element}: {value} at%")

    table2 = {
        "Al0.3CoCrFeNi": [("grain_size", "~500", "晶粒尺寸", "µm"), ("inclusion_fraction", "0.08", "夹杂物体积分数", "%"), ("dislocation_density", "1.4", "初始位错密度", "10^12 m^-2")],
        "CoCrFeMnNi": [("grain_size", "~400", "晶粒尺寸", "µm"), ("inclusion_fraction", "0.45", "夹杂物体积分数", "%"), ("dislocation_density", "2.6", "初始位错密度", "10^12 m^-2")],
        "316H": [("grain_size", "~100", "晶粒尺寸", "µm"), ("inclusion_fraction", "0.18", "夹杂物体积分数", "%"), ("dislocation_density", "3.5", "初始位错密度", "10^12 m^-2")],
    }
    for material, values in table2.items():
        for metric_key, value, meaning, unit in values:
            material_key = re.sub(r"[^a-z0-9]+", "_", material.lower()).strip("_")
            key = f"table2_{material_key}_{metric_key}"
            add(key, value, meaning, unit, f"{material}；辐照前原始状态；微观组织参数；Table 2；用于比较三种材料的初始组织", 8, "Table 2", f"{material}: {meaning} = {value} {unit}")

    table3 = {
        "Al0.3CoCrFeNi": [("h0", "3.56±0.05", "辐照前纳米硬度H0"), ("hirr", "4.63±0.03", "辐照后纳米硬度Hirr"), ("dh", "1.07±0.06", "实测辐照硬化增量ΔH"), ("dhc", "1.37", "模型计算辐照硬化增量ΔHc")],
        "CoCrFeMnNi": [("h0", "3.14±0.09", "辐照前纳米硬度H0"), ("hirr", "4.37±0.12", "辐照后纳米硬度Hirr"), ("dh", "1.23±0.15", "实测辐照硬化增量ΔH"), ("dhc", "1.27", "模型计算辐照硬化增量ΔHc")],
        "316H": [("h0", "2.97±0.04", "辐照前纳米硬度H0"), ("hirr", "3.98±0.06", "辐照后纳米硬度Hirr"), ("dh", "1.01±0.07", "实测辐照硬化增量ΔH"), ("dhc", "1.14", "模型计算辐照硬化增量ΔHc")],
    }
    for material, values in table3.items():
        for metric_key, value, meaning in values:
            material_key = re.sub(r"[^a-z0-9]+", "_", material.lower()).strip("_")
            key = f"table3_{material_key}_{metric_key}"
            condition = "辐照后；1 MeV Kr；300°C；1 dpa；100 nm压入深度" if "辐照后" in meaning or "硬化" in meaning else "辐照前；100 nm压入深度"
            explanation = f"{material}；{condition}；Hysitron TI 950；Berkovich压头；Table 3"
            if "实测" in meaning or "硬度H" in meaning:
                explanation += "；±为至少10个压痕硬度数据的一个标准差"
            if "模型计算" in meaning:
                explanation += "；由1 dpa位错环密度和尺寸通过Orowan硬化模型计算，并非仪器直接测量"
            add(key, value, meaning, "GPa", explanation, 8, "Table 3", f"{material}: {meaning} = {value} GPa")

    table4 = {
        "Al0.3CoCrFeNi": [("delta", "3.49", "原子尺寸失配参数δ", "%"), ("hmix", "-7.27", "混合焓ΔHmix", "kJ mol^-1"), ("smix", "12.83", "混合熵ΔSmix", "J K^-1 mol^-1"), ("tm", "1655", "熔化温度Tm", "K"), ("omega", "2.92", "固溶体形成参数Ω", "—")],
        "CoCrFeMnNi": [("delta", "0.92", "原子尺寸失配参数δ", "%"), ("hmix", "-4.16", "混合焓ΔHmix", "kJ mol^-1"), ("smix", "13.38", "混合熵ΔSmix", "J K^-1 mol^-1"), ("tm", "1562", "熔化温度Tm", "K"), ("omega", "5.02", "固溶体形成参数Ω", "—")],
        "316H": [("delta", "1.47", "原子尺寸失配参数δ", "%"), ("hmix", "-3.62", "混合焓ΔHmix", "kJ mol^-1"), ("smix", "8.72", "混合熵ΔSmix", "J K^-1 mol^-1"), ("tm", "1673", "熔化温度Tm", "K"), ("omega", "4.03", "固溶体形成参数Ω", "—")],
    }
    for material, values in table4.items():
        for metric_key, value, meaning, unit in values:
            material_key = re.sub(r"[^a-z0-9]+", "_", material.lower()).strip("_")
            key = f"table4_{material_key}_{metric_key}"
            extra = "；316H计算仅考虑Fe、Cr、Mn、Ni、Mo和Si" if material == "316H" else ""
            add(key, value, meaning, unit, f"{material}；由文章给出的材料成分及参考原子半径/二元混合焓计算；热力学与晶格失配参数；Table 4{extra}", 8, "Table 4", f"{material}: {meaning} = {value} {unit}")

    observations = [
        ("obs_loop_onset", "0.01", "可观察位错环出现剂量", "dpa", "全部三种材料；1 MeV Kr；300°C；原位TEM；位错环最早出现剂量；Fig. 8", 4, "Section 3.2 / Fig. 8", "dislocation loops appeared in all materials as early as 0.01 dpa"),
        ("obs_loop_saturation", "~0.1", "位错环密度开始趋于饱和的剂量", "dpa", "全部三种材料；1 MeV Kr；300°C；位错环密度随剂量增加，约0.1 dpa后逐渐饱和；Fig. 8", 4, "Section 3.2 / Fig. 8", "density increased ... until about 0.1 dpa when the density gradually saturated"),
        ("obs_loop_size", "a few", "多数位错环尺寸", "nm", "全部三种材料；1 MeV Kr；300°C；剂量不高于1 dpa；正文只报告为few nanometers，未从曲线猜读精确值", 4, "Section 3.2 / Fig. 8", "the majority of the loops remained to be a few nanometers up to 1 dpa"),
        ("obs_void", "not observed", "辐照诱导空洞观察结果", "—", "全部三种材料；1 MeV Kr；300°C；至1 dpa；TEM through-focus检查；未观察到空洞", 4, "Section 3.2", "No void was observed."),
        ("obs_ordering_al", "detected", "辐照后有序化信号", "—", "Al0.3CoCrFeNi；1 MeV Kr；300°C；1 dpa；[110]带轴电子衍射出现fcc禁戒{100}反射；指示原无序fcc晶格发生有序化", 4, "Section 3.2 / Fig. 4", "a trace of {100} reflections ... were observed in Al0.3CoCrFeNi after irradiation"),
        ("obs_no_precipitate", "not observed", "有序析出物观察结果", "—", "Al0.3CoCrFeNi；1 dpa；以{100}禁戒斑点做中心暗场成像；未明确观察到析出物，作者认为可能发生短程有序但未形核长程有序微区", 5, "Section 3.2", "no precipitates can be evidently revealed under TEM at 1 dpa"),
    ]
    for row in observations:
        add(*row)

    # Source pages below refer to the 10-page final published PDF in XJZQ42XP,
    # not the 18-page accepted-manuscript duplicate.
    locator_pages = {"Table 1": 2, "Table 2": 4, "Table 3": 5, "Table 4": 8, "Figures 5-7": 6}
    key_pages = {
        "gaussian_sigma": 3, "loop_noise_tolerance": 3, "nano_polish_time": 3,
        "nano_dry_time": 3, "nano_depth": 3, "nano_spacing": 3, "nano_count": 4,
        "srim_damage_depth": 9, "plastic_zone_factor": 9, "irradiation_duration": 7,
        "orowan_taylor": 9, "orowan_alpha": 9, "orowan_mu": 9, "orowan_b": 9, "hardness_k": 9,
        "obs_loop_onset": 5, "obs_loop_saturation": 5, "obs_loop_size": 5,
        "obs_void": 4, "obs_ordering_al": 7, "obs_no_precipitate": 8,
    }
    published_rows: list[ExtractedDatum] = []
    for datum in rows:
        page = key_pages.get(datum.stable_key)
        if page is None:
            page = locator_pages.get(datum.source_locator, 2 if datum.source_locator == "Section 2" else datum.source_page)
        published_rows.append(replace(datum, source_page=page))
    return published_rows


def find_target_paper(db: EvidenceDB) -> int:
    db.init()
    with db.connect() as conn:
        row = conn.execute("SELECT id FROM papers WHERE doi=?", (TARGET_DOI,)).fetchone()
    if not row:
        raise ValueError(f"Target article not found in evidence database: {TARGET_DOI}")
    return int(row["id"])


def _paper_matches_target(paper: dict[str, Any] | None) -> bool:
    if not paper:
        return False
    return any(
        str(paper.get(field) or "").strip().lower() == target
        for field, target in (
            ("doi", TARGET_DOI.lower()),
            ("zotero_key", TARGET_ZOTERO_KEY.lower()),
            ("local_article_key", TARGET_LOCAL_ARTICLE_KEY.lower()),
        )
    )


def _selector_title_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def resolve_paper_selector(db: EvidenceDB, paper_id: int | None = None, article_key: str | None = None) -> int:
    if paper_id is not None:
        if not db.get_paper(int(paper_id)):
            raise KeyError(f"Paper {paper_id} not found")
        return int(paper_id)
    selector = (article_key or "").strip()
    if not selector:
        raise ValueError("paper_id or article_key is required")
    numeric_id = int(selector) if selector.isdigit() else None
    with db.connect() as conn:
        row = None
        if numeric_id is not None:
            row = conn.execute("SELECT id FROM papers WHERE id=?", (numeric_id,)).fetchone()
        if not row:
            row = conn.execute(
                """SELECT id FROM papers
                WHERE LOWER(COALESCE(local_article_key,''))=LOWER(?)
                   OR LOWER(COALESCE(zotero_key,''))=LOWER(?)
                   OR LOWER(COALESCE(pilot_code,''))=LOWER(?)
                   OR LOWER(COALESCE(doi,''))=LOWER(?)
                LIMIT 1""",
                (selector, selector, selector, selector),
            ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT id FROM papers WHERE LOWER(COALESCE(title,''))=LOWER(?) LIMIT 1",
                (selector,),
            ).fetchone()
        if not row:
            selector_title_key = _selector_title_key(selector)
            title_rows = [dict(current) for current in conn.execute(
                "SELECT id,title,doi,year FROM papers WHERE COALESCE(title,'')!=''"
            )]
            matches = [
                current for current in title_rows
                if selector_title_key
                and (
                    _selector_title_key(current["title"]) == selector_title_key
                    or selector_title_key in _selector_title_key(current["title"])
                )
            ]
            if len(matches) == 1:
                row = {"id": matches[0]["id"]}
            elif len(matches) > 1:
                preview = "; ".join(
                    f"{item['id']}: {item['title']} ({item.get('doi') or item.get('year') or 'no DOI'})"
                    for item in matches[:5]
                )
                raise ValueError(f"文章选择器匹配到多篇文章，请改用 DOI 或完整题目：{preview}")
    if not row:
        raise KeyError(f"Paper selector not found: {selector}")
    return int(row["id"])


def get_current_paper_id(db: EvidenceDB) -> int:
    stored = db.get_meta(CURRENT_PAPER_META_KEY)
    if stored and stored.isdigit() and db.get_paper(int(stored)):
        return int(stored)
    paper_id = find_target_paper(db)
    db.set_meta(CURRENT_PAPER_META_KEY, str(paper_id))
    return paper_id


def get_current_paper(db: EvidenceDB) -> dict[str, Any]:
    paper = db.get_paper(get_current_paper_id(db))
    if not paper:
        raise ValueError("Current paper is not available")
    return paper


def set_current_paper(db: EvidenceDB, paper_id: int | None = None, article_key: str | None = None) -> dict[str, Any]:
    resolved_id = resolve_paper_selector(db, paper_id=paper_id, article_key=article_key)
    db.set_meta(CURRENT_PAPER_META_KEY, str(resolved_id))
    paper = db.get_paper(resolved_id)
    if not paper:
        raise KeyError(f"Paper {resolved_id} not found")
    return paper


def get_six_extraction_status(db: EvidenceDB, paper_id: int | None = None) -> dict[str, Any]:
    resolved_id = paper_id if paper_id is not None else get_current_paper_id(db)
    paper = db.get_paper(resolved_id)
    if not paper:
        raise KeyError(f"Paper {resolved_id} not found")
    row_count = len(list_current_facts(db, resolved_id))
    with db.connect() as conn:
        run_summary = conn.execute(
            """SELECT
                   COUNT(*) AS run_count,
                   SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed_count,
                   MAX(id) AS latest_run_id
               FROM ai_extraction_runs
               WHERE paper_id=?""",
            (resolved_id,),
        ).fetchone()
        latest_run = conn.execute(
            """SELECT status,mode,created_at,finished_at
               FROM ai_extraction_runs
               WHERE paper_id=?
               ORDER BY id DESC
               LIMIT 1""",
            (resolved_id,),
        ).fetchone()
    ai_run_count = int(run_summary["run_count"] or 0) if run_summary else 0
    completed_ai_run_count = int(run_summary["completed_count"] or 0) if run_summary else 0
    scanned = row_count > 0 or completed_ai_run_count > 0
    supported = _paper_matches_target(paper)
    pdf_ok = bool(paper.get("pdf_path") and Path(paper["pdf_path"]).is_file())
    packet_file = prompt_packet_path(resolved_id)
    packet_ready = packet_file.is_file()
    saved_snapshot = db.get_meta(f"{SAVED_SCAN_META_PREFIX}{resolved_id}")
    deepseek_ready = DeepSeekSettings.from_env().public_status()["configured"]
    if supported:
        action = "extract_now"
        action_label = "执行当前文章自动提取" if row_count == 0 else "补齐/确认当前文章自动提取"
        message = "这篇文章已接入自动六列抽取，可直接生成或补齐当前校对表。"
    elif pdf_ok and deepseek_ready:
        action = "deepseek_extract"
        action_label = "DeepSeek 自动提取" if row_count == 0 else "DeepSeek 补充抽取（不覆盖）"
        message = (
            "本地 PDF 和 DeepSeek 均已就绪；无现有数据时，双重验证通过的候选会进入待校对表。"
            if row_count == 0
            else "当前文章已有数据；DeepSeek 会生成补充候选，不覆盖已校对表。"
        )
    elif pdf_ok:
        action = "prepare_packet"
        action_label = "准备当前文章抽取包"
        message = "这篇文章还没有接入自动六列抽取规则，但可以先自动整理抽取包，供后续抽取与回填。"
    else:
        action = "manual_only"
        action_label = "当前只能人工补录"
        message = "这篇文章既没有接入自动六列抽取，也缺少可用本地 PDF，因此当前只能人工补录。"
    return {
        "paper_id": resolved_id,
        "article_key": paper.get("local_article_key") or paper.get("zotero_key") or paper.get("pilot_code") or str(resolved_id),
        "supported": supported,
        "scanned": scanned,
        "scan_state": "scanned" if scanned else "not_scanned",
        "row_count": row_count,
        "ai_run_count": ai_run_count,
        "completed_ai_run_count": completed_ai_run_count,
        "latest_ai_run_status": latest_run["status"] if latest_run else None,
        "latest_ai_run_mode": latest_run["mode"] if latest_run else None,
        "latest_ai_run_at": latest_run["finished_at"] or latest_run["created_at"] if latest_run else None,
        "saved_snapshot_path": saved_snapshot,
        "extractor_name": "xjzq42xp_curated_real_data" if supported else None,
        "parse_status": paper.get("parse_status"),
        "pdf_ready": pdf_ok,
        "deepseek_ready": deepseek_ready,
        "packet_ready": packet_ready,
        "packet_path": str(packet_file) if packet_ready else None,
        "packet_url": f"/api/papers/{resolved_id}/prompt-packet" if packet_ready else None,
        "action": action,
        "action_label": action_label,
        "message": message,
    }


def _safe_snapshot_stem(text: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", text.strip()).strip("._-")
    return stem or "paper"


def save_current_paper_snapshot(db: EvidenceDB, paper_id: int | None = None) -> dict[str, Any]:
    resolved_id = paper_id if paper_id is not None else get_current_paper_id(db)
    paper = db.get_paper(resolved_id)
    if not paper:
        raise KeyError(f"Paper {resolved_id} not found")
    rows = list_current_facts(db, resolved_id)
    if not rows:
        raise ValueError("当前文章还没有可保存的六列表格数据。请先完成扫描或人工录入。")
    article_key = paper.get("local_article_key") or paper.get("zotero_key") or paper.get("pilot_code") or str(resolved_id)
    stamp = now().replace(":", "").replace("+", "Z")
    output = SAVED_SCANS_DIR / f"{_safe_snapshot_stem(str(article_key))}_{stamp}.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    enriched = []
    for row in rows:
        item = {field: row.get(field) for field in SNAPSHOT_FIELDS}
        for field in ("fact_member_ids", "evidence_occurrences"):
            item[field] = json.dumps(item.get(field) or [], ensure_ascii=False)
        item["paper_id"] = resolved_id
        item["local_article_key"] = article_key
        enriched.append(item)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(SNAPSHOT_FIELDS), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(enriched)
    db.set_meta(f"{SAVED_SCAN_META_PREFIX}{resolved_id}", str(output))
    return {
        "ok": True,
        "paper_id": resolved_id,
        "article_key": article_key,
        "row_count": len(rows),
        "path": str(output),
    }


def extract_current_paper_data(db: EvidenceDB, paper_id: int | None = None) -> dict[str, Any]:
    status = get_six_extraction_status(db, paper_id)
    if not status["supported"]:
        raise ValueError(status["message"])
    extraction = seed_target_article(db)
    return {
        **status,
        "message": "已为当前文章执行自动六列抽取。",
        "extraction": extraction,
    }


def prepare_current_paper_packet(db: EvidenceDB, paper_id: int | None = None, max_pages: int = 8) -> dict[str, Any]:
    resolved_id = paper_id if paper_id is not None else get_current_paper_id(db)
    paper = db.get_paper(resolved_id)
    if not paper:
        raise KeyError(f"Paper {resolved_id} not found")
    if _paper_matches_target(paper):
        return {
            **get_six_extraction_status(db, resolved_id),
            "message": "这篇文章已接入直接自动抽取，一般不需要额外准备抽取包。",
        }
    if not paper.get("pdf_path") or not Path(paper["pdf_path"]).is_file():
        raise FileNotFoundError("当前文章没有可读取的本地 PDF，无法准备抽取包")
    from .prompts import build_prompt_packet
    packet = build_prompt_packet(db, resolved_id, max_pages=max_pages)
    return {
        **get_six_extraction_status(db, resolved_id),
        "message": "已为当前文章准备抽取包。",
        "packet_path": str(packet),
        "packet_url": f"/api/papers/{resolved_id}/prompt-packet",
    }


def _normalize_fragment(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _ai_stable_key(item: dict[str, Any]) -> str:
    fingerprint = " | ".join([
        _normalize_fragment(item.get("material_label")),
        _normalize_fragment(item.get("experiment_label")),
        _normalize_fragment(item.get("parameter")),
        _normalize_fragment(item.get("value_raw")),
        _normalize_fragment(item.get("locator")),
        _normalize_fragment(item.get("excerpt")),
        str(item.get("page_number") or ""),
    ])
    return "ai_" + hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:16]


def _evidence_label(value: str) -> str:
    return {
        "measured": "直接测量",
        "derived": "推导量",
        "calculated": "计算量",
        "qualitative": "定性结论",
    }.get(value, value)


def _precision_label(value: str) -> str:
    return {
        "exact_table": "表格精确值",
        "exact_text": "正文精确值",
        "trend": "趋势信息",
        "figure_only": "图中信息",
    }.get(value, value)


def _context_from_ai_measurement(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("material_label", "experiment_label", "condition_text", "measurement_method"):
        value = _normalize_fragment(item.get(key))
        if value:
            parts.append(value)
    parts.append(f"证据类型={_evidence_label(str(item.get('evidence_type') or ''))}")
    parts.append(f"来源精度={_precision_label(str(item.get('source_precision') or ''))}")
    return "；".join(parts)


def import_ai_result_to_six_column(db: EvidenceDB, paper_id: int, source: Path | str | dict[str, Any]) -> dict[str, Any]:
    payload = load_ai_result_payload(db, paper_id, source)
    paper = db.get_paper(paper_id)
    if not paper:
        raise KeyError(f"Paper {paper_id} not found")
    inserted = existing = rejected_non_numeric = 0
    with db.connect() as conn:
        for item in payload.get("measurements", []):
            if not is_reportable_value_text(item.get("value_raw")):
                rejected_non_numeric += 1
                continue
            stable_key = _ai_stable_key(item)
            row = conn.execute(
                "SELECT id FROM data_items WHERE paper_id=? AND stable_key=?",
                (paper_id, stable_key),
            ).fetchone()
            if row:
                existing += 1
                continue
            cur = conn.execute(
                "INSERT INTO data_items(paper_id,stable_key,origin_type,created_at) VALUES(?,?,?,?)",
                (paper_id, stable_key, "automatic", now()),
            )
            conn.execute(
                """INSERT INTO data_versions(item_id,version_no,value_text,meaning,unit,article_title,doi,
                context_explanation,source_page,source_locator,source_excerpt,editor,edit_note,review_action,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(cur.lastrowid),
                    0,
                    str(item["value_raw"]),
                    str(item["parameter"]),
                    str(item.get("unit_raw") or ""),
                    str(paper["title"]),
                    str(paper.get("doi") or ""),
                    _context_from_ai_measurement(item),
                    int(item["page_number"]),
                    item.get("locator"),
                    str(item["excerpt"]),
                    "AI packet import",
                    "Immutable automatic extraction imported from constrained JSON packet",
                    "automatic",
                    now(),
                ),
            )
            inserted += 1
    return {
        "paper_id": paper_id,
        "inserted": inserted,
        "existing": existing,
        "rejected_non_numeric": rejected_non_numeric,
        "total": inserted + existing,
        "packet_measurements": len(payload.get("measurements", [])),
        "packet_tasks": len(payload.get("pending_tasks", [])),
    }


def seed_target_article(db: EvidenceDB, export_path: Path | None = TARGET_EXPORT) -> dict[str, int]:
    paper_id = find_target_paper(db)
    if not TARGET_PDF_PATH.is_file():
        raise FileNotFoundError(TARGET_PDF_PATH)
    digest = hashlib.sha256(TARGET_PDF_PATH.read_bytes()).hexdigest()
    inserted = existing = 0
    db.init()
    with db.connect() as conn:
        conn.execute(
            """UPDATE papers
               SET pdf_path=?,pdf_sha256=?,local_article_key=?,
                   first_author=COALESCE(first_author,?),
                   corresponding_author=COALESCE(corresponding_author,?),
                   updated_at=?
               WHERE id=?""",
            (
                str(TARGET_PDF_PATH), digest, TARGET_LOCAL_ARTICLE_KEY,
                TARGET_FIRST_AUTHOR, TARGET_CORRESPONDING_AUTHOR, now(), paper_id,
            ),
        )
        for datum in target_article_data():
            row = conn.execute(
                "SELECT id FROM data_items WHERE paper_id=? AND stable_key=?", (paper_id, datum.stable_key)
            ).fetchone()
            if row:
                existing += 1
                continue
            cur = conn.execute(
                "INSERT INTO data_items(paper_id,stable_key,origin_type,created_at) VALUES(?,?,?,?)",
                (paper_id, datum.stable_key, "automatic", now()),
            )
            version = datum.as_version()
            conn.execute(
                """INSERT INTO data_versions(item_id,version_no,value_text,meaning,unit,article_title,doi,
                context_explanation,source_page,source_locator,source_excerpt,editor,edit_note,review_action,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(cur.lastrowid), 0, version["value_text"], version["meaning"], version["unit"],
                    version["article_title"], version["doi"], version["context_explanation"], version["source_page"],
                    version["source_locator"], version["source_excerpt"], "Codex initial extraction",
                    "Immutable automatic extraction", "automatic", now(),
                ),
            )
            inserted += 1
    if not db.get_meta(CURRENT_PAPER_META_KEY):
        db.set_meta(CURRENT_PAPER_META_KEY, str(paper_id))
    if export_path is not None:
        export_original_csv(db, export_path)
    return {"inserted": inserted, "existing": existing, "total": inserted + existing}


def _validate_fields(fields: dict[str, Any]) -> dict[str, str]:
    clean: dict[str, str] = {}
    for field in SIX_FIELDS:
        value = fields.get(field, "")
        if value is None:
            value = ""
        clean[field] = str(value).strip()
    for required in ("value_text", "meaning", "article_title", "context_explanation"):
        if not clean[required]:
            raise ValueError(f"{required} cannot be empty")
    if not is_reportable_value_text(clean["value_text"]):
        raise ValueError("具体数值必须包含数字；材料、方法、设施、条件和定性句子请写入具体意义或文章解释。")
    return clean


def confirm_correction(db: EvidenceDB, item_id: int, fields: dict[str, Any],
                       editor: str = "本地研究者", note: str = "") -> dict[str, Any]:
    clean = _validate_fields(fields)
    try:
        cluster = fact_cluster_for_item(db, item_id)
        member_ids = [int(value) for value in cluster.get("fact_member_ids", [item_id])]
    except KeyError:
        member_ids = [item_id]
    with db.connect() as conn:
        item = conn.execute("SELECT * FROM data_items WHERE id=?", (item_id,)).fetchone()
        if not item:
            raise KeyError(f"Data item {item_id} not found")
        representative_latest = conn.execute(
            "SELECT * FROM data_versions WHERE item_id=? ORDER BY version_no DESC LIMIT 1", (item_id,)
        ).fetchone()
        changed_fields = [
            field for field in SIX_FIELDS
            if str(clean[field]) != str(representative_latest[field] or "")
        ]
        review_action = "correction" if changed_fields else "confirmation"
        edit_note = note or (
            f"修改字段：{'、'.join(changed_fields)}" if changed_fields else "人工确认：内容无修改"
        )
        if len(member_ids) > 1:
            edit_note += f"；同一物理事实簇共 {len(member_ids)} 条来源记录"
        for member_id in member_ids:
            latest = conn.execute(
                "SELECT * FROM data_versions WHERE item_id=? ORDER BY version_no DESC LIMIT 1", (member_id,)
            ).fetchone()
            if not latest:
                continue
            conn.execute(
                """INSERT INTO data_versions(item_id,version_no,value_text,meaning,unit,article_title,doi,
                context_explanation,source_page,source_locator,source_excerpt,editor,edit_note,review_action,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    member_id, int(latest["version_no"]) + 1, clean["value_text"], clean["meaning"],
                    clean["unit"], clean["article_title"], clean["doi"], clean["context_explanation"],
                    latest["source_page"], latest["source_locator"], latest["source_excerpt"], editor,
                    edit_note, review_action, now(),
                ),
            )
    result = fact_cluster_for_item(db, item_id)
    result["history"] = get_data_item(db, int(result["item_id"]))["history"]
    return result


def set_row_review_decision(db: EvidenceDB, item_id: int, decision: str, *,
                            reason_code: str = "", note: str = "",
                            editor: str = "本地研究者") -> dict[str, Any]:
    """Record a reversible ambiguity/rejection decision as a new version."""

    if decision not in ROW_REVIEW_DECISIONS:
        raise ValueError(f"Unsupported row review decision: {decision}")
    reason = re.sub(r"\s+", " ", str(reason_code or "").strip())
    detail = re.sub(r"\s+", " ", str(note or "").strip())
    if decision in {"rejected", "ambiguous"} and not reason:
        raise ValueError("A review reason is required")
    try:
        cluster = fact_cluster_for_item(db, item_id)
        member_ids = [int(value) for value in cluster.get("fact_member_ids", [item_id])]
    except KeyError:
        member_ids = [item_id]
    with db.connect() as conn:
        item = conn.execute("SELECT * FROM data_items WHERE id=?", (item_id,)).fetchone()
        if not item:
            raise KeyError(f"Data item {item_id} not found")
        if item["origin_type"] != "automatic":
            raise ValueError("Manual rows cannot be marked as automatic extraction decisions")
        if decision == "automatic":
            edit_note = detail or "恢复为待审核"
        else:
            label = "不采用" if decision == "rejected" else "存在歧义"
            edit_note = f"{label}：{reason}" + (f"；{detail}" if detail else "")
        if len(member_ids) > 1:
            edit_note += f"；同一物理事实簇共 {len(member_ids)} 条来源记录"
        for member_id in member_ids:
            latest = conn.execute(
                "SELECT * FROM data_versions WHERE item_id=? ORDER BY version_no DESC LIMIT 1", (member_id,)
            ).fetchone()
            if not latest:
                continue
            conn.execute(
                """INSERT INTO data_versions(item_id,version_no,value_text,meaning,unit,article_title,doi,
                context_explanation,source_page,source_locator,source_excerpt,editor,edit_note,review_action,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    member_id, int(latest["version_no"]) + 1, latest["value_text"], latest["meaning"],
                    latest["unit"], latest["article_title"], latest["doi"], latest["context_explanation"],
                    latest["source_page"], latest["source_locator"], latest["source_excerpt"], editor,
                    edit_note, decision, now(),
                ),
            )
    result = fact_cluster_for_item(db, item_id)
    result["history"] = get_data_item(db, int(result["item_id"]))["history"]
    return result


def add_manual_item(db: EvidenceDB, paper_id: int, fields: dict[str, Any],
                    editor: str = "本地研究者") -> dict[str, Any]:
    clean = _validate_fields(fields)
    stable_key = f"manual_{uuid.uuid4().hex}"
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO data_items(paper_id,stable_key,origin_type,created_at) VALUES(?,?,?,?)",
            (paper_id, stable_key, "manual", now()),
        )
        item_id = int(cur.lastrowid)
        conn.execute(
            """INSERT INTO data_versions(item_id,version_no,value_text,meaning,unit,article_title,doi,
            context_explanation,editor,edit_note,review_action,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                item_id, 0, clean["value_text"], clean["meaning"], clean["unit"], clean["article_title"],
                clean["doi"], clean["context_explanation"], editor, "Manual data entry; no automatic original",
                "manual", now(),
            ),
        )
    return get_data_item(db, item_id)


def add_qualitative_item(db: EvidenceDB, paper_id: int, finding: dict[str, Any],
                         editor: str = "DeepSeek qualitative extraction") -> dict[str, Any]:
    """Store an evidence-grounded prose finding outside the numeric fact view."""

    paper = db.get_paper(paper_id)
    if not paper:
        raise KeyError(f"Paper {paper_id} not found")
    fields = {
        "value_text": str(finding.get("finding_text") or finding.get("value_text") or "").strip(),
        "meaning": str(finding.get("meaning") or "").strip(),
        "unit": "",
        "article_title": str(paper.get("title") or "").strip(),
        "doi": str(paper.get("doi") or "").strip(),
        "context_explanation": str(finding.get("context_explanation") or "").strip(),
    }
    for required in ("value_text", "meaning", "article_title", "context_explanation"):
        if not fields[required]:
            raise ValueError(f"qualitative finding {required} cannot be empty")
    if is_reportable_value_text(fields["value_text"]):
        raise ValueError("numeric values belong in the physical fact collection")
    if classify_nonreportable_row(fields) != "qualitative_finding":
        raise ValueError("methods, instruments, facilities, and condition labels are not qualitative findings")
    identity = "|".join((
        fields["value_text"].casefold(), fields["meaning"].casefold(),
        fields["context_explanation"].casefold(), str(finding.get("source_page") or ""),
        str(finding.get("source_excerpt") or "").casefold(),
    ))
    stable_key = f"qualitative_{hashlib.sha1(identity.encode('utf-8')).hexdigest()[:20]}"
    existing_id: int | None = None
    with db.connect() as conn:
        existing = conn.execute(
            "SELECT id FROM data_items WHERE paper_id=? AND stable_key=?", (paper_id, stable_key)
        ).fetchone()
        if existing:
            existing_id = int(existing["id"])
        else:
            cur = conn.execute(
                "INSERT INTO data_items(paper_id,stable_key,origin_type,created_at) VALUES(?,?,?,?)",
                (paper_id, stable_key, "automatic", now()),
            )
            existing_id = int(cur.lastrowid)
            conn.execute(
                """INSERT INTO data_versions(item_id,version_no,value_text,meaning,unit,article_title,doi,
                context_explanation,source_page,source_locator,source_excerpt,editor,edit_note,review_action,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    existing_id, 0, fields["value_text"], fields["meaning"], "", fields["article_title"],
                    fields["doi"], fields["context_explanation"], finding.get("source_page"),
                    str(finding.get("source_locator") or ""), str(finding.get("source_excerpt") or ""),
                    editor, "Automatically separated from numeric data as a qualitative finding",
                    "automatic", now(),
                ),
            )
    return get_data_item(db, int(existing_id))


def collect_learning_samples(db: EvidenceDB, paper_id: int | None = None) -> dict[str, Any]:
    rows = list_reportable_current_data(db, paper_id)
    samples: list[dict[str, Any]] = []
    correction_count = confirmation_count = manual_count = rejected_count = ambiguous_count = 0
    for row in rows:
        paper_info = {
            "paper_id": row["paper_id"],
            "article_key": row.get("local_article_key") or row.get("zotero_key") or row.get("pilot_code") or str(row["paper_id"]),
            "article_title": row["article_title"],
            "doi": row["doi"],
        }
        action = str(row.get("review_action") or "automatic")
        if row["origin_type"] == "automatic" and action in {"confirmation", "correction", "rejected", "ambiguous"}:
            original_fields = {field: row[f"original_{field}"] for field in SIX_FIELDS}
            corrected_fields = {field: row[field] for field in SIX_FIELDS}
            changed_fields = [
                field for field in SIX_FIELDS
                if str(original_fields.get(field) or "") != str(corrected_fields.get(field) or "")
            ]
            sample_type = {
                "confirmation": "confirmation",
                "correction": "correction",
                "rejected": "rejection",
                "ambiguous": "ambiguity",
            }[action]
            samples.append({
                "sample_type": sample_type,
                "item_id": row["item_id"],
                "stable_key": row["stable_key"],
                "version_no": row["version_no"],
                "changed_fields": changed_fields,
                "source": {
                    "page": row.get("original_source_page"),
                    "locator": row.get("original_source_locator"),
                    "excerpt": row.get("original_source_excerpt"),
                },
                "original": original_fields,
                "corrected": None if sample_type == "rejection" else corrected_fields,
                "editor": row.get("editor"),
                "edit_note": row.get("edit_note"),
                "review_action": row.get("review_action"),
                "created_at": row.get("created_at"),
                **paper_info,
            })
            if sample_type == "confirmation":
                confirmation_count += 1
            elif sample_type == "correction":
                correction_count += 1
            elif sample_type == "rejection":
                rejected_count += 1
            else:
                ambiguous_count += 1
        elif row["origin_type"] == "manual":
            manual_fields = {field: row[field] for field in SIX_FIELDS}
            samples.append({
                "sample_type": "manual_addition",
                "item_id": row["item_id"],
                "stable_key": row["stable_key"],
                "version_no": row["version_no"],
                "changed_fields": list(SIX_FIELDS),
                "source": {"page": None, "locator": None, "excerpt": None},
                "original": None,
                "corrected": manual_fields,
                "editor": row.get("editor"),
                "edit_note": row.get("edit_note"),
                "review_action": row.get("review_action"),
                "created_at": row.get("created_at"),
                **paper_info,
            })
            manual_count += 1
    paper_meta = db.get_paper(paper_id) if paper_id is not None else None
    return {
        "paper_id": paper_id,
        "paper_title": paper_meta["title"] if paper_meta else None,
        "sample_count": len(samples),
        "correction_count": correction_count,
        "confirmation_count": confirmation_count,
        "manual_count": manual_count,
        "rejected_count": rejected_count,
        "ambiguous_count": ambiguous_count,
        "samples": samples,
    }


def learning_samples_jsonl(db: EvidenceDB, paper_id: int | None = None) -> str:
    payload = collect_learning_samples(db, paper_id)
    return "\n".join(json.dumps(sample, ensure_ascii=False) for sample in payload["samples"])


def _base_current_sql() -> str:
    return """
        SELECT item_id,paper_id,stable_key,origin_type,
               local_article_key,zotero_key,pilot_code,first_author,corresponding_author,
               version_id,version_no,value_text,meaning,unit,article_title,doi,
               context_explanation,source_page,source_locator,source_excerpt,editor,edit_note,
               review_action,created_at,
               original_value_text,original_meaning,original_unit,
               original_article_title,original_doi,original_context_explanation,
               original_source_page,original_source_locator,original_source_excerpt
        FROM v_current_six_column_data i
    """


def list_current_data(db: EvidenceDB, paper_id: int | None = None) -> list[dict[str, Any]]:
    sql = _base_current_sql()
    params: tuple[Any, ...] = ()
    if paper_id is not None:
        sql += " WHERE i.paper_id=?"
        params = (paper_id,)
    sql += " ORDER BY i.item_id"
    with db.connect() as conn:
        rows = [dict(row) for row in conn.execute(sql, params)]
    # Visual assets are optional provenance. Import lazily to avoid coupling the
    # core six-column schema to PDF rendering during module import.
    from .visual_evidence import links_for_items

    links = links_for_items(db, [int(row["item_id"]) for row in rows])
    for row in rows:
        row_links = links.get(int(row["item_id"]), [])
        row["visual_assets"] = row_links
        primary = next((asset for asset in row_links if asset["relation_kind"] == "primary"), None)
        row["primary_visual_asset"] = primary
        if row.get("origin_type") == "manual":
            row["source_kind"] = "manual"
        elif primary and primary["asset_type"] == "table":
            row["source_kind"] = "table"
        elif primary and primary["asset_type"] == "figure":
            row["source_kind"] = "figure"
        elif any(asset["asset_type"] == "figure" for asset in row_links):
            row["source_kind"] = "text_with_figure"
        else:
            row["source_kind"] = "text"
    return rows


def list_reportable_current_data(db: EvidenceDB, paper_id: int | None = None) -> list[dict[str, Any]]:
    """Return current rows that satisfy the user-facing numeric-data contract.

    Non-reportable legacy versions remain in SQLite for provenance and history,
    but are not presented as current experimental data or calibration work.
    """

    return [
        row for row in list_current_data(db, paper_id)
        if is_reportable_value_text(row.get("value_text"))
    ]


def list_current_facts(db: EvidenceDB, paper_id: int | None = None) -> list[dict[str, Any]]:
    """Return one user-facing physical fact per semantic cluster.

    The underlying six-column rows and every source location remain intact.
    A fact exposes ``fact_member_ids`` and ``evidence_occurrences`` so review,
    search, and export can use one fact without losing provenance.
    """

    return cluster_fact_rows(list_reportable_current_data(db, paper_id))


def list_qualitative_findings(db: EvidenceDB, paper_id: int | None = None) -> list[dict[str, Any]]:
    """Return prose observations separated from the numeric fact collection."""

    prose_rows = [
        row for row in list_current_data(db, paper_id)
        if not is_reportable_value_text(row.get("value_text"))
    ]
    return cluster_qualitative_rows(prose_rows)


def fact_cluster_for_item(db: EvidenceDB, item_id: int) -> dict[str, Any]:
    row = get_data_item(db, item_id)
    facts = list_current_facts(db, int(row["paper_id"]))
    for fact in facts:
        if item_id in fact.get("fact_member_ids", []):
            return fact
    raise KeyError(f"Physical fact for data item {item_id} not found")


def list_paper_workflow_summaries(db: EvidenceDB) -> list[dict[str, Any]]:
    """Return paper-picker counts based on independent physical facts."""

    papers = db.list_papers()
    raw_reportable_rows = list_reportable_current_data(db)
    reportable_rows = cluster_fact_rows(raw_reportable_rows)
    reportable_counts: dict[int, int] = {}
    for row in raw_reportable_rows:
        row_paper_id = int(row["paper_id"])
        reportable_counts[row_paper_id] = reportable_counts.get(row_paper_id, 0) + 1
    counts: dict[int, dict[str, int]] = {}
    for row in reportable_rows:
        paper_id = int(row["paper_id"])
        current = counts.setdefault(paper_id, {
            "total": 0, "reviewed": 0, "confirmed": 0, "corrected": 0,
            "rejected": 0, "ambiguous": 0, "manual": 0,
        })
        current["total"] += 1
        action = str(row.get("review_action") or "automatic")
        origin = str(row.get("origin_type") or "automatic")
        if origin == "manual":
            current["manual"] += 1
            current["reviewed"] += 1
        elif action in {"confirmation", "correction", "rejected", "ambiguous"}:
            current["reviewed"] += 1
            current[{"confirmation": "confirmed", "correction": "corrected"}.get(action, action)] += 1

    result: list[dict[str, Any]] = []
    for source in papers:
        paper = dict(source)
        raw_total = int(paper.get("six_row_count") or 0)
        current = counts.get(int(paper["id"]), {
            "total": 0, "reviewed": 0, "confirmed": 0, "corrected": 0,
            "rejected": 0, "ambiguous": 0, "manual": 0,
        })
        total = current["total"]
        reviewed = current["reviewed"]
        unreviewed = max(total - reviewed, 0)
        reportable_count = reportable_counts.get(int(paper["id"]), 0)
        paper.update({
            "six_raw_row_count": raw_total,
            "six_reportable_row_count": reportable_count,
            "six_excluded_nonreportable_count": max(raw_total - reportable_count, 0),
            "six_semantic_duplicate_count": max(reportable_count - total, 0),
            "six_row_count": total,
            "six_reviewed_count": reviewed,
            "six_confirmed_count": current["confirmed"],
            "six_corrected_count": current["corrected"],
            "six_rejected_count": current["rejected"],
            "six_ambiguous_count": current["ambiguous"],
            "six_manual_count": current["manual"],
            "six_unreviewed_count": unreviewed,
            "six_reviewed_ratio": round(reviewed / total, 4) if total else 0.0,
        })
        completed_runs = int(paper.get("completed_ai_run_count") or 0)
        if total == 0 and completed_runs == 0:
            paper["six_workflow_state"] = "not_scanned"
            paper["six_workflow_label"] = "未扫描"
        elif total == 0:
            paper["six_workflow_state"] = "scanned_empty"
            paper["six_workflow_label"] = "已扫描无可报告数据"
        elif unreviewed:
            paper["six_workflow_state"] = "pending_review"
            paper["six_workflow_label"] = f"待审核 {unreviewed}/{total}"
        else:
            paper["six_workflow_state"] = "reviewed"
            paper["six_workflow_label"] = f"已完成 {total}/{total}"
        result.append(paper)
    return result


def review_progress(db: EvidenceDB, paper_id: int | None = None) -> dict[str, Any]:
    """Summarize human-review progress by independent physical fact."""

    rows = list_current_facts(db, paper_id)
    total = len(rows)
    manual = sum(1 for row in rows if row.get("origin_type") == "manual")
    confirmed = sum(1 for row in rows if row.get("review_action") == "confirmation")
    corrected = sum(1 for row in rows if row.get("review_action") == "correction")
    rejected = sum(1 for row in rows if row.get("review_action") == "rejected")
    ambiguous = sum(1 for row in rows if row.get("review_action") == "ambiguous")
    reviewed = manual + confirmed + corrected + rejected + ambiguous
    return {
        "paper_id": paper_id,
        "total": total,
        "reviewed": reviewed,
        "unreviewed": max(total - reviewed, 0),
        "confirmed": confirmed,
        "corrected": corrected,
        "rejected": rejected,
        "ambiguous": ambiguous,
        "manual": manual,
        "automatic": total - manual,
        "reviewed_ratio": round(reviewed / total, 4) if total else 0.0,
    }


def get_data_item(db: EvidenceDB, item_id: int) -> dict[str, Any]:
    with db.connect() as conn:
        row = conn.execute(_base_current_sql() + " WHERE i.item_id=?", (item_id,)).fetchone()
        if not row:
            raise KeyError(f"Data item {item_id} not found")
        result = dict(row)
        result["history"] = [dict(v) for v in conn.execute(
            "SELECT * FROM data_versions WHERE item_id=? ORDER BY version_no DESC", (item_id,)
        )]
        return result


def _query_terms(query: str) -> list[list[str]]:
    raw_terms = [term for term in re.findall(r"[\w.+×<≥±°µΩΔ]+", query, flags=re.UNICODE) if term.strip()]
    if not raw_terms:
        stripped = query.strip()
        raw_terms = [stripped] if stripped else []
    groups: list[list[str]] = []
    for raw in raw_terms:
        key = raw.lower()
        aliases = ELEMENT_SEARCH_ALIASES.get(key, (raw,))
        normalized = []
        for alias in aliases:
            alias_key = str(alias).lower()
            if alias_key not in normalized:
                normalized.append(alias_key)
        groups.append(normalized)
    return groups


def _chemical_symbol_score(term: str, text: str, weight: float) -> float:
    if term not in ELEMENT_SYMBOLS or not text:
        return 0.0
    symbol = next((alias for alias in ELEMENT_SEARCH_ALIASES.get(term, ()) if str(alias).isalpha() and str(alias)[0].isupper()), term)
    pattern = re.compile(rf"(?<![a-z]){re.escape(str(symbol))}(?=$|[^a-z]|[A-Z0-9])")
    if pattern.search(text):
        return weight * 2.2
    if re.search(rf"\b{re.escape(term)}\b", text.lower()):
        return weight * 2.0
    return 0.0


def _field_score(term: str, text: str, weight: float) -> float:
    raw_text = text or ""
    candidate = raw_text.lower()
    if not candidate:
        return 0.0
    symbol_score = _chemical_symbol_score(term, raw_text, weight)
    if symbol_score:
        return symbol_score
    if term in ELEMENT_SYMBOLS and len(term) <= 2:
        return 0.0
    if term in candidate:
        return weight * (3.0 if candidate == term else 2.0)
    # Short scientific/search tokens create excessive fuzzy collisions (for
    # example "less" matching "stress" or "half" matching Hf contexts).
    # Keep them exact; reserve typo tolerance for longer, distinctive terms.
    if len(term) < 5:
        return 0.0
    words = re.findall(r"[\w.+×<≥±°µΩΔ]+", candidate, flags=re.UNICODE)
    best = max((difflib.SequenceMatcher(None, term, word).ratio() for word in words), default=0.0)
    return weight * best if best >= 0.82 else 0.0


def _filter_search_rows(rows: list[dict[str, Any]], *,
                        review_filter: str, source_filter: str) -> list[dict[str, Any]]:
    if review_filter not in SEARCH_REVIEW_FILTERS:
        raise ValueError(f"unsupported search review filter: {review_filter}")
    if source_filter not in SEARCH_SOURCE_FILTERS:
        raise ValueError(f"unsupported search source filter: {source_filter}")
    if review_filter == "reviewed":
        rows = [row for row in rows if row.get("origin_type") == "manual" or row.get("review_action") in {"confirmation", "correction"}]
    elif review_filter == "pending":
        rows = [row for row in rows if row.get("origin_type") != "manual" and row.get("review_action") == "automatic"]
    if source_filter == "text":
        rows = [row for row in rows if row.get("source_kind") == "text"]
    elif source_filter == "table":
        rows = [row for row in rows if row.get("source_kind") == "table"]
    elif source_filter == "figure":
        rows = [row for row in rows if row.get("source_kind") in {"figure", "text_with_figure"}]
    elif source_filter == "manual":
        rows = [row for row in rows if row.get("source_kind") == "manual"]
    return rows


def _sort_search_rows(rows: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    if sort not in SEARCH_SORTS:
        raise ValueError(f"unsupported search sort: {sort}")
    if sort == "article":
        return sorted(rows, key=lambda row: (
            str(row.get("article_title") or "").casefold(),
            str(row.get("meaning") or "").casefold(),
            int(row["item_id"]),
        ))
    if sort == "source_page":
        return sorted(rows, key=lambda row: (
            str(row.get("article_title") or "").casefold(),
            int(row.get("source_page") or row.get("original_source_page") or 10**9),
            int(row["item_id"]),
        ))
    return rows


def search_current_data(db: EvidenceDB, query: str, limit: int = 100, *,
                        include_excluded: bool = False,
                        review_filter: str = "all",
                        source_filter: str = "all",
                        sort: str = "relevance") -> list[dict[str, Any]]:
    rows = list_current_facts(db)
    if not include_excluded:
        rows = [row for row in rows if row.get("review_action") not in {"rejected", "ambiguous"}]
    rows = _filter_search_rows(rows, review_filter=review_filter, source_filter=source_filter)
    if sort not in SEARCH_SORTS:
        raise ValueError(f"unsupported search sort: {sort}")
    visual_label = re.fullmatch(r"\s*(table|figure|fig\.?)\s*(\d+)\s*", query, re.I)
    if visual_label:
        asset_type = "table" if visual_label.group(1).lower() == "table" else "figure"
        number = int(visual_label.group(2))
        matched = [
            row for row in rows
            if any(
                asset.get("asset_type") == asset_type and int(asset.get("asset_number") or 0) == number
                for asset in row.get("visual_assets", [])
            )
        ]
        matched = [{**row, "search_score": 100.0} for row in matched]
        return _sort_search_rows(matched, sort)[:limit]
    term_groups = _query_terms(query)
    if not term_groups:
        return _sort_search_rows(rows, sort)[:limit]
    ranked: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        total = 0.0
        matched_terms = 0
        for term_group in term_groups:
            score = max(
                max(_field_score(term, row["meaning"], SEARCH_FIELD_WEIGHTS["meaning"]) for term in term_group),
                max(_field_score(term, row["context_explanation"], SEARCH_FIELD_WEIGHTS["context_explanation"]) for term in term_group),
                max(_field_score(term, row["value_text"], SEARCH_FIELD_WEIGHTS["value_text"]) for term in term_group),
                max(_field_score(term, row["unit"], SEARCH_FIELD_WEIGHTS["unit"]) for term in term_group),
                max(_field_score(term, row["article_title"], SEARCH_FIELD_WEIGHTS["article_title"]) for term in term_group),
                max(_field_score(term, row["doi"], SEARCH_FIELD_WEIGHTS["doi"]) for term in term_group),
                max(_field_score(term, row.get("first_author"), SEARCH_FIELD_WEIGHTS["first_author"]) for term in term_group),
                max(_field_score(term, row.get("corresponding_author"), SEARCH_FIELD_WEIGHTS["corresponding_author"]) for term in term_group),
                max(_field_score(term, row["source_excerpt"], SEARCH_FIELD_WEIGHTS["source_excerpt"]) for term in term_group),
                max(_field_score(term, row["source_locator"], SEARCH_FIELD_WEIGHTS["source_locator"]) for term in term_group),
                max(_field_score(term, row.get("search_text"), SEARCH_FIELD_WEIGHTS["source_excerpt"]) for term in term_group),
            )
            if score:
                matched_terms += 1
                total += score
        minimum_matches = 1 if len(term_groups) == 1 else math.ceil(len(term_groups) * 0.5)
        if total and matched_terms >= minimum_matches:
            coverage = matched_terms / len(term_groups)
            ranked.append((total * (0.65 + 0.35 * coverage), row))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["item_id"]))
    output = []
    for score, row in ranked[:limit]:
        row = dict(row)
        row["search_score"] = round(score, 3)
        output.append(row)
    return _sort_search_rows(output, sort)


def search_qualitative_findings(db: EvidenceDB, query: str, limit: int = 100) -> list[dict[str, Any]]:
    """Search prose observations without mixing them into numeric data rows."""

    findings = [
        row for row in list_qualitative_findings(db)
        if row.get("review_action") not in {"rejected"}
    ]
    term_groups = _query_terms(query)
    if not term_groups:
        return findings[:limit]
    ranked: list[tuple[float, dict[str, Any]]] = []
    for row in findings:
        total = 0.0
        matched = 0
        for group in term_groups:
            score = max(
                max(_field_score(term, row.get("finding_text"), 6.0) for term in group),
                max(_field_score(term, row.get("meaning"), 5.5) for term in group),
                max(_field_score(term, row.get("context_explanation"), 4.5) for term in group),
                max(_field_score(term, row.get("search_text"), 2.0) for term in group),
                max(_field_score(term, row.get("article_title"), 1.0) for term in group),
                max(_field_score(term, row.get("doi"), 1.0) for term in group),
            )
            if score:
                matched += 1
                total += score
        minimum = 1 if len(term_groups) == 1 else math.ceil(len(term_groups) * 0.5)
        if matched >= minimum:
            result = dict(row)
            result["search_score"] = round(total * (0.65 + 0.35 * matched / len(term_groups)), 3)
            ranked.append((result["search_score"], result))
    ranked.sort(key=lambda pair: (-pair[0], int(pair[1]["item_id"])))
    return [row for _, row in ranked[:limit]]


def export_original_csv(db: EvidenceDB, path: Path = TARGET_EXPORT) -> Path:
    paper_id = find_target_paper(db)
    rows = []
    for current in list_reportable_current_data(db, paper_id):
        if current["origin_type"] != "automatic":
            continue
        original = dict(current)
        for field in SIX_FIELDS:
            original[field] = current[f"original_{field}"]
        rows.append(original)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [*SIX_FIELDS, "source_page", "source_locator", "source_excerpt", "stable_key"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path
