from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


APP_IDENTIFIER = "com.researcher.autoresearch"
# The first private alpha used an ad-hoc signature whose identity changed after
# each rebuild.  macOS therefore asks the user to authorize the old item when a
# newer build tries to read it. The preview deliberately never probes or deletes
# that legacy item; formal signed distribution can reuse this provider class.
KEYCHAIN_SERVICE = f"{APP_IDENTIFIER}.librarian-history.v2"
KEYCHAIN_ACCOUNT = "local-encryption-key-v2"
HISTORY_AAD = f"{APP_IDENTIFIER}:librarian-history:v2".encode("utf-8")
MAX_HISTORY_BYTES = 2_500_000
MAX_SESSIONS = 16


class SecureHistoryError(RuntimeError):
    """Raised when private desktop history cannot be safely read or written."""


class HistoryKeyProvider(Protocol):
    def get_or_create_key(self) -> bytes: ...


def _security_status(value) -> int:
    return int(value[0] if isinstance(value, tuple) else value)


class MacKeychainKeyProvider:
    """Keep only the AES key in the device-local macOS Keychain."""

    def __init__(
        self,
        *,
        service: str = KEYCHAIN_SERVICE,
        account: str = KEYCHAIN_ACCOUNT,
    ) -> None:
        self.service = service
        self.account = account

    def _query(self) -> dict:
        try:
            import Security
        except ImportError as exc:  # pragma: no cover - only possible outside the macOS build.
            raise SecureHistoryError("macOS Keychain 组件不可用") from exc
        return {
            Security.kSecClass: Security.kSecClassGenericPassword,
            Security.kSecAttrService: self.service,
            Security.kSecAttrAccount: self.account,
        }

    def _read_key(self) -> tuple[int, bytes | None]:
        import Security

        query = self._query()
        query[Security.kSecReturnData] = True
        query[Security.kSecMatchLimit] = Security.kSecMatchLimitOne
        status, value = Security.SecItemCopyMatching(query, None)
        return int(status), bytes(value) if value is not None else None

    def get_or_create_key(self) -> bytes:
        import Security

        status, key = self._read_key()
        if status == Security.errSecSuccess:
            if key is None or len(key) != 32:
                raise SecureHistoryError("Keychain 中的本地历史密钥格式无效")
            return key
        if status != Security.errSecItemNotFound:
            raise SecureHistoryError(f"无法读取 macOS Keychain（状态 {status}）")

        key = os.urandom(32)
        query = self._query()
        query[Security.kSecValueData] = key
        query[Security.kSecAttrAccessible] = Security.kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        add_status = _security_status(Security.SecItemAdd(query, None))
        if add_status == Security.errSecSuccess:
            return key
        if add_status == Security.errSecDuplicateItem:
            retry_status, retry_key = self._read_key()
            if retry_status == Security.errSecSuccess and retry_key is not None and len(retry_key) == 32:
                return retry_key
        raise SecureHistoryError(f"无法创建 macOS Keychain 密钥（状态 {add_status}）")

    def delete_key(self) -> None:
        import Security

        status = _security_status(Security.SecItemDelete(self._query()))
        if status not in {Security.errSecSuccess, Security.errSecItemNotFound}:
            raise SecureHistoryError(f"无法删除 macOS Keychain 密钥（状态 {status}）")


