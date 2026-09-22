"""Hold the desktop instance lock throughout the installation transaction."""
from __future__ import annotations

from pathlib import Path
import subprocess

from desktop_runtime import LOCK_FILE, InstanceAlreadyRunningError, acquire_instance_lock


def run_locked_install(command: list[str], *, lock_path: Path = LOCK_FILE) -> int:
    try:
        lock = acquire_instance_lock(lock_path)
    except InstanceAlreadyRunningError:
        print("Auto Research 正在运行；请先结束当前任务并退出 App，再安装更新。未替换应用。")
        return 4
    with lock:
        # The transaction inherits the same lock, so killing this supervisor
        # cannot permit a new desktop instance during a still-running swap.
        return subprocess.run(command, pass_fds=(lock.fileno(),), check=False).returncode


def main() -> int:
    transaction = Path(__file__).resolve().with_name("install_transaction.zsh")
    return run_locked_install(["/bin/zsh", str(transaction)])


if __name__ == "__main__":
    raise SystemExit(main())
