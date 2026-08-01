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
    "PortableRepositoryError",
    "TrustedPublisherPolicy",
    "TrustedPublisherPolicyError",
    "default_active_state_path",
    "import_official_evidence_package",
    "open_active_official_repository",
    "rollback_official_evidence_package",
    "trusted_publisher_policy",
    "trusted_public_keys",
]
