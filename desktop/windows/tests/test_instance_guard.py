from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "instance_guard.py"
SPEC = importlib.util.spec_from_file_location("windows_instance_guard", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeMutexBackend:
    def __init__(self) -> None:
        self.active: dict[str, object] = {}
        self.released: list[object] = []

    def acquire(self, name: str) -> object | None:
        if name in self.active:
            return None
        handle = object()
        self.active[name] = handle
        return handle

    def release(self, handle: object) -> None:
        names = [name for name, value in self.active.items() if value is handle]
        if not names:
            raise AssertionError("unknown mutex handle")
        del self.active[names[0]]
        self.released.append(handle)


class WindowsInstanceGuardTests(unittest.TestCase):
    def test_same_workspace_is_single_instance_case_insensitive(self) -> None:
        backend = FakeMutexBackend()
        first = MODULE.WindowsInstanceGuard(
            r"C:\Users\Researcher\AppData\Local\Auto Research", backend
        ).acquire()
        try:
            second = MODULE.WindowsInstanceGuard(
                r"c:\users\researcher\appdata\local\AUTO RESEARCH", backend
            )
            with self.assertRaises(MODULE.InstanceAlreadyRunningError):
                second.acquire()
        finally:
            first.close()

    def test_different_test_workspace_can_run_independently(self) -> None:
        backend = FakeMutexBackend()
        first = MODULE.WindowsInstanceGuard(r"C:\AutoResearch-A", backend).acquire()
        second = MODULE.WindowsInstanceGuard(r"D:\AutoResearch-B", backend).acquire()
        self.assertNotEqual(first.name, second.name)
        first.close()
        second.close()

    def test_context_manager_releases_guard_after_error(self) -> None:
        backend = FakeMutexBackend()
        guard = MODULE.WindowsInstanceGuard(r"C:\AutoResearch", backend)
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with guard:
                self.assertTrue(guard.acquired)
                raise RuntimeError("boom")
        self.assertFalse(guard.acquired)
        self.assertEqual(len(backend.released), 1)

    def test_relative_or_parent_traversal_workspace_is_rejected(self) -> None:
        for value in ("relative", r"C:\AutoResearch\..\Other"):
            with self.subTest(value=value):
                with self.assertRaises(MODULE.WindowsInstanceError):
                    MODULE.workspace_mutex_name(value)

    def test_native_backend_is_not_faked_off_windows(self) -> None:
        if MODULE.os.name != "nt":
            with self.assertRaises(MODULE.WindowsInstanceError):
                MODULE.Win32MutexBackend()


if __name__ == "__main__":
    unittest.main()
