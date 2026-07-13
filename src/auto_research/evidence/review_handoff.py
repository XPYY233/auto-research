from __future__ import annotations

from collections import Counter
import re
from pathlib import Path
from typing import Any

from auto_research.paths import DATA_DIR

from .db import EvidenceDB
from .evidence_audit import audit_six_column_evidence
from .six_column import list_reportable_current_data, resolve_paper_selector, review_progress


REVIEW_HANDOFF_DIR = DATA_DIR / "evidence" / "review_handoffs"
REVIEW_BATCH_DIR = DATA_DIR / "evidence" / "review_batches"
REVIEW_BATCH_STRATEGIES = {"priority", "calibration"}


def _slug(text: str, limit: int = 64) -> str:
    slug = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "-", text).strip("-").lower()
    return (slug or "paper")[:limit].strip("-") or "paper"


def _status(value: bool) -> str:
    return "通过" if value else "需处理"


def _markdown(report: dict[str, Any]) -> str:
    paper = report["paper"]
    summary = report["summary"]
    progress = summary["review_progress"]
    checks = report["checks"]
    requirements = report["requirements"]
    failed_checks = [check for check in checks if not check.get("ok")]
    failed_requirements = [item for item in requirements if not item.get("ok")]
    doi = paper.get("doi") or "未登记 DOI"
    title = paper.get("title") or "未命名文章"
    pdf_path = paper.get("pdf_path") or "未登记 PDF"
    lines = [
        "# 目标文章人工核验交接摘要",
        "",
        "## 当前结论",
        "",
        f"- 状态：{_status(bool(report.get('ok')))}",
        f"- 文章：{title}",
        f"- DOI：{doi}",
        f"- 本地 PDF：`{pdf_path}`",
        f"- 实验类型：{summary.get('primary_experiment_label')}",
        f"- 六列数据：{summary.get('row_count')} 条",
        f"- 原文高亮定位：{summary.get('highlighted_rows')}/{summary.get('automatic_rows')} 条",
        f"- 强定位：{summary.get('strong_rows')} 条",
        "",
        "## 人工核验进度",
        "",
        f"- 已审核：{progress['reviewed']}/{progress['total']} 条（{progress['reviewed_ratio']:.2%}）",
        f"- 待审核：{progress['unreviewed']} 条",
        f"- 已确认：{progress['confirmed']} 条",
        f"- 已修正：{progress['corrected']} 条",
        f"- 人工补录：{progress['manual']} 条",
        "",
        "## 推荐核验流程",
        "",
        "1. 启动本地网页：",
        "",
        "   ```bash",
        "   PYTHONPATH=src python3 -m auto_research.cli evidence-serve",
        "   ```",
        "",
        "2. 打开 `http://127.0.0.1:8765`，进入“校对数据”。",
        "3. 选择“只看未审核”，或点击“下一条未审核”。",
        "4. 对每条数据先看右侧原始抽取值，再用 `Alt+S` 打开高亮原文证据。",
        "5. 若无需修改，用 `Shift+Ctrl/⌘+Enter` 确认并跳到下一条；若需要修改，先编辑左侧六列，再确认。",
        "6. 自动抽取漏掉的数据进入“人工补录”，选择所属文章后手动添加。",
        "",
        "## 六列字段",
        "",
        "- 具体数值",
        "- 具体意义",
        "- 单位",
        "- 文章题目",
        "- DOI",
        "- 数据在文中的解释",
        "",
        "## 快捷键",
        "",
        "- `Ctrl/⌘+Enter`：确认当前行",
        "- `Shift+Ctrl/⌘+Enter`：确认并下一条",
        "- `Alt+N`：跳到下一条未审核",
        "- `Alt+S`：打开当前行原文高亮证据",
        "",
        "## 学习样本与导出",
        "",
        "- 当前文章学习样本：`/api/current-paper/learning-samples.jsonl`",
        "- 全库学习样本：`/api/learning-samples.jsonl`",
        "- 当前文章 CSV/Excel：校对页顶部下载入口",
        "- 全库搜索 CSV/Excel：搜索页导出入口",
        "",
        "## 验收项",
        "",
    ]
    for item in requirements:
        lines.append(f"- {_status(bool(item.get('ok')))}：{item.get('requirement')}")
    if failed_checks or failed_requirements:
        lines.extend(["", "## 需要注意的问题", ""])
        for check in failed_checks:
            lines.append(f"- 检查 `{check.get('name')}` 未通过：{check.get('detail')}")
        for item in failed_requirements:
            lines.append(f"- 需求 `{item.get('id')}` 未通过：{item.get('requirement')}")
    else:
        lines.extend(["", "## 当前阻塞", "", "- 无代码侧阻塞；下一步主要是人工核验剩余数据。"])
    lines.extend([
        "",
        "## 复查命令",
        "",
        "```bash",
        f"PYTHONPATH=src python3 -m auto_research.cli evidence-self-check {doi!r} --query 温度 --query 硬度 --query Wei-Ying --min-rows 100 --min-highlight-ratio 0.8",
        "```",
        "",
    ])
    return "\n".join(lines)


