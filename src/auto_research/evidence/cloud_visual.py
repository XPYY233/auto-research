"""Cloud visual-evidence shadow pipeline.

The stable ``visual_assets`` table remains authoritative. MinerU and DeepSeek
results are stored as source candidates and can only affect hybrid display
after an explicit per-asset review decision.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol

import requests

from auto_research.ai.deepseek import DeepSeekClient, DeepSeekSettings
from auto_research.paths import ROOT

from .db import EvidenceDB, now


MINERU_KEYCHAIN_SERVICE = "auto-research-mineru"
CLOUD_VISUAL_DIR = ROOT / "data" / "evidence" / "cloud_visual"
ALLOWED_MODES = {"legacy", "shadow", "hybrid"}
TERMINAL_STATES = {"completed", "failed", "cancelled"}


class CloudVisualError(RuntimeError):
    pass


class MinerUNotConfigured(CloudVisualError):
    pass


def _keychain_token(service: str = MINERU_KEYCHAIN_SERVICE) -> str | None:
    if os.name != "posix":
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", getpass.getuser(), "-s", service, "-w"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip() if result.returncode == 0 else ""
    return value or None


def store_mineru_token(token: str, service: str = MINERU_KEYCHAIN_SERVICE) -> None:
    """Store the MinerU token without writing it to project files or logs."""

    value = token.strip()
    if not value:
        raise ValueError("MinerU Token 不能为空")
    if os.name != "posix":
        raise CloudVisualError("当前系统不支持 macOS 钥匙串")
    result = subprocess.run(
        [
            "security", "add-generic-password", "-U", "-a", getpass.getuser(),
            "-s", service, "-w", value,
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise CloudVisualError("MinerU Token 写入钥匙串失败")


@dataclass(frozen=True)
class MinerUSettings:
    token: str | None
    base_url: str = "https://mineru.net"
    model_version: str = "vlm"
    timeout_seconds: int = 180

    @classmethod
    def from_keychain(cls) -> "MinerUSettings":
        try:
            timeout = int(os.environ.get("MINERU_TIMEOUT_SECONDS", "180"))
        except (TypeError, ValueError):
            timeout = 180
        return cls(
            token=_keychain_token(),
            base_url=os.environ.get("MINERU_BASE_URL", "https://mineru.net").rstrip("/"),
            model_version=os.environ.get("MINERU_MODEL_VERSION", "vlm"),
            timeout_seconds=min(max(timeout, 30), 1800),
        )

    def public_status(self) -> dict[str, Any]:
        return {
            "provider": "mineru",
            "configured": bool(self.token),
            "base_url": self.base_url,
            "model_version": self.model_version,
            "credential_source": f"macOS Keychain:{MINERU_KEYCHAIN_SERVICE}" if self.token else None,
        }


class VisualCloudProvider(Protocol):
    """MCP-compatible tool granularity without a separate MCP process."""

    def submit_document(self, pdf_path: Path, *, data_id: str) -> str: ...
    def poll_task(self, task_id: str) -> dict[str, Any]: ...
    def download_artifact(self, url: str, destination: Path) -> Path: ...


class MinerUClient:
    def __init__(self, settings: MinerUSettings | None = None, session=None):
        self.settings = settings or MinerUSettings.from_keychain()
        self.session = session or requests

    @property
    def headers(self) -> dict[str, str]:
        if not self.settings.token:
            raise MinerUNotConfigured(
                f"MinerU 尚未配置；请把 Token 存入 macOS 钥匙串 {MINERU_KEYCHAIN_SERVICE}"
            )
        return {"Authorization": f"Bearer {self.settings.token}", "Content-Type": "application/json"}

    def _payload(self, response) -> dict[str, Any]:
        if not response.ok:
            raise CloudVisualError(f"MinerU API 请求失败：HTTP {response.status_code}")
        payload = response.json()
        if int(payload.get("code", -1)) != 0:
            raise CloudVisualError(f"MinerU API 返回错误：{payload.get('msg') or 'unknown error'}")
        return payload

    def submit_document(self, pdf_path: Path, *, data_id: str) -> str:
        if pdf_path.stat().st_size > 200 * 1024 * 1024:
            raise CloudVisualError("PDF 超过 MinerU 200 MB 限制")
        response = self.session.post(
            f"{self.settings.base_url}/api/v4/file-urls/batch",
            headers=self.headers,
            json={
                "files": [{"name": pdf_path.name, "data_id": data_id}],
                "model_version": self.settings.model_version,
                "enable_table": True,
                "enable_formula": True,
            },
            timeout=self.settings.timeout_seconds,
        )
        data = self._payload(response).get("data") or {}
        urls = data.get("file_urls") or []
        batch_id = str(data.get("batch_id") or "")
        if not batch_id or not urls:
            raise CloudVisualError("MinerU 未返回上传地址或任务编号")
        with pdf_path.open("rb") as handle:
            upload = self.session.put(urls[0], data=handle, timeout=self.settings.timeout_seconds)
        if not upload.ok:
            raise CloudVisualError(f"PDF 上传 MinerU 失败：HTTP {upload.status_code}")
        return batch_id

    def poll_task(self, task_id: str) -> dict[str, Any]:
        response = self.session.get(
            f"{self.settings.base_url}/api/v4/extract-results/batch/{task_id}",
            headers=self.headers,
            timeout=self.settings.timeout_seconds,
        )
        data = self._payload(response).get("data") or {}
        result = data.get("extract_result") or data.get("extract_results") or {}
        if isinstance(result, list):
            result = result[0] if result else {}
        progress = result.get("extract_progress") or {}
        return {
            "state": str(result.get("state") or "pending"),
            "download_url": result.get("full_zip_url"),
            "error": result.get("err_msg") or "",
            "current": int(progress.get("extracted_pages") or 0),
            "total": int(progress.get("total_pages") or 0),
        }

    def download_artifact(self, url: str, destination: Path) -> Path:
        response = self.session.get(url, stream=True, timeout=self.settings.timeout_seconds)
        if not response.ok:
            raise CloudVisualError(f"MinerU 结果下载失败：HTTP {response.status_code}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        with destination.open("wb") as handle:
            for chunk in response.iter_content(1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > 500 * 1024 * 1024:
                    raise CloudVisualError("MinerU 结果压缩包异常大，已停止下载")
                handle.write(chunk)
        return destination


class _TableCounter(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cells = 0
        self.header_cells = 0
        self.rows = 0
        self.header_rows: set[int] = set()

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "tr":
            self.rows += 1
        elif tag in {"td", "th"}:
            self.cells += 1
            if tag == "th":
                self.header_cells += 1
                self.header_rows.add(max(self.rows, 1))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cloud_visual_dir(db_path: Path) -> Path:
    """Keep test/temporary databases from writing artifacts into the live project."""

    resolved = db_path.resolve()
    live_db_dir = (ROOT / "db").resolve()
    if resolved.parent == live_db_dir:
        return CLOUD_VISUAL_DIR
    return resolved.parent / "cloud_visual"


def _safe_extract(zip_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        if sum(member.file_size for member in archive.infolist()) > 1024 * 1024 * 1024:
            raise CloudVisualError("MinerU 解压结果异常大，已停止处理")
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if target != root and root not in target.parents:
                raise CloudVisualError("MinerU 压缩包包含不安全路径")
        archive.extractall(destination)


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _caption_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return " ".join(str(part).strip() for part in value if str(part).strip())
    return ""


def _label_from_caption(caption: str, asset_type: str, fallback: int) -> str:
    prefix = r"(?:Table|Tab\.)" if asset_type == "table" else r"(?:Figure|Fig\.)"
    match = re.search(rf"\b{prefix}\s*([0-9]+)\b", caption, flags=re.I)
    number = int(match.group(1)) if match else fallback
    return f"{'Table' if asset_type == 'table' else 'Figure'} {number}"


def _locate_result_file(root: Path) -> Path:
    candidates = sorted(root.rglob("*_content_list.json")) + sorted(root.rglob("content_list.json"))
    if not candidates:
        raise CloudVisualError("MinerU 结果缺少 content_list.json")
    return candidates[0]


def _candidate_image(root: Path, raw: dict[str, Any]) -> Path | None:
    path_text = str(raw.get("img_path") or raw.get("image_path") or "").strip()
    if not path_text:
        return None
    direct = (root / path_text).resolve()
    if direct.is_file() and root.resolve() in direct.parents:
        return direct
    matches = list(root.rglob(Path(path_text).name))
    return matches[0].resolve() if matches else None


def _legacy_match(db: EvidenceDB, paper_id: int, asset_type: str, label: str, page: int) -> tuple[int | None, float]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id,label,page_start FROM visual_assets WHERE paper_id=? AND asset_type=?",
            (paper_id, asset_type),
        ).fetchall()
    normalized = re.sub(r"\W+", "", label).lower()
    exact = [row for row in rows if re.sub(r"\W+", "", str(row["label"])).lower() == normalized]
    if exact:
        row = min(exact, key=lambda item: abs(int(item["page_start"]) - page))
        distance = abs(int(row["page_start"]) - page)
        return int(row["id"]), 1.0 if distance == 0 else 0.95 if distance <= 1 else 0.8
    nearby = [row for row in rows if abs(int(row["page_start"]) - page) <= 1]
    if len(nearby) == 1:
        return int(nearby[0]["id"]), 0.65
    return None, 0.0


def _decode(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (json.JSONDecodeError, TypeError):
        return fallback


def _analysis_payload(raw: dict[str, Any], legacy: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "asset_type": raw["asset_type"],
        "label": raw["label"],
        "page": raw["page_start"],
        "caption": str(raw["caption"])[:8000],
        "source_context": str(raw["source_context"])[:12000],
        "table_html": str(raw.get("table_html") or "")[:30000],
        "mineru_visual_content": str(raw.get("visual_content") or "")[:12000],
        "stable_metadata": legacy or {},
    }


def _strip_curve_point_fields(value: Any) -> tuple[Any, bool]:
    removed = False
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            if str(key).lower() in {"curve_points", "data_points", "digitized_points", "xy_points"}:
                removed = True
                continue
            child, child_removed = _strip_curve_point_fields(item)
            removed = removed or child_removed
            clean[key] = child
        return clean, removed
    if isinstance(value, list):
        clean_list = []
        for item in value:
            child, child_removed = _strip_curve_point_fields(item)
            removed = removed or child_removed
            clean_list.append(child)
        return clean_list, removed
    return value, False


def _sanitize_analysis(result: dict[str, Any], source: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    stripped, removed_points = _strip_curve_point_fields(result)
    result.clear()
    result.update(stripped if isinstance(stripped, dict) else {})
    if removed_points:
        warnings.append("模型返回的曲线点已拒绝并删除")
    caption_context = f"{source.get('caption','')} {source.get('source_context','')}".lower()
    trends: list[dict[str, str]] = []
    for raw in result.get("trends") or []:
        if not isinstance(raw, dict) or not str(raw.get("statement") or "").strip():
            continue
        provenance = str(raw.get("provenance") or "visual_interpretation")
        evidence_text = str(raw.get("evidence_text") or "").strip()
        if provenance == "explicit_text" and (not evidence_text or evidence_text.lower() not in caption_context):
            provenance = "visual_interpretation"
            warnings.append("缺少可核对原文的显式趋势已降级为视觉解释")
        if provenance == "visual_interpretation" and re.search(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?", str(raw["statement"])):
            warnings.append("仅由视觉解释产生的精确数值趋势已拒绝")
            continue
        trends.append({
            "statement": str(raw["statement"]).strip(),
            "provenance": provenance if provenance in {"explicit_text", "visual_interpretation"} else "visual_interpretation",
            "evidence_text": evidence_text,
        })
    clean = {
        "display_name": str(result.get("display_name") or "").strip(),
        "physical_quantities": [str(v).strip() for v in result.get("physical_quantities") or [] if str(v).strip()],
        "variables": result.get("variables") if isinstance(result.get("variables"), dict) else {},
        "materials": [str(v).strip() for v in result.get("materials") or [] if str(v).strip()],
        "conditions_text": str(result.get("conditions_text") or "").strip(),
        "methods_text": str(result.get("methods_text") or "").strip(),
        "context_explanation": str(result.get("context_explanation") or "").strip(),
        "tags": [str(v).strip() for v in result.get("tags") or [] if str(v).strip()],
        "trends": trends,
    }
    return clean, warnings


def _deepseek_analyze(db: EvidenceDB, source_id: int, source: dict[str, Any], client: DeepSeekClient) -> None:
    legacy = None
    if source.get("asset_id"):
        with db.connect() as conn:
            row = conn.execute("SELECT * FROM visual_assets WHERE id=?", (source["asset_id"],)).fetchone()
            legacy = dict(row) if row else None
    request = _analysis_payload(source, legacy)
    result = client.request_json(
        [
            {
                "role": "system",
                "content": (
                    "你是科学论文图表证据整理器。只根据给定图注、邻近正文、MinerU结构化内容解释语义。"
                    "不得生成、估读或插值任何曲线数据点。输出JSON字段：display_name,physical_quantities,"
                    "variables,materials,conditions_text,methods_text,context_explanation,tags,trends。"
                    "trends每项含statement, provenance(explicit_text或visual_interpretation), evidence_text。"
                    "只有能在图注或正文逐字定位的趋势才能标explicit_text。"
                ),
            },
            {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
        ],
        task="analysis",
        max_tokens=4000,
    )
    clean, warnings = _sanitize_analysis(result, source)
    stamp = now()
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO visual_analysis_candidates(
              source_version_id,provider,model,status,display_name,physical_quantities_json,variables_json,
              materials_json,conditions_text,methods_text,context_explanation,tags_json,trends_json,
              provenance_json,raw_json,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source_version_id) DO UPDATE SET
              provider=excluded.provider,model=excluded.model,status=excluded.status,
              display_name=excluded.display_name,physical_quantities_json=excluded.physical_quantities_json,
              variables_json=excluded.variables_json,materials_json=excluded.materials_json,
              conditions_text=excluded.conditions_text,methods_text=excluded.methods_text,
              context_explanation=excluded.context_explanation,tags_json=excluded.tags_json,
              trends_json=excluded.trends_json,provenance_json=excluded.provenance_json,
              raw_json=excluded.raw_json,error_message=NULL,updated_at=excluded.updated_at""",
            (
                source_id, "deepseek", client.settings.analysis_model, "completed", clean["display_name"],
                _json_text(clean["physical_quantities"]), _json_text(clean["variables"]),
                _json_text(clean["materials"]), clean["conditions_text"], clean["methods_text"],
                clean["context_explanation"], _json_text(clean["tags"]), _json_text(clean["trends"]),
                _json_text({"warnings": warnings, "trend_policy": "no_curve_points"}),
                _json_text(result), stamp, stamp,
            ),
        )


