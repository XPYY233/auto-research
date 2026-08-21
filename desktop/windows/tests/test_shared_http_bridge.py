from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT))
try:
    import shared_http_bridge as MODULE
    from ai_runtime_composition import create_windows_ai_runtime_services
    from settings_bridge import WindowsSettingsBridge
finally:
    sys.path.pop(0)


IMPORT_ID = "personal_import_0123456789abcdef"


class _Settings:
    def __init__(self) -> None:
        self.revision = 0
        self.theme = "system"
        self.density = "comfortable"

    def get(self):
        return {
            "schema_version": "desktop-settings-v1",
            "revision": self.revision,
            "appearance": {"theme": self.theme, "density": self.density},
            "locale": {"selected": "zh-CN", "supported": ["zh-CN"]},
        }

    def patch_preferences(self, preferences, *, expected_revision):
        from auto_research.settings.desktop_settings import DesktopSettingsError

        if expected_revision != self.revision:
            raise DesktopSettingsError(
                "settings_revision_conflict",
                "桌面设置已在其他操作中更新，请重新加载后再试。",
                retryable=True,
            )
        appearance = preferences.get("appearance", {})
        if set(preferences) - {"appearance", "locale"} or not preferences:
            raise DesktopSettingsError(
                "settings_invalid", "桌面设置内容无效。", retryable=False
            )
        if "theme" in appearance:
            if appearance["theme"] not in {"system", "light", "dark"}:
                raise DesktopSettingsError(
                    "settings_invalid", "桌面设置内容无效。", retryable=False
                )
            self.theme = appearance["theme"]
        self.revision += 1
        return self.get()


class _Package:
    def status(self):
        return {"active": False, "repository_audited": False, "can_search_offline": False}

    def start_import(self, selection_id):
        return {
            "job_id": "package-job-0123456789",
            "operation": "import",
            "stage": "queued",
            "progress": 0,
            "terminal": False,
        }

    def get_job(self, job_id):
        return {
            "job_id": job_id,
            "operation": "import",
            "stage": "completed",
            "progress": 100,
            "terminal": True,
        }


class _Evidence:
    def search(self, query="", **kwargs):
        return {
            "schema_version": "federated-search-page-v1",
            "query": query,
            "page": kwargs["page"],
            "page_size": kwargs["page_size"],
            "total": 0,
            "total_pages": 0,
            "elapsed_ms": 0,
            "results": [],
        }

    def get(self, **identity):
        return {
            "entity_type": "finding",
            **identity,
            "display_title": "safe",
        }

    def open_private_pdf(self, **identity):
        return _PdfLease(identity["source_id"], identity["paper_uid"])


class _PdfLease:
    def __init__(self, source_id, paper_uid):
        self.source_id = source_id
        self.paper_uid = paper_uid
        self.body = b"%PDF-1.7\n%%EOF"
        self.offset = 0
        self.closed = False

    def public_metadata(self):
        return {
            "schema_version": "private-pdf-lease-v1",
            "source_scope": "private",
            "source_id": self.source_id,
            "paper_uid": self.paper_uid,
            "size_bytes": len(self.body),
            "media_type": "application/pdf",
        }

    def read(self, size):
        value = self.body[self.offset : self.offset + size]
        self.offset += len(value)
        return value

    def close(self):
        self.closed = True


class _PackageCenter:
    def status(self):
        return {"schema": "package-center-status-v1", "capabilities": {}}

    def inspect(self, selection_token):
        return {
            "schema": "package-summary-v1",
            "package_kind": "literature_collection",
            "selection_token": selection_token,
        }

    def plan_export(self, kind, scope, selection):
        return {
            "schema": "package-plan-v1",
            "plan_token": "plan_token_0123456789",
            "package_kind": kind,
            "scope": scope,
            "selection": selection,
        }

    def start_export(self, plan_token, rights_confirmations, destination_token):
        return {
            "schema": "package-job-v1",
            "job_id": "package_export_0123456789",
            "operation": "transfer_export",
            "stage": "queued",
            "progress": 0,
            "terminal": False,
        }

    def start_import(self, selection_token, **kwargs):
        return {
            "schema": "package-job-v1",
            "job_id": "package_import_0123456789",
            "operation": "transfer_import",
            "stage": "queued",
            "progress": 0,
            "terminal": False,
        }

    def get_job(self, job_id):
        return {
            "schema": "package-job-v1",
            "job_id": job_id,
            "operation": "transfer_import",
            "stage": "completed",
            "progress": 100,
            "terminal": True,
        }


