from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass


WINDOWS_10_22H2_BUILD = 19045
WINDOWS_11_MINIMUM_BUILD = 22000
X64_ARCHITECTURES = frozenset({"amd64", "x86_64"})


class WindowsCompatibilityError(RuntimeError):
    """Raised when the operating system is outside the Windows release policy."""


@dataclass(frozen=True)
class WindowsCompatibility:
    product_name: str
    build: int
    architecture: str
    support_level: str
    stable_release_supported: bool
    webview2_compatible: bool
    warning: str


def evaluate_windows_compatibility(
    *,
    major: int,
    build: int,
    architecture: str,
) -> WindowsCompatibility:
    normalized_architecture = str(architecture).casefold()
    if normalized_architecture not in X64_ARCHITECTURES:
        raise WindowsCompatibilityError("Auto Research 首版只支持 Windows x64")
    if int(major) != 10:
        raise WindowsCompatibilityError("Auto Research 需要 Windows 11 或 Windows 10 22H2")
    if int(build) >= WINDOWS_11_MINIMUM_BUILD:
        return WindowsCompatibility(
            product_name="Windows 11",
            build=int(build),
            architecture="x64",
            support_level="primary",
            stable_release_supported=True,
            webview2_compatible=True,
            warning="",
        )
    if int(build) >= WINDOWS_10_22H2_BUILD:
        return WindowsCompatibility(
            product_name="Windows 10 22H2",
            build=int(build),
            architecture="x64",
            support_level="legacy-compatible",
            stable_release_supported=False,
            webview2_compatible=True,
            warning=(
                "Windows 10 已结束常规支持。应用可继续做兼容测试，但正式稳定版以 Windows 11 为准。"
            ),
        )
    raise WindowsCompatibilityError("Windows 版本过旧；最低兼容测试版本为 Windows 10 22H2")


def detect_windows_compatibility() -> WindowsCompatibility:
    if os.name != "nt" or not hasattr(sys, "getwindowsversion"):
        raise WindowsCompatibilityError("Windows 兼容检查只能在 Windows 实机运行")
    version = sys.getwindowsversion()
    return evaluate_windows_compatibility(
        major=int(version.major),
        build=int(version.build),
        architecture=platform.machine(),
    )
