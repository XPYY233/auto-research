from __future__ import annotations

import io
import unittest
from email.message import Message
from pathlib import Path

from auto_research.evidence.webapp import EvidenceHandler, serve


def synthetic_handler(*headers: tuple[str, str], body: bytes = b"") -> EvidenceHandler:
    handler = EvidenceHandler.__new__(EvidenceHandler)
    message = Message()
    for name, value in headers:
        message.add_header(name, value)
    handler.headers = message
    handler.rfile = io.BytesIO(body)
    return handler


class LoopbackRequestSecurityTests(unittest.TestCase):
    def test_editable_core_service_requires_explicit_development_opt_in(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "editable browser service is retired"):
            serve(host="127.0.0.1", port=0, read_only=False)

    def test_json_rejects_negative_and_duplicate_content_length(self) -> None:
        negative = synthetic_handler(("Content-Length", "-1"), body=b"{}")
        with self.assertRaisesRegex(ValueError, "Invalid Content-Length"):
            negative.read_json()

        duplicate = synthetic_handler(
            ("Content-Length", "2"),
            ("Content-Length", "2"),
            body=b"{}",
        )
        with self.assertRaisesRegex(ValueError, "Exactly one Content-Length"):
            duplicate.read_json()

    def test_json_rejects_transfer_encoding_and_conflicting_framing(self) -> None:
        chunked = synthetic_handler(
            ("Transfer-Encoding", "chunked"),
            ("Content-Length", "2"),
            body=b"{}",
        )
        with self.assertRaisesRegex(ValueError, "Transfer-Encoding"):
            chunked.read_json()

    def test_json_rejects_short_body(self) -> None:
        short = synthetic_handler(("Content-Length", "3"), body=b"{}")
        with self.assertRaisesRegex(ValueError, "Incomplete request body"):
            short.read_json()

        missing = synthetic_handler(body=b"")
        with self.assertRaisesRegex(ValueError, "Exactly one Content-Length"):
            missing.read_json()

    def test_json_accepts_one_exact_bounded_body(self) -> None:
        valid = synthetic_handler(("Content-Length", "11"), body=b'{"ok":true}')
        self.assertEqual(valid.read_json(), {"ok": True})

    def test_shared_frontend_attaches_desktop_csrf_to_mutations(self) -> None:
        app_js = (
            Path(__file__).resolve().parents[1]
            / "auto_research"
            / "evidence"
            / "web"
            / "app.js"
        ).read_text(encoding="utf-8")
        self.assertIn('const desktopCsrfHeader = "X-Auto-Research-CSRF";', app_js)
        self.assertIn("headers.set(desktopCsrfHeader, desktopCsrfToken);", app_js)
        self.assertIn("captureDesktopCsrf(response);", app_js)
        self.assertIn(
            "fetch(librarianDesktopHistoryEndpoint, desktopRequestOptions({",
            app_js,
        )


if __name__ == "__main__":
    unittest.main()
