#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = PROJECT_ROOT / "config" / "release-contract.json"
MACOS_VERSION_PATH = PROJECT_ROOT / "desktop" / "macos" / "version.json"
WINDOWS_VERSION_PATH = PROJECT_ROOT / "desktop" / "windows" / "version.json"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON对象无效：{path.name}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def synchronize(*, write: bool) -> list[str]:
    contract = _read_json(CONTRACT_PATH)
    changed: list[str] = []
    for path, platform in (
        (MACOS_VERSION_PATH, "macos"),
        (WINDOWS_VERSION_PATH, "windows"),
    ):
        current = _read_json(path)
        expected = contract["desktop"][platform]
        merged = dict(current)
        for key, value in expected.items():
            merged[key] = value
        if merged != current:
            changed.append(path.relative_to(PROJECT_ROOT).as_posix())
            if write:
                _write_json(path, merged)

    assets = contract["web_assets"]
    actual = {}
    for relative in assets:
        path = PROJECT_ROOT / relative
        actual[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != assets:
        changed.append("config/release-contract.json:web_assets")
        if write:
            contract["web_assets"] = actual
            _write_json(CONTRACT_PATH, contract)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="同步或校验 Auto Research 发布契约")
    parser.add_argument("--write", action="store_true", help="把契约写入平台版本镜像并更新前端哈希")
    arguments = parser.parse_args()
    changed = synchronize(write=arguments.write)
    if changed and not arguments.write:
        print("发布契约尚未同步：" + ", ".join(changed))
        return 1
    if arguments.write:
        print("发布契约已同步" if changed else "发布契约无需更新")
    else:
        print("发布契约一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
