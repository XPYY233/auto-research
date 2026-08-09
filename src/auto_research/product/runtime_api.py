"""Narrow, read-side product API shared by desktop applications.

Only package verification/activation, trusted publisher policy, and immutable
repository reads belong here.  Publisher-side export and package construction
must stay outside desktop runtime dependency manifests.
"""

from __future__ import annotations

from .evidence_package import EvidencePackageError
from .official_package_store import (
    ACTIVE_SELECTOR_RELATIVE_PATH,
    EXPECTED_DISTRIBUTION_SCHEMA,
    ActiveOfficialPackage,
    default_active_state_path,
    import_official_evidence_package,
    open_active_official_repository,
    rollback_official_evidence_package,
)
from .portable_repository import OfficialEvidenceRepository, PortableRepositoryError
from .package_job_contract import (
    PackageJobContractError,
    PackageJobError,
    PackageJobProgress,
    PackageJobStage,
    PackageOperation,
    advance_package_job,
    begin_package_job,
    fail_package_job,
    package_job_stages,
)
from .package_center import (
    PackageCenter,
    PackageExportService,
    PackageJobService,
    PackageTransferImportService,
)
from .package_center_models import (
    PackageCenterError,
    PackageKind,
    PackageScope,
    PayloadPlanCandidate,
    RightsConfirmation,
    RightsRequirement,
)
from .transfer_package import (
    ImportedTransferPackage,
    TransferPackageError,
    TransferPackageKind,
    VerifiedTransferPackage,
    import_transfer_package,
    verify_transfer_package,
)
from .trusted_publishers import (
    TrustedPublisherPolicy,
    TrustedPublisherPolicyError,
    trusted_publisher_policy,
    trusted_public_keys,
)

__all__ = [
    "ACTIVE_SELECTOR_RELATIVE_PATH",
    "EXPECTED_DISTRIBUTION_SCHEMA",
    "ActiveOfficialPackage",
    "EvidencePackageError",
    "OfficialEvidenceRepository",
    "PackageJobContractError",
    "PackageJobError",
    "PackageJobProgress",
    "PackageJobStage",
    "PackageOperation",
    "PackageCenter",
    "PackageCenterError",
    "PackageExportService",
    "PackageJobService",
    "PackageKind",
    "PackageScope",
    "PackageTransferImportService",
    "PayloadPlanCandidate",
    "RightsConfirmation",
    "RightsRequirement",
    "PortableRepositoryError",
    "ImportedTransferPackage",
    "TransferPackageError",
    "TransferPackageKind",
    "TrustedPublisherPolicy",
    "TrustedPublisherPolicyError",
    "default_active_state_path",
    "advance_package_job",
    "begin_package_job",
    "fail_package_job",
    "import_official_evidence_package",
    "import_transfer_package",
    "open_active_official_repository",
    "rollback_official_evidence_package",
    "trusted_publisher_policy",
    "trusted_public_keys",
    "package_job_stages",
    "verify_transfer_package",
    "VerifiedTransferPackage",
]
