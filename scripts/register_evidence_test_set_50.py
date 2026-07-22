from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import fitz
import requests


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from auto_research.evidence.db import EvidenceDB, now  # noqa: E402
from auto_research.evidence.document_recognition import recognize_pdf_identity  # noqa: E402
from auto_research.evidence.experiment_types import classify_experiment_types  # noqa: E402
from auto_research.evidence.uploads import UploadService, inspect_pdf  # noqa: E402


DB_PATH = ROOT / "db" / "experimental_evidence.sqlite"
OLD_CONFIG = ROOT / "config" / "evidence_test_set_35.json"
NEW_CONFIG = ROOT / "config" / "evidence_test_set_50.json"
VERIFICATION = ROOT / "data" / "matrix" / "pdf7692_zotero_verification.csv"
OUTPUT_JSON = ROOT / "data" / "evidence" / "test_sets" / "full-corpus-50-v1_manifest.json"
OUTPUT_MD = ROOT / "data" / "evidence" / "test_sets" / "full-corpus-50-v1_manifest.md"
ZOTERO_API = "http://127.0.0.1:23119/api/users/0"

REPLACED_INVALID_DOIS = {
    "10.3390/ma17174383",
    "10.3390/ma17194751",
    "10.3390/ma16165530",
    "10.2172/6065200",
    "10.1016/j.heliyon.2023.e17725",
}

ADDITIONS = (
    ("10.1016/j.cossms.2022.101001", "电子能损与 HEA 辐照实验", "电子能损"),
    ("10.1038/s41598-018-34486-5", "多主元合金辐照抗性计算", "辐照抗性"),
    ("10.1016/j.jnucmat.2025.156004", "MoNbTaVW 机器学习势辐照模拟", "机器学习势"),
    ("10.1063/5.0302848", "CrMnV 原子间势与缺陷计算", "原子间势"),
    ("10.1016/j.jnucmat.2019.03.031", "NiFe 温度依赖辐照缺陷", "缺陷累积"),
    ("10.1016/j.actamat.2020.07.066", "FCC/BCC CCA 原位重离子辐照", "原位辐照"),
    ("10.1016/j.actamat.2020.01.060", "Al0.3CoCrFeNi 高温离子辐照", "相稳定性"),
    ("10.1016/j.actamat.2018.10.040", "Ni 基浓合金 He 空腔与缺陷能", "空腔形成"),
    ("10.1088/1741-4326/ad5aaf", "CrMoTaWV/W 多层膜等离子体辐照", "He扩散"),
    ("10.1016/j.actamat.2016.05.007", "FeNiMnCr 辐照微结构与力学", "力学行为"),
    ("10.1016/j.actamat.2023.118765", "HEA 与 He 相互作用实验", "He辐照"),
    ("10.1016/j.jnucmat.2021.152782", "增材 CCA 高通量离子辐照", "高通量辐照"),
    ("10.1016/j.jnucmat.2022.154163", "CrFeMnNi/AlCrFeMnNi 重离子辐照", "重离子辐照"),
    ("10.1016/j.mtla.2018.06.008", "中高熵合金辐照晶格畸变", "晶格畸变"),
    ("10.1038/s41598-020-66564-y", "FIB 离子辐照与微样品力学", "FIB"),
    ("10.1016/j.ijrmhm.2023.106209", "W 基 BCC 超合金热稳定性", "热稳定性"),
    ("10.1016/j.cossms.2024.101201", "W 基 RHEA 等离子体部件综述", "等离子体部件"),
    ("10.1103/physrevmaterials.5.033605", "Fe 基合金缺陷再分布多尺度模拟", "多尺度模拟"),
    ("10.1016/j.jnucmat.2021.152872", "增材不锈钢级联损伤 MD", "级联演化"),
    ("10.1103/physrevb.108.054312", "钨初级辐照损伤机器学习 MD", "初级辐照损伤"),
)

FIRST_AUTHOR_OVERRIDES = {
    "10.1038/s41598-018-34486-5": "Hyeon-Seok Do",
    "10.1016/j.jnucmat.2025.156004": "Jiahui Liu",
    "10.1016/j.actamat.2020.01.060": "Tengfei Yang",
    "10.1016/j.actamat.2016.05.007": "N.A.P. Kiran Kumar",
    "10.1016/j.actamat.2023.118765": "Tao Cheng",
    "10.1038/s41598-020-66564-y": "Jinqiao Liu",
    "10.1016/j.ijrmhm.2023.106209": "Neal Parkes",
    "10.1103/physrevmaterials.5.033605": "Liangzhao Huang",
    "10.1103/physrevb.108.054312": "Jiahui Liu",
}

