from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives import serialization


TRUSTED_PUBLISHER_REGISTRY_VERSION = 2


@dataclass(frozen=True)
class TrustedPublisher:
    key_id: str
    display_name: str
    manifest_publisher_name: str
    public_key_base64: str
    channel: str
    allowed_package_ids: tuple[str, ...]
    required_rights_redistribution: str

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
            "required_rights_redistribution": self.required_rights_redistribution,
        }


class TrustedPublisherPolicyError(RuntimeError):
    """A stable, path-free failure raised by a publisher trust policy."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TrustedPublisherPolicy:
    """Bind signature keys to package identity, channel and rights scope.

    A plain ``key_id -> public key`` mapping proves only who signed some bytes.
    This policy additionally defines which package identity that signer may
    publish and the exact redistribution scope accepted by the channel.
    """

    channel: str
    publishers: tuple[TrustedPublisher, ...]

    def __post_init__(self) -> None:
        if not self.channel or not self.publishers:
            raise ValueError("受信发布策略必须包含发布通道和发布者")
        key_ids: set[str] = set()
        for publisher in self.publishers:
            if publisher.channel != self.channel:
                raise ValueError("受信发布策略混入了其他发布通道")
            if publisher.key_id in key_ids:
                raise ValueError("受信发布策略包含重复 key_id")
            if not publisher.allowed_package_ids:
                raise ValueError("受信发布者必须至少授权一个 package_id")
            if not publisher.required_rights_redistribution:
                raise ValueError("受信发布者必须明确权利范围")
            key_ids.add(publisher.key_id)

    def public_keys(self) -> Mapping[str, bytes]:
        return MappingProxyType(
            {
                publisher.key_id: publisher.public_key_bytes()
                for publisher in self.publishers
            }
        )

    def assert_public_keys_match(
        self, supplied: Mapping[str, bytes | Ed25519PublicKey]
    ) -> None:
        expected = dict(self.public_keys())
        actual: dict[str, bytes] = {}
        try:
            for key_id, value in supplied.items():
                if isinstance(value, Ed25519PublicKey):
                    raw = value.public_bytes(
                        encoding=serialization.Encoding.Raw,
                        format=serialization.PublicFormat.Raw,
                    )
                else:
                    raw = bytes(value)
                    Ed25519PublicKey.from_public_bytes(raw)
                actual[str(key_id)] = raw
        except (TypeError, ValueError) as exc:
            raise TrustedPublisherPolicyError(
                "trusted_key_policy_mismatch",
                "资料包公钥与受信发布策略不一致",
            ) from exc
        if actual != expected:
            raise TrustedPublisherPolicyError(
                "trusted_key_policy_mismatch",
                "资料包公钥与受信发布策略不一致",
            )

    def assert_package_identity(
        self,
        *,
        key_id: str,
        package_id: str,
        publisher_name: str,
        rights_redistribution: str,
    ) -> TrustedPublisher:
        for publisher in self.publishers:
            if publisher.key_id != key_id:
                continue
            if (
                package_id not in publisher.allowed_package_ids
                or publisher_name != publisher.manifest_publisher_name
            ):
                raise TrustedPublisherPolicyError(
                    "untrusted_package_identity",
                    "资料包身份不在受信发布者允许范围内",
                )
            if rights_redistribution != publisher.required_rights_redistribution:
                raise TrustedPublisherPolicyError(
                    "untrusted_rights_scope",
                    "资料包权利范围与受信发布通道不一致",
                )
            return publisher
        raise TrustedPublisherPolicyError(
            "untrusted_signer", "资料包签名者不在受信发布策略中"
        )


_PUBLISHERS = (
    TrustedPublisher(
        key_id="auto-research-internal-preview-2026-v1",
        display_name="Auto Research 课题组内部预览",
        manifest_publisher_name="Auto Research internal preview",
        public_key_base64="oUWHaMlBQWoA/u6gT0q1BvPGTl5JfZF7x7hObwp7nig=",
        channel="internal-preview",
        allowed_package_ids=("auto-research-internal-evidence",),
        required_rights_redistribution="internal-preview-only",
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


def trusted_publisher_policy(*, channel: str) -> TrustedPublisherPolicy:
    """Return the bundled identity-and-rights policy for one release channel."""

    selected = tuple(
        publisher for publisher in _PUBLISHERS if publisher.channel == channel
    )
    if not selected:
        raise ValueError(f"未知受信发布通道：{channel}")
    return TrustedPublisherPolicy(channel=channel, publishers=selected)


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
    "TrustedPublisherPolicy",
    "TrustedPublisherPolicyError",
    "assert_trusted_package_identity",
    "trusted_publisher_policy",
    "trusted_publishers",
    "trusted_public_keys",
]
