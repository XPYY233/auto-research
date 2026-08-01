from __future__ import annotations

import base64
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


APP_IDENTIFIER = "com.researcher.autoresearch"
DEEPSEEK_PROVIDER = "deepseek"
DEEPSEEK_KEYCHAIN_SERVICE = f"{APP_IDENTIFIER}.deepseek-api.v1"
DEEPSEEK_KEYCHAIN_ACCOUNT = "deepseek-api-key"
DEEPSEEK_AAD = f"{APP_IDENTIFIER}:deepseek-api:v1".encode("utf-8")
MAX_API_KEY_CHARS = 512
MIN_API_KEY_CHARS = 20

ERROR_INVALID = "credential_invalid"
ERROR_UNAVAILABLE = "credential_store_unavailable"
ERROR_LOCKED = "credential_store_locked"
ERROR_DENIED = "credential_store_denied"
ERROR_CORRUPTED = "credential_store_corrupted"
ERROR_WRITE = "credential_store_write_failed"
ERROR_DELETE = "credential_store_delete_failed"


class SecureCredentialError(RuntimeError):
    """A stable, non-secret-bearing error from desktop credential storage."""

    def __init__(self, code: str, message: str, *, http_status: int = 500) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


@dataclass(frozen=True)
class CredentialStatus:
    provider: str
    configured: bool
    storage: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "configured": self.configured,
            "storage": self.storage,
        }


class CredentialBackend(Protocol):
    storage_label: str

    def exists(self) -> bool: ...

    def read(self) -> str | None: ...

    def write(self, secret: str) -> None: ...

    def delete(self) -> None: ...


def validate_deepseek_api_key(value: Any) -> str:
    if not isinstance(value, str):
        raise SecureCredentialError(ERROR_INVALID, "API 密钥格式无效", http_status=400)
    secret = value.strip()
    if not (MIN_API_KEY_CHARS <= len(secret) <= MAX_API_KEY_CHARS):
        raise SecureCredentialError(ERROR_INVALID, "API 密钥长度无效", http_status=400)
    if any(character.isspace() or ord(character) < 33 or ord(character) > 126 for character in secret):
        raise SecureCredentialError(ERROR_INVALID, "API 密钥不能包含空格或控制字符", http_status=400)
    return secret


def _security_status(value: Any) -> int:
    return int(value[0] if isinstance(value, tuple) else value)


