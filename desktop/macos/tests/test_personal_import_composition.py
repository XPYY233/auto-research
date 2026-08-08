from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))

from desktop_product_services import create_desktop_product_services  # noqa: E402


class _PrivateSource:
    def iter_search_documents(self):
        yield {
            "schema_version": "evidence-search-document-v1",
            "entity_type": "item",
            "source_scope": "private",
            "source_id": "private-lab",
            "entity_uid": "private-item-1",
            "display_title": "私人样品硬度",
            "meaning_text": "硬度",
            "context_text": "私人实验",
            "source_excerpt": "合成测试数据",
        }


class PersonalImportCompositionTests(unittest.TestCase):
    def test_composition_uses_separate_lazy_private_library(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "Application Support"
            services = create_desktop_product_services(
                data_root=data_root,
                current_app_version="0.4.0-preview.1",
            )
            expected = (data_root / "private-library").absolute()
            self.assertEqual(services.personal_import_service.data_root, expected)
            self.assertIs(
                services.personal_import_api.service,
                services.personal_import_service,
            )
            self.assertIs(
                services.personal_import_service.selection_provider,
                services.personal_file_selection_broker,
            )
            self.assertFalse(expected.exists())

    def test_package_reset_clears_only_official_search_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            services = create_desktop_product_services(
                data_root=Path(temporary) / "Application Support",
                current_app_version="0.4.0-preview.1",
            )
            services.federated_search_service.refresh_private_source(
                _PrivateSource(),
                source_id="private-lab",
                fingerprint="revision:private-1",
            )

            services.package_service.refresh_active(allow_missing=True)

            status = services.federated_search_service.status()
            self.assertFalse(status["official_ready"])
            self.assertTrue(status["private_ready"])
            self.assertTrue(status["federated_ready"])
            self.assertEqual(
                services.federated_search_service.search(query="")["total"],
                1,
            )


if __name__ == "__main__":
    unittest.main()