def import_mineru_artifact(
    db: EvidenceDB,
    run_id: int,
    artifact_dir: Path,
    *,
    deepseek_client: DeepSeekClient | None = None,
) -> dict[str, Any]:
    with db.connect() as conn:
        run = conn.execute("SELECT * FROM cloud_visual_runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        raise KeyError(f"cloud visual run not found: {run_id}")
    paper_id = int(run["paper_id"])
    content_path = _locate_result_file(artifact_dir)
    content = json.loads(content_path.read_text(encoding="utf-8"))
    if not isinstance(content, list):
        raise CloudVisualError("MinerU content_list 不是列表")
    counters = {"table": 0, "figure": 0}
    inserted: list[dict[str, Any]] = []
    stamp = now()
    for raw in content:
        if not isinstance(raw, dict):
            continue
        raw_type = str(raw.get("type") or "").lower()
        if raw_type == "table":
            asset_type = "table"
        elif raw_type in {"image", "figure", "chart"}:
            asset_type = "figure"
        else:
            continue
        counters[asset_type] += 1
        caption = _caption_text(raw.get(f"{raw_type}_caption") or raw.get(f"{asset_type}_caption") or raw.get("caption"))
        label = _label_from_caption(caption, asset_type, counters[asset_type])
        page = int(raw.get("page_idx") or raw.get("page_index") or 0) + 1
        image = _candidate_image(artifact_dir, raw)
        image_path = str(image.relative_to(ROOT)) if image and ROOT in image.parents else str(image or "")
        image_sha = _sha256(image) if image else None
        asset_id, confidence = _legacy_match(db, paper_id, asset_type, label, page)
        table_html = str(raw.get("table_body") or raw.get("html") or "") if asset_type == "table" else ""
        source_context = _caption_text(raw.get("text") or raw.get("content") or raw.get("image_content") or "")
        visual_content = _caption_text(raw.get("chart_content") or raw.get("image_content") or raw.get("content") or "")
        validation = {
            "image_present": bool(image),
            "caption_present": bool(caption),
            "table_structure_present": bool(table_html) if asset_type == "table" else None,
            "matched_legacy": bool(asset_id),
            "no_curve_points": True,
        }
        with db.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO visual_source_versions(
                  run_id,asset_id,source_kind,asset_type,label,page_start,page_end,bbox_json,caption,
                  source_context,image_path,image_sha256,match_confidence,quality_status,adoption_state,
                  validation_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'pending','shadow',?,?,?)
                ON CONFLICT(run_id,asset_type,label,page_start) DO UPDATE SET
                  asset_id=excluded.asset_id,bbox_json=excluded.bbox_json,caption=excluded.caption,
                  source_context=excluded.source_context,image_path=excluded.image_path,
                  image_sha256=excluded.image_sha256,match_confidence=excluded.match_confidence,
                  validation_json=excluded.validation_json,updated_at=excluded.updated_at
                RETURNING id""",
                (
                    run_id, asset_id, "mineru", asset_type, label, page, page,
                    _json_text(raw.get("bbox") or []), caption, source_context, image_path or None,
                    image_sha, confidence, _json_text(validation), stamp, stamp,
                ),
            )
            source_id = int(cursor.fetchone()[0])
            if asset_type == "table":
                counter = _TableCounter()
                counter.feed(table_html)
                structure = {
                    "rows": counter.rows,
                    "cells": counter.cells,
                    "header_cells": counter.header_cells,
                    "footnotes": raw.get("table_footnote") or raw.get("footnotes") or [],
                    "raw_bbox": raw.get("bbox") or [],
                }
                conn.execute(
                    """INSERT INTO table_structure_candidates(
                      source_version_id,html_content,structure_json,cell_count,header_row_count,
                      has_footnotes,exact_cell_count,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source_version_id) DO UPDATE SET
                      html_content=excluded.html_content,structure_json=excluded.structure_json,
                      cell_count=excluded.cell_count,header_row_count=excluded.header_row_count,
                      has_footnotes=excluded.has_footnotes,updated_at=excluded.updated_at""",
                    (
                        source_id, table_html, _json_text(structure), counter.cells,
                        len(counter.header_rows), int(bool(structure["footnotes"])), 0, stamp, stamp,
                    ),
                )
        source = {
            "id": source_id, "asset_id": asset_id, "asset_type": asset_type, "label": label,
            "page_start": page, "caption": caption, "source_context": source_context,
            "table_html": table_html, "visual_content": visual_content,
        }
        inserted.append(source)
        if deepseek_client:
            try:
                _deepseek_analyze(db, source_id, source, deepseek_client)
            except Exception as exc:
                with db.connect() as conn:
                    conn.execute(
                        """INSERT INTO visual_analysis_candidates(
                          source_version_id,provider,model,status,error_message,created_at,updated_at
                        ) VALUES(?,?,?,'failed',?,?,?)
                        ON CONFLICT(source_version_id) DO UPDATE SET status='failed',
                          error_message=excluded.error_message,updated_at=excluded.updated_at""",
                        (source_id, "deepseek", deepseek_client.settings.analysis_model, str(exc), stamp, stamp),
                    )
    return {"run_id": run_id, "candidate_count": len(inserted), "counts": counters, "content_list": str(content_path)}


def _update_run(db: EvidenceDB, run_id: int, **fields: Any) -> None:
    allowed = {
        "status", "progress_stage", "progress_current", "progress_total", "message",
        "remote_task_id", "artifact_dir", "manifest_path", "error_message", "finished_at",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    updates["updated_at"] = now()
    clause = ",".join(f"{key}=?" for key in updates)
    with db.connect() as conn:
        conn.execute(f"UPDATE cloud_visual_runs SET {clause} WHERE id=?", (*updates.values(), run_id))


def _run_pipeline(db_path: Path, run_id: int, provider: VisualCloudProvider | None = None) -> None:
    db = EvidenceDB(db_path)
    try:
        with db.connect() as conn:
            row = conn.execute(
                """SELECT r.*,p.pdf_path,p.title FROM cloud_visual_runs r
                JOIN papers p ON p.id=r.paper_id WHERE r.id=?""", (run_id,),
            ).fetchone()
        if not row:
            raise KeyError(f"cloud visual run not found: {run_id}")
        pdf_path = Path(str(row["pdf_path"]))
        if not pdf_path.is_file():
            raise FileNotFoundError(f"PDF 不存在：{pdf_path}")
        client = provider or MinerUClient()
        _update_run(db, run_id, status="uploading", progress_stage="uploading", message="正在上传整篇 PDF")
        task_id = client.submit_document(pdf_path, data_id=f"paper-{int(row['paper_id'])}-run-{run_id}")
        _update_run(db, run_id, status="pending", progress_stage="queued", remote_task_id=task_id, message="云端任务已排队")
        deadline = time.monotonic() + 45 * 60
        result: dict[str, Any] = {}
        while time.monotonic() < deadline:
            result = client.poll_task(task_id)
            state = str(result.get("state") or "pending")
            if state == "done":
                break
            if state == "failed":
                raise CloudVisualError(str(result.get("error") or "MinerU 解析失败"))
            _update_run(
                db, run_id, status="running" if state == "running" else "pending",
                progress_stage="parsing" if state == "running" else "queued",
                progress_current=int(result.get("current") or 0),
                progress_total=int(result.get("total") or 0),
                message="正在解析 PDF 页面" if state == "running" else "等待 MinerU 处理",
            )
            time.sleep(3)
        else:
            raise CloudVisualError("MinerU 任务超过 45 分钟仍未完成")
        download_url = str(result.get("download_url") or "")
        if not download_url:
            raise CloudVisualError("MinerU 完成但未返回结果下载地址")
        run_dir = _cloud_visual_dir(db.path) / f"run_{run_id:06d}"
        zip_path = run_dir / "mineru-result.zip"
        _update_run(db, run_id, status="downloading", progress_stage="downloading", message="正在下载结构化结果")
        client.download_artifact(download_url, zip_path)
        artifact_dir = run_dir / "artifact"
        _safe_extract(zip_path, artifact_dir)
        _update_run(db, run_id, status="analyzing", progress_stage="deepseek", artifact_dir=str(artifact_dir), message="正在生成科学语义候选")
        deepseek_settings = DeepSeekSettings.from_env()
        deepseek = DeepSeekClient(deepseek_settings) if deepseek_settings.api_key else None
        imported = import_mineru_artifact(db, run_id, artifact_dir, deepseek_client=deepseek)
        manifest_path = run_dir / "import-summary.json"
        manifest_path.write_text(json.dumps(imported, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        message = f"已生成 {imported['candidate_count']} 个云端候选"
        if deepseek is None:
            message += "；DeepSeek 未配置，语义解释待补充"
        _update_run(
            db, run_id, status="completed", progress_stage="completed",
            progress_current=int(result.get("total") or result.get("current") or 1),
            progress_total=int(result.get("total") or result.get("current") or 1),
            manifest_path=str(manifest_path), message=message, finished_at=now(), error_message=None,
        )
    except Exception as exc:
        _update_run(
            db, run_id, status="failed", progress_stage="failed", message="云端增强失败；稳定版仍可正常使用",
            error_message=str(exc), finished_at=now(),
        )


def start_cloud_visual_run(
    db: EvidenceDB,
    paper_id: int,
    *,
    requested_mode: str = "shadow",
    background: bool = True,
    provider: VisualCloudProvider | None = None,
) -> dict[str, Any]:
    if requested_mode not in {"shadow", "hybrid"}:
        raise ValueError("云端图表运行模式必须为 shadow 或 hybrid")
    db.init()
    paper = db.get_paper(paper_id)
    if not paper or not paper.get("pdf_path"):
        raise FileNotFoundError("当前文章没有可用 PDF")
    pdf_path = Path(str(paper["pdf_path"]))
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF 不存在：{pdf_path}")
    pdf_sha = _sha256(pdf_path)
    document_id = None
    for document in paper.get("documents") or []:
        if Path(str(document.get("stored_path") or "")).resolve() == pdf_path.resolve():
            document_id = int(document["id"])
            break
    stamp = now()
    with db.connect() as conn:
        active = conn.execute(
            """SELECT * FROM cloud_visual_runs WHERE paper_id=?
            AND status NOT IN ('completed','failed','cancelled') ORDER BY id DESC LIMIT 1""", (paper_id,),
        ).fetchone()
        if active:
            return _run_dict(dict(active))
        cursor = conn.execute(
            """INSERT INTO cloud_visual_runs(
              paper_id,document_id,provider,requested_mode,model_version,status,progress_stage,
              source_pdf_sha256,created_at,updated_at
            ) VALUES(?,?,?,?,?,'queued','queued',?,?,?)""",
            (paper_id, document_id, "mineru", requested_mode, "vlm", pdf_sha, stamp, stamp),
        )
        run_id = int(cursor.lastrowid)
    if background:
        threading.Thread(
            target=_run_pipeline, args=(db.path, run_id, provider),
            name=f"mineru-visual-{run_id}", daemon=True,
        ).start()
    else:
        _run_pipeline(db.path, run_id, provider)
    return get_cloud_visual_run(db, run_id)


def _run_dict(row: dict[str, Any]) -> dict[str, Any]:
    total = int(row.get("progress_total") or 0)
    current = int(row.get("progress_current") or 0)
    if row.get("status") == "completed":
        percent = 100
    elif total > 0:
        percent = min(99, round(current / total * 100))
    else:
        percent = {"queued": 2, "uploading": 8, "pending": 15, "running": 45, "downloading": 78, "analyzing": 88}.get(str(row.get("status")), 0)
    row["progress_percent"] = percent
    row["terminal"] = row.get("status") in TERMINAL_STATES
    return row


def get_cloud_visual_run(db: EvidenceDB, run_id: int) -> dict[str, Any]:
    db.init()
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM cloud_visual_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise KeyError(f"cloud visual run not found: {run_id}")
    return _run_dict(dict(row))


def list_cloud_visual_runs(db: EvidenceDB, paper_id: int) -> list[dict[str, Any]]:
    db.init()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM cloud_visual_runs WHERE paper_id=? ORDER BY id DESC", (paper_id,),
        ).fetchall()
    return [_run_dict(dict(row)) for row in rows]


def list_cloud_candidates(db: EvidenceDB, paper_id: int) -> list[dict[str, Any]]:
    db.init()
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT s.*,r.status run_status,r.created_at run_created_at,
              t.html_content,t.structure_json,t.cell_count,t.header_row_count,t.has_footnotes,t.exact_cell_count,
              a.provider analysis_provider,a.model analysis_model,a.status analysis_status,
              a.display_name,a.physical_quantities_json,a.variables_json,a.materials_json,
              a.conditions_text,a.methods_text,a.context_explanation,a.tags_json,a.trends_json,
              a.provenance_json,a.error_message analysis_error
            FROM visual_source_versions s
            JOIN cloud_visual_runs r ON r.id=s.run_id
            LEFT JOIN table_structure_candidates t ON t.source_version_id=s.id
            LEFT JOIN visual_analysis_candidates a ON a.source_version_id=s.id
            WHERE r.paper_id=? ORDER BY r.id DESC,s.page_start,s.asset_type,s.label""",
            (paper_id,),
        ).fetchall()
    output: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        for key, fallback in (
            ("bbox_json", []), ("validation_json", {}), ("structure_json", {}),
            ("physical_quantities_json", []), ("variables_json", {}), ("materials_json", []),
            ("tags_json", []), ("trends_json", []), ("provenance_json", {}),
        ):
            item[key.removesuffix("_json")] = _decode(item.pop(key, None), fallback)
        item["image_url"] = f"/api/cloud-visual-sources/{item['id']}/image" if item.get("image_path") else None
        output.append(item)
    return output


