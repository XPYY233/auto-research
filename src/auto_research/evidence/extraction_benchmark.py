from __future__ import annotations

from .candidate_matching import (
    MATCH_THRESHOLD,
    EXACT_THRESHOLD,
    NUMBER_WORDS,
    _text,
    _compact,
    _numbers,
    _unit,
    _tokens,
    _similarity,
    _scope_tokens,
    _scope_similarity,
    _value_score,
    _value_without_repeated_unit,
    _observation_signature,
    _observation_value_score,
    score_pair,
    _category,
    _maximum_cardinality_edges,
    compare_candidates,
)

import difflib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from auto_research.paths import DATA_DIR

from .db import EvidenceDB
from .six_column import list_current_data, resolve_paper_selector


BENCHMARK_DIR = DATA_DIR / "evidence" / "benchmarks"


def _artifact_for_run(db: EvidenceDB, paper_id: int, run_id: int | None,
                      artifact_path: Path | None) -> tuple[Path, dict[str, Any]]:
    if artifact_path:
        path = Path(artifact_path).expanduser().resolve()
    else:
        runs = db.list_ai_extraction_runs(paper_id=paper_id, limit=100)
        if run_id is not None:
            run = next((item for item in runs if int(item["id"]) == int(run_id)), None)
        else:
            run = next((item for item in runs if item.get("status") == "completed" and item.get("output_path")), None)
        if not run or not run.get("output_path"):
            raise FileNotFoundError("No completed DeepSeek artifact found for this paper")
        path = Path(run["output_path"])
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload.get("verified_candidates"), list):
        raise ValueError("DeepSeek artifact has no verified_candidates list")
    return path, payload


def benchmark_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    replay = report["postprocessing_replay"]
    lines = [
        f"# 自动抽取基准报告：{report['paper']['title']}",
        "",
        f"- DOI：{report['paper'].get('doi') or '未记录'}",
        f"- DeepSeek 运行：{report.get('run_id') or '外部产物'}",
        f"- 产物：`{report['artifact_path']}`",
        f"- 基准数据：{summary['baseline_count']} 条（已人工审核 {summary['reviewed_baseline_count']}，未审核 {summary['unreviewed_baseline_count']}）",
        f"- 产物内已验证候选：{replay['raw_candidate_count']} 条",
        f"- 按当前证据门离线回放：{replay['gate_passed_count']} 条（排除 {replay['gate_rejected_count']} 条非原子/不合格候选）",
        f"- 按当前去重规则离线回放：{summary['candidate_count']} 条（合并 {replay['duplicate_removed_count']} 条重复表述）",
        f"- 一对一匹配：{summary['candidate_match_count']} 条，其中严格匹配 {summary['exact_match_count']}、部分匹配 {summary['partial_match_count']}",
        f"- 基准覆盖率：{summary['baseline_coverage_rate']:.1%}",
        f"- 候选一致率：{summary['candidate_agreement_rate']:.1%}",
        f"- 未匹配候选：{summary['unmatched_candidate_count']} 条；未覆盖基准：{summary['uncovered_baseline_count']} 条",
        "",
        "> 注意：当前基准仍有未审核数据。这里的“覆盖率/一致率”用于比较抽取版本，不等同于科学准确率；未匹配候选可能是新增真数据，也可能是误提取，必须人工核验。",
        "> 离线回放只重跑本地确定性证据门、去重和基准比对，不调用 DeepSeek，也不改写原运行产物。",
        "",
        "## 分类型覆盖",
        "",
        "| 数据类型 | 覆盖 | 基准总数 | 覆盖率 |",
        "|---|---:|---:|---:|",
    ]
    for category, item in report["category_coverage"].items():
        lines.append(f"| {category} | {item['covered']} | {item['baseline']} | {item['coverage_rate']:.1%} |")
    lines.extend(["", "## 优先人工检查的未匹配候选", ""])
    if not report["unmatched_candidates"]:
        lines.append("- 无")
    else:
        for item in report["unmatched_candidates"][:30]:
            lines.append(
                f"- `{item.get('candidate_id') or item['candidate_index']}`：{item.get('value_text')} {item.get('unit') or ''}；"
                f"{item.get('meaning')}；PDF 第 {item.get('source_page') or '?'} 页；{item.get('source_locator') or '未标注'}"
            )
    lines.extend(["", "## 尚未覆盖的基准数据", ""])
    if not report["uncovered_baseline"]:
        lines.append("- 无")
    else:
        for row in report["uncovered_baseline"][:30]:
            lines.append(
                f"- `#{row.get('item_id')}` `{row.get('stable_key')}`：{row.get('value_text')} {row.get('unit') or ''}；"
                f"{row.get('meaning')}；PDF 第 {row.get('source_page') or '?'} 页"
            )
    return "\n".join(lines) + "\n"


