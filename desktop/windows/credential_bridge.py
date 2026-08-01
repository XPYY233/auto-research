from __future__ import annotations

from credential_manager import CredentialSecretStore, WindowsCredentialError


class CredentialBridgeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DeepSeekCredentialBridgeAdapter:
    """Expose credential lifecycle without ever returning the secret to UI DTOs."""

    def __init__(self, store: CredentialSecretStore) -> None:
        self.store = store

    def status(self) -> dict[str, object]:
        try:
            configured = self.store.load() is not None
        except WindowsCredentialError:
            raise CredentialBridgeError(
                "credential_store_unavailable", "Windows 安全凭据存储不可用。"
            ) from None
        return {
            "provider": "deepseek",
            "configured": configured,
            "storage": "windows-credential-manager",
        }

    def save(self, api_key: str) -> dict[str, object]:
        try:
            self.store.save(api_key)
        except (TypeError, ValueError, WindowsCredentialError):
            raise CredentialBridgeError(
                "credential_save_failed", "API key 未能安全保存。"
            ) from None
        return self.status()

    def clear(self) -> dict[str, object]:
        try:
            self.store.clear()
        except WindowsCredentialError:
            raise CredentialBridgeError(
                "credential_clear_failed", "API key 未能安全删除。"
            ) from None
        return self.status()

    def resolve_for_runtime(self) -> str | None:
        """Internal extraction hook; shared UI routes must never serialize it."""

        try:
            return self.store.load()
        except WindowsCredentialError:
            raise CredentialBridgeError(
                "credential_store_unavailable", "Windows 安全凭据存储不可用。"
            ) from None
