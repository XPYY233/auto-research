"""Narrow, read-side product API shared by desktop applications.

Only package verification/activation, immutable repository reads, and the
untrusted user-transfer package center belong here.  Official publisher-side
signing/builders must stay outside desktop runtime dependency manifests.
"""

from __future__ import annotations

from .evidence_package import EvidencePackageError
from .official_package_store import (
    ACTIVE_SELECTOR_RELATIVE_PATH,
    EXPECTED_DISTRIBUTION_SCHEMA,
    ActiveOfficialPackage,
    InstalledOfficialPackage,
    default_active_state_path,
    import_official_evidence_package,
    list_installed_official_packages,
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
    run_package_job_in_background,
    run_package_job_inline,
)
from .package_center_models import (
    MaterializedPayload,
    PackageCenterError,
    PackageKind,
    PackageScope,
    PayloadPlanCandidate,
    RightsConfirmation,
    RightsRequirement,
)
from .package_center_payload_adapter import StructuredPackageCenterPayloadAdapter
from .package_payload_sources import (
    EvidenceV12LiteraturePayloadSource,
    ExplicitLiteratureFilterResolver,
    LiteratureFilterResolution,
    LiteratureLicenseVerification,
    PrivateRepositoryPersonalPayloadSource,
)
from .package_transfer_activation import (
    ActivatedTransferPackage,
    PackageTransferActivationError,
    PackageTransferActivationService,
)
from .package_transfer_payloads import (
    LiteratureCollectionPayloadPlanner,
    PersonalExperimentsPayloadPlanner,
    audit_transfer_payload_tree,
    open_transferred_literature_repository,
    read_personal_transfer_snapshot,
)
from .transfer_package import (
    ImportedTransferPackage,
    TransferPackageError,
    TransferPackageKind,
    VerifiedTransferPackage,
    export_transfer_package,
    export_transfer_package_with_checksum,
    import_transfer_package,
    list_installed_transfer_packages,
    open_installed_transfer_package,
    verify_transfer_package,
)
from .trusted_publishers import (
    TrustedPublisherPolicy,
    TrustedPublisherPolicyError,
    trusted_publisher_policy,
    trusted_public_keys,
)
from .dataset_bundle import (
    DatasetBundleBuilder,
    DatasetBundleError,
    DatasetBundlePlan,
)
from .dataset_export_service import (
    DatasetExportCandidate,
    DatasetExportService,
)

__all__ = [
    "ACTIVE_SELECTOR_RELATIVE_PATH",
    "EXPECTED_DISTRIBUTION_SCHEMA",
    "ActiveOfficialPackage",
    "InstalledOfficialPackage",
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
    "run_package_job_in_background",
    "run_package_job_inline",
    "StructuredPackageCenterPayloadAdapter",
    "EvidenceV12LiteraturePayloadSource",
    "ExplicitLiteratureFilterResolver",
    "LiteratureFilterResolution",
    "LiteratureLicenseVerification",
    "PrivateRepositoryPersonalPayloadSource",
    "ActivatedTransferPackage",
    "PackageTransferActivationError",
    "PackageTransferActivationService",
    "LiteratureCollectionPayloadPlanner",
    "PersonalExperimentsPayloadPlanner",
    "MaterializedPayload",
    "PayloadPlanCandidate",
    "RightsConfirmation",
    "RightsRequirement",
    "PortableRepositoryError",
    "ImportedTransferPackage",
    "TransferPackageError",
    "TransferPackageKind",
    "TrustedPublisherPolicy",
    "TrustedPublisherPolicyError",
    "DatasetBundleBuilder",
    "DatasetBundleError",
    "DatasetBundlePlan",
    "DatasetExportCandidate",
    "DatasetExportService",
    "default_active_state_path",
    "advance_package_job",
    "begin_package_job",
    "fail_package_job",
    "import_official_evidence_package",
    "list_installed_official_packages",
    "import_transfer_package",
    "export_transfer_package",
    "export_transfer_package_with_checksum",
    "list_installed_transfer_packages",
    "open_installed_transfer_package",
    "audit_transfer_payload_tree",
    "open_transferred_literature_repository",
    "read_personal_transfer_snapshot",
    "open_active_official_repository",
    "rollback_official_evidence_package",
    "trusted_publisher_policy",
    "trusted_public_keys",
    "package_job_stages",
    "verify_transfer_package",
    "VerifiedTransferPackage",
]
