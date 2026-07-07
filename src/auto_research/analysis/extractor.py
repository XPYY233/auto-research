from __future__ import annotations

import json
import re
import html
from pathlib import Path
from typing import Any

from auto_research.db import ResearchDB
from auto_research.models import PaperState
from auto_research.paths import REPORTS_DIR

KEYWORDS = {
    "materials": ["W", "tungsten", "Ta", "V", "Nb", "Mo", "Cr", "FeCrAl", "ODS", "high entropy", "high-entropy", "RHEA", "CCA"],
    "mlip_types": ["GAP", "MTP", "SNAP", "NEP", "MACE", "Deep Potential", "DP", "machine learning interatomic potential", "MLIP", "MLIAP"],
    "methods": ["DFT", "molecular dynamics", "MD", "LAMMPS", "GPUMD", "VASP", "ab initio", "TEM", "APT", "nanoindentation", "ion irradiation", "neutron irradiation"],
    "defects": ["vacancy", "vacancies", "SIA", "interstitial", "dislocation loop", "cluster", "void", "Frank loop", "cascade"],
}

PARAM_PATTERNS = {
    "pka_energy": r"(?i)(?:PKA|primary knock-on atom).{0,80}?(\d+(?:\.\d+)?)\s*(keV|eV)",
    "temperature": r"(?i)(\d{2,4})\s*K",
    "dose": r"(?i)(\d+(?:\.\d+)?)\s*(dpa|ions/cm\^?2|ion/cm\^?2)",
    "box_size": r"(?i)(\d+(?:\.\d+)?)\s*(?:×|x)\s*(\d+(?:\.\d+)?)\s*(?:×|x)\s*(\d+(?:\.\d+)?)\s*(nm|Å|A)",
}


class Analyzer:
    def __init__(self, db: ResearchDB | None = None):
        self.db = db or ResearchDB()
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    def analyze_batch(self, limit: int = 20) -> tuple[int, int]:
        papers = self.db.list_papers(states=[PaperState.PARSED.value, PaperState.ANALYZED.value], limit=limit)
        ok = failed = 0
        for p in papers:
            try:
                self.analyze_one(dict(p))
                ok += 1
            except Exception as e:
                failed += 1
                with self.db.connect() as conn:
                    self.db.event(conn, int(p["id"]), "analysis_failed", repr(e))
        return ok, failed

    def analyze_one(self, paper: dict[str, Any]) -> Path:
        data = json.loads(Path(paper["text_path"]).read_text(encoding="utf-8"))
        text = data.get("text", "")
        profile = build_profile(paper, data, text)
        report_md = render_card(profile)
        out = REPORTS_DIR / f"{int(paper['id']):05d}_card.md"
        out.write_text(report_md, encoding="utf-8")
        profile_path = REPORTS_DIR / f"{int(paper['id']):05d}_profile.json"
        profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        self.db.update_paths(int(paper["id"]), PaperState.ANALYZED, report_path=str(out))
        return out


def build_profile(paper: dict[str, Any], parsed: dict[str, Any], text: str) -> dict[str, Any]:
    title = paper["title"]
    sections = parsed.get("sections", {})
    abstract = sections.get("abstract") or paper.get("abstract") or text[:2500]
    profile = {
        "paper_id": paper["id"],
        "title": title,
        "year": paper.get("year"),
        "doi": paper.get("doi"),
        "research_type": classify_research_type(text),
        "materials": find_keywords(text, KEYWORDS["materials"]),
        "mlip_type": find_keywords(text, KEYWORDS["mlip_types"]),
        "methods": find_keywords(text, KEYWORDS["methods"]),
        "defects": find_keywords(text, KEYWORDS["defects"]),
        "parameters": extract_parameters(text),
        "core_question": summarize_from_section(abstract, max_sentences=2),
        "method_summary": summarize_from_section(sections.get("methods") or text[:8000], max_sentences=3),
        "main_findings": summarize_from_section(sections.get("results") or sections.get("discussion") or sections.get("conclusion") or text[-10000:], max_sentences=4),
        "limitations": extract_limitations(text),
        "value_for_my_research": assess_value(text),
        "reusable_data": assess_reusable_data(text),
        "worth_deep_reading": assess_worth(text),
        "authenticity_status": paper.get("authenticity_status") or "not_verified",
        "authenticity_score": paper.get("authenticity_score"),
        "verification": safe_json(paper.get("verification_json")),
        "evidence_pages": evidence_pages(parsed.get("pages", [])),
    }
    return profile


def safe_json(value):
    try:
        return json.loads(value or "{}")
    except Exception:
        return {}


def find_keywords(text: str, keywords: list[str]) -> list[str]:
    lower = text.lower()
    hits = []
    for kw in keywords:
        pattern = r"\b" + re.escape(kw.lower()) + r"\b" if kw.isalnum() else re.escape(kw.lower())
        if re.search(pattern, lower):
            hits.append(kw)
    return sorted(set(hits), key=str.lower)


