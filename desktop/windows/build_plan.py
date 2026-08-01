from __future__ import annotations

import argparse
import json
import os
import platform
from dataclasses import dataclass
from pathlib import Path


class WindowsBuildError(RuntimeError):
    """Raised when a Windows candidate build would be premature or unsafe."""


@dataclass(frozen=True)
class WindowsBuildPlan:
    desktop_version: str
    target: str
    distribution_schema: int
    bundle_contract_version: int
    installer_ready: bool
    bundled_python_runtime: bool
    requires_user_environment_setup: bool
    first_run_entry: str
    python_target: str = "3.12.x x64"
    shell: str = "pywebview EdgeChromium"
    freezer: str = "PyInstaller"
    installer: str = "Inno Setup"

    @classmethod
    def load(cls, version_file: Path) -> "WindowsBuildPlan":
        try:
            value = json.loads(version_file.read_text(encoding="utf-8"))
            return cls(
                desktop_version=str(value["desktop_version"]),
                target=str(value["target"]),
                distribution_schema=int(value["distribution_schema"]),
                bundle_contract_version=int(value["bundle_contract_version"]),
                installer_ready=value["installer_ready"] is True,
                bundled_python_runtime=value["bundled_python_runtime"] is True,
                requires_user_environment_setup=value["requires_user_environment_setup"] is True,
                first_run_entry=str(value["first_run_entry"]),
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise WindowsBuildError("Windows version.json 无效") from exc

    def validate_contract(self) -> None:
        if not self.desktop_version or self.distribution_schema != 1:
            raise WindowsBuildError("Windows 构建契约尚未锁定 distribution schema v1")
        if self.bundle_contract_version != 1:
            raise WindowsBuildError("Windows 内置运行时清单契约必须为 v1")
        if "Windows" not in self.target or "x64" not in self.target:
            raise WindowsBuildError("Windows 构建目标必须明确为 Windows x64")
        if not self.bundled_python_runtime or self.requires_user_environment_setup:
            raise WindowsBuildError("Windows 安装包必须自带运行时且禁止要求用户配置环境")
        if self.first_run_entry != "import-evidence-package":
            raise WindowsBuildError("Windows 首次启动必须直接进入资料包导入")

    def require_candidate_build_ready(self) -> None:
        self.validate_contract()
        if os.name != "nt" or platform.machine().lower() not in {"amd64", "x86_64"}:
            raise WindowsBuildError("候选程序只能在 Windows x64 构建机上生成")
        if not self.installer_ready:
            raise WindowsBuildError("共享接口尚未冻结，version.json 禁止生成安装包")

    def as_dict(self) -> dict[str, object]:
        return {
            "desktop_version": self.desktop_version,
            "target": self.target,
            "distribution_schema": self.distribution_schema,
            "bundle_contract_version": self.bundle_contract_version,
            "installer_ready": self.installer_ready,
            "bundled_python_runtime": self.bundled_python_runtime,
            "requires_user_environment_setup": self.requires_user_environment_setup,
            "first_run_entry": self.first_run_entry,
            "python_target": self.python_target,
            "shell": self.shell,
            "freezer": self.freezer,
            "installer": self.installer,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the Windows desktop build contract")
    parser.add_argument("--version-file", type=Path, default=Path(__file__).with_name("version.json"))
    parser.add_argument("--candidate", action="store_true", help="Require a real Windows candidate build")
    args = parser.parse_args(argv)
    try:
        plan = WindowsBuildPlan.load(args.version_file)
        if args.candidate:
            plan.require_candidate_build_ready()
        else:
            plan.validate_contract()
    except WindowsBuildError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, **plan.as_dict()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
