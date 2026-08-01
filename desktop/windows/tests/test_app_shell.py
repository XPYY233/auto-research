from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT))
try:
    import app_shell as MODULE
finally:
    sys.path.pop(0)


class FakeGuard:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail

    def acquire(self):
        self.events.append("guard.acquire")
        if self.fail:
            raise RuntimeError("already running")
        return self

    def close(self):
        self.events.append("guard.close")


class FakeService:
    def __init__(self, events: list[str], port: int = 49152) -> None:
        self.events = events
        self._port = port

    @property
    def port(self) -> int:
        return self._port

    def start(self, *, host: str, port: int) -> None:
        self.events.append(f"service.start:{host}:{port}")

    def stop(self) -> None:
        self.events.append("service.stop")


class FakeWindow:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail
        self.url = ""

    def show(self, *, title: str, url: str, first_run_entry: str) -> None:
        self.events.append("window.show")
        self.url = url
        self.title = title
        self.first_run_entry = first_run_entry
        if self.fail:
            raise RuntimeError("window failed")


class AppShellCoordinatorTests(unittest.TestCase):
    def _coordinator(self, root: Path, events: list[str], *, window_fail: bool = False):
        service_details: dict[str, str] = {}

        def prepare() -> None:
            events.append("directories.prepare")
            (root / "State").mkdir(parents=True)

        def service_factory(*, bootstrap_token: str, first_run_entry: str):
            service_details["token"] = bootstrap_token
            service_details["entry"] = first_run_entry
            events.append("service.create")
            return FakeService(events)

        window = FakeWindow(events, fail=window_fail)
        coordinator = MODULE.WindowsAppShellCoordinator(
            data_root=root,
            state_directory=root / "State",
            prepare_directories=prepare,
            guard_factory=lambda data_root: FakeGuard(events),
            service_factory=service_factory,
            window=window,
            token_factory=lambda: "t" * 43,
        )
        return coordinator, window, service_details

    def test_shell_runs_import_entry_and_cleans_up_in_reverse_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events: list[str] = []
            coordinator, window, details = self._coordinator(Path(directory), events)
            report = coordinator.run()
            self.assertEqual(
                events,
                [
                    "directories.prepare",
                    "guard.acquire",
                    "service.create",
                    "service.start:127.0.0.1:0",
                    "window.show",
                    "service.stop",
                    "guard.close",
                ],
            )
            parsed = urlparse(window.url)
            query = parse_qs(parsed.query)
            self.assertEqual(parsed.hostname, "127.0.0.1")
            self.assertEqual(parsed.port, 49152)
            self.assertEqual(query["desktop_entry"], ["import-evidence-package"])
            self.assertEqual(query["desktop_token"], [details["token"]])
            self.assertEqual(report.first_run_entry, "import-evidence-package")
            self.assertFalse(hasattr(report, "bootstrap_token"))

    def test_window_failure_still_stops_service_and_releases_guard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events: list[str] = []
            coordinator, _, _ = self._coordinator(
                Path(directory), events, window_fail=True
            )
            with self.assertRaisesRegex(RuntimeError, "window failed"):
                coordinator.run()
            self.assertEqual(events[-2:], ["service.stop", "guard.close"])

    def test_failed_guard_never_creates_service_or_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events: list[str] = []
            coordinator = MODULE.WindowsAppShellCoordinator(
                data_root=root,
                state_directory=root / "State",
                prepare_directories=lambda: events.append("directories.prepare"),
                guard_factory=lambda data_root: FakeGuard(events, fail=True),
                service_factory=lambda **kwargs: events.append("service.create"),
                window=FakeWindow(events),
            )
            with self.assertRaisesRegex(RuntimeError, "already running"):
                coordinator.run()
            self.assertEqual(events, ["directories.prepare", "guard.acquire"])

    def test_short_or_wrong_first_run_entry_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(MODULE.AppShellError):
                MODULE.WindowsAppShellCoordinator(
                    data_root=root,
                    state_directory=root / "State",
                    prepare_directories=lambda: None,
                    guard_factory=lambda value: FakeGuard([]),
                    service_factory=lambda **kwargs: FakeService([]),
                    window=FakeWindow([]),
                    first_run_entry="choose-source-checkout",
                )


if __name__ == "__main__":
    unittest.main()