def get_cloud_candidate(db: EvidenceDB, source_id: int) -> dict[str, Any]:
    with db.connect() as conn:
        row = conn.execute(
            """SELECT r.paper_id FROM visual_source_versions s
            JOIN cloud_visual_runs r ON r.id=s.run_id WHERE s.id=?""", (source_id,),
        ).fetchone()
    if not row:
        raise KeyError(f"cloud visual source not found: {source_id}")
    return next(item for item in list_cloud_candidates(db, int(row["paper_id"])) if int(item["id"]) == source_id)


def cloud_candidate_image_path(db: EvidenceDB, source_id: int) -> Path:
    candidate = get_cloud_candidate(db, source_id)
    path = Path(str(candidate.get("image_path") or ""))
    resolved = path if path.is_absolute() else ROOT / path
    if not resolved.is_file():
        raise FileNotFoundError("云端候选图片不存在；稳定版截图仍可使用")
    return resolved


def review_cloud_candidate(db: EvidenceDB, source_id: int, decision: str, *, note: str = "") -> dict[str, Any]:
    mapping = {
        "keep_stable": ("rejected", "ignored"),
        "adopt_enhancement": ("passed", "enhancement"),
        "adopt_interpretation": ("passed", "interpretation_only"),
        "mark_error": ("rejected", "ignored"),
    }
    if decision not in mapping:
        raise ValueError("不支持的云端候选处理方式")
    quality, adoption = mapping[decision]
    candidate = get_cloud_candidate(db, source_id)
    if adoption != "ignored" and not candidate.get("asset_id"):
        raise ValueError("新增云端候选尚未与稳定图表匹配，不能直接采用")
    validation = dict(candidate.get("validation") or {})
    validation.update({"human_decision": decision, "human_note": note, "reviewed_at": now()})
    with db.connect() as conn:
        conn.execute(
            """UPDATE visual_source_versions SET quality_status=?,adoption_state=?,
            validation_json=?,updated_at=? WHERE id=?""",
            (quality, adoption, _json_text(validation), now(), source_id),
        )
    return get_cloud_candidate(db, source_id)


