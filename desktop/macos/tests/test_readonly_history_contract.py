from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
WEB_APP = PROJECT_ROOT / "src" / "auto_research" / "evidence" / "web" / "app.js"


class ReadOnlyHistoryContractTests(unittest.TestCase):
    def test_desktop_history_accepts_signed_and_adhoc_preview_storage(self) -> None:
        source = WEB_APP.read_text(encoding="utf-8")
        self.assertIn("'macos-keychain-aes-256-gcm'", source)
        self.assertIn("'macos-preview-local-key-aes-256-gcm'", source)
        self.assertIn("librarianDesktopStorageLabels.has(body.storage)", source)

    def test_read_only_history_is_memory_only_and_selected_before_desktop_probe(self) -> None:
        source = WEB_APP.read_text(encoding="utf-8")
        load_start = source.index("async function loadLibrarianHistory()")
        desktop_probe = source.index("  try {", load_start)
        readonly_branch = source[load_start:desktop_probe]

        self.assertIn("librarianHistoryBackend = 'readonly-none'", readonly_branch)
        self.assertIn("只读模式不保存对话；不写科学数据库。", readonly_branch)
        self.assertIn("resetLibrarian({ focus: false })", readonly_branch)
        self.assertNotIn("readDesktopLibrarianHistory", readonly_branch)
        self.assertNotIn("readLocalLibrarianHistory", readonly_branch)
        self.assertNotIn("localStorage", readonly_branch)

        init_start = source.index("async function initializeApplication()")
        init_end = source.index("initializeApplication().catch", init_start)
        initializer = source[init_start:init_end]
        self.assertLess(
            initializer.index("state.uiMode = await api('/api/ui-mode')"),
            initializer.index("await loadLibrarianHistory()"),
        )


if __name__ == "__main__":
    unittest.main()