PLACEHOLDER_PHRASES = (
    "preparing to download",
    "hhs vulnerability disclosure",
    "gauging your humanity",
    "checking your browser",
    "access denied",
)


def _verification_rows() -> dict[str, dict[str, str]]:
    with VERIFICATION.open(encoding="utf-8", newline="") as handle:
        return {
            str(row.get("doi") or "").strip().casefold(): row
            for row in csv.DictReader(handle)
            if row.get("doi") and row.get("valid_pdf") == "True"
        }


def _zotero_metadata(key: str) -> dict[str, Any]:
    try:
        response = requests.get(
            f"{ZOTERO_API}/items/{key}",
            headers={"Zotero-API-Version": "3"},
            timeout=10,
        )
        response.raise_for_status()
        return dict(response.json().get("data") or {})
    except (requests.RequestException, ValueError):
        return {}


def _author_name(metadata: dict[str, Any]) -> str | None:
    creators = [
        creator for creator in metadata.get("creators") or []
        if creator.get("creatorType") == "author"
    ]
    if not creators:
        return None
    first = creators[0]
    given = re.split(r"\s*(?:\[|\()", str(first.get("firstName") or ""), maxsplit=1)[0].strip()
    family = str(first.get("lastName") or "").strip()
    return " ".join(part for part in (given, family) if part) or None


def _year(metadata: dict[str, Any]) -> int | None:
    match = re.search(r"(?:19|20)\d{2}", str(metadata.get("date") or ""))
    return int(match.group(0)) if match else None


def _paper_record(doi: str, role: str, query: str, row: dict[str, str]) -> dict[str, Any]:
    paths = [Path(value) for value in str(row.get("pdf_paths") or "").split(" | ") if value]
    path = next((candidate for candidate in paths if candidate.is_file()), None)
    if path is None:
        raise FileNotFoundError(f"verified PDF path missing for {doi}")
    raw = path.read_bytes()
    inspection = inspect_pdf(raw)
    with fitz.open(path) as document:
        text = "\n".join(page.get_text("text") for page in document)
    normalized = " ".join(text.casefold().split())
    placeholder = any(phrase in normalized for phrase in PLACEHOLDER_PHRASES)
    metadata = _zotero_metadata(str(row.get("key") or ""))
    title = str(metadata.get("title") or row.get("title") or "").strip()
    paper = {"title": title, "doi": doi}
    identity = recognize_pdf_identity(paper, path)
    profile = classify_experiment_types(paper, pdf_path=path)
    valid = bool(
        not placeholder
        and not inspection.needs_ocr
        and inspection.text_char_count >= 1000
        and identity.get("valid")
    )
    if not valid:
        raise ValueError(
            f"candidate failed real-PDF gate: {doi}; placeholder={placeholder}; "
            f"needs_ocr={inspection.needs_ocr}; identity={identity.get('status')}"
        )
    return {
        "doi": doi,
        "title": title,
        "role": role,
        "queries": [doi, query],
        "min_rows": 1,
        "year": _year(metadata),
        "first_author": _author_name(metadata) or FIRST_AUTHOR_OVERRIDES.get(doi),
        "corresponding_author": None,
        "zotero_key": str(row.get("key") or "") or None,
        "pdf_path": str(path),
        "pdf_sha256": hashlib.sha256(raw).hexdigest(),
        "page_count": inspection.page_count,
        "text_char_count": inspection.text_char_count,
        "text_sha256": inspection.text_sha256,
        "text_sketch": inspection.text_sketch,
        "identity": identity,
        "recognition": {
            "paper_mode": profile.get("paper_mode"),
            "paper_mode_label": profile.get("paper_mode_label"),
            "primary_type": profile.get("primary_type"),
            "primary_label": profile.get("primary_label"),
            "mode_confidence": profile.get("mode_confidence"),
            "mode_scores": profile.get("mode_scores"),
        },
    }


