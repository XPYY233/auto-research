from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DESKTOP_ROOT = PROJECT_ROOT / "desktop" / "macos"
SOURCE_ROOT = PROJECT_ROOT / "src"
for path in (DESKTOP_ROOT, SOURCE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from auto_research.evidence.db import EvidenceDB  # noqa: E402
from auto_research.product import ActiveOfficialPackage  # noqa: E402
from desktop_server import CSRF_HEADER, create_desktop_server, new_session_token  # noqa: E402
from federated_search_api import (  # noqa: E402
    DesktopFederatedSearchService,
    FederatedSearchAPI,
)
from package_api import PackageAPI  # noqa: E402
from package_import_service import PackageServiceStatus  # noqa: E402


class _PackageService:
    def __init__(self) -> None:
        self.imports: list[str] = []
        self.job = SimpleNamespace(
            public_dict=lambda: {
                "job_id": "package_job_0123456789abcdef",
                "operation": "import",
                "stage": "queued",
                "progress": 0,
                "terminal": False,
            }
        )

    def status(self):
        return PackageServiceStatus(active=False, repository_audited=False)

    def start_import(self, selection_id: str):
        self.imports.append(selection_id)
        return self.job

    def start_rollback(self, _package_id: str, _target_version: str):
        return self.job

    def get_job(self, _job_id: str):
        return self.job


class _Repository:
    package_id = "official-preview"
    package_version = "0.1.0-preview.1"
    content_fingerprint = "a" * 64

    def iter_search_documents(self):
        yield {
            "schema_version": "official-evidence-document-v1",
            "entity_type": "item",
            "source_scope": "official",
            "source_id": self.package_id,
            "entity_uid": "entity-item-1",
            "article_title": "Tungsten irradiation",
            "display_title": "辐照后硬度",
            "source_excerpt": "硬度升高。",
        }


class DesktopProductRoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="auto-research-desktop-product-routes-test-"
        )
        self.package_service = _PackageService()
        search_service = DesktopFederatedSearchService()
        search_service.install_official_repository(
            ActiveOfficialPackage(
                package_id="official-preview",
                package_version="0.1.0-preview.1",
                content_fingerprint="a" * 64,
                manifest_sha256="b" * 64,
            ),
            _Repository(),
        )
        self.token = new_session_token()
        self.server, _ = create_desktop_server(
            EvidenceDB(Path(self.temporary.name) / "temporary.sqlite"),
            host="127.0.0.1",
            port=0,
            token=self.token,
            package_api=PackageAPI(self.package_service),
            federated_search_api=FederatedSearchAPI(search_service),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )
        self.opener.open(
            f"{self.base_url}/?desktop_token={self.token}", timeout=5
        ).close()
        with self.opener.open(f"{self.base_url}/api/ui-mode", timeout=5) as response:
            self.csrf = response.headers[CSRF_HEADER]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temporary.cleanup()

    def test_package_and_federated_get_routes_require_session(self) -> None:
        with self.opener.open(
            f"{self.base_url}/static/desktop_product.js", timeout=5
        ) as response:
            desktop_product_js = response.read().decode("utf-8")
        self.assertIn("AutoResearchDesktopProduct", desktop_product_js)

        with self.opener.open(
            f"{self.base_url}/api/desktop/evidence-packages", timeout=5
        ) as response:
            package = json.load(response)
        self.assertFalse(package["active"])

        with self.opener.open(
            f"{self.base_url}/api/desktop/federated-search?q=%E7%A1%AC%E5%BA%A6",
            timeout=5,
        ) as response:
            search = json.load(response)
        self.assertEqual(search["total"], 1)
        self.assertEqual(
            search["results"][0]["document"]["entity_uid"], "entity-item-1"
        )

        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(
                f"{self.base_url}/api/desktop/evidence-packages", timeout=5
            )
        self.assertEqual(raised.exception.code, 403)
        raised.exception.close()

    def test_package_import_route_requires_origin_and_csrf(self) -> None:
        payload = json.dumps(
            {"selection_id": "selection_0123456789abcdef"}
        ).encode("utf-8")
        missing_security = urllib.request.Request(
            f"{self.base_url}/api/desktop/evidence-packages/import",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.opener.open(missing_security, timeout=5)
        self.assertEqual(raised.exception.code, 403)
        raised.exception.close()

        authorized = urllib.request.Request(
            f"{self.base_url}/api/desktop/evidence-packages/import",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Origin": self.base_url,
                CSRF_HEADER: self.csrf,
            },
            method="POST",
        )
        with self.opener.open(authorized, timeout=5) as response:
            job = json.load(response)
            self.assertEqual(response.status, 202)
        self.assertEqual(job["stage"], "queued")
        self.assertEqual(
            self.package_service.imports,
            ["selection_0123456789abcdef"],
        )


if __name__ == "__main__":
    unittest.main()
