from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .evidence_package import (
    EvidencePackageError,
    ImportedEvidencePackage,
    PACKAGE_ID_RE,
    PACKAGE_VERSION_RE,
    _read_active_state,
    _validate_installed_tree,
    import_evidence_package,
    rollback_evidence_package,
)
from .portable_repository import (
    DATABASE_CONTRACT,
    DATABASE_PATH,
    DISTRIBUTION_SCHEMA_VERSION,
    IDENTITY_VERSION,
    PROVENANCE_PATH,
    RIGHTS_PATH,
    OfficialEvidenceRepository,
)
from .trusted_publishers import (
    TrustedPublisherPolicy,
    TrustedPublisherPolicyError,
    trusted_publisher_policy,
)


EXPECTED_DISTRIBUTION_SCHEMA = DISTRIBUTION_SCHEMA_VERSION
ACTIVE_SELECTOR_RELATIVE_PATH = Path("official-packages") / "active.json"
DEFAULT_OFFICIAL_CHANNEL = "internal-preview"


@dataclass(frozen=True)
class ActiveOfficialPackage:
    package_id: str
    package_version: str
    content_fingerprint: str
    manifest_sha256: str

    def public_dict(self) -> dict[str, str]:
        return {
            "package_id": self.package_id,
            "package_version": self.package_version,
            "content_fingerprint": self.content_fingerprint,
        }


@dataclass(frozen=True)
class InstalledOfficialPackage:
    package_id: str
    package_version: str
    content_fingerprint: str | None
    installed_at: str | None
    active: bool
    audit_status: str
    error_code: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema": "installed-official-package-v1",
            "package_id": self.package_id,
            "package_version": self.package_version,
            "content_fingerprint": self.content_fingerprint,
            "installed_at": self.installed_at,
            "active": self.active,
            "audit_status": self.audit_status,
            "error_code": self.error_code,
        }


def default_active_state_path(data_root: Path | str) -> Path:
    return Path(data_root).expanduser().resolve() / ACTIVE_SELECTOR_RELATIVE_PATH


def _resolve_publisher_policy(
    supplied_keys: Mapping[str, bytes | Ed25519PublicKey] | None,
    publisher_policy: TrustedPublisherPolicy | None,
) -> tuple[TrustedPublisherPolicy, Mapping[str, bytes]]:
    policy = publisher_policy or trusted_publisher_policy(
        channel=DEFAULT_OFFICIAL_CHANNEL
    )
    if supplied_keys is not None:
        try:
            policy.assert_public_keys_match(supplied_keys)
        except TrustedPublisherPolicyError as exc:
            raise EvidencePackageError(exc.code, str(exc)) from exc
    return policy, policy.public_keys()


def _read_trust_json(path: Path, *, label: str, maximum: int) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
            raise EvidencePackageError(
                "invalid_install", f"已安装资料包{label}缺失或不安全"
            )
        value = json.loads(path.read_text(encoding="utf-8"))
    except EvidencePackageError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidencePackageError(
            "invalid_install", f"已安装资料包{label}损坏"
        ) from exc
    if not isinstance(value, dict):
        raise EvidencePackageError("invalid_install", f"已安装资料包{label}格式无效")
    return value


def _assert_official_package_trust(
    install_root: Path,
    manifest: Mapping[str, Any],
    policy: TrustedPublisherPolicy,
) -> None:
    """Bind the verified installed signer to identity, channel and rights."""

    install_marker = _read_trust_json(
        install_root / "install.json", label="安装记录", maximum=64 * 1024
    )
    rights = _read_trust_json(
        install_root / RIGHTS_PATH, label="权利清单", maximum=8 * 1024 * 1024
    )
    publisher = manifest.get("publisher")
    publisher_name = (
        str(publisher.get("name") or "") if isinstance(publisher, Mapping) else ""
    )
    try:
        policy.assert_package_identity(
            key_id=str(install_marker.get("signer_key_id") or ""),
            package_id=str(manifest.get("package_id") or ""),
            publisher_name=publisher_name,
            rights_redistribution=str(rights.get("redistribution") or ""),
        )
    except TrustedPublisherPolicyError as exc:
        raise EvidencePackageError(exc.code, str(exc)) from exc


def _official_repository_validator(
    policy: TrustedPublisherPolicy,
):
    def validate(
        install_root: Path, manifest: Mapping[str, Any]
    ) -> OfficialEvidenceRepository:
        _assert_official_package_trust(install_root, manifest, policy)
        return _validate_official_repository(install_root, manifest)

    return validate