def generate_review_handoff(db: EvidenceDB, selector: str, *,
                            out: Path | None = None,
                            queries: list[str] | None = None,
                            min_rows: int = 100,
                            min_highlight_ratio: float = 0.8) -> dict[str, Any]:
    from .self_check import check_evidence_workflow

    report = check_evidence_workflow(
        db,
        selector,
        queries=queries or ["温度", "硬度", "Wei-Ying Chen"],
        min_rows=min_rows,
        min_highlight_ratio=min_highlight_ratio,
    )
    paper = report["paper"]
    target = out
    if target is None:
        REVIEW_HANDOFF_DIR.mkdir(parents=True, exist_ok=True)
        target = REVIEW_HANDOFF_DIR / f"{paper['id']}_{_slug(paper.get('title') or selector)}.md"
    else:
        target = target.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_markdown(report), encoding="utf-8")
    return {
        "ok": bool(report["ok"]),
        "path": str(target),
        "paper": report["paper"],
        "summary": report["summary"],
        "failed_checks": [check["name"] for check in report["checks"] if not check["ok"]],
        "failed_requirements": [item["id"] for item in report["requirements"] if not item["ok"]],
    }


def _value_shape(value: str) -> str:
    text = str(value or "").strip()
    if not re.search(r"\d", text):
        return "qualitative"
    if "±" in text or re.search(r"\+\s*/\s*-", text):
        return "uncertainty"
    if re.search(r"[,;，；]", text):
        return "vector"
    if re.search(r"(?:[<>≤≥~≈]|\d\s*[-–—]\s*\d)", text):
        return "range_or_limit"
    return "scalar"


def _locator_kind(row: dict[str, Any]) -> str:
    locator = str(row.get("original_source_locator") or row.get("source_locator") or "")
    if re.search(r"\btable\b", locator, re.I):
        return "table"
    if re.search(r"\b(?:fig(?:ure)?s?\.?|图)\b", locator, re.I):
        return "figure"
    if re.search(r"\b(?:eq(?:uation)?\.?|公式)\b", locator, re.I):
        return "equation"
    if re.search(r"\b(?:section|sec\.?)\b", locator, re.I):
        return "section"
    return "other"


def _semantic_family(row: dict[str, Any]) -> str:
    stable_key = str(row.get("stable_key") or "").lower()
    if stable_key.startswith("comp_") or stable_key.startswith("table1_"):
        return "composition"
    if stable_key.startswith("table2_"):
        return "microstructure"
    if stable_key.startswith("table3_"):
        return "mechanical"
    if stable_key.startswith("table4_") or stable_key.startswith(("orowan_", "srim_")):
        return "calculation"
    text = " ".join(str(row.get(field) or "") for field in (
        "stable_key", "meaning", "context_explanation", "source_excerpt",
    )).lower()
    families = (
        ("composition", ("成分", "原子分数", "composition", "at%", "comp_")),
        ("microstructure", ("晶粒", "位错", "夹杂", "空洞", "析出", "微观", "loop", "void")),
        ("mechanical", ("硬度", "硬化", "压痕", "载荷", "屈服", "hardness", "indent")),
        ("calculation", ("计算", "模型", "混合焓", "混合熵", "失配", "参数ω", "orowan", "srim")),
        ("preparation", ("制备", "轧", "退火", "均匀化", "固溶", "抛光", "试样", "specimen")),
        ("irradiation", ("辐照", "dpa", "注量", "通量", "kr离子", "irradiat", "dose")),
    )
    for family, keywords in families:
        if any(keyword in text for keyword in keywords):
            return family
    return "other"


def _candidate_role(row: dict[str, Any]) -> str:
    """A sampling hint, never a published evidence classification."""

    value_shape = _value_shape(str(row.get("value_text") or ""))
    if value_shape == "qualitative":
        return "qualitative"
    stable_key = str(row.get("stable_key") or "").lower()
    text = " ".join(str(row.get(field) or "") for field in (
        "meaning", "context_explanation", "source_excerpt",
    )).lower()
    if stable_key.startswith(("table4_", "orowan_", "srim_")) or any(
        keyword in text for keyword in ("模型计算", "由文章给出的材料成分", "srim计算", "calculated")
    ):
        return "calculated"
    if any(keyword in text for keyword in ("增量", "倍数", "关系常数", "终止深度", "趋于饱和")):
        return "derived_or_interpreted"
    return "direct_or_reported_numeric"


