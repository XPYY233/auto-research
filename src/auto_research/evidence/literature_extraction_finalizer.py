from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Mapping

from .db import EvidenceDB, now
from .fact_model import classify_nonreportable_row
from .literature_extraction_job import (
    LiteratureExtractionJobError,
    ValidatedLiteraturePackage,
    _canonical_bytes,
    _plain,
)
from .quality_pipeline import (
    PUBLISHABLE_STATUSES,
    _finding_stable_key,
    _measurement_payload,
)
from .six_column import (
    _ai_stable_key,
    _context_from_ai_measurement,
    is_reportable_value_text,
)


FINALIZER_SCHEMA_VERSION = "atomic-literature-finalizer-v1"
VALIDATED_SCHEMA_VERSION = "literature-extraction-validated-v1"
_SUPPORTED_GATES = frozenset({"dual_pass", "third_pass", "manual_review"})
_PUBLISHABLE = frozenset({"dual_pass", "third_pass"})


class AtomicEvidenceDBFinalizer:
    """Commit one validated text-evidence package in one SQLite transaction."""

    def __init__(
        self,
        db: EvidenceDB,
        *,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._db = db
        self._fault = fault_injector
        self._lock = threading.Lock()

    def finalize(self, package: ValidatedLiteraturePackage) -> Mapping[str, Any]:
        payload = self._validate_package(package)
        commit_fingerprint = hashlib.sha256(_canonical_bytes({
            "schema_version": FINALIZER_SCHEMA_VERSION,
            "paper_id": package.paper_id,
            "pdf_sha256": package.pdf_sha256,
            "snapshot_fingerprint": package.snapshot_fingerprint,
            "quality_result": payload,
        })).hexdigest()
        with self._lock:
            return self._commit(package, payload, commit_fingerprint)

    def _commit(
        self,
        package: ValidatedLiteraturePackage,
        payload: dict[str, Any],
        commit_fingerprint: str,
    ) -> Mapping[str, Any]:
        connection = sqlite3.connect(self._db.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            connection.execute("BEGIN IMMEDIATE")
            paper = connection.execute(
                "SELECT id,title,doi,pdf_sha256 FROM papers WHERE id=?", (package.paper_id,)
            ).fetchone()
            if not paper:
                raise LiteratureExtractionJobError(
                    "literature_commit_failed", "目标文献不存在，未保存任何科学记录"
                )
            if (
                str(paper["title"] or "") != str(package.paper.get("title") or "")
                or str(paper["doi"] or "") != str(package.paper.get("doi") or "")
            ):
                raise LiteratureExtractionJobError(
                    "literature_commit_failed", "目标文献身份已变化，未保存任何科学记录"
                )
            existing = self._find_existing(connection, package.paper_id, commit_fingerprint)
            if existing is not None:
                connection.commit()
                return self._public_result(
                    package, existing["summary"], idempotent=True
                )
            stamp = now()
            threshold = float(payload["summary"]["quality_threshold"])
            internal_summary = {
                **payload["summary"],
                "commit_schema_version": FINALIZER_SCHEMA_VERSION,
                "commit_fingerprint": commit_fingerprint,
                "visual_evidence_ready": False,
                "atomic_commit_ready": True,
            }
            run = connection.execute(
                """INSERT INTO quality_pipeline_runs(
                   paper_id,status,stage,progress,quality_threshold,summary_json,
                   created_at,finished_at
                   ) VALUES(?,'completed','completed',100,?,?,?,?)""",
                (
                    package.paper_id,
                    threshold,
                    self._json(internal_summary),
                    stamp,
                    stamp,
                ),
            )
            run_id = int(run.lastrowid)
            self._inject("after_run")
            published_items = 0
            existing_items = 0
            manual_review = 0
            for record in payload["records"]:
                item_id = None
                if record["gate_status"] in _PUBLISHABLE:
                    item_id, inserted = self._publish_item(
                        connection, package, record["entity_type"], record["candidate"], stamp
                    )
                    published_items += int(inserted)
                    existing_items += int(not inserted)
                else:
                    manual_review += 1
                connection.execute(
                    """INSERT INTO quality_candidates(
                       pipeline_run_id,paper_id,entity_type,candidate_key,chosen_source,
                       candidate_json,alternate_json,agreement_score,factuality_score,
                       completeness_score,evidence_score,overall_score,gate_status,gate_reason,
                       third_review_json,published_item_id,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        run_id,
                        package.paper_id,
                        record["entity_type"],
                        record["candidate_key"],
                        record["chosen_source"],
                        self._json(record["candidate"]),
                        self._json(record["alternate"]) if record.get("alternate") else None,
                        float(record["agreement_score"]),
                        float(record["factuality_score"]),
                        float(record["completeness_score"]),
                        float(record["evidence_score"]),
                        float(record["overall_score"]),
                        record["gate_status"],
                        str(record.get("gate_reason") or ""),
                        self._json(record["third_review"]) if record.get("third_review") else None,
                        item_id,
                        stamp,
                        stamp,
                    ),
                )
                self._inject("after_candidate")
            final_summary = {
                **internal_summary,
                "published_item_count": published_items,
                "existing_item_count": existing_items,
                "manual_review_count": manual_review,
            }
            connection.execute(
                "UPDATE quality_pipeline_runs SET summary_json=? WHERE id=?",
                (self._json(final_summary), run_id),
            )
            self._inject("before_commit")
            connection.commit()
            return self._public_result(package, final_summary, idempotent=False)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _find_existing(
        connection: sqlite3.Connection,
        paper_id: int,
        commit_fingerprint: str,
    ) -> dict[str, Any] | None:
        rows = connection.execute(
            """SELECT summary_json FROM quality_pipeline_runs
               WHERE paper_id=? AND status='completed' ORDER BY id DESC""",
            (paper_id,),
        ).fetchall()
        for row in rows:
            try:
                summary = json.loads(str(row["summary_json"] or "{}"))
            except json.JSONDecodeError:
                continue
            if summary.get("commit_fingerprint") == commit_fingerprint:
                return {"summary": summary}
        return None

    @staticmethod
    def _publish_item(
        connection: sqlite3.Connection,
        package: ValidatedLiteraturePackage,
        entity_type: str,
        candidate: dict[str, Any],
        stamp: str,
    ) -> tuple[int, bool]:
        if entity_type == "data":
            measurement = _measurement_payload(candidate)
            if not is_reportable_value_text(measurement["value_raw"]):
                raise ValueError("quality-passed numeric candidate is not reportable")
            stable_key = _ai_stable_key(measurement)
            fields = {
                "value_text": str(measurement["value_raw"]),
                "meaning": str(measurement["parameter"]),
                "unit": str(measurement.get("unit_raw") or ""),
                "context": _context_from_ai_measurement(measurement),
            }
            editor = "DeepSeek atomic quality gate"
            note = "Immutable automatic extraction committed by atomic staged quality gate"
        elif entity_type == "finding":
            fields = {
                "value_text": str(candidate.get("finding_text") or "").strip(),
                "meaning": str(candidate.get("meaning") or "").strip(),
                "unit": "",
                "context": str(candidate.get("context_explanation") or "").strip(),
            }
            if (
                not all(fields[key] for key in ("value_text", "meaning", "context"))
                or is_reportable_value_text(fields["value_text"])
                or classify_nonreportable_row(fields) != "qualitative_finding"
            ):
                raise ValueError("quality-passed finding is invalid")
            stable_key = _finding_stable_key(candidate)
            editor = "DeepSeek atomic quality gate"
            note = "Automatically separated from numeric data as a qualitative finding"
        else:
            raise ValueError("atomic text finalizer accepts only data and finding")
        existing = connection.execute(
            "SELECT id FROM data_items WHERE paper_id=? AND stable_key=?",
            (package.paper_id, stable_key),
        ).fetchone()
        if existing:
            return int(existing["id"]), False
        item = connection.execute(
            "INSERT INTO data_items(paper_id,stable_key,origin_type,created_at) VALUES(?,?,?,?)",
            (package.paper_id, stable_key, "automatic", stamp),
        )
        item_id = int(item.lastrowid)
        connection.execute(
            """INSERT INTO data_versions(
               item_id,version_no,value_text,meaning,unit,article_title,doi,
               context_explanation,source_page,source_locator,source_excerpt,
               editor,edit_note,review_action,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                item_id,
                0,
                fields["value_text"],
                fields["meaning"],
                fields["unit"],
                str(package.paper.get("title") or ""),
                str(package.paper.get("doi") or ""),
                fields["context"],
                int(candidate["source_page"]),
                str(candidate.get("source_locator") or ""),
                str(candidate.get("source_excerpt") or ""),
                editor,
                note,
                "automatic",
                stamp,
            ),
        )
        return item_id, True

    @staticmethod
    def _validate_package(package: ValidatedLiteraturePackage) -> dict[str, Any]:
        if not isinstance(package, ValidatedLiteraturePackage):
            raise LiteratureExtractionJobError(
                "literature_commit_failed", "抽取结果无效，未保存任何科学记录"
            )
        if (
            isinstance(package.paper_id, bool)
            or not isinstance(package.paper_id, int)
            or package.paper_id < 1
            or not isinstance(package.snapshot_fingerprint, str)
            or len(package.snapshot_fingerprint) != 64
            or not isinstance(package.pdf_sha256, str)
            or len(package.pdf_sha256) != 64
            or not str(package.paper.get("title") or "").strip()
        ):
            raise LiteratureExtractionJobError(
                "literature_commit_failed", "抽取来源身份无效，未保存任何科学记录"
            )
        payload = _plain(package.quality_result)
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != VALIDATED_SCHEMA_VERSION
            or not isinstance(payload.get("records"), list)
            or not isinstance(payload.get("summary"), dict)
            or not isinstance(payload.get("coverage"), dict)
            or payload["coverage"].get("visual_evidence_ready") is not False
        ):
            raise LiteratureExtractionJobError(
                "literature_commit_failed", "抽取质量包无效，未保存任何科学记录"
            )
        seen: set[str] = set()
        for record in payload["records"]:
            if (
                not isinstance(record, dict)
                or record.get("entity_type") not in {"data", "finding"}
                or record.get("gate_status") not in _SUPPORTED_GATES
                or record.get("chosen_source") not in {"extractor_a", "extractor_b", "merged"}
                or not isinstance(record.get("candidate"), dict)
                or not isinstance(record.get("candidate_key"), str)
                or not record["candidate_key"]
                or record["candidate_key"] in seen
            ):
                raise LiteratureExtractionJobError(
                    "literature_commit_failed", "抽取候选无效，未保存任何科学记录"
                )
            seen.add(record["candidate_key"])
            candidate = record["candidate"]
            if (
                isinstance(candidate.get("source_page"), bool)
                or not isinstance(candidate.get("source_page"), int)
                or candidate["source_page"] < 1
                or not str(candidate.get("source_excerpt") or "").strip()
            ):
                raise LiteratureExtractionJobError(
                    "literature_commit_failed", "抽取候选缺少原文证据，未保存任何科学记录"
                )
            if record["gate_status"] in _PUBLISHABLE and record["gate_status"] not in PUBLISHABLE_STATUSES:
                raise LiteratureExtractionJobError(
                    "literature_commit_failed", "抽取质量门无效，未保存任何科学记录"
                )
        if int(payload["summary"].get("candidate_count", -1)) != len(payload["records"]):
            raise LiteratureExtractionJobError(
                "literature_commit_failed", "抽取候选计数不一致，未保存任何科学记录"
            )
        expected_counts = {
            "dual_pass_count": sum(record["gate_status"] == "dual_pass" for record in payload["records"]),
            "third_pass_count": sum(record["gate_status"] == "third_pass" for record in payload["records"]),
            "manual_review_count": sum(record["gate_status"] == "manual_review" for record in payload["records"]),
        }
        if any(int(payload["summary"].get(key, -1)) != value for key, value in expected_counts.items()):
            raise LiteratureExtractionJobError(
                "literature_commit_failed", "抽取质量门计数不一致，未保存任何科学记录"
            )
        return payload

    @staticmethod
    def _public_result(
        package: ValidatedLiteraturePackage,
        summary: Mapping[str, Any],
        *,
        idempotent: bool,
    ) -> dict[str, Any]:
        return {
            "schema_version": "literature-extraction-commit-result-v1",
            "status": "completed",
            "paper": dict(package.paper),
            "candidate_count": int(summary.get("candidate_count", 0)),
            "published_item_count": int(summary.get("published_item_count", 0)),
            "existing_item_count": int(summary.get("existing_item_count", 0)),
            "manual_review_count": int(summary.get("manual_review_count", 0)),
            "visual_evidence_ready": False,
            "idempotent": idempotent,
        }

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(
            _plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )

    def _inject(self, stage: str) -> None:
        if self._fault is not None:
            self._fault(stage)


__all__ = ["AtomicEvidenceDBFinalizer", "FINALIZER_SCHEMA_VERSION"]