def get_visual_processing_mode(db: EvidenceDB) -> str:
    mode = str(db.get_meta("visual_processing_mode", "legacy") or "legacy")
    return mode if mode in ALLOWED_MODES else "legacy"


def set_visual_processing_mode(db: EvidenceDB, mode: str) -> str:
    if mode not in ALLOWED_MODES:
        raise ValueError("图表模式必须为 legacy、shadow 或 hybrid")
    if mode == "hybrid":
        evaluation = latest_cloud_quality_evaluation(db)
        if not evaluation or evaluation.get("decision") != "approved":
            raise ValueError("十篇测试集尚未通过全部云端质量门；当前只能使用稳定版或影子对比")
    db.set_meta("visual_processing_mode", mode)
    return mode


def adopted_candidate_for_asset(db: EvidenceDB, asset_id: int) -> dict[str, Any] | None:
    if get_visual_processing_mode(db) != "hybrid":
        return None
    with db.connect() as conn:
        row = conn.execute(
            """SELECT r.paper_id FROM visual_source_versions s
            JOIN cloud_visual_runs r ON r.id=s.run_id
            WHERE s.asset_id=? AND s.quality_status='passed'
              AND s.adoption_state IN ('enhancement','interpretation_only')
            ORDER BY s.updated_at DESC,s.id DESC LIMIT 1""", (asset_id,),
        ).fetchone()
    if not row:
        return None
    candidates = list_cloud_candidates(db, int(row["paper_id"]))
    return next((item for item in candidates if int(item.get("asset_id") or 0) == asset_id and item["quality_status"] == "passed" and item["adoption_state"] in {"enhancement", "interpretation_only"}), None)


