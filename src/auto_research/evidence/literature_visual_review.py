"""Frozen, budgeted AI semantics for source-local visual candidates."""
from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from .literature_extraction_budget import (
    MAX_VISUAL_REVIEW_BATCHES, VISUAL_REVIEW_BATCH_SIZE, VISUAL_REVIEW_MAX_TOKENS,
)
from .literature_extraction_job import FrozenModelCall, _canonical_bytes, _plain
from .quality_pipeline import _make_visual_record
from .visual_evidence import (
    _asset_label, _clean_model_visual_metadata, _generic_specs, _target_specs,
    _visual_metadata_messages,
)


def visual_source_key(spec: Mapping) -> str:
    return hashlib.sha256(_canonical_bytes({key: spec.get(key) for key in (
        "asset_type", "number", "page", "page_end", "bbox", "caption",
    )})).hexdigest()


def plan_visual_review(context):
    if context.pdf_snapshot is None:
        return [], ()
    content = context.pdf_snapshot.verified_bytes(context.pdf_snapshot.sha256)
    specs = _target_specs(dict(context.paper)) or _generic_specs(content)
    pages = {int(page["page"]) for page in context.pages}
    assets = [{
        "id": index + 1, "asset_type": spec["asset_type"],
        "label": _asset_label(spec["asset_type"], spec["number"]),
        "page_start": spec["page"], "caption": spec["caption"],
        "source_context": spec.get("source_context", ""),
        "source_key": visual_source_key(spec),
    } for index, spec in enumerate(specs) if spec["page"] in pages]
    calls = []
    limit = MAX_VISUAL_REVIEW_BATCHES * VISUAL_REVIEW_BATCH_SIZE
    for branch in ("a", "b"):
        for offset in range(0, min(len(assets), limit), VISUAL_REVIEW_BATCH_SIZE):
            batch = assets[offset:offset + VISUAL_REVIEW_BATCH_SIZE]
            messages = _visual_metadata_messages(dict(context.paper), batch)
            messages[0]["content"] = (
                ("独立全面编目。" if branch == "a" else "独立审慎编目，严格避免推测。")
                + messages[0]["content"]
            )
            calls.append(FrozenModelCall.create(
                call_id=f"visual-{branch}-{offset // VISUAL_REVIEW_BATCH_SIZE}",
                task="analysis", messages=messages, max_tokens=VISUAL_REVIEW_MAX_TOKENS,
                options={"thinking": False, "temperature": 0.1 if branch == "a" else 0.45},
            ))
    return assets, tuple(calls)


def compare_visual_review(assets, calls, responses, *, threshold):
    branches = {"a": {}, "b": {}}
    for call, raw in zip(calls, responses, strict=True):
        _, branch, batch_index = call.call_id.split("-")
        offset = int(batch_index) * VISUAL_REVIEW_BATCH_SIZE
        expected = {asset["id"] for asset in assets[offset:offset + VISUAL_REVIEW_BATCH_SIZE]}
        values = raw.get("assets") if isinstance(raw, Mapping) else None
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise ValueError("visual review must return assets")
        seen = set()
        for value in values:
            if not isinstance(value, Mapping):
                raise ValueError("invalid visual review item")
            asset_id = value.get("asset_id")
            if type(asset_id) is not int or asset_id not in expected or asset_id in seen:
                raise ValueError("visual review has duplicate or foreign identity")
            seen.add(asset_id)
            try:
                clean = _clean_model_visual_metadata(_plain(value))
            except (TypeError, ValueError):
                continue
            branches[branch][asset_id] = clean
    records = []
    for asset in assets:
        record = _make_visual_record(
            asset, branches["a"].get(asset["id"]), branches["b"].get(asset["id"]),
            is_new=True, threshold=threshold,
            # Geometry comes from the immutable PDF. The finalizer independently
            # matches that geometry and verifies the actual rendered asset hash.
            local_score=100.0 if asset["caption"] and asset["page_start"] > 0 else 0.0,
        )
        record["source_key"] = asset["source_key"]
        records.append(record)
    return records
