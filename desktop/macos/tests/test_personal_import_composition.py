from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))

from desktop_product_services import create_desktop_product_services  # noqa: E402


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
            self.assertFalse(expected.exists())


if __name__ == "__main__":
    unittest.main()