def classify_research_type(text: str) -> list[str]:
    lower = text.lower()
    out = []
    if any(x in lower for x in ["machine learning interatomic", "mlip", "gap", "mtp", "nep", "mace", "snap", "deep potential"]):
        out.append("MLIP")
    if any(x in lower for x in ["collision cascade", "displacement cascade", "pka", "primary knock-on"]):
        out.append("cascade")
    if any(x in lower for x in ["molecular dynamics", "lammps", "gpumd", "simulation"]):
        out.append("MD/simulation")
    if any(x in lower for x in ["dft", "density functional", "ab initio", "vasp"]):
        out.append("DFT")
    if any(x in lower for x in ["irradiation experiment", "ion irradiation", "neutron irradiation", "tem", "atom probe", "nanoindentation"]):
        out.append("experiment")
    if "review" in lower[:5000]:
        out.append("review")
    return out or ["unknown"]


def extract_parameters(text: str) -> dict[str, list[str]]:
    out = {}
    for name, pat in PARAM_PATTERNS.items():
        vals = []
        for m in re.finditer(pat, text):
            vals.append(" ".join(x for x in m.groups() if x)[:80])
        out[name] = sorted(set(vals))[:20]
    return out


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def split_sentences(text: str) -> list[str]:
    text = clean_text(text)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 40]


def summarize_from_section(text: str | None, max_sentences: int = 3) -> str:
    if not text:
        return "not reported"
    sentences = split_sentences(text)
    ranked = sorted(sentences[:80], key=lambda s: score_sentence(s), reverse=True)
    chosen = ranked[:max_sentences] if ranked else sentences[:max_sentences]
    return clean_text(" ".join(chosen))[:1400] if chosen else "not reported"


def score_sentence(s: str) -> float:
    keys = ["radiation", "irradiation", "cascade", "defect", "machine learning", "potential", "high entropy", "tungsten", "fusion", "result", "show"]
    lower = s.lower()
    return sum(1 for k in keys if k in lower) + min(len(s), 220) / 500


def extract_limitations(text: str) -> str:
    sentences = split_sentences(text)
    hits = [s for s in sentences if any(k in s.lower() for k in ["limitation", "limited", "future work", "however", "not considered", "further"])]
    return " ".join(hits[:3])[:1200] if hits else "not explicitly reported"


def assess_value(text: str) -> str:
    lower = text.lower()
    concepts = []
    for label, keys in {
        "HEA/RHEA": ["high entropy", "high-entropy", "rhea", "complex concentrated"],
        "radiation damage": ["radiation damage", "irradiation", "defect"],
        "cascade": ["cascade", "pka", "primary knock"],
        "MLIP": ["machine learning interatomic", "mlip", "gap", "mtp", "nep", "mace", "snap"],
        "fusion relevance": ["fusion", "plasma-facing", "divertor", "first wall"],
    }.items():
        if any(k in lower for k in keys):
            concepts.append(label)
    if len(concepts) >= 4:
        return "directly relevant: " + ", ".join(concepts)
    if len(concepts) >= 2:
        return "useful supporting paper: " + ", ".join(concepts)
    return "background or low-priority unless cited by core papers"


def assess_reusable_data(text: str) -> str:
    lower = text.lower()
    items = []
    if "training" in lower and "data" in lower:
        items.append("possible MLIP training/validation data description")
    if "pka" in lower or "cascade" in lower:
        items.append("cascade settings/results")
    if "supplementary" in lower or "data availability" in lower:
        items.append("supplementary/data availability section")
    return "; ".join(items) if items else "not obvious from extracted text"


def assess_worth(text: str) -> str:
    value = assess_value(text)
    return "yes" if value.startswith("directly") else "maybe"


def evidence_pages(pages: list[dict[str, Any]]) -> dict[str, int]:
    out = {}
    targets = ["machine learning interatomic", "cascade", "irradiation", "high entropy", "fusion", "pka"]
    for page in pages:
        lower = page.get("text", "").lower()
        for t in targets:
            if t in lower and t not in out:
                out[t] = page.get("page")
    return out


def render_card(profile: dict[str, Any]) -> str:
    params = "\n".join(f"- {k}: {', '.join(v) if v else 'not reported'}" for k, v in profile["parameters"].items())
    return f"""# Research Card: {profile['title']}

- 年份: {profile.get('year') or 'unknown'}
- DOI: {profile.get('doi') or 'not reported'}
- 研究类型: {', '.join(profile['research_type'])}
- 材料体系: {', '.join(profile['materials']) if profile['materials'] else 'not reported'}
- 势函数类型: {', '.join(profile['mlip_type']) if profile['mlip_type'] else 'not reported'}
- 方法: {', '.join(profile['methods']) if profile['methods'] else 'not reported'}
- 缺陷关键词: {', '.join(profile['defects']) if profile['defects'] else 'not reported'}
- 真实性验证: {profile.get('authenticity_status', 'not_verified')} ({profile.get('authenticity_score') if profile.get('authenticity_score') is not None else 'n/a'})

## 核心问题
{profile['core_question']}

## 方法
{profile['method_summary']}

## 关键参数
{params}

## 主要发现
{profile['main_findings']}

## 局限
{profile['limitations']}

## 对我的研究价值
{profile['value_for_my_research']}

## 可复用数据
{profile['reusable_data']}

## 是否值得精读
{profile['worth_deep_reading']}

## 证据页码
{profile['evidence_pages']}
"""
