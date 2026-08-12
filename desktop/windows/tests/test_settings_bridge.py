from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT))
try:
    import settings_bridge as MODULE
finally:
    sys.path.pop(0)

from auto_research.settings.desktop_settings import (  # noqa: E402
    DesktopSettingsError,
    DesktopSettingsService,
)


class _MemoryStore:
    def __init__(self) -> None:
        self.value = None

    def read(self):
        return copy.deepcopy(self.value)

    def compare_and_swap(self, *, expected_revision, value):
        revision = 0 if self.value is None else self.value["revision"]
        if revision != expected_revision:
            return False
        self.value = copy.deepcopy(dict(value))
        return True


class WindowsSettingsBridgeTests(unittest.TestCase):
    def test_bridge_projects_shared_service_without_platform_validation(self) -> None:
        bridge = MODULE.WindowsSettingsBridge(DesktopSettingsService(_MemoryStore()))
        self.assertEqual(bridge.get()["schema_version"], "desktop-settings-v1")
        updated = bridge.patch_preferences(
            {"appearance": {"theme": "dark"}},
            expected_revision=0,
        )
        self.assertEqual(updated["revision"], 1)
        self.assertEqual(updated["appearance"]["theme"], "dark")

    def test_shared_errors_keep_frozen_path_free_projection(self) -> None:
        error = DesktopSettingsError(
            "settings_revision_conflict",
            "桌面设置已在其他操作中更新，请重新加载后再试。",
            retryable=True,
        )
        payload, status = MODULE.WindowsSettingsBridge.public_error(error)
        self.assertEqual(status, 409)
        self.assertEqual(payload["code"], "settings_revision_conflict")
        self.assertNotIn("path", repr(payload).casefold())


if __name__ == "__main__":
    unittest.main()