class _Personal:
    def preview(self, selection_id):
        return {
            "schema_version": "personal-import-preview-v1",
            "status": {"import_id": IMPORT_ID, "stage": "previewed"},
            "preview": {"sheets": []},
        }

    def status(self, import_id):
        return {"schema_version": "personal-import-status-v1", "import_id": import_id}

    def save_draft(self, import_id, payload):
        return {"schema_version": "personal-import-status-v1", "import_id": import_id, "stage": "draft_saved"}

    def confirm(self, import_id, *, expected_revision):
        return {"schema_version": "personal-import-status-v1", "import_id": import_id, "stage": "indexable"}

    def suggest(self, import_id, *, sheet_index):
        return {
            "schema_version": "personal-import-suggestion-v1",
            "import_id": import_id,
            "sheet_index": sheet_index,
            "provider": "DeepSeek",
            "columns": [],
            "series": [],
            "requires_human_review": True,
        }

    def import_reviewed(self, import_id, payload, *, reviewed):
        return {
            "schema_version": "personal-import-status-v1",
            "import_id": import_id,
            "stage": "indexable",
            "indexable": reviewed and isinstance(payload, dict),
        }

    def search_status(self):
        return {"schema_version": "personal-search-readiness-v1", "state": "empty", "ready": False, "document_count": 0}

    def refresh_search(self):
        return self.search_status()


class _Credentials:
    configured = False

    def status(self):
        return {"provider": "deepseek", "configured": self.configured, "storage": "windows-credential-manager"}

    def save(self, api_key):
        self.configured = True
        return self.status()

    def clear(self):
        self.configured = False
        return self.status()


class _CredentialBackend:
    def __init__(self):
        self.values = {}

    def read(self, target):
        return self.values.get(target)

    def write(self, target, secret):
        self.values[target] = bytes(secret)

    def delete(self, target):
        self.values.pop(target, None)


class _Readiness:
    def status(self):
        return SimpleNamespace(
            public_dict=lambda: {
                "schema_version": "desktop-readiness-v2",
                "official_ready": False,
                "private_ready": False,
                "federated_ready": False,
                "ai_key_configured": False,
                "librarian_ready": False,
                "can_search_offline": False,
                "can_use_ai": False,
            }
        )


class _Librarian:
    def chat(self, question, **kwargs):
        return {"answer": question, "results": [], "research_state": kwargs.get("research_state")}


class SharedHttpBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.token = "bootstrap-" + "x" * 40
        self.ai_services = create_windows_ai_runtime_services(
            state_directory=Path(self.temporary.name) / "State",
            credential_backend=_CredentialBackend(),
        )
        self.services = SimpleNamespace(
            package_import=_Package(),
            evidence_search=_Evidence(),
            personal_import=_Personal(),
            ai=self.ai_services.http_api,
            readiness=_Readiness(),
            librarian=_Librarian(),
            package_center=_PackageCenter(),
            settings=_Settings(),
        )
        self.server = MODULE.WindowsSharedHttpBridge().build_server(
            host="127.0.0.1",
            port=0,
            bootstrap_token=self.token,
            first_run_entry="import-evidence-package",
            services=self.services,
            release={
                "label": "Windows internal development",
                "version": "0.8.0-internal.1",
                "evidence_schema": "distribution-sqlite-v1",
            },
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address
        self.origin = f"http://{self.host}:{self.port}"
        self.cookie, self.csrf = self._bootstrap()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _connection(self):
        return http.client.HTTPConnection(self.host, self.port, timeout=5)

    @staticmethod
    def _json(response):
        body = response.read()
        return json.loads(body) if body else {}

    def _bootstrap(self):
        connection = self._connection()
        connection.request("GET", f"/?desktop_token={self.token}")
        response = connection.getresponse()
        self.assertEqual(response.status, 303)
        cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        response.read()
        connection.close()

        connection = self._connection()
        connection.request("GET", "/api/ui-mode", headers={"Cookie": cookie})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        csrf = response.getheader(MODULE.CSRF_HEADER)
        self.assertTrue(csrf)
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        response.read()
        connection.close()
        return cookie, csrf

    def _get(self, path):
        connection = self._connection()
        connection.request("GET", path, headers={"Cookie": self.cookie})
        response = connection.getresponse()
        payload = self._json(response)
        status = response.status
        no_store = response.getheader("Cache-Control")
        connection.close()
        return status, payload, no_store

    def _get_text(self, path):
        connection = self._connection()
        connection.request("GET", path, headers={"Cookie": self.cookie})
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        status = response.status
        no_store = response.getheader("Cache-Control")
        connection.close()
        return status, body, no_store

    def _get_bytes(self, path):
        connection = self._connection()
        connection.request("GET", path, headers={"Cookie": self.cookie})
        response = connection.getresponse()
        body = response.read()
        status = response.status
        media_type = response.getheader("Content-Type")
        disposition = response.getheader("Content-Disposition")
        no_store = response.getheader("Cache-Control")
        connection.close()
        return status, body, media_type, disposition, no_store

    def _post(self, path, payload, *, origin=True, csrf=True):
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Cookie": self.cookie,
            "Content-Type": "application/json",
        }
        if origin:
            headers["Origin"] = self.origin
        if csrf:
            headers[MODULE.CSRF_HEADER] = self.csrf
        connection = self._connection()
        connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        result = self._json(response)
        status = response.status
        connection.close()
        return status, result

    def _patch(self, path, payload, *, origin=True, csrf=True):
        body = json.dumps(payload).encode("utf-8")
        headers = {"Cookie": self.cookie, "Content-Type": "application/json"}
        if origin:
            headers["Origin"] = self.origin
        if csrf:
            headers[MODULE.CSRF_HEADER] = self.csrf
        connection = self._connection()
        connection.request("PATCH", path, body=body, headers=headers)
        response = connection.getresponse()
        result = self._json(response)
        status = response.status
        no_store = response.getheader("Cache-Control")
        connection.close()
        return status, result, no_store

    def test_frozen_package_personal_search_credential_and_readiness_routes(self) -> None:
        get_paths = (
            "/api/desktop/evidence-packages",
            "/api/desktop/package-center",
            "/api/desktop/personal-imports/search-status",
            f"/api/desktop/personal-imports/{IMPORT_ID}",
            "/api/desktop/federated-search?q=&page=1&page_size=20&source_scope=private",
            "/api/desktop/federated-evidence?source_scope=private&source_id=private-1&entity_uid=finding-1",
            "/api/desktop/ai/providers",
            "/api/desktop/ai/settings",
            "/api/desktop/readiness",
            "/api/desktop/settings",
        )
        for path in get_paths:
            with self.subTest(path=path):
                status, payload, no_store = self._get(path)
                self.assertEqual(status, 200)
                self.assertEqual(no_store, "no-store")
                self.assertNotIn("path", json.dumps(payload).casefold())

        posts = (
            ("/api/desktop/evidence-packages/import", {"selection_id": "package-selection-0123456789"}, 202),
            ("/api/desktop/personal-imports/preview", {"selection_id": "personal_selection_0123456789abcdef"}, 201),
            (f"/api/desktop/personal-imports/{IMPORT_ID}/draft", {"sheet_index": 0}, 200),
            (f"/api/desktop/personal-imports/{IMPORT_ID}/confirm", {"expected_revision": 1}, 200),
            (f"/api/desktop/personal-imports/{IMPORT_ID}/ai-suggestion", {"sheet_index": 0, "consent": True}, 410),
            (f"/api/desktop/personal-imports/{IMPORT_ID}/reviewed-import", {"reviewed": True, "draft": {"sheet_index": 0}}, 200),
            ("/api/desktop/personal-imports/search-refresh", {}, 200),
            ("/api/desktop/credentials/deepseek", {"api_key": "sk-user-owned"}, 410),
        )
        for path, payload, expected in posts:
            with self.subTest(path=path):
                status, result = self._post(path, payload)
                self.assertEqual(status, expected)
                self.assertNotIn("sk-user-owned", json.dumps(result))
        status, job, _ = self._get("/api/desktop/evidence-package-jobs/package-job-0123456789")
        self.assertEqual(status, 200)
        self.assertEqual(job["stage"], "completed")

    def test_provider_settings_credentials_and_four_scopes_use_shared_contract(self) -> None:
        status, catalog, no_store = self._get("/api/desktop/ai/providers")
        self.assertEqual((status, no_store), (200, "no-store"))
        self.assertEqual(catalog["schema_version"], "ai-desktop-catalog-v1")
        self.assertEqual(
            {item["provider_id"] for item in catalog["providers"]},
            {"deepseek", "openai"},
        )
        status, saved = self._post(
            "/api/desktop/ai/credentials/openai",
            {"api_key": "sk-openai-owned"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(saved["configured"])
        self.assertNotIn("sk-openai-owned", json.dumps(saved))
        status, deepseek, _ = self._get("/api/desktop/ai/credentials/deepseek")
        self.assertEqual(status, 200)
        self.assertFalse(deepseek["configured"])
        for scope in (
            "personal_suggestion",
            "selected_evidence_chat",
            "librarian",
            "literature_extraction",
        ):
            with self.subTest(scope=scope):
                status, payload = self._post(
                    f"/api/desktop/ai/actions/{scope}/prepare",
                    {"request": "opaque"},
                )
                self.assertEqual(status, 503)
                self.assertEqual(payload["code"], "desktop_ai_business_unavailable")
        before = dict(self.ai_services.credential_manager.backend.values)
        status, payload = self._post(
            "/api/desktop/ai/actions/unknown/prepare",
            {"request": "opaque"},
        )
        self.assertEqual(status, 404)
        self.assertEqual(payload["code"], "desktop_ai_endpoint_not_found")
        self.assertEqual(before, self.ai_services.credential_manager.backend.values)

    def test_ai_routes_require_origin_csrf_and_enforce_shared_body_cap(self) -> None:
        backend = self.ai_services.credential_manager.backend
        before = dict(backend.values)
        self.assertEqual(
            self._post(
                "/api/desktop/ai/credentials/deepseek",
                {"api_key": "sk-deepseek-owned"},
                origin=False,
            )[0],
            403,
        )
        self.assertEqual(
            self._post(
                "/api/desktop/ai/credentials/deepseek",
                {"api_key": "sk-deepseek-owned"},
                csrf=False,
            )[0],
            403,
        )
        self.assertEqual(before, backend.values)

        connection = self._connection()
        connection.putrequest("POST", "/api/desktop/ai/credentials/deepseek")
        connection.putheader("Cookie", self.cookie)
        connection.putheader("Origin", self.origin)
        connection.putheader(MODULE.CSRF_HEADER, self.csrf)
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(8 * 1024 + 1))
        connection.endheaders()
        response = connection.getresponse()
        payload = self._json(response)
        self.assertEqual(response.status, 413)
        self.assertEqual(payload["code"], "desktop_ai_request_too_large")
        self.assertNotIn("path", json.dumps(payload).casefold())
        connection.close()
        self.assertEqual(before, backend.values)

    def test_ai_settings_patch_preserves_shared_public_dto(self) -> None:
        status, catalog, _ = self._get("/api/desktop/ai/providers")
        self.assertEqual(status, 200)
        openai = next(
            item for item in catalog["providers"] if item["provider_id"] == "openai"
        )
        models = {
            task: options[0]
            for task, options in openai["model_options"].items()
        }
        status, updated, no_store = self._patch(
            "/api/desktop/ai/settings",
            {
                "provider_id": "openai",
                "task_models": models,
                "expected_revision": 0,
            },
        )
        self.assertEqual((status, no_store), (200, "no-store"))
        self.assertEqual(updated["schema_version"], "ai-runtime-public-state-v1")
        self.assertEqual(updated["provider_id"], "openai")
        self.assertEqual(updated["task_models"], models)
        self.assertNotIn("credential_ref", updated)

    def test_package_center_routes_preserve_shared_dto_and_async_polling(self) -> None:
        status, inspected = self._post(
            "/api/desktop/package-center/inspect",
            {"selection_token": "selection_0123456789"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(inspected["schema"], "package-summary-v1")
        status, plan = self._post(
            "/api/desktop/package-center/export-plan",
            {"kind": "literature_collection", "scope": "all", "selection": None},
        )
        self.assertEqual(status, 200)
        status, queued = self._post(
            "/api/desktop/package-center/export",
            {
                "plan_token": plan["plan_token"],
                "rights_confirmations": {
                    "unencrypted_ack": True,
                    "unauthenticated_source_ack": True,
                    "internal_use_only_ack": True,
                    "paper_rights": {},
                },
                "destination_token": "destination_0123456789",
            },
        )
        self.assertEqual(status, 202)
        self.assertEqual(queued["stage"], "queued")
        status, completed, no_store = self._get(
            f"/api/desktop/package-center/jobs/{queued['job_id']}"
        )
        self.assertEqual((status, completed["stage"], no_store), (200, "completed", "no-store"))
        status, imported = self._post(
            "/api/desktop/package-center/import",
            {
                "selection_token": "selection_0123456789",
                "checksum_ack": True,
                "expected_sha": "a" * 64,
                "keep_conflicts": True,
            },
        )
        self.assertEqual(status, 202)
        self.assertEqual(imported["stage"], "queued")

    def test_federated_pdf_streams_only_from_lease(self) -> None:
        status, body, media_type, disposition, no_store = self._get_bytes(
            "/api/desktop/federated-pdf?source_id=literature-test&paper_uid=paper-test"
        )
        self.assertEqual(status, 200)
        self.assertEqual(media_type, "application/pdf")
        self.assertEqual(disposition, 'inline; filename="evidence.pdf"')
        self.assertEqual(no_store, "no-store")
        self.assertTrue(body.startswith(b"%PDF-"))

    def test_shared_fusion_workbench_exposes_four_primary_destinations(self) -> None:
        status, index, no_store = self._get_text("/index.html")
        self.assertEqual(status, 200)
        self.assertEqual(no_store, "no-store")
        for destination in ("paper", "search", "personal", "package"):
            self.assertIn(f'data-view="{destination}"', index)
        self.assertIn("文献", index)
        self.assertIn("搜索", index)
        self.assertIn("实验", index)
        self.assertIn("资料包", index)
        self.assertNotIn('data-view="manual"', index)
        self.assertNotIn('data-view="history"', index)
        self.assertIn('id="view-paper"', index)
        self.assertIn('id="fusion-import-pdf"', index)
        self.assertIn('id="fusion-start-extraction"', index)
        self.assertIn('id="fusion-open-librarian"', index)
        self.assertIn('id="view-personal"', index)
        self.assertIn('id="fusion-select-data-file"', index)
        self.assertIn('id="view-package"', index)
        self.assertNotIn('<script src="/static/app.js"></script>', index)
        self.assertNotIn('<script src="/static/workbench.js"></script>', index)
        self.assertLess(
            index.index('<script src="/static/ai_consent.js"></script>'),
            index.index('<script src="/static/fusion_review.js"></script>'),
        )
        status, ai_consent, media_type, _disposition, no_store = self._get_bytes(
            "/static/ai_consent.js"
        )
        self.assertEqual((status, no_store), (200, "no-store"))
        self.assertEqual(media_type, "application/javascript; charset=utf-8")
        self.assertIn(b"AutoResearchAIConsent", ai_consent)
        for path, media_type in (
            ("/static/workbench.css", "text/css; charset=utf-8"),
            ("/static/fusion_review.js", "application/javascript; charset=utf-8"),
        ):
            status, body, actual_type, _disposition, no_store = self._get_bytes(path)
            self.assertEqual((status, actual_type, no_store), (200, media_type, "no-store"))
            self.assertTrue(body)

    def test_ui_mode_uses_injected_release_and_settings_patch_is_cas_protected(self) -> None:
        status, ui_mode, no_store = self._get("/api/ui-mode")
        self.assertEqual((status, no_store), (200, "no-store"))
        self.assertEqual(ui_mode["release"]["version"], "0.8.0-internal.1")

        status, settings, no_store = self._get("/api/desktop/settings")
        self.assertEqual((status, no_store), (200, "no-store"))
        self.assertEqual(settings["schema_version"], "desktop-settings-v1")
        status, updated, no_store = self._patch(
            "/api/desktop/settings/preferences",
            {
                "expected_revision": 0,
                "preferences": {"appearance": {"theme": "dark"}},
            },
        )
        self.assertEqual((status, no_store), (200, "no-store"))
        self.assertEqual(updated["revision"], 1)
        self.assertEqual(updated["appearance"]["theme"], "dark")
        status, conflict, _ = self._patch(
            "/api/desktop/settings/preferences",
            {
                "expected_revision": 0,
                "preferences": {"appearance": {"theme": "light"}},
            },
        )
        self.assertEqual(status, 409)
        self.assertEqual(conflict["code"], "settings_revision_conflict")
        self.assertNotIn("path", json.dumps(conflict).casefold())

    def test_settings_patch_requires_exact_security_and_32k_body(self) -> None:
        payload = {
            "expected_revision": 0,
            "preferences": {"appearance": {"theme": "dark"}},
        }
        self.assertEqual(
            self._patch("/api/desktop/settings/preferences", payload, origin=False)[0],
            403,
        )
        self.assertEqual(
            self._patch("/api/desktop/settings/preferences", payload, csrf=False)[0],
            403,
        )
        status, invalid, _ = self._patch(
            "/api/desktop/settings/preferences",
            {**payload, "api_key": "never-store-this"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(invalid["code"], "settings_invalid")
        self.assertNotIn("never-store-this", json.dumps(invalid))

        connection = self._connection()
        connection.putrequest("PATCH", "/api/desktop/settings/preferences")
        connection.putheader("Cookie", self.cookie)
        connection.putheader("Origin", self.origin)
        connection.putheader(MODULE.CSRF_HEADER, self.csrf)
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(MODULE.MAX_SETTINGS_REQUEST_BYTES + 1))
        connection.endheaders()
        response = connection.getresponse()
        oversized = self._json(response)
        self.assertEqual(response.status, 400)
        self.assertEqual(oversized["code"], "settings_invalid")
        connection.close()

    def test_personal_ai_requires_explicit_consent_and_human_review(self) -> None:
        status, payload = self._post(
            f"/api/desktop/personal-imports/{IMPORT_ID}/ai-suggestion",
            {"sheet_index": 0, "consent": False},
        )
        self.assertEqual(status, 410)
        self.assertEqual(payload["code"], "desktop_legacy_ai_route_disabled")
        status, payload = self._post(
            f"/api/desktop/personal-imports/{IMPORT_ID}/reviewed-import",
            {"reviewed": False, "draft": {"sheet_index": 0}},
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["code"], "personal_review_required")

    def test_session_origin_csrf_and_one_time_bootstrap_are_enforced(self) -> None:
        connection = self._connection()
        connection.request("GET", "/api/desktop/readiness")
        denied = connection.getresponse()
        self.assertEqual(denied.status, 403)
        self.assertEqual(self._json(denied)["code"], "desktop_session_required")
        connection.close()

        self.assertEqual(self._post("/api/desktop/personal-imports/search-refresh", {}, origin=False)[0], 403)
        self.assertEqual(self._post("/api/desktop/personal-imports/search-refresh", {}, csrf=False)[0], 403)

        connection = self._connection()
        connection.request("GET", f"/?desktop_token={self.token}")
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        connection.close()

    def test_transfer_encoding_duplicate_length_and_body_cap_fail_path_free(self) -> None:
        for mode in ("transfer", "duplicate", "oversized"):
            with self.subTest(mode=mode):
                connection = self._connection()
                connection.putrequest("POST", "/api/desktop/personal-imports/search-refresh")
                connection.putheader("Cookie", self.cookie)
                connection.putheader("Origin", self.origin)
                connection.putheader(MODULE.CSRF_HEADER, self.csrf)
                connection.putheader("Content-Type", "application/json")
                if mode == "transfer":
                    connection.putheader("Transfer-Encoding", "chunked")
                    connection.putheader("Content-Length", "2")
                elif mode == "duplicate":
                    connection.putheader("Content-Length", "2")
                    connection.putheader("Content-Length", "2")
                else:
                    connection.putheader("Content-Length", str(MODULE.MAX_PERSONAL_REQUEST_BYTES + 1))
                connection.endheaders(b"{}" if mode != "oversized" else None)
                response = connection.getresponse()
                payload = self._json(response)
                self.assertEqual(response.status, 400)
                self.assertEqual(payload["code"], "desktop_request_invalid")
                self.assertNotIn("path", json.dumps(payload).casefold())
                connection.close()


if __name__ == "__main__":
    unittest.main()
