from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from types import SimpleNamespace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DESKTOP_ROOT = PROJECT_ROOT / "desktop" / "macos"
SOURCE_ROOT = PROJECT_ROOT / "src"
for path in (DESKTOP_ROOT, SOURCE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ai_runtime_composition import create_mac_ai_runtime_services  # noqa: E402
from auto_research.ai.business_actions import BusinessActionError  # noqa: E402
from auto_research.ai.desktop_controller import DESKTOP_AI_ROUTES  # noqa: E402
from auto_research.ai.harness_contract import (  # noqa: E402
    CORDIS_RUNTIME_PROTOCOL_PIN,
    HARNESS_SDK_PROTOCOL_PIN,
    HarnessDependencyMetadata,
    HarnessDependencySet,
)
from auto_research.ai.harness_official_sdk import safe_composition_metadata  # noqa: E402
from auto_research.evidence.db import EvidenceDB  # noqa: E402
from desktop_product_services import create_desktop_product_services  # noqa: E402
from desktop_ai_api import MacDesktopAIAPI  # noqa: E402
from desktop_server import (  # noqa: E402
    COOKIE_NAME,
    CSRF_HEADER,
    create_desktop_server,
    new_session_token,
)
from secure_credentials import (  # noqa: E402
    DEEPSEEK_PROVIDER,
    OPENAI_PROVIDER,
    ProviderCredentialManager,
)


class MemoryBackend:
    storage_label = "memory"

    def __init__(self):
        self.value = None

    def exists(self):
        return self.value is not None

    def read(self):
        return self.value

    def write(self, value):
        self.value = value

    def delete(self):
        self.value = None


class FakeHarnessRuntime:
    def dependency_metadata(self):
        return HarnessDependencySet(
            HarnessDependencyMetadata.from_pin(HARNESS_SDK_PROTOCOL_PIN),
            HarnessDependencyMetadata.from_pin(CORDIS_RUNTIME_PROTOCOL_PIN),
            "2.12.0",
        )

    def composition_metadata(self):
        return safe_composition_metadata()

    def execute(self, **_kwargs):
        raise AssertionError("Harness must not execute in route framing tests")


class MacDesktopAICancelStaticTests(unittest.TestCase):
    def test_cancel_route_is_owned_once_with_a_small_json_body_cap(self):
        path = "/api/desktop/ai/jobs/ai_job_abcdefghijklmnopqrstuvwxyz/cancel"
        matches = [route for route in DESKTOP_AI_ROUTES if route.match("POST", path)]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].route_id, "desktop_ai.business_job_cancel")
        self.assertLessEqual(matches[0].body_cap_bytes, 256)
        self.assertTrue(MacDesktopAIAPI.is_path(path))


class DesktopAIRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        manager = ProviderCredentialManager(
            {
                DEEPSEEK_PROVIDER: MemoryBackend(),
                OPENAI_PROVIDER: MemoryBackend(),
            }
        )
        self.database = EvidenceDB(root / "evidence.sqlite")
        self.database.init()
        self.product = create_desktop_product_services(
            data_root=root / "Application Support",
            current_app_version="0.8.0-preview",
        )
        self.desktop_session = "desktop-session-" + "s" * 32
        self.services = create_mac_ai_runtime_services(
            state_path=root / "state.json",
            attestation_key_path=root / "attestation.key",
            credential_manager=manager,
            database=self.database,
            personal_import_service=self.product.personal_import_service,
            desktop_session_id=self.desktop_session,
            federated_search_session=self.product.federated_search_service.session,
            harness_runtime=FakeHarnessRuntime(),
        )
        self.token = new_session_token()
        self.server, _ = create_desktop_server(
            self.database,
            host="127.0.0.1",
            port=0,
            token=self.token,
            credential_store=self.services.legacy_deepseek_store,
            desktop_ai_api=MacDesktopAIAPI(self.services.controller),
            session_token=self.desktop_session,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(1)
        self.temporary.cleanup()

    def bootstrap(self):
        jar = CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        opener.open(f"{self.base}/?desktop_token={self.token}").close()
        with opener.open(f"{self.base}/api/ui-mode") as response:
            csrf = response.headers[CSRF_HEADER]
        self.assertEqual(len([c for c in jar if c.name == COOKIE_NAME]), 1)
        return opener, csrf

    def request(self, opener, method, path, *, payload=None, csrf=None, origin=True):
        headers = {}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode()
        if csrf is not None:
            headers[CSRF_HEADER] = csrf
        if origin:
            headers["Origin"] = self.base
        return opener.open(
            urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        )

    def test_catalog_and_openai_key_are_session_csrf_protected_and_secret_free(self):
        opener, csrf = self.bootstrap()
        with opener.open(f"{self.base}/api/desktop/ai/providers") as response:
            catalog = json.load(response)
        self.assertEqual({p["provider_id"] for p in catalog["providers"]}, {"deepseek", "openai"})
        key = "sk-openai-private-never-render"
        with self.request(
            opener,
            "POST",
            "/api/desktop/ai/credentials/openai",
            payload={"api_key": key},
            csrf=csrf,
        ) as response:
            saved = json.load(response)
        self.assertTrue(saved["configured"])
        self.assertNotIn(key, json.dumps(saved))
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request(
                opener,
                "POST",
                "/api/desktop/ai/credentials/openai",
                payload={"api_key": "sk-replacement"},
            )
        self.assertEqual(raised.exception.code, 403)
        raised.exception.close()

    def test_all_shared_routes_are_owned_by_the_mac_adapter(self):
        expected = {
            ("GET", "/api/desktop/ai/providers"),
            ("GET", "/api/desktop/ai/settings"),
            ("PATCH", "/api/desktop/ai/settings"),
            ("GET", "/api/desktop/ai/credentials/openai"),
            ("POST", "/api/desktop/ai/credentials/openai"),
            ("DELETE", "/api/desktop/ai/credentials/openai"),
            ("POST", "/api/desktop/ai/providers/openai/test-actions"),
            ("POST", "/api/desktop/ai/providers/openai/test"),
            ("POST", "/api/desktop/ai/consents"),
            ("POST", "/api/desktop/ai/actions/personal_suggestion/prepare"),
            ("POST", "/api/desktop/ai/actions/personal_suggestion/execute"),
            ("POST", "/api/desktop/ai/actions/personal_suggestion/execute-jobs"),
            ("POST", "/api/desktop/ai/actions/literature_extraction/recover-finalization"),
            ("GET", "/api/desktop/ai/jobs/ai_job_abcdefghijklmnopqrstuvwxyz"),
            ("POST", "/api/desktop/ai/jobs/ai_job_abcdefghijklmnopqrstuvwxyz/cancel"),
        }
        self.assertEqual(len(DESKTOP_AI_ROUTES), 20)
        for method, path in expected:
            with self.subTest(method=method, path=path):
                self.assertTrue(MacDesktopAIAPI.is_path(path))
                self.assertTrue(
                    any(route.match(method, path) is not None for route in DESKTOP_AI_ROUTES)
                )

    def test_business_routes_reuse_session_origin_csrf_and_hide_internal_errors(self):
        opener, csrf = self.bootstrap()
        path = "/api/desktop/ai/actions/personal_suggestion/prepare"
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request(
                opener,
                "POST",
                path,
                payload={"import_id": "missing", "sheet_index": 0},
                csrf=csrf,
            )
        self.assertEqual(raised.exception.code, 422)
        payload = json.load(raised.exception)
        self.assertEqual(payload["code"], "business_action_prepare_failed")
        self.assertNotIn(str(Path(self.temporary.name)), json.dumps(payload))
        raised.exception.close()

        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request(
                opener,
                "POST",
                path,
                payload={"import_id": "missing", "sheet_index": 0},
                csrf=csrf,
                origin=False,
            )
        self.assertEqual(raised.exception.code, 403)
        raised.exception.close()

    def test_cancel_route_reuses_origin_csrf_and_strict_empty_body(self):
        opener, csrf = self.bootstrap()
        path = "/api/desktop/ai/jobs/ai_job_abcdefghijklmnopqrstuvwxyz/cancel"
        with self.assertRaises(urllib.error.HTTPError) as missing:
            self.request(opener, "POST", path, payload={}, csrf=csrf)
        self.assertEqual(missing.exception.code, 404)
        self.assertEqual(json.load(missing.exception)["code"], "ai_execution_job_invalid")
        missing.exception.close()

        with self.assertRaises(urllib.error.HTTPError) as forged:
            self.request(opener, "POST", path, payload={"prompt": "forged"}, csrf=csrf)
        self.assertEqual(forged.exception.code, 400)
        forged.exception.close()

        with self.assertRaises(urllib.error.HTTPError) as no_origin:
            self.request(opener, "POST", path, payload={}, csrf=csrf, origin=False)
        self.assertEqual(no_origin.exception.code, 403)
        no_origin.exception.close()

    def test_literature_prepare_error_keeps_safe_stage_and_recovery_action(self):
        paper_id = self.database.upsert_paper(
            title="Paper without PDF", doi="10.1/no-pdf"
        )
        opener, csrf = self.bootstrap()
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request(
                opener,
                "POST",
                "/api/desktop/ai/actions/literature_extraction/prepare",
                payload={"paper_id": paper_id, "force_rescan": False},
                csrf=csrf,
            )
        self.assertEqual(raised.exception.code, 422)
        payload = json.load(raised.exception)
        self.assertEqual(payload["code"], "business_action_prepare_failed")
        self.assertEqual(payload["cause_code"], "literature_pdf_missing")
        self.assertEqual(payload["stage"], "preflight")
        self.assertEqual(payload["next_action"], "reimport_pdf")
        self.assertNotIn(str(Path(self.temporary.name)), json.dumps(payload))
        raised.exception.close()

    def test_librarian_without_official_source_fails_closed_without_legacy_fallback(self):
        with self.assertRaises(BusinessActionError) as raised:
            self.services.librarian_ports.assembler.assemble(
                {"question": "你好", "conversation_id": "local-c1"}
            )
        self.assertEqual(raised.exception.code, "business_action_prepare_failed")
        self.assertFalse(
            self.services.desktop_service.credential_status("deepseek")["configured"]
        )

    def test_wrong_method_and_oversized_body_do_not_fall_through(self):
        opener, csrf = self.bootstrap()
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request(opener, "POST", "/api/desktop/ai/providers", payload={}, csrf=csrf)
        self.assertEqual(raised.exception.code, 405)
        self.assertEqual(json.load(raised.exception)["code"], "desktop_ai_method_not_allowed")
        raised.exception.close()
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1])
        connection.putrequest("PATCH", "/api/desktop/ai/settings")
        connection.putheader("Cookie", f"{COOKIE_NAME}={self.server.RequestHandlerClass.security_state.session_token}")
        connection.putheader("Origin", self.base)
        connection.putheader(CSRF_HEADER, csrf)
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(40 * 1024))
        connection.endheaders()
        connection.send(b"{}")
        response = connection.getresponse()
        self.assertEqual(response.status, 413)
        self.assertEqual(json.load(response)["code"], "desktop_ai_request_too_large")
        connection.close()

    def test_query_and_wrong_method_close_without_consuming_body(self):
        class FakeHandler:
            def __init__(self, path):
                self.path = path
                self.close_connection = False
                self.security_state = SimpleNamespace(session_token="session")
                self.response = None
                self.read_attempted = False

            def json_response(self, payload, status):
                self.response = (int(status), payload)

            def _content_length(self, *_args, **_kwargs):
                self.read_attempted = True
                raise AssertionError("wrong-method body must not be consumed")

            def _read_exact_body(self, _length):
                self.read_attempted = True
                raise AssertionError("wrong-method body must not be consumed")

            def _csrf_valid(self):
                return True

        api = MacDesktopAIAPI(self.services.controller)
        query = FakeHandler("/api/desktop/ai/providers?x=1")
        self.assertTrue(api.handle(query, "GET"))
        self.assertTrue(query.close_connection)
        self.assertEqual(query.response[0], 400)

        wrong_method = FakeHandler("/api/desktop/ai/providers")
        self.assertTrue(api.handle(wrong_method, "POST"))
        self.assertTrue(wrong_method.close_connection)
        self.assertFalse(wrong_method.read_attempted)
        self.assertEqual(wrong_method.response[0], 405)
        self.assertEqual(wrong_method.response[1]["code"], "desktop_ai_method_not_allowed")

        malformed = FakeHandler("/api/desktop/ai/credentials/openai")
        malformed._content_length = lambda *_args, **_kwargs: 2
        malformed._read_exact_body = lambda _length: (_ for _ in ()).throw(
            ValueError("Incomplete request body")
        )
        self.assertTrue(api.handle(malformed, "POST"))
        self.assertTrue(malformed.close_connection)
        self.assertEqual(malformed.response[0], 400)

    def raw(self, request_lines: list[tuple[str, str]], body: bytes = b""):
        state = self.server.RequestHandlerClass.security_state
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1])
        method, path = request_lines[0]
        connection.putrequest(method, path)
        connection.putheader("Cookie", f"{COOKIE_NAME}={state.session_token}")
        connection.putheader("Origin", self.base)
        connection.putheader(CSRF_HEADER, state.csrf_token)
        for name, value in request_lines[1:]:
            connection.putheader(name, value)
        connection.endheaders(body)
        response = connection.getresponse()
        status = response.status
        payload = response.read()
        connection.close()
        return status, payload

    def test_query_and_all_body_framing_fail_closed(self):
        opener, csrf = self.bootstrap()
        with self.assertRaises(urllib.error.HTTPError) as raised:
            opener.open(f"{self.base}/api/desktop/ai/providers?x=1")
        self.assertEqual(raised.exception.code, 400)
        raised.exception.close()
        cases = (
            [("GET", "/api/desktop/ai/providers"), ("Content-Length", "2")],
            [("GET", "/api/desktop/ai/providers"), ("Content-Length", "invalid")],
            [("GET", "/api/desktop/ai/providers"), ("Transfer-Encoding", "chunked")],
            [("DELETE", "/api/desktop/ai/credentials/openai"), ("Content-Length", "2")],
            [("POST", "/api/desktop/ai/credentials/openai"), ("Content-Type", "application/json"), ("Transfer-Encoding", "chunked")],
            [("PATCH", "/api/desktop/ai/settings"), ("Content-Type", "application/json"), ("Content-Length", "2"), ("Content-Length", "2")],
        )
        for headers in cases:
            with self.subTest(headers=headers):
                status, _ = self.raw(headers, b"{}")
                self.assertEqual(status, 400)
        status, _ = self.raw(
            [
                ("POST", "/api/desktop/ai/credentials/openai"),
                ("Content-Type", "application/json"),
                ("Content-Length", "1"),
            ],
            b"{",
        )
        self.assertEqual(status, 400)

    def test_missing_session_and_untrusted_origin_are_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(f"{self.base}/api/desktop/ai/providers")
        self.assertEqual(raised.exception.code, 403)
        raised.exception.close()

        opener, csrf = self.bootstrap()
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request(
                opener,
                "POST",
                "/api/desktop/ai/credentials/openai",
                payload={"api_key": "sk-never-save"},
                csrf=csrf,
                origin=False,
            )
        self.assertEqual(raised.exception.code, 403)
        raised.exception.close()

    def test_read_only_blocks_mutations_but_keeps_get_routes(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(1)
        root = Path(self.temporary.name)
        self.token = new_session_token()
        self.server, _ = create_desktop_server(
            EvidenceDB(root / "readonly.sqlite"),
            host="127.0.0.1",
            port=0,
            token=self.token,
            read_only=True,
            credential_store=self.services.legacy_deepseek_store,
            desktop_ai_api=MacDesktopAIAPI(self.services.controller),
            session_token=self.desktop_session,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        opener, csrf = self.bootstrap()
        with opener.open(f"{self.base}/api/desktop/ai/settings") as response:
            self.assertEqual(response.status, 200)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request(
                opener,
                "POST",
                "/api/desktop/ai/credentials/openai",
                payload={"api_key": "sk-never-save"},
                csrf=csrf,
            )
        self.assertEqual(raised.exception.code, 403)
        raised.exception.close()


if __name__ == "__main__":
    unittest.main()