def _calibration_facets(row: dict[str, Any]) -> dict[str, str]:
    page = row.get("original_source_page") or row.get("source_page") or "unknown"
    return {
        "source_kind": str(row.get("source_kind") or "text"),
        "locator_kind": _locator_kind(row),
        "value_shape": _value_shape(str(row.get("value_text") or "")),
        "candidate_role": _candidate_role(row),
        "semantic_family": _semantic_family(row),
        "page": str(page),
        "priority": str(row.get("review_priority_level") or "normal"),
    }


def _select_calibration_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Greedily cover rare evidence forms before taking near-duplicate rows."""

    facet_weights = {
        "source_kind": 8.0,
        "locator_kind": 7.0,
        "value_shape": 6.0,
        "candidate_role": 6.0,
        "semantic_family": 6.0,
        "page": 3.0,
        "priority": 2.0,
    }
    frequencies: dict[tuple[str, str], int] = Counter()
    for row in rows:
        row["calibration_facets"] = _calibration_facets(row)
        for name, value in row["calibration_facets"].items():
            frequencies[(name, value)] += 1

    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    seen_meanings: set[str] = set()
    remaining = list(rows)
    while remaining and len(selected) < max(0, limit):
        def marginal(row: dict[str, Any]) -> tuple[float, int, int]:
            novelty = sum(
                facet_weights[name] / max(1, frequencies[(name, value)]) ** 0.5
                for name, value in row["calibration_facets"].items()
                if (name, value) not in seen
            )
            priority = int(row.get("review_priority_score") or 0)
            meaning = re.sub(r"\W+", "", str(row.get("meaning") or "").lower(), flags=re.UNICODE)
            repeat_penalty = 3.0 if meaning and meaning in seen_meanings else 0.0
            return novelty + priority * 0.04 - repeat_penalty, priority, -int(row["item_id"])

        chosen = max(remaining, key=marginal)
        remaining.remove(chosen)
        selected.append(chosen)
        seen.update((name, value) for name, value in chosen["calibration_facets"].items())
        meaning = re.sub(r"\W+", "", str(chosen.get("meaning") or "").lower(), flags=re.UNICODE)
        if meaning:
            seen_meanings.add(meaning)
    return selected


def _calibration_summary(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    summary: dict[str, Counter[str]] = {}
    for row in rows:
        for name, value in (row.get("calibration_facets") or {}).items():
            summary.setdefault(name, Counter())[value] += 1
    return {name: dict(values) for name, values in summary.items()}


def _review_batch_markdown(paper: dict[str, Any], progress: dict[str, Any],
                           rows: list[dict[str, Any]], limit: int,
                           strategy: str = "priority") -> str:
    is_calibration = strategy == "calibration"
    lines = [
        "# 分层校准核验清单" if is_calibration else "# 下一批待审核数据清单",
        "",
        f"- 文章：{paper.get('title') or '未命名文章'}",
        f"- DOI：{paper.get('doi') or '未登记 DOI'}",
        f"- 本批数量：{len(rows)} 条（请求上限 {limit} 条）",
        f"- 取样方式：{'分层校准取样' if is_calibration else '核验优先级排序'}",
        f"- 总待审核：{progress['unreviewed']} 条",
        f"- 已审核：{progress['reviewed']}/{progress['total']} 条",
        "",
        "## 使用方式",
        "",
        "1. 启动 `evidence-serve` 并打开校对页。",
        "2. 按下方 `item_id` 在页面中定位，或点击“下一条未审核”逐条前进。",
        "3. 用 `Alt+S` 打开原文证据；确认无误后用 `Shift+Ctrl/⌘+Enter` 保存并进入下一条。",
        "",
    ]
    if is_calibration:
        summary = _calibration_summary(rows)
        lines.extend([
            "本清单优先覆盖不同证据来源、数值形态、物理类别和页面，适合用较少反馈校准后续自动抽取；它不是对候选可靠性的自动判定。",
            "",
            "## 本批覆盖",
            "",
            f"- 证据来源：{summary.get('source_kind', {})}",
            f"- 原文位置：{summary.get('locator_kind', {})}",
            f"- 数值形态：{summary.get('value_shape', {})}",
            f"- 候选角色：{summary.get('candidate_role', {})}（仅用于抽样覆盖，不替代人工判定）",
            f"- 物理类别：{summary.get('semantic_family', {})}",
            f"- PDF 页面：{summary.get('page', {})}",
            "",
        ])
    lines.extend([
        "## 本批待审核数据",
        "",
    ])
    if not rows:
        lines.append("- 当前没有未审核自动抽取数据。")
    for index, row in enumerate(rows, start=1):
        priority_reasons = "；".join(row.get("review_priority_reasons") or []) or "常规证据核验"
        lines.extend([
            f"### {index}. item_id={row['item_id']} · {row.get('meaning') or '未命名数据'}",
            "",
            f"- 核验优先级：{row.get('review_priority_label') or '常规核验'}（{priority_reasons}）",
            *( [f"- 校准覆盖：{row.get('calibration_facets') or {}}"] if is_calibration else [] ),
            f"- 具体数值：`{row.get('value_text') or ''}`",
            f"- 具体意义：{row.get('meaning') or ''}",
            f"- 单位：{row.get('unit') or ''}",
            f"- 文章题目：{row.get('article_title') or ''}",
            f"- DOI：{row.get('doi') or ''}",
            f"- 数据在文中的解释：{row.get('context_explanation') or ''}",
            f"- 证据位置：PDF 第 {row.get('original_source_page') or row.get('source_page') or '?'} 页；{row.get('original_source_locator') or row.get('source_locator') or '未标注'}",
            f"- 原文片段：{row.get('original_source_excerpt') or row.get('source_excerpt') or ''}",
            f"- 本地证据接口：`/api/six-data/{row['item_id']}/source-view`",
            "",
            "核验记录：- [ ] 确认无误  - [ ] 已修正  - [ ] 存在歧义  - [ ] 不采用  - [ ] 需要人工补录/备注",
            "",
        ])
    return "\n".join(lines)


def review_batch_payload(db: EvidenceDB, paper_id: int, *,
                         limit: int = 20,
                         strategy: str = "priority") -> dict[str, Any]:
    if strategy not in REVIEW_BATCH_STRATEGIES:
        raise ValueError(f"unsupported review batch strategy: {strategy}")
    paper = db.get_paper(paper_id) or {}
    rows = [
        row for row in list_reportable_current_data(db, paper_id)
        if row.get("origin_type") != "manual" and row.get("review_action") == "automatic"
    ]
    try:
        audit = audit_six_column_evidence(db, paper_id)
        priorities = {int(item["item_id"]): item for item in audit.get("review_priority_rows", [])}
    except Exception:
        priorities = {}
    for row in rows:
        priority = priorities.get(int(row["item_id"]), {})
        row["review_priority_score"] = int(priority.get("score") or 0)
        row["review_priority_level"] = priority.get("level") or "normal"
        row["review_priority_label"] = priority.get("label") or "常规核验"
        row["review_priority_reasons"] = priority.get("reasons") or []
    rows.sort(key=lambda row: (-int(row["review_priority_score"]), int(row["item_id"])))
    selected = (
        _select_calibration_rows(rows, limit)
        if strategy == "calibration"
        else rows[:max(0, limit)]
    )
    progress = review_progress(db, paper_id)
    return {
        "ok": True,
        "strategy": strategy,
        "paper": {"id": paper_id, "title": paper.get("title"), "doi": paper.get("doi")},
        "batch_count": len(selected),
        "remaining_unreviewed": progress["unreviewed"],
        "first_item_id": selected[0]["item_id"] if selected else None,
        "last_item_id": selected[-1]["item_id"] if selected else None,
        "calibration_summary": _calibration_summary(selected) if strategy == "calibration" else {},
        "selected_item_ids": [int(row["item_id"]) for row in selected],
        "markdown": _review_batch_markdown(paper, progress, selected, limit, strategy),
    }


def generate_review_batch(db: EvidenceDB, selector: str, *,
                          limit: int = 20,
                          strategy: str = "priority",
                          out: Path | None = None) -> dict[str, Any]:
    paper_id = resolve_paper_selector(db, article_key=selector)
    payload = review_batch_payload(db, paper_id, limit=limit, strategy=strategy)
    paper = payload["paper"]
    target = out
    if target is None:
        REVIEW_BATCH_DIR.mkdir(parents=True, exist_ok=True)
        batch_label = "calibration" if strategy == "calibration" else "next"
        target = REVIEW_BATCH_DIR / f"{paper_id}_{_slug(paper.get('title') or selector)}_{batch_label}{limit}.md"
    else:
        target = target.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload["markdown"], encoding="utf-8")
    return {
        "ok": True,
        "strategy": strategy,
        "path": str(target),
        "paper": paper,
        "batch_count": payload["batch_count"],
        "remaining_unreviewed": payload["remaining_unreviewed"],
        "first_item_id": payload["first_item_id"],
        "last_item_id": payload["last_item_id"],
        "selected_item_ids": payload["selected_item_ids"],
        "calibration_summary": payload["calibration_summary"],
    }
