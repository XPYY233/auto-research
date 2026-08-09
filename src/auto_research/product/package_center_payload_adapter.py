"""Bridge structured transfer payloads into the shared package-center contract."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from .package_center_models import (
    MaterializedPayload,
    PackageCenterError,
    PackageKind,
    PayloadPlanCandidate as CenterPayloadPlanCandidate,
    RightsConfirmation,
    RightsRequirement,
)
from .package_transfer_payloads import (
    OA_LICENSES,
    LiteratureCollectionPayloadPlanner,
    LiteraturePayloadSelection,
    PayloadPlanCandidate as StructuredPayloadPlanCandidate,
    PayloadSelection,
    PayloadSelectionMode,
    PersonalExperimentsPayloadPlanner,
    PersonalPayloadSelection,
    materialize_payload_candidate,
)
from .transfer_package import TransferFileRights, TransferPackageError


MAX_WORKSPACE_PARENT_FILES = 256


@dataclass(frozen=True)
class _CenterPayload:
    structured: StructuredPayloadPlanCandidate
    fingerprint: str


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _file_identity(path_value: Path | str | None) -> dict[str, Any]:
    if path_value is None:
        return {"state": "missing"}
    path = Path(path_value).expanduser()
    try:
        metadata = path.lstat()
    except OSError:
        return {"state": "missing"}
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        return {"state": "unsafe"}
    return {
        "state": "file",
        "device": int(metadata.st_dev),
        "inode": int(metadata.st_ino),
        "size": int(metadata.st_size),
        "mtime_ns": int(metadata.st_mtime_ns),
        "ctime_ns": int(metadata.st_ctime_ns),
    }


def structured_candidate_fingerprint(candidate: StructuredPayloadPlanCandidate) -> str:
    payload = candidate._payload
    if isinstance(payload, LiteraturePayloadSelection):
        files = [
            {
                "paper_uid": item.paper_uid,
                "file_name": item.file_name,
                "license_id": item.license_id,
                "license_verified": item.license_verified,
                "rights_allowed": bool(
                    item.rights and item.rights.redistribution_allowed
                ),
                "rights_basis": item.rights.basis if item.rights else "",
                "identity": _file_identity(item.source_path),
            }
            for item in payload.pdfs
        ]
        content = {
            "kind": candidate.kind.value,
            "source_id": candidate.source_id,
            "papers": payload.papers,
            "entities": payload.entities,
            "files": files,
        }
    elif isinstance(payload, PersonalPayloadSelection):
        content = {
            "kind": candidate.kind.value,
            "source_id": candidate.source_id,
            "records": payload.records,
            "files": [
                {
                    "file_name": item.file_name,
                    "run_uid": item.run_uid,
                    "identity": _file_identity(item.source_path),
                }
                for item in payload.tables
            ],
        }
    else:
        raise PackageCenterError("package_plan_invalid", "结构化资料包计划无效。")
    return hashlib.sha256(_canonical(content)).hexdigest()


def _selection(scope: str, selection: Any) -> PayloadSelection:
    try:
        mode = PayloadSelectionMode(scope)
    except ValueError as exc:
        raise PackageCenterError("package_scope_invalid", "资料包范围无效。") from exc
    if mode is PayloadSelectionMode.SELECTED:
        return PayloadSelection(mode, selected_ids=tuple(selection or ()))
    if mode is PayloadSelectionMode.FILTERED:
        return PayloadSelection(
            mode,
            filter_token=_canonical(selection).decode("utf-8"),
        )
    return PayloadSelection(mode)


def _requires_rights(item: Any) -> bool:
    license_id = str(item.license_id or "").strip().casefold()
    if item.license_verified and license_id in OA_LICENSES:
        return False
    # Prior metadata or an earlier export declaration is never authority for
    # this export. Every non-OA PDF needs a fresh per-paper acknowledgement.
    return True


class StructuredPackageCenterPayloadAdapter:
    """One platform-neutral adapter used by both desktop shells."""

    def __init__(
        self,
        *,
        literature_planner: LiteratureCollectionPayloadPlanner,
        personal_planner: PersonalExperimentsPayloadPlanner,
        workspace_parent: Path | str,
    ) -> None:
        self._literature = literature_planner
        self._personal = personal_planner
        parent = Path(workspace_parent).expanduser().absolute()
        if parent.is_symlink():
            raise PackageCenterError(
                "package_workspace_unsafe", "资料包临时目录不安全。"
            )
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if parent.is_symlink() or not parent.is_dir():
            raise PackageCenterError(
                "package_workspace_unsafe", "资料包临时目录不安全。"
            )
        if os.name != "nt":
            os.chmod(parent, 0o700)
        self._workspace_parent = parent

    def plan(self, *, kind: str, scope: str, selection: Any) -> CenterPayloadPlanCandidate:
        try:
            normalized_kind = PackageKind(kind)
            structured = (
                self._literature if normalized_kind is PackageKind.LITERATURE_COLLECTION
                else self._personal
            ).plan(_selection(scope, selection))
            fingerprint = structured_candidate_fingerprint(structured)
        except PackageCenterError:
            raise
        except Exception as exc:
            code = getattr(exc, "code", "package_plan_failed")
            message = getattr(exc, "safe_message", "无法生成结构化资料包计划。")
            raise PackageCenterError(str(code), str(message)) from None
        kind_label = "lit" if normalized_kind is PackageKind.LITERATURE_COLLECTION else "personal"
        identity = hashlib.sha256(structured.source_id.encode("utf-8")).hexdigest()[:16]
        requirements: tuple[RightsRequirement, ...] = ()
        if isinstance(structured._payload, LiteraturePayloadSelection):
            titles = {
                str(row.get("paper_uid") or ""): str(row.get("title") or "未命名论文")
                for row in structured._payload.papers
            }
            requirements = tuple(
                RightsRequirement(item.paper_uid, titles.get(item.paper_uid, "未命名论文"))
                for item in structured._payload.pdfs
                if _file_identity(item.source_path).get("state") == "file"
                and _requires_rights(item)
            )
        return CenterPayloadPlanCandidate(
            package_id=f"user-{kind_label}-{identity}",
            package_version=f"1.0.0+{fingerprint[:12]}",
            content_fingerprint=fingerprint,
            estimated_bytes=structured.estimated_bytes,
            item_count=structured.entity_count,
            paper_count=(
                structured.record_count
                if normalized_kind is PackageKind.LITERATURE_COLLECTION
                else 0
            ),
            missing_pdf_count=structured.missing_pdf_count,
            rights_requirements=requirements,
            exceeds_size_limit=structured.exceeds_size_limit,
            payload=_CenterPayload(structured, fingerprint),
        )

    def current_content_fingerprint(self, candidate: CenterPayloadPlanCandidate) -> str:
        payload = self._payload(candidate)
        planner = (
            self._literature
            if payload.structured.kind.value == PackageKind.LITERATURE_COLLECTION.value
            else self._personal
        )
        refreshed = planner.plan(payload.structured.selection)
        return structured_candidate_fingerprint(refreshed)

    def materialize(
        self,
        candidate: CenterPayloadPlanCandidate,
        *,
        rights_confirmations: Mapping[str, RightsConfirmation],
    ) -> MaterializedPayload:
        payload = self._payload(candidate)
        structured = payload.structured
        if isinstance(structured._payload, LiteraturePayloadSelection):
            pdfs = []
            for item in structured._payload.pdfs:
                confirmation = rights_confirmations.get(item.paper_uid)
                if _requires_rights(item) and confirmation is not None:
                    item = replace(
                        item,
                        rights=TransferFileRights(True, confirmation.basis),
                        rights_confirmed_for_export=True,
                    )
                pdfs.append(item)
            structured = replace(
                structured,
                _payload=replace(structured._payload, pdfs=tuple(pdfs)),
            )
        operation = Path(
            tempfile.mkdtemp(prefix=".package-center-", dir=self._workspace_parent)
        )
        try:
            if os.name != "nt":
                os.chmod(operation, 0o700)
            export_plan = materialize_payload_candidate(
                structured,
                operation / "payload",
                package_id=candidate.package_id,
                package_version=candidate.package_version,
            )
        except Exception:
            shutil.rmtree(operation, ignore_errors=True)
            raise
        return MaterializedPayload(
            export_plan,
            lambda: shutil.rmtree(operation, ignore_errors=True),
        )

    @staticmethod
    def _payload(candidate: CenterPayloadPlanCandidate) -> _CenterPayload:
        payload = candidate.payload
        if (
            not isinstance(payload, _CenterPayload)
            or payload.fingerprint != candidate.content_fingerprint
        ):
            raise PackageCenterError("package_plan_invalid", "资料包计划内容无效。")
        return payload


__all__ = [
    "StructuredPackageCenterPayloadAdapter",
    "structured_candidate_fingerprint",
]
