from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CapabilityManifest:
    provider: str
    planning_model: str
    synthesis_model: str
    evidence_types: tuple[str, ...] = ("item", "finding", "table", "figure")

    @classmethod
    def from_client(cls, client: Any) -> "CapabilityManifest":
        settings = getattr(client, "settings", None)
        return cls(
            provider="DeepSeek",
            planning_model=str(getattr(settings, "librarian_planning_model", "deepseek-v4-flash")),
            synthesis_model=str(getattr(settings, "librarian_synthesis_model", "deepseek-v4-pro")),
        )

    def answer(self) -> str:
        return (
            f"图书管理员的运行时 AI 是 {self.provider}：{self.planning_model} 负责有界查询规划，"
            f"{self.synthesis_model} 负责基于已召回公开证据的中文综合。材料、实验条件、证据分级、"
            "R# 引用、同一证据组定量比较门和隐私字段过滤由本地确定性程序负责。"
            "系统问题不会检索论文，也不会调用模型；模型不可用时，精确检索、引用解析和确定性报告仍可工作。"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "capability-manifest-v1",
            "runtime_ai_provider": self.provider,
            "planning_model": self.planning_model,
            "synthesis_model": self.synthesis_model,
            "evidence_types": list(self.evidence_types),
            "model_reads_visual_pixels": False,
            "librarian_is_read_only": True,
        }