def _replay_candidates(db: EvidenceDB, paper_id: int, paper: dict[str, Any],
                       raw_candidates: list[dict[str, Any]]) -> dict[str, Any]:
    baseline = list_current_data(db, paper_id)
    raw_summary = compare_candidates(baseline, [dict(item) for item in raw_candidates])["summary"]
    # Import locally to avoid a module-level cycle: the runtime extractor uses
    # this benchmark matcher after its own deterministic post-processing.
    from .deepseek_extraction import _deduplicate, _evidence_check, _read_pages

    pdf_path = Path(str(paper.get("pdf_path") or ""))
    page_text = {item["page"]: item["text"] for item in _read_pages(pdf_path)} if pdf_path.is_file() else {}
    gate_passed: list[dict[str, Any]] = []
    gate_rejected: list[dict[str, Any]] = []
    for item in raw_candidates:
        check = _evidence_check(item, page_text.get(int(item.get("source_page") or 0), ""))
        if check.get("passed"):
            gate_passed.append(dict(item))
        else:
            gate_rejected.append({
                "candidate_id": item.get("candidate_id"),
                "value_text": item.get("value_text"),
                "meaning": item.get("meaning"),
                "reason": check.get("reason"),
            })
    candidates = _deduplicate(gate_passed)
    comparison = compare_candidates(baseline, candidates)
    return {
        "candidates": candidates,
        "comparison": comparison,
        "postprocessing_replay": {
            "raw_candidate_count": len(raw_candidates),
            "gate_passed_count": len(gate_passed),
            "gate_rejected_count": len(gate_rejected),
            "current_candidate_count": len(candidates),
            "duplicate_removed_count": len(gate_passed) - len(candidates),
            "total_removed_count": len(raw_candidates) - len(candidates),
            "raw_candidate_agreement_rate": raw_summary["candidate_agreement_rate"],
            "current_candidate_agreement_rate": comparison["summary"]["candidate_agreement_rate"],
            "artifact_unchanged": True,
            "gate_rejected_candidates": gate_rejected,
        },
    }


def _write_benchmark_report(report: dict[str, Any], destination: Path, stem: str) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / f"{stem}.json"
    markdown_path = destination / f"{stem}.md"
    report["json_path"] = str(json_path)
    report["markdown_path"] = str(markdown_path)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(benchmark_markdown(report), encoding="utf-8")
    return report


def benchmark_extraction_run(db: EvidenceDB, article_key: str, *, run_id: int | None = None,
                             artifact_path: Path | None = None,
                             out_dir: Path | None = None) -> dict[str, Any]:
    paper_id = resolve_paper_selector(db, article_key=article_key)
    paper = db.get_paper(paper_id)
    if not paper:
        raise KeyError(f"Paper {paper_id} not found")
    artifact, payload = _artifact_for_run(db, paper_id, run_id, artifact_path)
    raw_candidates = [dict(item) for item in payload["verified_candidates"] if isinstance(item, dict)]
    replay = _replay_candidates(db, paper_id, paper, raw_candidates)
    report = {
        "paper": {"id": paper_id, "title": paper["title"], "doi": paper.get("doi")},
        "run_id": payload.get("run_id"),
        "artifact_path": str(artifact),
        "generated_without_model_call": True,
        "postprocessing_replay": replay["postprocessing_replay"],
        **replay["comparison"],
    }
    stem = f"paper_{paper_id:03d}_run_{int(payload.get('run_id') or 0):04d}_benchmark"
    return _write_benchmark_report(report, Path(out_dir or BENCHMARK_DIR), stem)


