"""macOS encrypted Store adapter for shared package operation history."""

from __future__ import annotations

from pathlib import Path

from auto_research.product.operation_history_contract import (
    MAX_STORE_BYTES,
    OperationHistoryError,
    unavailable_history,
)
from secure_atomic_json_store import AtomicAESGCMJSONStore, SecureJSONPolicy
from secure_history import HistoryKeyProvider, LocalFileHistoryKeyProvider


from crypto_identity import encryption_identity

APP_IDENTIFIER = encryption_identity()
OPERATION_HISTORY_AAD = f"{APP_IDENTIFIER}:operation-history:v1".encode("utf-8")
MAX_PLAINTEXT_BYTES = MAX_STORE_BYTES
MAX_ENVELOPE_BYTES = 4 * ((MAX_PLAINTEXT_BYTES + 18) // 3) + 1_024
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 2_100_000
OPERATION_HISTORY_POLICY = SecureJSONPolicy(
    aad=OPERATION_HISTORY_AAD,
    max_plaintext_bytes=MAX_PLAINTEXT_BYTES,
    max_envelope_bytes=MAX_ENVELOPE_BYTES,
    max_depth=MAX_JSON_DEPTH,
    max_nodes=MAX_JSON_NODES,
)


def _operation_history_error(_reason: str) -> OperationHistoryError:
    return unavailable_history("本机资料包任务历史暂时不可用。")


class SecureOperationHistoryStore:
    """Thin OperationHistoryStore over the shared authenticated JSON core."""

    def __init__(
        self,
        path: Path,
        key_provider: HistoryKeyProvider,
        *,
        storage_label: str = "macos-operation-history-aes-256-gcm",
    ) -> None:
        self.path = path.expanduser()
        self.key_provider = key_provider
        self.storage_label = storage_label
        self._store = AtomicAESGCMJSONStore(
            self.path,
            key_provider,
            policy=OPERATION_HISTORY_POLICY,
            error_factory=_operation_history_error,
            storage_label=storage_label,
        )

    def load(self) -> object:
        return self._store.load()

    def save(self, value: object) -> None:
        self._store.save(value)

    def clear(self) -> None:
        self._store.clear()


def default_secure_operation_history_store() -> SecureOperationHistoryStore:
    private_directory = (
        Path.home()
        / "Library"
        / "Application Support"
        / "Auto Research"
        / "Private Data"
    )
    return SecureOperationHistoryStore(
        private_directory / "operation-history-v1.enc",
        LocalFileHistoryKeyProvider(private_directory / "operation-history-v1.key"),
        storage_label="macos-preview-operation-history-local-key-aes-256-gcm",
    )


__all__ = [
    "OPERATION_HISTORY_AAD",
    "SecureOperationHistoryStore",
    "default_secure_operation_history_store",
]
