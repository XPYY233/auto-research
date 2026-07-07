from __future__ import annotations

import webbrowser
from dataclasses import dataclass
from pathlib import Path


@dataclass
class HumanHandoff:
    paper_id: int
    title: str
    url: str | None
    reason: str

    def message(self) -> str:
        return (
            f"Paper #{self.paper_id} requires human handoff: {self.reason}\n"
            f"Title: {self.title}\n"
            f"URL: {self.url or 'N/A'}\n"
            "After you download the PDF legally, run:\n"
            f"  auto-research attach-pdf {self.paper_id} /absolute/path/to/file.pdf"
        )


def open_for_handoff(url: str) -> None:
    webbrowser.open(url)
