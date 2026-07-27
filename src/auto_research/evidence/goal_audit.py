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
DEFAULT_BUNDLE_DIR = Path("/Users/USER/Zotero/auto-research-backups")
DEFAULT_CORPUS_AUDIT = PROJECT_ROOT / "data" / "evidence" / "test_sets" / "full-corpus-50-v1_audit.json"
INITIAL_COMPLETED_PAPER_TARGET = 30


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


def _corpus_snapshot(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return {
        "path": str(path),
        "version": payload.get("version"),
        "paper_count": int(payload.get("paper_count") or 0),
        "verified_pdf_count": int(payload.get("verified_pdf_count") or 0),
        "ready_paper_count": int(payload.get("ready_paper_count") or 0),
        "pending_extraction_count": int(payload.get("pending_extraction_count") or 0),
        "visual_ready_count": int(payload.get("visual_ready_count") or 0),
        "corpus_integrity_ok": bool(payload.get("corpus_integrity_ok")),
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
        f"- 自动工作流可用：{'是' if report['automatic_ready'] else '否'}",
        f"- 最终目标完成：{'是' if report['goal_complete'] else '否'}",
        f"- 当前主要剩余工作：{report['next_step']}",
        "",
        "## 目标文章",
        "",
        f"- 题目：{paper.get('title')}",
        f"- DOI：{paper.get('doi')}",
        f"- 本地 PDF：`{paper.get('pdf_path')}`",
        f"- 六列数据：{report['summary'].get('row_count')} 条",
        f"- 历史人工修正：{progress['reviewed']}/{progress['total']} 条（不作为自动收录前置条件）",
        "",
        "## 逐条需求审计",
        "",
    ]
    corpus = report.get("corpus")
    if corpus:
        lines[7:7] = [
            f"- 固定语料数据就绪：{corpus['ready_paper_count']}/{corpus['paper_count']} 篇",
            f"- 初始批量验收目标：至少 {INITIAL_COMPLETED_PAPER_TARGET} 篇完成处理",
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
        "2. 上传新 PDF 后运行“对抗式质量提取”，等待双路抽取与第三次低分复核完成。",
        "3. 在“搜索数据”页确认通过质量门的数值、表格、图片和实验结论可检索。",
        "4. 只有发现明显异常时才进入“数据检查”修正；低分候选由系统自动拦截。",
        "",
        "## 机器可读摘要",
        "",
        "```json",
        json.dumps({
            "automatic_ready": report["automatic_ready"],
            "goal_complete": report["goal_complete"],
            "row_count": report["summary"].get("row_count"),
            "unreviewed": progress["unreviewed"],
            "head": git.get("head"),
            "latest_bundle": git.get("latest_bundle"),
            "corpus_ready_papers": corpus.get("ready_paper_count") if corpus else None,
            "initial_completed_paper_target": INITIAL_COMPLETED_PAPER_TARGET,
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
                        bundle_dir: Path = DEFAULT_BUNDLE_DIR,
                        corpus_audit_path: Path | None = DEFAULT_CORPUS_AUDIT) -> dict[str, Any]:
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
    corpus = _corpus_snapshot(corpus_audit_path)
    progress = workflow["summary"]["review_progress"]
    launcher_ok = bool(launcher["exists"] and launcher["executable"])
    git_backup_ok = bool(git["is_git_repo"] and git["head"] and git["latest_bundle"])
    automatic_ready = bool(workflow["ok"] and launcher_ok and git_backup_ok)
    corpus_target_met = bool(
        corpus
        and corpus["corpus_integrity_ok"]
        and corpus["ready_paper_count"] >= INITIAL_COMPLETED_PAPER_TARGET
    )
    goal_complete = automatic_ready and corpus_target_met
    next_step = (
        "自动工作流与至少 30 篇批量语料验收均已通过。"
        if goal_complete
        else (
            f"自动工作流可用，但固定语料仅 {corpus['ready_paper_count'] if corpus else 0}/"
            f"{corpus['paper_count'] if corpus else 0} 篇数据就绪；需完成至少 "
            f"{INITIAL_COMPLETED_PAPER_TARGET} 篇并建立独立金标准。"
        )
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
            corpus_target_met,
            f"至少 {INITIAL_COMPLETED_PAPER_TARGET} 篇真实 PDF 完成数据、证据定位、搜索与图表验收。",
            ([
                f"固定语料：{corpus['paper_count']} 篇",
                f"真实且身份匹配的 PDF：{corpus['verified_pdf_count']} 篇",
                f"数据就绪：{corpus['ready_paper_count']} 篇",
                f"待提取：{corpus['pending_extraction_count']} 篇",
                f"图表就绪：{corpus['visual_ready_count']} 篇",
                f"审计文件：{corpus['path']}",
            ] if corpus else ["未找到固定语料审计文件；不能宣称批量目标完成。"]),
        ),
        _requirement(
            all(item["ok"] for item in workflow["requirements"]),
            "六列编辑、原始保留、自动质量门、多文章搜索和导出符合用户目标。",
            [f"{item['id']}：{'通过' if item['ok'] else '未通过'}" for item in workflow["requirements"]],
        ),
        _requirement(
            launcher_ok,
            "用户可以自行打开本地网页检查自动结果并进行搜索。",
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
            any(item["id"] == "automatic_quality_gate" and item["ok"] for item in workflow["requirements"]),
            "自动质量门替代逐条人工批准，未通过候选不会进入搜索。",
            [
                "两路 DeepSeek 独立抽取并比较。",
                "低分项进入第三次独立复核。",
                "仍低于阈值的候选自动拦截；人工修正仅为可选纠错路径。",
            ],
        ),
    ]
    result = {
        "automatic_ready": automatic_ready,
        "ready_for_human_review": automatic_ready,
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
        "corpus": corpus,
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
