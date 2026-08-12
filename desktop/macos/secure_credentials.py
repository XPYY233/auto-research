from __future__ import annotations

import base64
import json
import os
import stat
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from auto_research.settings.ai_runtime_state import BackendCredentialState


APP_IDENTIFIER = "com.researcher.autoresearch"
DEEPSEEK_PROVIDER = "deepseek"
OPENAI_PROVIDER = "openai"
SUPPORTED_PROVIDERS = (DEEPSEEK_PROVIDER, OPENAI_PROVIDER)
FIXED_CREDENTIAL_REFS = {
    DEEPSEEK_PROVIDER: "deepseek.default",
    OPENAI_PROVIDER: "openai.default",
}
PROVIDER_CREDENTIAL_ENVELOPE_SCHEMA = "provider-credential-envelope-v1"

# Keep the historical DeepSeek item/path identity so an installed preview can
# read its old pure-secret value. The first mutation replaces it atomically with
# a provider envelope. OpenAI always receives a separate item/file and AAD.
DEEPSEEK_KEYCHAIN_SERVICE = f"{APP_IDENTIFIER}.deepseek-api.v1"
DEEPSEEK_KEYCHAIN_ACCOUNT = "deepseek-api-key"
OPENAI_KEYCHAIN_SERVICE = f"{APP_IDENTIFIER}.openai-api.v1"
OPENAI_KEYCHAIN_ACCOUNT = "openai-api-key"
DEEPSEEK_AAD = f"{APP_IDENTIFIER}:deepseek-api:v1".encode("utf-8")
OPENAI_AAD = f"{APP_IDENTIFIER}:openai-api:v1".encode("utf-8")

MIN_API_KEY_CHARS = 8
MAX_API_KEY_CHARS = 4096
MAX_BACKEND_VALUE_BYTES = 16 * 1024

ERROR_INVALID = "credential_invalid"
ERROR_UNAVAILABLE = "credential_store_unavailable"
ERROR_LOCKED = "credential_store_locked"
ERROR_DENIED = "credential_store_denied"
ERROR_CORRUPTED = "credential_store_corrupted"
ERROR_WRITE = "credential_store_write_failed"
ERROR_DELETE = "credential_store_delete_failed"


class SecureCredentialError(RuntimeError):
    """A stable, path-free and secret-free desktop credential error."""

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


@dataclass(frozen=True)
class _ProviderEnvelope:
    provider_id: str
    generation: int
    api_key: str | None

    @property
    def configured(self) -> bool:
        return self.api_key is not None


class CredentialBackend(Protocol):
    """One atomic provider record; values may be legacy pure secrets on read."""

    storage_label: str

    def exists(self) -> bool: ...

    def read(self) -> str | None: ...

    def write(self, value: str) -> None: ...

    def delete(self) -> None: ...


def validate_provider_api_key(value: Any) -> str:
    if not isinstance(value, str):
        raise SecureCredentialError(ERROR_INVALID, "API 密钥格式无效", http_status=400)
    if not (MIN_API_KEY_CHARS <= len(value) <= MAX_API_KEY_CHARS):
        raise SecureCredentialError(ERROR_INVALID, "API 密钥长度无效", http_status=400)
    if any(character.isspace() or ord(character) < 33 or ord(character) > 126 for character in value):
        raise SecureCredentialError(
            ERROR_INVALID,
            "API 密钥只能包含无空白的可打印 ASCII 字符",
            http_status=400,
        )
    return value


def validate_deepseek_api_key(value: Any) -> str:
    """Historical public name retained for the old DeepSeek bridge."""

    return validate_provider_api_key(value)


def _security_status(value: Any) -> int:
    return int(value[0] if isinstance(value, tuple) else value)


