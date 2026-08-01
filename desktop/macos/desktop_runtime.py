from __future__ import annotations

import importlib
import json
import os
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import fcntl
from dataclasses import dataclass
from pathlib import Path
from typing import Any


APP_NAME = "Auto Research"
EXPECTED_EVIDENCE_SCHEMA = 12
PROJECT_ROOT_ENV = "AUTO_RESEARCH_DESKTOP_PROJECT_ROOT"
PREFERENCE_FILE = Path.home() / "Library" / "Application Support" / APP_NAME / "project-root.txt"
LOCK_FILE = Path.home() / "Library" / "Application Support" / APP_NAME / "desktop.lock"


class ProjectRootError(RuntimeError):
    """Raised when the desktop shell cannot find a usable evidence workspace."""


class InstanceAlreadyRunningError(RuntimeError):
    """Raised when another editable desktop instance owns the workspace."""


@dataclass(frozen=True)
class ProjectLocation:
    root: Path
    source: str


def _development_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def missing_project_markers(root: Path) -> list[str]:
    required = (
        Path("db/experimental_evidence.sqlite"),
        Path("data/evidence"),
        Path("config"),
    )
    return [str(relative) for relative in required if not (root / relative).exists()]


def is_project_root(root: Path) -> bool:
    return root.is_dir() and not missing_project_markers(root)


def _read_preference() -> Path | None:
    try:
        value = PREFERENCE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(value).expanduser() if value else None


def discover_project_root(explicit: str | Path | None = None) -> ProjectLocation:
    if explicit is not None:
        root = Path(explicit).expanduser().resolve()
        missing = missing_project_markers(root)
        if missing:
            raise ProjectRootError(
                f"指定的项目目录不可用：{root}\n缺少：{', '.join(missing)}"
            )
        return ProjectLocation(root=root, source="command-line")

    candidates: list[tuple[str, Path | None]] = [
        ("environment", Path(os.environ[PROJECT_ROOT_ENV]).expanduser() if os.environ.get(PROJECT_ROOT_ENV) else None),
        ("preference", _read_preference()),
    ]
    if not getattr(sys, "frozen", False):
        candidates.append(("development-checkout", _development_project_root()))
    candidates.append(("current-mac-default", Path.home() / "Zotero" / "auto-research"))

    attempted: list[str] = []
    seen: set[Path] = set()
    for source, candidate in candidates:
        if candidate is None:
            continue
        root = candidate.expanduser().resolve()
        if root in seen:
            continue
        seen.add(root)
        if is_project_root(root):
            return ProjectLocation(root=root, source=source)
        missing = ", ".join(missing_project_markers(root)) or "目录不存在"
        attempted.append(f"{root}（缺少 {missing}）")

    details = "\n".join(f"- {item}" for item in attempted) or "- 没有可检查的候选目录"
    raise ProjectRootError(
        "没有找到可用的 Auto Research 数据工作区。\n"
        "第一阶段桌面版仍使用当前项目中的数据库和证据资产。\n"
        f"已检查：\n{details}"
    )


def configure_core_paths(project_root: Path) -> None:
    """Point the frozen core at the external project data before importing evidence modules."""

    already_loaded = sorted(
        name
        for name in sys.modules
        if name.startswith("auto_research.") and name != "auto_research.paths"
    )
    if already_loaded:
        raise RuntimeError(
            "桌面运行路径必须在核心模块导入前配置；已提前载入："
            + ", ".join(already_loaded[:5])
        )

    paths = importlib.import_module("auto_research.paths")
    root = project_root.expanduser().resolve()
    paths.ROOT = root
    paths.CONFIG_DIR = root / "config"
    paths.DATA_DIR = root / "data"
    paths.PDF_DIR = paths.DATA_DIR / "pdf"
    paths.PAPERS_DIR = paths.DATA_DIR / "papers"
    paths.REPORTS_DIR = paths.DATA_DIR / "reports"
    paths.MATRIX_DIR = paths.DATA_DIR / "matrix"
    paths.DB_DIR = root / "db"
    paths.DB_PATH = paths.DB_DIR / "research.sqlite"

    os.environ[PROJECT_ROOT_ENV] = str(root)
    os.environ["AUTO_RESEARCH_DESKTOP"] = "1"
    os.chdir(root)


def configure_imported_module_paths(project_root: Path) -> None:
    """Bridge remaining dynamic __file__ lookups during the frozen Mac preview."""

    root = project_root.expanduser().resolve()
    quality_pipeline = sys.modules.get("auto_research.evidence.quality_pipeline")
    if quality_pipeline is not None:
        quality_pipeline.__file__ = str(
            root / "src" / "auto_research" / "evidence" / "quality_pipeline.py"
        )


def acquire_instance_lock(path: Path = LOCK_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise InstanceAlreadyRunningError(
            "Auto Research 已经在运行。为了保护同一个证据数据库，不能同时打开两个编辑实例。"
        ) from exc
    return handle


def legacy_editor_is_running(url: str = "http://127.0.0.1:8765") -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/ui-mode", timeout=0.8) as response:
            payload = json.load(response)
    except (OSError, ValueError, urllib.error.URLError):
        return False
    return payload.get("read_only") is False


def find_available_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def wait_for_ui(
    url: str,
    timeout_seconds: float = 90.0,
    *,
    bootstrap_token: str | None = None,
) -> dict[str, Any]:
    """Wait for the narrow desktop health endpoint without consuming bootstrap."""

    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    health_url = f"{url}/api/desktop/healthz"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=1.5) as response:
                if response.status == 204 and response.read(1) == b"":
                    return {"read_only": False}
                last_error = "桌面健康端点响应无效"
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(0.2)
    raise TimeoutError(f"桌面服务未在 {timeout_seconds:.0f} 秒内就绪：{last_error}")


def smoke_check_project(project_root: Path) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    missing = missing_project_markers(root)
    if missing:
        raise ProjectRootError(f"项目目录缺少：{', '.join(missing)}")

    database = root / "db" / "experimental_evidence.sqlite"
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        paper_count = int(connection.execute("SELECT COUNT(*) FROM papers").fetchone()[0])
        schema_row = connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
    finally:
        connection.close()

    return {
        "ok": integrity == "ok" and str(schema_row[0] if schema_row else "") == str(EXPECTED_EVIDENCE_SCHEMA),
        "project_root": str(root),
        "database": str(database),
        "sqlite_integrity": integrity,
        "schema_version": schema_row[0] if schema_row else None,
        "papers": paper_count,
        "data_mode": "external-project-preview",
    }
