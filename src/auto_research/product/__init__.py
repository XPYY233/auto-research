"""Cross-platform product distribution primitives for Auto Research."""

from .evidence_package import (
    ARESEARCH_FORMAT,
    ARESEARCH_FORMAT_VERSION,
    EvidencePackageError,
    ImportedEvidencePackage,
    VerifiedEvidencePackage,
    build_evidence_package,
    import_evidence_package,
    rollback_evidence_package,
    verify_evidence_package,
)
from .evidence_v12_export import plan_evidence_v12_export
from .portable_repository import (
    DATABASE_CONTRACT,
    DISTRIBUTION_SCHEMA_VERSION,
    OfficialEvidenceRepository,
    PortableExportPlan,
    PortableRepositoryError,
    PortableRepositoryExport,
    ReleasePolicy,
    audit_portable_repository,
    materialize_portable_repository,
    provenance_for_papers,
)

__all__ = [
    "ARESEARCH_FORMAT",
    "ARESEARCH_FORMAT_VERSION",
    "EvidencePackageError",
    "ImportedEvidencePackage",
    "VerifiedEvidencePackage",
    "build_evidence_package",
    "import_evidence_package",
    "rollback_evidence_package",
    "verify_evidence_package",
    "DATABASE_CONTRACT",
    "DISTRIBUTION_SCHEMA_VERSION",
    "OfficialEvidenceRepository",
    "PortableExportPlan",
    "PortableRepositoryError",
    "PortableRepositoryExport",
    "ReleasePolicy",
    "audit_portable_repository",
    "materialize_portable_repository",
    "plan_evidence_v12_export",
    "provenance_for_papers",
]
