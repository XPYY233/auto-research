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
]
