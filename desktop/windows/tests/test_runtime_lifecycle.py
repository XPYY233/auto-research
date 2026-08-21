from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "runtime_lifecycle.py"
SPEC = importlib.util.spec_from_file_location("windows_runtime_lifecycle", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeLoopbackService:
    def __init__(self, selected_port: int) -> None:
        self._port = selected_port
        self.start_calls: list[tuple[str, int]] = []
        self.stop_calls = 0
        self.fail_stop = False

    @property
    def port(self) -> int:
        return self._port

    def start(self, *, host: str, port: int) -> None:
        self.start_calls.append((host, port))

    def stop(self) -> None:
        self.stop_calls += 1
        if self.fail_stop:
            raise RuntimeError("simulated stop failure")


class WindowsRuntimeLifecycleTests(unittest.TestCase):
    def test_windows_liveness_probe_never_uses_os_kill(self) -> None:
        with mock.patch.object(MODULE.sys, "platform", "win32"), mock.patch.object(
            MODULE,
            "_windows_process_is_alive",
            return_value=True,
        ) as windows_probe, mock.patch.object(MODULE.os, "kill") as os_kill:
            self.assertTrue(MODULE.process_is_alive(1234))
        windows_probe.assert_called_once_with(1234)
        os_kill.assert_not_called()

    def test_non_positive_pid_is_rejected_before_windows_probe(self) -> None:
        with mock.patch.object(MODULE.sys, "platform", "win32"), mock.patch.object(
            MODULE,
            "_windows_process_is_alive",
        ) as windows_probe:
            self.assertFalse(MODULE.process_is_alive(0))
        windows_probe.assert_not_called()

    def test_start_uses_loopback_and_os_selected_port_without_persisting_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = FakeLoopbackService(49152)
            session = MODULE.WindowsRuntimeSession(
                state_directory=Path(directory) / "State",
                service=service,
                pid=123,
                pid_probe=lambda pid: False,
            )
            recovery = session.start()
            self.assertFalse(recovery.recovered_crash)
            self.assertEqual(service.start_calls, [("127.0.0.1", 0)])
            state_text = session.state_file.read_text(encoding="utf-8")
            state = json.loads(state_text)
            self.assertEqual(state["port"], 49152)
            self.assertNotIn("token", state_text.casefold())
            session.close()
            self.assertFalse(session.state_file.exists())
            self.assertFalse(session.runtime_directory.exists())

    def test_crash_recovery_deletes_only_old_runtime_and_never_reuses_port(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_directory = Path(directory) / "State"
            runtime_root = state_directory / "Runtime"
            old_session_id = str(uuid.uuid4())
            old_runtime = runtime_root / old_session_id
            old_runtime.mkdir(parents=True)
            (old_runtime / "disposable.tmp").write_text("old", encoding="utf-8")
            private_file = Path(directory) / "Repositories" / "Private" / "keep.sqlite"
            private_file.parent.mkdir(parents=True)
            private_file.write_text("private", encoding="utf-8")
            state_directory.mkdir(parents=True, exist_ok=True)
            (state_directory / MODULE.STATE_FILE_NAME).write_text(
                json.dumps(
                    {
                        "version": 1,
                        "session_id": old_session_id,
                        "pid": 77,
                        "port": 41000,
                        "host": "127.0.0.1",
                    }
                ),
                encoding="utf-8",
            )
            service = FakeLoopbackService(42000)
            session = MODULE.WindowsRuntimeSession(
                state_directory=state_directory,
                service=service,
                pid=88,
                pid_probe=lambda pid: False,
            )
            recovery = session.start()
            self.assertTrue(recovery.recovered_crash)
            self.assertEqual(recovery.previous_port, 41000)
            self.assertEqual(service.start_calls, [("127.0.0.1", 0)])
            self.assertFalse(old_runtime.exists())
            self.assertTrue(private_file.exists())
            session.close()

    def test_live_previous_process_blocks_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_directory = Path(directory) / "State"
            state_directory.mkdir(parents=True)
            previous_id = str(uuid.uuid4())
            (state_directory / MODULE.STATE_FILE_NAME).write_text(
                json.dumps(
                    {
                        "version": 1,
                        "session_id": previous_id,
                        "pid": 77,
                        "port": 41000,
                    }
                ),
                encoding="utf-8",
            )
            session = MODULE.WindowsRuntimeSession(
                state_directory=state_directory,
                service=FakeLoopbackService(42000),
                pid_probe=lambda pid: True,
            )
            with self.assertRaises(MODULE.WindowsRuntimeError):
                session.start()

    def test_failed_stop_keeps_crash_marker_for_next_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = FakeLoopbackService(43000)
            session = MODULE.WindowsRuntimeSession(
                state_directory=Path(directory) / "State",
                service=service,
                pid_probe=lambda pid: False,
            )
            session.start()
            service.fail_stop = True
            with self.assertRaises(MODULE.WindowsRuntimeError):
                session.close()
            self.assertTrue(session.state_file.exists())
            self.assertTrue(session.runtime_directory.exists())


if __name__ == "__main__":
    unittest.main()
