from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from .ai_runtime_state import AI_BUSINESS_SCOPES, AIRuntimeStateService


AI_READINESS_SCHEMA_VERSION = "ai-readiness-v1"
_HARNESS_SCOPES = frozenset({"librarian", "selected_evidence_chat"})


class HarnessReadinessAuthority(Protocol):
    def readiness(self) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class AIReadinessService:
    runtime_state: AIRuntimeStateService
    harness: HarnessReadinessAuthority | None = None

    def public_dict(self) -> dict[str, object]:
        state = self.runtime_state.get().public_dict()
        configured = state.get("configured") is True
        verified = state.get("verified") is True
        if not configured:
            connection = self._entry(
                "blocked", "ai_credential_required", "save_credential"
            )
        elif not verified:
            connection = self._entry(
                "verification_required", "ai_connection_verification_required", "test_connection"
            )
        else:
            connection = self._entry(
                "ready", "ai_connection_ready", "none", state.get("verified_until")
            )

        harness = self._harness_entry()
        businesses: dict[str, object] = {}
        for scope in sorted(AI_BUSINESS_SCOPES):
            if connection["state"] != "ready":
                businesses[scope] = dict(connection)
                continue
            if scope in _HARNESS_SCOPES and harness["state"] != "ready":
                businesses[scope] = dict(harness)
                continue
            expiry = self.runtime_state.business_verification(scope)
            businesses[scope] = (
                self._entry("ready", "ai_business_ready", "none", expiry)
                if expiry is not None
                else self._entry(
                    "verification_required",
                    "ai_business_verification_required",
                    "test_business_capability",
                )
            )
        return {
            "schema_version": AI_READINESS_SCHEMA_VERSION,
            "provider_id": state.get("provider_id"),
            "provider_connection": connection,
            "harness": harness,
            "businesses": businesses,
        }

    def _harness_entry(self) -> dict[str, object]:
        if self.harness is None:
            return self._entry(
                "unavailable", "harness_runtime_unavailable", "repair_harness_runtime"
            )
        try:
            raw = self.harness.readiness()
        except Exception:
            return self._entry(
                "unavailable", "harness_runtime_unavailable", "repair_harness_runtime"
            )
        if not isinstance(raw, Mapping):
            return self._entry(
                "unavailable", "harness_runtime_invalid", "repair_harness_runtime"
            )
        state = raw.get("state")
        reason = raw.get("reason_code")
        action = raw.get("next_action")
        if (
            state not in {"ready", "unavailable"}
            or not isinstance(reason, str)
            or not reason.startswith("harness_")
            or not isinstance(action, str)
            or not action
        ):
            return self._entry(
                "unavailable", "harness_runtime_invalid", "repair_harness_runtime"
            )
        return self._entry(str(state), reason, action)

    @staticmethod
    def _entry(
        state: str,
        reason_code: str,
        next_action: str,
        verified_until: object = None,
    ) -> dict[str, object]:
        return {
            "state": state,
            "reason_code": reason_code,
            "next_action": next_action,
            "verified_until": (
                verified_until
                if isinstance(verified_until, int) and not isinstance(verified_until, bool)
                else None
            ),
        }


__all__ = [
    "AI_READINESS_SCHEMA_VERSION",
    "AIReadinessService",
    "HarnessReadinessAuthority",
]
