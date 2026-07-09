from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from auto_research.paths import DATA_DIR

from .db import EvidenceDB
from .self_check import check_evidence_workflow


REVIEW_HANDOFF_DIR = DATA_DIR / "evidence" / "review_handoffs"


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
