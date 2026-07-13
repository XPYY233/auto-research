from __future__ import annotations

import difflib
import hashlib
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


def _generic_specs(pdf_path: Path) -> list[dict[str, Any]]:
    """Find caption-led visual regions without interpreting curve values."""

    specs: list[dict[str, Any]] = []
    doc = fitz.open(pdf_path)
    try:
        for page_index, page in enumerate(doc):
            blocks = [b for b in page.get_text("blocks") if str(b[4]).strip()]
            image_rects: list[fitz.Rect] = []
            for image in page.get_images(full=True):
                for rect in page.get_image_rects(image[0]):
                    if rect.width > 60 and rect.height > 40:
                        image_rects.append(rect)
            drawings = [drawing["rect"] for drawing in page.get_drawings()]
            for block in blocks:
                text = " ".join(str(block[4]).split())
                match = re.match(r"(?:Fig\.|Figure)\s*(\d+)\.?\s*(.*)", text, re.I)
                if match:
                    caption_rect = fitz.Rect(*block[:4])
                    candidates = [rect for rect in image_rects if rect.y1 <= caption_rect.y0 + 12]
                    if not candidates:
                        continue
                    image_rect = min(candidates, key=lambda rect: (max(caption_rect.y0 - rect.y1, 0), -rect.get_area()))
                    crop = image_rect | caption_rect
                    crop = fitz.Rect(crop.x0 - 8, crop.y0 - 7, crop.x1 + 8, crop.y1 + 7) & page.rect
                    specs.append({
                        "asset_type": "figure", "number": int(match.group(1)), "page": page_index + 1,
                        "bbox": list(crop), "caption": match.group(2).strip() or text,
                        "physical_quantities": [], "variables": {}, "materials": [],
                        "conditions": "", "methods": "", "context": match.group(2).strip(),
                        "tags": ["Figure", f"Figure {match.group(1)}"], "source_context": text,
                    })
                    continue
                match = re.match(r"Table\s*(\d+)\.?\s*(.*)", text, re.I)
                if not match:
                    continue
                caption_rect = fitz.Rect(*block[:4])
                horizontal = [
                    rect for rect in drawings
                    if rect.y0 >= caption_rect.y1 - 2 and rect.width > 100 and rect.height < 10
                ]
                if not horizontal:
                    continue
                bottom = max(rect.y1 for rect in horizontal)
                column_right = page.rect.width - 28 if caption_rect.x0 > page.rect.width / 2 else min(page.rect.width - 28, caption_rect.x0 + 270)
                crop = fitz.Rect(caption_rect.x0 - 6, caption_rect.y0 - 6, column_right, min(bottom + 26, page.rect.height - 28)) & page.rect
                specs.append({
                    "asset_type": "table", "number": int(match.group(1)), "page": page_index + 1,
                    "bbox": list(crop), "caption": match.group(2).strip() or text,
                    "physical_quantities": [], "variables": {}, "materials": [],
                    "conditions": "", "methods": "", "context": match.group(2).strip(),
                    "tags": ["Table", f"Table {match.group(1)}"], "source_context": text,
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
    values = (
        int(paper["id"]), asset_type, label, number, str(spec["caption"]), int(spec["page"]),
        int(spec.get("page_end") or spec["page"]), _json(spec["bbox"]), relative_path, image_sha,
        _json(spec.get("physical_quantities") or []), _json(spec.get("variables") or {}),
        _json(spec.get("materials") or []), str(spec.get("conditions") or ""),
        str(spec.get("methods") or ""), str(spec.get("context") or ""),
        _json(spec.get("tags") or []), str(spec.get("source_context") or ""),
        str(spec.get("review_status") or "draft"), str(spec.get("extraction_method") or "pdf_layout"), stamp, stamp,
    )
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO visual_assets(
              paper_id,asset_type,label,asset_number,caption,page_start,page_end,bbox_json,image_path,image_sha256,
              physical_quantities_json,variables_json,materials_json,conditions_text,methods_text,
              context_explanation,tags_json,source_context,review_status,extraction_method,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(paper_id,asset_type,label) DO UPDATE SET
              asset_number=excluded.asset_number,caption=excluded.caption,page_start=excluded.page_start,
              page_end=excluded.page_end,bbox_json=excluded.bbox_json,image_path=excluded.image_path,
              image_sha256=excluded.image_sha256,physical_quantities_json=excluded.physical_quantities_json,
              variables_json=excluded.variables_json,materials_json=excluded.materials_json,
              conditions_text=excluded.conditions_text,methods_text=excluded.methods_text,
              context_explanation=excluded.context_explanation,tags_json=excluded.tags_json,
              source_context=excluded.source_context,review_status=excluded.review_status,
              extraction_method=excluded.extraction_method,updated_at=excluded.updated_at""",
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


def list_visual_assets(db: EvidenceDB, *, asset_type: str | None = None, paper_id: int | None = None) -> list[dict[str, Any]]:
    db.init()
    sql = (
        "SELECT a.*,p.title article_title,p.doi,p.year,p.first_author,p.corresponding_author,"
        "(SELECT COUNT(*) FROM data_item_visual_links l WHERE l.asset_id=a.id) linked_item_count "
        "FROM visual_assets a JOIN papers p ON p.id=a.paper_id WHERE 1=1"
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
        return [_decode_asset(dict(row)) for row in conn.execute(sql, params)]


def get_visual_asset(db: EvidenceDB, asset_id: int) -> dict[str, Any]:
    rows = [asset for asset in list_visual_assets(db) if int(asset["id"]) == int(asset_id)]
    if not rows:
        raise KeyError(f"visual asset not found: {asset_id}")
    return rows[0]


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
        "physical_quantities": 6.0, "caption": 5.5, "context_explanation": 5.0,
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
            f"""SELECT l.item_id,l.relation_kind,l.cell_locator,a.id,a.asset_type,a.label,a.asset_number,
                       a.page_start,a.caption,a.image_path
                FROM data_item_visual_links l JOIN visual_assets a ON a.id=l.asset_id
                WHERE l.item_id IN ({placeholders})
                ORDER BY l.item_id,CASE l.relation_kind WHEN 'primary' THEN 0 ELSE 1 END,a.asset_number""",
            item_ids,
        )]
    output: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        row["image_url"] = f"/api/visual-assets/{row['id']}/image"
        output.setdefault(int(row["item_id"]), []).append(row)
    return output
