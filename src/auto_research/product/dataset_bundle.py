"""Deterministic, path-free dataset bundles built from public evidence DTOs.

This module deliberately has no database dependency.  Callers must first resolve
and sanitize records through the existing public evidence repositories.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping, Protocol, Sequence


SCHEMA_VERSION = "dataset-bundle-v1"
ERROR_SCHEMA_VERSION = "dataset-bundle-error-v1"
RECORD_SCHEMA_VERSION = "dataset-record-v1"
ENTITY_TYPES = ("item", "finding", "table", "figure")
SPLITS = ("train", "validation", "test")
MAX_RECORDS = 100_000
MAX_TEXT_CHARS = 100_000
MAX_NODES = 2_000
MAX_DEPTH = 8

_SAFE_UID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,255}$")
_SAFE_DESTINATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_LOCAL_VALUE_RE = re.compile(
    r"(?i)(?:^|[\s='\"])(?:"
    r"(?:file|sqlite):|"
    r"(?:[a-z]:[\\/])|"
    r"(?:\\\\|//)[^/\\]|"
    r"~[/\\]|"
    r"/(?:Users|home|private|tmp|var|etc|usr|root|srv|mnt|media|Applications|Library|System)(?:/|$)|"
    r"/(?:[^/\s]+/)+[^/\s]*"
    r")"
)

_BANNED_KEYS = {
    "id",
    "paper_id",
    "item_id",
    "asset_id",
    "database_id",
    "db_id",
    "run_id",
    "draft_id",
    "import_id",
    "file_id",
    "selection_id",
    "source_file_id",
    "path",
    "pdf_path",
    "image_path",
    "file_path",
    "local_path",
    "api_key",
    "zotero_key",
    "secret",
    "password",
    "credential_ref",
    "session_id",
    "conversation_id",
    "state_token",
    "snapshot_token",
    "job_token",
    "consent_nonce",
    "sha256",
    "file_hash",
    "content_hash",
}

_PAPER_KEYS = {
    "paper_uid",
    "source_scope",
    "title",
    "doi",
    "year",
    "first_author",
    "corresponding_author",
    "material_focus",
    "rights_scope",
    "license",
}

_EVIDENCE_KEYS = {
    "source_scope",
    "source_id",
    "entity_uid",
    "entity_type",
    "paper_uid",
    "article_title",
    "doi",
    "year",
    "first_author",
    "corresponding_author",
    "material_focus",
    "quality_gate_status",
    "review_status",
    "record_status",
    "indexable",
    "value_text",
    "finding_text",
    "meaning",
    "unit",
    "context_explanation",
    "source_page",
    "source_locator",
    "source_excerpt",
    "evidence_occurrences",
    "evidence_type",
    "source_precision",
    "evidence_count",
    "label",
    "display_name",
    "caption",
    "page_start",
    "page_end",
    "conditions_text",
    "methods_text",
    "source_context",
    "physical_quantities",
    "materials",
    "tags",
    "variables",
    "asset_ref",
    # Sanitized private-experiment display fields.
    "title",
    "project",
    "sample",
    "material",
    "method",
    "conditions",
    "series",
    "sheet_name",
    "columns",
}

_ASSET_KEYS = {"asset_uid", "media_type", "rights_scope", "included"}
_REVIEWED_VALUES = {
    "dual_pass",
    "third_pass",
    "manual_approved",
    "published",
    "confirmed",
    "audited",
}
_SAFE_RIGHTS = {"internal-research", "redistributable", "open-license"}
_REQUIRED_PAPER_FIELDS = ("title", "doi", "year")
_REQUIRED_EVIDENCE_FIELDS = {
    "item": ("value_text", "meaning"),
    "finding": ("finding_text",),
    "table": ("caption",),
    "figure": ("caption",),
}


class DatasetBundleError(RuntimeError):
    """Stable, path-free dataset bundle failure."""

    def __init__(self, code: str, safe_message: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.details = dict(details or {})

    def public_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": ERROR_SCHEMA_VERSION,
            "code": self.code,
            "message": self.safe_message,
        }
        if self.details:
            result["details"] = dict(self.details)
        return result


class ParquetWriter(Protocol):
    def write(self, records: Sequence[Mapping[str, Any]], destination: Path) -> Mapping[str, Any]: ...


class PyArrowParquetWriter:
    """Lazy optional Parquet implementation; never writes a disguised file."""

    def write(self, records: Sequence[Mapping[str, Any]], destination: Path) -> Mapping[str, Any]:
        try:
            import pyarrow as pa  # type: ignore[import-not-found]
            import pyarrow.parquet as pq  # type: ignore[import-not-found]
        except (ImportError, ModuleNotFoundError) as exc:
            raise DatasetBundleError(
                "dataset_bundle_parquet_unavailable",
                "Parquet 组件不可用，未生成数据集。",
            ) from exc
        try:
            table = pa.Table.from_pylist(list(records))
            pq.write_table(
                table,
                destination,
                compression="zstd",
                use_dictionary=True,
                write_statistics=True,
            )
        except DatasetBundleError:
            raise
        except Exception as exc:
            raise DatasetBundleError(
                "dataset_bundle_write_failed",
                "Parquet 数据写入失败，未发布数据集。",
            ) from exc
        return {
            "engine": "pyarrow",
            "engine_version": str(getattr(pa, "__version__", "unknown"))[:64],
        }


@dataclass(frozen=True)
class DatasetBundlePlan:
    schema_version: str
    content_fingerprint: str
    include_private: bool
    entity_counts: tuple[tuple[str, int], ...]
    split_counts: tuple[tuple[str, int], ...]
    missing_fields: tuple[tuple[str, int], ...]
    unreviewed_count: int
    rights_risks: tuple[str, ...]
    record_count: int
    _paper_json: tuple[str, ...]
    _record_json: tuple[str, ...]

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "content_fingerprint": self.content_fingerprint,
            "include_private": self.include_private,
            "entity_counts": dict(self.entity_counts),
            "split_counts": dict(self.split_counts),
            "missing_fields": dict(self.missing_fields),
            "unreviewed_count": self.unreviewed_count,
            "rights_risks": list(self.rights_risks),
            "record_count": self.record_count,
            "binary_assets_included": False,
        }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _safe_uid(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_UID_RE.fullmatch(value):
        raise DatasetBundleError(
            "dataset_bundle_invalid",
            "数据集记录身份无效。",
            details={"field": field},
        )
    return value


def _bounded_public_value(value: Any, *, depth: int = 0, nodes: list[int] | None = None) -> Any:
    if nodes is None:
        nodes = [0]
    nodes[0] += 1
    if depth > MAX_DEPTH or nodes[0] > MAX_NODES:
        raise DatasetBundleError("dataset_bundle_invalid", "数据集记录结构超出安全限制。")
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise DatasetBundleError("dataset_bundle_invalid", "数据集包含无效数值。")
        return value
    if isinstance(value, str):
        if len(value) > MAX_TEXT_CHARS:
            raise DatasetBundleError("dataset_bundle_invalid", "数据集文本超出安全限制。")
        if _LOCAL_VALUE_RE.search(value):
            raise DatasetBundleError("dataset_bundle_invalid", "数据集记录包含本机位置。")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, child in value.items():
            if not isinstance(raw_key, str) or not raw_key or len(raw_key) > 80:
                raise DatasetBundleError("dataset_bundle_invalid", "数据集字段无效。")
            normalized = raw_key.casefold()
            if normalized in _BANNED_KEYS:
                raise DatasetBundleError(
                    "dataset_bundle_invalid",
                    "数据集记录包含禁止公开的字段。",
                    details={"field": normalized},
                )
            result[raw_key] = _bounded_public_value(child, depth=depth + 1, nodes=nodes)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, memoryview)):
        return [_bounded_public_value(child, depth=depth + 1, nodes=nodes) for child in value]
    raise DatasetBundleError("dataset_bundle_invalid", "数据集包含不支持的字段类型。")


def _sanitize_exact(raw: Mapping[str, Any], allowed: set[str], kind: str) -> dict[str, Any]:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise DatasetBundleError(
            "dataset_bundle_invalid",
            f"{kind}包含未允许的字段。",
            details={"field_count": min(len(unknown), 10)},
        )
    value = _bounded_public_value(raw)
    assert isinstance(value, dict)
    return value


def _sanitize_asset_ref(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise DatasetBundleError("dataset_bundle_invalid", "资产引用格式无效。")
    result = _sanitize_exact(raw, _ASSET_KEYS, "资产引用")
    _safe_uid(result.get("asset_uid"), "asset_uid")
    if result.get("included") is not False:
        raise DatasetBundleError("dataset_bundle_invalid", "数据集不得复制 PDF 或图片二进制。")
    if not isinstance(result.get("media_type"), str) or len(result["media_type"]) > 100:
        raise DatasetBundleError("dataset_bundle_invalid", "资产媒体类型无效。")
    return result


def _is_reviewed(record: Mapping[str, Any]) -> bool:
    statuses = {
        str(record.get("quality_gate_status", "")).casefold(),
        str(record.get("review_status", "")).casefold(),
        str(record.get("record_status", "")).casefold(),
    }
    return bool(statuses & _REVIEWED_VALUES)


def _split_for_paper(paper_uid: str) -> str:
    digest = hashlib.sha256(b"auto-research/dataset-split/v1\0" + paper_uid.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], "big") % 100
    if bucket < 80:
        return "train"
    if bucket < 90:
        return "validation"
    return "test"


def _file_metadata(path: Path) -> dict[str, Any]:
    stat = path.lstat()
    if path.is_symlink() or not path.is_file():
        raise DatasetBundleError("dataset_bundle_write_failed", "数据集文件校验失败，未发布。")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return {"name": path.name, "size": stat.st_size, "sha256": digest.hexdigest()}


class DatasetBundleBuilder:
    def __init__(self, parquet_writer: ParquetWriter | None = None):
        self._parquet_writer = parquet_writer or PyArrowParquetWriter()

    def plan(
        self,
        *,
        papers: Iterable[Mapping[str, Any]],
        evidence: Iterable[Mapping[str, Any]],
        include_private: bool = False,
        private_records: Iterable[Mapping[str, Any]] = (),
    ) -> DatasetBundlePlan:
        paper_rows: list[dict[str, Any]] = []
        paper_uids: set[str] = set()
        missing: dict[str, int] = {}
        rights_risks: set[str] = set()
        for raw in papers:
            if not isinstance(raw, Mapping):
                raise DatasetBundleError("dataset_bundle_invalid", "论文元数据格式无效。")
            paper = _sanitize_exact(raw, _PAPER_KEYS, "论文元数据")
            paper_uid = _safe_uid(paper.get("paper_uid"), "paper_uid")
            if paper_uid in paper_uids:
                raise DatasetBundleError("dataset_bundle_invalid", "论文身份重复。")
            paper_uids.add(paper_uid)
            for field in _REQUIRED_PAPER_FIELDS:
                if paper.get(field) in (None, ""):
                    missing[f"paper.{field}"] = missing.get(f"paper.{field}", 0) + 1
            rights = str(paper.get("rights_scope", "")).casefold()
            if rights not in _SAFE_RIGHTS:
                rights_risks.add(f"paper_rights:{paper_uid}")
            paper_rows.append(paper)

        regular_records = list(evidence)
        private_rows = list(private_records)
        if private_rows and not include_private:
            raise DatasetBundleError(
                "dataset_bundle_private_forbidden",
                "私人实验记录未获本次导出授权。",
            )
        if len(regular_records) + len(private_rows) > MAX_RECORDS:
            raise DatasetBundleError("dataset_bundle_invalid", "数据集记录数量超出安全限制。")

        record_rows: list[dict[str, Any]] = []
        identities: set[tuple[str, str, str]] = set()
        counts = {kind: 0 for kind in ENTITY_TYPES}
        split_counts = {split: 0 for split in SPLITS}
        unreviewed = 0
        for raw, is_private_input in [
            *((row, False) for row in regular_records),
            *((row, True) for row in private_rows),
        ]:
            if not isinstance(raw, Mapping):
                raise DatasetBundleError("dataset_bundle_invalid", "证据记录格式无效。")
            record = _sanitize_exact(raw, _EVIDENCE_KEYS, "证据记录")
            entity_type = record.get("entity_type")
            if entity_type not in ENTITY_TYPES:
                raise DatasetBundleError("dataset_bundle_invalid", "证据类型不受支持。")
            source_scope = record.get("source_scope")
            if source_scope not in {"workspace", "official", "private"}:
                raise DatasetBundleError("dataset_bundle_invalid", "证据来源范围无效。")
            if is_private_input:
                if source_scope != "private" or record.get("record_status") != "confirmed" or record.get("indexable") is not True:
                    raise DatasetBundleError("dataset_bundle_unreviewed", "私人实验记录尚未确认并进入检索。")
            elif source_scope == "private":
                raise DatasetBundleError(
                    "dataset_bundle_private_forbidden",
                    "私人实验记录必须通过单独的净化输入提供。",
                )
            source_id = _safe_uid(record.get("source_id"), "source_id")
            entity_uid = _safe_uid(record.get("entity_uid"), "entity_uid")
            paper_uid = _safe_uid(record.get("paper_uid"), "paper_uid")
            if not is_private_input and paper_uid not in paper_uids:
                raise DatasetBundleError("dataset_bundle_invalid", "证据缺少对应论文元数据。")
            identity = (source_scope, source_id, entity_uid)
            if identity in identities:
                raise DatasetBundleError("dataset_bundle_invalid", "证据身份重复。")
            identities.add(identity)
            if "asset_ref" in record:
                record["asset_ref"] = _sanitize_asset_ref(record["asset_ref"])
                rights = str(record["asset_ref"].get("rights_scope", "")).casefold()
                if rights not in _SAFE_RIGHTS:
                    rights_risks.add(f"asset_rights:{source_scope}:{source_id}:{entity_uid}")
            if not _is_reviewed(record):
                unreviewed += 1
            for field in _REQUIRED_EVIDENCE_FIELDS[entity_type]:
                if record.get(field) in (None, ""):
                    missing[f"{entity_type}.{field}"] = missing.get(f"{entity_type}.{field}", 0) + 1
            split = _split_for_paper(paper_uid)
            record["split"] = split
            counts[entity_type] += 1
            split_counts[split] += 1
            record_rows.append(record)

        paper_rows.sort(key=lambda row: row["paper_uid"])
        record_rows.sort(
            key=lambda row: (row["split"], row["paper_uid"], row["entity_type"], row["source_scope"], row["source_id"], row["entity_uid"])
        )
        paper_json = tuple(_canonical_json(row) for row in paper_rows)
        record_json = tuple(_canonical_json(row) for row in record_rows)
        fingerprint_payload = {
            "schema_version": SCHEMA_VERSION,
            "include_private": bool(include_private),
            "papers": paper_rows,
            "records": record_rows,
        }
        fingerprint = hashlib.sha256(_canonical_json(fingerprint_payload).encode("utf-8")).hexdigest()
        return DatasetBundlePlan(
            schema_version=SCHEMA_VERSION,
            content_fingerprint=fingerprint,
            include_private=bool(include_private),
            entity_counts=tuple((kind, counts[kind]) for kind in ENTITY_TYPES),
            split_counts=tuple((split, split_counts[split]) for split in SPLITS),
            missing_fields=tuple(sorted(missing.items())),
            unreviewed_count=unreviewed,
            rights_risks=tuple(sorted(rights_risks)),
            record_count=len(record_rows),
            _paper_json=paper_json,
            _record_json=record_json,
        )

    def publish(
        self,
        plan: DatasetBundlePlan,
        destination: str | os.PathLike[str],
        *,
        rights_acknowledged: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(plan, DatasetBundlePlan) or plan.schema_version != SCHEMA_VERSION:
            raise DatasetBundleError("dataset_bundle_invalid", "数据集计划无效。")
        papers, records = self._verified_plan_rows(plan)
        if plan.unreviewed_count:
            raise DatasetBundleError(
                "dataset_bundle_unreviewed",
                "数据集中仍有未审核记录，未发布。",
                details={"count": plan.unreviewed_count},
            )
        if plan.rights_risks and not rights_acknowledged:
            raise DatasetBundleError(
                "dataset_bundle_rights_unconfirmed",
                "数据权利风险尚未确认，未发布。",
                details={"count": len(plan.rights_risks)},
            )
        target = Path(destination)
        if target.name in {"", ".", ".."} or not _SAFE_DESTINATION_RE.fullmatch(target.name):
            raise DatasetBundleError("dataset_bundle_unsafe_destination", "数据集目标名称无效。")
        parent = target.parent
        try:
            if parent.is_symlink() or not parent.is_dir() or target.exists() or target.is_symlink():
                code = "dataset_bundle_destination_exists" if target.exists() or target.is_symlink() else "dataset_bundle_unsafe_destination"
                raise DatasetBundleError(code, "数据集目标不可用。")
        except OSError as exc:
            raise DatasetBundleError("dataset_bundle_unsafe_destination", "数据集目标不可用。") from exc

        staging: Path | None = None
        try:
            staging = Path(tempfile.mkdtemp(prefix=".dataset-bundle-", dir=parent))
            papers_by_uid = {row["paper_uid"]: row for row in papers}
            jsonl_rows: list[dict[str, Any]] = []
            parquet_rows: list[dict[str, Any]] = []
            for evidence in records:
                paper = papers_by_uid.get(evidence["paper_uid"], {})
                row = {
                    "schema_version": RECORD_SCHEMA_VERSION,
                    "split": evidence.pop("split"),
                    "paper_uid": evidence["paper_uid"],
                    "paper": paper,
                    "evidence": evidence,
                }
                jsonl_rows.append(row)
                parquet_rows.append(
                    {
                        "schema_version": RECORD_SCHEMA_VERSION,
                        "split": row["split"],
                        "paper_uid": row["paper_uid"],
                        "source_scope": evidence["source_scope"],
                        "source_id": evidence["source_id"],
                        "entity_type": evidence["entity_type"],
                        "entity_uid": evidence["entity_uid"],
                        "paper_json": _canonical_json(paper),
                        "evidence_json": _canonical_json(evidence),
                    }
                )

            jsonl_path = staging / "dataset.jsonl"
            with jsonl_path.open("x", encoding="utf-8", newline="\n") as stream:
                for row in jsonl_rows:
                    stream.write(_canonical_json(row) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

            parquet_path = staging / "dataset.parquet"
            parquet_meta = dict(self._parquet_writer.write(tuple(parquet_rows), parquet_path))
            if not parquet_path.is_file() or parquet_path.is_symlink():
                raise DatasetBundleError("dataset_bundle_write_failed", "Parquet 文件未正确生成，未发布。")
            with parquet_path.open("rb") as stream:
                header = stream.read(4)
                stream.seek(-4, os.SEEK_END)
                footer = stream.read(4)
            if header != b"PAR1" or footer != b"PAR1":
                raise DatasetBundleError("dataset_bundle_write_failed", "Parquet 文件格式校验失败，未发布。")
            with parquet_path.open("rb") as stream:
                os.fsync(stream.fileno())

            card_path = staging / "DATA_CARD.md"
            card = self._data_card(plan)
            with card_path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(card)
                stream.flush()
                os.fsync(stream.fileno())

            files = [_file_metadata(path) for path in (jsonl_path, parquet_path, card_path)]
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "content_fingerprint": plan.content_fingerprint,
                "record_count": plan.record_count,
                "entity_counts": dict(plan.entity_counts),
                "split_counts": dict(plan.split_counts),
                "missing_fields": dict(plan.missing_fields),
                "unreviewed_count": plan.unreviewed_count,
                "rights_risks": list(plan.rights_risks),
                "rights_acknowledged": bool(rights_acknowledged),
                "include_private": plan.include_private,
                "binary_assets_included": False,
                "parquet": _bounded_public_value(parquet_meta),
                "files": files,
            }
            manifest_path = staging / "manifest.json"
            with manifest_path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(_canonical_json(manifest) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staging, target)
            staging = None
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "published",
                "content_fingerprint": plan.content_fingerprint,
                "record_count": plan.record_count,
                "entity_counts": dict(plan.entity_counts),
                "split_counts": dict(plan.split_counts),
                "binary_assets_included": False,
            }
        except DatasetBundleError:
            raise
        except FileExistsError as exc:
            raise DatasetBundleError("dataset_bundle_destination_exists", "数据集目标已存在。") from exc
        except Exception as exc:
            raise DatasetBundleError("dataset_bundle_write_failed", "数据集写入失败，未发布。") from exc
        finally:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)

    def _verified_plan_rows(
        self, plan: DatasetBundlePlan
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        try:
            papers = [json.loads(text) for text in plan._paper_json]
            records_with_splits = [json.loads(text) for text in plan._record_json]
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DatasetBundleError("dataset_bundle_invalid", "数据集计划内容无效。") from exc
        records: list[dict[str, Any]] = []
        for raw in records_with_splits:
            if not isinstance(raw, dict) or raw.get("split") not in SPLITS:
                raise DatasetBundleError("dataset_bundle_invalid", "数据集计划分组无效。")
            record = dict(raw)
            record.pop("split")
            records.append(record)
        regular = [row for row in records if row.get("source_scope") != "private"]
        private = [row for row in records if row.get("source_scope") == "private"]
        rebuilt = self.plan(
            papers=papers,
            evidence=regular,
            include_private=plan.include_private,
            private_records=private,
        )
        if rebuilt != plan:
            raise DatasetBundleError("dataset_bundle_invalid", "数据集计划校验失败。")
        return papers, records_with_splits

    @staticmethod
    def _data_card(plan: DatasetBundlePlan) -> str:
        entity_lines = "\n".join(f"- {kind}: {count}" for kind, count in plan.entity_counts)
        split_lines = "\n".join(f"- {split}: {count}" for split, count in plan.split_counts)
        missing_lines = "\n".join(f"- {field}: {count}" for field, count in plan.missing_fields) or "- 无"
        risk_lines = "\n".join(f"- {risk}" for risk in plan.rights_risks) or "- 无"
        return (
            "# Auto Research Dataset Card\n\n"
            f"Schema: `{SCHEMA_VERSION}`  \n"
            f"Content fingerprint: `{plan.content_fingerprint}`  \n"
            f"Records: {plan.record_count}  \n"
            f"Private records included: {'yes' if plan.include_private else 'no'}  \n"
            "PDF/image binaries included: no\n\n"
            "## Evidence types\n\n"
            f"{entity_lines}\n\n"
            "## Deterministic paper-level splits\n\n"
            f"{split_lines}\n\n"
            "All records sharing one `paper_uid` are assigned to the same split.\n\n"
            "## Missing fields\n\n"
            f"{missing_lines}\n\n"
            "## Rights risks requiring acknowledgement\n\n"
            f"{risk_lines}\n\n"
            "Asset references are metadata-only. This bundle does not copy PDFs or images.\n"
        )


__all__ = [
    "DatasetBundleBuilder",
    "DatasetBundleError",
    "DatasetBundlePlan",
    "ParquetWriter",
    "PyArrowParquetWriter",
    "SCHEMA_VERSION",
]
