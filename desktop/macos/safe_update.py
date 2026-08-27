from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
import json
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


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
    source_path = PROJECT_ROOT / "db" / "experimental_evidence.sqlite"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = BACKUP_ROOT / f"experimental_evidence-before-desktop-update-{stamp}.sqlite"
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)

    source = sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
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

    run([sys.executable, "-m", "unittest", "discover", "-s", "desktop/macos/tests", "-p", "test_*.py"])
    source_environment = {
        **dict(os.environ),
        "PYTHONPATH": str(PROJECT_ROOT / "src"),
    }
    run(
        [sys.executable, "-m", "unittest", "discover", "-s", "src/tests", "-p", "test_*.py"],
        env=source_environment,
    )
    run([sys.executable, "-m", "compileall", "-q", "src", "desktop/macos"])
    for script_path in production_javascript_assets():
        run(["node", "--check", script_path])
    run(["git", "diff", "--check"])
    run(
        [sys.executable, "-m", "auto_research.cli", "evidence-db-health"],
        env=source_environment,
    )
    run([str(DESKTOP_ROOT / "build_app.command")])
    # build_app.command already performs the isolated candidate smoke check and
    # signature/plist validation. Do not repeat the same high-load verification.
    print("\n桌面版更新完成。上一版应用仍保存在 desktop/macos/releases/ 中。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
