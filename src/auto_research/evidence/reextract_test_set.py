from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .db import EvidenceDB, now
from .deepseek_extraction import DeepSeekEvidenceExtractor
from .six_column import resolve_paper_selector
from .test_set import DEFAULT_CONFIG, DEFAULT_OUTPUT_DIR, _pdf_status, load_test_set


DEFAULT_STATE = DEFAULT_OUTPUT_DIR / "full-corpus-35-v1_reextract.json"


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def reextract_test_set(
    db: EvidenceDB,
    *,
    config_path: Path | str | None = None,
    state_path: Path | str | None = None,
    max_pages: int | None = None,
    chunk_pages: int = 4,
    force_completed: bool = False,
    valid_limit: int | None = None,
) -> dict[str, Any]:
    """Run a resumable, evidence-gated DeepSeek pass over the fixed corpus."""

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
            "started_at": now(),
            "papers": {},
        }
    state["max_pages"] = max_pages
    state["chunk_pages"] = chunk_pages
    state["valid_limit"] = valid_limit
    state["status"] = "running"
    _write_state(state_file, state)

    extractor = DeepSeekEvidenceExtractor(db)
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
            "paper_id": paper_id,
            "title": paper.get("title"),
            "doi": paper.get("doi"),
            "pdf_sha256": pdf.get("sha256"),
        }
        if not pdf["content_valid"]:
            print(f"[{order}/{config['expected_paper_count']}] SKIP invalid PDF: {paper.get('title')}", file=sys.stderr, flush=True)
            state["papers"][key] = {
                **base,
                "status": "skipped_invalid_pdf",
                "reason": "placeholder, missing, unreadable, or insufficient PDF content",
                "updated_at": now(),
            }
            state["updated_at"] = now()
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
        ):
            print(f"[{order}/{config['expected_paper_count']}] RESUME completed: {paper.get('title')}", file=sys.stderr, flush=True)
            continue
        print(f"[{order}/{config['expected_paper_count']}] RUN: {paper.get('title')}", file=sys.stderr, flush=True)
        state["papers"][key] = {**base, "status": "running", "max_pages": max_pages, "updated_at": now()}
        state["updated_at"] = now()
        _write_state(state_file, state)
        try:
            result = extractor.run(
                paper_id,
                commit=True,
                merge_existing=True,
                max_pages=max_pages,
                chunk_pages=chunk_pages,
                parallel_focuses=True,
            )
        except Exception as exc:
            print(f"[{order}/{config['expected_paper_count']}] FAIL: {exc}", file=sys.stderr, flush=True)
            state["papers"][key] = {
                **base,
                "status": "failed",
                "max_pages": max_pages,
                "error": str(exc)[:1500],
                "updated_at": now(),
            }
        else:
            print(
                f"[{order}/{config['expected_paper_count']}] DONE: verified={result['verified_count']} "
                f"inserted={result['imported'].get('inserted', 0)}",
                file=sys.stderr,
                flush=True,
            )
            state["papers"][key] = {
                **base,
                "status": "completed",
                "max_pages": max_pages,
                "run_id": result["run_id"],
                "chunk_count": result["chunk_count"],
                "verified_count": result["verified_count"],
                "imported": result["imported"],
                "qualitative_imported": result["qualitative_imported"],
                "visual_evidence": {
                    key: value for key, value in result["visual_evidence"].items()
                    if key not in {"assets", "paper"}
                },
                "output_path": result["output_path"],
                "updated_at": now(),
            }
        state["updated_at"] = now()
        _write_state(state_file, state)

    statuses = [item.get("status") for item in state["papers"].values()]
    state["summary"] = {
        "expected": config["expected_paper_count"],
        "target_valid": valid_limit,
        "valid_considered": min(valid_seen, valid_limit) if valid_limit is not None else valid_seen,
        "completed": statuses.count("completed"),
        "skipped_invalid_pdf": statuses.count("skipped_invalid_pdf"),
        "failed": statuses.count("failed"),
    }
    if stopped_at_limit:
        state["status"] = "partial_completed" if not state["summary"]["failed"] else "partial_completed_with_failures"
    else:
        state["status"] = "completed" if not state["summary"]["failed"] else "completed_with_failures"
    state["finished_at"] = now()
    state["updated_at"] = now()
    state["state_path"] = str(state_file)
    _write_state(state_file, state)
    return state
