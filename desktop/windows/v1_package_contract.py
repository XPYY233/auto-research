from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from auto_research.product.evidence_package import verify_evidence_package
from auto_research.product.official_package_store import (
    import_official_evidence_package,
    open_active_official_repository,
)
from auto_research.product.trusted_publishers import (
    trusted_public_keys,
    trusted_publisher_policy,
)


CONTROL_MEMBERS = frozenset({"manifest.json", "checksums.json", "signature.json"})
OFFICIAL_PAYLOAD_MEMBERS = frozenset(
    {"evidence/repository.sqlite", "provenance/sources.json", "rights/licenses.json"}
)


class WindowsV1PackageContractError(RuntimeError):
    """Path-free failure for the frozen Windows v1 package gate."""


@dataclass(frozen=True)
class WindowsV1PackageReport:
    sha256: str
    package_version: str
    signer_key_id: str
    content_fingerprint: str
    document_count: int
    remake_required: bool = False


def _load_contract(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WindowsV1PackageContractError("v1 资料包契约不可用") from exc
    if not isinstance(value, dict) or value.get("contract") != "auto-research-windows-official-package-v1":
        raise WindowsV1PackageContractError("v1 资料包契约无效")
    return value


def verify_windows_v1_official_package(
    package_path: Path,
    *,
    contract_path: Path | None = None,
) -> WindowsV1PackageReport:
    contract = _load_contract(contract_path or Path(__file__).with_name("official-package-v1.json"))
    package = package_path.expanduser().resolve()
    if not package.is_file() or package.name != contract["file_name"]:
        raise WindowsV1PackageContractError("请选择冻结的 v1 官方资料包")
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    if digest != contract["sha256"]:
        raise WindowsV1PackageContractError("v1 官方资料包字节哈希不一致")

    verified = verify_evidence_package(
        package,
        trusted_public_keys=trusted_public_keys(channel="internal-preview"),
        current_app_version="1.0.0-windows.rc.1",
        expected_evidence_schema=1,
    )
    manifest = verified.manifest
    expected_manifest = {
        "package_id": contract["package_id"],
        "package_version": contract["package_version"],
        "database_contract": contract["database_contract"],
        "evidence_schema": contract["distribution_schema"],
        "identity_version": contract["identity_version"],
        "content_fingerprint": contract["content_fingerprint"],
        "content_counts": contract["counts"],
        "app_compatibility": {
            "minimum": contract["minimum_app_version"],
            "maximum_exclusive": contract["maximum_app_version_exclusive"],
        },
    }
    if any(manifest.get(key) != value for key, value in expected_manifest.items()):
        raise WindowsV1PackageContractError("v1 官方资料包清单与冻结契约不一致")
    if verified.signer_key_id != contract["signer_key_id"]:
        raise WindowsV1PackageContractError("v1 官方资料包签名身份不一致")
    if set(verified.payload_members) != OFFICIAL_PAYLOAD_MEMBERS:
        raise WindowsV1PackageContractError("v1 官方资料包混入了非官方或私人载荷")
    with zipfile.ZipFile(package, "r") as archive:
        if set(archive.namelist()) != CONTROL_MEMBERS | OFFICIAL_PAYLOAD_MEMBERS:
            raise WindowsV1PackageContractError("v1 官方资料包成员集合不一致")

    # A temporary official root exercises checksum, signature, rights policy,
    # distribution-sqlite-v1 audit and active selector without touching a user DB.
    with tempfile.TemporaryDirectory(prefix="auto-research-windows-v1-package-") as directory:
        official_root = Path(directory) / "official"
        policy = trusted_publisher_policy(channel="internal-preview")
        import_official_evidence_package(
            package,
            data_root=official_root,
            current_app_version="1.0.0-windows.rc.1",
            publisher_policy=policy,
        )
        active, repository = open_active_official_repository(
            data_root=official_root,
            current_app_version="1.0.0-windows.rc.1",
            publisher_policy=policy,
        )
        document_count = sum(1 for _ in repository.iter_search_documents())
        if (
            active.package_id != contract["package_id"]
            or active.package_version != contract["package_version"]
            or active.content_fingerprint != contract["content_fingerprint"]
            or document_count != contract["counts"]["entities"]
            or (official_root / "private").exists()
        ):
            raise WindowsV1PackageContractError("v1 官方仓库审计或官方/私人隔离失败")

    return WindowsV1PackageReport(
        sha256=digest,
        package_version=str(manifest["package_version"]),
        signer_key_id=verified.signer_key_id,
        content_fingerprint=str(manifest["content_fingerprint"]),
        document_count=document_count,
    )