def benchmark_ensemble_preview(db: EvidenceDB, article_key: str, *, primary_run_id: int,
                               supplemental_run_ids: list[int],
                               supplemental_focus: str | list[str] = "coverage_gap_audit",
                               supplemental_focus_by_run: dict[int, list[str]] | None = None,
                               primary_artifact_path: Path | None = None,
                               supplemental_artifact_paths: dict[int, Path] | None = None,
                               out_dir: Path | None = None) -> dict[str, Any]:
    """Build a non-overwriting primary-run plus focused-supplement preview."""

    focus_selectors = (
        [supplemental_focus]
        if isinstance(supplemental_focus, str)
        else list(supplemental_focus)
    )
    focus_selectors = [str(item).strip() for item in focus_selectors if str(item).strip()]
    if not focus_selectors:
        focus_selectors = ["coverage_gap_audit"]

    def focus_selected(item: dict[str, Any], selectors: list[str]) -> bool:
        focus = str(item.get("extraction_focus") or "")
        candidate_text = _text(
            f"{item.get('meaning') or ''} {item.get('context_explanation') or ''} "
            f"{item.get('source_excerpt') or ''}"
        )
        is_composition_table = (
            bool(re.search(
                r"\b(?:fe|cr|ni|mn|co|al|mo|si|c|n|v|p|s|b)\b|元素",
                candidate_text,
            ))
            and bool(re.search(
                r"\b(?:nominal|measured|eds|composition)\b|名义|实测|测量|成分|原子分数",
                candidate_text,
            ))
            and (
                _unit(item.get("unit")) in {"at", "atomic"}
                or bool(re.search(r"\bat\s*%|atomic percent|原子百分比", candidate_text))
            )
        )
        aliases = {
            "coverage_gap_audit": lambda: focus == "coverage_gap_audit",
            "results": lambda: focus.startswith("Focus on experimental results"),
            "qualitative_results": lambda: (
                focus.startswith("Focus on experimental results")
                and item.get("evidence_type") == "qualitative"
            ),
            "methods": lambda: focus.startswith("Focus on experimental setup"),
            "composition_table": lambda: is_composition_table,
            "targeted": lambda: focus.startswith("Targeted experiment-specific pass"),
            "all": lambda: True,
        }
        return any(aliases.get(selector, lambda selector=selector: focus == selector)()
                   for selector in selectors)

    paper_id = resolve_paper_selector(db, article_key=article_key)
    paper = db.get_paper(paper_id)
    if not paper:
        raise KeyError(f"Paper {paper_id} not found")
    primary_path, primary_payload = _artifact_for_run(
        db, paper_id, primary_run_id, primary_artifact_path
    )
    raw_candidates: list[dict[str, Any]] = []
    for item in primary_payload["verified_candidates"]:
        if isinstance(item, dict):
            candidate = dict(item)
            candidate["ensemble_source_run"] = primary_run_id
            raw_candidates.append(candidate)
    artifact_paths = [str(primary_path)]
    supplemental_counts: dict[str, int] = {}
    effective_focuses_by_run: dict[str, list[str]] = {}
    for run_id in supplemental_run_ids:
        path, payload = _artifact_for_run(
            db, paper_id, run_id, (supplemental_artifact_paths or {}).get(run_id)
        )
        artifact_paths.append(str(path))
        run_focuses = list((supplemental_focus_by_run or {}).get(run_id) or focus_selectors)
        effective_focuses_by_run[str(run_id)] = run_focuses
        selected = [
            item for item in payload["verified_candidates"]
            if isinstance(item, dict) and focus_selected(item, run_focuses)
        ]
        supplemental_counts[str(run_id)] = len(selected)
        for item in selected:
            candidate = dict(item)
            candidate["ensemble_source_run"] = run_id
            raw_candidates.append(candidate)
    replay = _replay_candidates(db, paper_id, paper, raw_candidates)
    run_label = f"ensemble {primary_run_id}+" + "+".join(str(run_id) for run_id in supplemental_run_ids)
    report = {
        "paper": {"id": paper_id, "title": paper["title"], "doi": paper.get("doi")},
        "run_id": run_label,
        "artifact_path": "；".join(artifact_paths),
        "artifact_paths": artifact_paths,
        "generated_without_model_call": True,
        "ensemble": {
            "primary_run_id": primary_run_id,
            "supplemental_run_ids": supplemental_run_ids,
            "supplemental_focus": focus_selectors[0] if len(focus_selectors) == 1 else ",".join(focus_selectors),
            "supplemental_focuses": focus_selectors,
            "supplemental_focuses_by_run": effective_focuses_by_run,
            "supplemental_candidate_counts": supplemental_counts,
            "database_rows_changed": 0,
        },
        "ensemble_candidates": replay["candidates"],
        "postprocessing_replay": replay["postprocessing_replay"],
        **replay["comparison"],
    }
    suffix = "_".join(f"{run_id:04d}" for run_id in supplemental_run_ids)
    focus_slug = "_".join(re.sub(r"[^a-z0-9_-]+", "-", item.casefold()).strip("-")
                          for item in focus_selectors)
    if supplemental_focus_by_run:
        focus_slug = "_".join(
            f"r{run_id}-" + "-".join(
                re.sub(r"[^a-z0-9_-]+", "-", item.casefold()).strip("-")
                for item in effective_focuses_by_run[str(run_id)]
            )
            for run_id in supplemental_run_ids
        )
    stem = f"paper_{paper_id:03d}_ensemble_{primary_run_id:04d}_plus_{suffix}_{focus_slug}"
    return _write_benchmark_report(report, Path(out_dir or BENCHMARK_DIR), stem)
