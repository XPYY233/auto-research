from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid
from copy import deepcopy
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol


STATE_SCHEMA_VERSION = "research-state-v1"
_ENTITY_TYPES = {"item", "finding", "table", "figure"}
_STATE_KEYS = {
    "schema_version", "conversation_id", "turn_id", "evidence_version", "active_topic",
    "constraints", "anchors", "bundles", "selected_anchor_uids", "issued_at",
    "expires_at", "parent_state_hash", "state_hash",
}


class ResearchStateError(ValueError):
    pass


class StateSigner(Protocol):
    def sign(self, payload: bytes) -> str: ...

    def verify(self, payload: bytes, signature: str) -> bool: ...


class HMACStateSigner:
    def __init__(self, secret: bytes | None = None):
        self._secret = secret or secrets.token_bytes(32)

    def sign(self, payload: bytes) -> str:
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()

    def verify(self, payload: bytes, signature: str) -> bool:
        expected = self.sign(payload)
        return bool(signature) and hmac.compare_digest(signature, expected)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def opaque_source_id(source_scope: str, source_identity: str) -> str:
    digest = hashlib.sha256(f"{source_scope}:{source_identity}".encode("utf-8")).hexdigest()[:20]
    return f"{source_scope}-{digest}"


def stable_entity_uid(source_scope: str, source_id: str, entity_type: str, locator: Any) -> str:
    digest = hashlib.sha256(
        _canonical_json([source_scope, source_id, entity_type, str(locator)])
    ).hexdigest()
    return f"ev-{digest[:32]}"


def stable_bundle_uid(source_id: str, bundle: Mapping[str, Any]) -> str:
    identity = [
        source_id,
        str(bundle.get("doi") or bundle.get("article_title") or ""),
        str(bundle.get("material") or ""),
        str(bundle.get("conditions") or ""),
    ]
    return f"bu-{hashlib.sha256(_canonical_json(identity)).hexdigest()[:32]}"


@dataclass(frozen=True)
class ResolvedAnchor:
    entity_uid: str
    entity_type: str
    locator: int