class LocalFileHistoryKeyProvider:
    """Development-preview key storage that never opens a Keychain prompt.

    Ad-hoc macOS signatures change identity on every build, so a Keychain item
    created by one preview build can require the login password after the next
    build.  Until the app has a stable Developer ID signature, keep a random
    key in the user's private Application Support directory instead.  This is
    protected by per-user permissions and FileVault when enabled; production
    distribution must switch back to the OS credential store.
    """

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser()

    def get_or_create_key(self) -> bytes:
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        if self.path.is_symlink():
            raise SecureHistoryError("本机历史密钥路径不安全")
        try:
            key = self.path.read_bytes()
        except FileNotFoundError:
            key = os.urandom(32)
            try:
                descriptor = os.open(
                    self.path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                key = self.path.read_bytes()
            else:
                try:
                    os.write(descriptor, key)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        except OSError as exc:
            raise SecureHistoryError("无法读取本机历史密钥") from exc
        if len(key) != 32:
            raise SecureHistoryError("本机历史密钥格式无效")
        try:
            os.chmod(self.path, 0o600)
        except OSError as exc:
            raise SecureHistoryError("无法保护本机历史密钥") from exc
        return key

    def delete_key(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise SecureHistoryError("无法清除本机历史密钥") from exc


class StaticHistoryKeyProvider:
    """Test-only key provider that never touches the user's Keychain."""

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("AES-256 key must contain exactly 32 bytes")
        self.key = key

    def get_or_create_key(self) -> bytes:
        return self.key


class SecureHistoryStore:
    def __init__(
        self,
        path: Path,
        key_provider: HistoryKeyProvider,
        *,
        storage_label: str = "local-aes-256-gcm",
    ) -> None:
        self.path = path.expanduser()
        self.key_provider = key_provider
        self.storage_label = storage_label

    @staticmethod
    def _serialize_sessions(sessions: object) -> bytes:
        if not isinstance(sessions, list):
            raise SecureHistoryError("对话历史必须是列表")
        if len(sessions) > MAX_SESSIONS:
            raise SecureHistoryError(f"对话历史最多保留 {MAX_SESSIONS} 组")
        for session in sessions:
            if not isinstance(session, dict):
                raise SecureHistoryError("对话历史包含无效会话")
            if not isinstance(session.get("id"), str) or not session["id"].strip():
                raise SecureHistoryError("对话会话缺少有效 ID")
            if not isinstance(session.get("messages"), list):
                raise SecureHistoryError("对话会话缺少消息列表")
        try:
            payload = json.dumps(
                sessions,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise SecureHistoryError("对话历史不能安全序列化") from exc
        if len(payload) > MAX_HISTORY_BYTES:
            raise SecureHistoryError("对话历史超过本机加密存储上限")
        return payload

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
            if envelope.get("version") != 1 or envelope.get("algorithm") != "AES-256-GCM":
                raise SecureHistoryError("本机加密历史版本不受支持")
            nonce = base64.b64decode(envelope["nonce"], validate=True)
            ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
            plaintext = AESGCM(self.key_provider.get_or_create_key()).decrypt(
                nonce,
                ciphertext,
                HISTORY_AAD,
            )
            sessions = json.loads(plaintext.decode("utf-8"))
            self._serialize_sessions(sessions)
            return sessions
        except SecureHistoryError:
            raise
        except (OSError, KeyError, TypeError, ValueError, UnicodeDecodeError, InvalidTag) as exc:
            raise SecureHistoryError("本机加密历史损坏、被篡改或密钥不可用") from exc

    def save(self, sessions: object) -> None:
        plaintext = self._serialize_sessions(sessions)
        nonce = os.urandom(12)
        ciphertext = AESGCM(self.key_provider.get_or_create_key()).encrypt(
            nonce,
            plaintext,
            HISTORY_AAD,
        )
        envelope = json.dumps(
            {
                "version": 1,
                "algorithm": "AES-256-GCM",
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            },
            separators=(",", ":"),
        ).encode("utf-8")

        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".librarian-history-",
                dir=directory,
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                os.fchmod(handle.fileno(), 0o600)
                handle.write(envelope)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            os.chmod(self.path, 0o600)
        except OSError as exc:
            raise SecureHistoryError("无法写入本机加密历史") from exc
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink(missing_ok=True)

    def clear(self, *, delete_key: bool = False) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise SecureHistoryError("无法清除本机加密历史") from exc
        if delete_key:
            delete = getattr(self.key_provider, "delete_key", None)
            if callable(delete):
                delete()


def default_secure_history_store() -> SecureHistoryStore:
    private_directory = (
        Path.home()
        / "Library"
        / "Application Support"
        / "Auto Research"
        / "Private Data"
    )
    return SecureHistoryStore(
        private_directory / "librarian-history-v2.enc",
        LocalFileHistoryKeyProvider(private_directory / "librarian-history-v2.key"),
        storage_label="macos-preview-local-key-aes-256-gcm",
    )