def _build_config(records: list[dict[str, Any]]) -> dict[str, Any]:
    old = json.loads(OLD_CONFIG.read_text(encoding="utf-8"))
    retained = [
        item for item in old["papers"]
        if str(item.get("doi") or "").casefold() not in REPLACED_INVALID_DOIS
    ]
    additions = [
        {key: record[key] for key in ("doi", "role", "queries", "min_rows")}
        for record in records
    ]
    papers = retained + additions
    if len(retained) != 30 or len(papers) != 50:
        raise RuntimeError(f"expected 30 retained + 20 added papers, got {len(retained)} + {len(additions)}")
    return {
        "version": "full-corpus-50-v1",
        "description": (
            "真实性迭代固定回归集：保留旧35篇中通过PDF内容与身份双门禁的30篇，"
            "并加入20篇经本地DOI/题目复核的真实PDF；用于验证研究模式、自动抽取、"
            "六列数据、原文定位、图表证据与全库搜索。"
        ),
        "expected_paper_count": 50,
        "calibration_limit_per_paper": int(old.get("calibration_limit_per_paper") or 20),
        "replaced_invalid_dois": sorted(REPLACED_INVALID_DOIS),
        "papers": papers,
    }


def _register(db: EvidenceDB, records: list[dict[str, Any]]) -> None:
    for record in records:
        paper_id = db.upsert_paper(
            title=record["title"],
            doi=record["doi"],
            year=record["year"],
            first_author=record["first_author"],
            corresponding_author=record["corresponding_author"],
            zotero_key=record["zotero_key"],
            pdf_path=record["pdf_path"],
            pdf_sha256=record["pdf_sha256"],
            authenticity_status="verified_pdf",
            parse_status="queued",
        )
        record["paper_id"] = paper_id
    UploadService(db).index_existing_pdfs()
    with db.connect() as conn:
        for record in records:
            paper_id = int(record["paper_id"])
            document = conn.execute(
                "SELECT id FROM documents WHERE paper_id=? AND stored_path=?",
                (paper_id, record["pdf_path"]),
            ).fetchone()
            existing = conn.execute(
                "SELECT id FROM processing_jobs WHERE paper_id=? AND job_type='extract' "
                "AND status IN ('queued','running','completed') ORDER BY id DESC LIMIT 1",
                (paper_id,),
            ).fetchone()
            if document and not existing:
                stamp = now()
                conn.execute(
                    "INSERT INTO processing_jobs(paper_id,document_id,job_type,status,provider,message,created_at,updated_at) "
                    "VALUES(?,?, 'extract','queued','deepseek',?,?,?)",
                    (paper_id, int(document["id"]), "真实PDF已通过身份门禁，等待自动质量流程", stamp, stamp),
                )


def _write_reports(config: dict[str, Any], records: list[dict[str, Any]], *, committed: bool) -> None:
    NEW_CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload = {
        "version": config["version"],
        "committed": committed,
        "retained_valid_from_35": 30,
        "excluded_invalid_from_35": 5,
        "added_real_pdfs": len(records),
        "expected_paper_count": 50,
        "excluded_dois": sorted(REPLACED_INVALID_DOIS),
        "additions": records,
    }
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# 50 篇真实性回归集清单",
        "",
        "- 保留旧集合中通过内容与身份双门禁的论文：30 篇",
        "- 排除下载/验证占位文件：5 篇",
        f"- 新增真实本地 PDF：{len(records)} 篇",
        f"- 已写入证据数据库：{'是' if committed else '否（预检）'}",
        "",
        "| DOI | 题目 | 页数 | 研究模式 | 主类型 | 身份依据 |",
        "|---|---|---:|---|---|---|",
    ]
    for item in records:
        lines.append(
            f"| {item['doi']} | {item['title']} | {item['page_count']} | "
            f"{item['recognition']['paper_mode_label']} | {item['recognition']['primary_label']} | "
            f"{item['identity']['status']} ({item['identity']['title_match_score']}) |"
        )
    lines.extend(["", "排除的 5 个 DOI 仍保留在旧 35 篇审计中，不能作为真实 PDF 成功项。", ""])
    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true", help="Register the 20 additions and queue extraction")
    args = parser.parse_args()
    rows = _verification_rows()
    records = []
    for doi, role, query in ADDITIONS:
        if doi not in rows:
            raise KeyError(f"verified Zotero record not found: {doi}")
        records.append(_paper_record(doi, role, query, rows[doi]))
    config = _build_config(records)
    if args.commit:
        _register(EvidenceDB(DB_PATH), records)
    _write_reports(config, records, committed=args.commit)
    print(json.dumps({
        "ok": True,
        "committed": args.commit,
        "config": str(NEW_CONFIG),
        "manifest": str(OUTPUT_JSON),
        "paper_count": 50,
        "added": len(records),
        "modes": [record["recognition"]["paper_mode"] for record in records],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