class MacKeychainCredentialBackend:
    """Formal signed-build storage using one device-local generic password item."""

    storage_label = "macos-keychain"

    def __init__(
        self,
        *,
        service: str = DEEPSEEK_KEYCHAIN_SERVICE,
        account: str = DEEPSEEK_KEYCHAIN_ACCOUNT,
        security_module: Any | None = None,
    ) -> None:
        self.service = service
        self.account = account
        self._security = security_module

    def _module(self) -> Any:
        if self._security is not None:
            return self._security
        try:
            import Security
        except ImportError as exc:  # pragma: no cover - only outside macOS/PyObjC.
            raise SecureCredentialError(ERROR_UNAVAILABLE, "macOS Keychain 组件不可用") from exc
        return Security

    def _query(self) -> dict[Any, Any]:
        security = self._module()
        return {
            security.kSecClass: security.kSecClassGenericPassword,
            security.kSecAttrService: self.service,
            security.kSecAttrAccount: self.account,
        }

    def _raise_status(self, status: int, *, operation: str) -> None:
        security = self._module()
        if status == getattr(security, "errSecInteractionNotAllowed", -25308):
            raise SecureCredentialError(ERROR_LOCKED, "macOS 钥匙串当前已锁定", http_status=503)
        if status in {
            getattr(security, "errSecAuthFailed", -25293),
            getattr(security, "errSecUserCanceled", -128),
        }:
            raise SecureCredentialError(ERROR_DENIED, "macOS 钥匙串访问未获授权", http_status=403)
        code = ERROR_DELETE if operation == "delete" else ERROR_WRITE if operation == "write" else ERROR_UNAVAILABLE
        message = {
            "delete": "无法删除 macOS 钥匙串凭据",
            "write": "无法写入 macOS 钥匙串凭据",
        }.get(operation, "无法读取 macOS 钥匙串凭据")
        raise SecureCredentialError(code, message)

    def exists(self) -> bool:
        security = self._module()
        query = self._query()
        query[security.kSecReturnAttributes] = True
        query[security.kSecMatchLimit] = security.kSecMatchLimitOne
        status, _value = security.SecItemCopyMatching(query, None)
        status = int(status)
        if status == security.errSecSuccess:
            return True
        if status == security.errSecItemNotFound:
            return False
        self._raise_status(status, operation="read")
        return False

    def read(self) -> str | None:
        security = self._module()
        query = self._query()
        query[security.kSecReturnData] = True
        query[security.kSecMatchLimit] = security.kSecMatchLimitOne
        status, value = security.SecItemCopyMatching(query, None)
        status = int(status)
        if status == security.errSecItemNotFound:
            return None
        if status != security.errSecSuccess:
            self._raise_status(status, operation="read")
        try:
            return validate_deepseek_api_key(bytes(value).decode("ascii"))
        except (AttributeError, TypeError, UnicodeDecodeError, SecureCredentialError) as exc:
            raise SecureCredentialError(ERROR_CORRUPTED, "Keychain 中的 API 密钥格式无效") from exc

    def write(self, secret: str) -> None:
        security = self._module()
        encoded = validate_deepseek_api_key(secret).encode("ascii")
        query = self._query()
        status = _security_status(
            security.SecItemUpdate(query, {security.kSecValueData: encoded})
        )
        if status == security.errSecSuccess:
            return
        if status != security.errSecItemNotFound:
            self._raise_status(status, operation="write")
        add_query = self._query()
        add_query[security.kSecValueData] = encoded
        add_query[security.kSecAttrAccessible] = security.kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        add_status = _security_status(security.SecItemAdd(add_query, None))
        if add_status != security.errSecSuccess:
            self._raise_status(add_status, operation="write")

    def delete(self) -> None:
        security = self._module()
        status = _security_status(security.SecItemDelete(self._query()))
        if status not in {security.errSecSuccess, security.errSecItemNotFound}:
            self._raise_status(status, operation="delete")