class MacKeychainCredentialBackend:
    """Formal signed-build storage using one provider-specific Keychain item."""

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
        except ImportError as exc:  # pragma: no cover - outside macOS/PyObjC.
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
            raw = bytes(value)
            if len(raw) > MAX_BACKEND_VALUE_BYTES:
                raise ValueError("oversized credential record")
            return raw.decode("utf-8")
        except (AttributeError, TypeError, UnicodeDecodeError, ValueError) as exc:
            raise SecureCredentialError(ERROR_CORRUPTED, "Keychain 中的凭据格式无效") from exc

    def write(self, value: str) -> None:
        if not isinstance(value, str):
            raise SecureCredentialError(ERROR_WRITE, "无法写入 macOS 钥匙串凭据")
        encoded = value.encode("utf-8")
        if not encoded or len(encoded) > MAX_BACKEND_VALUE_BYTES:
            raise SecureCredentialError(ERROR_WRITE, "无法写入 macOS 钥匙串凭据")
        security = self._module()
        query = self._query()
        status = _security_status(security.SecItemUpdate(query, {security.kSecValueData: encoded}))
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
    """Ad-hoc preview AES-256-GCM store for one provider record.

    The random key and ciphertext remain separate 0600 regular files in one
    0700 directory. Reads use O_NOFOLLOW and verify the opened inode was not
    replaced while it was being consumed. Writes replace one complete encrypted
    provider envelope atomically.
    """

    storage_label = "macos-preview-local-aes-256-gcm"

    def __init__(
        self,
        ciphertext_path: Path,
        key_path: Path,
        *,
        aad: bytes = DEEPSEEK_AAD,
        temporary_prefix: str = ".deepseek-api-key-",
    ) -> None:
        self.ciphertext_path = ciphertext_path.expanduser()
        self.key_path = key_path.expanduser()
        if not isinstance(aad, bytes) or not aad:
            raise ValueError("credential AAD must be non-empty bytes")
        if not temporary_prefix or "/" in temporary_prefix:
            raise ValueError("credential temporary prefix is invalid")
        self.aad = aad
        self.temporary_prefix = temporary_prefix

    def _private_directory(self) -> Path:
        if self.ciphertext_path.parent != self.key_path.parent:
            raise SecureCredentialError(ERROR_UNAVAILABLE, "本机凭据路径配置无效")
        directory = self.ciphertext_path.parent
        try:
            directory.mkdir(parents=True, exist_ok=True)
            metadata = os.lstat(directory)
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise OSError("unsafe credential directory")
            os.chmod(directory, 0o700)
        except OSError as exc:
            raise SecureCredentialError(ERROR_UNAVAILABLE, "无法保护本机凭据目录") from exc
        return directory

    @staticmethod
    def _read_regular_file(path: Path, *, maximum: int) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise SecureCredentialError(ERROR_UNAVAILABLE, "本机凭据路径不安全") from exc
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise SecureCredentialError(ERROR_UNAVAILABLE, "本机凭据路径不安全")
            if opened.st_mode & 0o077:
                raise SecureCredentialError(ERROR_UNAVAILABLE, "本机凭据文件权限不安全")
            chunks: list[bytes] = []
            length = 0
            while True:
                chunk = os.read(descriptor, min(4096, maximum + 1 - length))
                if not chunk:
                    break
                chunks.append(chunk)
                length += len(chunk)
                if length > maximum:
                    raise SecureCredentialError(ERROR_CORRUPTED, "本机凭据内容超出安全上限")
            current = os.stat(path, follow_symlinks=False)
            if (
                not stat.S_ISREG(current.st_mode)
                or current.st_dev != opened.st_dev
                or current.st_ino != opened.st_ino
            ):
                raise SecureCredentialError(ERROR_UNAVAILABLE, "本机凭据读取期间发生替换")
            return b"".join(chunks)
        except FileNotFoundError as exc:
            raise SecureCredentialError(ERROR_UNAVAILABLE, "本机凭据读取期间发生替换") from exc
        finally:
            os.close(descriptor)

    def _read_or_create_key(self) -> bytes:
        self._private_directory()
        try:
            key = self._read_regular_file(self.key_path, maximum=32)
        except FileNotFoundError:
            created = os.urandom(32)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(self.key_path, flags, 0o600)
            except FileExistsError:
                key = self._read_regular_file(self.key_path, maximum=32)
            except OSError as exc:
                raise SecureCredentialError(ERROR_WRITE, "无法创建本机凭据密钥") from exc
            else:
                try:
                    if os.write(descriptor, created) != len(created):
                        raise OSError("short credential key write")
                    os.fsync(descriptor)
                except OSError as exc:
                    raise SecureCredentialError(ERROR_WRITE, "无法创建本机凭据密钥") from exc
                finally:
                    os.close(descriptor)
                key = created
        if len(key) != 32:
            raise SecureCredentialError(ERROR_CORRUPTED, "本机凭据密钥格式无效")
        return key

    def exists(self) -> bool:
        self._private_directory()
        try:
            self._read_regular_file(self.ciphertext_path, maximum=MAX_BACKEND_VALUE_BYTES * 2)
        except FileNotFoundError:
            return False
        return True

    def read(self) -> str | None:
        self._private_directory()
        try:
            serialized = self._read_regular_file(
                self.ciphertext_path, maximum=MAX_BACKEND_VALUE_BYTES * 2
            )
        except FileNotFoundError:
            return None
        try:
            encrypted = json.loads(serialized.decode("utf-8"))
            if set(encrypted) != {"version", "algorithm", "nonce", "ciphertext"}:
                raise ValueError("invalid encrypted envelope fields")
            if encrypted["version"] != 1 or encrypted["algorithm"] != "AES-256-GCM":
                raise ValueError("unsupported encrypted envelope")
            nonce = base64.b64decode(encrypted["nonce"], validate=True)
            ciphertext = base64.b64decode(encrypted["ciphertext"], validate=True)
            plaintext = AESGCM(self._read_or_create_key()).decrypt(nonce, ciphertext, self.aad)
            if len(plaintext) > MAX_BACKEND_VALUE_BYTES:
                raise ValueError("oversized plaintext")
            return plaintext.decode("utf-8")
        except SecureCredentialError:
            raise
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, InvalidTag) as exc:
            raise SecureCredentialError(ERROR_CORRUPTED, "本机凭据损坏、被篡改或密钥不可用") from exc

    def write(self, value: str) -> None:
        if not isinstance(value, str):
            raise SecureCredentialError(ERROR_WRITE, "无法安全保存本机凭据")
        plaintext = value.encode("utf-8")
        if not plaintext or len(plaintext) > MAX_BACKEND_VALUE_BYTES:
            raise SecureCredentialError(ERROR_WRITE, "无法安全保存本机凭据")
        directory = self._private_directory()
        # Refuse to replace an unsafe existing object.
        try:
            self._read_regular_file(self.ciphertext_path, maximum=MAX_BACKEND_VALUE_BYTES * 2)
        except FileNotFoundError:
            pass
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._read_or_create_key()).encrypt(nonce, plaintext, self.aad)
        encrypted = json.dumps(
            {
                "version": 1,
                "algorithm": "AES-256-GCM",
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=self.temporary_prefix,
                dir=directory,
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                os.fchmod(handle.fileno(), 0o600)
                handle.write(encrypted)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.ciphertext_path)
            temporary_path = None
            directory_flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            directory_descriptor = os.open(directory, directory_flags)
            try:
                if not stat.S_ISDIR(os.fstat(directory_descriptor).st_mode):
                    raise OSError("unsafe credential directory")
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError as exc:
            raise SecureCredentialError(ERROR_WRITE, "无法安全保存本机凭据") from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def delete(self) -> None:
        """Physical deletion is maintenance-only; manager deletion writes a tombstone."""

        try:
            self.ciphertext_path.unlink(missing_ok=True)
            self.key_path.unlink(missing_ok=True)
        except OSError as exc:
            raise SecureCredentialError(ERROR_DELETE, "无法删除本机凭据") from exc


class ProviderCredentialManager:
    """The sole macOS authority for provider secrets and generations."""

    def __init__(self, backends: Mapping[str, CredentialBackend]) -> None:
        if not isinstance(backends, Mapping) or not backends:
            raise ValueError("provider credential backends are required")
        unsupported = set(backends) - set(SUPPORTED_PROVIDERS)
        if unsupported:
            raise ValueError("unsupported provider credential backend")
        self._backends = dict(backends)
        self._lock = threading.RLock()

    @staticmethod
    def _provider(provider_id: object) -> str:
        if not isinstance(provider_id, str) or provider_id not in SUPPORTED_PROVIDERS:
            raise SecureCredentialError(ERROR_INVALID, "AI 提供商无效", http_status=400)
        return provider_id

    @staticmethod
    def _credential_ref(provider_id: str, credential_ref: object) -> str:
        expected = FIXED_CREDENTIAL_REFS[provider_id]
        if credential_ref != expected:
            raise SecureCredentialError(ERROR_INVALID, "AI 凭据引用无效", http_status=400)
        return expected

    def _backend(self, provider_id: str) -> CredentialBackend:
        try:
            return self._backends[provider_id]
        except KeyError as exc:
            raise SecureCredentialError(ERROR_UNAVAILABLE, "本机 AI 凭据存储不可用") from exc

    @staticmethod
    def _serialize(record: _ProviderEnvelope) -> str:
        return json.dumps(
            {
                "schema": PROVIDER_CREDENTIAL_ENVELOPE_SCHEMA,
                "provider_id": record.provider_id,
                "generation": record.generation,
                "api_key": record.api_key,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def _read_record(self, provider_id: str) -> _ProviderEnvelope:
        raw = self._backend(provider_id).read()
        if raw is None:
            return _ProviderEnvelope(provider_id, 0, None)
        if not isinstance(raw, str):
            raise SecureCredentialError(ERROR_CORRUPTED, "本机 AI 凭据格式无效")
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            # Historical DeepSeek stores contained only the pure API secret.
            if provider_id != DEEPSEEK_PROVIDER:
                raise SecureCredentialError(ERROR_CORRUPTED, "本机 AI 凭据格式无效")
            try:
                legacy = validate_provider_api_key(raw)
            except SecureCredentialError as exc:
                raise SecureCredentialError(ERROR_CORRUPTED, "本机 AI 凭据格式无效") from exc
            if not legacy.startswith("sk-"):
                raise SecureCredentialError(ERROR_CORRUPTED, "本机 AI 凭据格式无效")
            return _ProviderEnvelope(provider_id, 1, legacy)
        if not isinstance(parsed, dict) or set(parsed) != {
            "schema",
            "provider_id",
            "generation",
            "api_key",
        }:
            raise SecureCredentialError(ERROR_CORRUPTED, "本机 AI 凭据格式无效")
        generation = parsed.get("generation")
        if (
            parsed.get("schema") != PROVIDER_CREDENTIAL_ENVELOPE_SCHEMA
            or parsed.get("provider_id") != provider_id
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 1
        ):
            raise SecureCredentialError(ERROR_CORRUPTED, "本机 AI 凭据格式无效")
        api_key = parsed.get("api_key")
        if api_key is not None:
            try:
                api_key = validate_provider_api_key(api_key)
            except SecureCredentialError as exc:
                raise SecureCredentialError(ERROR_CORRUPTED, "本机 AI 凭据格式无效") from exc
        return _ProviderEnvelope(provider_id, generation, api_key)

    def state_for(self, provider_id: str) -> BackendCredentialState:
        provider = self._provider(provider_id)
        with self._lock:
            record = self._read_record(provider)
        return BackendCredentialState(
            FIXED_CREDENTIAL_REFS[provider], record.generation, record.configured
        )

    def resolve(self, credential_ref: str) -> str | None:
        providers = [
            provider
            for provider, expected in FIXED_CREDENTIAL_REFS.items()
            if credential_ref == expected
        ]
        if len(providers) != 1:
            raise SecureCredentialError(ERROR_INVALID, "AI 凭据引用无效", http_status=400)
        provider = providers[0]
        with self._lock:
            return self._read_record(provider).api_key

    def save(
        self, *, provider_id: str, credential_ref: str, api_key: str
    ) -> BackendCredentialState:
        provider = self._provider(provider_id)
        self._credential_ref(provider, credential_ref)
        secret = validate_provider_api_key(api_key)
        with self._lock:
            before = self._read_record(provider)
            after = _ProviderEnvelope(provider, before.generation + 1, secret)
            self._backend(provider).write(self._serialize(after))
        return BackendCredentialState(credential_ref, after.generation, True)

    def delete(
        self, *, provider_id: str, credential_ref: str
    ) -> BackendCredentialState:
        provider = self._provider(provider_id)
        self._credential_ref(provider, credential_ref)
        with self._lock:
            before = self._read_record(provider)
            after = _ProviderEnvelope(provider, before.generation + 1, None)
            self._backend(provider).write(self._serialize(after))
        return BackendCredentialState(credential_ref, after.generation, False)

    def storage_label(self, provider_id: str) -> str:
        provider = self._provider(provider_id)
        return self._backend(provider).storage_label

    def backend_for_compatibility(self, provider_id: str) -> CredentialBackend:
        """Narrow test/transition access; never creates a second authority."""

        return self._backend(self._provider(provider_id))

    def deepseek_legacy_view(self) -> "DeepSeekCredentialStore":
        return DeepSeekCredentialStore(self)


class DeepSeekCredentialStore:
    """Old route API as a thin view over the shared provider manager."""

    def __init__(self, backend_or_manager: CredentialBackend | ProviderCredentialManager) -> None:
        if isinstance(backend_or_manager, ProviderCredentialManager):
            self.manager = backend_or_manager
            self._legacy_backend_probe = None
        else:
            self.manager = ProviderCredentialManager({DEEPSEEK_PROVIDER: backend_or_manager})
            self._legacy_backend_probe = backend_or_manager
        self.backend = self.manager.backend_for_compatibility(DEEPSEEK_PROVIDER)

    def status(self) -> CredentialStatus:
        # Preserve old injected-backend availability semantics without making
        # that backend a separate secret authority. Production factories inject
        # the shared manager and therefore perform only the canonical read.
        if self._legacy_backend_probe is not None:
            self._legacy_backend_probe.exists()
        state = self.manager.state_for(DEEPSEEK_PROVIDER)
        return CredentialStatus(
            provider=DEEPSEEK_PROVIDER,
            configured=state.configured,
            storage=self.manager.storage_label(DEEPSEEK_PROVIDER),
        )

    def read_for_runtime(self) -> str | None:
        return self.manager.resolve(FIXED_CREDENTIAL_REFS[DEEPSEEK_PROVIDER])

    def save(self, api_key: Any) -> CredentialStatus:
        self.manager.save(
            provider_id=DEEPSEEK_PROVIDER,
            credential_ref=FIXED_CREDENTIAL_REFS[DEEPSEEK_PROVIDER],
            api_key=validate_provider_api_key(api_key),
        )
        return self.status()

    def delete(self) -> CredentialStatus:
        self.manager.delete(
            provider_id=DEEPSEEK_PROVIDER,
            credential_ref=FIXED_CREDENTIAL_REFS[DEEPSEEK_PROVIDER],
        )
        return self.status()


def default_provider_credential_manager() -> ProviderCredentialManager:
    mode = os.environ.get("AUTO_RESEARCH_MACOS_CREDENTIAL_STORE", "local-preview").strip().lower()
    if mode == "keychain":
        return ProviderCredentialManager(
            {
                DEEPSEEK_PROVIDER: MacKeychainCredentialBackend(
                    service=DEEPSEEK_KEYCHAIN_SERVICE,
                    account=DEEPSEEK_KEYCHAIN_ACCOUNT,
                ),
                OPENAI_PROVIDER: MacKeychainCredentialBackend(
                    service=OPENAI_KEYCHAIN_SERVICE,
                    account=OPENAI_KEYCHAIN_ACCOUNT,
                ),
            }
        )
    if mode != "local-preview":
        raise SecureCredentialError(ERROR_UNAVAILABLE, "桌面凭据存储模式配置无效")
    private_directory = (
        Path.home()
        / "Library"
        / "Application Support"
        / "Auto Research"
        / "Private Data"
    )
    return ProviderCredentialManager(
        {
            DEEPSEEK_PROVIDER: LocalPreviewCredentialBackend(
                private_directory / "deepseek-api-key-v1.enc",
                private_directory / "deepseek-api-key-v1.key",
                aad=DEEPSEEK_AAD,
                temporary_prefix=".deepseek-api-key-",
            ),
            OPENAI_PROVIDER: LocalPreviewCredentialBackend(
                private_directory / "openai-api-key-v1.enc",
                private_directory / "openai-api-key-v1.key",
                aad=OPENAI_AAD,
                temporary_prefix=".openai-api-key-",
            ),
        }
    )


def default_deepseek_credential_store(
    manager: ProviderCredentialManager | None = None,
) -> DeepSeekCredentialStore:
    """Compatibility factory; callers may inject the one shared manager instance."""

    authority = manager if manager is not None else default_provider_credential_manager()
    return authority.deepseek_legacy_view()
