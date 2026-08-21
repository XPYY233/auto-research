from __future__ import annotations

import json
from pathlib import Path

from build_plan import WindowsBuildError, WindowsBuildPlan
from composition_root import WindowsCompositionError, WindowsCompositionRoot


def _internal_app_version() -> str:
    try:
        plan = WindowsBuildPlan.load(Path(__file__).with_name("version.json"))
        plan.validate_contract()
    except WindowsBuildError as exc:
        raise WindowsCompositionError("Windows internal version contract is invalid") from exc
    identity = plan.desktop_version.casefold()
    if not ("internal" in identity or "windows.rc" in identity) or plan.installer_ready:
        raise WindowsCompositionError("Windows launcher is not an unaccepted internal RC build")
    return plan.desktop_version


def main() -> int:
    """Launch the internal Windows composition; release gates remain separate."""

    try:
        root = WindowsCompositionRoot.from_environment(
            current_app_version=_internal_app_version(),
        )
        root.launch()
    except WindowsCompositionError as exc:
        print(json.dumps({"ok": False, "code": "windows_composition_unavailable", "message": str(exc)}, ensure_ascii=False))
        return 2
    except Exception:
        print(
            json.dumps(
                {
                    "ok": False,
                    "code": "windows_startup_failed",
                    "message": "Windows 内部预览未能安全启动。",
                },
                ensure_ascii=False,
            )
        )
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