def cloud_quality_report(db: EvidenceDB, paper_id: int | None = None) -> dict[str, Any]:
    db.init()
    clause = "WHERE r.paper_id=?" if paper_id is not None else ""
    params = (paper_id,) if paper_id is not None else ()
    with db.connect() as conn:
        rows = conn.execute(
            f"""SELECT s.*,t.cell_count,t.exact_cell_count,a.trends_json
            FROM visual_source_versions s JOIN cloud_visual_runs r ON r.id=s.run_id
            LEFT JOIN table_structure_candidates t ON t.source_version_id=s.id
            LEFT JOIN visual_analysis_candidates a ON a.source_version_id=s.id {clause}""", params,
        ).fetchall()
    total = len(rows)
    matched = sum(row["asset_id"] is not None for row in rows)
    passed = sum(row["quality_status"] == "passed" for row in rows)
    table_cells = sum(int(row["cell_count"] or 0) for row in rows)
    exact_cells = sum(int(row["exact_cell_count"] or 0) for row in rows)
    point_violations = 0
    trends_with_provenance = 0
    trend_total = 0
    for row in rows:
        trends = _decode(row["trends_json"], [])
        trend_total += len(trends)
        trends_with_provenance += sum(t.get("provenance") in {"explicit_text", "visual_interpretation"} for t in trends if isinstance(t, dict))
        point_violations += sum(bool(t.get("curve_points") or t.get("data_points")) for t in trends if isinstance(t, dict))
    evaluation = latest_cloud_quality_evaluation(db)
    return {
        "paper_id": paper_id,
        "mode": get_visual_processing_mode(db),
        "candidate_count": total,
        "matched_count": matched,
        "passed_count": passed,
        "match_rate": round(matched / total, 4) if total else 0.0,
        "table_cells_sampled": table_cells,
        "table_cells_exact": exact_cells,
        "table_cell_exact_rate": round(exact_cells / table_cells, 4) if table_cells else None,
        "trend_count": trend_total,
        "trends_with_provenance": trends_with_provenance,
        "curve_point_violations": point_violations,
        "hybrid_gate_ready": bool(evaluation and evaluation.get("decision") == "approved"),
        "quality_evaluation": evaluation,
    }


