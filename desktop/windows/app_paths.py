from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Mapping


APP_DIRECTORY_NAME = "Auto Research"
LOCAL_APP_DATA_ENV = "LOCALAPPDATA"


class WindowsPathError(RuntimeError):
    """Raised when the Windows per-user data root cannot be resolved safely."""


def _windows_absolute(value: str, *, label: str) -> PureWindowsPath:
    text = str(value or "").strip()
    candidate = PureWindowsPath(text)
    if not text or not candidate.is_absolute() or ".." in candidate.parts:
        raise WindowsPathError(f"{label} 必须是无上级跳转的 Windows 绝对路径")
    return candidate


@dataclass(frozen=True)
class WindowsAppPaths:
    """Separate application state, official content, and private user data.

    The values remain ``PureWindowsPath`` objects so the contract can be tested
    from macOS without pretending that a Windows path is a local POSIX path.
    ``materialize`` is intentionally Windows-only.
    """

    root: PureWindowsPath
    state: PureWindowsPath
    official_repositories: PureWindowsPath
    private_repository: PureWindowsPath
    private_uploads: PureWindowsPath
    package_inbox: PureWindowsPath
    package_staging: PureWindowsPath
    package_rollback: PureWindowsPath
    private_history: PureWindowsPath
    cache: PureWindowsPath
    logs: PureWindowsPath

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        override_root: str | None = None,
    ) -> "WindowsAppPaths":
        values = os.environ if environment is None else environment
        if override_root is not None:
            root = _windows_absolute(override_root, label="Windows 数据目录")
        else:
            local_app_data = _windows_absolute(
                values.get(LOCAL_APP_DATA_ENV, ""),
                label=f"环境变量 {LOCAL_APP_DATA_ENV}",
            )
            root = local_app_data / APP_DIRECTORY_NAME

        return cls(
            root=root,
            state=root / "State",
            official_repositories=root / "Repositories" / "Official",
            private_repository=root / "Repositories" / "Private",
            private_uploads=root / "Private Data" / "Uploads",
            package_inbox=root / "Packages" / "Inbox",
            package_staging=root / "Packages" / "Staging",
            package_rollback=root / "Packages" / "Rollback",
            private_history=root / "Private Data" / "History",
            cache=root / "Cache",
            logs=root / "Logs",
        )

    def all_directories(self) -> tuple[PureWindowsPath, ...]:
        return (
            self.root,
            self.state,
            self.official_repositories,
            self.private_repository,
            self.private_uploads,
            self.package_inbox,
            self.package_staging,
            self.package_rollback,
            self.private_history,
            self.cache,
            self.logs,
        )

    def materialize(self) -> None:
        if os.name != "nt":
            raise WindowsPathError("只能在 Windows 上创建 Windows 应用数据目录")
        for directory in self.all_directories():
            Path(directory).mkdir(parents=True, exist_ok=True)

    def assert_separated(self) -> None:
        official = self.official_repositories
        private_roots = (
            self.private_repository,
            self.private_uploads,
            self.private_history,
        )
        if any(official == private or official in private.parents or private in official.parents for private in private_roots):
            raise WindowsPathError("官方资料包与用户私人数据目录不能重叠")
        if self.package_staging == self.package_rollback:
            raise WindowsPathError("资料包暂存区与回退区不能共用目录")
