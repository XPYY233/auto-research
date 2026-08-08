from __future__ import annotations

from typing import Any, Mapping, Protocol


class LibrarianV3Runtime(Protocol):
    def run(
        self,
        question: str,
        *,
        history: Any = None,
        research_state: Any = None,
        state_token: Any = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]: ...


class LibrarianBridgeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

    def public_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


class LibrarianV3BridgeAdapter:
    """Forward the frozen V3 request and response without interpreting either."""

    def __init__(self, runtime: LibrarianV3Runtime | None) -> None:
        self.runtime = runtime

    @property
    def available(self) -> bool:
        return self.runtime is not None and callable(getattr(self.runtime, "run", None))

    def chat(
        self,
        question: str,
        *,
        history: Any = None,
        research_state: Any = None,
        state_token: Any = None,
        conversation_id: str | None = None,
    ) -> Mapping[str, Any]:
        if not self.available:
            raise LibrarianBridgeError(
                "librarian_unavailable", "图书管理员服务尚未连接。"
            )
        assert self.runtime is not None
        return self.runtime.run(
            question,
            history=history,
            research_state=research_state,
            state_token=state_token,
            conversation_id=conversation_id,
        )
