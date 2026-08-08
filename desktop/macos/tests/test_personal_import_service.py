from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))

from auto_research.personal.import_service import (  # noqa: E402
    PersonalImportService as SharedPersonalImportService,
)
from personal_file_selection_broker import PersonalFileSelectionBroker  # noqa: E402
from personal_import_service import PersonalImportService  # noqa: E402


class PersonalImportServiceAdapterTests(unittest.TestCase):
    def test_macos_adapter_only_supplies_native_selection_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            broker = PersonalFileSelectionBroker(local_volume_probe=lambda _path: True)
            service = PersonalImportService(
                data_root=Path(temporary) / "private-library",
                broker=broker,
            )
            self.assertIsInstance(service, SharedPersonalImportService)
            self.assertIs(service.selection_provider, broker)
            self.assertIs(service.broker, broker)
            self.assertFalse(service.data_root.exists())

    def test_adapter_rejects_ambiguous_provider_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            broker = PersonalFileSelectionBroker(local_volume_probe=lambda _path: True)
            with self.assertRaises(ValueError):
                PersonalImportService(
                    data_root=temporary,
                    broker=broker,
                    selection_provider=broker,
                )


if __name__ == "__main__":
    unittest.main()
