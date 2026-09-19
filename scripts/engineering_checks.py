"""Offline engineering pytest policy; corpus evaluation uses a separate command."""
import ipaddress
import socket

import pytest


@pytest.fixture(autouse=True)
def offline_network(monkeypatch):
    connect = socket.socket.connect

    def local_connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            host = address[0]
            if host != "localhost" and not ipaddress.ip_address(host).is_loopback:
                raise RuntimeError("Engineering checks prohibit external network calls")
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", local_connect)


def pytest_sessionfinish(session, exitstatus):
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter and reporter.stats.get("skipped"):
        reporter.write_sep("!", "Unexpected skipped tests: engineering gate failed")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
