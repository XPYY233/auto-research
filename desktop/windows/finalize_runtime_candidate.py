from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime_bundle import (
    REQUIRED_COMPONENTS,
    create_runtime_manifest,
    verify_runtime_bundle,
    write_runtime_manifest,
)


FORBIDDEN_FROZEN_MODULES = (
    "auto_research.product.internal_preview_builder",
    "auto_research.product.evidence_v12_export",
)
REQUIRED_NATIVE_RUNTIME_FILES = (
    "Microsoft.Web.WebView2.Core.dll",
    "Microsoft.Web.WebView2.WinForms.dll",
    "WebView2Loader.dll",
    "Python.Runtime.dll",
    "_mupdf.pyd",
)


def finalize_candidate(
    candidate_root: Path,
    *,
    desktop_version: str,
    python_version: str,
    dependency_audit: Path,
    analysis_toc: Path,
) -> dict[str, object]:
    root = candidate_root.resolve(strict=True)
    audit_text = dependency_audit.read_text(encoding="utf-8")
    toc_text = analysis_toc.read_text(encoding="utf-8", errors="replace")
    forbidden = [name for name in FORBIDDEN_FROZEN_MODULES if name in toc_text]
    if forbidden:
        raise RuntimeError("Windows 冻结包混入禁止的维护模块")
    if "webview.platforms.edgechromium" not in toc_text:
        raise RuntimeError("Windows 冻结包缺少 Edge Chromium 后端")

    bundled_names = {
        path.name
        for path in root.rglob("*")
        if path.is_file()
    }
    missing_native = sorted(set(REQUIRED_NATIVE_RUNTIME_FILES) - bundled_names)
    if missing_native:
        raise RuntimeError("Windows 冻结包缺少原生运行组件")

    asset_root = root / "_internal" / "auto_research" / "evidence" / "web"
    required_assets = {"index.html", "app.css", "workbench.css", "ai_consent.js", "fusion_review.js"}
    if not required_assets.issubset({path.name for path in asset_root.glob("*") if path.is_file()}):
        raise RuntimeError("Windows 冻结包缺少共享 Fusion 资源")

    marker_root = root / "_internal" / "auto-research-runtime-contract"
    marker_root.mkdir(parents=True, exist_ok=False)
    component_details = {
        "python-runtime": f"python={python_version}\n",
        "python-stdlib": "bundled-by=pyinstaller\n",
        "sqlite-runtime": "sqlite=python-stdlib-extension\n",
        "cryptography": audit_text,
        "pymupdf": audit_text,
        "pywebview": audit_text,
        "webview2-loader": "backend=edgechromium\n",
        "auto-research-core": f"desktop={desktop_version}\nforbidden_modules=absent\n",
        "shared-web-assets": "\n".join(sorted(required_assets)) + "\n",
    }
    components: dict[str, str] = {}
    for name in sorted(REQUIRED_COMPONENTS):
        marker = marker_root / f"{name}.txt"
        marker.write_text(component_details[name], encoding="utf-8")
        components[name] = marker.relative_to(root).as_posix()
    manifest = create_runtime_manifest(
        root,
        desktop_version=desktop_version,
        python_version=python_version,
        components=components,
    )
    write_runtime_manifest(root, manifest)
    report = verify_runtime_bundle(root)
    return {
        "desktop_version": report.desktop_version,
        "python_version": report.python_version,
        "architecture": report.architecture,
        "component_count": report.component_count,
        "forbidden_modules_absent": True,
        "native_runtime_present": True,
        "fusion_assets_present": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--desktop-version", required=True)
    parser.add_argument("--python-version", required=True)
    parser.add_argument("--dependency-audit", type=Path, required=True)
    parser.add_argument("--analysis-toc", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = finalize_candidate(
            args.candidate_root,
            desktop_version=args.desktop_version,
            python_version=args.python_version,
            dependency_audit=args.dependency_audit,
            analysis_toc=args.analysis_toc,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "code": "runtime_bundle_invalid", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, **report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
