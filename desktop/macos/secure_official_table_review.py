"""Encrypted macOS store for official table-structure review state."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Mapping

from auto_research.evidence.official_table_structure_review import (
    MAX_STORE_BYTES,
    OfficialTableStructureReviewError,
)
from secure_atomic_json_store import AtomicAESGCMJSONStore, SecureJSONPolicy
from secure_history import HistoryKeyProvider, LocalFileHistoryKeyProvider


from crypto_identity import encryption_identity

APP_IDENTIFIER = encryption_identity()
OFFICIAL_TABLE_REVIEW_AAD = (
    f"{APP_IDENTIFIER}:official-table-structure-review:v1".encode("utf-8")
)
OFFICIAL_TABLE_REVIEW_KEYCHAIN_SERVICE = (
    f"{APP_IDENTIFIER}.official-table-structure-review"
)
OFFICIAL_TABLE_REVIEW_KEYCHAIN_ACCOUNT = "official-table-structure-review-v1"
MAX_PLAINTEXT_BYTES = MAX_STORE_BYTES
MAX_ENVELOPE_BYTES = 4 * ((MAX_PLAINTEXT_BYTES + 18) // 3) + 1_024
OFFICIAL_TABLE_REVIEW_POLICY = SecureJSONPolicy(
    aad=OFFICIAL_TABLE_REVIEW_AAD,
    max_plaintext_bytes=MAX_PLAINTEXT_BYTES,
    max_envelope_bytes=MAX_ENVELOPE_BYTES,
    max_depth=32,
    max_nodes=2_100_000,
)
_PROCESS_CAS_LOCK = threading.RLock()


def _store_error(_reason: str) -> OfficialTableStructureReviewError:
    return OfficialTableStructureReviewError("official_table_review_unavailable")


class SecureOfficialTableReviewStore:
    """Atomic encrypted store implementing the shared review-store protocol."""

    def __init__(
        self,
        path: Path,
        key_provider: HistoryKeyProvider,
        *,
        storage_label: str = "macos-official-table-review-aes-256-gcm",
    ) -> None:
        self.path = path.expanduser()
        self.key_provider = key_provider
        self.storage_label = storage_label
        self._store = AtomicAESGCMJSONStore(
            self.path,
            key_provider,
            policy=OFFICIAL_TABLE_REVIEW_POLICY,
            error_factory=_store_error,
            storage_label=storage_label,
        )
        # All adapters share one lock domain so two service instances cannot
        # both win a read-revision/write race for the same encrypted file.
        self._lock = _PROCESS_CAS_LOCK

    def load(self) -> object:
        with self._lock:
            return self._store.load()

    def compare_and_swap(
        self,
        expected_revision: int,
        value: Mapping[str, Any],
    ) -> None:
        if type(expected_revision) is not int or expected_revision < 0:
            raise OfficialTableStructureReviewError("official_table_review_invalid")
        if not isinstance(value, Mapping):
            raise OfficialTableStructureReviewError("official_table_review_invalid")
        with self._lock:
            current = self._store.load()
            if current is None:
                revision = 0
            elif isinstance(current, Mapping) and type(current.get("revision")) is int:
                revision = int(current["revision"])
            else:
                raise OfficialTableStructureReviewError("official_table_review_corrupt")
            if revision != expected_revision:
                raise OfficialTableStructureReviewError(
                    "official_table_review_version_conflict"
                )
            self._store.save(dict(value))


def default_secure_official_table_review_store(
    *,
    data_root: Path | None = None,
    key_provider: HistoryKeyProvider | None = None,
) -> SecureOfficialTableReviewStore:
    """Create the preview/local-key adapter; stable signing may inject Keychain."""

    application_root = (
        Path(data_root).expanduser()
        if data_root is not None
        else Path.home()
        / "Library"
        / "Application Support"
        / "Auto Research"
    )
    private_directory = application_root / "Private Data"
    provider = key_provider or LocalFileHistoryKeyProvider(
        private_directory / "official-table-structure-review-v1.key"
    )
    return SecureOfficialTableReviewStore(
        private_directory / "official-table-structure-review-v1.enc",
        provider,
        storage_label=(
            "macos-official-table-review-keychain-aes-256-gcm"
            if key_provider is not None
            else "macos-preview-official-table-review-local-key-aes-256-gcm"
        ),
    )


__all__ = [
    "OFFICIAL_TABLE_REVIEW_AAD",
    "OFFICIAL_TABLE_REVIEW_KEYCHAIN_ACCOUNT",
    "OFFICIAL_TABLE_REVIEW_KEYCHAIN_SERVICE",
    "SecureOfficialTableReviewStore",
    "default_secure_official_table_review_store",
]
