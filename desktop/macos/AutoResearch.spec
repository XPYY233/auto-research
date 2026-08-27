from pathlib import Path
import json
import os

from PyInstaller.utils.hooks import collect_data_files, copy_metadata


project_root = Path(os.environ["AUTO_RESEARCH_DESKTOP_BUILD_ROOT"]).resolve()
desktop_root = project_root / "desktop" / "macos"
version = json.loads((desktop_root / "version.json").read_text(encoding="utf-8"))

analysis = Analysis(
    [str(desktop_root / "launcher.py")],
    pathex=[str(project_root / "src"), str(desktop_root)],
    binaries=[],
    datas=[
        *[
            (
                str(project_root / "src" / "auto_research" / "evidence" / "web" / name),
                "auto_research/evidence/web",
            )
            for name in (
                "index.html",
                "app.css",
                "workbench.css",
                "ai_consent.js",
                "document_tab_store.js",
                "pane_layout_controller.js",
                "workspace_layout_controller.js",
                "fusion_pdf_controller.js",
                "fusion_ai_experience.js",
                "fusion_package_center.js",
                "fusion_personal_import.js",
                "fusion_review.js",
                "codex-pet-working.webp",
            )
        ],
        (
            str(desktop_root / "version.json"),
            "desktop/macos",
        ),
        (
            str(project_root / "config" / "auto-research-harness.runtime.cordis.yml"),
            "config",
        ),
        *collect_data_files("deepseek_harness_runtime"),
        *copy_metadata("deepseek-harness-sdk"),
        *copy_metadata("deepseek-harness-runtime-bin"),
        *copy_metadata("pydantic"),
        *copy_metadata("pyarrow"),
    ],
    hiddenimports=[
        "webview.platforms.cocoa",
        "Security",
        "deepseek_harness",
        "deepseek_harness_runtime",
        "pyarrow",
        "pyarrow.parquet",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["playwright", "pytesseract"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Auto Research",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
)

collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Auto Research",
)

app = BUNDLE(
    collection,
    name="Auto Research.app",
    icon=str(desktop_root / "assets" / "AutoResearch.icns"),
    bundle_identifier="com.researcher.autoresearch",
    version=version["bundle_short_version"],
    info_plist={
        "CFBundleDisplayName": "Auto Research",
        "CFBundleShortVersionString": version["bundle_short_version"],
        "CFBundleVersion": version["build_number"],
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "Auto Research macOS application",
    },
)
