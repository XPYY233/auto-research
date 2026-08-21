from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from build_plan import WindowsBuildPlan
from v1_package_contract import verify_windows_v1_official_package
from auto_research.release_contract import load_release_contract, verify_web_asset_hashes


class WindowsBuildInputError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_build_inputs(project_root: Path, package_path: Path) -> dict[str, object]:
    root = project_root.resolve(strict=True)
    windows = root / "desktop" / "windows"
    build_contract = json.loads(
        (windows / "build-contract-v1.json").read_text(encoding="utf-8")
    )
    if build_contract.get("contract") != "auto-research-windows-build-v1":
        raise WindowsBuildInputError("Windows 构建契约无效")

    plan = WindowsBuildPlan.load(windows / "version.json")
    plan.validate_contract()
    if plan.desktop_version != build_contract["candidate_version"]:
        raise WindowsBuildInputError("Windows RC 版本身份不一致")
    if plan.installer_ready:
        raise WindowsBuildInputError("Win11 验收前 installer_ready 必须为 false")

    release = load_release_contract(root / "config" / "release-contract.json")
    if (
        release.value.get("core_version") != build_contract["shared_core_version"]
        or release.official_package_version != "1.0.0"
    ):
        raise WindowsBuildInputError("共享 v1 发布契约不一致")
    verify_web_asset_hashes(release, root)

    for relative, expected in build_contract["locked_files"].items():
        path = windows / relative
        if not path.is_file() or _sha256(path) != expected:
            raise WindowsBuildInputError(f"Windows 锁定构建文件已变化：{relative}")

    package_report = verify_windows_v1_official_package(package_path)
    return {
        "ok": True,
        "candidate_version": plan.desktop_version,
        "shared_source_commit": build_contract["shared_source_commit"],
        "shared_core_version": build_contract["shared_core_version"],
        "web_assets_verified": True,
        "package_sha256": package_report.sha256,
        "package_version": package_report.package_version,
        "package_documents": package_report.document_count,
        "package_remake_required": package_report.remake_required,
        "installer_ready": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = verify_build_inputs(args.project_root, args.package)
    except Exception as exc:
        print(json.dumps({"ok": False, "code": "build_inputs_invalid", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
