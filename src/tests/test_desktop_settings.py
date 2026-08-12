from __future__ import annotations

import copy
import unittest

from auto_research.settings.desktop_settings import (
    DesktopSettingsError,
    DesktopSettingsService,
)


class _MemoryStore:
    def __init__(self, value=None):
        self.value = copy.deepcopy(value)
        self.fail_read = False
        self.fail_write = False
        self.force_conflict = False
        self.writes = []

    def read(self):
        if self.fail_read:
            raise OSError("/Users/private/settings.json: secret-store-detail")
        return copy.deepcopy(self.value)

    def compare_and_swap(self, *, expected_revision, value):
        if self.fail_write:
            raise OSError("C:\\Users\\private\\settings.json: secret-store-detail")
        if self.force_conflict:
            return False
        current_revision = 0 if self.value is None else self.value["revision"]
        if current_revision != expected_revision:
            return False
        self.value = copy.deepcopy(dict(value))
        self.writes.append((expected_revision, copy.deepcopy(dict(value))))
        return True


class DesktopSettingsTests(unittest.TestCase):
    def test_get_returns_path_free_default_public_dto(self) -> None:
        payload = DesktopSettingsService(_MemoryStore()).get().public_dict()
        self.assertEqual(payload["schema_version"], "desktop-settings-v1")
        self.assertEqual(payload["revision"], 0)
        self.assertEqual(
            payload["appearance"],
            {"theme": "system", "density": "comfortable"},
        )
        self.assertEqual(
            payload["locale"],
            {"selected": "zh-CN", "supported": ["zh-CN"]},
        )
        rendered = repr(payload).casefold()
        for forbidden in ("api_key", "credential", "/users/", "c:\\", "internal_id"):
            self.assertNotIn(forbidden, rendered)

    def test_patch_merges_preferences_and_increments_revision(self) -> None:
        store = _MemoryStore()
        service = DesktopSettingsService(store)
        updated = service.patch_preferences(
            {"appearance": {"theme": "dark"}},
            expected_revision=0,
        )
        self.assertEqual(updated.revision, 1)
        self.assertEqual(updated.preferences.theme, "dark")
        self.assertEqual(updated.preferences.density, "comfortable")
        self.assertEqual(store.writes[0][0], 0)
        reread = service.get()
        self.assertEqual(reread, updated)

    def test_stale_revision_and_atomic_cas_loss_are_conflicts(self) -> None:
        store = _MemoryStore()
        service = DesktopSettingsService(store)
        service.patch_preferences(
            {"appearance": {"density": "compact"}}, expected_revision=0
        )
        with self.assertRaises(DesktopSettingsError) as stale:
            service.patch_preferences(
                {"appearance": {"theme": "light"}}, expected_revision=0
            )
        self.assertEqual(stale.exception.code, "settings_revision_conflict")
        store.force_conflict = True
        with self.assertRaises(DesktopSettingsError) as raced:
            service.patch_preferences(
                {"appearance": {"theme": "light"}}, expected_revision=1
            )
        self.assertEqual(raced.exception.code, "settings_revision_conflict")

    def test_unknown_fields_and_bad_values_are_rejected_without_write(self) -> None:
        invalid_payloads = (
            {},
            {"api_key": "secret"},
            {"appearance": {"theme": "blue"}},
            {"appearance": {"density": "dense"}},
            {"appearance": {"theme": ["dark"]}},
            {"appearance": {"theme": "dark", "path": "/tmp/x"}},
            {"locale": {"selected": "en-US"}},
            {"locale": {"selected": "zh-CN", "supported": ["en-US"]}},
        )
        store = _MemoryStore()
        service = DesktopSettingsService(store)
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(
                DesktopSettingsError
            ) as raised:
                service.patch_preferences(payload, expected_revision=0)
            self.assertEqual(raised.exception.code, "settings_invalid")
        self.assertEqual(store.writes, [])

    def test_invalid_revision_is_rejected(self) -> None:
        service = DesktopSettingsService(_MemoryStore())
        for revision in (True, -1, "0", None):
            with self.subTest(revision=revision), self.assertRaises(
                DesktopSettingsError
            ) as raised:
                service.patch_preferences(
                    {"appearance": {"theme": "light"}}, revision
                )
            self.assertEqual(raised.exception.code, "settings_invalid")

    def test_store_exceptions_and_corrupt_state_are_sanitized(self) -> None:
        stores = (_MemoryStore(), _MemoryStore(), _MemoryStore({"path": "/tmp/x"}))
        stores[0].fail_read = True
        stores[1].fail_write = True
        operations = (
            lambda: DesktopSettingsService(stores[0]).get(),
            lambda: DesktopSettingsService(stores[1]).patch_preferences(
                {"appearance": {"theme": "dark"}}, 0
            ),
            lambda: DesktopSettingsService(stores[2]).get(),
        )
        for operation in operations:
            with self.assertRaises(DesktopSettingsError) as raised:
                operation()
            self.assertEqual(raised.exception.code, "settings_store_unavailable")
            rendered = repr(raised.exception.public_dict()).casefold()
            self.assertNotIn("/users/", rendered)
            self.assertNotIn("c:\\", rendered)
            self.assertNotIn("secret-store-detail", rendered)

    def test_error_dto_uses_only_frozen_codes(self) -> None:
        store = _MemoryStore()
        store.force_conflict = True
        with self.assertRaises(DesktopSettingsError) as raised:
            DesktopSettingsService(store).patch_preferences(
                {"locale": {"selected": "zh-CN"}}, 0
            )
        payload = raised.exception.public_dict()
        self.assertEqual(payload["schema_version"], "desktop-settings-error-v1")
        self.assertEqual(payload["code"], "settings_revision_conflict")
        self.assertTrue(payload["retryable"])
        self.assertNotIn("details", payload)


if __name__ == "__main__":
    unittest.main()
