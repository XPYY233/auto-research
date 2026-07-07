from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .paths import CONFIG_DIR


@dataclass(frozen=True)
class Settings:
    themes: dict[str, Any]
    sources: dict[str, Any]


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_settings() -> Settings:
    themes = load_yaml(CONFIG_DIR / "themes.yaml")
    sources = load_yaml(CONFIG_DIR / "sources.yaml")
    return Settings(themes=themes, sources=sources)
