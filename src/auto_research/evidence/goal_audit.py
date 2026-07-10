from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from auto_research.paths import DATA_DIR

from .db import EvidenceDB
from .self_check import check_evidence_workflow


PROJECT_ROOT = Path(__file__).resolve().parents[3]
GOAL_AUDIT_DIR = DATA_DIR / "evidence" / "goal_audits"
DEFAULT_BUNDLE_DIR = Path("/Users/USER/Zotero/auto-research-git-backups")


def _slug(text: str, limit: int = 72) -> str:
    import re

    slug = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "-", text).strip("-").lower()
    return (slug or "paper")[:limit].strip("-") or "paper"


def _run_git(args: list[str], project_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=project_root,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return result.stdout.strip()


def _git_snapshot(project_root: Path, bundle_dir: Path) -> dict[str, Any]:
    project_root = project_root.expanduser().resolve()
    bundle_dir = bundle_dir.expanduser()
    bundles = sorted(
        bundle_dir.glob("auto-research-*.bundle"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ) if bundle_dir.exists() else []
    status_lines = [
        line for line in _run_git(["status", "--short"], project_root).splitlines()
        if line.strip()
    ]
    return {
        "project_root": str(project_root),
        "is_git_repo": (project_root / ".git").exists(),
        "head": _run_git(["rev-parse", "--short", "HEAD"], project_root),
        "tags_at_head": _run_git(["tag", "--points-at", "HEAD"], project_root).splitlines(),
        "dirty_paths": status_lines,
        "bundle_dir": str(bundle_dir),
        "latest_bundle": str(bundles[0]) if bundles else None,
        "bundle_count": len(bundles),
    }


def _launcher_snapshot(project_root: Path) -> dict[str, Any]:
    launcher = project_root / "scripts" / "start_evidence_ui.command"
    return {
        "path": str(launcher),
        "exists": launcher.is_file(),
        "executable": launcher.is_file() and bool(launcher.stat().st_mode & 0o111),
        "url": "http://127.0.0.1:8765",
    }


def _requirement(ok: bool, requirement: str, evidence: list[str],
                 *, status: str | None = None) -> dict[str, Any]:
    return {
        "ok": bool(ok),
        "status": status or ("satisfied" if ok else "needs_work"),
        "requirement": requirement,
        "evidence": evidence,
    }


def _markdown(report: dict[str, Any]) -> str:
    paper = report["paper"]
    progress = report["review_progress"]
    git = report["git"]
    launcher = report["launcher"]
    lines = [
        "# 自动化实验数据提取目标审计",
        "",
        "## 结论",
        "",
        f"- 可开始人工核验：{'是' if report['ready_for_human_review'] else '否'}",
        f"- 最终目标完成：{'是' if report['goal_complete'] else '否'}",
        f"- 当前主要剩余工作：{report['next_step']}",
        "",
        "## 目标文章",
        "",
        f"- 题目：{paper.get('title')}",
        f"- DOI：{paper.get('doi')}",
        f"- 本地 PDF：`{paper.get('pdf_path')}`",
        f"- 六列数据：{report['summary'].get('row_count')} 条",
        f"- 审核进度：{progress['reviewed']}/{progress['total']} 条；待审核 {progress['unreviewed']} 条",
        "",
        "## 逐条需求审计",
        "",
    ]
    for item in report["goal_requirements"]:
        mark = "通过" if item["ok"] else "未完成"
        lines.append(f"### {mark} · {item['requirement']}")
        lines.append("")
        for evidence in item["evidence"]:
            lines.append(f"- {evidence}")
        lines.append("")
    lines.extend([
        "## 启动与备份",
        "",
        f"- 本地网页：{launcher['url']}",
        f"- 双击启动脚本：`{launcher['path']}`",
        f"- Git HEAD：`{git.get('head') or 'unknown'}`",
        f"- HEAD 标签：{', '.join(git.get('tags_at_head') or []) or '无'}",
        f"- 最新 bundle：`{git.get('latest_bundle') or '未找到'}`",
        "",
        "## 推荐下一步",
        "",
        "1. 双击 `scripts/start_evidence_ui.command` 或打开 `http://127.0.0.1:8765`。",
        "2. 在“校对数据”页点击“下一条未审核”。",
        "3. 每条数据用右侧原始记录和 `Alt+S` 高亮原文核对。",
        "4. 确认、修正或人工补录；这些人工样本会进入后续 DeepSeek 抽取优化。",
        "",
        "## 机器可读摘要",
        "",
        "```json",
        json.dumps({
            "ready_for_human_review": report["ready_for_human_review"],
            "goal_complete": report["goal_complete"],
            "row_count": report["summary"].get("row_count"),
            "unreviewed": progress["unreviewed"],
            "head": git.get("head"),
            "latest_bundle": git.get("latest_bundle"),
        }, ensure_ascii=False, indent=2),
        "```",
        "",
    ])
    return "\n".join(lines)


def generate_goal_audit(db: EvidenceDB, selector: str, *,
                        out: Path | None = None,
                        queries: list[str] | None = None,
                        min_rows: int = 100,
                        min_highlight_ratio: float = 0.8,
                        project_root: Path = PROJECT_ROOT,
                        bundle_dir: Path = DEFAULT_BUNDLE_DIR) -> dict[str, Any]:
    """Generate a user-objective audit without calling AI or mutating data."""

    workflow = check_evidence_workflow(
        db,
        selector,
        queries=queries or ["温度", "硬度", "Wei-Ying Chen"],
        min_rows=min_rows,
        min_highlight_ratio=min_highlight_ratio,
    )
    git = _git_snapshot(project_root, bundle_dir)
    launcher = _launcher_snapshot(project_root)
    progress = workflow["summary"]["review_progress"]
    launcher_ok = bool(launcher["exists"] and launcher["executable"])
    git_backup_ok = bool(git["is_git_repo"] and git["head"] and git["latest_bundle"])
    human_review_complete = progress["total"] > 0 and progress["unreviewed"] == 0
    ready_for_human_review = bool(workflow["ok"] and launcher_ok and git_backup_ok)
    goal_complete = bool(ready_for_human_review and human_review_complete)
    next_step = (
        "目标文章全部数据已人工核验，可进入下一轮跨文章自动化验证。"
        if goal_complete
        else f"继续人工核验剩余 {progress['unreviewed']} 条自动抽取数据，并把确认/修正样本用于优化抽取。"
        if ready_for_human_review
        else "先修复审计报告中未通过的功能项，再开始集中人工核验。"
    )
    requirements = [
        _requirement(
            workflow["ok"],
            "指定文章标识后，系统能解析本地真实 PDF，并给出可追溯的六列真实数据候选。",
            [
                f"选择器：{selector}",
                f"文章：{workflow['paper'].get('title')}",
                f"PDF：{workflow['paper'].get('pdf_path')}",
                f"六列数据：{workflow['summary'].get('row_count')} 条",
                f"原文高亮：{workflow['summary'].get('highlighted_rows')} 条",
            ],
        ),
        _requirement(
            all(item["ok"] for item in workflow["requirements"]),
            "六列编辑、原始保留、人工补录、自由关键词搜索和学习闭环符合用户目标。",
            [f"{item['id']}：{'通过' if item['ok'] else '未通过'}" for item in workflow["requirements"]],
        ),
        _requirement(
            launcher_ok,
            "用户可以自行打开本地网页进行人工核验。",
            [
                f"网页地址：{launcher['url']}",
                f"启动脚本存在：{launcher['exists']}",
                f"启动脚本可执行：{launcher['executable']}",
            ],
        ),
        _requirement(
            git_backup_ok,
            "项目已有 Git 历史版本和 bundle 备份。",
            [
                f"Git 仓库：{git['is_git_repo']}",
                f"HEAD：{git.get('head') or 'unknown'}",
                f"最新 bundle：{git.get('latest_bundle') or '未找到'}",
            ],
        ),
        _requirement(
            human_review_complete,
            "目标文章候选数据已经完成用户人工核验。",
            [
                f"已审核：{progress['reviewed']}/{progress['total']}",
                f"待审核：{progress['unreviewed']}",
                "这是最终自动化优化前的必要人工反馈，不应由程序替代。",
            ],
            status="pending_user_review" if not human_review_complete else "satisfied",
        ),
    ]
    result = {
        "ready_for_human_review": ready_for_human_review,
        "goal_complete": goal_complete,
        "next_step": next_step,
        "selector": selector,
        "paper": workflow["paper"],
        "summary": workflow["summary"],
        "review_progress": progress,
        "workflow_ok": workflow["ok"],
        "workflow_requirements": workflow["requirements"],
        "goal_requirements": requirements,
        "launcher": launcher,
        "git": git,
    }
    target = out
    if target is None:
        GOAL_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        target = GOAL_AUDIT_DIR / f"{workflow['paper']['id']}_{_slug(workflow['paper'].get('title') or selector)}_goal_audit.md"
    else:
        target = target.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_markdown(result), encoding="utf-8")
    result["path"] = str(target)
    return result