class LocalPreviewCredentialBackend:
    """Ad-hoc preview fallback that never probes macOS Keychain.

    The random AES key and ciphertext are separate 0600 files in a 0700
    Application Support directory. Formal signed builds select Keychain.
    """

    storage_label = "macos-preview-local-aes-256-gcm"

    def __init__(self, ciphertext_path: Path, key_path: Path) -> None:
        self.ciphertext_path = ciphertext_path.expanduser()
        self.key_path = key_path.expanduser()

    def _private_directory(self) -> Path:
        if self.ciphertext_path.parent != self.key_path.parent:
            raise SecureCredentialError(ERROR_UNAVAILABLE, "本机凭据路径配置无效")
        directory = self.ciphertext_path.parent
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink():
            raise SecureCredentialError(ERROR_UNAVAILABLE, "本机凭据目录不安全")
        try:
            os.chmod(directory, 0o700)
        except OSError as exc:
            raise SecureCredentialError(ERROR_UNAVAILABLE, "无法保护本机凭据目录") from exc
        return directory

    def _read_or_create_key(self) -> bytes:
        self._private_directory()
        if self.key_path.is_symlink():
            raise SecureCredentialError(ERROR_UNAVAILABLE, "本机凭据密钥路径不安全")
        try:
            key = self.key_path.read_bytes()
        except FileNotFoundError:
            key = os.urandom(32)
            try:
                descriptor = os.open(
                    self.key_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                key = self.key_path.read_bytes()
            except OSError as exc:
                raise SecureCredentialError(ERROR_WRITE, "无法创建本机凭据密钥") from exc
            else:
                try:
                    os.write(descriptor, key)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        except OSError as exc:
            raise SecureCredentialError(ERROR_UNAVAILABLE, "无法读取本机凭据密钥") from exc
        if len(key) != 32:
            raise SecureCredentialError(ERROR_CORRUPTED, "本机凭据密钥格式无效")
        try:
            os.chmod(self.key_path, 0o600)
        except OSError as exc:
            raise SecureCredentialError(ERROR_UNAVAILABLE, "无法保护本机凭据密钥") from exc
        return key

    def exists(self) -> bool:
        if self.ciphertext_path.is_symlink() or self.key_path.is_symlink():
            raise SecureCredentialError(ERROR_UNAVAILABLE, "本机凭据路径不安全")
        if not self.ciphertext_path.exists():
            return False
        # Status must fail closed if the ciphertext exists but is unusable.
        return self.read() is not None

    def read(self) -> str | None:
        if not self.ciphertext_path.exists():
            return None
        if self.ciphertext_path.is_symlink():
            raise SecureCredentialError(ERROR_UNAVAILABLE, "本机凭据路径不安全")
        try:
            envelope = json.loads(self.ciphertext_path.read_text(encoding="utf-8"))
            if envelope.get("version") != 1 or envelope.get("algorithm") != "AES-256-GCM":
                raise SecureCredentialError(ERROR_CORRUPTED, "本机凭据版本不受支持")
            nonce = base64.b64decode(envelope["nonce"], validate=True)
            ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
            plaintext = AESGCM(self._read_or_create_key()).decrypt(
                nonce,
                ciphertext,
                DEEPSEEK_AAD,
            )
            return validate_deepseek_api_key(plaintext.decode("ascii"))
        except SecureCredentialError:
            raise
        except (OSError, KeyError, TypeError, ValueError, UnicodeDecodeError, InvalidTag) as exc:
            raise SecureCredentialError(ERROR_CORRUPTED, "本机凭据损坏、被篡改或密钥不可用") from exc

    def write(self, secret: str) -> None:
        validated = validate_deepseek_api_key(secret)
        directory = self._private_directory()
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._read_or_create_key()).encrypt(
            nonce,
            validated.encode("ascii"),
            DEEPSEEK_AAD,
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
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".deepseek-api-key-",
                dir=directory,
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                os.fchmod(handle.fileno(), 0o600)
                handle.write(envelope)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.ciphertext_path)
            os.chmod(self.ciphertext_path, 0o600)
        except OSError as exc:
            raise SecureCredentialError(ERROR_WRITE, "无法安全保存本机凭据") from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def delete(self) -> None:
        try:
            self.ciphertext_path.unlink(missing_ok=True)
            self.key_path.unlink(missing_ok=True)
        except OSError as exc:
            raise SecureCredentialError(ERROR_DELETE, "无法删除本机凭据") from exc


class DeepSeekCredentialStore:
    def __init__(self, backend: CredentialBackend) -> None:
        self.backend = backend

    def status(self) -> CredentialStatus:
        return CredentialStatus(
            provider=DEEPSEEK_PROVIDER,
            configured=self.backend.exists(),
            storage=self.backend.storage_label,
        )

    def read_for_runtime(self) -> str | None:
        return self.backend.read()

    def save(self, api_key: Any) -> CredentialStatus:
        validated = validate_deepseek_api_key(api_key)
        self.backend.write(validated)
        return self.status()

    def delete(self) -> CredentialStatus:
        self.backend.delete()
        return self.status()


def default_deepseek_credential_store() -> DeepSeekCredentialStore:
    mode = os.environ.get("AUTO_RESEARCH_MACOS_CREDENTIAL_STORE", "local-preview").strip().lower()
    if mode == "keychain":
        return DeepSeekCredentialStore(MacKeychainCredentialBackend())
    if mode != "local-preview":
        raise SecureCredentialError(ERROR_UNAVAILABLE, "桌面凭据存储模式配置无效")
    private_directory = (
        Path.home()
        / "Library"
        / "Application Support"
        / "Auto Research"
        / "Private Data"
    )
    return DeepSeekCredentialStore(
        LocalPreviewCredentialBackend(
            private_directory / "deepseek-api-key-v1.enc",
            private_directory / "deepseek-api-key-v1.key",
        )
    )
