from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from build_plan import WindowsBuildError, WindowsBuildPlan
from composition_root import WindowsCompositionError, WindowsCompositionRoot
from frozen_resources import application_resource


def _internal_app_version() -> str:
    try:
        plan = WindowsBuildPlan.load(
            application_resource("desktop", "windows", "version.json")
        )
        plan.validate_contract()
    except WindowsBuildError as exc:
        raise WindowsCompositionError("Windows internal version contract is invalid") from exc
    identity = plan.desktop_version.casefold()
    if not ("internal" in identity or "windows.rc" in identity) or plan.installer_ready:
        raise WindowsCompositionError("Windows launcher is not an unaccepted internal RC build")
    return plan.desktop_version


def _show_startup_error(message: str) -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            str(message),
            "Auto Research 无法启动",
            0x10,
        )
    except Exception:
        pass


def _emit_status(value: dict[str, object]) -> None:
    stream = getattr(sys, "stdout", None)
    if stream is None:
        return
    try:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")
        stream.flush()
    except Exception:
        pass


def _frozen_smoke_check() -> int:
    """Exercise frozen resources and native dependency imports without a UI."""

    _internal_app_version()
    WindowsCompositionRoot._load_release_contract()
    import fitz  # noqa: F401
    import clr  # noqa: F401
    import webview.platforms.edgechromium  # noqa: F401

    return 0


def main() -> int:
    """Launch the internal Windows composition; release gates remain separate."""

    if "--frozen-smoke" in sys.argv[1:]:
        try:
            return _frozen_smoke_check()
        except Exception:
            return 4
    try:
        root = WindowsCompositionRoot.from_environment(
            current_app_version=_internal_app_version(),
        )
        root.launch()
    except WindowsCompositionError as exc:
        _show_startup_error(str(exc))
        _emit_status(
            {
                "ok": False,
                "code": "windows_composition_unavailable",
                "message": str(exc),
            }
        )
        return 2
    except Exception:
        _show_startup_error("Windows 运行组件未能安全启动，请保留安装日志并联系维护者。")
        _emit_status(
            {
                "ok": False,
                "code": "windows_startup_failed",
                "message": "Windows 内部预览未能安全启动。",
            }
        )
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
