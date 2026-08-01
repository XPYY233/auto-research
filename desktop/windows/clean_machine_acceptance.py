from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from runtime_bundle import RuntimeBundleReport, verify_runtime_bundle


FORBIDDEN_COMMAND_NAMES = frozenset({"git", "node", "npm", "python", "python3", "py"})
FORBIDDEN_ENV_PREFIXES = ("AUTO_RESEARCH_", "PYTHON", "NODE_", "NPM_")


class CleanMachineAcceptanceError(RuntimeError):
    """Raised when a candidate is not usable by a non-technical Windows user."""


class CandidateRunner(Protocol):
    def run(
        self,
        executable: Path,
        *,
        cwd: Path,
        environment: Mapping[str, str],
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class CleanMachineReport:
    runtime: RuntimeBundleReport
    used_empty_working_directory: bool
    used_clean_environment: bool
    first_run_entry: str


def clean_environment(*, local_app_data: Path, temporary: Path) -> dict[str, str]:
    """Return only OS/user locations a packaged app may legitimately consume."""
    return {
        "LOCALAPPDATA": str(local_app_data),
        "TEMP": str(temporary),
        "TMP": str(temporary),
        # An empty PATH proves that direct executable launch does not discover
        # Python, Git, Node, or project tools from the test machine.
        "PATH": "",
    }


def _validate_runtime_report(value: Mapping[str, object], local_app_data: Path) -> str:
    if value.get("bundled_python") is not True:
        raise CleanMachineAcceptanceError("候选程序没有使用内置 Python")
    if value.get("requires_source_checkout") is not False:
        raise CleanMachineAcceptanceError("候选程序仍依赖源码目录")
    if value.get("external_commands") != []:
        commands = {str(command).casefold() for command in value.get("external_commands", [])}
        forbidden = sorted(commands & FORBIDDEN_COMMAND_NAMES)
        raise CleanMachineAcceptanceError(f"候选程序调用外部命令：{forbidden or sorted(commands)}")
    if value.get("required_environment_variables") != []:
        raise CleanMachineAcceptanceError("候选程序仍要求用户配置环境变量")
    if value.get("first_run_entry") != "import-evidence-package":
        raise CleanMachineAcceptanceError("首次启动没有进入资料包导入")
    data_root_value = str(value.get("data_root") or "")
    try:
        data_root = Path(data_root_value).resolve()
        data_root.relative_to(local_app_data.resolve())
    except (OSError, ValueError) as exc:
        raise CleanMachineAcceptanceError("候选程序没有把数据放入 LOCALAPPDATA") from exc
    return "import-evidence-package"


def run_clean_machine_acceptance(
    candidate_root: Path,
    *,
    runner: CandidateRunner,
    sandbox_root: Path,
) -> CleanMachineReport:
    runtime = verify_runtime_bundle(candidate_root)
    sandbox = sandbox_root.expanduser().resolve()
    working = sandbox / "Empty Working Directory"
    local_app_data = sandbox / "User" / "AppData" / "Local"
    temporary = sandbox / "Temp"
    for directory in (working, local_app_data, temporary):
        directory.mkdir(parents=True, exist_ok=False)
    if any(working.iterdir()):
        raise CleanMachineAcceptanceError("干净机工作目录必须为空")
    environment = clean_environment(local_app_data=local_app_data, temporary=temporary)
    if any(key.startswith(FORBIDDEN_ENV_PREFIXES) for key in environment):
        raise CleanMachineAcceptanceError("干净机环境意外包含开发变量")
    result = runner.run(runtime.entrypoint, cwd=working, environment=environment)
    first_run_entry = _validate_runtime_report(result, local_app_data)
    return CleanMachineReport(
        runtime=runtime,
        used_empty_working_directory=not any(working.iterdir()),
        used_clean_environment=environment.get("PATH") == "",
        first_run_entry=first_run_entry,
    )
