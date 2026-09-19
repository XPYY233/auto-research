from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
import json
import tempfile
from contextlib import closing
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from desktop_runtime import discover_project_root
from auto_research.workspace import validate_workspace


DESKTOP_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = DESKTOP_ROOT.parents[1]
BACKUP_ROOT = PROJECT_ROOT.parent / "auto-research-backups"
RELEASE_CONTRACT_PATH = PROJECT_ROOT / "config" / "release-contract.json"


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print(f"\n→ {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, env=env, check=True)


def production_javascript_assets(
    contract_path: Path = RELEASE_CONTRACT_PATH,
) -> tuple[str, ...]:
    """Return the JavaScript assets that the signed release contract publishes."""

    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if contract.get("schema") != "auto-research-release-contract-v1":
        raise ValueError("发布契约格式不受支持")
    web_assets = contract.get("web_assets")
    if not isinstance(web_assets, dict):
        raise ValueError("发布契约缺少 web_assets")

    scripts: list[str] = []
    web_root = (PROJECT_ROOT / "src" / "auto_research" / "evidence" / "web").resolve()
    for relative_path in web_assets:
        if not isinstance(relative_path, str) or not relative_path.endswith(".js"):
            continue
        candidate = (PROJECT_ROOT / relative_path).resolve()
        if candidate.parent != web_root or not candidate.is_file():
            raise ValueError("发布契约包含无效的 JavaScript 资源")
        scripts.append(relative_path)
    if not scripts:
        raise ValueError("发布契约没有可检查的 JavaScript 资源")
    return tuple(scripts)


def git_status() -> str:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


def legacy_editor_is_running() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/api/ui-mode", timeout=0.8) as response:
            return json.load(response).get("read_only") is False
    except (OSError, ValueError, urllib.error.URLError):
        return False


def backup_database() -> tuple[Path, str]:
    workspace = discover_project_root().root
    validate_workspace(workspace)
    source_path = workspace / "db" / "experimental_evidence.sqlite"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f"experimental_evidence-before-desktop-update-{stamp}-",
        suffix=".partial", dir=BACKUP_ROOT,
    )
    os.close(fd)
    staged = Path(temporary)
    destination = staged.with_suffix(".sqlite")
    try:
        with closing(sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(staged)) as target:
                source.backup(target)
                if target.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise RuntimeError("更新前数据库备份完整性校验未通过；未开始构建。")
        digest = hashlib.sha256(staged.read_bytes()).hexdigest()
        with staged.open("rb") as stream:
            os.fsync(stream.fileno())
        # Publish only a verified copy; never replace an earlier backup.
        os.link(staged, destination)
    finally:
        staged.unlink(missing_ok=True)
    return destination, digest


def main() -> int:
    if legacy_editor_is_running():
        print(
            "已经退役的浏览器编辑服务仍在运行。为了获得一致的数据库备份和单一写入入口，"
            "桌面更新已经停止。\n请先关闭旧服务；如果另一个 Codex 正在测试，请等它完成。"
        )
        return 4
    dirty = git_status()
    if dirty:
        print(
            "项目还有未完成或未提交的改动。为了避免把两个 Codex 对话混进同一个应用，"
            "本次更新已经停止。\n\n当前文件：\n" + dirty
        )
        print("\n请先让正在工作的 Codex 完成测试和提交，再重新双击更新入口。")
        return 3

    backup, digest = backup_database()
    print(f"已创建更新前数据库备份：{backup}")
    print(f"SHA-256：{digest}")

    # The same gate is used locally and in PR CI; pytest includes function tests.
    run([sys.executable, str(PROJECT_ROOT / "scripts" / "check.py")])
    run([str(DESKTOP_ROOT / "build_app.command")])
    # build_app.command already performs the isolated candidate smoke check and
    # signature/plist validation. Do not repeat the same high-load verification.
    print("\n候选应用已生成；尚未替换已安装的 App。请完成候选验收后再安装。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
