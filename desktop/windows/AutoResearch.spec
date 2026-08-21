from pathlib import Path
import json
import os


project_root = Path(os.environ["AUTO_RESEARCH_WINDOWS_BUILD_ROOT"]).resolve()
windows_root = project_root / "desktop" / "windows"
web_root = project_root / "src" / "auto_research" / "evidence" / "web"
release = json.loads((project_root / "config" / "release-contract.json").read_text(encoding="utf-8"))

analysis = Analysis(
    [str(windows_root / "launcher.py")],
    pathex=[str(project_root / "src"), str(windows_root)],
    binaries=[],
    datas=[
        *[
            (str(project_root / relative), "auto_research/evidence/web")
            for relative in release["web_assets"]
        ],
        (str(project_root / "config" / "release-contract.json"), "config"),
        (str(windows_root / "version.json"), "desktop/windows"),
        (str(windows_root / "official-package-v1.json"), "desktop/windows"),
    ],
    hiddenimports=["webview.platforms.edgechromium"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["playwright", "pytesseract"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(analysis.pure)
exe = EXE(
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
)
collect = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Auto Research",
)
