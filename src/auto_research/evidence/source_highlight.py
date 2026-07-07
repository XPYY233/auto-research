from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz

from .db import EvidenceDB
from .six_column import get_data_item


@dataclass(frozen=True)
class SourceHighlightResult:
    page_number: int
    locator: str
    excerpt: str
    match_type: str
    match_label: str
    match_note: str
    rects: list[fitz.Rect]


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    for src, dst in (
        ("…", " "),
        ("...", " "),
        ("×", "x"),
        ("µ", "u"),
        ("°", " "),
        ("·", " "),
        ("\u00a0", " "),
    ):
        normalized = normalized.replace(src, dst)
    normalized = re.sub(r"[\[\]{}()_,;:]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _tokenize(text: str) -> list[str]:
    return [token for token in re.findall(r"[\w.+<>/=+-]+", _normalize_text(text)) if token]


def _rect_union(rects: list[fitz.Rect]) -> fitz.Rect:
    union = fitz.Rect(rects[0])
    for rect in rects[1:]:
        union |= rect
    return union


def _dedupe_rects(rects: list[fitz.Rect]) -> list[fitz.Rect]:
    unique: list[fitz.Rect] = []
    seen: set[tuple[float, float, float, float]] = set()
    for rect in rects:
        key = tuple(round(value, 2) for value in (rect.x0, rect.y0, rect.x1, rect.y1))
        if key in seen:
            continue
        seen.add(key)
        unique.append(fitz.Rect(rect))
    return unique


def _search_terms(page: fitz.Page, terms: list[str]) -> list[fitz.Rect]:
    hits: list[fitz.Rect] = []
    for term in terms:
        term = (term or "").strip()
        if not term:
            continue
        hits.extend(page.search_for(term))
    return _dedupe_rects(hits)


def _excerpt_chunks(excerpt: str) -> list[str]:
    parts = re.split(r"\.\.\.|…", excerpt or "")
    chunks: list[str] = []
    for part in parts:
        piece = part.strip(" ,;:")
        if len(piece) >= 12:
            chunks.append(piece)
    return chunks


def _value_variants(value_text: str) -> list[str]:
    raw = (value_text or "").strip()
    if not raw:
        return []
    variants = [raw]
    stripped = re.sub(r"^[~≈<>≤≥]+", "", raw).strip()
    if stripped and stripped not in variants:
        variants.append(stripped)
    plus_minus_stripped = stripped.replace("±", " ").strip()
    if plus_minus_stripped and plus_minus_stripped not in variants:
        variants.append(plus_minus_stripped)
    if "±" in stripped:
        left = stripped.split("±", 1)[0].strip()
        if left and left not in variants:
            variants.append(left)
    return variants


def _locator_variants(locator: str) -> list[str]:
    value = (locator or "").strip()
    if not value:
        return []
    variants = [value]
    figure_range = re.fullmatch(r"Figures?\s+(\d+)\s*-\s*(\d+)", value, flags=re.IGNORECASE)
    if figure_range:
        start, end = int(figure_range.group(1)), int(figure_range.group(2))
        for number in range(start, end + 1):
            variants.extend([f"Figure {number}", f"Fig. {number}"])
    for number in re.findall(r"Fig\.?\s*(\d+)", value, flags=re.IGNORECASE):
        variants.extend([f"Fig. {number}", f"Figure {number}"])
    for number in re.findall(r"Table\s*(\d+)", value, flags=re.IGNORECASE):
        variants.append(f"Table {number}")
    return list(dict.fromkeys(variants))


def _context_terms(context: str) -> list[str]:
    terms: list[str] = []
    for piece in re.split(r"[；;]", context or "")[:3]:
        part = piece.strip()
        if not part:
            continue
        if re.search(r"[A-Za-z]", part) or re.search(r"\d", part):
            terms.append(part)
    return terms


def _page_line_windows(page: fitz.Page) -> list[tuple[str, fitz.Rect]]:
    words = page.get_text("words")
    if not words:
        return []
    buckets: dict[tuple[int, int], list[tuple[Any, ...]]] = {}
    order: list[tuple[int, int]] = []
    for word in words:
        key = (int(word[5]), int(word[6]))
        buckets.setdefault(key, []).append(word)
        if key not in order:
            order.append(key)
    lines: list[tuple[str, fitz.Rect]] = []
    for key in order:
        line_words = sorted(buckets[key], key=lambda item: item[7])
        text = " ".join(str(item[4]) for item in line_words).strip()
        rects = [fitz.Rect(*item[:4]) for item in line_words]
        lines.append((text, _rect_union(rects)))
    windows: list[tuple[str, fitz.Rect]] = []
    for start in range(len(lines)):
        text_parts: list[str] = []
        rects: list[fitz.Rect] = []
        for end in range(start, min(len(lines), start + 4)):
            text_parts.append(lines[end][0])
            rects.append(lines[end][1])
            windows.append((" ".join(text_parts).strip(), _rect_union(rects)))
    return windows


def _fuzzy_window_match(page: fitz.Page, excerpt: str, locator: str, value_text: str) -> list[fitz.Rect]:
    target_text = excerpt or f"{locator} {value_text}"
    target_norm = _normalize_text(target_text)
    if not target_norm:
        return []
    target_tokens = set(_tokenize(target_text))
    best_score = 0.0
    best_rect: fitz.Rect | None = None
    value_signals = [_normalize_text(value) for value in _value_variants(value_text) if _normalize_text(value)]
    locator_signals = [_normalize_text(value) for value in _locator_variants(locator) if _normalize_text(value)]
    for text, rect in _page_line_windows(page):
        window_norm = _normalize_text(text)
        if not window_norm:
            continue
        ratio = difflib.SequenceMatcher(None, target_norm, window_norm).ratio()
        window_tokens = set(_tokenize(text))
        overlap = len(target_tokens & window_tokens) / len(target_tokens) if target_tokens else 0.0
        score = ratio * 0.6 + overlap * 0.4
        if any(signal and signal in window_norm for signal in value_signals):
            score += 0.12
        if any(signal and signal in window_norm for signal in locator_signals):
            score += 0.10
        if score > best_score:
            best_score = score
            best_rect = rect
    if best_rect and best_score >= 0.43:
        return [best_rect]
    return []


def locate_highlight(page: fitz.Page, *, locator: str, excerpt: str, value_text: str,
                     context_explanation: str) -> SourceHighlightResult:
    exact_excerpt = _search_terms(page, [excerpt])
    if exact_excerpt:
        return SourceHighlightResult(
            page_number=page.number + 1,
            locator=locator,
            excerpt=excerpt,
            match_type="exact_excerpt",
            match_label="已高亮原文句子",
            match_note="直接在 PDF 文本层中找到这条证据的原始句子或短语。",
            rects=exact_excerpt,
        )

    excerpt_fragments = _search_terms(page, _excerpt_chunks(excerpt))
    if excerpt_fragments:
        return SourceHighlightResult(
            page_number=page.number + 1,
            locator=locator,
            excerpt=excerpt,
            match_type="excerpt_fragments",
            match_label="已高亮原文片段",
            match_note="原句跨行或被省略号截断，因此改为高亮能直接匹配到的原文片段。",
            rects=excerpt_fragments,
        )

    locator_hits = _search_terms(page, _locator_variants(locator))
    value_hits = _search_terms(page, _value_variants(value_text))
    context_hits = _search_terms(page, _context_terms(context_explanation))

    if locator_hits and value_hits:
        rects = locator_hits + value_hits[:3]
        return SourceHighlightResult(
            page_number=page.number + 1,
            locator=locator,
            excerpt=excerpt,
            match_type="locator_and_value",
            match_label="已高亮表格/图注锚点",
            match_note="这条证据更像表格或图中的值，因此高亮了图表编号和对应数值，便于快速定位。",
            rects=_dedupe_rects(rects),
        )

    if value_hits and context_hits:
        rects = context_hits[:2] + value_hits[:3]
        return SourceHighlightResult(
            page_number=page.number + 1,
            locator=locator,
            excerpt=excerpt,
            match_type="context_and_value",
            match_label="已高亮材料与数值",
            match_note="原句无法直接匹配，因此高亮了材料/条件锚点和该数据值本身。",
            rects=_dedupe_rects(rects),
        )

    if locator_hits:
        return SourceHighlightResult(
            page_number=page.number + 1,
            locator=locator,
            excerpt=excerpt,
            match_type="locator_only",
            match_label="已高亮图表编号",
            match_note="这页上更容易通过图表编号定位，因此先高亮对应的 Table/Fig. 标题。",
            rects=locator_hits,
        )

    if value_hits:
        return SourceHighlightResult(
            page_number=page.number + 1,
            locator=locator,
            excerpt=excerpt,
            match_type="value_only",
            match_label="已高亮数值位置",
            match_note="未找到整句，但已在这一页上定位到同一数值。",
            rects=value_hits[:3],
        )

    fuzzy = _fuzzy_window_match(page, excerpt, locator, value_text)
    if fuzzy:
        return SourceHighlightResult(
            page_number=page.number + 1,
            locator=locator,
            excerpt=excerpt,
            match_type="fuzzy_window",
            match_label="已高亮最接近的原文行",
            match_note="原文文本层和结构化片段不完全一致，因此按相似度高亮了最接近的原文区域。",
            rects=fuzzy,
        )

    return SourceHighlightResult(
        page_number=page.number + 1,
        locator=locator,
        excerpt=excerpt,
        match_type="page_only",
        match_label="仅定位到页码",
        match_note="这一条证据暂时没法在文本层中精确高亮，当前保留页码定位供人工核验。",
        rects=[],
    )


def _resolve_source_fields(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("origin_type") == "manual":
        raise ValueError("Manual items do not have immutable automatic source evidence")
    return {
        "paper_id": int(item["paper_id"]),
        "page_number": int(item.get("original_source_page") or item.get("source_page") or 1),
        "locator": str(item.get("original_source_locator") or item.get("source_locator") or ""),
        "excerpt": str(item.get("original_source_excerpt") or item.get("source_excerpt") or ""),
        "value_text": str(item.get("original_value_text") or item.get("value_text") or ""),
        "context_explanation": str(item.get("original_context_explanation") or item.get("context_explanation") or ""),
    }


def _load_pdf_path(db: EvidenceDB, paper_id: int) -> Path:
    paper = db.get_paper(paper_id)
    if not paper or not paper.get("pdf_path"):
        raise FileNotFoundError(f"PDF not found for paper {paper_id}")
    path = Path(paper["pdf_path"])
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def get_source_view(db: EvidenceDB, item_id: int) -> dict[str, Any]:
    item = get_data_item(db, item_id)
    source = _resolve_source_fields(item)
    pdf_path = _load_pdf_path(db, source["paper_id"])
    with fitz.open(pdf_path) as doc:
        page_index = max(0, min(source["page_number"] - 1, len(doc) - 1))
        match = locate_highlight(
            doc[page_index],
            locator=source["locator"],
            excerpt=source["excerpt"],
            value_text=source["value_text"],
            context_explanation=source["context_explanation"],
        )
    return {
        "item_id": item_id,
        "paper_id": source["paper_id"],
        "page_number": match.page_number,
        "locator": source["locator"],
        "excerpt": source["excerpt"],
        "match_type": match.match_type,
        "match_label": match.match_label,
        "match_note": match.match_note,
        "image_url": f"/api/six-data/{item_id}/source-highlight.png",
        "pdf_url": f"/api/papers/{source['paper_id']}/pdf#page={match.page_number}",
    }


def render_source_highlight_png(db: EvidenceDB, item_id: int, zoom: float = 2.4) -> bytes:
    item = get_data_item(db, item_id)
    source = _resolve_source_fields(item)
    pdf_path = _load_pdf_path(db, source["paper_id"])
    with fitz.open(pdf_path) as doc:
        page_index = max(0, min(source["page_number"] - 1, len(doc) - 1))
        page = doc[page_index]
        match = locate_highlight(
            page,
            locator=source["locator"],
            excerpt=source["excerpt"],
            value_text=source["value_text"],
            context_explanation=source["context_explanation"],
        )
        for rect in match.rects:
            annot = page.add_highlight_annot(rect)
            annot.set_colors(stroke=(0.98, 0.70, 0.08))
            annot.set_opacity(0.42)
            annot.update()
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), annots=True, alpha=False)
        return pixmap.tobytes("png")
