from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from auto_research.paths import DATA_DIR

from .db import EvidenceDB, now
from .quality_pipeline import AdversarialQualityPipeline
from .six_column import resolve_paper_selector
from .test_set import _pdf_status, load_test_set


DEFAULT_CONFIG = Path("config/evidence_test_set_50.json")
DEFAULT_STATE = DATA_DIR / "evidence" / "quality_runs" / "full-corpus-50-v1_batch_state.json"


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def run_quality_test_set(
    db: EvidenceDB,
    *,
    config_path: Path | str | None = None,
    state_path: Path | str | None = None,
    max_pages: int | None = None,
    chunk_pages: int = 4,
    threshold: float = 85.0,
    force_completed: bool = False,
    valid_limit: int | None = None,
) -> dict[str, Any]:
    """Run the adversarial quality gate over a fixed, resumable PDF test set."""

    config = load_test_set(config_path or DEFAULT_CONFIG)
    state_file = Path(state_path or DEFAULT_STATE).expanduser().resolve()
    if state_file.is_file():
        state = json.loads(state_file.read_text(encoding="utf-8"))
        if state.get("config_version") != config.get("version"):
            raise ValueError("resume state belongs to a different test-set version")
    else:
        state = {
            "config_version": config.get("version"),
            "config_path": config["config_path"],
            "provider": "deepseek",
            "pipeline": "adversarial_quality_gate",
            "started_at": now(),
            "papers": {},
        }
    state.update({
        "status": "running",
        "max_pages": max_pages,
        "chunk_pages": chunk_pages,
        "quality_threshold": threshold,
        "valid_limit": valid_limit,
        "updated_at": now(),
    })
    _write_state(state_file, state)

    pipeline = AdversarialQualityPipeline(db)
    valid_seen = 0
    stopped_at_limit = False
    for order, spec in enumerate(config["papers"], start=1):
        selector = str(spec.get("doi") or spec.get("title") or "").strip()
        paper_id = resolve_paper_selector(db, article_key=selector)
        paper = db.get_paper(paper_id) or {}
        key = str(paper_id)
        previous = state["papers"].get(key, {})
        pdf = _pdf_status(db, paper)
        base = {
            "order": order,
            "role": spec.get("role"),
            "paper_id": paper_id,
            "title": paper.get("title"),
            "doi": paper.get("doi"),
            "pdf_sha256": pdf.get("sha256"),
        }
        if not pdf["content_valid"]:
            print(
                f"[{order}/{config['expected_paper_count']}] SKIP invalid PDF: {paper.get('title')}",
                file=sys.stderr, flush=True,
            )
            state["papers"][key] = {
                **base, "status": "skipped_invalid_pdf",
                "reason": "placeholder, missing, unreadable, or insufficient PDF content",
                "updated_at": now(),
            }
            _write_state(state_file, state)
            continue
        valid_seen += 1
        if valid_limit is not None and valid_seen > valid_limit:
            stopped_at_limit = True
            break
        if (
            not force_completed
            and previous.get("status") == "completed"
            and previous.get("pdf_sha256") == pdf.get("sha256")
            and previous.get("max_pages") == max_pages
            and previous.get("chunk_pages") == chunk_pages
            and float(previous.get("quality_threshold", threshold)) == float(threshold)
        ):
            print(
                f"[{order}/{config['expected_paper_count']}] RESUME completed: {paper.get('title')}",
                file=sys.stderr, flush=True,
            )
            continue
        print(
            f"[{order}/{config['expected_paper_count']}] RUN quality gate: {paper.get('title')}",
            file=sys.stderr, flush=True,
        )
        state["papers"][key] = {
            **base, "status": "running", "max_pages": max_pages,
            "chunk_pages": chunk_pages, "quality_threshold": threshold,
            "updated_at": now(),
        }
        state["updated_at"] = now()
        _write_state(state_file, state)
        try:
            result = pipeline.run(
                paper_id,
                max_pages=max_pages,
                chunk_pages=chunk_pages,
                threshold=threshold,
            )
        except Exception as exc:
            print(
                f"[{order}/{config['expected_paper_count']}] FAIL: {exc}",
                file=sys.stderr, flush=True,
            )
            state["papers"][key] = {
                **base, "status": "failed", "max_pages": max_pages,
                "chunk_pages": chunk_pages, "quality_threshold": threshold,
                "error": str(exc)[:1500], "updated_at": now(),
            }
        else:
            summary = result["summary"]
            print(
                f"[{order}/{config['expected_paper_count']}] DONE: "
                f"dual={summary['dual_pass_count']} third={summary['third_pass_count']} "
                f"manual={summary['manual_review_count']}",
                file=sys.stderr, flush=True,
            )
            state["papers"][key] = {
                **base, "status": "completed", "max_pages": max_pages,
                "chunk_pages": chunk_pages, "quality_threshold": threshold,
                "pipeline_run_id": result["pipeline_run_id"],
                "extractor_a_run_id": result["extractor_a_run_id"],
                "extractor_b_run_id": result["extractor_b_run_id"],
                "summary": summary,
                "output_path": result["output_path"],
                "updated_at": now(),
            }
        state["updated_at"] = now()
        _write_state(state_file, state)

    paper_states = list(state["papers"].values())
    completed = [item for item in paper_states if item.get("status") == "completed"]
    failed = [item for item in paper_states if item.get("status") == "failed"]
    totals = {
        "candidates": sum(int(item.get("summary", {}).get("candidate_count", 0)) for item in completed),
        "dual_pass": sum(int(item.get("summary", {}).get("dual_pass_count", 0)) for item in completed),
        "third_pass": sum(int(item.get("summary", {}).get("third_pass_count", 0)) for item in completed),
        "manual_review": sum(int(item.get("summary", {}).get("manual_review_count", 0)) for item in completed),
        "published_items": sum(int(item.get("summary", {}).get("published_item_count", 0)) for item in completed),
        "published_visuals": sum(int(item.get("summary", {}).get("published_visual_count", 0)) for item in completed),
    }
    state["summary"] = {
        "expected": config["expected_paper_count"],
        "target_valid": valid_limit,
        "valid_considered": min(valid_seen, valid_limit) if valid_limit is not None else valid_seen,
        "completed": len(completed),
        "skipped_invalid_pdf": sum(item.get("status") == "skipped_invalid_pdf" for item in paper_states),
        "failed": len(failed),
        **totals,
    }
    if stopped_at_limit:
        state["status"] = "partial_completed" if not failed else "partial_completed_with_failures"
    else:
        state["status"] = "completed" if not failed else "completed_with_failures"
    state["finished_at"] = now()
    state["updated_at"] = now()
    state["state_path"] = str(state_file)
    _write_state(state_file, state)
    return state
