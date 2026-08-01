from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT))
try:
    import loopback_server_adapter as MODULE
finally:
    sys.path.pop(0)


class FakeServer:
    def __init__(self, host: str, port: int) -> None:
        self.server_address = (host, port)
        self.shutdown_called = False
        self.close_called = False
        self.started = threading.Event()
        self.stopped = threading.Event()

    def serve_forever(self) -> None:
        self.started.set()
        self.stopped.wait(timeout=1)

    def shutdown(self) -> None:
        self.shutdown_called = True
        self.stopped.set()

    def server_close(self) -> None:
        self.close_called = True


class LoopbackServerAdapterTests(unittest.TestCase):
    def test_builder_receives_token_entry_and_random_port(self) -> None:
        values = {}

        def builder(**kwargs):
            values.update(kwargs)
            values["server"] = FakeServer(kwargs["host"], 49300)
            return values["server"]

        adapter = MODULE.LoopbackServerAdapter(
            builder,
            bootstrap_token="t" * 43,
            first_run_entry="import-evidence-package",
        )
        adapter.start(host="127.0.0.1", port=0)
        self.assertTrue(values["server"].started.wait(timeout=1))
        self.assertEqual(values["port"], 0)
        self.assertEqual(values["bootstrap_token"], "t" * 43)
        self.assertEqual(adapter.port, 49300)
        adapter.stop()
        self.assertTrue(values["server"].shutdown_called)
        self.assertTrue(values["server"].close_called)

    def test_non_loopback_or_fixed_port_is_rejected(self) -> None:
        adapter = MODULE.LoopbackServerAdapter(
            lambda **kwargs: FakeServer("127.0.0.1", 49300),
            bootstrap_token="t" * 43,
            first_run_entry="import-evidence-package",
        )
        for host, port in (("0.0.0.0", 0), ("127.0.0.1", 8765)):
            with self.subTest(host=host, port=port):
                with self.assertRaises(MODULE.LoopbackAdapterError):
                    adapter.start(host=host, port=port)

    def test_builder_bound_to_public_host_is_closed_and_rejected(self) -> None:
        server = FakeServer("0.0.0.0", 49300)
        adapter = MODULE.LoopbackServerAdapter(
            lambda **kwargs: server,
            bootstrap_token="t" * 43,
            first_run_entry="import-evidence-package",
        )
        with self.assertRaises(MODULE.LoopbackAdapterError):
            adapter.start(host="127.0.0.1", port=0)
        self.assertTrue(server.close_called)


if __name__ == "__main__":
    unittest.main()
