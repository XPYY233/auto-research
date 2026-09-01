from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))

from desktop_product_services import create_desktop_product_services  # noqa: E402
import desktop_product_services  # noqa: E402


class _PrivateSource:
    source_id = "private-lab"
    content_fingerprint = "f" * 64
    document_count = 1

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
    def test_ambient_deepseek_key_cannot_activate_legacy_personal_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            "os.environ",
            {"DEEPSEEK_API_KEY": "sk-ambient-must-not-run"},
        ):
            services = create_desktop_product_services(
                data_root=Path(temporary) / "Application Support",
                current_app_version="0.8.0-preview",
            )
        self.assertIsNone(services.personal_import_service._suggestion_model)
        self.assertFalse(hasattr(desktop_product_services, "DeepSeekClient"))
        self.assertFalse(
            hasattr(desktop_product_services, "RuntimeDeepSeekPersonalSuggestionModel")
        )

    def test_composition_uses_separate_private_library_and_wires_refresh(self) -> None:
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
            self.assertIs(
                services.personal_import_api.search_service,
                services.federated_search_service,
            )
            self.assertIs(
                services.personal_import_service._repository_value,
                services.personal_repository,
            )
            self.assertTrue(expected.exists())
            self.assertFalse(
                services.federated_search_service.status()["private_ready"]
            )

    def test_official_table_review_uses_the_injected_application_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "Application Support"
            services = create_desktop_product_services(
                data_root=data_root,
                current_app_version="1.2.0",
            )

            review_service = services.official_table_structure_service._review
            review_store = review_service._store
            expected_directory = data_root / "Private Data"
            self.assertEqual(
                review_store.path,
                expected_directory / "official-table-structure-review-v1.enc",
            )
            self.assertEqual(
                review_store.key_provider.path,
                expected_directory / "official-table-structure-review-v1.key",
            )
            self.assertFalse(review_store.path.exists())
            self.assertFalse(review_store.key_provider.path.exists())

    def test_startup_restores_nonempty_private_search_snapshot(self) -> None:
        snapshot = _PrivateSource()

        class _PersonalService:
            def __init__(
                self,
                *,
                data_root,
                selection_provider,
                repository=None,
                suggestion_model=None,
            ) -> None:
                self.data_root = Path(data_root).absolute()
                self.selection_provider = selection_provider
                self.suggestion_model = suggestion_model

            def private_search_snapshot(self):
                return snapshot

        with tempfile.TemporaryDirectory() as temporary, patch(
            "desktop_product_services.PersonalImportService",
            _PersonalService,
        ):
            services = create_desktop_product_services(
                data_root=Path(temporary) / "Application Support",
                current_app_version="0.4.0-preview.1",
            )

        status = services.federated_search_service.status()
        self.assertTrue(status["private_ready"])
        self.assertEqual(status["private_source"]["source_id"], snapshot.source_id)
        self.assertEqual(
            status["private_source"]["fingerprint"],
            snapshot.content_fingerprint,
        )
        self.assertEqual(
            services.federated_search_service.search(query="硬度")["total"],
            1,
        )

    def test_startup_private_restore_failure_keeps_composition_available(self) -> None:
        class _FailingPersonalService:
            def __init__(
                self,
                *,
                data_root,
                selection_provider,
                repository=None,
                suggestion_model=None,
            ) -> None:
                self.data_root = Path(data_root).absolute()
                self.selection_provider = selection_provider
                self.suggestion_model = suggestion_model

            def private_search_snapshot(self):
                raise RuntimeError("/private/hidden/personal_experiments.sqlite")

        with tempfile.TemporaryDirectory() as temporary, patch(
            "desktop_product_services.PersonalImportService",
            _FailingPersonalService,
        ):
            services = create_desktop_product_services(
                data_root=Path(temporary) / "Application Support",
                current_app_version="0.4.0-preview.1",
            )

        personal_status = services.personal_import_api.search_status()
        self.assertEqual(personal_status["state"], "retry_required")
        self.assertEqual(
            personal_status["error"]["code"],
            "personal_search_refresh_failed",
        )
        self.assertFalse(
            services.federated_search_service.status()["private_ready"]
        )

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
