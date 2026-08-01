from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


TRUSTED_PUBLISHER_REGISTRY_VERSION = 1


@dataclass(frozen=True)
class TrustedPublisher:
    key_id: str
    display_name: str
    manifest_publisher_name: str
    public_key_base64: str
    channel: str
    allowed_package_ids: tuple[str, ...]

    def public_key_bytes(self) -> bytes:
        try:
            raw = base64.b64decode(self.public_key_base64, validate=True)
            Ed25519PublicKey.from_public_bytes(raw)
        except (ValueError, binascii.Error) as exc:
            raise RuntimeError("受信发布者公钥注册表损坏") from exc
        if len(raw) != 32:
            raise RuntimeError("受信发布者公钥注册表损坏")
        return raw

    def public_dict(self) -> dict[str, Any]:
        return {
            "key_id": self.key_id,
            "display_name": self.display_name,
            "channel": self.channel,
            "public_key_fingerprint": hashlib.sha256(
                self.public_key_bytes()
            ).hexdigest(),
            "allowed_package_ids": list(self.allowed_package_ids),
        }


_PUBLISHERS = (
    TrustedPublisher(
        key_id="auto-research-internal-preview-2026-v1",
        display_name="Auto Research 课题组内部预览",
        manifest_publisher_name="Auto Research internal preview",
        public_key_base64="oUWHaMlBQWoA/u6gT0q1BvPGTl5JfZF7x7hObwp7nig=",
        channel="internal-preview",
        allowed_package_ids=("auto-research-internal-evidence",),
    ),
)


def trusted_publishers() -> tuple[TrustedPublisher, ...]:
    """Return the immutable publisher registry bundled with the application."""

    return _PUBLISHERS


def trusted_public_keys(*, channel: str | None = None) -> Mapping[str, bytes]:
    """Return a fresh, read-only key map for package verification.

    Private signing keys never belong in the application or an evidence
    package.  Updating this registry requires a normal reviewed application
    release, making package trust independent from the imported file itself.
    """

    keys: dict[str, bytes] = {}
    for publisher in _PUBLISHERS:
        if channel is not None and publisher.channel != channel:
            continue
        if publisher.key_id in keys:
            raise RuntimeError("受信发布者注册表包含重复 key_id")
        keys[publisher.key_id] = publisher.public_key_bytes()
    return MappingProxyType(keys)


def assert_trusted_package_identity(
    *, key_id: str, package_id: str, publisher_name: str
) -> TrustedPublisher:
    for publisher in _PUBLISHERS:
        if publisher.key_id != key_id:
            continue
        if (
            package_id not in publisher.allowed_package_ids
            or publisher_name != publisher.manifest_publisher_name
        ):
            raise RuntimeError("资料包身份不在受信发布者允许范围内")
        return publisher
    raise RuntimeError("资料包签名者不在应用内置信任注册表中")


__all__ = [
    "TRUSTED_PUBLISHER_REGISTRY_VERSION",
    "TrustedPublisher",
    "assert_trusted_package_identity",
    "trusted_publishers",
    "trusted_public_keys",
]