class ResearchStateCodec:
    """Current-process signing plus an in-memory opaque-identity locator map."""

    def __init__(
        self,
        secret: bytes | None = None,
        *,
        signer: StateSigner | None = None,
        clock: Callable[[], float] | None = None,
        ttl_seconds: int = 7_200,
        max_locators: int = 8_192,
    ):
        self._signer = signer or HMACStateSigner(secret)
        self._clock = clock or time.time
        self._ttl_seconds = min(max(int(ttl_seconds), 60), 86_400)
        self._max_locators = min(max(int(max_locators), 128), 65_536)
        self._locators: OrderedDict[tuple[str, str], ResolvedAnchor] = OrderedDict()
        self._consumed: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def sign(self, state: Mapping[str, Any]) -> str:
        payload = self.validate_public_state(state)
        return self._signer.sign(_canonical_json(payload))

    def verify(
        self,
        state: Any,
        token: Any,
        *,
        evidence_version: str,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        payload = self.validate_public_state(state)
        supplied = str(token or "")
        if not self._signer.verify(_canonical_json(payload), supplied):
            raise ResearchStateError("research_state_signature_invalid")
        if float(payload.get("expires_at") or 0) < float(self._clock()):
            raise ResearchStateError("research_state_expired")
        if str(payload.get("evidence_version") or "") != str(evidence_version or ""):
            raise ResearchStateError("research_state_evidence_version_mismatch")
        if conversation_id and str(payload.get("conversation_id") or "") != str(conversation_id):
            raise ResearchStateError("research_state_conversation_mismatch")
        return payload

    def consume(self, token: str, request_fingerprint: str) -> bool:
        """Consume once; return True only for an idempotent replay."""

        token_hash = hashlib.sha256(str(token).encode("utf-8")).hexdigest()
        with self._lock:
            now = float(self._clock())
            for key, (_, expires_at) in tuple(self._consumed.items()):
                if expires_at < now:
                    self._consumed.pop(key, None)
            previous = self._consumed.get(token_hash)
            if previous is None:
                self._consumed[token_hash] = (request_fingerprint, now + self._ttl_seconds)
                return False
            if hmac.compare_digest(previous[0], request_fingerprint):
                return True
        raise ResearchStateError("research_state_replayed_for_different_request")

    @staticmethod
    def validate_public_state(state: Any) -> dict[str, Any]:
        if not isinstance(state, Mapping):
            raise ResearchStateError("research_state_invalid")
        if set(state) - _STATE_KEYS or state.get("schema_version") != STATE_SCHEMA_VERSION:
            raise ResearchStateError("research_state_invalid")
        payload = deepcopy(dict(state))
        expected_hash = str(payload.get("state_hash") or "")
        unsigned = dict(payload)
        unsigned.pop("state_hash", None)
        actual_hash = hashlib.sha256(_canonical_json(unsigned)).hexdigest()
        if not expected_hash or not hmac.compare_digest(expected_hash, actual_hash):
            raise ResearchStateError("research_state_hash_invalid")
        serialized = json.dumps(payload, ensure_ascii=False, default=str)
        forbidden = ("/Users/", "file://", "pdf_path", "image_path", "zotero_key")
        if any(value.casefold() in serialized.casefold() for value in forbidden):
            raise ResearchStateError("research_state_contains_private_fields")
        anchors = payload.get("anchors")
        bundles = payload.get("bundles")
        if not isinstance(anchors, dict) or len(anchors) > 80:
            raise ResearchStateError("research_state_invalid_anchors")
        if not isinstance(bundles, dict) or len(bundles) > 80:
            raise ResearchStateError("research_state_invalid_bundles")
        for ref, anchor in anchors.items():
            if not isinstance(ref, str) or not ref.startswith("R") or not isinstance(anchor, dict):
                raise ResearchStateError("research_state_invalid_anchors")
            if anchor.get("entity_type") not in _ENTITY_TYPES:
                raise ResearchStateError("research_state_invalid_entity_type")
            if set(anchor) - {"source_scope", "source_id", "entity_uid", "entity_type"}:
                raise ResearchStateError("research_state_invalid_anchors")
        anchor_uids = [str(anchor.get("entity_uid") or "") for anchor in anchors.values()]
        if not all(anchor_uids) or len(set(anchor_uids)) != len(anchor_uids):
            raise ResearchStateError("research_state_duplicate_anchor")
        known_uids = set(anchor_uids)
        for bundle in bundles.values():
            if not isinstance(bundle, dict) or set(bundle) - {
                "source_scope", "source_id", "bundle_uid", "member_entity_uids"
            }:
                raise ResearchStateError("research_state_invalid_bundles")
            members = bundle.get("member_entity_uids") or []
            if not isinstance(members, list) or len(members) != len(set(members)):
                raise ResearchStateError("research_state_invalid_bundles")
            if not set(members).issubset(known_uids):
                raise ResearchStateError("research_state_unknown_bundle_anchor")
        return payload

    def register(self, source_id: str, entity_uid: str, entity_type: str, locator: int) -> None:
        with self._lock:
            key = (source_id, entity_uid)
            self._locators[key] = ResolvedAnchor(
                entity_uid=entity_uid,
                entity_type=entity_type,
                locator=int(locator),
            )
            self._locators.move_to_end(key)
            while len(self._locators) > self._max_locators:
                self._locators.popitem(last=False)

    def resolve(self, source_id: str, entity_uid: str) -> ResolvedAnchor | None:
        with self._lock:
            key = (source_id, entity_uid)
            value = self._locators.get(key)
            if value is not None:
                self._locators.move_to_end(key)
            return value

    def build(
        self,
        *,
        evidence_version: str,
        conversation_id: str,
        active_topic: str,
        constraints: Mapping[str, Any],
        source_id: str,
        candidates: list[dict[str, Any]],
        bundles: list[dict[str, Any]],
        selected_refs: set[str] | None = None,
        parent_state: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        selected_refs = selected_refs or set()
        anchors: dict[str, dict[str, str]] = {}
        uid_by_ref: dict[str, str] = {}
        for candidate in candidates[:80]:
            ref = str(candidate.get("ref") or "")
            entity_type = str(candidate.get("entity_type") or "")
            locator = int(candidate.get("entity_id") or 0)
            if not ref.startswith("R") or entity_type not in _ENTITY_TYPES or locator < 1:
                continue
            uid = stable_entity_uid("official", source_id, entity_type, locator)
            self.register(source_id, uid, entity_type, locator)
            uid_by_ref[ref] = uid
            anchors[ref] = {
                "source_scope": "official",
                "source_id": source_id,
                "entity_uid": uid,
                "entity_type": entity_type,
            }
        public_bundles: dict[str, dict[str, Any]] = {}
        for bundle in bundles[:80]:
            display_id = str(bundle.get("id") or "")
            if not display_id.startswith("B"):
                continue
            member_uids = [uid_by_ref[ref] for ref in bundle.get("refs") or [] if ref in uid_by_ref]
            bundle_uid = stable_bundle_uid(source_id, bundle)
            bundle["bundle_uid"] = bundle_uid
            public_bundles[display_id] = {
                "source_scope": "official",
                "source_id": source_id,
                "bundle_uid": bundle_uid,
                "member_entity_uids": member_uids,
            }
        state = {
            "schema_version": STATE_SCHEMA_VERSION,
            "conversation_id": str(conversation_id or uuid.uuid4()),
            "turn_id": str(uuid.uuid4()),
            "evidence_version": str(evidence_version or ""),
            "active_topic": " ".join(str(active_topic or "").split())[:500],
            "constraints": deepcopy(dict(constraints)),
            "anchors": anchors,
            "bundles": public_bundles,
            "selected_anchor_uids": [uid_by_ref[ref] for ref in sorted(selected_refs) if ref in uid_by_ref],
            "issued_at": int(self._clock()),
            "expires_at": int(self._clock()) + self._ttl_seconds,
            "parent_state_hash": str((parent_state or {}).get("state_hash") or ""),
        }
        state["state_hash"] = hashlib.sha256(_canonical_json(state)).hexdigest()
        return state, self.sign(state)

    @staticmethod
    def fingerprint(state: Mapping[str, Any] | None) -> str:
        if not state:
            return "none"
        return hashlib.sha256(_canonical_json(state)).hexdigest()


DEFAULT_RESEARCH_STATE_CODEC = ResearchStateCodec()
