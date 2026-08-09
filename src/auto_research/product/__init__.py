"""Cross-platform product distribution primitives for Auto Research.

The package root keeps the historical public API, but resolves exports lazily.
Desktop applications should import :mod:`auto_research.product.runtime_api` so
publisher-only exporters and builders do not become runtime dependencies.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORT_MODULES = {
    "ARESEARCH_FORMAT": ".evidence_package",
    "ARESEARCH_FORMAT_VERSION": ".evidence_package",
    "EvidencePackageError": ".evidence_package",
    "ImportedEvidencePackage": ".evidence_package",
    "VerifiedEvidencePackage": ".evidence_package",
    "build_evidence_package": ".evidence_package",
    "import_evidence_package": ".evidence_package",
    "rollback_evidence_package": ".evidence_package",
    "verify_evidence_package": ".evidence_package",
    "PackageJobContractError": ".package_job_contract",
    "PackageJobError": ".package_job_contract",
    "PackageJobProgress": ".package_job_contract",
    "PackageJobStage": ".package_job_contract",
    "PackageOperation": ".package_job_contract",
    "advance_package_job": ".package_job_contract",
    "begin_package_job": ".package_job_contract",
    "fail_package_job": ".package_job_contract",
    "package_job_stages": ".package_job_contract",
    "PackageCenter": ".package_center",
    "PackageExportService": ".package_center",
    "PackageJobService": ".package_center",
    "PackageTransferImportService": ".package_center",
    "PackageCenterError": ".package_center_models",
    "PackageKind": ".package_center_models",
    "PackageScope": ".package_center_models",
    "PayloadPlanCandidate": ".package_center_models",
    "RightsConfirmation": ".package_center_models",
    "RightsRequirement": ".package_center_models",
    "plan_evidence_v12_export": ".evidence_v12_export",
    "ACTIVE_SELECTOR_RELATIVE_PATH": ".official_package_store",
    "EXPECTED_DISTRIBUTION_SCHEMA": ".official_package_store",
    "ActiveOfficialPackage": ".official_package_store",
    "default_active_state_path": ".official_package_store",
    "import_official_evidence_package": ".official_package_store",
    "open_active_official_repository": ".official_package_store",
    "rollback_official_evidence_package": ".official_package_store",
    "DATABASE_CONTRACT": ".portable_repository",
    "DISTRIBUTION_SCHEMA_VERSION": ".portable_repository",
    "OfficialEvidenceRepository": ".portable_repository",
    "PortableExportPlan": ".portable_repository",
    "PortableRepositoryError": ".portable_repository",
    "PortableRepositoryExport": ".portable_repository",
    "ReleasePolicy": ".portable_repository",
    "audit_portable_repository": ".portable_repository",
    "materialize_portable_repository": ".portable_repository",
    "provenance_for_papers": ".portable_repository",
    "TRUSTED_PUBLISHER_REGISTRY_VERSION": ".trusted_publishers",
    "TrustedPublisher": ".trusted_publishers",
    "TrustedPublisherPolicy": ".trusted_publishers",
    "TrustedPublisherPolicyError": ".trusted_publishers",
    "assert_trusted_package_identity": ".trusted_publishers",
    "trusted_publisher_policy": ".trusted_publishers",
    "trusted_public_keys": ".trusted_publishers",
    "trusted_publishers": ".trusted_publishers",
    "TRANSFER_FORMAT": ".transfer_package",
    "TRANSFER_FORMAT_VERSION": ".transfer_package",
    "ExportedTransferPackage": ".transfer_package",
    "ImportedTransferPackage": ".transfer_package",
    "TransferFileRights": ".transfer_package",
    "TransferFileSpec": ".transfer_package",
    "TransferPackageError": ".transfer_package",
    "TransferPackageKind": ".transfer_package",
    "TransferPackagePlan": ".transfer_package",
    "VerifiedTransferPackage": ".transfer_package",
    "export_transfer_package": ".transfer_package",
    "import_transfer_package": ".transfer_package",
    "plan_transfer_package": ".transfer_package",
    "verify_transfer_package": ".transfer_package",
}

__all__ = list(_EXPORT_MODULES)


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