def _validate_official_repository(
    install_root: Path,
    manifest: Mapping[str, Any],
) -> OfficialEvidenceRepository:
    required = {
        "database_contract": DATABASE_CONTRACT,
        "identity_version": IDENTITY_VERSION,
        "evidence_schema": EXPECTED_DISTRIBUTION_SCHEMA,
        "database_path": DATABASE_PATH,
        "rights_path": RIGHTS_PATH,
        "provenance_path": PROVENANCE_PATH,
    }
    for key, expected in required.items():
        if manifest.get(key) != expected:
            raise EvidencePackageError(
                "incompatible_schema", "资料包清单与官方只读仓库契约不一致"
            )
    expected_fingerprint = str(manifest.get("content_fingerprint") or "")
    if len(expected_fingerprint) != 64 or any(
        character not in "0123456789abcdef" for character in expected_fingerprint
    ):
        raise EvidencePackageError("invalid_manifest", "资料包缺少有效公开内容指纹")
    counts = manifest.get("content_counts")
    if not isinstance(counts, Mapping) or set(counts) != {
        "papers",
        "entities",
        "items",
        "findings",
        "tables",
        "figures",
    }:
        raise EvidencePackageError("invalid_manifest", "资料包缺少规范内容数量摘要")
    try:
        normalized_counts = {str(key): int(value) for key, value in counts.items()}
    except (TypeError, ValueError) as exc:
        raise EvidencePackageError("invalid_manifest", "资料包内容数量摘要无效") from exc
    if any(value < 0 for value in normalized_counts.values()) or normalized_counts["entities"] != sum(
        normalized_counts[key] for key in ("items", "findings", "tables", "figures")
    ):
        raise EvidencePackageError("invalid_manifest", "资料包内容数量摘要不一致")

    try:
        repository = OfficialEvidenceRepository.open_installed_package(
            install_root,
            expected_package_id=str(manifest["package_id"]),
            expected_version=str(manifest["package_version"]),
        )
    except Exception as exc:
        if isinstance(exc, EvidencePackageError):
            raise
        raise EvidencePackageError(
            "repository_audit_failed", "官方只读仓库审计失败"
        ) from exc
    if repository.content_fingerprint != expected_fingerprint:
        raise EvidencePackageError("repository_audit_failed", "资料包公开内容指纹不一致")
    papers = repository.list_papers()
    type_counts = {"item": 0, "finding": 0, "table": 0, "figure": 0}
    for document in repository.iter_search_documents():
        type_counts[str(document["entity_type"])] += 1
    if normalized_counts != {
        "papers": len(papers),
        "entities": sum(type_counts.values()),
        "items": type_counts["item"],
        "findings": type_counts["finding"],
        "tables": type_counts["table"],
        "figures": type_counts["figure"],
    }:
        raise EvidencePackageError("repository_audit_failed", "资料包内容数量与仓库不一致")
    return repository


def import_official_evidence_package(
    package_path: Path | str,
    *,
    data_root: Path | str,
    current_app_version: str,
    trusted_public_keys: Mapping[str, bytes | Ed25519PublicKey] | None = None,
    publisher_policy: TrustedPublisherPolicy | None = None,
    active_state_path: Path | str | None = None,
) -> ImportedEvidencePackage:
    policy, policy_keys = _resolve_publisher_policy(
        trusted_public_keys, publisher_policy
    )
    return import_evidence_package(
        package_path,
        data_root=data_root,
        trusted_public_keys=policy_keys,
        current_app_version=current_app_version,
        expected_evidence_schema=EXPECTED_DISTRIBUTION_SCHEMA,
        repository_validator=_official_repository_validator(policy),
        active_state_path=active_state_path,
    )


def rollback_official_evidence_package(
    *,
    data_root: Path | str,
    package_id: str,
    target_version: str,
    current_app_version: str,
    trusted_public_keys: Mapping[str, bytes | Ed25519PublicKey] | None = None,
    publisher_policy: TrustedPublisherPolicy | None = None,
    active_state_path: Path | str | None = None,
) -> ImportedEvidencePackage:
    policy, policy_keys = _resolve_publisher_policy(
        trusted_public_keys, publisher_policy
    )
    return rollback_evidence_package(
        data_root=data_root,
        package_id=package_id,
        target_version=target_version,
        trusted_public_keys=policy_keys,
        current_app_version=current_app_version,
        expected_evidence_schema=EXPECTED_DISTRIBUTION_SCHEMA,
        repository_validator=_official_repository_validator(policy),
        active_state_path=active_state_path,
    )


