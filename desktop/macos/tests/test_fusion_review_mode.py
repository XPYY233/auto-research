from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DESKTOP_ROOT = PROJECT_ROOT / "desktop" / "macos"
SOURCE_ROOT = PROJECT_ROOT / "src"
for path in (DESKTOP_ROOT, SOURCE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from auto_research.evidence.db import EvidenceDB  # noqa: E402
from desktop_server import CSRF_HEADER, create_desktop_server, new_session_token  # noqa: E402
from desktop_settings_api import DesktopSettingsAPI  # noqa: E402
from desktop_settings_store import MacAtomicDesktopSettingsStore  # noqa: E402
from auto_research.settings.desktop_settings import DesktopSettingsService  # noqa: E402
from fusion_review_mode import create_fusion_review_snapshot  # noqa: E402


def make_source(path: Path) -> None:
    database = sqlite3.connect(path)
    try:
        database.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key,value) VALUES ('schema_version','12');
            CREATE TABLE papers (id INTEGER PRIMARY KEY, title TEXT NOT NULL);
            INSERT INTO papers(title) VALUES ('isolated review paper');
            """
        )
        database.commit()
    finally:
        database.close()


class FusionReviewModeTests(unittest.TestCase):
    def test_snapshot_is_an_independent_schema_v12_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.sqlite"
            destination = root / "review" / "snapshot.sqlite"
            make_source(source)
            before = source.read_bytes()

            snapshot = create_fusion_review_snapshot(source, destination=destination)

            self.assertEqual(snapshot.database, destination)
            self.assertEqual(snapshot.source_schema, 12)
            self.assertEqual(source.read_bytes(), before)
            review = sqlite3.connect(destination)
            try:
                self.assertEqual(
                    review.execute("SELECT title FROM papers").fetchone()[0],
                    "isolated review paper",
                )
            finally:
                review.close()
            self.assertNotEqual(source.stat().st_ino, destination.stat().st_ino)

    def test_review_server_blocks_mutations_but_allows_appearance_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = new_session_token()
            service = DesktopSettingsService(
                MacAtomicDesktopSettingsStore(root / "settings-v1.json")
            )
            server, _ = create_desktop_server(
                EvidenceDB(root / "review.sqlite"),
                host="127.0.0.1",
                port=0,
                token=token,
                desktop_settings_api=DesktopSettingsAPI(service),
                experience_mode="fusion-review",
            )
            import threading

            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(CookieJar())
            )
            try:
                opener.open(f"{base}/?desktop_token={token}", timeout=5).close()
                with opener.open(f"{base}/api/ui-mode", timeout=5) as response:
                    mode = json.load(response)
                    csrf = response.headers[CSRF_HEADER]
                self.assertEqual(mode["mode"], "fusion-review")
                self.assertFalse(mode["experience"]["mutations"])
                self.assertFalse(mode["experience"]["model_calls"])
                with opener.open(f"{base}/api/desktop/settings", timeout=5) as response:
                    self.assertEqual(response.headers[CSRF_HEADER], csrf)

                patch = urllib.request.Request(
                    f"{base}/api/desktop/settings/preferences",
                    data=json.dumps(
                        {
                            "expected_revision": 0,
                            "preferences": {"appearance": {"theme": "dark"}},
                        }
                    ).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Origin": base,
                        CSRF_HEADER: csrf,
                    },
                    method="PATCH",
                )
                with opener.open(patch, timeout=5) as response:
                    self.assertEqual(json.load(response)["appearance"]["theme"], "dark")

                for method, path, data in (
                    ("POST", "/api/current-paper", b"{}"),
                    ("DELETE", "/api/desktop/ai/credentials/deepseek", None),
                ):
                    with self.subTest(path=path), self.assertRaises(
                        urllib.error.HTTPError
                    ) as raised:
                        opener.open(
                            urllib.request.Request(
                                f"{base}{path}",
                                data=data,
                                headers={
                                    "Content-Type": "application/json",
                                    "Origin": base,
                                    CSRF_HEADER: csrf,
                                },
                                method=method,
                            ),
                            timeout=5,
                        )
                    error = raised.exception
                    self.assertEqual(error.code, 403)
                    self.assertEqual(json.loads(error.read())["code"], "fusion_review_read_only")
                    error.close()

                for path in (
                    "/api/six-export.csv",
                    "/api/papers/1/pdf",
                    "/api/visual-assets/1",
                    "/api/current-paper",
                    "/api/desktop/readiness",
                ):
                    with self.subTest(path=path), self.assertRaises(
                        urllib.error.HTTPError
                    ) as raised:
                        opener.open(f"{base}{path}", timeout=5)
                    error = raised.exception
                    self.assertEqual(error.code, 403)
                    self.assertEqual(
                        json.loads(error.read())["code"],
                        "fusion_review_read_only",
                    )
                    error.close()

                for asset in (
                    "app.js",
                    "desktop_product.js",
                    "package_center.js",
                    "workbench.js",
                ):
                    with self.subTest(asset=asset), self.assertRaises(
                        urllib.error.HTTPError
                    ) as raised:
                        opener.open(f"{base}/static/{asset}", timeout=5)
                    error = raised.exception
                    self.assertEqual(error.code, 403)
                    self.assertEqual(
                        json.loads(error.read())["code"],
                        "fusion_review_read_only",
                    )
                    error.close()
                with opener.open(f"{base}/static/ai_consent.js", timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(b"AutoResearchAIConsent", response.read())
                with opener.open(
                    f"{base}/static/document_tab_store.js", timeout=5
                ) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(b"AutoResearchDocumentTabs", response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_fusion_product_restores_protected_services_without_review_bypass(self) -> None:
        class PersonalAPI:
            @staticmethod
            def is_post_route(path: str) -> bool:
                return path == "/api/desktop/personal-imports/search-refresh"

            @staticmethod
            def handle_post(handler) -> bool:
                length = handler._content_length(512 * 1024, require_body=True)
                handler._read_exact_body(length)
                handler.json_response({"ok": True, "mode": "fusion-product"})
                return True

            @staticmethod
            def handle_get(_handler) -> bool:
                return False

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = new_session_token()
            database = EvidenceDB(root / "workspace.sqlite")
            database.init()
            image = root / "table-1.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\nreal-table-preview")
            stamp = "2026-08-20T00:00:00+00:00"
            with database.connect() as conn:
                paper = conn.execute(
                    "INSERT INTO papers(title,created_at,updated_at) VALUES(?,?,?)",
                    ("Fusion visual evidence", stamp, stamp),
                )
                asset = conn.execute(
                    """INSERT INTO visual_assets(
                       paper_id,asset_type,label,asset_number,caption,page_start,page_end,
                       bbox_json,image_path,image_sha256,physical_quantities_json,
                       variables_json,materials_json,conditions_text,methods_text,
                       context_explanation,tags_json,source_context,review_status,
                       extraction_method,metadata_source,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        int(paper.lastrowid), "table", "Table 1", 1,
                        "Measured irradiation temperatures", 3, 3, "[0,0,1,1]",
                        str(image), "visual-sha", '["temperature"]',
                        '{"column_1":"temperature"}', '["W"]', "irradiated",
                        "table extraction", "Original table screenshot", '["verified"]',
                        "Table 1 context", "verified", "pdf_layout", "deterministic",
                        stamp, stamp,
                    ),
                )
                asset_id = int(asset.lastrowid)
            server, _ = create_desktop_server(
                database,
                host="127.0.0.1",
                port=0,
                token=token,
                personal_import_api=PersonalAPI(),
                release_info={
                    "label": "Auto Research 1.1.0",
                    "version": "1.1.0",
                    "build": "24",
                    "evidence_schema": 12,
                },
                experience_mode="fusion-product",
            )
            import threading

            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(CookieJar())
            )
            try:
                opener.open(f"{base}/?desktop_token={token}", timeout=5).close()
                with opener.open(f"{base}/api/ui-mode", timeout=5) as response:
                    mode = json.load(response)
                    csrf = response.headers[CSRF_HEADER]
                self.assertEqual(mode["mode"], "fusion-product")
                self.assertTrue(mode["experience"]["mutations"])
                self.assertTrue(mode["experience"]["model_calls"])
                self.assertEqual(mode["release"]["version"], "1.1.0")
                self.assertEqual(mode["release"]["build"], "24")

                with opener.open(
                    f"{base}/api/visual-assets/{asset_id}", timeout=5
                ) as response:
                    visual = json.load(response)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(visual["asset_type"], "table")
                self.assertEqual(visual["id"], asset_id)
                self.assertEqual(
                    visual["image_url"], f"/api/visual-assets/{asset_id}/image"
                )
                self.assertNotIn("image_path", visual)

                with opener.open(
                    f"{base}/api/visual-assets/{asset_id}/image", timeout=5
                ) as response:
                    self.assertEqual(response.headers["Content-Type"], "image/png")
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    self.assertEqual(response.read(), image.read_bytes())

                request = urllib.request.Request(
                    f"{base}/api/desktop/personal-imports/search-refresh",
                    data=b"{}",
                    headers={
                        "Content-Type": "application/json",
                        "Origin": base,
                        CSRF_HEADER: csrf,
                    },
                    method="POST",
                )
                with opener.open(request, timeout=5) as response:
                    self.assertEqual(json.load(response)["mode"], "fusion-product")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
