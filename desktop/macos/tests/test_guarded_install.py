from __future__ import annotations

import os
from pathlib import Path
import select
import subprocess
import sys
import time

import pytest

from desktop_runtime import InstanceAlreadyRunningError, acquire_instance_lock
from guarded_install import run_locked_install


def test_running_app_prevents_any_transaction(tmp_path, capsys):
    lock_path = tmp_path / "desktop.lock"
    marker = tmp_path / "would-replace-app"
    with acquire_instance_lock(lock_path):
        assert run_locked_install([sys.executable, "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).touch()", str(marker)],
            lock_path=lock_path) == 4
    assert not marker.exists()
    assert "未替换应用" in capsys.readouterr().out


@pytest.mark.parametrize("exit_code", [0, 19])
def test_transaction_blocks_new_app_and_releases_lock_on_exit(tmp_path, exit_code):
    lock_path = tmp_path / "desktop.lock"
    script = '''
import sys
from pathlib import Path
from desktop_runtime import acquire_instance_lock, InstanceAlreadyRunningError
try:
    handle = acquire_instance_lock(Path(sys.argv[1]))
except InstanceAlreadyRunningError:
    raise SystemExit(int(sys.argv[2]))
else:
    handle.close()
    raise SystemExit(99)
'''
    assert run_locked_install([sys.executable, "-c", script, str(lock_path), str(exit_code)],
                              lock_path=lock_path) == exit_code
    with acquire_instance_lock(lock_path):
        pass


def test_missing_transaction_releases_lock(tmp_path):
    lock_path = tmp_path / "desktop.lock"
    with pytest.raises(FileNotFoundError):
        run_locked_install([str(tmp_path / "missing-program")], lock_path=lock_path)
    with acquire_instance_lock(lock_path):
        pass


def test_shell_transaction_retains_lock_when_supervisor_terminates(tmp_path):
    lock_path = tmp_path / "desktop.lock"
    finished = tmp_path / "finished"
    worker = tmp_path / "transaction.zsh"
    worker.write_text('''#!/bin/zsh
print -r -- ready
read -r acknowledgement
: > "$1"
''')
    launcher = '''
import sys
from pathlib import Path
from guarded_install import run_locked_install
raise SystemExit(run_locked_install(["/bin/zsh", sys.argv[2], sys.argv[3]], lock_path=Path(sys.argv[1])))
'''
    process = subprocess.Popen([sys.executable, "-c", launcher, str(lock_path), str(worker), str(finished)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert select.select([process.stdout], [], [], 5)[0], "transaction did not start"
        assert process.stdout.readline() == b"ready\n"
        process.terminate()
        process.wait(timeout=5)
        with pytest.raises(InstanceAlreadyRunningError):
            acquire_instance_lock(lock_path)
        process.stdin.write(b"finish\n")
        process.stdin.flush()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if finished.exists():
                try:
                    handle = acquire_instance_lock(lock_path)
                except InstanceAlreadyRunningError:
                    pass
                else:
                    handle.close()
                    break
            time.sleep(0.02)
        else:
            pytest.fail("transaction did not release inherited lock")
    finally:
        if process.stdin:
            process.stdin.close()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()


def test_public_entry_uses_guard_before_accessing_candidate(tmp_path):
    desktop = Path(__file__).resolve().parents[1]
    home = tmp_path / "user"
    lock_path = home / "Library/Application Support/Auto Research/desktop.lock"
    env = {**os.environ, "HOME": str(home), "AUTO_RESEARCH_DESKTOP_PYTHON": sys.executable}
    with acquire_instance_lock(lock_path):
        result = subprocess.run(["/bin/zsh", str(desktop / "install_fusion_review.command")],
                                env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 4
    assert "未替换应用" in result.stdout
    assert not result.stderr
