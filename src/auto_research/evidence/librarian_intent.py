from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


INTENT_KINDS = {
    "system_capability",
    "research_lookup",
    "research_review",
    "followup_ref",
    "followup_bundle",
    "clarification",
    "conversation",
}

RETRIEVAL_POLICIES = {"none", "resolve_anchors", "focused", "review_map"}

_REF_PATTERN = re.compile(r"(?<![A-Za-z0-9])R(\d{1,4})(?![A-Za-z0-9])", re.IGNORECASE)
_BUNDLE_PATTERN = re.compile(r"(?<![A-Za-z0-9])B(\d{1,4})(?![A-Za-z0-9])", re.IGNORECASE)
_SYSTEM_PATTERNS = (
    re.compile(r"(?:你|系统|软件).{0,12}(?:用|使用|基于|调用).{0,8}(?:什么|哪个).{0,4}(?:AI|模型)", re.I),
    re.compile(r"(?:什么|哪个).{0,4}(?:AI|大模型|模型).{0,8}(?:回答|驱动|支持)", re.I),
    re.compile(r"(?:你是谁|你能做什么|系统能力|软件能力|隐私|数据会上传|是否联网|模型版本)", re.I),
)
_CONVERSATION_PATTERN = re.compile(
    r"^(?:你好|您好|嗨|谢谢|感谢|再见|好的|明白了|收到)[！!。,.，\s]*$",
    re.I,
)
_REVIEW_PATTERN = re.compile(
    r"(?:综述|概述|总体|全景|研究进展|研究现状|主要方向|有哪些用途|有何用途|用来做什么|"
    r"应用(?:于|在|场景)|常见方法|主要机制|如何用于|起什么作用)",
    re.I,
)


@dataclass(frozen=True)
class IntentDecision:
    kind: str
    retrieval_policy: str
    confidence: float
    reason_code: str
    anchor_refs: tuple[str, ...] = ()
    bundle_refs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "intent-decision-v1",
            "kind": self.kind,
            "retrieval_policy": self.retrieval_policy,
            "confidence": round(float(self.confidence), 3),
            "reason_code": self.reason_code,
            "anchor_refs": list(self.anchor_refs),
            "bundle_refs": list(self.bundle_refs),
        }


def route_librarian_intent(question: str) -> IntentDecision:
    """Route locally before retrieval or model use.

    The router deliberately favors high-precision system and anchor routes.
    Ambiguous scientific wording remains a research lookup and can later be
    upgraded to ``clarification`` by deterministic constraint analysis.
    """

    prompt = " ".join(str(question or "").split()).strip()
    refs = tuple(f"R{value}" for value in _REF_PATTERN.findall(prompt))
    bundles = tuple(f"B{value}" for value in _BUNDLE_PATTERN.findall(prompt))
    if refs:
        return IntentDecision(
            "followup_ref", "resolve_anchors", 1.0, "explicit_evidence_ref", refs, ()
        )
    if bundles:
        return IntentDecision(
            "followup_bundle", "resolve_anchors", 1.0, "explicit_bundle_ref", (), bundles
        )
    if any(pattern.search(prompt) for pattern in _SYSTEM_PATTERNS):
        return IntentDecision(
            "system_capability", "none", 0.99, "local_system_capability_pattern"
        )
    if _CONVERSATION_PATTERN.fullmatch(prompt):
        return IntentDecision("conversation", "none", 0.99, "local_conversation_pattern")
    if _REVIEW_PATTERN.search(prompt):
        return IntentDecision("research_review", "review_map", 0.9, "local_review_pattern")
    return IntentDecision("research_lookup", "focused", 0.7, "default_research_route")


def clarification_intent(reason_code: str = "local_critical_ambiguity") -> IntentDecision:
    return IntentDecision("clarification", "none", 1.0, reason_code)