def list_installed_official_packages(
    *,
    data_root: Path | str,
    current_app_version: str,
    trusted_public_keys: Mapping[str, bytes | Ed25519PublicKey] | None = None,
    publisher_policy: TrustedPublisherPolicy | None = None,
    active_state_path: Path | str | None = None,
) -> tuple[InstalledOfficialPackage, ...]:
    """Return path-free installed versions after revalidating every candidate.

    A damaged version remains visible as an unavailable rollback target, while
    healthy siblings can still be used.  Unsafe directory topology fails the
    whole listing instead of following attacker-controlled links.
    """

    policy, policy_keys = _resolve_publisher_policy(
        trusted_public_keys, publisher_policy
    )
    root = Path(data_root).expanduser().resolve()
    official_root = root / "official-packages"
    selector_path = (
        Path(active_state_path).expanduser().resolve()
        if active_state_path is not None
        else default_active_state_path(root)
    )
    active = _read_active_state(selector_path)
    if not official_root.exists():
        return ()
    if official_root.is_symlink() or not official_root.is_dir():
        raise EvidencePackageError("unsafe_install_root", "本机官方资料包目录不安全")

    results: list[InstalledOfficialPackage] = []
    for package_root in sorted(official_root.iterdir(), key=lambda path: path.name):
        if package_root.name == "active.json":
            continue
        if (
            package_root.is_symlink()
            or not package_root.is_dir()
            or not PACKAGE_ID_RE.fullmatch(package_root.name)
        ):
            raise EvidencePackageError("unsafe_install_root", "本机官方资料包目录不安全")
        for version_root in sorted(package_root.iterdir(), key=lambda path: path.name):
            if (
                version_root.is_symlink()
                or not version_root.is_dir()
                or not PACKAGE_VERSION_RE.fullmatch(version_root.name)
            ):
                raise EvidencePackageError("unsafe_install_root", "本机官方资料包目录不安全")
            package_id = package_root.name
            package_version = version_root.name
            is_active = bool(
                active
                and str(active.get("package_id") or "") == package_id
                and str(active.get("package_version") or "") == package_version
            )
            installed_at: str | None = None
            fingerprint: str | None = None
            try:
                marker = _read_trust_json(
                    version_root / "install.json", label="安装记录", maximum=64 * 1024
                )
                installed_at_value = str(marker.get("installed_at") or "")
                installed_at = (
                    installed_at_value if len(installed_at_value) <= 80 else None
                )
                manifest = _validate_installed_tree(
                    version_root,
                    trusted_public_keys=policy_keys,
                    current_app_version=current_app_version,
                    expected_evidence_schema=EXPECTED_DISTRIBUTION_SCHEMA,
                    expected_package_id=package_id,
                    expected_package_version=package_version,
                    expected_manifest_sha256=str(marker.get("manifest_sha256") or ""),
                )
                repository = _official_repository_validator(policy)(
                    version_root, manifest
                )
                fingerprint = repository.content_fingerprint
                results.append(
                    InstalledOfficialPackage(
                        package_id=package_id,
                        package_version=package_version,
                        content_fingerprint=fingerprint,
                        installed_at=installed_at,
                        active=is_active,
                        audit_status="ready",
                    )
                )
            except EvidencePackageError as exc:
                results.append(
                    InstalledOfficialPackage(
                        package_id=package_id,
                        package_version=package_version,
                        content_fingerprint=fingerprint,
                        installed_at=installed_at,
                        active=is_active,
                        audit_status="invalid",
                        error_code=exc.code,
                    )
                )
    return tuple(
        sorted(
            results,
            key=lambda entry: (not entry.active, entry.package_id, entry.package_version),
        )
    )


def open_active_official_repository(
    *,
    data_root: Path | str,
    current_app_version: str,
    trusted_public_keys: Mapping[str, bytes | Ed25519PublicKey] | None = None,
    publisher_policy: TrustedPublisherPolicy | None = None,
    active_state_path: Path | str | None = None,
) -> tuple[ActiveOfficialPackage, OfficialEvidenceRepository]:
    policy, policy_keys = _resolve_publisher_policy(
        trusted_public_keys, publisher_policy
    )
    root = Path(data_root).expanduser().resolve()
    selector_path = (
        Path(active_state_path).expanduser().resolve()
        if active_state_path is not None
        else default_active_state_path(root)
    )
    state = _read_active_state(selector_path)
    if state is None:
        raise EvidencePackageError("active_package_missing", "尚未导入可用的官方资料包")
    package_id = str(state["package_id"])
    package_version = str(state["package_version"])
    install_root = root / "official-packages" / package_id / package_version
    manifest = _validate_installed_tree(
        install_root,
        trusted_public_keys=policy_keys,
        current_app_version=current_app_version,
        expected_evidence_schema=EXPECTED_DISTRIBUTION_SCHEMA,
        expected_package_id=package_id,
        expected_package_version=package_version,
        expected_manifest_sha256=str(state["manifest_sha256"]),
    )
    repository = _official_repository_validator(policy)(install_root, manifest)
    if repository.content_fingerprint != str(state["content_fingerprint"]):
        raise EvidencePackageError("invalid_active_state", "活动资料包内容指纹不一致")
    return (
        ActiveOfficialPackage(
            package_id=package_id,
            package_version=package_version,
            content_fingerprint=repository.content_fingerprint,
            manifest_sha256=str(state["manifest_sha256"]),
        ),
        repository,
    )