def latest_cloud_quality_evaluation(db: EvidenceDB) -> dict[str, Any] | None:
    db.init()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM cloud_visual_quality_evaluations ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["metrics"] = _decode(result.pop("metrics_json", None), {})
    return result


def record_cloud_quality_evaluation(
    db: EvidenceDB,
    metrics: dict[str, Any],
    *,
    test_set_version: str,
    baseline_manifest_sha256: str,
    reviewer: str,
    note: str = "",
) -> dict[str, Any]:
    """Record the fixed ten-paper quality gate; approval is computed, not asserted."""

    required = {
        "legacy_recall", "cloud_recall", "new_candidate_precision",
        "table_cells_sampled", "table_cell_exact_rate", "axis_legend_sampled",
        "axis_legend_accuracy", "curve_point_violations",
    }
    missing = sorted(required - set(metrics))
    if missing:
        raise ValueError(f"质量门指标缺失：{', '.join(missing)}")
    try:
        legacy_recall = float(metrics["legacy_recall"])
        cloud_recall = float(metrics["cloud_recall"])
        precision = float(metrics["new_candidate_precision"])
        cell_sample = int(metrics["table_cells_sampled"])
        cell_rate = float(metrics["table_cell_exact_rate"])
        axis_sample = int(metrics["axis_legend_sampled"])
        axis_rate = float(metrics["axis_legend_accuracy"])
        point_violations = int(metrics["curve_point_violations"])
    except (TypeError, ValueError) as exc:
        raise ValueError("质量门指标必须为数值") from exc
    checks = {
        "recall_not_lower_than_legacy": cloud_recall >= legacy_recall,
        "new_candidate_precision_at_least_95pct": precision >= 0.95,
        "at_least_100_table_cells": cell_sample >= 100,
        "table_cell_exact_rate_at_least_95pct": cell_rate >= 0.95,
        "axis_legend_accuracy_at_least_90pct": axis_sample > 0 and axis_rate >= 0.90,
        "no_curve_points": point_violations == 0,
    }
    decision = "approved" if all(checks.values()) else "experimental"
    normalized = {
        **metrics,
        "legacy_recall": legacy_recall,
        "cloud_recall": cloud_recall,
        "new_candidate_precision": precision,
        "table_cells_sampled": cell_sample,
        "table_cell_exact_rate": cell_rate,
        "axis_legend_sampled": axis_sample,
        "axis_legend_accuracy": axis_rate,
        "curve_point_violations": point_violations,
        "checks": checks,
    }
    with db.connect() as conn:
        cursor = conn.execute(
            """INSERT INTO cloud_visual_quality_evaluations(
              test_set_version,baseline_manifest_sha256,metrics_json,decision,reviewer,note,created_at
            ) VALUES(?,?,?,?,?,?,?)""",
            (
                test_set_version, baseline_manifest_sha256, _json_text(normalized), decision,
                reviewer.strip() or "本地研究者", note.strip(), now(),
            ),
        )
        evaluation_id = int(cursor.lastrowid)
    evaluation = latest_cloud_quality_evaluation(db)
    assert evaluation and int(evaluation["id"]) == evaluation_id
    return evaluation
