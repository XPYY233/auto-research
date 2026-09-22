"""Keep historical encryption domains local when application branding changes.

The bundle identifier is not a key, but changing authenticated-data bytes makes
existing ciphertext unreadable. Preserve the old identifier without embedding
private maintainer metadata in public source or rewriting any encrypted store.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import tempfile

PUBLIC_APP_IDENTIFIER = "com.researcher.autoresearch"
IDENTITY_FILENAME = "crypto-identity-v1.json"
_SCHEMA = "auto-research-local-crypto-identity-v1"
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{2,199}\Z")


def private_directory() -> Path:
    return Path.home() / "Library/Application Support/Auto Research/Private Data"


def _validate(value: object) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError("本机加密身份记录无效；请从升级前备份恢复，不能删除后重试。")
    return value


def encryption_identity(root: Path | None = None) -> str:
    path = (root if root is not None else private_directory()) / IDENTITY_FILENAME
    if path.is_symlink():
        raise ValueError("本机加密身份记录不能是符号链接。")
    try:
        with path.open("rb") as stream:
            raw = stream.read(4097)
    except FileNotFoundError:
        return PUBLIC_APP_IDENTIFIER
    try:
        if len(raw) > 4096:
            raise ValueError
        record = json.loads(raw)
        if set(record) != {"schema", "application_identifier"} or record["schema"] != _SCHEMA:
            raise ValueError
        return _validate(record["application_identifier"])
    except (ValueError, TypeError):
        raise ValueError("本机加密身份记录无效；请从升级前备份恢复，不能删除后重试。") from None


def preserve_identity(previous_app: Path, *, root: Path | None = None) -> bool:
    """Publish once before replacing the old App; an existing identity wins.

    Subsequent upgrades have a public bundle identifier while encrypted state
    still uses the original identity. Never overwrite that recorded authority.
    Returns whether a new local record was written. No identity is logged.
    """
    root = root if root is not None else private_directory()
    target = root / IDENTITY_FILENAME
    if target.exists() or target.is_symlink():
        encryption_identity(root)
        return False
    info = plistlib.loads((previous_app / "Contents/Info.plist").read_bytes())
    if info.get("CFBundleName", info.get("CFBundleDisplayName")) != "Auto Research":
        raise ValueError("请选择升级前的 Auto Research App。")
    identifier = _validate(info.get("CFBundleIdentifier"))
    if root.is_symlink():
        raise ValueError("本机私人状态目录不能是符号链接。")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    payload = json.dumps({"schema": _SCHEMA, "application_identifier": identifier}).encode()
    descriptor, name = tempfile.mkstemp(prefix=".crypto-identity-", dir=root)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(staged, target)
        except FileExistsError:
            if encryption_identity(root) != identifier:
                raise ValueError("另一更新已保存不同加密身份；请保留原 App 并检查。") from None
            return False
        directory_fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return True
    finally:
        staged.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="升级前保留本机历史加密身份；不读取或上传 API 密钥")
    parser.add_argument("--previous-app", required=True, type=Path)
    args = parser.parse_args()
    preserve_identity(args.previous_app)
    print("本机加密身份已保留；原密文和密钥未修改。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
